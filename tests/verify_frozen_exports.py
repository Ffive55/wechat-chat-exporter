"""Verify all formats and remembered preferences by starting the real EXE."""
import argparse
import json
import pathlib
import subprocess

from export_fixture import create_fixture


def verify(exe, root):
    fixture = create_fixture(root, count=240)
    data = pathlib.Path(fixture['data'])
    output = pathlib.Path(fixture['exports'])
    def run(*args):
        subprocess.run([str(exe), '--data', str(data), *args], check=True, timeout=180)
    reports = []
    for format, include in [('html', False), ('pdf', False), ('md', False), ('md', True)]:
        name = 'frozen-' + format + ('-media' if include else '') + '.' + format
        target = output / name
        extra = ['--include-media'] if include else []
        run('--verify-export', fixture['user'], '--export-path', str(target), '--export-format', format,
            '--offline', '--start', '2026-10-26T00:00:00', '--end', '2026-10-26T23:59:59', *extra)
        report = json.loads((data / 'frozen-export-test.json').read_text(encoding='utf-8'))
        assert report.get('format') == format, report
        assert report['messages'] == fixture['messages'] - 1, report
        assert not report['missing'], report
        assert report['embedded'] == (0 if format == 'md' and not include else 4), report
        assert target.is_file() and target.stat().st_size == report['bytes'], report
        if format == 'pdf':
            assert target.read_bytes().startswith(b'%PDF-')
        else:
            text = target.read_text(encoding='utf-8')
            for token in ('开始边界', '结束边界', '长消息结束', '消息 0240'):
                assert token in text, token
            assert '下一天消息' not in text
        if include:
            assets = pathlib.Path(report['assets_path'])
            assert len(list(assets.iterdir())) == 6
            assert len(list(assets.glob('*.gif'))) == 1
            assert report['inline_emojis'] == 4 and report['emoji_files'] == 3, report
            assert len(list(assets.glob('emoji_*.png'))) == 3
            for label in ('[笑脸]', '[破涕为笑]', '[微笑]'):
                assert 'alt="' + label + '"' in text
        run('--self-test')
        state = json.loads((data / 'self-test.json').read_text(encoding='utf-8'))
        assert state['version'] == '1.1.2' and state['export_format'] == format, state
        reports.append(report)
        print(f'PASS: frozen {format} include_media={include}, {report["messages"]} messages, {report["bytes"]} bytes, remembered after new process')
    (output.parent / 'verification.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    return reports


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('--exe', required=True);parser.add_argument('--root', required=True)
    args = parser.parse_args()
    verify(pathlib.Path(args.exe).resolve(), pathlib.Path(args.root).resolve())
