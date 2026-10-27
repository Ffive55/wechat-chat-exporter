import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from export_fixture import ROOT, create_fixture
from api import Api
from markdown_export import export_markdown, message_text
from pdf_export import build_print_page


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wxexport_formats_')
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.fixture = create_fixture(self.root, count=3)
        self.api = Api(self.fixture['data'], ROOT / 'assets', test_export=self.fixture['exports'])
        self.rows = self.fixture['rows']
        self.out = pathlib.Path(self.fixture['exports'])

    def wait_job(self):
        deadline = time.monotonic() + 10
        while self.api.state()['running'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.api.state()['running'])
        return self.api.state()

    def test_text_md_never_resolves_media_and_preserves_raw(self):
        with patch.object(self.api._media, 'resolve', side_effect=AssertionError('文本模式不能加载媒体')):
            result = self.api.export_to(self.rows, '测试', self.out / 'text.md', format='md')
        text = pathlib.Path(result['path']).read_text(encoding='utf-8')
        self.assertEqual(result['messages'], len(self.rows))
        self.assertEqual(result['missing'], [])
        self.assertEqual(result['embedded'], 0)
        for expected in ('[图片]', '[表情]', '[语音附件未嵌入]', '被引用的原始消息', '长消息结束', '<未知消息>', '````', '\\*\\*星号', '&lt;script&gt;'):
            self.assertIn(expected, text)
        self.assertIn('\\[微笑\\]  \n第二行', text)
        self.assertFalse(result['assets_path'])

    def test_md_media_preserves_gif_deduplicates_and_uses_relative_links(self):
        result = self.api.export_to(self.rows, '测试', self.out / '中文 文件.md', online=False, format='md', include_media=True)
        self.assertEqual(result['missing'], [])
        self.assertEqual(result['embedded'], 4)
        assets = pathlib.Path(result['assets_path'])
        self.assertEqual(len(list(assets.iterdir())), 6)
        self.assertEqual(result['inline_emojis'], 4)
        self.assertEqual(result['emoji_files'], 3)
        self.assertEqual(len(list(assets.glob('*.gif'))), 1)
        text = pathlib.Path(result['path']).read_text(encoding='utf-8')
        self.assertIn('%E4%B8%AD%E6%96%87%20%E6%96%87%E4%BB%B6_assets/', text)
        self.assertNotIn('data:image', text)
        from PIL import Image
        with Image.open(next(assets.glob('*.gif'))) as image:
            self.assertEqual(image.n_frames, 2)

    def test_inline_builtin_emojis_use_original_local_files_and_keep_unknown_labels(self):
        import hashlib
        from html.parser import HTMLParser
        from urllib.parse import unquote
        class Images(HTMLParser):
            def __init__(self):
                super().__init__();self.images=[]
            def handle_starttag(self, tag, attrs):
                if tag == 'img':
                    self.images.append(dict(attrs))
        row = dict(self.rows[1], content='你好[笑脸][破涕为笑][笑脸][未收录表情]\n下一行 <标签>')
        with patch.object(self.api._media, 'resolve', side_effect=AssertionError('内置表情不应调用媒体加载或联网')):
            result = self.api.export_to([row], '测试', self.out / '表情 测试.md', online=False, format='md', include_media=True)
        text = pathlib.Path(result['path']).read_text(encoding='utf-8')
        parser = Images();parser.feed(text)
        self.assertEqual([im['alt'] for im in parser.images], ['[笑脸]', '[破涕为笑]', '[笑脸]'])
        self.assertEqual(result['inline_emojis'], 3)
        self.assertEqual(result['emoji_files'], 2)
        self.assertEqual(result['media_files'], 2)
        self.assertEqual(result['embedded'], 0)
        self.assertIn('\\[未收录表情\\]  \n下一行 &lt;标签&gt;', text)
        self.assertEqual(parser.images[0]['src'], parser.images[2]['src'])
        for im in parser.images:
            self.assertEqual((im['width'], im['height']), ('22', '22'))
            self.assertEqual(im['title'], im['alt'])
            source = self.out / unquote(im['src'])
            expected = self.api._renderer.emoji[im['alt'][1:-1]]
            self.assertEqual(source.read_bytes(), expected)

    def test_all_builtin_emoji_labels_export_offline(self):
        import hashlib
        emoji = self.api._renderer.emoji
        row = dict(self.rows[1], content=''.join('[' + name + ']' for name in emoji))
        with patch.object(self.api._media, 'resolve', side_effect=AssertionError('内置表情不应联网')):
            result = self.api.export_to([row], '全部内置表情', self.out / 'all.md', online=False, format='md', include_media=True)
        self.assertEqual(result['inline_emojis'], len(emoji))
        self.assertEqual(result['emoji_files'], len({hashlib.sha256(data).hexdigest() for data in emoji.values()}))
        self.assertEqual(result['missing'], [])
        text = pathlib.Path(result['path']).read_text(encoding='utf-8')
        for name in emoji:
            self.assertIn('alt="[' + name + ']"', text)

    def test_emoji_conversion_keeps_link_targets_and_unknown_raw_content(self):
        link = dict(self.rows[1], type=49, subtype=5, content='<msg><appmsg><title>链接[笑脸]</title><url>https://example.com/[破涕为笑]</url></appmsg></msg>')
        reference = dict(self.rows[1], type=49, subtype=57, content='<msg><appmsg><title>回复[笑脸]</title><refermsg><type>1</type><displayname>好友</displayname><content>引用[破涕为笑]</content><svrid>1</svrid></refermsg></appmsg></msg>')
        unknown = dict(self.rows[1], type=99999, content='<原文>[笑脸]</原文>')
        result = self.api.export_to([link, reference, unknown], '测试', self.out / 'structured.md', format='md', include_media=True)
        text = pathlib.Path(result['path']).read_text(encoding='utf-8')
        self.assertEqual(result['inline_emojis'], 3)
        self.assertIn('https://example.com/%5B%E7%A0%B4%E6%B6%95%E4%B8%BA%E7%AC%91%5D', text)
        self.assertIn('<原文>[笑脸]</原文>', text)
        self.assertIn('> 好友：引用<img ', text)

    def test_failed_emoji_write_keeps_old_document_and_cleans_partial_assets(self):
        target = self.out / 'old.md';target.write_text('old', encoding='utf-8')
        row = dict(self.rows[1], content='[笑脸]')
        with patch.object(pathlib.Path, 'write_bytes', side_effect=PermissionError('无法写入表情资源')):
            with self.assertRaises(PermissionError):
                self.api.export_to([row], '测试', target, format='md', include_media=True)
        self.assertEqual(target.read_text(encoding='utf-8'), 'old')
        self.assertEqual(list(self.out.iterdir()), [target])

    def test_existing_assets_directory_is_never_overwritten(self):
        assets = self.out / 'test_assets';assets.mkdir();(assets / 'keep.txt').write_text('keep')
        result = self.api.export_to(self.rows, '测试', self.out / 'test.md', online=False, format='md', include_media=True)
        self.assertEqual((assets / 'keep.txt').read_text(), 'keep')
        self.assertEqual(pathlib.Path(result['assets_path']).name, 'test_assets_2')

    def test_missing_media_is_marked(self):
        with patch.object(self.api._media, 'resolve', side_effect=ValueError('测试资源缺失')):
            result = self.api.export_to(self.rows, '测试', self.out / 'missing.md', format='md', include_media=True)
        self.assertEqual(len(result['missing']), 4)
        self.assertIn('图片未导出：测试资源缺失', pathlib.Path(result['path']).read_text(encoding='utf-8'))

    def test_failed_write_preserves_old_document_and_cleans_staging(self):
        target = self.out / 'old.md';target.write_text('old')
        import markdown_export
        original = markdown_export.os.replace
        def replace(source, destination):
            if pathlib.Path(destination) == target:
                raise PermissionError('文件占用')
            return original(source, destination)
        with patch('markdown_export.os.replace', side_effect=replace):
            with self.assertRaises(PermissionError):
                self.api.export_to(self.rows, '测试', target, online=False, format='md', include_media=True)
        self.assertEqual(target.read_text(), 'old')
        self.assertEqual(list(self.out.iterdir()), [target])
        self.assertEqual(self.api.boot()['export_format'], 'html')

    def test_only_success_persists_format_across_restarts(self):
        self.assertEqual(self.api.boot()['export_format'], 'html')
        self.api.export_to(self.rows, '测试', self.out / 'test.md', format='md')
        reopened = Api(self.fixture['data'], ROOT / 'assets')
        self.assertEqual(reopened.boot()['export_format'], 'md')
        with patch('pdf_export.export_pdf', side_effect=RuntimeError('PDF失败')):
            self.assertTrue(self.api.choose_export(self.fixture['user'], format='pdf')['ok'])
            self.assertIn('PDF失败', self.wait_job()['error'])
        self.assertEqual(Api(self.fixture['data'], ROOT / 'assets').boot()['export_format'], 'md')

    def test_cancel_does_not_persist(self):
        self.api._test_export = None
        self.api._window = Mock();self.api._window.create_file_dialog.return_value = None
        self.assertTrue(self.api.choose_export(self.fixture['user'], format='pdf')['cancelled'])
        self.assertEqual(self.api.boot()['export_format'], 'html')

    def test_invalid_saved_format_falls_back_to_html(self):
        for value in ('invalid', None, [], {}):
            self.api._settings['export_format'] = value
            self.assertEqual(self.api.boot()['export_format'], 'html')

    def test_protects_source_root_and_enforces_actual_suffix(self):
        for format in ('html', 'pdf', 'md'):
            with self.subTest(format=format):
                with self.assertRaisesRegex(ValueError, '微信数据目录'):
                    self.api._export_path(pathlib.Path(self.fixture['account']).parent / 'archive.any', format)
                self.assertEqual(self.api._export_path(self.out / 'test.html', format).suffix, '.' + format)

    def test_range_inclusive_and_export_unlimited(self):
        store = self.api._store
        rows, count = store.rows(self.fixture['user'], '2026-10-26T00:00:00', '2026-10-26T23:59:59', limit=None)
        self.assertEqual(count, len(self.rows) - 1)
        self.assertEqual(rows[0]['content'], '开始边界：零点整')
        self.assertEqual(rows[-1]['content'], '结束边界：23点59分59秒')
        self.assertFalse(self.api.choose_export(self.fixture['user'], '2026-10-28', '2026-10-27', format='md')['ok'])
        with self.assertRaisesRegex(ValueError, '不支持'):
            self.api._export_path(self.out / 'x', 'docx')

    def test_pdf_page_eager_images_static_gif_no_interactive_controls(self):
        path = self.out / 'print.html'
        result = build_print_page(self.api._renderer, self.rows, '测试 <标题>', path, '', '', False, lambda *_: None)
        self.assertEqual(result['embedded'], 4)
        self.assertEqual(result['missing'], [])
        text = path.read_text(encoding='utf-8')
        for forbidden in ('loading="lazy"', 'data:image/gif', '<details', '<input', '<script', 'original-data'):
            self.assertNotIn(forbidden, text)
        for expected in ('data:image/png', '&lt;标题&gt;', '长消息结束', 'class="row long', '原始内容和'):
            self.assertIn(expected, text)

    def test_html_regression(self):
        result = self.api.export_to(self.rows, '测试', self.out / 'test.html', online=False)
        self.assertEqual(result['format'], 'html')
        self.assertEqual(result['embedded'], 4)
        text = pathlib.Path(result['path']).read_text(encoding='utf-8')
        self.assertIn('data:image/gif', text)
        self.assertIn('export-metadata', text)


if __name__ == '__main__':
    unittest.main()
