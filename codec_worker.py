"""Run the installed native WXGF decoder in an isolated helper process."""
from __future__ import annotations
import multiprocessing,threading

def _worker(pipe,dll):
    try:
        from media_crypto import WxgfDecoder
        decoder=WxgfDecoder(dll)
        while True:
            raw=pipe.recv()
            if raw is None:break
            try:pipe.send((True,decoder.convert(raw)))
            except Exception as e:pipe.send((False,str(e)))
    except (EOFError,BrokenPipeError):pass
    finally:pipe.close()

class IsolatedCodec:
    def __init__(self,dll):self.dll=str(dll);self.process=None;self.pipe=None;self.lock=threading.Lock()
    def _stop(self):
        if self.pipe:
            try:self.pipe.close()
            except OSError:pass
        if self.process:
            if self.process.is_alive():self.process.terminate()
            self.process.join(timeout=3)
        self.process=None;self.pipe=None
    def _start(self):
        ctx=multiprocessing.get_context('spawn');self.pipe,child=ctx.Pipe();self.process=ctx.Process(target=_worker,args=(child,self.dll),daemon=True);self.process.start();child.close()
    def convert(self,raw):
        with self.lock:
            error='未知解码错误'
            for attempt in range(2):
                try:
                    if not self.process or not self.process.is_alive():self._stop();self._start()
                    self.pipe.send(raw)
                    if not self.pipe.poll(40):raise TimeoutError('微信图片解码超时')
                    ok,value=self.pipe.recv()
                    if ok:return value
                    raise RuntimeError(value)
                except (RuntimeError,TimeoutError,EOFError,BrokenPipeError,OSError) as e:error=str(e);self._stop()
            raise RuntimeError('微信原生图片解码失败：'+error)
