import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from download_models import download,download_large,normalize_template
from fetch_materials import extract
from deploy import install_commands

class Downloads(unittest.TestCase):
    def test_fresh_and_resume_through_real_http(self):
        data=b'model weights'*100;seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                r=self.headers.get('Range');seen.append(r);offset=int(r.split('=')[1].split('-')[0]) if r else 0
                end=int(r.split('-')[1]) if r and r.split('-')[1] else len(data)-1
                self.send_response(206 if r else 200)
                if r:self.send_header('Content-Range',f'bytes {offset}-{end}/{len(data)}')
                self.end_headers();self.wfile.write(data[offset:end+1])
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                path=Path(d)/'model';expected={'size':len(data),'sha256':hashlib.sha256(data).hexdigest()};url=f'http://127.0.0.1:{server.server_port}/model'
                download(url,path,expected);self.assertEqual(path.read_bytes(),data)
                self.assertEqual(download(url,path,expected)['network_bytes'],0)
                path.unlink();path.with_name('model.part').write_bytes(data[:100]);download(url,path,expected)
                self.assertEqual(path.read_bytes(),data);self.assertEqual(seen,[None,'bytes=100-'])
                path.unlink();path.with_name('model.part').write_bytes(data[:80]);download_large(url,path,expected,connections=2,chunk_size=100)
                self.assertEqual(path.read_bytes(),data)
                path.write_bytes(b'wrong')
                with self.assertRaises(ValueError):download(url,path,expected)
        finally:server.shutdown();thread.join();server.server_close()

    def test_hash_failure_has_finite_retries_and_no_published_file(self):
        data=b'wrong'
        class Response(io.BytesIO):
            status=200;headers={}
        with tempfile.TemporaryDirectory() as d,patch('download_models.urllib.request.build_opener') as opener,patch('download_models.time.sleep'):
            opener.return_value.open.side_effect=lambda *a,**k:Response(data)
            path=Path(d)/'model'
            with self.assertRaises(RuntimeError):download('https://example.invalid',path,{'size':5,'sha256':'0'*64})
            self.assertEqual(opener.return_value.open.call_count,4);self.assertFalse(path.exists())

    def test_archive_escape_and_command_overwrite_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive=root/'x.tar'
            with tarfile.open(archive,'w') as tar:
                info=tarfile.TarInfo('../escape');info.size=1;tar.addfile(info,io.BytesIO(b'x'))
            with self.assertRaises(ValueError):extract(archive,root/'assets')
            bindir=root/'bin';bindir.mkdir();command=bindir/'hermes-box-voice';command.write_text('production')
            with self.assertRaises(ValueError):install_commands(root/'candidate',bindir)
            self.assertEqual(command.read_text(),'production')

    def test_template_patch_rejects_upstream_drift(self):
        self.assertEqual(normalize_template('before',{'before':'before','after':'after'}),'after')
        with self.assertRaises(ValueError):normalize_template('changed',{'before':'before','after':'after'})

if __name__=='__main__':unittest.main()

class AuthRedirectTests(unittest.TestCase):
    def test_private_asset_auth_is_not_forwarded_to_cdn(self):
        import urllib.request
        from download_models import SafeRedirect
        req=urllib.request.Request('https://api.github.com/repos/example/assets/1',headers={'Authorization':'Bearer private','Accept':'application/octet-stream'})
        redirected=SafeRedirect().redirect_request(req,None,302,'Found',{},'https://release-assets.githubusercontent.com/file')
        self.assertIsNone(redirected.get_header('Authorization'))
        self.assertEqual(redirected.get_header('Accept'),'application/octet-stream')
