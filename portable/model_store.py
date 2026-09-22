"""Verified, resumable model downloads and non-destructive local imports."""
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.request
from pathlib import Path

CHUNK=4*1024*1024

class Cancelled(Exception): pass

def atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)

class ModelStore:
    def __init__(self,root,manifest,cache_file):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.models=manifest['models'];self.by_id={m['id']:m for m in self.models}
        self.cache_file=Path(cache_file);self.lock=threading.RLock()
        try:self.cache=json.loads(self.cache_file.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.cache={}

    def target(self,model):
        path=(self.root/model['path']).resolve()
        if self.root not in path.parents: raise ValueError('模型目标目录越界')
        return path

    def _remember(self,path,digest):
        stat=path.stat()
        with self.lock:
            self.cache[str(path)]={'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns,'sha256':digest}
            atomic_json(self.cache_file,self.cache)

    def status(self,model):
        path=self.target(model)
        if not path.is_file():return 'missing'
        stat=path.stat()
        if stat.st_size!=model['bytes']:return 'wrong_size'
        cached=self.cache.get(str(path),{})
        if cached.get('bytes')==stat.st_size and cached.get('mtime_ns')==stat.st_mtime_ns:
            return 'verified' if cached.get('sha256')==model['sha256'] else 'wrong_hash'
        return 'found'

    def digest(self,path,cancel=None,progress=None):
        h=hashlib.sha256();done=0;total=path.stat().st_size
        with path.open('rb') as source:
            while block:=source.read(CHUNK):
                if cancel and cancel.is_set():raise Cancelled('操作已取消')
                h.update(block);done+=len(block)
                if progress:progress('校验',done,total,path.name)
        return h.hexdigest()

    def verify(self,identifier,cancel=None,progress=None):
        model=self.by_id[identifier];path=self.target(model)
        if not path.is_file():raise ValueError('未找到 '+model['filename'])
        digest=self.digest(path,cancel,progress);self._remember(path,digest)
        if path.stat().st_size!=model['bytes'] or digest!=model['sha256']:
            raise ValueError('文件与清单版本不一致：'+model['filename']+'。原文件已保留，请核对下载来源。')
        return path

    def identify(self,source,cancel=None,progress=None):
        size=source.stat().st_size
        candidates=[m for m in self.models if m['bytes']==size]
        if not candidates:raise ValueError('未识别或下载未完成：'+source.name+'；请核对清单里的文件名和大小')
        digest=self.digest(source,cancel,progress)
        for model in candidates:
            if model['sha256']==digest:return model
        raise ValueError('校验值不匹配：'+source.name+'；可能是不同版本，原文件未改动')

    def import_file(self,source,cancel=None,progress=None):
        source=Path(source).resolve()
        if not source.is_file():raise ValueError('找不到权重文件：'+str(source))
        # A matching name still has to pass size and SHA-256 validation.
        normalized=re.sub(r' \(\d+\)(?=\.[^.]+$)','',source.name).casefold()
        candidates=[m for m in self.models if m['filename'].casefold()==normalized and m['bytes']==source.stat().st_size]
        model=candidates[0] if len(candidates)==1 else self.identify(source,cancel,progress)
        target=self.target(model);target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            if self.status(model)=='verified':return model,'已存在'
            self.verify(model['id'],cancel,progress)
            return model,'已校验，无需复制'
        if shutil.disk_usage(target.parent).free<source.stat().st_size+64*1024*1024:
            raise ValueError('模型目录空间不足，至少需要 '+str(round(source.stat().st_size/1024**3,2))+' GiB')
        temporary=target.with_name(target.name+'.importing')
        digest=hashlib.sha256();done=0
        try:
            with source.open('rb') as inp,temporary.open('wb') as out:
                while block:=inp.read(CHUNK):
                    if cancel and cancel.is_set():raise Cancelled('导入已取消，原文件未改动')
                    out.write(block);digest.update(block);done+=len(block)
                    if progress:progress('复制并校验',done,model['bytes'],model['filename'])
            if done!=model['bytes'] or digest.hexdigest()!=model['sha256']:
                raise ValueError('文件内容不匹配：'+source.name+'，没有替换目标模型')
            if target.exists():raise ValueError('目标文件已出现，请刷新后重试')
            temporary.replace(target);self._remember(target,model['sha256'])
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return model,'已自动归位'

    def download(self,identifier,cancel=None,progress=None):
        model=self.by_id[identifier]
        if not model.get('urls'):raise ValueError('此版本未核实自动下载地址，请点击下载页面并导入权重')
        target=self.target(model);target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():return self.verify(identifier,cancel,progress)
        part=target.with_name(target.name+'.part')
        offset=part.stat().st_size if part.exists() else 0
        if offset>model['bytes']:raise ValueError('断点文件大于预期，请先移走 '+str(part))
        if shutil.disk_usage(target.parent).free<model['bytes']-offset+64*1024*1024:
            raise ValueError('磁盘空间不足，无法下载 '+model['filename'])
        if offset<model['bytes']:
            url=model['urls'][0]
            if not url.startswith('https://'):raise ValueError('下载地址必须为 HTTPS')
            headers={'User-Agent':'ReferenceRestorePortable/1.0'}
            if offset:headers['Range']='bytes='+str(offset)+'-'
            request=urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(request,timeout=45) as response:
                if not response.geturl().startswith('https://'):raise ValueError('下载重定向不是 HTTPS')
                resume=offset and response.status==206 and response.headers.get('Content-Range','').startswith('bytes '+str(offset)+'-')
                if not resume:offset=0
                with part.open('ab' if resume else 'wb') as out:
                    while block:=response.read(CHUNK):
                        if cancel and cancel.is_set():raise Cancelled('下载已暂停；再次点击下载可继续')
                        offset+=len(block)
                        if offset>model['bytes']:raise ValueError('下载内容大于清单大小，请检查模型版本')
                        out.write(block)
                        if progress:progress('下载',offset,model['bytes'],model['filename'])
        if offset!=model['bytes']:raise ValueError('下载未完成；再次点击下载可断点续传')
        digest=self.digest(part,cancel,progress)
        if digest!=model['sha256']:
            part.replace(part.with_name(part.name+'.invalid-'+str(time.time_ns())))
            raise ValueError('下载校验失败，文件已隔离；请重新下载或打开来源页面')
        if target.exists():raise ValueError('目标文件已出现，请刷新后校验')
        part.replace(target);self._remember(target,digest)
        return target
