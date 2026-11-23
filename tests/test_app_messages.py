"""Regression coverage for the wx-cli-again appmsg parsing port (see THIRD_PARTY.txt)."""
import html
import pathlib
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vendor.formatters import parse_app, row_to_msg
from rendering import Renderer
from markdown_export import export_markdown, message_text
from pdf_export import build_print_page


PLACEHOLDER = '当前版本不支持展示该内容，请升级至最新版本。'
UPGRADE = 'https://support.weixin.qq.com/update/'
PAYLOAD_URL = 'https://wxapp.tc.qq.com/video?key=abc&token=xyz'


def card(subtype=51, title=PLACEHOLDER, description='', payload='', url=UPGRADE):
    return ('<msg><appmsg><title>' + html.escape(title) + '</title><des>'
            + html.escape(description) + f'</des><type>{subtype}</type><url>'
            + html.escape(url) + '</url>' + payload + '</appmsg></msg>')


def row(content, subtype=51):
    return dict(id=1, username='test', server_id='2', local_id=3,
                ts=1792938610, sender='test', sender_display='测试', is_self=0,
                type=49, subtype=subtype, content=content)


def feed(title='真实视频标题 <片段>', url=PAYLOAD_URL):
    return ('<finderFeed><desc><![CDATA[' + title + ']]></desc><nickname>作者</nickname>'
            '<mediaList><media><url>' + html.escape(url)
            + '</url></media></mediaList></finderFeed>')


