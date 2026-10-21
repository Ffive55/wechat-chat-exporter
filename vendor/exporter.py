# -*- coding: utf-8 -*-
"""wxlocal — 会话导出

把一个会话导出成自包含归档（zip：index.html + data.json + media/）。
媒体在写 zip 的同一次遍历里落盘，写完再从记录表回填 HTML —— 不需要二次打开。

Python 3.14 | 无第三方依赖
"""
from __future__ import annotations

import os
import io
import re
import json
import html as _html
import sqlite3
import datetime
import tempfile
import urllib.parse
import zipfile
from typing import Any, Callable, Optional

__all__ = ["build_zip", "build_html", "EXPORT_CSS"]


# ── 工具 ────────────────────────────────────────────────────────────────────
def _safe_name(s: str, fallback: str = "chat") -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (s or "").strip())
    s = s.strip(" .")
    return (s or fallback)[:60]


_IMG_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/webp": ".webp", "image/bmp": ".bmp",
}


def _esc(s: Any) -> str:
    return _html.escape("" if s is None else str(s), quote=True)


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _fmt_ts(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _fmt_size(n: Any) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return ""


# ── 样式 ────────────────────────────────────────────────────────────────────
EXPORT_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
 background:#ededed;color:#1a1a1a;padding:0 0 80px}
#top{position:sticky;top:0;background:#f7f7f7;border-bottom:1px solid #ddd;
 padding:11px 24px;z-index:9;display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
#top h1{font-size:16px;font-weight:600}
#top .sub{font-size:12px;color:#8b8b8b}
#top input{margin-left:auto;height:28px;border:1px solid #ddd;border-radius:14px;
 padding:0 12px;font:inherit;font-size:12px;outline:none;background:#fff;width:240px}
#top input:focus{border-color:#07c160}
#wrap{max-width:920px;margin:0 auto;padding:22px 24px}
.day{text-align:center;margin:20px 0 12px}
.day span{background:#d5d5d5;color:#fff;font-size:11px;padding:2px 9px;border-radius:4px}
.row{display:flex;gap:10px;margin-bottom:14px;align-items:flex-start}
.row.self{flex-direction:row-reverse}
.row .av{width:34px;height:34px;border-radius:4px;flex:0 0 34px;object-fit:cover;
 background:#cfcfcf;display:grid;place-items:center;color:#fff;font-size:12px;
 overflow:hidden;user-select:none}
.col{max-width:min(640px,74%);min-width:0}
.who{font-size:11px;color:#8b8b8b;margin:0 0 3px 2px}
.row.self .who{text-align:right;margin:0 2px 3px 0}
.bub{background:#fff;border-radius:5px;padding:8px 11px;word-break:break-word;
 white-space:pre-wrap;box-shadow:0 1px 1px rgba(0,0,0,.06);display:inline-block;
 max-width:100%;text-align:left}
.row.self .bub{background:#95ec69}
.bub.sys{background:#d5d5d5;color:#fff;font-size:12px;text-align:center;
 box-shadow:none;border-radius:4px;padding:3px 10px;white-space:normal}
.row.center{justify-content:center}
.row.center .col{max-width:84%;display:flex;justify-content:center}
.bub a{color:#576b95;text-decoration:none}
.bub a:hover{text-decoration:underline}
img.pic{max-width:100%;max-height:520px;border-radius:4px;display:block;
 box-shadow:0 1px 2px rgba(0,0,0,.12);background:#f4f4f4}
video.pic{max-width:min(460px,100%);border-radius:4px;display:block;background:#000}
.card{display:flex;gap:10px;background:#fff;border-radius:5px;padding:10px 12px;
 box-shadow:0 1px 1px rgba(0,0,0,.06);text-decoration:none;color:inherit;
 max-width:420px;align-items:center}
a.card:hover{background:#fafafa}
.card .ic{width:38px;height:38px;flex:0 0 38px;border-radius:4px;background:#4b8bf5;
 color:#fff;display:grid;place-items:center;font-size:11px;font-weight:600;
 text-transform:uppercase;letter-spacing:.3px}
.card .ct{min-width:0;flex:1}
.card .ti{font-weight:500;word-break:break-all;line-height:1.4}
.card .de{font-size:12px;color:#8b8b8b;margin-top:2px;display:-webkit-box;
 -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.45}
.card .th{width:54px;height:54px;flex:0 0 54px;object-fit:cover;border-radius:3px;background:#eee}
.emo{font-size:13px;color:#7a7a7a;font-style:italic}
.emoimg{width:104px;height:104px;object-fit:contain}
.vid{position:relative;display:inline-block;max-width:320px;border-radius:4px;
 overflow:hidden;background:#000;line-height:0}
.vid img{display:block;width:100%}
.vid .pl{position:absolute;inset:0;display:grid;place-items:center;font-size:34px;
 color:#fff;text-shadow:0 2px 8px rgba(0,0,0,.65);line-height:1}
.voice{display:inline-flex;align-items:center;gap:7px;color:#333;font-size:13px}
.quote{border-left:3px solid #d8d8d8;padding:4px 9px;margin-bottom:7px;color:#666;
 font-size:13px;background:#f7f7f7;border-radius:0 3px 3px 0;white-space:normal}
.tag{font-size:11px;color:#a0a0a0;font-style:italic}
.hid{display:none !important}
#nomsg{padding:60px 0;text-align:center;color:#999;display:none}
"""


# ── 消息 → HTML ─────────────────────────────────────────────────────────────
def _msg_html(m: dict, srcs: dict[int, str], thumbs: dict[int, str]) -> str:
    k = m.get("kind") or "unknown"
    mid = m["id"]
    if k == "system":
        return (f'<div class="row center"><div class="col">'
                f'<div class="bub sys">{_esc(m.get("text"))}</div></div></div>')

    self_cls = "row self" if m.get("is_self") else "row"
    if m.get("avatar_src"):
        av = f'<img class="av" src="{_esc(m["avatar_src"])}" alt="" loading="lazy">'
    else:
        av = f'<div class="av">{_esc((m.get("sender_name") or "?")[-2:])}</div>'
    who = _esc(m.get("sender_name") or ("我" if m.get("is_self") else ""))
    meta = f'{who}　{_fmt_ts(m["ts"])}' if who else _fmt_ts(m["ts"])

    body = ""
    if k == "text":
        body = f'<div class="bub">{_esc(m.get("text"))}</div>'

    elif k == "image":
        src = srcs.get(mid)
        body = (f'<a href="{_esc(src)}" target="_blank">'
                f'<img class="pic" src="{_esc(src)}" alt="" loading="lazy"></a>'
                if src else '<div class="bub"><span class="tag">[图片未缓存]</span></div>')

    elif k == "sticker":
        src = m.get("sticker_src")
        if src:
            body = (f'<a href="{_esc(src)}" target="_blank">'
                    f'<img class="emoimg" src="{_esc(src)}" alt="" loading="lazy"></a>')
        else:
            cap = m.get("caption")
            body = (f'<div class="bub"><span class="emo">{_esc(cap)}</span></div>' if cap
                    else '<div class="bub"><span class="tag">[表情]</span></div>')

    elif k == "video":
        src, th = srcs.get(mid), thumbs.get(mid)
        if src:
            body = (f'<video class="pic" src="{_esc(src)}" controls preload="metadata" '
                    f'playsinline></video>')
        elif th:
            body = (f'<div><div class="vid"><img src="{_esc(th)}" alt="" loading="lazy">'
                    f'<div class="pl">▶</div></div>'
                    f'<div class="tag" style="margin-top:4px">视频未缓存 · 仅缩略图</div></div>')
        else:
            body = '<div class="bub"><span class="tag">[视频未缓存]</span></div>'

    elif k == "voice":
        d = m.get("duration")
        body = (f'<div class="bub"><span class="voice">🎤 '
                f'{_esc(d)}″ <span class="tag">语音未落盘</span></span></div>')

    elif k == "file":
        name = m.get("title") or "文件"
        ext = (name.rsplit(".", 1)[-1] if "." in name else "FILE")[:4]
        sz = _fmt_size(m.get("size"))
        src = srcs.get(mid)
        inner = (f'<div class="ic">{_esc(ext)}</div>'
                 f'<div class="ct"><div class="ti">{_esc(name)}</div>'
                 f'<div class="de">{_esc(sz)}</div></div>')
        body = (f'<a class="card" href="{_esc(src)}" download>{inner}</a>' if src
                else f'<div class="card">{inner[:-6]}'
                     f'<div class="de">文件未缓存</div></div></div>')

    elif k in ("link", "music", "channel", "channel_live", "miniprogram"):
        th = thumbs.get(mid) or m.get("thumb_src")
        thh = f'<img class="th" src="{_esc(th)}" alt="" loading="lazy">' if th else ""
        inner = (f'<div class="ic">{_esc(k[:4].upper())}</div>'
                 f'<div class="ct"><div class="ti">{_esc(m.get("title"))}</div>'
                 f'<div class="de">{_esc(m.get("des"))}</div></div>{thh}')
        url = m.get("url")
        body = (f'<a class="card" href="{_esc(url)}" target="_blank" rel="noopener">'
                f'{inner}</a>' if url else f'<div class="card">{inner}</div>')

    elif k == "quote":
        q = m.get("quote") or {}
        head = (f'<div class="quote"><b>{_esc(q.get("from"))}</b><br>'
                f'{_esc((q.get("text") or "")[:200])}</div>' if q.get("text") else "")
        body = f'<div class="bub">{head}{_esc(m.get("title") or m.get("des"))}</div>'

    elif k == "chat_record":
        body = (f'<div class="card"><div class="ic">记录</div>'
                f'<div class="ct"><div class="ti">{_esc(m.get("title") or "聊天记录")}</div>'
                f'<div class="de">{_esc(m.get("des"))}</div></div></div>')

    elif k in ("transfer", "redpacket"):
        icon = "🧧 微信红包" if k == "redpacket" else "💸 转账"
        body = f'<div class="bub">{icon} · {_esc(m.get("title") or m.get("des"))}</div>'

    elif k == "mail":
        body = (f'<div class="card"><div class="ic">邮件</div>'
                f'<div class="ct"><div class="ti">{_esc(m.get("title"))}</div>'
                f'<div class="de">{_esc(m.get("sender"))}'
                f'{" · " + _esc(m.get("date")) if m.get("date") else ""}</div></div></div>')

    elif k == "location":
        ll = ""
        if m.get("x") and m.get("y"):
            ll = f' · {m["y"]}, {m["x"]}'
        body = (f'<div class="card"><div class="ic">位置</div>'
                f'<div class="ct"><div class="ti">{_esc(m.get("title") or "位置")}</div>'
                f'<div class="de">{_esc(m.get("label"))}{_esc(ll)}</div></div></div>')

    elif k == "voip":
        d = m.get("duration")
        tail = f' · {d}″' if d else ""
        body = f'<div class="bub"><span class="tag">📞 {_esc(m.get("title") or "通话")}{tail}</span></div>'

    elif k in ("friend", "card"):
        th = m.get("avatar_src") or ""
        thh = f'<img class="th" src="{_esc(th)}" alt="" loading="lazy">' if th else ""
        body = (f'<div class="card"><div class="ic">名片</div>'
                f'<div class="ct"><div class="ti">{_esc(m.get("title") or "名片")}</div>'
                f'<div class="de">{_esc(m.get("des"))}</div></div>{thh}</div>')

    else:
        body = (f'<div class="bub">{_esc(m.get("title") or m.get("text") or "")}'
                f'<div class="tag">{_esc(k)}</div></div>')

    return (f'<div class="{self_cls}">{av}<div class="col">'
            f'<div class="who">{meta}</div>{body}</div></div>')


def _render_page(display: str, msgs: list[dict], srcs: dict[int, str],
                 thumbs: dict[int, str], title_note: str = "") -> str:
    out: list[str] = []
    prev = ""
    for m in msgs:
        dk = datetime.datetime.fromtimestamp(m["ts"]).strftime("%Y-%m-%d")
        if dk != prev:
            out.append(f'<div class="day"><span>{dk}</span></div>')
            prev = dk
        out.append(_msg_html(m, srcs, thumbs))
    stamp = _now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(display)} · 聊天记录</title><style>{EXPORT_CSS}</style></head>
<body>
<div id="top">
  <h1>{_esc(display)}</h1>
  <div class="sub">{len(msgs):,} 条消息 · 导出于 {stamp}{_esc(title_note)}</div>
  <input id="q" placeholder="在本页查找…" autocomplete="off" spellcheck="false">
</div>
<div id="wrap">
{''.join(out)}
</div>
<div id="nomsg">没有匹配的消息</div>
<script>
(function(){{
  var q = document.getElementById('q'), nom = document.getElementById('nomsg');
  var rows = [].slice.call(document.querySelectorAll('#wrap .row'));
  var days = [].slice.call(document.querySelectorAll('#wrap .day'));
  q.addEventListener('input', function(){{
    var v = q.value.trim().toLowerCase(), hit = 0;
    rows.forEach(function(r){{
      var ok = !v || r.textContent.toLowerCase().indexOf(v) >= 0;
      r.classList.toggle('hid', !ok);
      if(ok && v) hit++;
    }});
    days.forEach(function(d){{ d.classList.toggle('hid', !!v); }});
    nom.style.display = (v && !hit) ? 'block' : 'none';
  }});
  q.addEventListener('keydown', function(e){{ if(e.key === 'Escape'){{ q.value=''; q.dispatchEvent(new Event('input')); }} }});
}})();
</script>
</body></html>"""


def _put(zf: zipfile.ZipFile, arc: str, data: bytes) -> None:
    """媒体一律 STORED：JPEG/PNG/MP4 已是压缩格式，DEFLATE 只烧 CPU 不省空间。"""
    zi = zipfile.ZipInfo(arc, date_time=_now().timetuple()[:6])
    zi.compress_type = zipfile.ZIP_STORED
    zi.external_attr = 0o644 << 16
    zf.writestr(zi, data)


def _put_file(zf: zipfile.ZipFile, arc: str, path: str) -> None:
    zi = zipfile.ZipInfo.from_file(path, arc)
    zi.compress_type = zipfile.ZIP_STORED
    with open(path, "rb") as f:
        zf.writestr(zi, f.read())


# ── 主流程 ──────────────────────────────────────────────────────────────────
def _raw_row(r) -> dict:
    """把一行 message 抠成可直接写进另一个 wxlocal 库的字段。

    行可能是 sqlite3.Row，也可能是 dict。缺的键给 None，导入端会各自兜底。
    """
    def g(k):
        try:
            return r[k]
        except (IndexError, KeyError, TypeError):
            return None

    return {
        "id": g("id"),
        "local_id": g("local_id") or 0,
        "server_id": g("server_id") or 0,
        "ts": g("ts") or 0,
        "sender": g("sender") or "",
        "sender_name": g("sender_display") or "",
        "is_self": 1 if g("is_self") else 0,
        "type": g("type") or 0,
        "subtype": g("subtype") or 0,
        "content": g("content") or "",
        "src": g("src") or "",
        "sort_seq": g("sort_seq") or 0,
    }


def build_zip(username: str, display: str,
              rows: list, media_map: dict[int, tuple[str, str]],
              vthumb_map: dict[int, str], row_to_msg: Callable,
              decrypt_image: Callable[[str], tuple[bytes, str]],
              out_dir: str,
              avatar_fn: Optional[Callable[[str], Optional[bytes]]] = None,
              sticker_map: Optional[dict[str, str]] = None,
              name_map: Optional[dict[int, str]] = None,
              ) -> tuple[str, dict]:
    """写一个自包含 zip，返回 (路径, 统计)。

    decrypt_image(path) -> (bytes, 扩展名)。只有图片走它；文件/视频原样拷贝。
    avatar_fn(username) -> bytes|None，用来把头像也打进归档（离线可看）。
    name_map(msg_id) -> 原始文件名，库里存的是 md5，归档要还原成人认得的名字。
    任何一个媒体失败都跳过，不中断导出。
    """
    os.makedirs(out_dir, exist_ok=True)
    name_map = name_map or {}
    base = f"{_safe_name(display)}_{_now():%Y%m%d_%H%M}"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=out_dir)
    tmp.close()

    srcs: dict[int, str] = {}
    thumbs: dict[int, str] = {}
    st = {"media_ok": 0, "media_skip": 0, "avatars": 0, "stickers": 0, "no_file": []}

    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for r in rows:
            mid = r["id"]
            if mid in media_map:
                kind, path = media_map[mid]
                if not os.path.exists(path):
                    st["media_skip"] += 1
                    st["no_file"].append(path)
                else:
                    try:
                        if kind in ("img", "image"):
                            data, ext = decrypt_image(path)
                            if data:
                                arc = f"media/{mid:09d}{ext}"
                                _put(zf, arc, data)
                                srcs[mid] = arc
                                st["media_ok"] += 1
                            else:
                                st["media_skip"] += 1
                        else:
                            # 文件/视频保留原文件名，带消息号前缀防重名
                            orig = name_map.get(mid) or os.path.basename(path)
                            arc = f"media/{mid:09d}_{_safe_name(orig, 'file')}"
                            _put_file(zf, arc, path)
                            srcs[mid] = arc
                            st["media_ok"] += 1
                    except Exception:
                        st["media_skip"] += 1
            if mid in vthumb_map:
                p = vthumb_map[mid]
                if os.path.exists(p):
                    try:
                        arc = f"media/{mid:09d}_t.jpg"
                        _put_file(zf, arc, p)
                        thumbs[mid] = arc
                        st["media_ok"] += 1
                    except Exception:
                        st["media_skip"] += 1

        msgs = [row_to_msg(r, None, {}) for r in rows]

        # 表情包：从 CDN 拉回来的明文副本，按 md5 去重写进 media/emo/
        if sticker_map:
            used: dict[str, str] = {}
            for m in msgs:
                if m.get("kind") != "sticker":
                    continue
                md5 = m.get("md5")
                if not md5 or md5 not in sticker_map:
                    continue
                if md5 in used:
                    m["sticker_src"] = used[md5]
                    continue
                p = sticker_map[md5]
                if not os.path.exists(p):
                    continue
                try:
                    ext = os.path.splitext(p)[1].lower() or ".gif"
                    arc = f"media/emo/{md5}{ext}"
                    _put_file(zf, arc, p)
                    used[md5] = arc
                    m["sticker_src"] = arc
                    st["stickers"] = st.get("stickers", 0) + 1
                except Exception:
                    pass

        # 头像：按 sender 去重，一边写进 zip 一边回填 avatar_src
        av_cache: dict[str, str] = {}
        if avatar_fn is not None:
            for m in msgs:
                u = m.get("sender") or ""
                if u in av_cache:
                    m["avatar_src"] = av_cache[u]
                    continue
                try:
                    blob = avatar_fn(u)
                except Exception:
                    blob = None
                if blob:
                    arc = f"avatar/{_safe_name(u, 'x')[:40]}.jpg"
                    try:
                        _put(zf, arc, blob)
                        av_cache[u] = arc
                        m["avatar_src"] = arc
                        st["avatars"] = st.get("avatars", 0) + 1
                    except Exception:
                        pass
                av_cache.setdefault(u, "")

        page = _render_page(display, msgs, srcs, thumbs)
        zf.writestr("index.html", page.encode("utf-8"))

        payload = {
            "username": username,
            "display": display,
            "exported_at": _now().isoformat(timespec="seconds"),
            "count": len(msgs),
            "messages": msgs,
            "media": srcs,
            "video_thumbs": thumbs,
            # 原始库字段：别的 wxlocal 实例可以靠它把整个会话并回去，
            # 不依赖渲染后的文本。
            "raw": [_raw_row(r) for r in rows],
        }

        zf.writestr("data.json",
                    json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))

        readme = (
            f"{display}\n"
            f"{'=' * len(display)}\n\n"
            f"导出于 {_now():%Y-%m-%d %H:%M}\n"
            f"消息 {len(msgs):,} 条 · 媒体文件 {st['media_ok']:,} 个 · 表情 {st['stickers']:,} 个\n\n"
            "怎么用\n"
            "  双击 index.html 就能看，任意浏览器打开，不需要联网、不需要微信。\n"
            "  media/ 是图片、文件、视频、缩略图，文件名是消息编号。\n"
            "  data.json 是全部消息的原始结构化数据，可用来导入其他工具。\n\n"
            "注意\n"
            "  微信本地只缓存了一部分媒体。没缓存到的图片/视频/语音在这里会显示占位标记，\n"
            "  原始数据本来就不在这台电脑上，不是导出丢的。\n"
        )
        zf.writestr("README.txt", readme.encode("utf-8"))

    st["path"] = tmp.name
    st["base"] = base
    st["bytes"] = os.path.getsize(tmp.name)
    return tmp.name, st


def build_html(username: str, display: str,
               rows: list, media_map: dict[int, tuple[str, str]],
               vthumb_map: dict[int, str], row_to_msg: Callable) -> bytes:
    """单文件 HTML（不打包媒体，媒体位置留空 —— 只适合快速看文字）。"""
    msgs = [row_to_msg(r, None, {}) for r in rows]
    return _render_page(display, msgs, {}, {}).encode("utf-8")
