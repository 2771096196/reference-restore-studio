"""Explicitly download and verify the two optional official Real-ESRGAN models."""
import hashlib
import urllib.request
from config import WEIGHT_ROOT

MODELS={
    'realesr-general-x4v3.pth':'8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292',
    'realesr-general-wdn-x4v3.pth':'1641f8c4464b9f097c9fdda5589273713f67cf59f3d909e0bd688f0cee269dca',
}
ORIGIN='https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/'


def main():
    WEIGHT_ROOT.mkdir(parents=True,exist_ok=True)
    for name,expected in MODELS.items():
        target=WEIGHT_ROOT/name
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest()!=expected:
                raise RuntimeError(f'Existing file checksum mismatch; move it aside before retrying: {target}')
            print(f'Already verified: {name}')
            continue
        temporary=target.with_suffix('.download')
        print(f'Downloading official model: {name}')
        with urllib.request.urlopen(ORIGIN+name,timeout=60) as response,temporary.open('wb') as file:
            while block:=response.read(1024*1024): file.write(block)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=expected:
            raise RuntimeError(f'Download checksum mismatch: {temporary}')
        temporary.replace(target)
        print(f'Verified: {target}')


if __name__=='__main__':main()
