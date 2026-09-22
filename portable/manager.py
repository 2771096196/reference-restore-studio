"""Local portable hub: app lifecycle, model reuse/import/download and diagnostics."""
import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web
from model_store import ModelStore, Cancelled, atomic_json

ROOT=Path(__file__).resolve().parent.parent
TOKEN=secrets.token_urlsafe(32)
NO_WINDOW=getattr(subprocess,'CREATE_NO_WINDOW',0)

class Hub:
    def __init__(self,root):
        self.root=Path(root).resolve();self.log=self.root/'logs';self.log.mkdir(exist_ok=True)
        self.config_path=self.root/'portable-config.json'
        self.config=json.loads(self.config_path.read_text(encoding='utf-8')) if self.config_path.exists() else {}
        self.manifest=json.loads((self.root/'portable/models.json').read_text(encoding='utf-8'))
        self.store=self.make_store()
        self.python=self.root/'runtime/python.exe'
        self.job={'phase':'idle','message':'准备就绪','done':0,'total':0,'errors':[]}
        self.job_lock=threading.Lock();self.cancel=threading.Event();self.seen={};self.processes={};self.handles=[]

    def make_store(self):
        path=Path(self.config.get('models_dir','models'))
        if not path.is_absolute():path=self.root/path
        return ModelStore(path,self.manifest,self.log/'model-verifications.json')

    def port(self,name):return int(self.config.get('ports',{}).get(name,8188 if name=='comfyui' else 8191))
    def url(self,name):return 'http://127.0.0.1:'+str(self.port(name))

    def owned_process(self,name):
        import psutil
        try:
            pid=json.loads((self.log/(name+'-process.json')).read_text())['pid']
            process=psutil.Process(pid)
            command=process.cmdline()
            if str(self.root/'portable/run_app.py').casefold() not in [x.casefold() for x in command] or name not in command:return None
            return process if process.is_running() else None
        except (OSError,ValueError,KeyError,psutil.Error):return None

    def service(self,name):
        process=self.owned_process(name)
        if process:
            try:
                endpoint='/system_stats' if name=='comfyui' else '/api/state'
                with urllib.request.urlopen(self.url(name)+endpoint,timeout=.7) as response:json.load(response)
                phase='ready'
            except Exception:phase='starting'
        else:
            with socket.socket() as sock:
                sock.settimeout(.15);occupied=sock.connect_ex(('127.0.0.1',self.port(name)))==0
            phase='occupied' if occupied else ('failed' if (self.log/(name+'-process.json')).exists() else 'stopped')
        message=''
        if phase=='failed':
            try:message=(self.log/(name+'.stderr.log')).read_text(encoding='utf-8',errors='replace')[-1500:]
            except OSError:message='服务已退出，请查看日志'
        return {'phase':phase,'url':self.url(name),'port':self.port(name),'message':message}

    def environment(self):
        env=os.environ.copy()
        for key in list(env):
            if key.startswith(('PYTHON','RETOUCH_')) or key in ('VIRTUAL_ENV','CONDA_PREFIX'):env.pop(key,None)
        env['PYTHONUTF8']='1';env['PYTHONIOENCODING']='utf-8'
        env['PATH']=os.pathsep.join([str(self.root/'bin'),str(self.root/'runtime'),os.path.join(os.environ.get('WINDIR','C:\\Windows'),'System32'),os.environ.get('WINDIR','C:\\Windows')])
        env['RETOUCH_DATA_DIR']=str(self.root/'retouch-data')
        env['RETOUCH_WEIGHTS_DIR']=str(self.store.root/'upscale_models')
        env['RETOUCH_CONFIG_FILE']=str(self.root/'portable/empty-config.json')
        env['HF_HOME']=str(self.root/'cache/huggingface')
        return env

    def configure(self,path):
        if any(self.owned_process(n) for n in ('comfyui','retouch')):raise ValueError('请先停止两个程序，再更换模型目录')
        candidate=Path(path).expanduser().resolve()
        if not candidate.is_dir():raise ValueError('请选择存在的 models 文件夹')
        try:stored=candidate.relative_to(self.root).as_posix()
        except ValueError:stored=str(candidate)
        self.config['models_dir']=stored;atomic_json(self.config_path,self.config)
        self.store=self.make_store();self.seen.clear()

    def launch(self,name):
        state=self.service(name)
        if state['phase'] in ('ready','starting'):return state
        if state['phase']=='occupied':raise ValueError(str(self.port(name))+' 端口已被其他程序占用，请先关闭旧实例或更换端口')
        missing=[]
        if name=='comfyui':
            missing=[m['filename'] for m in self.manifest['models'] if 'h3-starter' in m['groups'] and self.store.status(m) not in ('found','verified')]
            # JSON strings are also valid YAML quoted values, including Unicode paths.
            mapping='portable:\n  base_path: '+json.dumps(str(self.store.root),ensure_ascii=False)+'\n  is_default: true\n'
            for category in ('diffusion_models','text_encoders','vae','loras','LLM','upscale_models'):
                mapping+='  '+category+': '+category+'\n'
            paths=self.root/'ComfyUI/extra_model_paths.yaml';paths.write_text(mapping,encoding='utf-8')
            args=['--listen','127.0.0.1','--port',str(self.port(name)),'--reserve-vram','2','--disable-auto-launch']
            if self.config.get('comfy_cpu'):args+=['--cpu']
        else:args=['--port',str(self.port(name))]
        out=(self.log/(name+'.stdout.log')).open('ab');err=(self.log/(name+'.stderr.log')).open('ab');self.handles.extend([out,err])
        process=subprocess.Popen([str(self.python),'-s',str(self.root/'portable/run_app.py'),name,*args],cwd=self.root,env=self.environment(),stdout=out,stderr=err,creationflags=NO_WINDOW)
        self.processes[name]=process;atomic_json(self.log/(name+'-process.json'),{'pid':process.pid})
        return {'phase':'starting','url':self.url(name),'port':self.port(name),'missing_h3_models':missing}

    def stop(self,name):
        process=self.owned_process(name)
        if process is None:return
        if name=='retouch':
            try:
                with urllib.request.urlopen(self.url(name)+'/api/state',timeout=2) as r:state=json.load(r)
                if state.get('export',{}).get('phase')=='exporting' or state.get('status',{}).get('phase')=='analyzing':raise ValueError('正在分析或导出，请在编辑页面完成或终止后再停止服务')
            except OSError:pass
        if name=='comfyui':
            try:
                with urllib.request.urlopen(self.url(name)+'/queue',timeout=2) as r:queue=json.load(r)
                if queue.get('queue_running') or queue.get('queue_pending'):raise ValueError('ComfyUI 仍有任务，请先完成或取消任务')
            except OSError:pass
        children=process.children(recursive=True)
        process.terminate()
        for child in children:
            try:child.terminate()
            except Exception:pass
        (self.log/(name+'-process.json')).unlink(missing_ok=True)

    def progress(self,phase,done,total,name):
        self.job=dict(self.job,message=phase+'：'+name,done=done,total=total)

    def start_job(self,kind,items):
        if not self.job_lock.acquire(blocking=False):raise ValueError('已有模型任务正在进行，请等待完成或暂停')
        self.cancel.clear();self.job={'phase':'running','message':'准备处理','done':0,'total':0,'errors':[],'results':[]}
        def work():
            errors=[];results=[]
            try:
                for item in items:
                    if self.cancel.is_set():raise Cancelled('任务已暂停')
                    try:
                        if kind=='import':
                            model,message=self.store.import_file(item,self.cancel,self.progress);results.append(message+'：'+model['path'])
                        elif kind=='download':results.append(str(self.store.download(item,self.cancel,self.progress)))
                        else:results.append(str(self.store.verify(item,self.cancel,self.progress)))
                    except Cancelled:raise
                    except Exception as ex:errors.append(str(ex))
                self.job=dict(self.job,phase='error' if errors else 'done',message='处理完成' if not errors else '部分文件未完成，请查看详情',errors=errors,results=results)
            except Cancelled as ex:self.job=dict(self.job,phase='paused',message=str(ex),errors=errors,results=results)
            finally:self.job_lock.release()
        threading.Thread(target=work,daemon=True).start()

    def doctor(self):
        code='import sys,json,torch,cv2,av,aiohttp,spandrel; print(json.dumps({"python":sys.version.split()[0],"prefix":sys.prefix,"torch":torch.__version__,"cuda":torch.version.cuda,"gpu_available":torch.cuda.is_available(),"gpu":torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))'
        result=subprocess.run([str(self.python),'-s','-c',code],env=self.environment(),capture_output=True,text=True,encoding='utf-8',timeout=90,creationflags=NO_WINDOW)
        report={'ok':result.returncode==0,'details':result.stdout.strip(),'error':result.stderr[-3000:]}
        ffmpeg=subprocess.run([str(self.root/'bin/ffmpeg.exe'),'-version'],capture_output=True,text=True,timeout=15,creationflags=NO_WINDOW)
        report['ffmpeg']=ffmpeg.stdout.splitlines()[0] if ffmpeg.returncode==0 else ffmpeg.stderr
        atomic_json(self.log/'diagnostics.json',report)
        return report

    async def watch_inbox(self):
        inbox=self.root/'inbox';inbox.mkdir(exist_ok=True)
        while True:
            await asyncio.sleep(5)
            if self.job_lock.locked():continue
            ready=[]
            for p in inbox.iterdir():
                if not p.is_file() or p.suffix.lower() not in ('.safetensors','.gguf','.pth'):continue
                stat=p.stat();stamp=(stat.st_size,stat.st_mtime_ns)
                if self.seen.get(str(p))==stamp:continue
                if time.time()-stat.st_mtime<10:continue
                if not any(m['bytes']==stat.st_size for m in self.manifest['models']):continue
                self.seen[str(p)]=stamp;ready.append(str(p))
            if ready:
                try:self.start_job('import',ready)
                except ValueError:pass

