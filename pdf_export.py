"""Print a local, eager-loading document using the existing WebView2 runtime."""
from __future__ import annotations

import datetime
import html
import io
import os
import pathlib
import re
import tempfile
import threading
import time

from PIL import Image
from storage import TZ
from vendor import exporter


PRINT_CSS = '''
@page{size:A4;margin:14mm 12mm 16mm}
html{scroll-behavior:auto}body{background:white;padding:0;font-size:12px;line-height:1.65;
 -webkit-print-color-adjust:exact;print-color-adjust:exact}
#top{position:static;display:block;background:white;padding:0 0 12px;border-bottom:1px solid #ddd}
#top h1{font-size:20px;color:#253332;margin-bottom:6px}#top .sub{color:#53645b;font-size:11px}
#wrap{max-width:none;padding:8px 0}.day{break-after:avoid;page-break-after:avoid;margin:14px 0 10px}
.day span{color:#43524a;background:#e8ede9}.row{break-inside:avoid;page-break-inside:avoid;margin-bottom:12px}
.row.long{break-inside:auto;page-break-inside:auto;display:block;position:relative}
.row.long>.av{position:absolute;left:0;top:0}.row.long.self>.av{left:auto;right:0}
.row.long .col{display:block;margin-left:38px}.row.long.self .col{margin-left:auto;margin-right:38px}
.row.long .bub{display:block}
.col{max-width:78%;overflow-wrap:anywhere}.row .av{width:28px;height:28px;flex-basis:28px}
.bub{box-shadow:none;border:1px solid #e3e8e4;overflow-wrap:anywhere;white-space:pre-wrap}
.bub.sys{color:#43524a;background:#edf0ed}.who{color:#53645b;font-size:10px}
img.pic{max-width:100%;max-height:210mm;object-fit:contain;box-shadow:none}
.emoimg{width:80px;height:80px}.card{box-shadow:none;border:1px solid #e3e8e4;max-width:100%}
.card .de{display:block;-webkit-line-clamp:unset;overflow:visible;color:#53645b}
.tag{color:#53645b}.original-msg{display:none}.quote{overflow-wrap:anywhere}
.unknown{white-space:pre-wrap;overflow-wrap:anywhere}.wx-inline-emoji{width:18px;height:18px;vertical-align:-4px}
'''


def build_print_page(renderer, rows, title, target, start, end, online, notify):
    out, missing, used = [], [], set()
    embedded, last = 0, ''
    for index, row in enumerate(rows):
        day = datetime.datetime.fromtimestamp(row['ts'], TZ).strftime('%Y-%m-%d')
        if day != last:
            out.append('<div class="day"><span>' + day + '</span></div>')
            last = day
        used.update(n for n in re.findall(r'\[([^\[\]\r\n]{1,20})\]', row['content']) if n in renderer.emoji)
        result, error = None, ''
        if row['type'] in (3, 47):
            try:
                result = renderer.media.resolve(row, online)
                if result.get('frames', 1) > 1:
                    with Image.open(io.BytesIO(result['data'])) as im:
                        im.seek(0)
                        buffer = io.BytesIO()
                        im.convert('RGBA').save(buffer, format='PNG')
                    result = {**result, 'data': buffer.getvalue(), 'mime': 'image/png'}
                embedded += 1
            except Exception as exc:
                error = str(exc)
                missing.append({'message_id': row['id'], 'reason': error})
        fragment = renderer.message(row, result, error=error)
        fragment = re.sub(r'<details class="original-msg">.*?</details>', '', fragment, flags=re.S)
        fragment = fragment.replace('loading="lazy"', 'loading="eager"')
        if len(row['content']) > 1200 or row['content'].count('\n') > 18:
            fragment = fragment.replace('class="row', 'class="row long', 1)
        if row['type'] not in (1, 3, 34, 35, 37, 42, 43, 47, 48, 49, 50, 51, 62, 65, 10000, 10002):
            fragment = '<div class="row long"><div class="col"><div class="who">' + html.escape(row['sender_display']) + ' ' + datetime.datetime.fromtimestamp(row['ts'], TZ).strftime('%Y-%m-%d %H:%M:%S') + '</div><div class="bub unknown">[未解析消息] ' + html.escape(row['content']) + '</div></div></div>'
        out.append(fragment)
        notify(f'正在准备 PDF {index + 1}/{len(rows)} 条消息', int(75 * (index + 1) / max(1, len(rows))))
    avatars = renderer.avatar_map([r['sender'] for r in rows])
    css = exporter.EXPORT_CSS + renderer.emoji_css(used) + renderer.avatar_css(avatars) + PRINT_CSS
    note = f'{len(rows)} 条消息 · 北京时间（UTC+8）· 导出于 ' + datetime.datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
    scope = '开始：' + (start.replace('T', ' ') or '不限') + '；结束：' + (end.replace('T', ' ') or '不限') + '（含起止时刻）'
    notice = 'GIF 显示首帧；语音、视频及文件保留记录，附件未嵌入。'
    if missing:
        notice += f' {len(missing)} 个图片/表情缺失，已在对应消息标注。'
    page = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>' + html.escape(title) + '</title><style>' + css + '</style></head><body><div id="top"><h1>' + html.escape(title) + ' 聊天记录</h1><div class="sub">' + html.escape(note) + '<br>' + html.escape(scope) + '<br>' + html.escape(notice) + '</div></div><div id="wrap">' + ''.join(out) + '</div></body></html>'
    pathlib.Path(target).write_text(page, encoding='utf-8')
    return {'format': 'pdf', 'messages': len(rows), 'embedded': embedded, 'missing': missing, 'avatars': len(avatars)}


