"""Split a ZIP into release-sized assets and compile the automatic downloader."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while block:=f.read(8*1024*1024):h.update(block)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('archive',type=Path);p.add_argument('--tag',required=True);p.add_argument('--repo',default='2771096196/reference-restore-studio')
    args=p.parse_args();archive=args.archive.resolve();parts=[]
    limit=1500*1024*1024
    if archive.stat().st_size<1900*1024*1024:
        parts=[{'Name':archive.name,'Size':archive.stat().st_size,'Sha256':digest(archive)}]
    else:
        with archive.open('rb') as source:
            number=1
            while source.tell()<archive.stat().st_size:
                part=archive.with_name(archive.name+f'.{number:03d}');size=0;h=hashlib.sha256()
                with part.open('wb') as out:
                    while size<limit:
                        block=source.read(min(8*1024*1024,limit-size))
                        if not block:break
                        out.write(block);h.update(block);size+=len(block)
                parts.append({'Name':part.name,'Size':size,'Sha256':h.hexdigest()});number+=1
                print('Prepared',part.name,size,flush=True)
    manifest={'Version':args.tag,'BaseUrl':f'https://github.com/{args.repo}/releases/download/{args.tag}/','ArchiveSha256':digest(archive),'Parts':parts}
    output=archive.parent
    (output/'download-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    source=Path(__file__).with_name('DownloadHelper.cs').read_text(encoding='utf-8')
    generated=output/'DownloadHelper.generated.cs'
    generated.write_text(source.replace('__RELEASE_MANIFEST__',json.dumps(manifest).replace('"','""')),encoding='utf-8-sig')
    compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    exe=output/'H3-Portable-Downloader.exe'
    command=[str(compiler),'/nologo','/target:winexe','/optimize+','/out:'+str(exe)]
    for assembly in ('System.Windows.Forms','System.Drawing','System.IO.Compression','System.IO.Compression.FileSystem','System.Web.Extensions'):
        command.append('/reference:'+assembly+'.dll')
    subprocess.run([*command,str(generated)],check=True)
    checks=[manifest['ArchiveSha256']+'  '+archive.name]+[part['Sha256']+'  '+part['Name'] for part in parts]+[digest(exe)+'  '+exe.name]
    (output/'SHA256SUMS.txt').write_text('\n'.join(checks)+'\n',encoding='ascii')
    print('Downloader ready:',exe,flush=True)

if __name__=='__main__':main()
