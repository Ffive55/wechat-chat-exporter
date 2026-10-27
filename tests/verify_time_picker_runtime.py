"""Verify time selection with the actual application in its WebView2 runtime."""
import argparse
import pathlib
import time

from export_fixture import ROOT, create_fixture
from api import Api
import webview


def verify(root):
    fixture = create_fixture(root, count=3)
    api = Api(fixture['data'], ROOT / 'assets')
    window = webview.create_window('时间选择器验证', html=(ROOT / 'assets/ui.html').read_text(encoding='utf-8'),
                                   js_api=api, hidden=True, width=1320, height=880)
    api._window = window
    failures = []
    def run():
        try:
            assert window.events.loaded.wait(30)
            deadline = time.monotonic() + 30
            while not window.evaluate_js("Boolean(api&&Object.keys(dateTimeFields).length===2)"):
                assert time.monotonic() < deadline, '界面初始化超时'
                time.sleep(.1)
            result = window.evaluate_js("""(()=>{
              const date=$('start-date');date.value='2026-10-26';date.dispatchEvent(new Event('change'));
              const start=$('start-time'),end=$('end-time');
              const native=[start,end].every(el=>el.type==='time'&&el.step==='1'&&typeof el.showPicker==='function'&&getComputedStyle(el,'::-webkit-calendar-picker-indicator').display!=='none');
              start.value='09:30:15';start.dispatchEvent(new Event('input'));start.dispatchEvent(new Event('change'));
              const applied=$('start').value;
              date.value='2026-10-27';date.dispatchEvent(new Event('change'));const changed=$('start').value;
              const independent=end.value==='23:59:59';
              end.value='18:20:45';end.dispatchEvent(new Event('change'));
              $('end-date').value='2026-10-27';$('end-date').dispatchEvent(new Event('change'));const endValue=$('end').value;
              resetRange();return {native,applied,changed,independent,endValue,reset:[start.value,end.value]};
            })()""")
            assert result == {'native': True, 'applied': '2026-10-26T09:30:15',
                              'changed': '2026-10-27T09:30:15', 'independent': True,
                              'endValue': '2026-10-27T18:20:45', 'reset': ['00:00:00', '23:59:59']}, result
            print('PASS: actual WebView2 native time controls, visible clock icons, one-second precision, independent start/end, date changes and reset')
        except Exception as error:
            failures.append(error)
        finally:
            window.destroy()
    webview.start(run, gui='edgechromium', debug=False, private_mode=True,
                  storage_path=str(pathlib.Path(fixture['data']) / 'webview'))
    if failures:
        raise failures[0]


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('--root', required=True)
    verify(pathlib.Path(parser.parse_args().root).resolve())