def print_to_pdf(source, target, title, notify, timeout=180):
    # This function runs on the export worker; WebView2 calls run on its UI thread.
    import webview
    from System import Func, Object

    window = webview.create_window('正在生成 PDF', url=pathlib.Path(source).resolve().as_uri(),
                                   hidden=True, width=1000, height=800)
    try:
        if not window.events.loaded.wait(timeout):
            raise TimeoutError('PDF 页面加载超时')
        ready = threading.Event()
        status = []
        def on_ready(value):
            status.append(value)
            ready.set()
        window.evaluate_js('''(async()=>{await document.fonts.ready;
          await Promise.all([...document.images].map(im=>im.complete?Promise.resolve():new Promise(r=>{im.onload=r;im.onerror=r})));
          await new Promise(r=>setTimeout(r,150));
          return [...document.images].every(im=>im.naturalWidth>0)})()''', on_ready)
        if not ready.wait(timeout):
            raise TimeoutError('PDF 图片或字体加载超时')
        if not status or status[0] is not True:
            raise RuntimeError('PDF 页面图片加载失败，请重新导出')
        notify('正在生成 PDF 分页文档，请稍候', 88)
        def start_print():
            core = window.native.webview.CoreWebView2
            settings = core.Environment.CreatePrintSettings()
            settings.PageWidth = 210 / 25.4
            settings.PageHeight = 297 / 25.4
            settings.MarginTop = 12 / 25.4
            settings.MarginBottom = 15 / 25.4
            settings.MarginLeft = settings.MarginRight = 12 / 25.4
            settings.ShouldPrintBackgrounds = True
            settings.ShouldPrintHeaderAndFooter = True
            settings.HeaderTitle = title
            settings.FooterUri = ' '
            return core.PrintToPdfAsync(str(pathlib.Path(target).resolve()), settings)
        task = window.native.Invoke(Func[Object](start_print))
        deadline = time.monotonic() + timeout
        while not task.IsCompleted:
            if time.monotonic() >= deadline:
                raise TimeoutError('PDF 生成超时，请缩小时间范围后重试')
            time.sleep(0.1)
        if not task.GetAwaiter().GetResult():
            raise RuntimeError('WebView2 未能生成 PDF，请检查保存路径')
        if not pathlib.Path(target).is_file() or pathlib.Path(target).stat().st_size < 100:
            raise RuntimeError('PDF 文件生成不完整')
    finally:
        window.destroy()


def export_pdf(renderer, rows, title, path, start, end, online, notify):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.wxexport-pdf-', dir=path.parent) as folder:
        root = pathlib.Path(folder)
        result = build_print_page(renderer, rows, title, root / 'print.html', start, end, online, notify)
        temporary = root / 'output.pdf'
        print_to_pdf(root / 'print.html', temporary, title, notify)
        os.replace(temporary, path)
    return {**result, 'path': str(path), 'bytes': path.stat().st_size}
