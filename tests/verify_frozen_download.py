"""Verify a packaged EXE downloads stickers with no application or WeChat cache.

python tests/verify_frozen_download.py --exe dist/1.0.4/微信聊天导出器.exe \
    --data LOCAL_APP_DATA --message-id MESSAGE_ID [--message-id ANOTHER_ID]
Only selected messages enter a temporary fixture. Source databases stay read-only.
"""
import argparse
import hashlib
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from contextlib import closing

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from media import emoji_node
from storage import SCHEMA, Store


def verify(exe, source, message_ids):
    if not (source / 'messages.db').is_file():
        raise ValueError('找不到源消息索引')
    store = Store(source)
    rows = [store.row(mid) for mid in message_ids]
    if any(not row or row['type'] != 47 for row in rows):
        raise ValueError('请选择有效的自定义表情消息')
    with tempfile.TemporaryDirectory(prefix='wxexport_frozen_download_') as folder:
        fixture = pathlib.Path(folder)
        account = fixture / 'empty_account'
        account.mkdir()
        with closing(sqlite3.connect(fixture / 'messages.db')) as c, c:
            c.executescript(SCHEMA)
            columns = list(rows[0])
            c.executemany('insert into message(' + ','.join(columns) + ') values('
                          + ','.join('?' for _ in columns) + ')',
                          [[row[k] for k in columns] for row in rows])
            c.execute('insert into meta values(?,?)', ('account', str(account)))
            for user in {row['username'] for row in rows}:
                selected = [row for row in rows if row['username'] == user]
                c.execute('insert into session values(?,?,?,?,?,?)',
                          (user, '表情下载验证', '', len(selected),
                           min(row['ts'] for row in selected), max(row['ts'] for row in selected)))

        def run(*args):
            return subprocess.run([str(exe), '--data', str(fixture), *args], timeout=60)

        for row in rows:
            digest = emoji_node(row['content']).get('md5').lower()
            cached = fixture / 'media' / (digest + '.image')
            if cached.exists():
                raise ValueError('请选择资源 MD5 不同的消息，以逐条验证空缓存下载')
            process = run('--verify-media', str(row['id']))
            report = json.loads((fixture / 'media-test.json').read_text(encoding='utf-8'))
            if process.returncode or not report['ok']:
                raise RuntimeError(json.dumps(report, ensure_ascii=False))
            raw = cached.read_bytes()
            assert hashlib.md5(raw).hexdigest() == digest
            assert hashlib.sha256(raw).hexdigest() == report['sha256']
            print(f"首次下载成功：消息 {row['id']}，{report['mime']}，{report['bytes']} 字节")

        # Remove all usable network URLs from the fixture to prove cache reuse.
        with closing(sqlite3.connect(fixture / 'messages.db')) as c, c:
            for row in rows:
                node = emoji_node(row['content'])
                for key in list(node.attrib):
                    if key.endswith('url'):
                        node.set(key, 'https://example.invalid/unavailable')
                c.execute('update message set content=? where id=?',
                          ('<msg>' + ET.tostring(node, encoding='unicode') + '</msg>', row['id']))
        for row in rows:
            process = run('--verify-media', str(row['id']))
            report = json.loads((fixture / 'media-test.json').read_text(encoding='utf-8'))
            assert process.returncode == 0 and report['ok'], report
        for index, user in enumerate({row['username'] for row in rows}):
            target = fixture / f'export-{index}.html'
            process = run('--verify-export', user, '--export-path', str(target))
            report = json.loads((fixture / 'frozen-export-test.json').read_text(encoding='utf-8'))
            expected = sum(row['username'] == user for row in rows)
            assert process.returncode == 0 and not report['missing'], report
            assert report['embedded'] == expected, report
        print(f'打包版验证通过：{len(rows)} 张表情首次下载、MD5 校验、断开资源地址后从缓存加载及导出。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', required=True, type=pathlib.Path)
    parser.add_argument('--data', required=True, type=pathlib.Path)
    parser.add_argument('--message-id', required=True, type=int, action='append')
    options = parser.parse_args()
    verify(options.exe.resolve(), options.data.resolve(), options.message_id)
