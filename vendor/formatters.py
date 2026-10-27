# Message formatters adapted from wxlocal (AGPL-3.0).
from __future__ import annotations
import re,sqlite3
from typing import Any,Optional

KIND_BY_TYPE = {
    1: "text", 3: "image", 34: "voice", 35: "mail", 37: "friend", 42: "card",
    43: "video", 47: "sticker", 48: "location", 49: "app", 50: "voip",
    51: "voip", 62: "voip", 65: "friend", 10000: "system", 10002: "revoke",
}

APP_KIND = {
    3: "music", 4: "link", 5: "link", 6: "file", 8: "sticker_gif",
    19: "chat_record", 33: "miniprogram", 36: "miniprogram", 51: "quote",
    57: "quote", 62: "channel", 63: "channel_live", 87: "channel",
    2000: "transfer", 2001: "redpacket", 124: "gift",
}

XML_TAG = re.compile(r"<(\w+)>(.*?)</\1>", re.S)

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)

DES_RE = re.compile(r"<des>(.*?)</des>", re.S)

URL_RE = re.compile(r'<url>(.*?)</url>', re.S)

THUMB_RE = re.compile(r'<thumburl>(.*?)</thumburl>', re.S)

FILENAME_RE = re.compile(r'<title>(.*?)</title>', re.S)

def unxml(s: str) -> str:
    s = s.strip()
    if s.startswith("<![CDATA[") and s.endswith("]]>"):
        return s[9:-3].strip()
    s = re.sub(r"^<!\[CDATA\[", "", s)
    s = re.sub(r"\]\]>$", "", s)
    return (s.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&apos;", "'")
             .replace("&amp;", "&"))

KIND_ZH = {
    "text": "文本", "image": "图片", "voice": "语音", "video": "视频",
    "sticker": "表情", "link": "链接", "music": "音乐", "file": "文件",
    "chat_record": "聊天记录", "miniprogram": "小程序", "quote": "引用",
    "channel": "视频号", "transfer": "转账", "redpacket": "红包",
    "friend": "名片", "location": "位置", "voip": "通话", "system": "系统",
    "app": "消息", "unknown": "消息", "revoke": "撤回", "card": "名片",
    "sticker_gif": "表情", "channel_live": "直播", "gift": "礼物",
    "mail": "邮件",
}

def parse_app(content: str, subtype: int) -> dict:
    """把 appmsg XML 压成前端好用的字段。"""
    out: dict[str, Any] = {"kind": APP_KIND.get(subtype, "app")}
    t = TITLE_RE.search(content)
    if t:
        out["title"] = unxml(t.group(1)).strip()
    d = DES_RE.search(content)
    if d:
        out["des"] = unxml(d.group(1)).strip()[:400]
    u = URL_RE.search(content)
    if u:
        out["url"] = unxml(u.group(1)).strip()
    th = THUMB_RE.search(content)
    if th:
        out["thumb"] = unxml(th.group(1)).strip()
    if not out.get("thumb"):
        cv = re.search(r"<cover>(.*?)</cover>", content, re.S)
        if cv:
            out["thumb"] = unxml(cv.group(1)).strip()
    if subtype == 57:
        m = re.search(r"<refermsg>(.*?)</refermsg>", content, re.S)
        if m:
            ref = m.group(1)

            def _f(tag: str) -> str:
                mm = re.search(rf"<{tag}>(.*?)</{tag}>", ref, re.S)
                return unxml(mm.group(1)).strip() if mm else ""

            qtype = _f("type")
            try:
                qn = int(qtype)
            except ValueError:
                qn = 0
            raw = _f("content")
            # 被引用消息的 <content> 按类型各有各的形态：
            #   1 纯文本 / 3、43、47 空 / 34 是 "wxid:长度:0" / 49 是嵌套 XML
            # 直接把 raw 塞给前端，群聊里就会看到一坨 XML。
            if qn == 1:
                text = raw
            elif qn == 3:
                text = "[图片]"
            elif qn == 43:
                text = "[视频]"
            elif qn == 47:
                text = "[表情]"
            elif qn == 34:
                text = "[语音]"
            elif qn == 42:
                text = "[名片]"
            elif qn == 48:
                text = "[位置]"
            elif qn == 49:
                tt = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", raw, re.S)
                text = (tt.group(1) if tt else "").strip() or "[链接]"
            else:
                text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
            q: dict[str, Any] = {"type": qtype, "svrid": _f("svrid"),
                                 "from": _f("displayname"), "text": text[:200]}
            if q["svrid"]:
                out["quote"] = q
    if subtype == 6:
        m = re.search(r"<totallen>(.*?)</totallen>", content, re.S)
        if m and m.group(1).strip().isdigit():
            out["size"] = int(m.group(1).strip())
        m = re.search(r"<fileext>(.*?)</fileext>", content, re.S)
        if m:
            out["ext"] = m.group(1).strip()
    if subtype == 19:
        m = re.search(r"<recorditem>(.*?)</recorditem>", content, re.S)
        if m:
            out["title"] = out.get("title") or "聊天记录"
    return out

