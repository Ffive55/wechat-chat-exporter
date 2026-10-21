from __future__ import annotations
import pathlib,threading,datetime,json,os,re,sys
from storage import Store,account_options,TZ
from media import Media
from rendering import Renderer,uri
from media import image_info

class Api:
    def __init__(self,data,assets,test_export=None):
        self._data=pathlib.Path(data);self._store=Store(data);self._media=Media(self._store,data);self._renderer=Renderer(assets,self._media);self._window=None;self._lock=threading.Lock();self._job={'running':False,'kind':'','message':'请选择账号并读取记录','progress':0,'result':None,'error':'','revision':0};self._test_export=test_export;self._settings_file=self._data/'settings.json';self._profiles_file=self._data/'account_profiles.json'
        self._settings=self._load_json(self._settings_file);self._profiles=self._load_json(self._profiles_file)
    def _notify(self,msg,pct):
        with self._lock:self._job.update(message=msg,progress=pct)
    def _run(self,kind,fn):
        with self._lock:
            if self._job['running']:return {'ok':False,'error':'已有任务正在运行，请等待完成'}
            self._job.update(running=True,kind=kind,message='准备中',progress=0,result=None,error='')
        def worker():
            try:
                result=fn()
                with self._lock:self._job.update(result=result,progress=100,message='导出完成' if kind=='export' else '读取完成')
            except Exception as e:
                with self._lock:self._job.update(error=str(e),message='任务未完成：'+str(e))
                import traceback
                with (self._data/'error.log').open('a',encoding='utf-8') as f:traceback.print_exc(file=f)
            finally:
                with self._lock:self._job['running']=False;self._job['revision']+=1
        threading.Thread(target=worker,daemon=True).start();return {'ok':True}
    def _load_json(self,path):
        try:
            value=json.loads(path.read_text(encoding='utf-8-sig'))
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError):return {}
    def _save_json(self,path,value):
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,path)
    def _root(self):
        saved=self._settings.get('account_root')
        if saved:return str(pathlib.Path(saved).resolve())
        current=self._store.current()
        if current:return str(pathlib.Path(current).parent.resolve())
        options=account_options()
        return str(pathlib.Path(options[0]['path']).parent) if options else ''
    def _accounts(self,root):
        result=[];changed=False
        for index,option in enumerate(account_options(root) if root else [],1):
            path=option['path'];cached=self._profiles.get(path,{})
            try:profile=self._store.account_profile(path)
            except (OSError,ValueError):profile={}
            name=profile.get('name') or cached.get('display_name') or ''
            avatar=cached.get('avatar','')
            if profile.get('avatar'):
                try:
                    data=profile['avatar'];info=image_info(data);avatar=uri(data,info['mime'])
                except Exception:pass
            entry={'path':path,'account_id':option['name'],'username':profile.get('username') or cached.get('username') or option['name'],'display_name':name,'avatar':avatar}
            if self._profiles.get(path)!=entry:self._profiles[path]=entry;changed=True
            result.append({**entry,'name':name or f'账号 {index}','display_index':index,'profile_pending':not name})
        if changed:self._save_json(self._profiles_file,self._profiles)
        return result
    def account_list(self):
        with self._lock:
            root=self._root();options=self._accounts(root);valid={o['path'] for o in options};current=self._store.current();selected=self._settings.get('selected_account')
            if selected not in valid:selected=current if current in valid else (options[0]['path'] if options else '')
            return {'root':root,'accounts':options,'selected':selected,'current':current}
    def set_account_root(self,root):
        with self._lock:
            if self._job['running']:return {'ok':False,'error':'请等待当前任务完成后再切换目录'}
            path=pathlib.Path(root).expanduser().resolve()
            if (path/'db_storage').is_dir():return {'ok':False,'error':'这里是具体账号目录，请选择它的上一级 xwechat_files 目录'}
            if not path.is_dir():return {'ok':False,'error':'所选目录不存在'}
            options=self._accounts(str(path))
            if not options:return {'ok':False,'error':'该目录下没有微信账号，请选择 xwechat_files 这一层目录'}
            valid={o['path'] for o in options};selected=self._settings.get('selected_account');current=self._store.current()
            if selected not in valid:selected=current if current in valid else options[0]['path']
            self._settings.update(account_root=str(path),selected_account=selected);self._save_json(self._settings_file,self._settings)
            return {'root':str(path),'accounts':options,'selected':selected,'current':current}
    def select_account(self,path):
        with self._lock:
            if self._job['running']:return {'ok':False,'error':'请等待当前任务完成后再切换账号'}
            path=str(pathlib.Path(path).resolve());valid={o['path'] for o in account_options(self._root())}
            if path not in valid:return {'ok':False,'error':'此账号不在当前根目录中，请重新选择目录'}
            self._settings['selected_account']=path;self._save_json(self._settings_file,self._settings)
            return {'ok':True,'selected':path,'current':self._store.current()}
    def boot(self):
        state=self.account_list()
        return {**state,'has_data':bool(state['current']),'emoji_css':self._renderer.emoji_css(),'data_directory':str(self._data),'version':'1.0.2'}
    def state(self):
        with self._lock:return dict(self._job)
    def sessions(self,query=''):
        rows=self._store.sessions(str(query))
        for r in rows:r['avatar_class']=self._renderer.avatar_class(r['username'])
        return rows
    def avatars(self,users):
        if not isinstance(users,list) or len(users)>200:return {}
        return self._renderer.avatar_map([u for u in users if isinstance(u,str)])
    def messages(self,user,start='',end='',offset=0,latest=False):
        try:
            rows,total=self._store.rows(str(user),str(start),str(end),offset=int(offset))
            if latest:
                offset=max(0,total-200);rows,total=self._store.rows(str(user),str(start),str(end),offset=offset)
            return {'ok':True,'html':self._renderer.preview(rows),'total':total,'offset':offset,'loaded':len(rows),'avatars':self._renderer.avatar_map([r['sender'] for r in rows])}
        except Exception as e:return {'ok':False,'error':str(e)}
    def load_media(self,mid,online=True):
        try:
            row=self._store.row(mid)
            if not row:return {'ok':False,'error':'消息不存在'}
            result=self._media.resolve(row,bool(online));return {'ok':True,'html':self._renderer.message(row,result)}
        except Exception as e:return {'ok':False,'error':str(e)}
    def start_sync(self,account):
        check=self.select_account(account)
        if not check.get('ok'):return check
        def sync():self._store.synchronize(account,self._notify);self._media.results.clear();return {'account':str(account)}
        return self._run('sync',sync)
    def choose_account(self):
        import webview
        paths=self._window.create_file_dialog(webview.FileDialog.FOLDER,directory=self._root())
        if not paths:return None
        return self.set_account_root(paths[0])
    def choose_export(self,user,start='',end='',online=True):
        try:
            rows,total=self._store.rows(user,start,end,limit=None)
            if not rows:return {'ok':False,'error':'所选时间段没有消息'}
            session=next((s for s in self._store.sessions() if s['username']==user),None)
            if not session:return {'ok':False,'error':'会话不存在'}
            name=re.sub(r'[\\/:*?"<>|]','_',session['display'])[:60]+'_聊天记录_'+datetime.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')+'.html'
            if self._test_export:path=str(pathlib.Path(self._test_export)/name)
            else:
                import webview
                selected=self._window.create_file_dialog(webview.FileDialog.SAVE,save_filename=name,file_types=('HTML (*.html)',))
                if not selected:return {'ok':True,'cancelled':True}
                path=selected if isinstance(selected,str) else selected[0]
            resolved=pathlib.Path(path).resolve();account=pathlib.Path(self._store.current()).resolve()
            if resolved.is_relative_to(account):return {'ok':False,'error':'请把导出文件保存到微信数据目录以外'}
            return self._run('export',lambda:self._renderer.export(rows,session['display'],resolved,start,end,bool(online),self._notify))
        except Exception as e:return {'ok':False,'error':str(e)}
    def open_export(self):
        result=self.state().get('result') or {};path=result.get('path')
        if path and pathlib.Path(path).is_file():os.startfile(path);return {'ok':True}
        return {'ok':False,'error':'尚无可打开的导出文件'}
    def open_data_folder(self):os.startfile(str(self._data));return {'ok':True}
