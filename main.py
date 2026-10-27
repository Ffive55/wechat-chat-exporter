from __future__ import annotations
import argparse,pathlib,os,sys,json,threading,traceback

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',default='');p.add_argument('--self-test',action='store_true');p.add_argument('--verify-export',default='');p.add_argument('--export-path',default='');p.add_argument('--verify-media',default='');p.add_argument('--export-format',choices=('html','pdf','md'),default='html');p.add_argument('--include-media',action='store_true');p.add_argument('--offline',action='store_true');p.add_argument('--start',default='');p.add_argument('--end',default='');a=p.parse_args()
    base=pathlib.Path(getattr(sys,'_MEIPASS',pathlib.Path(__file__).parent));data=pathlib.Path(a.data or pathlib.Path(os.environ.get('LOCALAPPDATA',str(pathlib.Path.home())))/'WeChatLocalExporter');data.mkdir(parents=True,exist_ok=True)
    # Windowed executables may have no stdout/stderr; retain diagnostics locally.
    if sys.stdout is None:sys.stdout=(data/'application.log').open('a',encoding='utf-8',buffering=1)
    if sys.stderr is None:sys.stderr=sys.stdout
    from api import Api
    api=Api(data,base/'assets')
    if a.verify_media:
        try:
            row=api._store.row(a.verify_media)
            if not row:raise ValueError('消息不存在')
            media=api._media.resolve(row,True)
            result={'ok':True,**{k:media[k] for k in ('mime','width','height','frames','sha256')},'bytes':len(media['data'])}
        except Exception as e:result={'ok':False,'error':str(e),'traceback':traceback.format_exc()}
        (data/'media-test.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        if not result['ok']:raise SystemExit(2)
        return
    if a.verify_export:
        if not a.export_path:raise ValueError('--verify-export requires --export-path')
        target=api._export_path(a.export_path,a.export_format)
        rows,_=api._store.rows(a.verify_export,a.start,a.end,limit=None)
        if not rows:raise ValueError('Conversation has no cached messages')
        name=next(s['display'] for s in api._store.sessions() if s['username']==a.verify_export)
        verification=[]
        def verify():
            try:
                result=api.export_to(rows,name,target,a.start,a.end,not a.offline,a.export_format,a.include_media)
                verification.append(2 if result['missing'] else 0)
            except Exception as error:
                result={'error':str(error),'traceback':traceback.format_exc()};verification.append(2)
            (data/'frozen-export-test.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        if a.export_format=='pdf':
            import webview
            host=webview.create_window('PDF 导出验证',html='<html><body></body></html>',hidden=True)
            def run_verification():
                try:verify()
                finally:host.destroy()
            webview.start(run_verification,gui='edgechromium',debug=False,private_mode=True,storage_path=str(data/'webview'))
        else:verify()
        if verification and verification[0]:raise SystemExit(verification[0])
        return
    if a.self_test:
        state=api.boot()
        result={'ok':True,'version':state['version'],'export_format':state['export_format'],'emoji_assets':len(api._renderer.emoji),'accounts':len(state['accounts']),'account_root':state['root'],'account_names':sum(bool(a['display_name']) for a in state['accounts']),'account_avatars':sum(bool(a['avatar']) for a in state['accounts'])}
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

