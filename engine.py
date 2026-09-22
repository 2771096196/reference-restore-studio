"""Local reference-image restoration with optional neural video upscaling."""
from pathlib import Path
import json
import threading
import subprocess
import shutil
import time
import math
from functools import lru_cache

import av
import cv2
import numpy as np
from PIL import Image, ImageOps
from upscaler import RealESRGAN, ExportCancelled, model_available, MODEL_FILES, MODEL_LABELS

cv2.setNumThreads(2)

DEFAULTS = dict(enabled=True, auto=True, threshold=1.5, feather=12, strength=1.0,
                face=True, face_feather=8, face_strength=1.0,
                alignment_mode='fixed', alignment_frame=0, paint_blend='soft')
EXPORT_DEFAULTS=dict(size_mode='video',width=1920,height=1080,upscale='lanczos',denoise=.5,device='auto',tile=192)


def read_image(path):
    with Image.open(path) as im:
        return cv2.cvtColor(np.array(ImageOps.exif_transpose(im).convert('RGB')), cv2.COLOR_RGB2BGR)


def read_grayscale(path):
    # cv2.imread on Windows can fail on non-ASCII paths; Pillow accepts Path.
    with Image.open(path) as im:
        return np.array(im.convert('L'))


@lru_cache(maxsize=1)
def cached_reference(path,modified):
    image=read_image(path)
    image.flags.writeable=False
    return image


def write_image(path, image):
    ok, data = cv2.imencode('.png', image)
    if not ok:
        raise RuntimeError('Cannot encode image')
    data.tofile(str(path))


def remap(image, flow, interpolation=cv2.INTER_LINEAR):
    h, w = flow.shape[:2]
    x, y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    return cv2.remap(image, x + flow[..., 0], y + flow[..., 1], interpolation,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def scaled_flow(flow, width, height):
    h, w = flow.shape[:2]
    f = cv2.resize(flow.astype(np.float32), (width, height), interpolation=cv2.INTER_LINEAR)
    f[..., 0] *= width / w
    f[..., 1] *= height / h
    return f


def feather_inside(mask, pixels):
    """Soften inward, so restoration never bleeds beyond painted/auto-selected regions."""
    if pixels <= 0:
        return mask.astype(np.float32)
    binary = (mask > .5).astype(np.uint8)
    distance = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    ramp = np.clip(distance / pixels, 0, 1)
    return (ramp * ramp * (3 - 2 * ramp)).astype(np.float32)


def brush_coverage(coords,shape,radius,hardness,opacity,flow,spacing=.1):
    """Soft round tip; opacity caps one stroke, flow builds per spaced dab.

    At full flow the stroke uses maximum tip coverage so overlapping dabs do
    not harden the soft edge. Lower flow accumulates with source-over blending.
    Centers and radius remain fractional, independent of pointer event density.
    """
    ph,pw=shape
    coverage=np.zeros(shape,np.float32)
    if not coords or opacity==0 or flow==0: return coverage
    r=max(.25,float(radius));step=max(.25,2*r*spacing)
    dabs=[coords[0]];until_next=step
    for a,b in zip(coords,coords[1:]):
        a=np.asarray(a,dtype=np.float64);b=np.asarray(b,dtype=np.float64)
        distance=float(np.linalg.norm(b-a))
        if distance==0: continue
        offset=until_next
        while offset<=distance+1e-9:
            dabs.append(a+(b-a)*(offset/distance))
            offset+=step
        until_next=offset-distance
    for x,y in dabs:
        x0,y0=max(0,math.floor(x-r-.5)),max(0,math.floor(y-r-.5))
        x1,y1=min(pw,math.ceil(x+r+.5)+1),min(ph,math.ceil(y+r+.5)+1)
        if x0>=x1 or y0>=y1: continue
        xx,yy=np.meshgrid(np.arange(x0,x1,dtype=np.float64)-x,np.arange(y0,y1,dtype=np.float64)-y)
        distance=np.sqrt(xx*xx+yy*yy)
        edge=np.clip(r+.5-distance,0,1)
        if hardness>=1:
            tip=edge
        else:
            t=np.clip((r-distance)/max(r*(1-hardness),.001),0,1)
            tip=t*t*(3-2*t)*edge
        region=coverage[y0:y1,x0:x1]
        if flow>=1: np.maximum(region,tip,out=region)
        else: region+=tip*flow*(1-region)
    return coverage*opacity


def align_reference(reference, first):
    gray0 = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    gray1 = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    detector = cv2.ORB_create(nfeatures=4000)
    k0, d0 = detector.detectAndCompute(gray0, None)
    k1, d1 = detector.detectAndCompute(gray1, None)
    identity = np.array([[1, 0, 0], [0, 1, 0]], np.float32)
    if d0 is None or d1 is None:
        return identity, {'method': '尺寸对齐（特征不足）', 'inliers': 0}
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d0, d1, k=2)
    good = [a for pair in pairs if len(pair) == 2 for a, b in [pair] if a.distance < .75*b.distance]
    if len(good) < 12:
        return identity, {'method': '尺寸对齐（匹配不足）', 'inliers': 0}
    src = np.float32([k0[m.queryIdx].pt for m in good])
    dst = np.float32([k1[m.trainIdx].pt for m in good])
    matrix, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                                ransacReprojThreshold=2.5)
    count = int(inliers.sum()) if inliers is not None else 0
    if matrix is None:
        return identity, {'method': '尺寸对齐（匹配失败）', 'inliers': count}
    scale = np.hypot(matrix[0, 0], matrix[1, 0])
    angle = abs(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0])))
    h, w = first.shape[:2]
    if count < 12 or not .75 < scale < 1.3 or angle > 15 or abs(matrix[0, 2]) > w*.2 or abs(matrix[1, 2]) > h*.2:
        return identity, {'method': '尺寸对齐（自动变换未通过检查）', 'inliers': count}
    return matrix.astype(np.float32), {'method': '特征匹配 + 旋转/缩放对齐', 'inliers': count,
                                     'scale': float(scale), 'angle': float(angle)}


