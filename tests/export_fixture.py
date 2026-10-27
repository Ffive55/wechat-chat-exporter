"""Synthetic data only: source, UI and frozen-EXE export verification."""
import argparse
import datetime
import hashlib
import io
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw
from storage import SCHEMA, TZ


def create_fixture(root, count=240):
    root = pathlib.Path(root).resolve()
    data, exports = root / 'data', root / 'exports'
    account = root / 'xwechat_files' / 'wxid_fixture_1111'
    for path in (data, exports, account / 'db_storage', data / 'media'):
        path.mkdir(parents=True, exist_ok=True)
    user = 'fixture_chat'
    rows = []
    first = int(datetime.datetime(2026, 10, 26, tzinfo=TZ).timestamp())
    def append(kind, content, subtype=0, ts=None):
        i = len(rows) + 1
        rows.append(dict(id=i, username=user, local_id=i, server_id=str(i), ts=first + i if ts is None else ts,
                         sender='wxid_fixture' if i % 2 == 0 else user,
                         sender_display='我' if i % 2 == 0 else '测试好友', is_self=int(i % 2 == 0),
                         type=kind, subtype=subtype, content=content, src='', sort_seq=i))
    append(1, '开始边界：零点整', ts=first)
    append(1, '中文测试：你好，世界！[微笑]\n第二行包含 <script>、**星号**、[方括号] 和 `代码`。[笑脸][破涕为笑][笑脸]')
    for i in range(count):
        append(1, f'消息 {i + 1:04d}：讨论项目进度，确认日期筛选与完整导出。')
    append(1, '长消息开始\n' + '\n'.join(f'第 {i + 1:03d} 行：长文本跨页应完整保留，不得裁切。' for i in range(105)) + '\n长消息结束')
    folder = account / 'msg' / 'attach' / hashlib.md5(user.encode()).hexdigest() / '2026-10' / 'Img'
    folder.mkdir(parents=True, exist_ok=True)
    for i, size in enumerate(((1200, 700), (450, 2200))):
        image = Image.new('RGB', size, '#e5f2e9')
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, size[0] - 20, size[1] - 20), outline='#248459', width=8)
        draw.text((50, 60), f'IMAGE {i + 1} - FULL FRAME', fill='#165334')
        buffer = io.BytesIO();image.save(buffer, format='PNG');raw = buffer.getvalue()
        (folder / f'image{i}.dat').write_bytes(raw)
        append(3, f'<msg><img length="{len(raw)}" md5="{hashlib.md5(raw).hexdigest()}"/></msg>')
    output = io.BytesIO()
    frames = [Image.new('RGB', (96, 96), color) for color in ('#ee5555', '#5588ee')]
    frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:], duration=120, loop=0)
    raw = output.getvalue();digest = hashlib.md5(raw).hexdigest()
    (data / 'media' / (digest + '.image')).write_bytes(raw)
    append(47, f'<msg><emoji md5="{digest}"/></msg>')
    append(47, f'<msg><emoji md5="{digest}"/></msg>')
    append(34, '<msg><voicemsg voicelength="3200"/></msg>')
    append(43, '<msg><videomsg playlength="8"/></msg>')
    append(49, '<msg><appmsg><title>测试文档.pdf</title><totallen>1024</totallen><fileext>pdf</fileext></appmsg></msg>', 6)
    append(49, '<msg><appmsg><title>官方文档</title><des>链接说明</des><url>https://example.com/doc?a=1&amp;b=2</url></appmsg></msg>', 5)
    append(49, '<msg><appmsg><title>引用回复内容</title><refermsg><type>1</type><displayname>测试好友</displayname><content>被引用的原始消息</content><svrid>1</svrid></refermsg></appmsg></msg>', 57)
    append(10000, '测试好友撤回了一条消息')
    append(98765, '<未知消息>原始内容和 ``` 三个反引号</未知消息>')
    append(1, '结束边界：23点59分59秒', ts=first + 86399)
    append(1, '下一天消息', ts=first + 86400)
    database = data / 'messages.db'
    if database.exists():
        raise ValueError('测试数据目录已存在，请使用新的目录')
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA)
        fields = list(rows[0])
        connection.executemany('insert into message (' + ','.join(fields) + ') values (' + ','.join('?' for _ in fields) + ')', [[row[f] for f in fields] for row in rows])
        connection.execute('insert into meta values (?,?)', ('account', str(account)))
        connection.execute('insert into session values (?,?,?,?,?,?)', (user, '导出验收测试', '测试好友', len(rows), first, first + 86400))
        connection.execute('insert into contact values (?,?,?,?)', ('wxid_fixture', '', '测试账号', '测试账号'))
        avatar = io.BytesIO();Image.new('RGB', (40, 40), '#248459').save(avatar, format='PNG')
        for sender in (user, 'wxid_fixture'):
            connection.execute('insert into avatar values (?,?)', (sender, avatar.getvalue()))
    connection.close()
    (data / 'settings.json').write_text(json.dumps({'account_root': str(account.parent), 'selected_account': str(account)}, ensure_ascii=False), encoding='utf-8')
    return {'data': str(data), 'exports': str(exports), 'account': str(account), 'user': user, 'messages': len(rows), 'rows': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('--root', required=True);parser.add_argument('--count', type=int, default=240)
    args = parser.parse_args()
    result = create_fixture(args.root, args.count)
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, ensure_ascii=False))
