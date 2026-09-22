"""Launch either app with an isolated embedded interpreter and an explicit cwd."""
import os
import runpy
import sys
from pathlib import Path

root=Path(__file__).resolve().parent.parent
name=sys.argv[1]
directory=root/('ComfyUI' if name=='comfyui' else 'retouch-studio')
script=directory/('main.py' if name=='comfyui' else 'server.py')
sys.path.insert(0,str(directory))
os.chdir(directory)
sys.argv=[str(script),*sys.argv[2:]]
runpy.run_path(str(script),run_name='__main__')
