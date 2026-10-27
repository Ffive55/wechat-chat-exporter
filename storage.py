from __future__ import annotations
import os,pathlib,sqlite3,json,hashlib,struct,threading,subprocess,time,datetime,re
from contextlib import contextmanager
import zstandard
from Crypto.Cipher import AES
from vendor import keyfind,wxdecrypt
TZ=datetime.timezone(datetime.timedelta(hours=8))

@contextmanager
def ro(path):
    c=sqlite3.connect(pathlib.Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=30);c.row_factory=sqlite3.Row
    try:yield c
    finally:c.close()

def decode(value):
    if value is None:return ''
    if isinstance(value,str):return value
    if isinstance(value,bytes):
        if value.startswith(b'\x28\xb5\x2f\xfd'):value=zstandard.ZstdDecompressor().decompress(value,max_output_size=128<<20)
        return value.decode('utf-8','replace')
    return str(value)

def parse_range(start='',end=''):
    def parse(s):
        if not s:return None
        try:d=datetime.datetime.fromisoformat(s)
        except ValueError:raise ValueError('时间格式不正确')
        if d.tzinfo is None:d=d.replace(tzinfo=TZ)
        return int(d.timestamp())
    a,b=parse(start),parse(end)
    if a is not None and b is not None and a>b:raise ValueError('开始时间不能晚于结束时间')
    return a,b

def account_options(root=None):
    if root is not None:
        base=pathlib.Path(root).expanduser().resolve()
        if not base.is_dir():return []
        return [{'path':str(p.resolve()),'name':p.name} for p in sorted(base.iterdir(),key=lambda p:p.name.casefold()) if p.is_dir() and (p/'db_storage').is_dir()]
    roots=[pathlib.Path.home()/'Documents/xwechat_files']
    config=pathlib.Path(os.environ.get('APPDATA',''))/'Tencent/xwechat/config'
    for p in config.glob('*.ini'):
        try:
            value=p.read_text(encoding='utf-8-sig').strip('\x00\r\n ')
            if re.match(r'^[A-Za-z]:[\\/]',value):roots.extend([pathlib.Path(value),pathlib.Path(value)/'xwechat_files'])
        except (OSError,UnicodeError):pass
    result=[];seen=set()
    for root in roots:
        if not root.is_dir():continue
        choices=[root]+[p for p in root.iterdir() if p.is_dir()]
        for p in choices:
            if (p/'db_storage/contact/contact.db').is_file() and str(p) not in seen:
                result.append({'path':str(p),'name':p.name});seen.add(str(p))
    return result

def recover_key(account,notify):
    original_probe=keyfind._probe_db;original_candidates=keyfind.memory_candidates
    keyfind._probe_db=lambda _:str(account/'db_storage/contact/contact.db')
    def candidates(progress=None):
        strong=[];weak=[];seen=set();ptrs=set();sig=struct.pack('<QQ',32,47)
        for pid in keyfind._weixin_pids(fresh=True):
            h=keyfind._k32.OpenProcess(0x410,False,pid)
            if not h:continue
            try:
                for base,size,prot in keyfind._regions(h):
                    if prot&0x101:continue
                    for off in range(0,size,4<<20):
                        mem=keyfind._read(h,base+off,min((4<<20)+32,size-off))
                        if not mem:continue
                        at=0
                        while True:
                            at=mem.find(sig,at)
                            if at<0:break
                            if at>=16:
                                ptr=struct.unpack_from('<Q',mem,at-16)[0]
                                if 0x10000<=ptr<=0x7fffffffffff and (pid,ptr) not in ptrs:
                                    ptrs.add((pid,ptr));v=keyfind._read(h,ptr,32)
                                    if v and v not in seen and keyfind.is_potential_key(v):
                                        seen.add(v);(strong if mem[at-8:at]==b'\0'*8 else weak).append(v)
                            at+=1
            finally:keyfind._k32.CloseHandle(h)
        notify('正在校验数据库密钥',5)
        return strong+weak
    keyfind.memory_candidates=candidates
    try:r=keyfind.recover(str(account),use_cache=False)
    finally:keyfind._probe_db=original_probe;keyfind.memory_candidates=original_candidates
    if not r.get('ok') or not r.get('key'):raise RuntimeError(r.get('error') or '取钥失败，请确认对应账号已在微信中登录')
    return bytes.fromhex(r['key'])

