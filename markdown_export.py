"""Portable Markdown export, with optional local, deduplicated media."""
from __future__ import annotations

import datetime
import hashlib
import html
import os
import pathlib
import re
import shutil
import tempfile
from urllib.parse import quote

from storage import TZ
from vendor.formatters import KIND_ZH, row_to_msg, unxml


def escape(text):
    text = str(text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', text).replace('\r\n', '\n').replace('\r', '\n').replace('\n', '  \n')


def raw_block(content):
    fence = '`' * max(3, 1 + max((len(m) for m in re.findall(r'`+', content)), default=0))
    return fence + '\n' + content + '\n' + fence


def message_text(row, render_text=escape):
    m = row_to_msg(row, None, {})
    kind = m['kind']
    if kind == 'text':
        return render_text(row['content'])
    if kind in ('image', 'sticker'):
        return '[' + KIND_ZH[kind] + ']'
    if kind == 'unknown' or (kind == 'app' and not m.get('title')):
        return '[未解析消息：类型 ' + str(row['type']) + ']\n\n' + raw_block(row['content'])
    if kind == 'system':
        return render_text(m.get('text'))
    parts = []
    reference = m.get('quote')
    if reference:
        text = reference.get('text', '')
        if reference.get('type') == '1':
            ref = re.search(r'<refermsg>(.*?)</refermsg>', row['content'], re.S)
            match = re.search(r'<content>(.*?)</content>', ref.group(1), re.S) if ref else None
            if match:
                text = unxml(match.group(1))
        parts.append('\n'.join('> ' + line for line in (escape((reference.get('from') or '') + '：') + render_text(text)).split('\n')))
    label = KIND_ZH.get(kind, '消息')
    if m.get('title'):
        parts.append('[' + label + '] ' + render_text(m['title']))
    elif not reference:
        parts.append('[' + label + ']')
    des = re.search(r'<des>(.*?)</des>', row['content'], re.S)
    description = unxml(des.group(1)) if des else m.get('des', '')
    if description:
        parts.append(render_text(description))
    url = m.get('url', '')
    if re.match(r'^https?://', url, re.I):
        parts.append('[打开链接](<' + quote(url, safe=':/?#@!$&\'()*+,;=%-._~').replace('>', '%3E') + '>)')
    if 'duration' in m:
        parts.append('时长：' + str(m['duration']) + ' 秒')
    if kind in ('voice', 'video', 'file'):
        parts.append('[' + label + '附件未嵌入]')
    if m.get('size'):
        parts.append('文件大小：' + str(m['size']) + ' 字节')
    if kind == 'location' and m.get('label') != m.get('title'):
        parts.append(render_text(m.get('label')))
    return '\n\n'.join(parts)


def export_markdown(rows, title, path, start, end, online, include_media, media, notify, emoji=None):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = pathlib.Path(tempfile.mkdtemp(prefix='.wxexport-', dir=path.parent))
    assets = path.with_name(path.stem + '_assets')
    number = 2
    while assets.exists():
        assets = path.with_name(path.stem + '_assets_' + str(number))
        number += 1
    media_stage = stage / 'media'
    missing, saved = [], {}
    embedded = 0
    inline_emojis, emoji_files = 0, set()
    published_assets = False
    def save_image(data, mime, prefix=''):
        digest = hashlib.sha256(data).hexdigest()
        if digest not in saved:
            ext = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp', 'image/bmp': '.bmp'}.get(mime)
            if not ext:
                raise ValueError('不支持的图片格式：' + mime)
            media_stage.mkdir(exist_ok=True)
            filename = prefix + digest + ext
            (media_stage / filename).write_bytes(data)
            saved[digest] = filename
        return quote(assets.name + '/' + saved[digest], safe='/'), digest

    def inline(text):
        nonlocal inline_emojis
        text = str(text or '')
        if not include_media or not emoji:
            return escape(text)
        output, cursor = [], 0
        # Parse only original display text, never generated Markdown or URLs.
        for match in re.finditer(r'\[([^\[\]\r\n]{1,20})\]', text):
            name = match.group(1)
            if name not in emoji:
                continue
            output.append(escape(text[cursor:match.start()]))
            src, digest = save_image(emoji[name], 'image/png', 'emoji_')
            label = html.escape(match.group(), quote=True)
            output.append('<img src="' + src + '" alt="' + label + '" title="' + label + '" width="22" height="22" style="vertical-align:middle">')
            inline_emojis += 1
            emoji_files.add(digest)
            cursor = match.end()
        output.append(escape(text[cursor:]))
        return ''.join(output)

    try:
        parts = ['# ' + escape(title) + ' 聊天记录',
                 '- 时间范围：' + escape(start.replace('T', ' ') or '不限') + ' 至 ' + escape(end.replace('T', ' ') or '不限'),
                 '- 时区：北京时间（UTC+8），包含起止时刻',
                 '- 消息数量：' + str(len(rows)) + ' 条',
                 '- 图片和表情：' + ('照片、自定义表情及内置表情使用相对路径，移动时请同时携带资源文件夹' if include_media else '使用文字占位，未读取或下载图片'),
                 '- 语音、视频及文件保留消息记录，附件未嵌入']
        last = ''
        for index, row in enumerate(rows):
            stamp = datetime.datetime.fromtimestamp(row['ts'], TZ)
            day = stamp.strftime('%Y-%m-%d')
            if day != last:
                parts.append('## ' + day)
                last = day
            sender = row['sender_display'] or ('我' if row['is_self'] else row['sender']) or '系统'
            parts.append('### ' + stamp.strftime('%H:%M:%S') + ' ' + escape(sender))
            text = message_text(row, inline)
            if include_media and row['type'] in (3, 47):
                label = '图片' if row['type'] == 3 else '表情'
                try:
                    result = media.resolve(row, online)
                    src, _ = save_image(result['data'], result['mime'])
                    text = '![' + label + '](' + src + ')'
                    embedded += 1
                except Exception as exc:
                    missing.append({'message_id': row['id'], 'reason': str(exc)})
                    text = '[' + label + '未导出：' + escape(str(exc)) + ']'
            parts.append(text)
            notify(f'正在生成 Markdown {index + 1}/{len(rows)} 条消息', int(95 * (index + 1) / max(1, len(rows))))
        temporary = stage / 'export.md'
        temporary.write_text('\n\n'.join(parts) + '\n', encoding='utf-8')
        if saved:
            os.replace(media_stage, assets)
            published_assets = True
        os.replace(temporary, path)
        return {'path': str(path), 'format': 'md', 'messages': len(rows), 'embedded': embedded,
                'missing': missing, 'bytes': path.stat().st_size, 'assets_path': str(assets) if saved else '',
                'include_media': include_media, 'media_files': len(saved),
                'inline_emojis': inline_emojis, 'emoji_files': len(emoji_files)}
    except Exception:
        if published_assets:
            shutil.rmtree(assets)
        raise
    finally:
        shutil.rmtree(stage)