def create_app(hub):
    @web.middleware
    async def local(request,handler):
        if urlsplit('//'+request.host).hostname not in ('127.0.0.1','localhost'):raise web.HTTPForbidden()
        if request.method!='GET':
            if request.headers.get('X-Portable-Token')!=TOKEN:raise web.HTTPForbidden()
            origin=request.headers.get('Origin')
            port=request.transport.get_extra_info('sockname')[1]
            if origin not in (None,'http://127.0.0.1:'+str(port),'http://localhost:'+str(port)):raise web.HTTPForbidden()
        try:return await handler(request)
        except (ValueError,KeyError,FileNotFoundError) as ex:return web.json_response({'error':str(ex)},status=400)
    app=web.Application(middlewares=[local]);routes=web.RouteTableDef()
    @routes.get('/')
    async def index(request):
        html=(hub.root/'portable/index.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN)
        return web.Response(text=html,content_type='text/html',headers={'Cache-Control':'no-store'})
    @routes.get('/api/state')
    async def state(request):
        services=await asyncio.to_thread(lambda:{n:hub.service(n) for n in ('comfyui','retouch')})
        return web.json_response({'application':'H3-Portable','root':str(hub.root),'models_dir':str(hub.store.root),'models':[dict(m,status=hub.store.status(m)) for m in hub.manifest['models']],'job':hub.job,'services':services})
    @routes.post('/api/service')
    async def service(request):
        d=await request.json();name=d['name']
        if name not in ('comfyui','retouch'):raise ValueError('未知程序')
        if d.get('action')=='stop':await asyncio.to_thread(hub.stop,name);return web.json_response({'ok':True})
        return web.json_response(await asyncio.to_thread(hub.launch,name))
    @routes.post('/api/pick')
    async def pick(request):
        d=await request.json();kind=d.get('kind','folder')
        if kind not in ('files','folder'):raise ValueError('无效选择类型')
        def dialog():
            ps=Path(os.environ.get('WINDIR','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
            r=subprocess.run([str(ps),'-NoProfile','-STA','-ExecutionPolicy','Bypass','-File',str(hub.root/'portable/pick_path.ps1'),kind],capture_output=True,encoding='utf-8',creationflags=NO_WINDOW)
            if r.returncode:raise ValueError('文件选择器失败：'+r.stderr[-500:])
            return json.loads(r.stdout.strip() or '[]')
        return web.json_response({'paths':await asyncio.to_thread(dialog)})
    @routes.post('/api/configure')
    async def configure(request):
        if hub.job_lock.locked():raise ValueError('请等待模型任务完成')
        hub.configure((await request.json())['path']);return web.json_response({'ok':True})
    @routes.post('/api/models')
    async def models(request):
        d=await request.json();kind=d['action'];items=d.get('items',[])
        if kind=='cancel':hub.cancel.set();return web.json_response({'ok':True})
        if kind=='scan':
            directory=Path(d['path']).resolve()
            if not directory.is_dir():raise ValueError('目录不存在')
            items=[str(p) for p in directory.rglob('*') if p.is_file() and p.suffix.lower() in ('.safetensors','.gguf','.pth')]
            kind='import'
        if kind not in ('import','download','verify') or not isinstance(items,list) or not items:raise ValueError('没有可处理的模型')
        if kind!='import' and any(i not in hub.store.by_id for i in items):raise ValueError('未知模型')
        hub.start_job(kind,items);return web.json_response({'ok':True})
    @routes.post('/api/open')
    async def open_folder(request):
        kind=(await request.json()).get('kind')
        target={'models':hub.store.root,'inbox':hub.root/'inbox','logs':hub.log,'outputs':hub.root/'ComfyUI/output','retouch-data':hub.root/'retouch-data'}.get(kind)
        if target is None:raise ValueError('未知目录')
        target.mkdir(parents=True,exist_ok=True);os.startfile(str(target));return web.json_response({'ok':True})
    @routes.post('/api/doctor')
    async def doctor(request):return web.json_response(await asyncio.to_thread(hub.doctor))
    async def start(app):app['watcher']=asyncio.create_task(hub.watch_inbox())
    async def end(app):app['watcher'].cancel()
    app.on_startup.append(start);app.on_cleanup.append(end);app.add_routes(routes)
    return app

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--open',action='store_true');parser.add_argument('--check',action='store_true');parser.add_argument('--port',type=int)
    args=parser.parse_args();hub=Hub(ROOT)
    if args.check:
        report=hub.doctor();print(json.dumps(report,ensure_ascii=False));return 0 if report['ok'] else 1
    instance_lock=None
    if sys.platform=='win32':
        import msvcrt
        instance_lock=(hub.log/'manager.lock').open('a+b')
        if instance_lock.tell()==0:instance_lock.write(b'0');instance_lock.flush()
        instance_lock.seek(0)
        try:msvcrt.locking(instance_lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            for attempt in range(10):
                try:
                    info=json.loads((hub.log/'manager.json').read_text())
                    with urllib.request.urlopen(info['url']+'/api/state',timeout=1) as r:state=json.load(r)
                    if state.get('application')=='H3-Portable' and state.get('root')==str(ROOT):
                        if args.open:webbrowser.open(info['url'])
                        return 0
                except Exception:pass
                time.sleep(.3)
            raise ValueError('启动器已在运行或正在初始化，请稍候再打开')
    selected=None
    for port in ([args.port] if args.port else range(8195,8216)):
        try:
            with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/api/state',timeout=.3) as r:state=json.load(r)
            if state.get('application')=='H3-Portable' and state.get('root')==str(ROOT):
                if args.open:webbrowser.open('http://127.0.0.1:'+str(port))
                return 0
        except Exception:pass
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port));selected=port;break
            except OSError:continue
    if selected is None:raise ValueError('启动器端口被占用')
    url='http://127.0.0.1:'+str(selected)
    atomic_json(hub.log/'manager.json',{'port':selected,'pid':os.getpid(),'url':url})
    if args.open:threading.Timer(1.2,lambda:webbrowser.open(url)).start()
    web.run_app(create_app(hub),host='127.0.0.1',port=selected,access_log=None)
    return 0

if __name__=='__main__':
    (ROOT/'logs').mkdir(exist_ok=True)
    log=(ROOT/'logs/manager.log').open('a',encoding='utf-8',buffering=1)
    sys.stdout=log;sys.stderr=log
    try:raise SystemExit(main())
    except Exception:
        import traceback;traceback.print_exc();raise SystemExit(1)
