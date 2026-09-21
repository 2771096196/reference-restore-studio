"""Official Real-ESRGAN general-x4v3 checkpoints, tiled inference through Spandrel."""
from pathlib import Path
import cv2
import numpy as np

from config import WEIGHT_ROOT
MODEL_NAMES=('realesr-general-x4v3.pth','realesr-general-wdn-x4v3.pth')


def model_available():
    return all((WEIGHT_ROOT/name).is_file() for name in MODEL_NAMES)


class ExportCancelled(Exception):
    pass


class RealESRGAN:
    def __init__(self,denoise=.5,device='auto',tile=192):
        import torch
        import spandrel
        self.torch=torch
        if not model_available():
            raise ValueError('缺少官方 Real-ESRGAN 权重，请先运行 python download_models.py')
        if device=='cuda' and not torch.cuda.is_available():
            raise ValueError('CUDA 不可用，请选择 CPU')
        self.device='cuda' if device in ('auto','cuda') and torch.cuda.is_available() else 'cpu'
        # Both files are official SRVGG checkpoints. Use safe tensor-only deserialization.
        strong=torch.load(WEIGHT_ROOT/MODEL_NAMES[0],map_location='cpu',weights_only=True)
        weak=torch.load(WEIGHT_ROOT/MODEL_NAMES[1],map_location='cpu',weights_only=True)
        strong=strong.get('params_ema',strong.get('params',strong))
        weak=weak.get('params_ema',weak.get('params',weak))
        weights={k:denoise*v+(1-denoise)*weak[k] for k,v in strong.items()}
        self.model=spandrel.ModelLoader().load_from_state_dict(weights).eval().to(self.device)
        self.half=self.device=='cuda'
        if self.half: self.model.half()
        self.scale=self.model.scale
        self.tile=tile
        self.pad=24
        if self.scale!=4: raise ValueError('超分模型倍率不正确')

    def enhance(self,frame,progress=None,cancel=None):
        torch=self.torch
        h,w=frame.shape[:2]
        output=np.empty((h*4,w*4,3),np.uint8)
        total=((w+self.tile-1)//self.tile)*((h+self.tile-1)//self.tile)
        count=0
        with torch.inference_mode():
            for y in range(0,h,self.tile):
                for x in range(0,w,self.tile):
                    if cancel is not None and cancel.is_set(): raise ExportCancelled()
                    right,bottom=min(w,x+self.tile),min(h,y+self.tile)
                    x0,y0,x1,y1=max(0,x-self.pad),max(0,y-self.pad),min(w,right+self.pad),min(h,bottom+self.pad)
                    rgb=cv2.cvtColor(frame[y0:y1,x0:x1],cv2.COLOR_BGR2RGB)
                    tensor=torch.from_numpy(np.ascontiguousarray(rgb.transpose(2,0,1))).unsqueeze(0)
                    tensor=tensor.to(device=self.device,dtype=torch.float16 if self.half else torch.float32)/255
                    try:
                        result=self.model(tensor)
                    except torch.cuda.OutOfMemoryError as exc:
                        raise RuntimeError('超分显存不足：停止其他GPU任务，或把分块改为96 / 选择CPU。') from exc
                    result=result[0,:, (y-y0)*4:(bottom-y0)*4, (x-x0)*4:(right-x0)*4]
                    result=result.clamp(0,1).mul(255).round().byte().permute(1,2,0).cpu().numpy()
                    output[y*4:bottom*4,x*4:right*4]=cv2.cvtColor(result,cv2.COLOR_RGB2BGR)
                    del tensor,result
                    count+=1
                    if progress: progress(count,total)
        return output

    def close(self):
        self.model.to('cpu')
        if self.device=='cuda': self.torch.cuda.empty_cache()
