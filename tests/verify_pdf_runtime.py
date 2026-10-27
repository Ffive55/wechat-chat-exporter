"""Exercise repeated PDF exports with the real application UI kept alive."""
import argparse
import json
import pathlib
import threading
import time

from export_fixture import ROOT, create_fixture
from api import Api
import webview


def main(root):
    fixture = create_fixture(root, count=3)
    api = Api(fixture['data'], ROOT / 'assets', test_export=fixture['exports'])
    window = webview.create_window('运行时导出验证', html=(ROOT / 'assets/ui.html').read_text(encoding='utf-8'),
                                   js_api=api, hidden=True, width=1320, height=880)
    api._window = window
    failures = []
    def verify():
        try:
            assert window.events.loaded.wait(30)
            deadline = time.monotonic() + 30
            while not window.evaluate_js("Boolean(api&&sessions.length&&Object.keys(dateTimeFields).length===2)"):
                assert time.monotonic() < deadline, '界面初始化超时'
                time.sleep(.1)
            result = window.evaluate_js("""(()=>{
              const date=$('start-date'),time=$('start-time');
              date.value='2026-10-26';date.dispatchEvent(new Event('change'));
              const first=$('start').value;
              time.value='09:30:15';time.dispatchEvent(new Event('change'));
              date.value='2026-10-27';date.dispatchEvent(new Event('change'));
              const second=$('start').value;
              resetRange();return [first,second,$('end-time').value];
            })()""")
            assert result == ['2026-10-26T00:00:00', '2026-10-27T09:30:15', '23:59:59'], result
            for attempt in range(2):
                assert api.choose_export(fixture['user'], online=False, format='pdf')['ok']
                deadline = time.monotonic() + 120
                while api.state()['running']:
                    assert time.monotonic() < deadline, '导出超时'
                    time.sleep(.1)
                state = api.state()
                assert not state['error'], state
                assert state['result']['messages'] == fixture['messages'], state
                assert not state['result']['missing'], state
                assert window.evaluate_js('document.title') == '微信聊天导出器'
                assert api.boot()['export_format'] == 'pdf'
            print('PASS: real WebView2 date controls, two PDF exports in one UI session, primary UI remains available')
        except Exception as exc:
            failures.append(exc)
        finally:
            window.destroy()
    webview.start(verify, gui='edgechromium', debug=False, private_mode=True,
                  storage_path=str(pathlib.Path(fixture['data']) / 'webview'))
    if failures:
        raise failures[0]


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('--root', required=True)
    main(pathlib.Path(parser.parse_args().root).resolve())
