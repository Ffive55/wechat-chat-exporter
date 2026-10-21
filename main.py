from __future__ import annotations
import argparse,pathlib,os,sys,json,threading,traceback

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',default='');p.add_argument('--self-test',action='store_true');p.add_argument('--verify-export',default='');p.add_argument('--export-path',default='');a=p.parse_args()
    base=pathlib.Path(getattr(sys,'_MEIPASS',pathlib.Path(__file__).parent));data=pathlib.Path(a.data or pathlib.Path(os.environ.get('LOCALAPPDATA',str(pathlib.Path.home())))/'WeChatLocalExporter');data.mkdir(parents=True,exist_ok=True)
    # Windowed executables may have no stdout/stderr; retain diagnostics locally.
    if sys.stdout is None:sys.stdout=(data/'application.log').open('a',encoding='utf-8',buffering=1)
    if sys.stderr is None:sys.stderr=sys.stdout
    from api import Api
    api=Api(data,base/'assets')
    if a.verify_export:
        if not a.export_path:raise ValueError('--verify-export requires --export-path')
        target=pathlib.Path(a.export_path).resolve();account=pathlib.Path(api._store.current()).resolve()
        if target.is_relative_to(account):raise ValueError('Cannot export into source account directory')
        rows,_=api._store.rows(a.verify_export,limit=None)
        if not rows:raise ValueError('Conversation has no cached messages')
        name=next(s['display'] for s in api._store.sessions() if s['username']==a.verify_export)
        result=api._renderer.export(rows,name,target,'','',True,lambda *_:None)
        (data/'frozen-export-test.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        if result['missing']:raise SystemExit(2)
        return
    if a.self_test:
        state=api.boot()
        result={'ok':True,'version':state['version'],'emoji_assets':len(api._renderer.emoji),'accounts':len(state['accounts']),'account_root':state['root'],'account_names':sum(bool(a['display_name']) for a in state['accounts']),'account_avatars':sum(bool(a['avatar']) for a in state['accounts'])}
        (data/'self-test.json').write_text(json.dumps(result),encoding='utf-8');return
    import ctypes,hashlib
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR];kernel.CreateMutexW.restype=wintypes.HANDLE;kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    mutex=kernel.CreateMutexW(None,False,'Local\\WxLocalExporter_'+hashlib.sha256(str(data.resolve()).lower().encode()).hexdigest()[:24])
    if ctypes.get_last_error()==183:
        user=ctypes.WinDLL('user32');user.FindWindowW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR];user.FindWindowW.restype=wintypes.HWND;user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int];user.SetForegroundWindow.argtypes=[wintypes.HWND]
        hwnd=user.FindWindowW(None,'微信聊天导出器')
        if hwnd:user.ShowWindow(hwnd,9);user.SetForegroundWindow(hwnd)
        kernel.CloseHandle(mutex);return
    try:
        import webview
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=True
        window=webview.create_window('微信聊天导出器',html=(base/'assets/ui.html').read_text(encoding='utf-8'),js_api=api,width=1320,height=880,min_size=(980,650),background_color='#f6f8f6')
        api._window=window
        webview.start(gui='edgechromium',debug=False,private_mode=True,storage_path=str(data/'webview'),icon=str(base/'assets/app.ico'))
    except Exception:
        message=traceback.format_exc();(data/'startup-error.log').write_text(message,encoding='utf-8')
        import ctypes
        ctypes.windll.user32.MessageBoxW(0,'程序启动失败。请确认已安装 Microsoft Edge WebView2 Runtime。\n详细日志：'+str(data/'startup-error.log'),'微信聊天导出器',16)
        raise
    finally:
        if mutex:kernel.CloseHandle(mutex)
if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()