class AppMessageTests(unittest.TestCase):
    def test_video_payload_and_group_prefixes(self):
        for prefix in ('', 'sender:\n', 'sender:\r\n', r'sender:\n', r'sender:\r\n'):
            with self.subTest(prefix=prefix):
                message = row_to_msg(row(prefix + card(payload=feed()) + '\n\x00\x00'), None, {})
                self.assertEqual(message['kind'], 'channel')
                self.assertEqual(message['title'], '真实视频标题 <片段>')
                self.assertEqual(message['url'], PAYLOAD_URL)
                self.assertNotIn('quote', message)

    def test_video_title_fallbacks(self):
        for payload, title, expected in (
            ('', '真实外层标题', '真实外层标题'),
            ('<finderFeed><desc/><name/><nickname/></finderFeed>', '真实外层标题', '真实外层标题'),
            ('<finderFeed><nickname>作者昵称</nickname></finderFeed>', PLACEHOLDER, '作者昵称'),
            ('<finderFeed><name>名称</name><nickname>作者</nickname></finderFeed>', PLACEHOLDER, '名称'),
            ('<finderFeed><desc>' + PLACEHOLDER + '</desc><nickname>作者</nickname></finderFeed>', PLACEHOLDER, '作者'),
            ('', PLACEHOLDER, ''),
        ):
            with self.subTest(payload=payload, title=title):
                result = parse_app(card(payload=payload, title=title), 51)
                self.assertEqual(result['title'], expected)
                self.assertEqual(result['kind'], 'channel')

    def test_placeholder_semantic_title_and_labels(self):
        for subtype, kind in ((33, 'miniprogram'), (36, 'miniprogram'), (44, 'miniprogram'),
                              (63, 'channel_live'), (124, 'gift'), (999, 'app')):
            with self.subTest(subtype=subtype):
                result = parse_app(card(subtype, description='真实内容'), subtype)
                self.assertEqual((result['title'], result['kind']), ('真实内容', kind))
                self.assertEqual(result['url'], '')
        for payload, expected in (
            ('<sourcename>来源</sourcename>', '来源'),
            ('<sourcedisplayname>显示名</sourcedisplayname>', '显示名'),
            ('<weappinfo><title>深层标题</title></weappinfo>', '深层标题'),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(parse_app(card(33, payload=payload), 33)['title'], expected)

    def test_placeholder_only_does_not_leak_into_markdown(self):
        for title in (PLACEHOLDER, '当前微信版本不支持展示该内容', '暂不支持查看',
                      'Your current WeChat version DOES NOT SUPPORT THIS CONTENT.'):
            for subtype, label in ((51, '视频号'), (124, '礼物'), (999, '消息')):
                with self.subTest(title=title, subtype=subtype):
                    item = row(card(subtype, title=title, description=PLACEHOLDER), subtype)
                    self.assertEqual(message_text(item), '[' + label + ']')

    def test_url_filter_and_fallbacks(self):
        for url in (UPGRADE, 'https://SUPPORT.WEIXIN.QQ.COM:443/security/readtemplate?t=upgrade',
                    'https://support.weixin.qq.com./update/', 'javascript:alert(1)',
                    'file:///C:/test', 'https://[invalid', 'https://', 'https://user:pass@example.com/'):
            with self.subTest(url=url):
                payload = '<url1>https://example.com/real</url1>'
                self.assertEqual(parse_app(card(5, '链接', payload=payload, url=url), 5)['url'],
                                 'https://example.com/real')
        # Match the hostname, not a substring in a different site's path/query.
        for url in ('https://example.com/?next=support.weixin.qq.com',
                    'https://support.weixin.qq.com.example.org/content'):
            self.assertEqual(parse_app(card(5, '链接', url=url), 5)['url'], url)
        self.assertEqual(parse_app(card(payload=feed(url=UPGRADE)
                                       + '<payload><videoUrl>https://example.com/video</videoUrl></payload>'), 51)['url'],
                         'https://example.com/video')

    def test_reference_and_record_payloads_are_not_outer_card_content(self):
        for wrapper in ('refermsg', 'recorditem'):
            payload = f'<{wrapper}><title>内层标题</title><desc>内层描述</desc><url>https://example.com/inner</url></{wrapper}>'
            result = parse_app(card(999, payload=payload), 999)
            self.assertEqual((result['title'], result['url']), ('', ''))
        quote = ('<refermsg><type>1</type><svrid>42</svrid><displayname>好友</displayname>'
                 '<content>请升级至最新版本，这是被引用的原话</content><url>https://example.com/inner</url></refermsg>')
        result = parse_app(card(57, title='我的回复', payload=quote), 57)
        self.assertEqual(result['kind'], 'quote')
        self.assertEqual(result['title'], '我的回复')
        self.assertEqual(result['quote']['text'], '请升级至最新版本，这是被引用的原话')
        self.assertEqual(result['url'], '')
        self.assertFalse(result['placeholder'])

    def test_malformed_xml_falls_back_without_upgrade_text_or_url(self):
        malformed = card()[:-6]
        result = parse_app(malformed, 51)
        self.assertEqual((result['kind'], result['title'], result['url']), ('channel', '', ''))
        self.assertEqual(message_text(row(malformed)), '[视频号]')

    def test_normal_links_full_description_and_literal_cdata(self):
        description = '长描述' * 300
        item = row(card(5, '普通标题', description, url=PAYLOAD_URL), 5)
        self.assertIn(description, message_text(item))
        self.assertEqual(len(parse_app(item['content'], 5)['des']), 400)
        result = parse_app('<appmsg><title><![CDATA[保留 &lt; 字面量]]></title>'
                           '<url><![CDATA[https://example.com/?x=1&y=2]]></url></appmsg>', 5)
        self.assertEqual(result['title'], '保留 &lt; 字面量')
        self.assertEqual(result['url'], 'https://example.com/?x=1&y=2')

    def test_missing_database_subtype_uses_xml_type(self):
        result = parse_app(card(6, '报告.txt', payload='<appattach><totallen>123</totallen><fileext>txt</fileext></appattach>'), 0)
        self.assertEqual((result['kind'], result['size'], result['ext']), ('file', 123, 'txt'))
        self.assertEqual(parse_app(card(payload=feed()), 0)['kind'], 'channel')

    def test_nested_cover_and_deep_payload_do_not_break_parsing(self):
        payload = ('<finderFeed><cover>https://example.com/cover.jpg</cover></finderFeed>'
                   + '<payload>' * 1100 + '<title>深层标题</title>' + '</payload>' * 1100)
        result = parse_app(card(33, payload=payload), 33)
        self.assertEqual(result['title'], '深层标题')
        self.assertEqual(result['thumb'], 'https://example.com/cover.jpg')

    def test_preview_html_markdown_and_pdf_page_share_fixed_fields(self):
        media = Mock()
        media.avatar.return_value = None
        renderer = Renderer(ROOT / 'assets', media)
        items = [row(card(payload=feed())),
                 dict(row(card(63, description='直播标题'), 63), id=2),
                 dict(row(card(124, description=PLACEHOLDER), 124), id=3)]
        def displayed(page):
            return re.sub(r'<details class="original-msg">.*?</details>', '', page, flags=re.S)

        with tempfile.TemporaryDirectory(prefix='wxexport_appmsg_') as tmp:
            root = pathlib.Path(tmp)
            renderer.export(items, '卡片', root / 'chat.html', '', '', False, lambda *_: None)
            export_markdown(items, '卡片', root / 'chat.md', '', '', False, False, media, lambda *_: None)
            build_print_page(renderer, items, '卡片', root / 'print.html', '', '', False, lambda *_: None)
            page = (root / 'chat.html').read_text(encoding='utf-8')
            # The original XML remains available for auditing in HTML only.
            self.assertIn(PLACEHOLDER, page)
            self.assertIn('原始消息内容', page)
            for output in (displayed(renderer.preview(items)), displayed(page),
                           (root / 'print.html').read_text(encoding='utf-8'),
                           (root / 'chat.md').read_text(encoding='utf-8')):
                with self.subTest(output=output[:60]):
                    self.assertIn('真实视频标题 &lt;片段&gt;', output)
                    self.assertIn('直播标题', output)
                    self.assertIn('礼物', output)
                    self.assertNotIn(PLACEHOLDER, output)
                    self.assertNotIn('support.weixin.qq.com', output)
                    self.assertNotIn('[引用]', output)
            self.assertIn(html.escape(PAYLOAD_URL, quote=True), displayed(page))
            self.assertIn(PAYLOAD_URL, (root / 'chat.md').read_text(encoding='utf-8'))
        media.resolve.assert_not_called()


if __name__ == '__main__':
    unittest.main()