def parse_img(content: str) -> dict:
    out: dict[str, Any] = {}
    for k in ("md5", "originsourcemd5", "cdnthumbwidth", "cdnthumbheight", "length"):
        m = re.search(rf'{k}\s*=\s*"([^"]*)"', content)
        if m:
            out[k] = m.group(1)
    return out

def _tag(content: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", content, re.S)
    return unxml(m.group(1)).strip() if m else ""

def parse_mail(content: str) -> dict:
    """type 35：QQ 邮件 / 系统邮件推送。"""
    return {
        "kind": "mail",
        "title": _tag(content, "subject") or _tag(content, "digest")[:60],
        "sender": _tag(content, "sender"),
        "des": _tag(content, "digest")[:400],
        "date": _tag(content, "date"),
    }

def parse_location(content: str) -> dict:
    """type 48：位置共享卡片。"""
    g = lambda k: (re.search(rf'{k}\s*=\s*"([^"]*)"', content) or [None, ""])[1]
    return {
        "kind": "location",
        "title": unxml(g("poiname")) or unxml(g("label")),
        "label": unxml(g("label")),
        "x": g("x"), "y": g("y"),
    }

def parse_voip(content: str) -> dict:
    """type 50/51/62：音视频通话提示。"""
    m = re.search(r"<msg>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</msg>", content, re.S)
    txt = unxml(m.group(1)).strip() if m else ""
    mt = re.search(r"<msg_type>(\d+)</msg_type>", content)
    dur = re.search(r"<(?:duration|talk_time)>(\d+)</(?:duration|talk_time)>", content)
    return {
        "kind": "voip",
        "title": txt or "通话",
        "duration": int(dur.group(1)) if dur else 0,
        "msg_type": int(mt.group(1)) if mt else 0,
    }

def parse_system(content: str) -> dict:
    """type 10000/10002：系统提示。

    大部分是纯文本；有 2000+ 条是 <sysmsg> XML，直接塞进气泡会把整段标签
    露出来。这里按 type 属性拆出给人看的一句话。
    """
    s = (content or "").strip()
    if not s.startswith("<?xml") and not s.startswith("<sysmsg") and not s.startswith("<msg"):
        return {"kind": "system", "text": s}

    t = (re.search(r'<sysmsg[^>]*\btype\s*=\s*"([^"]+)"', s) or [None, ""])[1]
    if t == "revokemsg":
        txt = _tag(s, "content") or _tag(s, "replacemsg")
        newid = _tag(s, "newmsgid")
        return {"kind": "system", "text": txt or "有一条消息被撤回",
                "revoked": True, "newmsgid": newid}
    if t == "paymsg":
        return {"kind": "system", "text": _tag(s, "content") or "转账提醒"}
    if t == "sysmsgtemplate":
        txt = (_tag(s, "plain") or _tag(s, "pc_plain")
               or re.sub(r"\$\w+\$", "", _tag(s, "template")))
        return {"kind": "system", "text": txt.strip() or "系统提示"}
    if t == "mmchatroomtopmsg":
        return {"kind": "system", "text": "群公告更新"}
    # 兜底：把所有 CDATA / 纯文本挤出来
    plain = unxml(re.sub(r"<[^>]+>", " ", re.sub(r"<[^>]+/>", " ", s)))
    plain = re.sub(r"\s+", " ", plain).strip()
    return {"kind": "system", "text": plain[:300] or "系统提示"}

def parse_card(content: str) -> dict:
    """type 42/37/65：名片 / 好友推荐。"""
    g = lambda k: (re.search(rf'{k}\s*=\s*"([^"]*)"', content) or [None, ""])[1]
    return {
        "kind": "friend",
        "title": unxml(g("nickname")) or unxml(g("fromnickname")) or unxml(_tag(content, "content")),
        "username": unxml(g("username")) or unxml(g("fromusername")),
        "avatar": unxml(g("smallheadimgurl")),
    }

def row_to_msg(r: sqlite3.Row, media: Optional[str], wxgf_map: dict,
               caption: Optional[str] = None, vthumb: Optional[str] = None,
               sticker: Optional[str] = None, refmap: Optional[dict] = None) -> dict:
    t = r["type"]
    sub = r["subtype"] or 0
    kind = KIND_BY_TYPE.get(t, "unknown")
    content = r["content"] or ""
    m: dict[str, Any] = {
        "id": r["id"],
        "ts": r["ts"],
        "sender": r["sender"],
        "sender_name": r["sender_display"] or "",
        "is_self": bool(r["is_self"]),
        "type": t,
        "subtype": sub,
        "kind": kind,
    }
    if kind == "text":
        m["text"] = content
    elif kind == "image":
        info = parse_img(content)
        m["img"] = info
        m["media"] = media
        m["kind"] = "image"
    elif kind == "sticker":
        m["kind"] = "sticker"
        mm = re.search(r'md5\s*=\s*"([0-9a-f]{32})"', content)
        if mm:
            m["md5"] = mm.group(1)
        m["media"] = media
        if sticker:
            m["sticker"] = sticker
        if caption:
            m["caption"] = caption
    elif kind == "video":
        mm = re.search(r'playlength\s*=\s*"(\d+)"', content)
        m["kind"] = "video"
        if mm:
            m["duration"] = int(mm.group(1))
        m["media"] = media
        m["thumb"] = vthumb
        mm = re.search(r'cdnthumbwidth\s*=\s*"(\d+)"', content)
        if mm:
            m["thumb_w"] = int(mm.group(1))
        mm = re.search(r'cdnthumbheight\s*=\s*"(\d+)"', content)
        if mm:
            m["thumb_h"] = int(mm.group(1))
    elif kind == "voice":
        mm = re.search(r'voicelength\s*=\s*"(\d+)"', content)
        m["kind"] = "voice"
        if mm:
            m["duration"] = round(int(mm.group(1)) / 1000, 1)
        m["media"] = media
    elif kind == "app":
        m.update(parse_app(content, sub))
        m["media"] = media
        # 引用消息：把被引用的那条在本库里找出来，前端才能点着跳过去。
        # 99.8% 的 refermsg/svrid 能在 message.server_id 里命中（同会话内）。
        q = m.get("quote")
        if q and refmap:
            sid = q.get("svrid")
            hit = refmap.get(str(sid)) if sid else None
            if hit:
                m["quote_ref"] = {"id": hit[0], "ts": hit[3],
                                  "kind": KIND_BY_TYPE.get(hit[1], "app"),
                                  "type": hit[1], "subtype": hit[2] or 0}
    elif kind == "mail":
        m.update(parse_mail(content))
        m["media"] = media
    elif kind == "location":
        m.update(parse_location(content))
        m["media"] = media
    elif kind == "voip":
        m.update(parse_voip(content))
        m["media"] = media
    elif kind in ("card", "friend"):
        m.update(parse_card(content))
        m["media"] = media
    elif kind in ("system", "revoke"):
        m.update(parse_system(content))
        m["kind"] = "system"
    else:
        m["text"] = content[:300] if content and not content.startswith("<?xml") else ""
        m["kind"] = "unknown"
        m["sub"] = sub
        if content.startswith("<?xml") or content.startswith("<msg"):
            m.update(parse_app(content, sub))
    return m
