import base64
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.request
from unittest.mock import Mock, patch

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from media import Media, Redirect, allowed, emoji_node
from rendering import Renderer


def protobuf_bytes(field, data):
    def varint(value):
        output = bytearray()
        while value > 127:
            output.append((value & 127) | 128)
            value >>= 7
        output.append(value)
        return bytes(output)
    return varint(field << 3 | 2) + varint(len(data)) + data


class StickerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wxexport_stickers_')
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.account = self.root / 'account'
        self.account.mkdir()
        self.store = Mock()
        self.store.current.return_value = str(self.account)
        self.media = Media(self.store, self.root)
        frames = [Image.new('RGB', (24, 24), color) for color in ('red', 'blue')]
        output = io.BytesIO()
        frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:], duration=100, loop=0)
        self.data = output.getvalue()
        self.digest = hashlib.md5(self.data).hexdigest()
        self.url = 'http://mmbiz.qpic.cn/mmemoticon/test/0'
        self.row = dict(id=1, username='test', server_id='2', local_id=3,
                        ts=1792938610, sender='test', sender_display='测试',
                        is_self=1, type=47, subtype=0,
                        content=f'<msg><emoji md5="{self.digest}" cdnurl="{self.url}" /></msg>')

    def opener(self, data=None):
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=Mock(read=Mock(return_value=self.data if data is None else data)))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        return opener

    def app_row(self, payload=None, digest=None):
        if payload is None:
            payload = protobuf_bytes(1, self.digest.encode()) + protobuf_bytes(2, self.url.encode())
        encoded = base64.b64encode(payload).decode()
        digest = self.digest if digest is None else digest
        return dict(self.row, type=49, subtype=8,
                    content='<msg><appmsg><type>8</type><appattach>'
                    f'<emoticonmd5>{digest}</emoticonmd5><emojiinfo>{encoded}</emojiinfo>'
                    '</appattach></appmsg></msg>')

    def test_app_gif_download_preserves_original_and_offline_cache(self):
        row = self.app_row()
        opener = self.opener()
        with patch('media.urllib.request.build_opener', return_value=opener):
            result = self.media.resolve(row)
        self.assertEqual(result['data'], self.data)
        self.assertEqual((result['mime'], result['frames']), ('image/gif', 2))
        self.assertEqual(opener.open.call_args.args[0].full_url, self.url)
        # The MD5 alone must suffice after the original was cached.
        offline_row = self.app_row(payload=b'')
        with patch('media.urllib.request.build_opener', side_effect=AssertionError('离线模式不能联网')):
            self.assertEqual(Media(self.store, self.root).resolve(offline_row, False)['data'], self.data)

    def test_app_gif_protobuf_long_url_and_unknown_fields(self):
        url = self.url + '?token=' + 'a' * 300
        payload = (b'\x50\x96\x01' + b'\x59' + b'\x00' * 8 + b'\x65' + b'\x00' * 4
                   + protobuf_bytes(5, b'https://mmbiz.qpic.cn/encrypted-variant')
                   + protobuf_bytes(2, url.encode())
                   + protobuf_bytes(1, self.digest.upper().encode()))
        row = self.app_row(payload, digest='')
        for prefix in ('', 'sender:\n', 'sender:\r\n', r'sender:\n', r'sender:\r\n'):
            with self.subTest(prefix=prefix):
                content = prefix + row['content'] + '\n\x00\x00'
                node = emoji_node(content)
                self.assertEqual(node.get('md5'), self.digest.upper())
                self.assertEqual(node.get('cdnurl'), url)

    def test_app_gif_rejects_malformed_protobuf_and_base64(self):
        for payload in (b'\x0a\x20short', b'\x12\x80', b'\x00\x00', b'\x0b',
                        b'\x80' * 11, b'\x09short', b'\x0dxx', b'\x12\x01\xff'):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, 'GIF 表情资源信息格式不正确'):
                    self.media.resolve(self.app_row(payload))
        row = self.app_row()
        encoded = base64.b64encode(protobuf_bytes(1, self.digest.encode()) + protobuf_bytes(2, self.url.encode())).decode()
        with self.assertRaisesRegex(ValueError, 'GIF 表情资源信息格式不正确'):
            emoji_node(row['content'].replace(encoded, '!!!'))

    def test_app_gif_digest_and_url_validation(self):
        with patch('media.urllib.request.build_opener') as build:
            with self.assertRaisesRegex(ValueError, '资源标识不一致'):
                self.media.resolve(self.app_row(digest='a' * 32))
            with self.assertRaisesRegex(ValueError, '资源标识不合法'):
                self.media.resolve(self.app_row(payload=b'', digest='../' + 'a' * 29))
            payload = protobuf_bytes(2, b'https://example.com/untrusted.gif')
            with self.assertRaisesRegex(ValueError, '没有可用'):
                self.media.resolve(self.app_row(payload))
            build.assert_not_called()
        with patch('media.urllib.request.build_opener', return_value=self.opener(b'wrong image')):
            with self.assertRaisesRegex(ValueError, '完整性校验失败'):
                self.media.resolve(self.app_row())
        self.assertFalse((self.media.cache / (self.digest + '.image')).exists())

    def test_app_gif_preview_html_and_missing_placeholder(self):
        self.store.avatar.return_value = None
        renderer = Renderer(ROOT / 'assets', self.media)
        row = self.app_row()
        preview = renderer.preview([row])
        self.assertIn('media-wait', preview)
        self.assertIn('点击加载表情', preview)
        self.assertNotIn('sticker_gif', preview)
        with patch('media.urllib.request.build_opener', return_value=self.opener()):
            result = self.media.resolve(row)
        fragment = renderer.message(row, result)
        self.assertIn('class="emoimg"', fragment)
        embedded = 'data:image/gif;base64,' + base64.b64encode(self.data).decode()
        self.assertIn(embedded, fragment)
        target = self.root / 'app-gif.html'
        exported = renderer.export([row], 'GIF 表情', target, '', '', False, lambda *_: None)
        self.assertEqual((exported['embedded'], exported['missing']), (1, []))
        page = target.read_text(encoding='utf-8')
        self.assertIn(embedded, page)
        self.assertNotIn(self.url, page)
        metadata = json.loads(page.split('id="export-metadata">')[1].split('</script>')[0])
        self.assertEqual(metadata['media'][0]['frames'], 2)
        self.assertEqual(metadata['media'][0]['sha256'], hashlib.sha256(self.data).hexdigest())
        missing = renderer.message(row, error='资源缺失')
        self.assertIn('[表情未嵌入：资源缺失]', missing)
        self.assertNotIn('sticker_gif', missing)

    def test_app_gif_markdown_and_pdf_share_media_support(self):
        from markdown_export import export_markdown, message_text
        from pdf_export import build_print_page
        row = self.app_row()
        self.store.avatar.return_value = None
        (self.media.cache / (self.digest + '.image')).write_bytes(self.data)
        self.assertEqual(message_text(row), '[表情]')
        md = export_markdown([row], 'GIF', self.root / 'app.md', '', '', False, True, self.media, lambda *_: None)
        self.assertEqual((md['embedded'], md['missing']), (1, []))
        gif = next(pathlib.Path(md['assets_path']).glob('*.gif'))
        self.assertEqual(gif.read_bytes(), self.data)
        target = self.root / 'print.html'
        pdf = build_print_page(Renderer(ROOT / 'assets', self.media), [row], 'GIF', target, '', '', False, lambda *_: None)
        self.assertEqual((pdf['embedded'], pdf['missing']), (1, []))
        self.assertIn('data:image/png', target.read_text(encoding='utf-8'))
        self.assertNotIn('data:image/gif', target.read_text(encoding='utf-8'))

    def test_wechat_hosts_and_malformed_urls(self):
        for url in (self.url, 'https://emoji.qpic.cn/wx_emoji/a',
                    'https://vweixinf.tc.qq.com/a', 'https://QQ.COM/a'):
            with self.subTest(url=url):
                self.assertTrue(allowed(url))
        for url in ('https://qpic.cn.evil.example/a', 'https://evilqpic.cn/a',
                    'https://evilqq.com/a', 'file:///test', 'https://example.com/a',
                    'https://user:password@mmbiz.qpic.cn/a', 'https://[bad'):
            with self.subTest(url=url):
                self.assertFalse(allowed(url))

    def test_qpic_download_preserves_animation_and_offline_cache(self):
        opener = self.opener()
        with patch('media.urllib.request.build_opener', return_value=opener):
            result = self.media.resolve(self.row)
        self.assertEqual(result['data'], self.data)
        self.assertEqual((result['mime'], result['frames']), ('image/gif', 2))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, self.url)
        with patch('media.urllib.request.build_opener', side_effect=AssertionError('离线模式不应联网')):
            offline = Media(self.store, self.root).resolve(self.row, online=False)
        self.assertEqual(offline['data'], self.data)

    def test_group_sender_prefixes(self):
        for prefix in ('sender:\n', 'sender:\r\n', r'sender:\n', r'sender:\r\n'):
            with self.subTest(prefix=prefix):
                row = dict(self.row, content=prefix + self.row['content'])
                with patch('media.urllib.request.build_opener', return_value=self.opener()):
                    result = Media(self.store, self.root).resolve(row)
                self.assertEqual(result['data'], self.data)
        self.assertEqual(emoji_node(f'<emoji md5="{self.digest}"/>').get('md5'), self.digest)

    def test_uppercase_digest_and_whitespace_url(self):
        row = dict(self.row, content=f'<msg><emoji md5="{self.digest.upper()}" cdnurl=" {self.url} " /></msg>')
        with patch('media.urllib.request.build_opener', return_value=self.opener()):
            self.assertEqual(self.media.resolve(row)['data'], self.data)

    def test_invalid_digest_cannot_be_used_as_cache_path(self):
        for digest in ('../' + 'a' * 29, 'g' * 32, ''):
            with self.subTest(digest=digest):
                row = dict(self.row, content=f'<msg><emoji md5="{digest}" cdnurl="{self.url}" /></msg>')
                with self.assertRaisesRegex(ValueError, '资源标识不合法'):
                    self.media.resolve(row)

    def test_untrusted_url_does_not_download(self):
        row = dict(self.row, content=self.row['content'].replace(self.url, 'https://example.com/a'))
        with patch('media.urllib.request.build_opener') as build:
            with self.assertRaisesRegex(ValueError, '没有可用'):
                self.media.resolve(row)
            build.assert_not_called()

    def test_redirect_checks_destination(self):
        redirect = Redirect()
        request = urllib.request.Request(self.url)
        with self.assertRaisesRegex(ValueError, '重定向'):
            redirect.redirect_request(request, None, 302, 'Found', {}, 'https://example.com/a')
        redirected = redirect.redirect_request(request, None, 302, 'Found', {}, 'https://emoji.qpic.cn/a')
        self.assertEqual(redirected.full_url, 'https://emoji.qpic.cn/a')

    def test_corrupt_cache_is_replaced_after_verified_download(self):
        saved = self.media.cache / (self.digest + '.image')
        saved.write_bytes(b'corrupt cache')
        local = self.account / 'cache/2026-10/Emoticon' / self.digest[:2] / self.digest
        local.parent.mkdir(parents=True)
        local.write_bytes(b'wrong local file')
        with patch('media.urllib.request.build_opener', return_value=self.opener()):
            self.assertEqual(self.media.resolve(self.row)['data'], self.data)
        self.assertEqual(saved.read_bytes(), self.data)
        self.assertEqual(local.read_bytes(), b'wrong local file')

    def test_offline_missing_cache_does_not_download(self):
        with patch('media.urllib.request.build_opener') as build:
            with self.assertRaises(FileNotFoundError):
                self.media.resolve(self.row, online=False)
            build.assert_not_called()

    def test_hash_mismatch_and_oversized_download_are_not_cached(self):
        for data in (b'wrong data', b'x' * ((32 << 20) + 1)):
            with self.subTest(bytes=len(data)):
                with patch('media.urllib.request.build_opener', return_value=self.opener(data)):
                    with self.assertRaisesRegex(ValueError, '完整性校验失败'):
                        self.media.resolve(self.row)
                self.assertFalse((self.media.cache / (self.digest + '.image')).exists())

    def test_export_and_preview_embed_original_gif(self):
        self.store.avatar.return_value = None
        renderer = Renderer(ROOT / 'assets', self.media)
        target = self.root / 'stickers.html'
        with patch('media.urllib.request.build_opener', return_value=self.opener()):
            result = renderer.export([self.row], '测试', target, '', '', True, lambda *_: None)
        self.assertEqual((result['embedded'], result['missing']), (1, []))
        embedded = 'data:image/gif;base64,' + base64.b64encode(self.data).decode()
        page = target.read_text(encoding='utf-8')
        self.assertIn(embedded, page)
        metadata = json.loads(page.split('id="export-metadata">')[1].split('</script>')[0])
        self.assertEqual(metadata['media'][0]['frames'], 2)
        self.assertIn(embedded, renderer.message(self.row, self.media.resolve(self.row)))


if __name__ == '__main__':
    unittest.main()