class Project:
    def __init__(self, directory):
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=True)
        self.settings = dict(DEFAULTS)
        self.export_preferences=dict(EXPORT_DEFAULTS)
        self.cancel_export=threading.Event()
        self.face_region = None
        self.undo_stack = []
        self.lock = threading.RLock()
        self.revision = 0
        self.meta = None
        self.status = {'phase': 'empty', 'progress': 0, 'message': '等待素材'}
        self.export_status = {'phase': 'idle', 'progress': 0}
        if (self.path / 'meta.json').exists():
            self.meta = json.loads((self.path/'meta.json').read_text(encoding='utf-8'))
            for key in ('image','video'):
                recorded=Path(self.meta[key])
                if not recorded.is_absolute():
                    self.meta[key]=str((self.path/recorded).resolve())
                elif not recorded.is_file() and (self.path/recorded.name).is_file():
                    # Relocate projects written by versions that stored absolute paths.
                    self.meta[key]=str((self.path/recorded.name).resolve())
            self._open_analysis()
            self.status = {'phase': 'ready', 'progress': 100, 'message': '已恢复上次工程'}
            exports=sorted((self.path/'exports').glob('restored-*.mp4'),key=lambda p:p.stat().st_mtime)
            if exports:
                self.export_status=dict(phase='done',progress=100,message='上次导出',
                                        filename=exports[-1].name,path=str(exports[-1]))

    def _open_analysis(self):
        m = self.meta
        if 'image_width' not in m:
            with Image.open(m['image']) as im:
                m['image_width'],m['image_height']=ImageOps.exif_transpose(im).size
        self.back = np.load(self.path/'back.npy', mmap_mode='r')
        self.forward = np.load(self.path/'forward.npy', mmap_mode='r')
        self.score = np.load(self.path/'motion.npy')
        self.reference = read_image(self.path/'aligned-reference.png')
        self.ref_small = cv2.resize(self.reference, (m['pw'], m['ph']), interpolation=cv2.INTER_AREA)
        self.valid = read_grayscale(self.path/'valid.png')
        self.restore = np.zeros((m['ph'], m['pw']), np.uint8)
        self.protect = self.restore.copy()
        for name in ['restore', 'protect']:
            p = self.path/f'{name}.png'
            if p.exists():
                setattr(self, name, read_grayscale(p))
        p = self.path/'edit.json'
        if p.exists():
            edits = json.loads(p.read_text(encoding='utf-8'))
            self.settings.update(edits.get('settings', {}))
            self.face_region = edits.get('face_region')
            self.export_preferences.update(edits.get('export_preferences',{}))

    def save_edits(self):
        with self.lock:
            for key in ['restore', 'protect']:
                write_image(self.path/f'{key}.png', getattr(self, key))
            target = self.path/'edit.json'
            tmp = target.with_suffix('.tmp')
            tmp.write_text(json.dumps({'settings': self.settings, 'face_region': self.face_region,
                                      'export_preferences':self.export_preferences},
                                     ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(target)
            self.revision += 1

    def analyze(self, image_path, video_path):
        try:
            self.status = dict(phase='analyzing', progress=0, message='读取素材并匹配首帧')
            container = av.open(str(video_path))
            stream = container.streams.video[0]
            fps = stream.average_rate or stream.base_rate
            if not fps:
                raise ValueError('无法读取视频帧率')
            width, height = stream.width, stream.height
            if width % 2 or height % 2:
                raise ValueError('当前版本需要偶数视频宽高，请先裁切或缩放一像素。')
            pw = min(784, width)
            ph = max(2, round(height * pw / width))
            frames_dir = self.path/'frames'
            frames_dir.mkdir(exist_ok=True)
            frames = []
            timestamps = []
            first_full = None
            for frame in container.decode(stream):
                full = frame.to_ndarray(format='bgr24')
                if first_full is None:
                    first_full = full
                small = cv2.resize(full, (pw, ph), interpolation=cv2.INTER_AREA)
                frames.append(small)
                timestamps.append(float(frame.time) if frame.time is not None else len(timestamps)/float(fps))
                write_image(frames_dir/f'{len(frames)-1:05d}.png', small)
                if len(frames) > 2400:
                    raise ValueError('请先裁剪成不超过2400帧的短片。')
            container.close()
            count = len(frames)
            if not count:
                raise ValueError('视频中没有可解码帧')
            if len(timestamps) > 2 and np.max(np.abs(np.diff(timestamps)-1/float(fps))) > .02:
                raise ValueError('检测到可变帧率，请先转为固定帧率后导入，以免音画不同步。')
            ref = read_image(image_path)
            ref = cv2.resize(ref, (width, height), interpolation=cv2.INTER_AREA)
            ref_small = cv2.resize(ref, (pw, ph), interpolation=cv2.INTER_AREA)
            matrix, info = align_reference(ref_small, frames[0])
            full_matrix = matrix.copy()
            full_matrix[:, 2] *= width/pw
            aligned = cv2.warpAffine(ref, full_matrix, (width, height), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_REFLECT_101)
            valid = cv2.warpAffine(np.full((ph, pw), 255, np.uint8), matrix, (pw, ph))
            valid = cv2.erode(valid, np.ones((5, 5), np.uint8))
            write_image(self.path/'aligned-reference.png', aligned)
            write_image(self.path/'valid.png', valid)
            write_image(self.path/'first-frame.png', first_full)
            shape = (count, ph, pw, 2)
            back = np.lib.format.open_memmap(self.path/'back.npy', mode='w+', dtype=np.float16, shape=shape)
            forward = np.lib.format.open_memmap(self.path/'forward.npy', mode='w+', dtype=np.float16, shape=shape)
            first_gray = cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY)
            flow_engine = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
            displacements, residuals = [], []
            base_float = cv2.GaussianBlur(frames[0], (5,5), 0).astype(np.float32)
            for index, frame in enumerate(frames):
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if index == 0:
                    f = np.zeros((ph, pw, 2), np.float32)
                    b = f.copy()
                else:
                    f = flow_engine.calc(first_gray, gray, None)
                    b = flow_engine.calc(gray, first_gray, None)
                back[index] = b.astype(np.float16)
                forward[index] = f.astype(np.float16)
                # Score is in ORIGINAL VIDEO pixels, in the reference/first-frame coordinate system.
                displacements.append(np.linalg.norm(f, axis=2).astype(np.float32) * width/pw)
                registered = remap(frame, f)
                delta = np.mean(np.abs(cv2.GaussianBlur(registered, (5,5), 0).astype(np.float32)-base_float), axis=2)
                residuals.append(delta)
                self.status = dict(phase='analyzing', progress=round(5+90*(index+1)/count),
                                   message=f'双向对齐与稳定区域分析 {index+1} / {count}')
            back.flush(); forward.flush()
            motion = np.percentile(np.stack(displacements), 90, axis=0).astype(np.float32)
            appearance = np.percentile(np.stack(residuals), 90, axis=0).astype(np.float32)
            # Penalise flickering / lighting drift as well as motion; a conservative heuristic, not segmentation.
            score = np.maximum(motion, np.maximum(appearance-4, 0)*.3)
            score = cv2.dilate(score, np.ones((5,5), np.uint8))
            np.save(self.path/'motion.npy', score)
            self.meta = dict(width=width, height=height, pw=pw, ph=ph, frames=count,
                             fps_num=int(fps.numerator), fps_den=int(fps.denominator), fps=float(fps),
                             duration=count/float(fps), image=str(image_path), video=str(video_path),
                             image_name=Path(image_path).name, video_name=Path(video_path).name,
                             alignment=info, matrix=matrix.tolist(), created=time.time())
            # Completion marker is written only after every analysis asset succeeds.
            portable_meta=dict(self.meta)
            for key in ('image','video'):
                try:portable_meta[key]=Path(self.meta[key]).resolve().relative_to(self.path.resolve()).as_posix()
                except ValueError:pass
            (self.path/'meta.json').write_text(json.dumps(portable_meta, ensure_ascii=False, indent=2), encoding='utf-8')
            self._open_analysis()
            self.save_edits()
            self.status = dict(phase='ready', progress=100, message='对齐完成，可以涂抹与预览')
        except Exception as exc:
            self.status = dict(phase='error', progress=0, message=str(exc))
            raise

    def snapshot(self):
        with self.lock:
            return dict(settings=dict(self.settings), face_region=self.face_region[:] if self.face_region else None,
                        restore=self.restore.copy(), protect=self.protect.copy())

    def push_undo(self):
        self.undo_stack.append((self.restore.copy(), self.protect.copy(),
                                self.face_region[:] if self.face_region else None))
        self.undo_stack = self.undo_stack[-20:]

    @lru_cache(maxsize=8)
    def fixed_alignment(self, index):
        """One global alignment to the anchor frame, reused for the whole clip.

        Masks live in first-frame coordinates. Do not freeze a dense optical-flow
        field: it would still distort the reference's eyes at the chosen frame.
        """
        pw,ph=self.meta['pw'],self.meta['ph']
        if index==0:
            matrix=np.array([[1,0,0],[0,1,0]],np.float32)
        else:
            first=read_image(self.path/'frames'/'00000.png')
            anchor=read_image(self.path/'frames'/f'{index:05d}.png')
            matrix,info=align_reference(first,anchor)
            if info['inliers']<12 or info['method']!='特征匹配 + 旋转/缩放对齐':
                raise ValueError('此帧无法可靠固定对齐，请选择更接近首帧的画面，或固定到第1帧')
        inverse=cv2.invertAffineTransform(matrix)
        x,y=np.meshgrid(np.arange(pw,dtype=np.float32),np.arange(ph,dtype=np.float32))
        flow=np.stack((inverse[0,0]*x+inverse[0,1]*y+inverse[0,2]-x,
                       inverse[1,0]*x+inverse[1,1]*y+inverse[1,2]-y),axis=-1)
        flow.flags.writeable=False
        matrix.flags.writeable=False
        return flow,matrix

    def alignment_flow(self,index,settings):
        if settings.get('alignment_mode','tracked')=='fixed':
            return self.fixed_alignment(settings.get('alignment_frame',0))[0]
        return self.back[index].astype(np.float32)

    def fixed_paint_support(self,s,size):
        settings=s['settings']
        if (settings.get('alignment_mode','tracked')!='fixed' or settings.get('paint_blend','frozen')!='frozen' or
                not settings.get('enabled',True) or settings['strength']==0 or not s['restore'].any()):
            return None
        manual=np.minimum(s['restore'].astype(np.float32)/255,1-s['protect'].astype(np.float32)/255)
        manual*=self.valid.astype(np.float32)/255
        support=remap(manual,self.alignment_flow(settings.get('alignment_frame',0),settings))
        return cv2.resize(support,size,interpolation=cv2.INTER_LINEAR)>0

    def fixed_paint_patch(self,s,size):
        """Freeze final blended RGB, including partial-opacity/soft brush pixels.

        Freezing just the reference image is insufficient: the changing video
        underneath would still flicker through translucent paint. Cache an anchor
        composite and paste its painted support identically into every frame.
        """
        settings=s['settings']
        support=self.fixed_paint_support(s,size)
        if support is None: return None
        key=(size,tuple(sorted(settings.items())),tuple(s['face_region'] or ()),
             s['restore'].tobytes(),s['protect'].tobytes())
        with self.lock:
            cached=getattr(self,'_paint_patch',None)
            if cached is not None and cached[0]==key: return cached[1]
            anchor=settings.get('alignment_frame',0)
            source=self.original_frame(anchor)
            reference=cached_reference(self.meta['image'],Path(self.meta['image']).stat().st_mtime_ns)
            patch=self.compose_target(anchor,source,s,size,reference,_lock_paint=False)
            result=(support,patch)
            self._paint_patch=(key,result)
            return result

    def stroke(self, points, radius, mode, index, space, hardness=.5, opacity=1.0, flow_rate=1.0, spacing=.1):
        with self.lock:
            if opacity==0 or flow_rate==0: return
            self.push_undo()
            pw, ph = self.meta['pw'], self.meta['ph']
            flow = self.alignment_flow(index,self.settings)
            coords = []
            for x, y in points:
                px, py = np.clip(x*pw, 0, pw-1), np.clip(y*ph, 0, ph-1)
                if space != 'reference':
                    dx, dy = flow[round(py), round(px)]
                    px, py = px+dx, py+dy
                coords.append((float(px), float(py)))
            r = max(.25, radius * pw)
            brush=brush_coverage(coords,(ph,pw),r,hardness,opacity,flow_rate,spacing)
            restore=self.restore.astype(np.float32)/255
            protect=self.protect.astype(np.float32)/255
            if mode == 'restore':
                restore=restore+brush*(1-restore); protect*=1-brush
            elif mode == 'protect':
                protect=protect+brush*(1-protect); restore*=1-brush
            elif mode == 'auto':
                restore*=1-brush; protect*=1-brush
            self.restore=np.round(restore*255).astype(np.uint8)
            self.protect=np.round(protect*255).astype(np.uint8)
            self.save_edits()

    def rigid_face_matrix(self, index, region):
        pw, ph = self.meta['pw'], self.meta['ph']
        x0,y0,x1,y1 = region
        xs = np.arange(max(0,int(x0*pw)), min(pw,int(x1*pw))+1, 3)
        ys = np.arange(max(0,int(y0*ph)), min(ph,int(y1*ph))+1, 3)
        x,y = np.meshgrid(xs,ys)
        x=np.clip(x,0,pw-1); y=np.clip(y,0,ph-1)
        pts=np.column_stack([x.ravel(),y.ravel()]).astype(np.float32)
        # Use adjacent frames to smooth tracking, without freezing motion.
        lo,hi=max(0,index-1),min(self.meta['frames'],index+2)
        f=np.mean(self.forward[lo:hi].astype(np.float32),axis=0)
        dst=pts+f[y.ravel(),x.ravel()]
        matrix,inliers=cv2.estimateAffinePartial2D(pts,dst,method=cv2.RANSAC,ransacReprojThreshold=1.5)
        if matrix is None or not .8<np.hypot(matrix[0,0],matrix[1,0])<1.2:
            delta=np.median(dst-pts,axis=0)
            matrix=np.array([[1,0,delta[0]],[0,1,delta[1]]],np.float32)
        return matrix.astype(np.float32)

    def restoration_masks(self,index,s):
        m=self.meta; settings=s['settings']; flow=self.alignment_flow(index,settings)
        mask=(self.score<settings['threshold']).astype(np.float32) if settings['auto'] else np.zeros_like(self.score)
        mask=feather_inside(mask,settings['feather']*m['pw']/m['width'])
        white=s['restore'].astype(np.float32)/255
        black=s['protect'].astype(np.float32)/255
        white=np.minimum(white,1-black)
        valid=self.valid.astype(np.float32)/255
        # White and black are mutually exclusive paint contributions. Their
        # source-over updates already attenuate the other color in stroke().
        # Multiplying (1-black) again would square opacity (50% -> 25%).
        automatic=mask*np.clip(1-white-black,0,1)*valid
        manual=white*valid
        if settings.get('alignment_mode','tracked')=='fixed':
            # Both image mapping and opacity remain identical across frames.
            alpha=remap(automatic+manual,flow)
        else:
            roundtrip=flow+remap(self.forward[index].astype(np.float32),flow)
            confidence=np.clip(1-(np.linalg.norm(roundtrip,axis=2)-.75)/2,0,1)
            alpha=remap(automatic,flow)*confidence+remap(manual,flow)
        face_mask=None; matrix=None
        if settings['face'] and s['face_region']:
            pw,ph=m['pw'],m['ph']; x0,y0,x1,y1=s['face_region']
            face_mask=np.zeros((ph,pw),np.float32)
            cv2.ellipse(face_mask,(round((x0+x1)/2*pw),round((y0+y1)/2*ph)),
                        (max(1,round((x1-x0)/2*pw)),max(1,round((y1-y0)/2*ph))),0,0,360,1,-1)
            face_mask=feather_inside(face_mask*valid,settings['face_feather']*pw/m['width'])*(1-black)
            face_mask*=settings['face_strength']
            matrix=(self.fixed_alignment(settings.get('alignment_frame',0))[1].copy()
                    if settings.get('alignment_mode','tracked')=='fixed'
                    else self.rigid_face_matrix(index,s['face_region']))
        return alpha,face_mask,matrix

    def composition_masks(self,index,s,size):
        """Target-space masks shared by compositing and every coverage view."""
        alpha,face,matrix=self.restoration_masks(index,s)
        alpha=cv2.resize(alpha,size,interpolation=cv2.INTER_LINEAR)
        if face is None: return alpha,None,matrix
        width,height=size;pw,ph=self.meta['pw'],self.meta['ph']
        inverse=cv2.invertAffineTransform(matrix)
        target_face=np.empty((height,width),np.float32)
        x=(np.arange(width,dtype=np.float32)+.5)*pw/width-.5
        for top in range(0,height,128):
            bottom=min(height,top+128)
            y=(np.arange(top,bottom,dtype=np.float32)+.5)*ph/height-.5
            cx,cy=np.meshgrid(x,y)
            qx=inverse[0,0]*cx+inverse[0,1]*cy+inverse[0,2]
            qy=inverse[1,0]*cx+inverse[1,1]*cy+inverse[1,2]
            target_face[top:bottom]=cv2.remap(face,qx,qy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        return alpha,target_face,matrix

    def composition_coverage(self,index,s,size):
        """Contribution of the replacement result, including opaque frozen paint.

        Frozen paint can contain a translucent reference mixed with the anchor
        video, but that *finished mixture* replaces the live video completely.
        Its coverage must therefore be one, including its soft painted edges.
        """
        if not s['settings'].get('enabled',True): return np.zeros((size[1],size[0]),np.float32)
        alpha,face,_=self.composition_masks(index,s,size)
        if face is not None: alpha=alpha*(1-face)+face
        alpha*=s['settings']['strength']
        support=self.fixed_paint_support(s,size)
        if support is not None: alpha[support]=1
        return np.clip(alpha,0,1)

    def compose(self, index, full=False, snapshot=None, frame=None):
        m=self.meta;s=snapshot or self.snapshot()
        size=(m['width'],m['height']) if full else (m['pw'],m['ph'])
        if frame is None: frame=self.original_frame(index)
        raw=cv2.resize(frame,size,interpolation=cv2.INTER_LANCZOS4)
        reference=cached_reference(m['image'],Path(m['image']).stat().st_mtime_ns)
        merged=self.compose_target(index,frame,s,size,reference)
        aligned=self.compose_target(index,frame,s,size,reference,mode='aligned')
        return merged,aligned,self.composition_coverage(index,s,size),raw

    def preview(self,index,mode):
        return self.preview_at_size(index,mode,(self.meta['pw'],self.meta['ph']))

    def preview_size(self,resolution):
        m=self.meta
        if resolution=='original': size=(m['image_width'],m['image_height'])
        elif resolution in ('eighth','quarter','half','video','double'):
            scale={'eighth':.125,'quarter':.25,'half':.5,'video':1,'double':2}[resolution]
            size=(max(1,round(m['width']*scale)),max(1,round(m['height']*scale)))
        else: raise ValueError('未知预览分辨率')
        if max(size)>16384 or size[0]*size[1]>60_000_000: raise ValueError('预览尺寸过大，请选择视频原尺寸或较低档位')
        return size

    def original_frame(self,index):
        with av.open(self.meta['video']) as container:
            stream=container.streams.video[0]
            start=stream.start_time or 0
            timestamp=start+round(index/self.meta['fps']/float(stream.time_base))
            container.seek(timestamp,stream=stream,backward=True,any_frame=False)
            for frame in container.decode(stream):
                if frame.pts is not None and frame.pts>=timestamp-1:
                    return frame.to_ndarray(format='bgr24')
        raise ValueError('无法解码所选视频帧')

    def preview_sized(self,index,mode,resolution='half'):
        size=self.preview_size(resolution)
        return self.preview_at_size(index,mode,size)

    def preview_at_size(self,index,mode,size):
        # Every preview size uses the export compositor and the original source.
        # Previously small previews used a separate low-resolution renderer.
        source=self.original_frame(index)
        if mode=='video': return cv2.resize(source,size,interpolation=cv2.INTER_LANCZOS4)
        snapshot=self.snapshot()
        if mode in ('mask','overlay'):
            alpha=self.composition_coverage(index,snapshot,size)
            # Keep every nonzero replacement pixel visible in the 8-bit mask.
            if mode=='mask':return cv2.cvtColor(np.ceil(alpha*255).clip(0,255).astype(np.uint8),cv2.COLOR_GRAY2BGR)
        reference=cached_reference(self.meta['image'],Path(self.meta['image']).stat().st_mtime_ns)
        if mode=='overlay':
            composite=self.compose_target(index,source,snapshot,size,reference)
            return (composite*(1-alpha[...,None]*.55)+np.array([45,200,255],dtype=np.float32)*alpha[...,None]*.55).clip(0,255).astype(np.uint8)
        return self.compose_target(index,source,snapshot,size,reference,mode=mode)

    def export_options(self,values=None):
        options=dict(self.export_preferences)
        values=values or {}
        if not isinstance(values,dict) or set(values)-set(EXPORT_DEFAULTS): raise ValueError('未知导出设置')
        options.update(values)
        if options['size_mode'] not in ('video','2x','4x','original','custom'): raise ValueError('请选择有效的导出尺寸')
        if options['upscale'] not in ('lanczos',*MODEL_FILES): raise ValueError('请选择有效的放大方式')
        if options['device'] not in ('auto','cuda','cpu'): raise ValueError('无效运算设备')
        if options['tile'] not in (96,192,256): raise ValueError('无效分块大小')
        if not isinstance(options['denoise'],(int,float)) or not math.isfinite(options['denoise']) or not 0<=options['denoise']<=1:
            raise ValueError('去噪强度应在0到1之间')
        m=self.meta
        if options['size_mode']=='original': width,height=m['image_width'],m['image_height']
        elif options['size_mode']=='custom': width,height=options['width'],options['height']
        else:
            scale={'video':1,'2x':2,'4x':4}[options['size_mode']]
            width,height=m['width']*scale,m['height']*scale
        if any(type(v) is not int or not 2<=v<=16384 for v in (width,height)) or width*height>60_000_000:
            raise ValueError('宽高需为2～16384的整数，总像素不超过6000万')
        # H.264 yuv420p requires even dimensions. The UI displays this exact rounding rule.
        options['width']=width+width%2; options['height']=height+height%2
        if options['upscale'] in MODEL_FILES and not model_available(options['upscale']):
            raise ValueError(f"未找到 {MODEL_LABELS[options['upscale']]} 权重")
        return options

    def compose_target(self,index,frame,snapshot,size,native_reference,cancel_event=None,mode='composite',_lock_paint=True):
        """Render strips directly from the original image, never from the low-resolution preview."""
        width,height=size; m=self.meta; pw,ph=m['pw'],m['ph']
        result=cv2.resize(frame,(width,height),interpolation=cv2.INTER_LANCZOS4)
        if mode=='composite' and not snapshot['settings'].get('enabled',True): return result
        alpha,face_mask,face_matrix=self.composition_masks(index,snapshot,size)
        inverse=cv2.invertAffineTransform(np.array(m['matrix'],np.float32))
        face_inverse=cv2.invertAffineTransform(face_matrix) if face_matrix is not None else None
        original_h,original_w=native_reference.shape[:2]
        flow=self.alignment_flow(index,snapshot['settings'])
        x=(np.arange(width,dtype=np.float32)+.5)*pw/width-.5

        def source_at(qx,qy):
            sx=(inverse[0,0]*qx+inverse[0,1]*qy+inverse[0,2]+.5)*original_w/pw-.5
            sy=(inverse[1,0]*qx+inverse[1,1]*qy+inverse[1,2]+.5)*original_h/ph-.5
            return cv2.remap(native_reference,sx,sy,cv2.INTER_CUBIC,borderMode=cv2.BORDER_REFLECT_101)

        for top in range(0,height,128):
            if cancel_event is not None and cancel_event.is_set(): raise ExportCancelled()
            bottom=min(height,top+128)
            ys=(np.arange(top,bottom,dtype=np.float32)+.5)*ph/height-.5
            cx,cy=np.meshgrid(x,ys)
            a=alpha[top:bottom,...,None]
            stripe=result[top:bottom].astype(np.float32)
            if mode=='reference':
                stripe=source_at(cx,cy).astype(np.float32)
            elif mode=='aligned' or a.max()>0:
                f=cv2.remap(flow,cx,cy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
                ref=source_at(cx+f[...,0],cy+f[...,1])
                stripe=ref.astype(np.float32) if mode=='aligned' else stripe*(1-a)+ref*a
            if face_mask is not None and mode!='reference':
                qx=face_inverse[0,0]*cx+face_inverse[0,1]*cy+face_inverse[0,2]
                qy=face_inverse[1,0]*cx+face_inverse[1,1]*cy+face_inverse[1,2]
                fa=face_mask[top:bottom,...,None]
                if fa.max()>0: stripe=stripe*(1-fa)+source_at(qx,qy)*fa
            opacity=snapshot['settings']['strength'] if mode=='composite' else 1
            result[top:bottom]=np.clip(result[top:bottom]*(1-opacity)+stripe*opacity,0,255).astype(np.uint8)
        if mode=='composite' and _lock_paint:
            locked=self.fixed_paint_patch(snapshot,size)
            if locked is not None:
                support,patch=locked
                result[support]=patch[support]
        return result

    def render_export_frame(self,index,frame,options,snapshot,reference,upscaler=None,progress=None,cancel=None):
        if upscaler:
            frame=upscaler.enhance(frame,progress,cancel)
        return self.compose_target(index,frame,snapshot,(options['width'],options['height']),reference,cancel)

    def export_preview(self,index,options,snapshot):
        upscaler=None
        try:
            if options['upscale'] in MODEL_FILES:
                upscaler=RealESRGAN(options['denoise'],options['device'],options['tile'],model=options['upscale'])
            reference=cached_reference(self.meta['image'],Path(self.meta['image']).stat().st_mtime_ns)
            return self.render_export_frame(index,self.original_frame(index),options,snapshot,reference,upscaler)
        finally:
            if upscaler: upscaler.close()

    def export(self,options=None,snapshot=None):
        options=self.export_options(options)
        snapshot=snapshot or self.snapshot()
        self.export_status=dict(phase='exporting',progress=0,message='开始全分辨率合成')
        width,height=options['width'],options['height']
        label={'realesrgan':'-AI4x','realesrgan-animevideo':'-AnimeVideo4x'}.get(options['upscale'],'')
        if snapshot['settings'].get('alignment_mode')=='fixed': label+='-Fixed'
        name=f'restored-{time.strftime("%Y%m%d-%H%M%S")}-{width}x{height}{label}.mp4'
        exports=self.path/'exports'; exports.mkdir(exist_ok=True)
        dest=exports/name; temporary=exports/('partial-'+name)
        m=self.meta
        fps=f"{m['fps_num']}/{m['fps_den']}"
        cmd=[shutil.which('ffmpeg') or 'ffmpeg','-hide_banner','-loglevel','error','-y',
             '-f','rawvideo','-pixel_format','bgr24','-video_size',f'{width}x{height}',
             '-framerate',fps,'-i','pipe:0','-i',m['video'],'-map','0:v:0','-map','1:a?',
             '-c:v','libx264','-preset','veryfast','-threads','2','-crf','18','-pix_fmt','yuv420p',
             '-c:a','copy','-movflags','+faststart','-map_metadata','-1',str(temporary)]
        upscaler=None; started=time.monotonic()
        try:
            native_reference=cached_reference(m['image'],Path(m['image']).stat().st_mtime_ns)
            if options['upscale'] in MODEL_FILES:
                self.export_status=dict(phase='exporting',progress=0,message=f"加载 {MODEL_LABELS[options['upscale']]} 4× 超分模型")
                upscaler=RealESRGAN(options['denoise'],options['device'],options['tile'],model=options['upscale'])
            with (exports/'ffmpeg.log').open('wb') as err:
                process=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=err,
                                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                try:
                    container=av.open(m['video'])
                    for i,frame in enumerate(container.decode(video=0)):
                        if i>=m['frames']: break
                        if self.cancel_export.is_set(): raise ExportCancelled()
                        video_frame=frame.to_ndarray(format='bgr24')
                        progress=None
                        if upscaler:
                            def progress(done,total):
                                self.export_status=dict(phase='exporting',progress=round(95*(i+done/total)/m['frames']),
                                  message=f'{upscaler.name} {upscaler.device}：第{i+1}/{m["frames"]}帧，分块{done}/{total}')
                        merged=self.render_export_frame(i,video_frame,options,snapshot,native_reference,upscaler,progress,self.cancel_export)
                        process.stdin.write(merged.tobytes())
                        elapsed=time.monotonic()-started
                        self.export_status=dict(phase='exporting',progress=round(95*(i+1)/m['frames']),
                                                message=f"{width}×{height} 合成 {i+1}/{m['frames']} 帧，保留原音轨",
                                                elapsed=round(elapsed),eta=round(elapsed/(i+1)*(m['frames']-i-1)))
                        del merged,video_frame
                    container.close()
                    process.stdin.close()
                    code=process.wait(timeout=120)
                    if code:
                        raise RuntimeError((exports/'ffmpeg.log').read_text(errors='replace')[-2000:])
                except Exception:
                    if process.poll() is None:
                        process.kill(); process.wait()
                    raise
            temporary.replace(dest)
            metadata=dict(settings=snapshot['settings'],face_region=snapshot['face_region'],
                          source=m['video'],image=m['image'],frames=m['frames'],fps=m['fps'],
                          export_options=options,native_reference=True,
                          model=upscaler.name if upscaler else None,
                          device=upscaler.device if upscaler else 'cpu')
            dest.with_suffix('.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
            self.export_status=dict(phase='done',progress=100,message='导出完成',filename=name,path=str(dest))
        except ExportCancelled:
            temporary.unlink(missing_ok=True)
            self.export_status=dict(phase='cancelled',progress=0,message='已取消导出，编辑内容已保留')
        except Exception as exc:
            self.export_status=dict(phase='error',progress=0,message=str(exc))
            raise
        finally:
            if upscaler: upscaler.close()
