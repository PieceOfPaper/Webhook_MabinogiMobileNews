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


def card(title, image=None):
    return {'title': title, 'thumbnailUrl': image, 'category': None}


class Tests(unittest.TestCase):
    def test_real_official_html(self):
        for name in w.BOARDS.values():
            cards, paging = w.parse_page((Path(__file__).parent / 'fixtures' / (name + '.html')).read_text())
            self.assertEqual(2, len(cards))
            self.assertTrue(all(cards.values()))
            self.assertGreater(int(paging['data-totalcount']), 2)

    def test_duplicate_mobile_markup(self):
        cards, _ = w.parse_page(page([5, 5, 4]))
        self.assertEqual({'5': card('Post 5 & news'), '4': card('Post 4 & news')}, cards)

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
        result = w.discover(original, lambda board, known: {'4': card('four'), '3': card('three'), '2': card('renamed')})
        self.assertEqual([], original['pending'])
        self.assertEqual(['3', '4'], [e['postId'] for e in result['pending'][:2]])
        self.assertEqual('mabinogi-mobile:notice:3:created', result['pending'][0]['eventId'])
        self.assertEqual(result, w.discover(result, lambda b, k: {'3': card('edited'), '4': card('four')}))

    def test_failed_collection_does_not_mutate_state(self):
        original = state()
        before = copy.deepcopy(original)
        def collect(board, known):
            if board == 'update':
                raise ValueError('blocked')
            return {'3': card('new')}
        with self.assertRaises(ValueError):
            w.discover(original, collect)
        self.assertEqual(before, original)

    def test_delivery_failure_and_restart(self):
        s = w.discover(state(), lambda b, k: {'3': card('new')})
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
            self.assertEqual(len(w.BOARDS) - 1, len(restored['pending']))
            w.deliver(restored, path, '', '', lambda u, t, e: sent.append(e['eventId']))
            self.assertEqual(len(w.BOARDS), len(set(sent)))
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

    def test_notice_category_labels_and_whitespace(self):
        for label in ['주요상품', '점검', '안내', '완료', '새로운분류']:
            markup = page([3]).replace('<a class="title">',
                f'<div class="type"> \n<span>{label}</span> </div><a class="title">')
            cards, _ = w.parse_page(markup)
            self.assertEqual(label, cards['3']['category'])
            self.assertEqual('Post 3 & news', cards['3']['title'])
        self.assertIsNone(w.parse_page(page([3]))[0]['3']['category'])

    def test_category_duplicate_and_no_leak_between_cards(self):
        first = '<li data-threadid="3"><div class="type"><span>점검</span></div><a class="title">Title</a></li>'
        cards, _ = w.parse_page(first + page([3, 4]))
        self.assertEqual('점검', cards['3']['category'])
        self.assertIsNone(cards['4']['category'])

    def test_category_payload_and_retry(self):
        def collect(board, known):
            return {'3': {'title': 'Title', 'category': '주요상품', 'thumbnailUrl': None}}
        s = w.discover(state(), collect)
        self.assertEqual(['주요상품', None, None, '주요상품'], [e['category'] for e in s['pending']])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'news.json'
            w.save_state(path, s)
            sent = []
            w.deliver(w.load_state(path), path, '', '', lambda u, t, e: sent.append(e))
            self.assertEqual('주요상품', sent[0]['category'])

    def test_devnote_payload_and_official_categories(self):
        cards, _ = w.parse_page((Path(__file__).parent / 'fixtures/Devnote.html').read_text())
        self.assertTrue(all(c['category'] == '개발자노트' for c in cards.values()))
        result = w.discover(state(), lambda b, k: {'3': {'title': '에린 노트', 'category': 'CM노트', 'thumbnailUrl': None}})
        event = result['pending'][-1]
        self.assertEqual('devnote', event['board'])
        self.assertEqual('CM노트', event['category'])
        self.assertEqual(w.ORIGIN + '/News/Devnote/3', event['url'])
        self.assertEqual('mabinogi-mobile:devnote:3:created', event['eventId'])

    def test_legacy_state_baselines_only_new_board_then_notifies(self):
        old = state()
        del old['boards']['devnote']
        original = copy.deepcopy(old)
        calls = []
        def collect(board, known, initialize=False):
            calls.append((board, initialize))
            return {'3': card('new')}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'news.json'
            w.save_state(path, old)
            migrated = w.discover(w.load_state(path), collect)
            self.assertEqual(original, old)
            self.assertEqual(('devnote', True), calls[-1])
            self.assertEqual(['3'], migrated['boards']['devnote'])
            self.assertEqual(3, len(migrated['pending']))
            self.assertTrue(all(e['board'] != 'devnote' for e in migrated['pending']))
            w.save_state(path, migrated)
            next_state = w.discover(w.load_state(path), lambda b, k: {'3': card('old'), '4': card('new')})
            self.assertEqual(1, len([e for e in next_state['pending'] if e['board'] == 'devnote']))

    def test_incomplete_or_unknown_board_state_still_rejected(self):
        for missing in ['notice', 'update', 'events']:
            broken = state()
            del broken['boards'][missing]
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'news.json'
                w.save_state(path, broken)
                with self.assertRaises(ValueError):
                    w.load_state(path)

    def test_new_board_baseline_failure_does_not_advance_state(self):
        old = state()
        del old['boards']['devnote']
        original = copy.deepcopy(old)
        def collect(board, known, initialize=False):
            if initialize:
                raise ValueError('baseline failed')
            return {'3': card('new')}
        with self.assertRaises(ValueError):
            w.discover(old, collect)
        self.assertEqual(original, old)

    def test_no_redirect(self):
        self.assertIsNone(w.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))

    def test_sticky_banner_image_enriches_matching_list_post_only(self):
        banner = '<li class="banner_item swiper-slide"><div class="thumbnail" onclick="mmCommunity.Thread.link(3, this)" style="background-image:url(https://cdn.example/banner.png)"></div><a class="title">Banner</a></li>'
        cards, _ = w.parse_page(banner + page([3, 4]))
        self.assertEqual('https://cdn.example/banner.png', cards['3']['thumbnailUrl'])
        self.assertEqual('Post 3 & news', cards['3']['title'])
        self.assertIsNone(cards['4']['thumbnailUrl'])
        cards, _ = w.parse_page(banner + page([4]))
        self.assertNotIn('3', cards)

    def test_thumbnail_css_and_img_variants(self):
        styles = ["background-image: url('https://cdn.example/notice.png')",
                  'background-image:URL("https://cdn.example/notice.png")',
                  'background-image: url(https://cdn.example/notice.png)']
        import html
        for style in styles:
            markup = page([3]).replace('<a class="title">',
                f'<div class="thumbnail" style="{html.escape(style, quote=True)}"></div><a class="title">')
            cards, _ = w.parse_page(markup)
            self.assertEqual('https://cdn.example/notice.png', cards['3']['thumbnailUrl'])
        markup = page([3]).replace('<a class="title">',
            '<div class="thumbnail"><img src="/banner.png?a=1&amp;b=2" /></div><a class="title">')
        self.assertEqual(w.ORIGIN + '/banner.png?a=1&b=2', w.parse_page(markup)[0]['3']['thumbnailUrl'])

    def test_profile_and_missing_invalid_images_do_not_block_news(self):
        for image in ['', '<img src="https://cdn.example/profile.png">',
                      '<div class="thumbnail"><img src="javascript:bad"></div>',
                      '<div class="thumbnail"><img src="https://[invalid"></div>']:
            markup = page([3]).replace('<a class="title">', image + '<a class="title">')
            cards, _ = w.parse_page(markup)
            self.assertEqual('Post 3 & news', cards['3']['title'])
            self.assertIsNone(cards['3']['thumbnailUrl'])

    def test_duplicate_cards_preserve_image_in_both_orders(self):
        with_image = '<li data-threadid="3"><div class="thumbnail"><img src="/banner.png"></div><a class="title">Title</a></li>'
        no_image = '<li data-threadid="3"><a class="title">Title</a></li>'
        paging = page([3]).split('</li>')[1]
        for cards_html in [with_image + no_image, no_image + with_image]:
            cards, _ = w.parse_page(cards_html + paging)
            self.assertEqual(w.ORIGIN + '/banner.png', cards['3']['thumbnailUrl'])
            self.assertEqual(1, len(cards))

    def test_thumbnail_is_persisted_and_retried_unchanged(self):
        s = w.discover(state(), lambda b, k: {'3': card('new', 'https://cdn.example/banner.png')})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'news.json'
            w.save_state(path, s)
            before = copy.deepcopy(s['pending'])
            with self.assertRaises(RuntimeError):
                w.deliver(s, path, '', '', lambda *args: (_ for _ in ()).throw(RuntimeError('failed')))
            restored = w.load_state(path)
            sent = []
            w.deliver(restored, path, '', '', lambda u, t, e: sent.append(copy.deepcopy(e)))
            self.assertEqual(before, sent)

    def test_old_pending_payload_without_thumbnail_still_delivers(self):
        s = state()
        s['pending'] = [{'board': 'notice', 'eventId': 'old', 'title': 'Old payload'}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'news.json'
            w.save_state(path, s)
            loaded = w.load_state(path)
            sent = []
            w.deliver(loaded, path, '', '', lambda u, t, e: sent.append(e))
            self.assertNotIn('thumbnailUrl', sent[0])


if __name__ == '__main__':
    unittest.main()
