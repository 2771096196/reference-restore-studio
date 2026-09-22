import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from engine import Project, read_grayscale, write_image

class PortableProjectTests(unittest.TestCase):
    def test_unicode_mask_and_relative_media_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'项目 空格';root.mkdir()
            image=np.full((16,20,3),100,np.uint8)
            for name in ('image.png','aligned-reference.png'):write_image(root/name,image)
            write_image(root/'valid.png',np.full((16,20),255,np.uint8))
            write_image(root/'restore.png',np.full((16,20),128,np.uint8))
            write_image(root/'protect.png',np.zeros((16,20),np.uint8))
            (root/'video.mp4').write_bytes(b'fixture')
            for name in ('back','forward'):np.save(root/(name+'.npy'),np.zeros((1,16,20,2),np.float16))
            np.save(root/'motion.npy',np.zeros((16,20),np.float32))
            meta=dict(width=20,height=16,pw=20,ph=16,image_width=20,image_height=16,frames=1,image='image.png',video='video.mp4',matrix=[[1,0,0],[0,1,0]])
            (root/'meta.json').write_text(json.dumps(meta),encoding='utf-8')
            project=Project(root)
            self.assertEqual(project.meta['image'],str((root/'image.png').resolve()))
            self.assertTrue(np.all(project.valid==255));self.assertTrue(np.all(project.restore==128))
            self.assertTrue(np.all(read_grayscale(root/'restore.png')==128))
            # Legacy absolute source paths should recover from files in this project.
            meta['image']=str(Path(temporary)/'no-longer-present'/'image.png')
            meta['video']=str(Path(temporary)/'no-longer-present'/'video.mp4')
            (root/'meta.json').write_text(json.dumps(meta),encoding='utf-8')
            relocated=Project(root)
            self.assertEqual(relocated.meta['video'],str((root/'video.mp4').resolve()))
            for instance in (project,relocated):
                instance.back._mmap.close()
                instance.forward._mmap.close()

if __name__=='__main__':unittest.main()
