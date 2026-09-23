"""Build a relocatable Windows bundle from a tested ComfyUI installation.

No user projects, models, account files, or .git directories are exported.
Run with a normal build-time Python; end users use the bundled interpreter.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

PYTHON_URL='https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
NODES=('ComfyUI-GGUF','ComfyUI-KJNodes','rgthree-comfy','TE-Speed-MiniMaxH3-OSS','comfyui-krea2edit')

def git_copy(source,dest):
    paths=subprocess.check_output(['git','-C',str(source),'ls-files','-z']).decode('utf-8').split('\0')
    for name in paths:
        if not name or name.startswith(('.github/','.git/')): continue
        p=source/name
        if not p.is_file(): continue
        target=dest/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,target)

def download(url,path):
    if path.exists() and zipfile.is_zipfile(path): return
    print('Download:',url,flush=True)
    part=path.with_suffix('.part')
    try:
        with urllib.request.urlopen(url,timeout=60) as r,part.open('wb') as out: shutil.copyfileobj(r,out)
    except OSError:
        curl=Path(os.environ['WINDIR'])/'System32/curl.exe'
        subprocess.run([str(curl),'-fL','--retry','2','--connect-timeout','20','--max-time','240',url,'-o',str(part)],check=True)
    if not zipfile.is_zipfile(part):raise ValueError('Downloaded archive is incomplete: '+str(part))
    part.replace(path)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-h3',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cache',type=Path,required=True)
    args=parser.parse_args()
    source=args.source_h3.resolve();root=args.output.resolve();cache=args.cache.resolve()
    if root==source or source in root.parents: raise ValueError('Build outside the original installation')
    root.mkdir(parents=True,exist_ok=True);cache.mkdir(parents=True,exist_ok=True)
    runtime=root/'runtime';runtime.mkdir(exist_ok=True)
    archive=cache/'python-3.12.10-embed-amd64.zip';download(PYTHON_URL,archive)
    with zipfile.ZipFile(archive) as z:z.extractall(runtime)
    # ._pth enables isolated paths: no system Python, registry, PYTHONPATH or user site.
    (runtime/'python312._pth').write_text('python312.zip\n.\nLib/site-packages\n../portable\nimport site\n',encoding='utf-8')
    packages=source/'venv/Lib/site-packages'
    print('Copy tested dependencies...',flush=True)
    shutil.copytree(packages,runtime/'Lib/site-packages',dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo','.git','direct_url.json'))
    # Application-local release runtime DLLs; never copy debug/non-redist DLLs.
    # The build host must have a licensed Visual Studio installation.
    for name in ('msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll','msvcp140_atomic_wait.dll','msvcp140_codecvt_ids.dll','concrt140.dll','vcomp140.dll','vcruntime140.dll','vcruntime140_1.dll','vcruntime140_threads.dll'):
        dll=Path(os.environ['WINDIR'])/'System32'/name
        if dll.exists():shutil.copy2(dll,runtime/name)
    print('Export ComfyUI and open-source nodes...',flush=True)
    git_copy(source/'ComfyUI',root/'ComfyUI')
    components=[]
    for node in NODES:
        location=source/'ComfyUI/custom_nodes'/node
        if not location.exists(): continue
        git_copy(location,root/'ComfyUI/custom_nodes'/node)
        components.append(dict(name=node,url=subprocess.check_output(['git','-C',str(location),'remote','get-url','origin'],text=True).strip(),commit=subprocess.check_output(['git','-C',str(location),'rev-parse','HEAD'],text=True).strip()))
    # Retouch's source is taken from this checkout, including pending release changes.
    repo=Path(__file__).resolve().parent.parent
    git_copy(repo,root/'retouch-studio')
    for p in (repo/'static').glob('*'):
        if p.is_file(): shutil.copy2(p,root/'retouch-studio/static'/p.name)
    (root/'retouch-studio/start.bat').write_text('@echo off\r\ncd /d "%~dp0.."\r\nruntime\\python.exe -s portable\\manager.py --open\r\nif errorlevel 1 pause\r\n',encoding='ascii')
    shutil.copytree(repo/'portable',root/'portable',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('logs','models','inbox','retouch-data','ComfyUI/input','ComfyUI/output','ComfyUI/temp','ComfyUI/user/default/workflows','bin','licenses'):
        (root/name).mkdir(parents=True,exist_ok=True)
    catalog=json.loads((repo/'portable/models.json').read_text(encoding='utf-8'))
    for model in catalog['models']:
        if not model['path'].startswith('upscale_models/'):continue
        original=source/'models'/model['path']
        if original.is_file() and hashlib.sha256(original.read_bytes()).hexdigest()==model['sha256']:
            target=root/'models'/model['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,target)
    # This verified beginner workflow only needs core ComfyUI + ComfyUI-GGUF.
    for name in ('H3_新手入门_已实测.json','H3_3060_T2V_starter.json'):
        shutil.copy2(source/'ComfyUI/user/default/workflows'/name,root/'ComfyUI/user/default/workflows'/name)
        workflow=root/'ComfyUI/user/default/workflows'/name
        workflow.write_text(re.sub(r'[A-Za-z]:/[^"\n]*?/ComfyUI/output/video','ComfyUI/output/video',workflow.read_text(encoding='utf-8')),encoding='utf-8')
    for workflow in (repo/'portable/workflows').glob('*.json'):
        shutil.copy2(workflow,root/'ComfyUI/user/default/workflows'/workflow.name)
    # Shared models are configured at launch, relative to the selected directory.
    (root/'retouch-studio/config.local.json').write_text(json.dumps({'data_dir':'../retouch-data','weights_dir':'../models/upscale_models'}),encoding='utf-8')
    ffmpeg_zip=cache/'ffmpeg-release-essentials.zip'
    download('https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',ffmpeg_zip)
    with zipfile.ZipFile(ffmpeg_zip) as z:
        for info in z.infolist():
            name=Path(info.filename).name
            if name in ('ffmpeg.exe','ffprobe.exe'):
                with z.open(info) as inp,(root/'bin'/name).open('wb') as out: shutil.copyfileobj(inp,out)
            elif name.lower().startswith(('license','readme')) and not info.is_dir():
                (root/'licenses'/('ffmpeg-'+name)).write_bytes(z.read(info))
    (root/'licenses/FFmpeg-source.txt').write_text('FFmpeg binary distributor: https://www.gyan.dev/ffmpeg/builds/\nCorresponding source/build information: https://www.gyan.dev/ffmpeg/builds/#sources\nFFmpeg source: https://git.ffmpeg.org/ffmpeg.git\n',encoding='utf-8')
    (root/'licenses/Microsoft-runtime.txt').write_text('Microsoft Visual C++ release runtime libraries are included application-locally under the Visual Studio redistribution terms. They are not covered by this project\'s MIT license.\nhttps://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files\nhttps://visualstudio.microsoft.com/license-terms/\n',encoding='utf-8')
    with urllib.request.urlopen('https://raw.githubusercontent.com/xinntao/Real-ESRGAN/master/LICENSE',timeout=30) as response:
        (root/'licenses/Real-ESRGAN-LICENSE.txt').write_bytes(response.read())
    (root/'licenses/Real-ESRGAN-models.json').write_text(json.dumps([m for m in catalog['models'] if m['path'].startswith('upscale_models/')],indent=2),encoding='utf-8')
    pick=root/'portable/pick_path.ps1';pick.write_text(pick.read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
    shutil.copy2(repo/'portable/README.md',root/'使用说明.md')
    compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler),'/nologo','/target:winexe','/optimize+','/reference:System.Windows.Forms.dll','/out:'+str(root/'H3便携启动器.exe'),str(repo/'portable/Launcher.cs')],check=True)
    (root/'Start.bat').write_text('@echo off\r\ncd /d "%~dp0"\r\nruntime\\python.exe -s portable\\manager.py --open\r\nif errorlevel 1 pause\r\n',encoding='ascii')
    (root/'BUILD-INFO.json').write_text(json.dumps({'python_url':PYTHON_URL,'python_archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'ffmpeg_url':'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip','ffmpeg_archive_sha256':hashlib.sha256(ffmpeg_zip.read_bytes()).hexdigest(),'comfyui_commit':subprocess.check_output(['git','-C',str(source/'ComfyUI'),'rev-parse','HEAD'],text=True).strip(),'custom_nodes':components,'runtime':'Windows x64, Python 3.12.10, PyTorch CUDA 13.0'},indent=2),encoding='utf-8')
    print('BASE READY:',root,flush=True)

if __name__=='__main__':main()