def fingerprint(src):
    paths=[src,pathlib.Path(str(src)+'-wal')]
    return [(p.stat().st_size,p.stat().st_mtime_ns) if p.exists() else None for p in paths]

def decrypt(src,dst,key):
    for _ in range(3):
        before=fingerprint(src);raw=src.read_bytes();wp=pathlib.Path(str(src)+'-wal');wal=wp.read_bytes() if wp.exists() else b''
        if fingerprint(src)==before:break
    else:raise RuntimeError('微信正在持续写入数据，请稍后重试：'+src.name)
    if raw.startswith(b'SQLite format 3\0'):
        if wal:raise RuntimeError('暂不支持带WAL的未加密源数据库')
        plain=raw
    else:
        raw,wi=wxdecrypt.merge_wal(raw,wal);enc,mac=wxdecrypt.derive_keys(key,raw[:16]);first=b'SQLite format 3\0'+AES.new(enc,AES.MODE_CBC,raw[4016:4032]).decrypt(raw[16:4016])+b'\0'*80
        n=int.from_bytes(first[28:32],'big')
        if not (0<n<=len(raw)//4096) or any(raw[n*4096:]):n=wi['database_pages'] if wi.get('committed_frames') else n
        if not 0<n<=len(raw)//4096:raise RuntimeError('数据库页数校验失败')
        if any(raw[n*4096:]):raise RuntimeError('数据库尾部存在未处理的有效数据')
        plain=bytearray(n*4096)
        for i in range(n):
            page=raw[i*4096:(i+1)*4096]
            if not any(page):continue
            if wxdecrypt._page_mac(mac,page,i+1)!=page[4032:4096]:raise RuntimeError(f'{src.name} 第{i+1}页完整性校验失败')
            body=AES.new(enc,AES.MODE_CBC,page[4016:4032]).decrypt(page[16 if i==0 else 0:4016])
            if i==0:body=b'SQLite format 3\0'+body
            plain[i*4096:(i+1)*4096]=body+b'\0'*80
        plain[28:32]=n.to_bytes(4,'big')
    dst.parent.mkdir(parents=True,exist_ok=True);temp=dst.with_suffix('.tmp');temp.write_bytes(plain)
    with ro(temp) as c:
        check=c.execute('pragma quick_check').fetchone()[0]
        if check!='ok':raise RuntimeError(src.name+' 解密副本完整性检查失败：'+check[:160])
    os.replace(temp,dst);return before

SCHEMA='''PRAGMA journal_mode=DELETE;
CREATE TABLE contact(username TEXT PRIMARY KEY,remark TEXT,nick TEXT,display TEXT);
CREATE TABLE avatar(username TEXT PRIMARY KEY,data BLOB);
CREATE TABLE message(id INTEGER PRIMARY KEY,username TEXT,local_id INTEGER,server_id TEXT,ts INTEGER,sender TEXT,sender_display TEXT,is_self INTEGER,type INTEGER,subtype INTEGER,content TEXT,src TEXT,sort_seq INTEGER);
CREATE UNIQUE INDEX dedup_sid ON message(username,server_id) WHERE server_id!='0';
CREATE UNIQUE INDEX dedup_local ON message(username,local_id,ts) WHERE server_id='0';
CREATE INDEX timeline ON message(username,ts,id);
CREATE TABLE session(username TEXT PRIMARY KEY,display TEXT,nick TEXT,count INTEGER,first_ts INTEGER,last_ts INTEGER);
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
'''

class Store:
    def __init__(self,data):
        self.data=pathlib.Path(data);self.data.mkdir(parents=True,exist_ok=True);self.db=self.data/'messages.db';self.lock=threading.RLock()
    def current(self):
        if not self.db.exists():return None
        with self.lock,ro(self.db) as c:
            row=c.execute("select value from meta where key='account'").fetchone();return row[0] if row else None
    def synchronize(self,account,notify):
        account=pathlib.Path(account).resolve()
        if not (account/'db_storage/contact/contact.db').is_file():raise ValueError('请选择包含 db_storage 的微信账号目录')
        cache=self.data/'decrypted'/hashlib.sha256(str(account).encode()).hexdigest()[:16]
        message_files=sorted((account/'db_storage/message').glob('message_[0-9]*.db'));head=account/'db_storage/head_image/head_image.db'
        files=[account/'db_storage/contact/contact.db']+([head] if head.is_file() else [])+message_files
        fpfile=cache/'fingerprints.json';old=json.loads(fpfile.read_text()) if fpfile.exists() else {};changed=[p for p in files if old.get(str(p))!=[list(v) if v else None for v in fingerprint(p)] or not (cache/p.relative_to(account/'db_storage')).exists()]
        if not changed and self.current()==str(account):notify('本地记录已是最新',100);return
        key=None
        if changed:notify('正在读取登录微信的数据库密钥',2);key=recover_key(account,notify)
        new={}
        for i,src in enumerate(files):
            dst=cache/src.relative_to(account/'db_storage')
            if src in changed:new[str(src)]=decrypt(src,dst,key)
            else:new[str(src)]=fingerprint(src)
            notify(f'校验数据库 {i+1}/{len(files)}',10+int(45*(i+1)/len(files)))
        tmp=self.data/'messages.build.db'
        if tmp.exists():tmp.unlink()
        c=sqlite3.connect(tmp);c.executescript(SCHEMA)
        with ro(cache/'contact/contact.db') as contacts:
            entries=[(u,rm or '',nk or '',rm or nk or u) for u,rm,nk in contacts.execute('select username,remark,nick_name from contact')]
        if head.is_file():
            with ro(cache/'head_image/head_image.db') as avatars:
                c.executemany('insert or replace into avatar values(?,?)',[(u,bytes(b)) for u,b in avatars.execute('select username,image_buffer from head_image') if u and b])
        c.executemany('insert or replace into contact values(?,?,?,?)',entries);names={u:d for u,rm,nk,d in entries};nicks={u:nk for u,rm,nk,d in entries};self_id=re.sub(r'_[0-9a-fA-F]{4}$','',account.name);hashmap={hashlib.md5(u.encode()).hexdigest():u for u in names}
        for i,src in enumerate(message_files):
            with ro(cache/'message'/src.name) as shard:
                idmap={r[0]:r[1] for r in shard.execute('select rowid,user_name from Name2Id')}
                hashmap.update({hashlib.md5(u.encode()).hexdigest():u for u in idmap.values() if u})
                tables=[r[0] for r in shard.execute("select name from sqlite_master where type='table' and name like 'Msg_%'")]
                for t in tables:
                    if not re.fullmatch(r'Msg_[a-fA-F0-9]{32}',t) or t[4:] not in hashmap:continue
                    user=hashmap[t[4:]];buf=[]
                    for lid,sid,kind,sq,sender_id,ts,mc,sr in shard.execute(f'select local_id,server_id,local_type,sort_seq,real_sender_id,create_time,message_content,source from "{t}"'):
                        sender=idmap.get(sender_id,'');kind=kind or 0
                        buf.append((user,lid or 0,str(sid or 0),ts or 0,sender,names.get(sender,sender),int(sender==self_id),kind&0xffffffff,kind>>32,decode(mc),decode(sr),sq or 0))
                    c.executemany('insert or ignore into message(username,local_id,server_id,ts,sender,sender_display,is_self,type,subtype,content,src,sort_seq) values(?,?,?,?,?,?,?,?,?,?,?,?)',buf)
            c.commit();notify(f'整理消息 {i+1}/{len(message_files)}',55+int(40*(i+1)/max(1,len(message_files))))
        stats=c.execute('select username,count(*),min(ts),max(ts) from message group by username').fetchall()
        c.executemany('insert into session values(?,?,?,?,?,?)',[(u,names.get(u,u),nicks.get(u,''),n,a,b) for u,n,a,b in stats]);c.execute('insert into meta values(?,?)',('account',str(account)));c.commit();c.close()
        with self.lock:os.replace(tmp,self.db)
        fpfile.write_text(json.dumps(new),encoding='utf-8');notify(f'读取完成，共 {len(stats)} 个会话',100)
    def sessions(self,query=''):
        if not self.db.exists():return []
        with self.lock,ro(self.db) as c:
            return [dict(r) for r in c.execute('select * from session where display like ? or nick like ? or username like ? order by last_ts desc',tuple('%'+query+'%' for _ in range(3)))]
    def rows(self,user,start='',end='',offset=0,limit=200):
        a,b=parse_range(start,end);where='username=?';args=[user]
        if a is not None:where+=' and ts>=?';args.append(a)
        if b is not None:where+=' and ts<=?';args.append(b)
        if not self.db.exists():return [],0
        with self.lock,ro(self.db) as c:
            total=c.execute('select count(*) from message where '+where,args).fetchone()[0]
            sql='select * from message where '+where+' order by ts,sort_seq,id'
            if limit is not None:sql+=' limit ? offset ?';args.extend([min(500,int(limit)),max(0,int(offset))])
            return [dict(r) for r in c.execute(sql,args)],total
    def row(self,mid):
        with self.lock,ro(self.db) as c:
            if isinstance(mid,str) and mid.startswith('['):
                identity=json.loads(mid)
                if not isinstance(identity,list) or len(identity)!=4:raise ValueError('无效消息标识')
                r=c.execute('select * from message where username=? and server_id=? and local_id=? and ts=?',identity).fetchone()
            else:r=c.execute('select * from message where id=?',(int(mid),)).fetchone()
            return dict(r) if r else None



    def avatar(self,user):
        if not self.db.exists():return None
        with self.lock,ro(self.db) as c:
            if not c.execute("select 1 from sqlite_master where name='avatar'").fetchone():return None
            r=c.execute('select data from avatar where username=?',(user,)).fetchone()
            return bytes(r[0]) if r and r[0] else None

    def account_profile(self,account):
        account=pathlib.Path(account).resolve();user=re.sub(r'_[0-9a-fA-F]{4}$','',account.name);name='';avatar=None
        if self.db.exists():
            with self.lock,ro(self.db) as c:
                row=c.execute('select nick from contact where username=?',(user,)).fetchone()
                if row and row[0]:name=row[0]
                if c.execute("select 1 from sqlite_master where name='avatar'").fetchone():
                    row=c.execute('select data from avatar where username=?',(user,)).fetchone()
                    if row and row[0]:avatar=bytes(row[0])
        cache=self.data/'decrypted'/hashlib.sha256(str(account).encode()).hexdigest()[:16]
        if not name and (cache/'contact/contact.db').is_file():
            with ro(cache/'contact/contact.db') as c:
                row=c.execute('select nick_name from contact where username=?',(user,)).fetchone()
                if row and row[0]:name=row[0]
        if not avatar and (cache/'head_image/head_image.db').is_file():
            with ro(cache/'head_image/head_image.db') as c:
                row=c.execute('select image_buffer from head_image where username=? limit 1',(user,)).fetchone()
                if row and row[0]:avatar=bytes(row[0])
        return {'username':user,'name':name,'avatar':avatar}
