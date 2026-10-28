"""Official news -> Molly webhook. Python 3.12+, standard library only."""
import argparse
import copy
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = 'https://mabinogimobile.nexon.com'
BOARDS = {'notice': 'Notice', 'update': 'Update', 'events': 'Events'}
MAX_PAGES = 100


class ListParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = {}
        self.paging = None
        self.card = None
        self.title_depth = 0
        self.title_parts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get('data-pagingtype') == 'thread':
            self.paging = a
        if tag == 'li' and a.get('data-threadid', '').isdigit():
            self.card = a['data-threadid']
        if self.card and tag == 'a' and 'title' in a.get('class', '').split():
            self.title_depth = 1
            self.title_parts = []
        elif self.title_depth:
            if tag not in {'br', 'img', 'input', 'hr', 'meta', 'link'}:
                self.title_depth += 1

    def handle_endtag(self, tag):
        if self.title_depth:
            self.title_depth -= 1
            if self.title_depth == 0:
                title = ' '.join(''.join(self.title_parts).split())
                if title:
                    self.cards[self.card] = title
        if tag == 'li':
            self.card = None

    def handle_data(self, data):
        if self.title_depth:
            self.title_parts.append(data)


def parse_page(html):
    parser = ListParser()
    parser.feed(html)
    if not parser.cards or not parser.paging:
        raise ValueError('No validated news list (blocked page or markup changed)')
    total = int(parser.paging.get('data-totalcount', '-1'))
    if total < len(parser.cards):
        raise ValueError('Invalid pagination count')
    return parser.cards, parser.paging


def fetch_html(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 MollyNewsWatcher/1.0',
        'Accept-Language': 'ko-KR,ko;q=0.9',
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode('utf-8-sig')
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError('Official news request failed') from None
            time.sleep(2 ** attempt)


def collect(board, known, initialize=False, fetch=fetch_html):
    """Bootstrap all history; subsequent scans stop at a fully known page."""
    base = f'{ORIGIN}/News/{BOARDS[board]}'
    collected = {}
    params = {}
    expected_total = None
    for page in range(1, MAX_PAGES + 1):
        url = base if page == 1 else base + '?' + urllib.parse.urlencode({**params, 'pageno': page})
        cards, paging = parse_page(fetch(url))
        total = int(paging['data-totalcount'])
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise ValueError('List changed during pagination; retry next run')
        if set(cards).issubset(collected):
            raise ValueError('Pagination repeated a page')
        collected.update(cards)
        if not initialize and set(cards).issubset(known):
            return collected
        if len(collected) >= total:
            return collected
        params = {'blockStartNo': paging.get('data-blockstartno', ''),
                  'blockStartKey': paging.get('data-blockstartkey', '')}
        if not all(params.values()):
            raise ValueError('Missing pagination cursor')
        time.sleep(0.2)
    raise ValueError('Pagination limit reached; state not advanced')


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.state-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_state(path):
    state = json.loads(Path(path).read_text(encoding='utf-8'))
    if state.get('version') != 1 or set(state.get('boards', {})) != set(BOARDS):
        raise ValueError('Invalid state schema')
    if not isinstance(state.get('pending'), list):
        raise ValueError('Invalid pending state')
    for ids in state['boards'].values():
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i.isdigit() for i in ids):
            raise ValueError('Invalid seen IDs')
    for event in state['pending']:
        if event.get('board') not in BOARDS or not event.get('eventId'):
            raise ValueError('Invalid pending event')
    return state


def discover(state, collector=collect):
    updated = copy.deepcopy(state)
    now = datetime.now(timezone.utc).isoformat()
    for board in BOARDS:
        known = set(state['boards'][board])
        cards = collector(board, known)
        for post_id in sorted(cards.keys() - known, key=int):
            updated['pending'].append({
                'schemaVersion': 1,
                'eventId': f'mabinogi-mobile:{board}:{post_id}:created',
                'board': board, 'postId': post_id, 'title': cards[post_id],
                'url': f'{ORIGIN}/News/{BOARDS[board]}/{post_id}',
                'detectedAt': now,
            })
        updated['boards'][board] = sorted(known | cards.keys(), key=int)
    return updated


def validate_endpoint(url, token):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError('MOLLY_WEBHOOK_URL must be an HTTPS receiver URL without credentials/fragment')
    if not token or any(c in token for c in '\r\n'):
        raise ValueError('MOLLY_WEBHOOK_TOKEN must be set')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the bearer secret to another destination.


def send_event(url, token, event):
    req = urllib.request.Request(url, method='POST',
        data=json.dumps(event, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json; charset=utf-8',
                 'Authorization': f'Bearer {token}', 'Idempotency-Key': event['eventId']})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f'Webhook HTTP {response.status}')
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'Webhook HTTP {error.code}; pending retained') from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError('Webhook connection failed; pending retained') from None


def deliver(state, path, url, token, sender=send_event):
    while state['pending']:
        event = state['pending'][0]
        sender(url, token, event)
        state['pending'].pop(0)
        save_state(path, state)
        print(f'Delivered {event["eventId"]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', default='state/news.json')
    parser.add_argument('--initialize', action='store_true', help='Record all history, send nothing; refuses existing state')
    parser.add_argument('--check', action='store_true', help='Fetch/parse only first pages; no writes or webhook')
    args = parser.parse_args()
    if args.check:
        for board, name in BOARDS.items():
            cards, _ = parse_page(fetch_html(f'{ORIGIN}/News/{name}'))
            print(f'{board}: {len(cards)} unique posts parsed')
        return
    path = Path(args.state)
    if args.initialize:
        if path.exists():
            raise ValueError('State already exists; initialization refused')
        state = {'version': 1, 'boards': {}, 'pending': []}
        for board in BOARDS:
            cards = collect(board, set(), initialize=True)
            state['boards'][board] = sorted(cards, key=int)
            print(f'{board}: baselined {len(cards)} historical posts')
        save_state(path, state)
        return
    # Missing/corrupt state is a failure, never an implicit reinitialization.
    state = load_state(path)
    url = os.environ.get('MOLLY_WEBHOOK_URL', '')
    token = os.environ.get('MOLLY_WEBHOOK_TOKEN', '')
    validate_endpoint(url, token)
    # Retry the persisted outbox before collecting further news.
    deliver(state, path, url, token)
    updated = discover(state)
    if updated != state:
        save_state(path, updated)  # Persist discovered IDs + outbox BEFORE delivery.
    deliver(updated, path, url, token)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Do not print webhook URLs, headers or response bodies.
        print(f'Watcher failed ({type(error).__name__}): {error}', file=sys.stderr)
        sys.exit(1)
