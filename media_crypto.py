"""只读解密本机微信 V1/V2 图片缓存。密钥由本机元数据派生，不写入文件。
参考算法：https://github.com/kyangc/wx-cli-again/tree/main/src/attachment/image_key
依赖：Python 3.10+、cryptography、Pillow；WXGF 转 JPEG 使用本机 VoipEngine.dll。
"""
from __future__ import annotations
import argparse,ctypes,hashlib,io,json,os,pathlib,re,struct,subprocess,sys
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from PIL import Image

V1=b'\x07\x08V1\x08\x07';V2=b'\x07\x08V2\x08\x07'

def ecb_unpad(ciphertext:bytes,key:bytes)->bytes:
    if not ciphertext or len(ciphertext)%16:raise ValueError('AES 密文长度错误')
    dec=Cipher(algorithms.AES(key),modes.ECB()).decryptor()
    plain=dec.update(ciphertext)+dec.finalize();n=plain[-1]
    if not 1<=n<=16 or plain[-n:]!=bytes([n])*n:raise ValueError('密钥或 PKCS7 校验失败')
    return plain[:-n]

def derive_candidates(account:pathlib.Path,metadata:pathlib.Path):
    match=re.fullmatch(r'(.+)_([0-9a-fA-F]{4})',account.name)
    if not match:raise ValueError('账号目录应为账号名加四位后缀，例如 mc..._24e0')
    username,suffix=match.groups();codes=set()
    if not metadata.is_dir():raise FileNotFoundError('找不到本机微信 kvcomm 元数据目录')
    for p in metadata.iterdir():
        if not (p.name.endswith('.statistic') or p.name.startswith('monitordata_')):continue
        m=re.match(r'^(?:key_|monitordata_)?(\d+)_',p.name)
        if m:
            value=int(m.group(1))
            if 0<=value<2**32 and hashlib.md5(str(value).encode()).hexdigest()[:4]==suffix.lower():codes.add(value)
    if not codes:raise ValueError('元数据中没有与该账号后缀匹配的条目，不能确定图片密钥')
    return [(hashlib.md5((str(code)+user).encode()).hexdigest()[:16].encode(),code&255)
            for code in sorted(codes) for user in (username,account.name)]

def decode_v(raw:bytes,key:bytes,xor:int)->bytes:
    if len(raw)<31 or raw[:6] not in (V1,V2):raise ValueError('不是受支持的 V1/V2 缓存')
    aes_size,xor_size=struct.unpack_from('<II',raw,6);end=15+aes_size+16-aes_size%16;tail=len(raw)-xor_size
    if aes_size==0 or end>tail or tail<15:raise ValueError('缓存长度字段不合法')
    plain=ecb_unpad(raw[15:end],key)
    if len(plain)!=aes_size:raise ValueError('解密长度与缓存头不一致')
    return plain+raw[end:tail]+raw[tail:].translate(bytes(i^xor for i in range(256)))

class WxgfDecoder:
    class Config(ctypes.Structure):_fields_=[('mode',ctypes.c_int),('reserved',ctypes.c_int)]
    def __init__(self,dll:pathlib.Path):
        self.dll=ctypes.WinDLL(str(dll));self.fn=self.dll.wxam_dec_wxam2pic_5
        self.fn.argtypes=[ctypes.c_int64,ctypes.c_int,ctypes.c_int64,ctypes.POINTER(ctypes.c_int),ctypes.c_int64]
        self.fn.restype=ctypes.c_int64
    def convert(self,raw:bytes)->bytes:
        size=52*1024*1024;ib=ctypes.create_string_buffer(raw);ob=ctypes.create_string_buffer(size);n=ctypes.c_int(size);cfg=self.Config(0,0)
        rc=self.fn(ctypes.addressof(ib),len(raw),ctypes.addressof(ob),ctypes.byref(n),ctypes.addressof(cfg))
        if rc!=0 or not 0<n.value<=size:raise ValueError('微信 WXGF 解码器未能生成图片')
        return ob.raw[:n.value]

def find_decoder(explicit:str|None)->pathlib.Path:
    roots=[]
    if explicit:roots.append(pathlib.Path(explicit))
    else:
        try:
            r=subprocess.run(['pwsh','-NoProfile','-Command','Get-Process Weixin -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path -Unique'],capture_output=True,text=True,timeout=15,creationflags=0x08000000)
            roots.extend(pathlib.Path(s.strip()).parent for s in r.stdout.splitlines() if s.strip())
        except (OSError,subprocess.TimeoutExpired):pass
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Tencent\Weixin') as k:
                roots.append(pathlib.Path(winreg.QueryValueEx(k,'InstallPath')[0]))
        except (OSError,ImportError):pass
        roots.extend([pathlib.Path(r'D:\Program Files\Tencent\Weixin'),pathlib.Path(r'C:\Program Files\Tencent\Weixin')])
    choices=[]
    for root in roots:
        if root.name.lower()=='voipengine.dll' and root.is_file():choices.append(root)
        if root.is_dir():
            choices.extend(root.glob('VoipEngine.dll'));choices.extend(root.glob('*/VoipEngine.dll'))
    if not choices:raise FileNotFoundError('找不到 VoipEngine.dll；请传入 --wechat-directory')
    return max(set(choices),key=lambda p:tuple(map(int,re.findall(r'\d+',p.parent.name))) or (0,))

