import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable'))
from model_store import ModelStore, Cancelled

class Response(io.BytesIO):
    def __init__(self,data,status=200,headers=None):
        super().__init__(data);self.status=status;self.headers=headers or {}
    def geturl(self):return 'https://example.test/model'

class PortableModelsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.payload=b'known-model-weights'*127
        self.model={'id':'test','filename':'sample.safetensors','path':'diffusion_models/sub/sample.safetensors','bytes':len(self.payload),'sha256':hashlib.sha256(self.payload).hexdigest(),'urls':['https://example.test/model'],'groups':[]}
        self.store=ModelStore(self.root/'models',{'models':[self.model]},self.root/'cache.json')

    def test_renamed_import_is_verified_placed_and_source_is_kept(self):
        source=self.root/'download-renamed.safetensors';source.write_bytes(self.payload)
        model,status=self.store.import_file(source)
        self.assertEqual(model['id'],'test');self.assertEqual(source.read_bytes(),self.payload)
        self.assertEqual(self.store.target(model).read_bytes(),self.payload)
        self.assertEqual(self.store.status(model),'verified')

    def test_corrupted_same_name_is_not_imported(self):
        source=self.root/self.model['filename'];source.write_bytes(b'x'*len(self.payload))
        with self.assertRaisesRegex(ValueError,'内容不匹配'):self.store.import_file(source)
        self.assertFalse(self.store.target(self.model).exists());self.assertTrue(source.exists())

    def test_existing_different_file_is_never_overwritten(self):
        target=self.store.target(self.model);target.parent.mkdir(parents=True);target.write_bytes(b'old-version')
        source=self.root/self.model['filename'];source.write_bytes(self.payload)
        with self.assertRaises(ValueError):self.store.import_file(source)
        self.assertEqual(target.read_bytes(),b'old-version')

    def test_resume_honors_range_and_verifies_final_content(self):
        target=self.store.target(self.model);target.parent.mkdir(parents=True)
        part=target.with_name(target.name+'.part');part.write_bytes(self.payload[:50])
        with patch('model_store.urllib.request.urlopen',return_value=Response(self.payload[50:],206,{'Content-Range':f'bytes 50-{len(self.payload)-1}/{len(self.payload)}'})) as opener:
            self.store.download('test')
            self.assertEqual(opener.call_args.args[0].get_header('Range'),'bytes=50-')
        self.assertEqual(target.read_bytes(),self.payload);self.assertFalse(part.exists())

    def test_server_ignoring_range_restarts_cleanly(self):
        target=self.store.target(self.model);target.parent.mkdir(parents=True);target.with_name(target.name+'.part').write_bytes(self.payload[:20])
        with patch('model_store.urllib.request.urlopen',return_value=Response(self.payload)):
            self.store.download('test')
        self.assertEqual(target.read_bytes(),self.payload)

    def test_cancel_keeps_partial_download_for_resume(self):
        cancel=threading.Event();cancel.set()
        with patch('model_store.urllib.request.urlopen',return_value=Response(self.payload)):
            with self.assertRaises(Cancelled):self.store.download('test',cancel)
        target=self.store.target(self.model)
        self.assertFalse(target.exists());self.assertTrue(target.with_name(target.name+'.part').exists())

    def test_manifest_cannot_escape_model_root(self):
        with self.assertRaises(ValueError):self.store.target(dict(self.model,path='../escape.pth'))

if __name__=='__main__':unittest.main()
