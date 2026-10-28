"""Official news -> Molly webhook. Python 3.12+, standard library only."""
import argparse
import copy
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = 'https://mabinogimobile.nexon.com'
BOARDS = {'notice': 'Notice', 'update': 'Update', 'events': 'Events', 'devnote': 'Devnote'}
MAX_PAGES = 100


def thumbnail_url(value):
    """Ignore malformed/unsupported images without failing news detection."""
    try:
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            return None
        absolute = urllib.parse.urljoin(ORIGIN, value)
        parts = urllib.parse.urlsplit(absolute)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return None
        return absolute
    except ValueError:
        return None


class ListParser(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = {}
        self.paging = None
        self.card = None
        self.stack = []
        self.category_parts = []
        self.title_parts = []
        self.title = ''
        self.image = None
        self.banner = False
        self.banner_images = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get('data-pagingtype') == 'thread':
            self.paging = a
        is_banner = tag == 'li' and 'banner_item' in a.get('class', '').split()
        if tag == 'li' and (a.get('data-threadid', '').isdigit() or is_banner):
            self.banner = is_banner
            self.card = a.get('data-threadid') or '__banner__'
            self.stack = []
            self.category_parts = []
            self.title_parts = []
            self.title = ''
            self.image = None
        if not self.card:
            return
        if self.banner:
            link = re.search(r'Thread\.link\(\s*(\d+)', a.get('onclick', ''))
            href = re.search(r'/News/(?:Notice|Update|Events|Devnote)/(\d+)', a.get('href', ''))
            if link or href:
                self.card = (link or href).group(1)
        classes = a.get('class', '').split()
        in_thumbnail = 'thumbnail' in classes or bool(self.stack and self.stack[-1][1])
        in_title = (tag == 'a' and 'title' in classes) or bool(self.stack and self.stack[-1][2])
        in_category = 'type' in classes or bool(self.stack and self.stack[-1][3])
        if 'thumbnail' in classes:
            match = re.search(r"""background-image\s*:\s*url\(\s*(?:'([^']*)'|"([^"]*)"|([^)]*))\s*\)""",
                              a.get('style', ''), re.IGNORECASE)
            if match:
                self.image = self.image or thumbnail_url(next((v for v in match.groups() if v), ''))
        if tag == 'img' and in_thumbnail:
            self.image = (self.image or thumbnail_url(a.get('data-src') or '')
                          or thumbnail_url(a.get('src') or ''))
        if tag not in self.VOID:
            self.stack.append((tag, in_thumbnail, in_title, in_category))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.card:
            return
        if tag == 'a' and self.stack and self.stack[-1][2]:
            self.title = ' '.join(''.join(self.title_parts).split())
            self.title_parts = []
        if tag == 'li':
            if self.banner and self.card.isdigit() and self.image:
                self.banner_images[self.card] = self.image
            if self.title and not self.banner:
                previous = self.cards.get(self.card, {})
                self.cards[self.card] = {
                    'title': self.title,
                    'category': ' '.join(''.join(self.category_parts).split()) or previous.get('category'),
                    # A duplicate mobile card without an image must not erase the desktop image.
                    'thumbnailUrl': self.image or previous.get('thumbnailUrl'),
                }
            self.card = None
            self.stack = []
        else:
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    def handle_data(self, data):
        if self.card and self.stack and self.stack[-1][2]:
            self.title_parts.append(data)
        if self.card and self.stack and self.stack[-1][3]:
            self.category_parts.append(data)


def parse_page(html):
    parser = ListParser()
    parser.feed(html)
    if not parser.cards or not parser.paging:
        raise ValueError('No validated news list (blocked page or markup changed)')
    total = int(parser.paging.get('data-totalcount', '-1'))
    if total < len(parser.cards):
        raise ValueError('Invalid pagination count')
    # Sticky banners have no data-threadid; only enrich IDs present in the real list.
    for post_id, card in parser.cards.items():
        card['thumbnailUrl'] = card['thumbnailUrl'] or parser.banner_images.get(post_id)
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
    board_keys = set(state.get('boards', {}))
    # Only the exact legacy three-board shape may omit the newly added board.
    if state.get('version') != 1 or board_keys not in (set(BOARDS), set(BOARDS) - {'devnote'}):
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
        if board not in state['boards']:
            # Baseline added boards once; do not flood subscribers with historical posts.
            cards = collector(board, set(), initialize=True)
            updated['boards'][board] = sorted(cards, key=int)
            continue
        known = set(state['boards'][board])
        cards = collector(board, known)
        for post_id in sorted(cards.keys() - known, key=int):
            updated['pending'].append({
                'schemaVersion': 1,
                'eventId': f'mabinogi-mobile:{board}:{post_id}:created',
                'board': board, 'postId': post_id, 'title': cards[post_id]['title'],
                'url': f'{ORIGIN}/News/{BOARDS[board]}/{post_id}',
                'detectedAt': now,
                'thumbnailUrl': cards[post_id].get('thumbnailUrl'),
                'category': cards[post_id].get('category') if board in {'notice', 'devnote'} else None,
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
            images = sum(bool(card.get('thumbnailUrl')) for card in cards.values())
            print(f'{board}: {len(cards)} unique posts parsed, {images} thumbnails')
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