def image_info(raw:bytes):
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        return im.format,im.width,im.height,getattr(im,'n_frames',1)

def export(account:pathlib.Path,source:pathlib.Path,out:pathlib.Path,metadata:pathlib.Path,wechat_directory=None):
    account=account.resolve();source=source.resolve();out=out.resolve()
    if not source.is_relative_to(account):raise ValueError('缓存目录必须位于指定账号目录内')
    if out.is_relative_to(account):raise ValueError('导出位置必须位于微信账号目录之外')
    files=sorted(source.rglob('*.dat')) if source.is_dir() else [source]
    if not files:raise ValueError('未找到 .dat 缓存')
    candidates=derive_candidates(account,metadata);decoder=None;results=[];errors=[]
    for path in files:
        try:
            raw=path.read_bytes()
            if raw[:6]==V2:
                decoded=None
                for key,xor in candidates:
                    try:
                        value=decode_v(raw,key,xor)
                        if value.startswith(b'wxgf') or value.startswith((b'\xff\xd8\xff',b'\x89PNG\r\n\x1a\n',b'GIF8',b'RIFF',b'BM')):
                            decoded=value;break
                    except ValueError:continue
                if decoded is None:raise ValueError('没有候选通过完整 AES 校验及图片格式识别')
            elif raw[:6]==V1:
                decoded=decode_v(raw,b'cfcd208495d565ef',candidates[0][1])
            else:
                raise ValueError('当前工具只支持 V1/V2 .dat 格式')
            native_format='WXGF' if decoded.startswith(b'wxgf') else 'image'
            original_payload=decoded
            if native_format=='WXGF':
                if decoder is None:decoder=WxgfDecoder(find_decoder(wechat_directory))
                decoded=decoder.convert(decoded)
            fmt,w,h,frames=image_info(decoded)
            ext={'JPEG':'.jpg','PNG':'.png','GIF':'.gif','WEBP':'.webp','BMP':'.bmp'}.get(fmt)
            if ext is None:raise ValueError('不支持的图片输出格式 '+fmt)
            rel=path.relative_to(source) if source.is_dir() else pathlib.Path(path.name)
            dst=(out/rel).with_suffix(ext);dst.parent.mkdir(parents=True,exist_ok=True)
            if dst.exists() and dst.read_bytes()!=decoded:raise FileExistsError('目标文件已存在且内容不同：'+str(dst))
            if not dst.exists():dst.write_bytes(decoded)
            if native_format=='WXGF':
                original_dst=(out/rel).with_suffix('.wxgf')
                if original_dst.exists() and original_dst.read_bytes()!=original_payload:raise FileExistsError('原始缓存目标已存在且内容不同')
                if not original_dst.exists():original_dst.write_bytes(original_payload)
            result={'source':str(path),'output':str(dst),'cache_format':raw[2:4].decode(),'decoded_format':native_format,'format':fmt,'width':w,'height':h,'frames':frames,'bytes':len(decoded),'sha256':hashlib.sha256(decoded).hexdigest()}
            results.append(result);print('已导出',path.name,'→',dst.name,f'{w}×{h}',flush=True)
        except Exception as exc:
            errors.append({'source':str(path),'error':str(exc)});print('失败',path.name,str(exc),flush=True)
    out.mkdir(parents=True,exist_ok=True)
    report={'exported':len(results),'failed':len(errors),'files':results,'errors':errors,'keys_saved':False,'source_modified':False}
    (out/'自动导出报告.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

def main():
    p=argparse.ArgumentParser(description='只读、离线解密微信 V1/V2 图片缓存，支持 WXGF 自动转 JPEG')
    p.add_argument('--account-directory',required=True);p.add_argument('--source-directory',required=True);p.add_argument('--output-directory',required=True)
    p.add_argument('--metadata-directory',default=str(pathlib.Path(os.environ.get('APPDATA',''),'Tencent/xwechat/net/kvcomm')))
    p.add_argument('--wechat-directory')
    a=p.parse_args()
    try:r=export(pathlib.Path(a.account_directory),pathlib.Path(a.source_directory),pathlib.Path(a.output_directory),pathlib.Path(a.metadata_directory),a.wechat_directory)
    except Exception as e:print('导出失败：'+str(e),file=sys.stderr);return 1
    print(f"完成：{r['exported']} 个成功，{r['failed']} 个失败。")
    return 0 if r['exported'] and not r['failed'] else 1
if __name__=='__main__':raise SystemExit(main())

