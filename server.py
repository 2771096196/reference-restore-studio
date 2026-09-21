import argparse
import asyncio
import functools
import json
import logging
import math
import mimetypes
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np
from aiohttp import web

from engine import Project, DEFAULTS
from upscaler import model_available
from config import DATA, SAMPLE_IMAGE, SAMPLE_VIDEO, SAMPLE_PROJECT, sample_available

ROOT = Path(__file__).resolve().parent
DATA.mkdir(parents=True, exist_ok=True)
PROJECTS = {}
CURRENT = None
TASKS = set()
PREVIEW_LOCK=asyncio.Lock()
PREVIEW_LATEST={}


def launch(fn, *args):
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    TASKS.add(task)
    def done(t):
        TASKS.discard(t)
        try: t.result()
        except Exception: logging.exception('Background task failed')
    task.add_done_callback(done)


def active(ready=True):
    if CURRENT is None:
        raise web.HTTPConflict(text='请先导入素材')
    p=PROJECTS[CURRENT]
    if ready and p.status['phase']!='ready':
        raise web.HTTPConflict(text='请等待对齐完成')
    return p


@web.middleware
async def local_only(request,handler):
    if urlsplit('//'+request.host).hostname not in ('127.0.0.1','localhost'):
        raise web.HTTPForbidden(text='仅接受本机主机名')
    origin=request.headers.get('Origin')
    port=request.transport.get_extra_info('sockname')[1]
    if request.method not in ('GET','HEAD') and origin not in (None,f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
        raise web.HTTPForbidden(text='仅接受本机编辑页面的请求')
    try:
        return await handler(request)
    except (ValueError,KeyError,TypeError) as exc:
        return web.json_response({'error':str(exc)},status=400)


routes=web.RouteTableDef()


@routes.get('/')
async def index(request):
    return web.FileResponse(ROOT/'static/index.html',headers={'Cache-Control':'no-store'})


@routes.get('/api/state')
async def state(request):
    if not CURRENT:
        return web.json_response({'status':{'phase':'empty','progress':0},'settings':DEFAULTS,'sample_available':sample_available()})
    p=active(False)
    meta={k:v for k,v in (p.meta or {}).items() if k not in ('image','video','matrix')}
    return web.json_response(dict(project=CURRENT,status=p.status,meta=meta,settings=p.settings,
                                  face_region=p.face_region,revision=p.revision,undo=len(p.undo_stack),
                                  export=p.export_status,export_preferences=p.export_preferences,
                                  sample_available=sample_available(),
                                  super_resolution={'available':model_available(),'model':'realesr-general-x4v3','scale':4}))


def idle_for_import():
    if CURRENT:
        p=active(False)
        if p.status['phase']=='analyzing' or p.export_status['phase']=='exporting':
            raise web.HTTPConflict(text='正在分析或导出，请完成后再导入新素材')


@routes.post('/api/sample')
async def sample(request):
    global CURRENT
    idle_for_import()
    if not sample_available():
        raise web.HTTPNotFound(text='未配置本地示例，请导入自己的原图和视频')
    id=SAMPLE_PROJECT
    if id not in PROJECTS:
        p=Project(DATA/id); PROJECTS[id]=p
    p=PROJECTS[id]
    CURRENT=id
    (DATA/'current.txt').write_text(id)
    if p.status['phase']!='ready':
        launch(p.analyze,SAMPLE_IMAGE,SAMPLE_VIDEO)
        p.status=dict(phase='analyzing',progress=0,message='准备示例素材')
    return web.json_response({'project':id})


@routes.post('/api/import')
async def import_media(request):
    global CURRENT
    idle_for_import()
    project_id=uuid.uuid4().hex
    directory=DATA/project_id; directory.mkdir()
    reader=await request.multipart()
    found={}
    async for part in reader:
        if part.name not in ('image','video') or part.name in found:
            raise ValueError('只接受一张原图和一段视频')
        suffix='.png' if part.name=='image' else '.mp4'
        path=directory/(part.name+suffix)
        size=0
        with path.open('wb') as f:
            while chunk:=await part.read_chunk(1024*1024):
                size+=len(chunk)
                if size>1024*1024*700:
                    raise ValueError('单个素材请小于700MB')
                f.write(chunk)
        found[part.name]=path
    if set(found)!= {'image','video'}:
        raise ValueError('请同时选择原图和视频')
    p=Project(directory); PROJECTS[project_id]=p; CURRENT=project_id
    (DATA/'current.txt').write_text(project_id)
    launch(p.analyze,found['image'],found['video'])
    p.status=dict(phase='analyzing',progress=0,message='准备对齐新素材')
    return web.json_response({'project':project_id})


@routes.get('/api/frame')
async def preview(request):
    p=active()
    index=int(request.query.get('frame','0'))
    if not 0<=index<p.meta['frames']: raise ValueError('帧数超出范围')
    mode=request.query.get('mode','composite')
    if mode not in ('composite','video','reference','aligned','mask','overlay'): raise ValueError('未知视图')
    resolution=request.query.get('resolution','half')
    p.preview_size(resolution)
    client=request.query.get('client','default')[:80]
    key=(CURRENT,client)
    ticket=object()
    if len(PREVIEW_LATEST)>32: PREVIEW_LATEST.clear()
    PREVIEW_LATEST[key]=ticket
    def render():
        image=p.preview_sized(index,mode,resolution)
        ok,data=cv2.imencode('.png',image)
        if not ok: raise ValueError('预览生成失败')
        return data.tobytes()
    async with PREVIEW_LOCK:
        if PREVIEW_LATEST.get(key) is not ticket or request.transport is None or request.transport.is_closing():
            raise web.HTTPConflict(text='预览请求已被更新')
        data=await asyncio.to_thread(render)
    return web.Response(body=data,content_type='image/png',headers={'Cache-Control':'no-store'})


@routes.post('/api/settings')
async def settings(request):
    p=active()
    values=await request.json()
    limits=dict(threshold=(0,30),feather=(0,80),strength=(0,1),face_feather=(0,40),face_strength=(0,1))
    checked={}
    for key,value in values.items():
        if key in ('enabled','auto','face'):
            if type(value) is not bool: raise ValueError('开关参数格式不正确')
        elif key in limits:
            if not isinstance(value,(float,int)) or not math.isfinite(value): raise ValueError('参数必须是有限数字')
            if not limits[key][0]<=value<=limits[key][1]: raise ValueError('参数超出范围')
        else: raise ValueError('未知设置')
        checked[key]=value
    with p.lock:
        p.settings.update(checked); p.save_edits()
    return web.json_response({'revision':p.revision})


@routes.post('/api/stroke')
async def stroke(request):
    p=active(); d=await request.json()
    pts=d['points']
    if not isinstance(pts,list) or not 1<=len(pts)<=4000: raise ValueError('笔画点数不正确')
    for pt in pts:
        if len(pt)!=2 or any(not isinstance(v,(float,int)) or not math.isfinite(v) or not 0<=v<=1 for v in pt):
            raise ValueError('画笔坐标不正确')
    radius=d['radius']; index=d['frame']; mode=d['mode']; space=d['space']
    hardness=d.get('hardness',.5); opacity=d.get('opacity',1.0)
    flow_rate=d.get('flow',1.0)
    if any(not isinstance(x,(float,int)) or not math.isfinite(x) or not 0<=x<=1 for x in (hardness,opacity,flow_rate)):
        raise ValueError('画笔硬度与不透明度必须在0到1之间')
    if mode not in ('restore','protect','auto') or space not in ('reference','current'): raise ValueError('画笔模式不正确')
    if not isinstance(radius,(float,int)) or not .0001<=radius<=.25: raise ValueError('画笔尺寸不正确')
    if type(index) is not int or not 0<=index<p.meta['frames']: raise ValueError('帧数不正确')
    await asyncio.to_thread(p.stroke,pts,radius,mode,index,space,hardness,opacity,flow_rate)
    return web.json_response({'revision':p.revision})


@routes.get('/api/thumbnail')
async def thumbnail(request):
    p=active(); mode=request.query.get('mode','reference'); index=int(request.query.get('frame',0))
    if mode not in ('reference','video','mask') or not 0<=index<p.meta['frames']: raise ValueError('无效缩略图')
    def render():
        if mode=='mask':
            alpha,face,matrix=p.restoration_masks(index,p.snapshot())
            if face is not None:
                fa=cv2.warpAffine(face,matrix,(p.meta['pw'],p.meta['ph']))
                alpha=alpha*(1-fa)+fa
            image=(alpha*255).clip(0,255).astype(np.uint8)
        else:
            image=p.preview(index,mode)
        image=cv2.resize(image,(160,max(1,round(image.shape[0]*160/image.shape[1]))),interpolation=cv2.INTER_AREA)
        ok,data=cv2.imencode('.png',image)
        if not ok: raise ValueError('缩略图生成失败')
        return data.tobytes()
    return web.Response(body=await asyncio.to_thread(render),content_type='image/png',headers={'Cache-Control':'no-store'})


@routes.post('/api/face')
async def face(request):
    p=active(); region=(await request.json()).get('region')
    if region is not None:
        if len(region)!=4 or any(not isinstance(v,(float,int)) or not math.isfinite(v) or not 0<=v<=1 for v in region):
            raise ValueError('请在画面内框选')
        if region[2]-region[0]<.005 or region[3]-region[1]<.005:
            raise ValueError('选区太小，请重新框选')
    with p.lock:
        p.push_undo(); p.face_region=region; p.save_edits()
    return web.json_response({'revision':p.revision})


@routes.post('/api/undo')
async def undo(request):
    p=active()
    with p.lock:
        if p.undo_stack:
            p.restore,p.protect,p.face_region=p.undo_stack.pop(); p.save_edits()
    return web.json_response({'revision':p.revision})


@routes.post('/api/reset-mask')
async def reset_mask(request):
    p=active()
    with p.lock:
        p.push_undo(); p.restore[:]=0; p.protect[:]=0; p.save_edits()
    return web.json_response({'revision':p.revision})


@routes.post('/api/export')
async def export(request):
    p=active()
    if p.export_status['phase']=='exporting': raise web.HTTPConflict(text='已有导出任务正在进行')
    options=p.export_options(await request.json())
    with p.lock:
        p.export_preferences=options.copy(); p.save_edits()
        snapshot=p.snapshot()
    p.cancel_export.clear()
    p.export_status=dict(phase='exporting',progress=0,message='准备导出')
    launch(p.export,options,snapshot)
    return web.json_response({'started':True})


@routes.post('/api/cancel-export')
async def cancel_export(request):
    p=active()
    if p.export_status['phase']=='exporting': p.cancel_export.set()
    return web.json_response({'cancelling':p.cancel_export.is_set()})


@routes.get('/api/mask-download')
async def mask_download(request):
    p=active(); index=int(request.query.get('frame','0'))
    if not 0<=index<p.meta['frames']: raise ValueError('帧数超出范围')
    def render():
        _,_,alpha,_=p.compose(index)
        alpha=cv2.resize(alpha,(p.meta['width'],p.meta['height']),interpolation=cv2.INTER_LINEAR)
        ok,data=cv2.imencode('.png',(alpha*255).astype(np.uint8))
        if not ok: raise ValueError('蒙版编码失败')
        return data.tobytes()
    data=await asyncio.to_thread(render)
    return web.Response(body=data,content_type='image/png',headers={
        'Content-Disposition':f'attachment; filename="mask-{index:04d}.png"'})


@routes.get('/api/download/{name}')
async def download(request):
    p=active(); name=request.match_info['name']
    if Path(name).name!=name or not name.startswith('restored-') or not name.endswith('.mp4'):
        raise web.HTTPNotFound()
    path=p.path/'exports'/name
    if not path.is_file(): raise web.HTTPNotFound()
    return web.FileResponse(path,headers={'Content-Type':'video/mp4',
                           'Content-Disposition':f'attachment; filename="{name}"',
                           'X-Content-Type-Options':'nosniff'})


@routes.get('/api/export-video/{name}')
async def export_video(request):
    p=active(); name=request.match_info['name']
    if Path(name).name!=name or not name.startswith('restored-') or not name.endswith('.mp4'):
        raise web.HTTPNotFound()
    path=p.path/'exports'/name
    if not path.is_file(): raise web.HTTPNotFound()
    return web.FileResponse(path,headers={'Content-Type':'video/mp4'})


async def startup(app):
    global CURRENT
    marker=DATA/'current.txt'
    if marker.exists():
        project_id=marker.read_text().strip()
        if '/' not in project_id and '\\' not in project_id and '..' not in project_id:
            p=Project(DATA/project_id)
            if p.meta:
                PROJECTS[project_id]=p; CURRENT=project_id


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8191)
    args=parser.parse_args()
    app=web.Application(middlewares=[local_only],client_max_size=750*1024*1024)
    app.add_routes(routes)
    app.router.add_static('/static/',ROOT/'static')
    app.on_startup.append(startup)
    web.run_app(app,host='127.0.0.1',port=args.port,access_log=None)


if __name__=='__main__': main()
