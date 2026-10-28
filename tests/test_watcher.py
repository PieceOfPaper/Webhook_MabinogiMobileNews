import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import watcher as w


def page(ids, total=None):
    cards = ''.join(f'<li data-threadid="{i}"><a class="title"><span>Post {i} &amp; news</span></a></li>' for i in ids)
    return cards + f'<div data-pagingtype="thread" data-totalcount="{total or len(ids)}" data-blockstartno="1" data-blockstartkey="cursor"></div>'


def state():
    return {'version': 1, 'boards': {b: ['1', '2'] for b in w.BOARDS}, 'pending': []}


class Tests(unittest.TestCase):
    def test_real_official_html(self):
        for name in w.BOARDS.values():
            cards, paging = w.parse_page((Path(__file__).parent / 'fixtures' / (name + '.html')).read_text())
            self.assertEqual(2, len(cards))
            self.assertTrue(all(cards.values()))
            self.assertGreater(int(paging['data-totalcount']), 2)

    def test_duplicate_mobile_markup(self):
        cards, _ = w.parse_page(page([5, 5, 4]))
        self.assertEqual({'5': 'Post 5 & news', '4': 'Post 4 & news'}, cards)

    def test_blocked_page_rejected(self):
        with self.assertRaises(ValueError):
            w.parse_page('<html>Security challenge</html>')

    @patch('watcher.time.sleep')
    def test_pagination_catches_burst_even_with_old_pinned_post(self, _):
        pages = iter([page([1, 6, 5], 6), page([4, 3, 2], 6)])
        cards = w.collect('notice', {'1', '2'}, fetch=lambda _: next(pages))
        self.assertTrue({'3', '4', '5', '6'} <= cards.keys())

    @patch('watcher.time.sleep')
    def test_initialize_reads_all_history(self, _):
        pages = iter([page([4, 3], 4), page([2, 1], 4)])
        self.assertEqual(4, len(w.collect('events', set(), True, lambda _: next(pages))))

    @patch('watcher.time.sleep')
    def test_repeated_page_rejected(self, _):
        with self.assertRaises(ValueError):
            w.collect('notice', set(), True, lambda _: page([4, 3], 5))

    def test_order_event_identity_and_no_mutation(self):
        original = state()
        result = w.discover(original, lambda board, known: {'4': 'four', '3': 'three', '2': 'renamed'})
        self.assertEqual([], original['pending'])
        self.assertEqual(['3', '4'], [e['postId'] for e in result['pending'][:2]])
        self.assertEqual('mabinogi-mobile:notice:3:created', result['pending'][0]['eventId'])
        self.assertEqual(result, w.discover(result, lambda b, k: {'3': 'edited', '4': 'four'}))

    def test_failed_collection_does_not_mutate_state(self):
        original = state()
        before = copy.deepcopy(original)
        def collect(board, known):
            if board == 'update':
                raise ValueError('blocked')
            return {'3': 'new'}
        with self.assertRaises(ValueError):
            w.discover(original, collect)
        self.assertEqual(before, original)

    def test_delivery_failure_and_restart(self):
        s = w.discover(state(), lambda b, k: {'3': 'new'})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'news.json'
            w.save_state(path, s)
            sent = []
            def send(url, token, event):
                if len(sent) == 1:
                    raise RuntimeError('network')
                sent.append(event['eventId'])
            with self.assertRaises(RuntimeError):
                w.deliver(s, path, 'https://example.com', 'secret', send)
            restored = w.load_state(path)
            self.assertEqual(2, len(restored['pending']))
            w.deliver(restored, path, '', '', lambda u, t, e: sent.append(e['eventId']))
            self.assertEqual(3, len(set(sent)))
            self.assertEqual([], w.load_state(path)['pending'])

    def test_missing_or_corrupt_state_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'news.json'
            with self.assertRaises(FileNotFoundError):
                w.load_state(path)
            path.write_text('{}')
            with self.assertRaises(ValueError):
                w.load_state(path)

    def test_endpoint_validation(self):
        for url in ['http://example.com', 'https://user:pass@example.com', 'https://example.com/#secret']:
            with self.assertRaises(ValueError):
                w.validate_endpoint(url, 'token')
        w.validate_endpoint('https://example.com/webhooks/mabinogi/news', 'token')

    def test_no_redirect(self):
        self.assertIsNone(w.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))


if __name__ == '__main__':
    unittest.main()
