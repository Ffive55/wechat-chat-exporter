from __future__ import annotations
import pathlib,os,hashlib,json,struct,datetime,xml.etree.ElementTree as ET,urllib.request,urllib.parse,io,threading,re
from PIL import Image
import media_crypto as crypto
from codec_worker import IsolatedCodec
from storage import TZ

class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not allowed(newurl):raise ValueError('表情资源重定向到未允许的地址')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def allowed(url):
    try:
        p=urllib.parse.urlparse(url);host=p.hostname or ''
        return p.scheme in ('http','https') and any(host==domain or host.endswith('.'+domain) for domain in ('qq.com','qpic.cn')) and not p.username and not p.password
    except ValueError:return False


def emoji_node(content):
    # Group messages can prefix the XML with a sender and a real or literal newline.
    content=re.sub(r'^\s*[^\s<>:]+:(?:\r?\n|(?:\\r)?\\n)', '',content,count=1)
    try:
        root=ET.fromstring(content)
    except ET.ParseError as e:raise ValueError('表情消息资源信息格式不正确') from e
    node=root if root.tag=='emoji' else root.find('emoji')
    if node is None:raise ValueError('表情消息缺少资源信息')
    return node

def image_info(data):
    with Image.open(io.BytesIO(data)) as im:
        im.load();mime=Image.MIME.get(im.format)
        if not mime:raise ValueError('无法识别图片格式')
        return {'mime':mime,'width':im.width,'height':im.height,'frames':getattr(im,'n_frames',1)}

