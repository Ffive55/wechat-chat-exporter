# Development-only adapter. Never included in the packaged executable.
import argparse,pathlib,json,secrets
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from api import Api

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--exports',required=True);p.add_argument('--port',type=int,default=18771);a=p.parse_args()
    api=Api(a.data,pathlib.Path(__file__).parent/'assets',test_export=a.exports);token=secrets.token_urlsafe(24);prefix='/'+token
    methods={'boot','state','sessions','messages','load_media','start_sync','choose_export','open_export','open_data_folder','avatars','account_list','set_account_root','select_account','choose_account'}
    bridge="<script>window.__TEST_BRIDGE__=true;window.pywebview={api:new Proxy({}, {get:(_,name)=>(...args)=>fetch('"+prefix+"/rpc/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)}).then(r=>r.json())})};</script>"
    page=(pathlib.Path(__file__).parent/'assets/ui.html').read_text(encoding='utf-8').replace('<script>',bridge+'<script>',1).encode()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!=prefix:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type','text/html;charset=utf-8');self.end_headers();self.wfile.write(page)
        def do_POST(self):
            method=self.path.removeprefix(prefix+'/rpc/')
            if not self.path.startswith(prefix+'/rpc/') or method not in methods:self.send_error(404);return
            try:args=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))));result=getattr(api,method)(*args)
            except Exception as e:result={'ok':False,'error':str(e)}
            data=json.dumps(result,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler);url=f'http://127.0.0.1:{a.port}'+prefix;pathlib.Path(a.data).mkdir(parents=True,exist_ok=True);pathlib.Path(a.data,'test-url.txt').write_text(url);print(url,flush=True);server.serve_forever()
if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
