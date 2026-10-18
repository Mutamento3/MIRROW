from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest
from wander_manager.xhs_request import compile_task, latest_cursor, material_for_k
from wander_manager.flash_structured import FlashJsonResult
from wander_manager.xiaohongshu_adb import parse_ui_dump
from wander_manager.xhs_profile_navigation import ProfileNavigator, profile_cards
TASK = {'operation': 'profile', 'query': '示例伙伴＆AI', 'count': 2, 'scope': 'all', 'task_id': ''}
class PublicProfileNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_listing_title_cannot_bind_unrelated_page_text(self):
        listing = parse_ui_dump('<hierarchy><node package="com.xingin.xhs">'
            '<node content-desc="笔记,,来自示例伙伴＆AI,1赞，" bounds="[0,500][500,700]"/>'
            '</node></hierarchy>')
        card = profile_cards(listing, TASK['query'])[0]
        source = SimpleNamespace(_dump_ui=AsyncMock(), _screenshot=AsyncMock())
        result = await ProfileNavigator(source, 'internal-only').read_article(card, '123')
        self.assertEqual('article_title_unconfirmed', result['status'])
        self.assertNotIn('source_id', result)
        source._dump_ui.assert_not_awaited()
        source._screenshot.assert_not_awaited()

    async def test_compiler_preserves_account_symbols_and_rejects_writes(self):
        compiler = AsyncMock(return_value=FlashJsonResult(status='ok', parsed=TASK))
        self.assertEqual('示例伙伴＆AI', (await compile_task('读她全部主页', compiler=compiler))['query'])
        compiler.return_value = FlashJsonResult(status='ok', parsed={'operation': 'unsupported'})
        with self.assertRaises(ValueError):
            await compile_task('发评论', compiler=compiler)

    def test_failed_cursor_does_not_advance_and_snapshot_is_detached(self):
        nodes = [{'execution_status': 'succeeded', 'source_payload': {'cursor_after': {'read_count': 2}}},
                 {'execution_status': 'failed', 'source_payload': {'cursor_after': {'read_count': 3}}}]
        self.assertEqual(2, latest_cursor(nodes)['read_count'])
        payload = {'article_text': '全文', 'image_observations': [{'page': '1', 'observation': '图', 'screenshot_base64': 'secret'}]}
        material = material_for_k(payload)
        payload['image_observations'][0]['observation'] = 'changed'
        self.assertEqual('图', material['image_observations'][0]['observation'])
        self.assertNotIn('secret', str(material))

    async def test_masonry_overlap_never_requeues_an_old_card(self):
        def page(titles):
            xml = '<hierarchy><node package="com.xingin.xhs" bounds="[0,0][1080,2290]">'
            for index, title in enumerate(titles):
                xml += f'<node content-desc="笔记,{title},来自示例伙伴＆AI,1赞，" bounds="[0,{500+index*200}][500,{650+index*200}]"/>'
            return parse_ui_dump(xml+'</node></hierarchy>')
        first, second = page(['A', 'B', 'C', 'D']), page(['C', 'E', 'D', 'F'])
        source = SimpleNamespace(post_wait_seconds=0, _sleeper=AsyncMock(),
                                 _run_device=AsyncMock(return_value=SimpleNamespace(returncode=0)))
        navigator = ProfileNavigator(source, 'internal-only')
        navigator.enter_profile = AsyncMock(return_value=(first, '123'))
        navigator.snapshot = AsyncMock(return_value=second)
        navigator.read_article = AsyncMock(return_value={'status': 'success', 'source_id': 'observed'})
        cursor = {'profile_id': '123', 'read_count': 4, 'listing_path': [c['key'] for c in profile_cards(first, TASK['query'])]}
        result = await navigator.fetch(TASK, cursor)
        self.assertEqual('E', navigator.read_article.call_args.args[0]['title'])
        self.assertEqual(5, result['cursor_after']['read_count'])
        self.assertEqual(5, len(set(result['cursor_after']['listing_path'])))
        self.assertEqual('observed', result['source_id'])

    async def test_noncontiguous_repeated_title_is_ambiguous_not_list_end(self):
        def page(title, end=''):
            return parse_ui_dump(f'<hierarchy><node package="com.xingin.xhs" bounds="[0,0][1080,2290]">'
                f'<node content-desc="笔记,{title},来自示例伙伴＆AI,1赞，" bounds="[0,500][500,700]"/>'
                f'<node text="{end}"/></node></hierarchy>')
        first, second, last = page('A'), page('B'), page('A', '没有更多了')
        keys = [profile_cards(p, TASK['query'])[0]['key'] for p in (first, second)]
        source = SimpleNamespace(post_wait_seconds=0, _sleeper=AsyncMock(),
                                 _run_device=AsyncMock(return_value=SimpleNamespace(returncode=0)))
        navigator = ProfileNavigator(source, 'internal-only')
        navigator.enter_profile = AsyncMock(return_value=(first, '123'))
        navigator.snapshot = AsyncMock(side_effect=[second, last])
        navigator.read_article = AsyncMock()
        result = await navigator.fetch(TASK, {'profile_id': '123', 'read_count': 2, 'listing_path': keys})
        self.assertEqual('profile_listing_ambiguous', result['status'])
        navigator.read_article.assert_not_awaited()

    async def test_tall_gallery_can_verify_title_below_the_first_screen(self):
        header = '<hierarchy><node package="com.xingin.xhs" bounds="[0,0][1080,2290]"><node resource-id="x/nickNameTV" text="示例伙伴＆AI"/>'
        initial = parse_ui_dump(header+'<node content-desc="图片,第1张,共1张"/></node></hierarchy>')
        body = parse_ui_dump(header+'<node text="同题"/><node text="作者正文"/><node content-desc="评论框"/></node></hierarchy>')
        source = SimpleNamespace(_dump_ui=AsyncMock(side_effect=[initial, body, body]), _guard_snapshot=lambda _: '',
            _screenshot=AsyncMock(return_value='pixels'), post_wait_seconds=0,
            _sleeper=AsyncMock(), _run_device=AsyncMock(return_value=SimpleNamespace(returncode=0)))
        result = await ProfileNavigator(source, 'internal-only').read_article({'title': '同题', 'author': '示例伙伴＆AI'}, '123')
        self.assertEqual('success', result['status'])
        self.assertEqual('作者正文', result['article_text'])
        self.assertEqual('all_captured', result['coverage']['images'])

    async def test_mid_gallery_never_claims_all_and_comments_are_not_body(self):
        xml = '<hierarchy><node package="com.xingin.xhs" bounds="[0,0][1080,2290]">' + ''.join([
            '<node resource-id="x/nickNameTV" text="示例伙伴＆AI"/>',
            '<node text="同题"/><node text="这是正文"/>',
            '<node content-desc="评论框"/><node text="这是评论内容"/>',
            '<node content-desc="图片,第2张,共2张,说明"/>']) + '</node></hierarchy>'
        snap = parse_ui_dump(xml)
        source = SimpleNamespace(_dump_ui=AsyncMock(return_value=snap), _guard_snapshot=lambda _: '',
            _screenshot=AsyncMock(return_value='pixels'), post_wait_seconds=0,
            _sleeper=AsyncMock(), _run_device=AsyncMock(return_value=SimpleNamespace(returncode=0)))
        result = await ProfileNavigator(source, 'internal-only').read_article({'title': '同题', 'author': '示例伙伴＆AI'}, '123')
        self.assertEqual('这是正文', result['article_text'])
        self.assertEqual('partial_or_unknown', result['coverage']['images'])
        self.assertEqual('partial', result['coverage']['text'])

    async def test_comment_quoting_body_cannot_extend_the_article(self):
        header = '<hierarchy><node package="com.xingin.xhs" bounds="[0,0][1080,2290]"><node resource-id="x/nickNameTV" text="示例伙伴＆AI"/>'
        initial = parse_ui_dump(header+'<node text="同题"/><node text="作者原文"/><node content-desc="评论框"/></node></hierarchy>')
        comments = parse_ui_dump(header+'<node content-desc="评论框"/><node text="作者原文加上评论者的话"/></node></hierarchy>')
        source = SimpleNamespace(_dump_ui=AsyncMock(side_effect=[initial, comments]), _guard_snapshot=lambda _: '',
            _screenshot=AsyncMock(return_value='pixels'), post_wait_seconds=0,
            _sleeper=AsyncMock(), _run_device=AsyncMock(return_value=SimpleNamespace(returncode=0)))
        result = await ProfileNavigator(source, 'internal-only').read_article({'title': '同题', 'author': '示例伙伴＆AI'}, '123')
        self.assertEqual('作者原文', result['article_text'])