class Media:
    def __init__(self,store,data):
        self.store=store;self.cache=pathlib.Path(data)/'media';self.cache.mkdir(exist_ok=True);self._crypto={};self._decoder=None;self.lock=threading.RLock();self.results={}
    def _decode(self,p,root):
        raw=p.read_bytes();original=None
        if raw[:6] in (crypto.V1,crypto.V2):
            if str(root) not in self._crypto:self._crypto[str(root)]=crypto.derive_candidates(root,pathlib.Path(os.environ.get('APPDATA',''))/'Tencent/xwechat/net/kvcomm')
            plain=None
            for key,xor in self._crypto[str(root)]:
                try:
                    v=crypto.decode_v(raw,key if raw[:6]==crypto.V2 else b'cfcd208495d565ef',xor)
                    if v.startswith(b'wxgf') or v.startswith((b'\xff\xd8\xff',b'\x89PNG',b'GIF8',b'RIFF',b'BM')):plain=v;break
                except ValueError:continue
            if plain is None:raise ValueError('图片缓存密钥校验失败')
        else:plain=raw
        if plain.startswith(b'wxgf'):
            original=plain
            if self._decoder is None:self._decoder=IsolatedCodec(crypto.find_decoder(None))
            plain=self._decoder.convert(plain)
        return plain,original
    def _image_path(self,row,root):
        node=ET.fromstring(row['content']).find('img')
        if node is None:raise ValueError('图片消息缺少资源信息')
        month=datetime.datetime.fromtimestamp(row['ts'],TZ).strftime('%Y-%m');folder=root/'msg/attach'/hashlib.md5(row['username'].encode()).hexdigest()/month/'Img'
        if not folder.is_dir():raise FileNotFoundError('本机没有缓存此会话该月份的图片')
        def size(p):
            with p.open('rb') as f:h=f.read(15)
            if len(h)==15 and h[:6] in (crypto.V1,crypto.V2):
                a,x=struct.unpack_from('<II',h,6);return p.stat().st_size-15-(16-a%16)
            return p.stat().st_size
        thumb=int(node.get('cdnthumblength') or 0);sizes={int(node.get(k) or 0) for k in ('length','hevc_mid_size')}-{0}
        files=list(folder.glob('*.dat'));bases={p.stem.removesuffix('_t').removesuffix('_h') for p in files};matches=[]
        for stem in bases:
            variants=[p for p in files if p.stem in (stem,stem+'_h',stem+'_t')]
            mains=[p for p in variants if not p.stem.endswith('_t')];thumbs=[p for p in variants if p.stem.endswith('_t')]
            goodmain=any(size(p) in sizes for p in mains) if sizes else bool(mains)
            goodthumb=any(size(p)==thumb for p in thumbs) if thumb else True
            if goodmain and goodthumb:matches.append((stem,mains,thumbs))
        if len(matches)!=1:
            # Verify a content hash when length metadata is ambiguous; never pair by time alone.
            expected={node.get('md5'),node.get('originsourcemd5')}-{None,''};exact=[]
            for p in files:
                if p.stem.endswith('_t'):continue
                try:
                    raw,original=self._decode(p,root)
                    if hashlib.md5(raw).hexdigest() in expected or original and hashlib.md5(original).hexdigest() in expected:exact.append(p)
                except Exception:continue
            if len(exact)==1:return exact[0],False
            raise ValueError('图片缓存无法唯一匹配，未猜测配图')
        stem,mains,thumbs=matches[0]
        selected=sorted(mains,key=lambda p:(p.stem.endswith('_h'),p.stat().st_size),reverse=True)
        if selected:return selected[0],False
        if thumbs:return thumbs[0],True
        raise FileNotFoundError('图片缓存缺失')
    def resolve(self,row,online=True):
        account=self.store.current()
        if not account:raise RuntimeError('请先读取微信数据')
        root=pathlib.Path(account);cachekey=(account,row['username'],row['server_id'],row['local_id'],row['ts'],online)
        with self.lock:
            if cachekey in self.results:return self.results[cachekey]
            if row['type']==3:
                p,thumbnail=self._image_path(row,root);data,original=self._decode(p,root)
                result={'data':data,'original':original,'thumbnail':thumbnail,'source':'本机图片缓存自动解密','name':f"图片_{row['ts']}"}
            elif row['type']==47:
                node=emoji_node(row['content'])
                digest=(node.get('md5') or '').lower()
                if not re.fullmatch(r'[0-9a-f]{32}',digest):raise ValueError('表情资源标识不合法')
                saved=self.cache/(digest+'.image');data=saved.read_bytes() if saved.exists() else None
                if data and hashlib.md5(data).hexdigest()!=digest:data=None
                if not data:
                    month=datetime.datetime.fromtimestamp(row['ts'],TZ).strftime('%Y-%m');p=root/'cache'/month/'Emoticon'/digest[:2]/digest
                    if p.is_file():
                        raw=p.read_bytes()
                        if hashlib.md5(raw).hexdigest()==digest:data=raw
                if not data:
                    if not online:raise FileNotFoundError('本地无可用表情缓存；可启用联网补全表情')
                    url=(node.get('cdnurl') or '').strip()
                    if not allowed(url):raise ValueError('没有可用的微信表情资源地址')
                    opener=urllib.request.build_opener(Redirect())
                    with opener.open(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=15) as response:data=response.read((32<<20)+1)
                    if len(data)>32<<20 or hashlib.md5(data).hexdigest()!=digest:raise ValueError('表情资源完整性校验失败')
                    image_info(data);saved.write_bytes(data)
                result={'data':data,'original':None,'thumbnail':False,'source':'微信表情资源，MD5校验通过','name':'表情_'+digest}
            else:raise ValueError('当前支持照片和表情包的媒体导出')
            result.update(image_info(result['data']));result['sha256']=hashlib.sha256(result['data']).hexdigest();self.results[cachekey]=result
            while len(self.results)>64:self.results.pop(next(iter(self.results)))
            return result


    def avatar(self,user):
        with self.lock:
            data=self.store.avatar(user)
            if not data:return None
            if data.startswith(b'wxgf'):
                if self._decoder is None:self._decoder=IsolatedCodec(crypto.find_decoder(None))
                data=self._decoder.convert(data)
            info=image_info(data);info['data']=data;return info
