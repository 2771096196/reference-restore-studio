"""Create a clean distribution ZIP; never include local projects or settings."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

def allowed(relative):
    parts=relative.parts
    if any(p in ('.git','__pycache__','.pytest_cache') for p in parts):return False
    if relative.suffix in ('.pyc','.pyo','.log','.part','.importing'):return False
    if relative.name in ('direct_url.json','portable-config.json'):return False
    if parts[0] in ('logs','inbox','retouch-data','cache','models-test'):return False
    if parts[0]=='models':return len(parts)==3 and parts[1]=='upscale_models' and parts[2] in ('realesr-general-x4v3.pth','realesr-general-wdn-x4v3.pth','realesr-animevideov3.pth')
    if parts[0]=='ComfyUI':
        if len(parts)>1 and parts[1] in ('input','output','temp','models'):return False
        if len(parts)>1 and parts[1]=='user':return len(parts)>4 and parts[2:4]==('default','workflows') and relative.suffix=='.json'
        if relative.name in ('extra_model_paths.yaml','comfyui.db'):return False
    if parts[0]=='retouch-studio' and len(parts)>1 and parts[1] in ('data','models','logs','exports','portable'):return False
    if parts[0]=='retouch-studio' and relative.name=='config.local.json':return False
    return True

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();root=args.root.resolve();dest=args.output.resolve();dest.parent.mkdir(parents=True,exist_ok=True)
    files=[x for x in root.rglob('*') if x.is_file() and allowed(x.relative_to(root))]
    total=sum(x.stat().st_size for x in files);done=0;reported=0
    print(f'Packing {len(files)} files / {total/1024**3:.2f} GiB',flush=True)
    with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        for folder in ('models','inbox','logs','retouch-data','ComfyUI/input','ComfyUI/output','ComfyUI/temp'):
            z.writestr('H3-Portable/'+folder+'/',b'')
        for file in sorted(files):
            z.write(file,'H3-Portable/'+file.relative_to(root).as_posix());done+=file.stat().st_size
            if done-reported>256*1024*1024:
                print(f'{done/total*100:.0f}% / compressed {dest.stat().st_size/1024**2:.0f} MiB',flush=True);reported=done
    digest=hashlib.sha256()
    with dest.open('rb') as f:
        while block:=f.read(8*1024*1024):digest.update(block)
    dest.with_suffix(dest.suffix+'.sha256').write_text(digest.hexdigest()+'  '+dest.name+'\n',encoding='ascii')
    print(json.dumps({'file':str(dest),'bytes':dest.stat().st_size,'sha256':digest.hexdigest()}),flush=True)

if __name__=='__main__':main()
