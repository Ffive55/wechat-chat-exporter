from __future__ import annotations
import pathlib,json,base64,re,html,hashlib,datetime,os
from html.parser import HTMLParser
from vendor import exporter
from vendor.formatters import row_to_msg
from storage import TZ
exporter._fmt_ts=lambda ts:datetime.datetime.fromtimestamp(ts,TZ).strftime('%Y-%m-%d %H:%M:%S')
exporter._now=lambda:datetime.datetime.now(TZ)

def uri(data,mime):return 'data:'+mime+';base64,'+base64.b64encode(data).decode()

class Renderer:
    def __init__(self,assets,media):
        self.media=media;root=pathlib.Path(assets);mapping=json.loads((root/'emoji_map.json').read_text(encoding='utf-8'));self.emoji={n:(root/p).read_bytes() for n,p in mapping.items()}
    def emoji_css(self,names=None):
        names=set(names) if names is not None else self.emoji.keys()
        css='.wx-inline-emoji{display:inline-block;width:22px;height:22px;vertical-align:-5px;background-position:center;background-repeat:no-repeat;background-size:contain;position:relative;margin:0 1px}.wx-emoji-text{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}'
        for name in sorted(names):
            if name in self.emoji:css+='.'+self.cls(name)+'{background-image:url("'+uri(self.emoji[name],'image/png')+'");}'
        return css
    def cls(self,name):return 'wx-e-'+hashlib.sha256(name.encode()).hexdigest()[:12]
    def inline(self,fragment):
        owner=self
        class Parser(HTMLParser):
            def __init__(self):super().__init__(convert_charrefs=False);self.out=[]
            def handle_starttag(self,t,a):self.out.append(self.get_starttag_text())
            def handle_endtag(self,t):self.out.append('</'+t+'>')
            def handle_startendtag(self,t,a):self.out.append(self.get_starttag_text())
            def handle_data(self,d):
                def swap(m):
                    n=m.group(1)
                    if n not in owner.emoji:return m.group()
                    token=html.escape(m.group(),quote=True)
                    return '<span class="wx-inline-emoji '+owner.cls(n)+'" role="img" aria-label="'+token+'" title="'+token+'"><span class="wx-emoji-text">'+token+'</span></span>'
                self.out.append(re.sub(r'\[([^\[\]\r\n]{1,20})\]',swap,d))
            def handle_entityref(self,n):self.out.append('&'+n+';')
            def handle_charref(self,n):self.out.append('&#'+n+';')
        p=Parser();p.feed(fragment);return ''.join(p.out)
    def message(self,row,result=None,preview=False,error=''):
        m=row_to_msg(row,None,{})
        if m.get('url') and not re.match(r'^https?://',m['url'],re.I):m.pop('url')
        srcs={}
        if result:
            value=uri(result['data'],result['mime'])
            if row['type']==47:m['sticker_src']=value
            else:srcs[row['id']]=value
        fragment=exporter._msg_html(m,srcs,{})
        if row.get('sender'):fragment=fragment.replace('<div class="av">','<div class="av '+self.avatar_class(row['sender'])+'" role="img" aria-label="'+html.escape(row['sender_display']+'的头像',quote=True)+'">',1)
        if row['type'] in (3,47) and not result:
            label='图片' if row['type']==3 else '表情'
            placeholder='<div class="bub"><span class="tag">['+('图片未缓存' if row['type']==3 else '表情')+']</span></div>'
            if preview:
                identity=html.escape(json.dumps([row['username'],row['server_id'],row['local_id'],row['ts']]),quote=True)
                new=f'<div class="media-wait" data-mid="{row["id"]}" data-key="{identity}">点击加载{label}</div>'
            else:new='<div class="bub"><span class="tag">['+label+'未嵌入：'+html.escape(error or '本机资源缺失')+']</span></div>'
            fragment=fragment.replace(placeholder,new)
        if row['type'] not in (1,3,47):
            raw='<details class="original-msg"><summary>原始消息内容</summary><pre>'+html.escape(row['content'])+'</pre></details>'
            fragment=fragment.replace('</div></div>',raw+'</div></div>',1)
        fragment=fragment.replace('语音未落盘','语音附件未嵌入').replace('文件未缓存','文件附件未嵌入').replace('视频未缓存','视频附件未嵌入')
        if result:fragment=re.sub(r'<a href="data:image/[^" ]+" target="_blank">','<a href="#" class="image-link">',fragment)
        return self.inline(fragment)
    def avatar_class(self,user):return 'avatar-'+hashlib.sha256(user.encode()).hexdigest()[:20]
    def avatar_map(self,users):
        result={}
        for user in set(users):
            if not user:continue
            try:
                avatar=self.media.avatar(user)
                if avatar:result[self.avatar_class(user)]=uri(avatar['data'],avatar['mime'])
            except Exception:pass
        return result
    def avatar_css(self,avatars):
        return ''.join('.row .av.'+k+'{background-image:url("'+v+'");background-size:cover;background-position:center;font-size:0!important;}' for k,v in avatars.items())
    def preview(self,rows):return ''.join('<article data-message-id="'+str(r['id'])+'">'+self.message(r,preview=True)+'</article>' for r in rows)
    def export(self,rows,title,path,start,end,online,notify):
        out=[];missing=[];originals=[];manifest=[];used=set();last=''
        for i,row in enumerate(rows):
            for n in re.findall(r'\[([^\[\]\r\n]{1,20})\]',row['content']):
                if n in self.emoji:used.add(n)
            day=datetime.datetime.fromtimestamp(row['ts'],TZ).strftime('%Y-%m-%d')
            if day!=last:out.append('<div class="day"><span>'+day+'</span></div>');last=day
            result=None;error=''
            if row['type'] in (3,47):
                try:
                    result=self.media.resolve(row,online)
                    manifest.append({'message_id':row['id'],'mime':result['mime'],'bytes':len(result['data']),'width':result['width'],'height':result['height'],'frames':result['frames'],'sha256':result['sha256']})
                    if result['original']:
                        originals.append({'message_id':row['id'],'name':'图片_'+str(row['ts'])+'.wxgf','sha256':hashlib.sha256(result['original']).hexdigest(),'base64':base64.b64encode(result['original']).decode()})
                except Exception as e:error=str(e);missing.append({'message_id':row['id'],'reason':error})
            out.append(self.message(row,result,error=error));notify(f'正在导出 {i+1}/{len(rows)} 条消息',int(100*(i+1)/max(1,len(rows))))
        esc=html.escape;warning=('；'+str(len(missing))+' 个图片/表情未能嵌入，原因已标在对应消息处') if missing else ''
        unsupported=sum(r['type'] in (34,43,49) and r.get('subtype')==6 or r['type'] in (34,43) for r in rows)
        note=f'{len(rows)} 条消息 · 北京时间 · 图片及表情按原字节内嵌'+warning
        if unsupported:note+='；语音、视频及文件消息保留记录，附件文件未嵌入'
        details='<details class="export-notes"><summary>导出范围与原始图片数据</summary><p>'+esc(('开始：'+(start or '不限')+'；结束：'+(end or '不限')+'，含起止时刻。'))+'</p><p>照片自动解密，WXGF使用本机微信原生解码器转换，原始WXGF数据同时保留。图片显示大小不改变文件分辨率；PNG、JPEG和GIF不再次编码。</p><div id="original-downloads"></div><p>内置表情来源：xxk8/wechat-emojis，图片版权归腾讯公司所有。</p></details>'
        avatars=self.avatar_map([r['sender'] for r in rows])
        css=exporter.EXPORT_CSS+self.emoji_css(used)+self.avatar_css(avatars)+'.original-msg{font-size:11px;color:#777}.original-msg pre{white-space:pre-wrap;overflow-wrap:anywhere}.export-notes{margin:12px auto;max-width:920px;padding:12px 24px;font-size:12px;color:#555}.export-notes a{margin-right:12px}'
        payload=json.dumps(originals,ensure_ascii=False).replace('</','<\\/');meta=json.dumps({'messages':len(rows),'media':manifest,'missing':missing},ensure_ascii=False).replace('</','<\\/')
        page='<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+' 聊天记录</title><style>'+css+'</style></head><body><div id="top"><h1>'+esc(title)+'</h1><div class="sub">'+esc(note)+'</div><input id="q" placeholder="查找文字或表情标签"></div>'+details+'<div id="wrap">'+''.join(out)+'</div><div id="nomsg">没有匹配消息</div><script type="application/json" id="original-data">'+payload+'</script><script type="application/json" id="export-metadata">'+meta+'</script><script>'
        page+='''document.querySelectorAll('.image-link').forEach(a=>{a.href=a.querySelector('img').src;a.download='image';});const rows=[...document.querySelectorAll('#wrap .row')];q.addEventListener('input',()=>{const v=q.value.toLowerCase();let n=0;rows.forEach(r=>{let ok=r.textContent.toLowerCase().includes(v);r.classList.toggle('hid',!ok);if(ok)n++});document.querySelectorAll('.day').forEach(d=>d.classList.toggle('hid',!!v));nomsg.style.display=n?'none':'block'});JSON.parse(document.getElementById('original-data').textContent).forEach(o=>{let a=document.createElement('a');a.textContent='下载原始数据 '+o.name;a.download=o.name;a.href='data:application/octet-stream;base64,'+o.base64;document.getElementById('original-downloads').appendChild(a)});</script></body></html>'''
        path=pathlib.Path(path)
        if path.suffix.lower()!='.html':path=path.with_suffix('.html')
        path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.tmp');temp.write_text(page,encoding='utf-8');os.replace(temp,path)
        return {'path':str(path),'messages':len(rows),'embedded':len(manifest),'missing':missing,'bytes':path.stat().st_size,'originals':len(originals),'avatars':len(avatars)}
