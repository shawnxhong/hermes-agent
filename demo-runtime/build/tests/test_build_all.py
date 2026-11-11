import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import build_all as b
from build_policy import docker_build

class BuildAllTests(unittest.TestCase):
    def test_resume_requires_inputs_artifacts_and_available_images(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'receipt.json').write_text('{"image":"sha256:test"}')
            record={'status':'passed','key':'inputs','path':d,'artifacts':b.artifacts(p),'images':['sha256:test']}
            self.assertTrue(b.reusable(record,'inputs',lambda x:True))
            self.assertFalse(b.reusable(record,'changed-source',lambda x:True))
            self.assertFalse(b.reusable(record,'inputs',lambda x:False))
            record['status']='failed';self.assertFalse(b.reusable(record,'inputs',lambda x:True));record['status']='passed'
            (p/'receipt.json').write_text('{}');self.assertFalse(b.reusable(record,'inputs',lambda x:True))

    def test_native_artifact_tamper_invalidates_resume(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'receipt.json').write_text('{}');(p/'install').mkdir();(p/'install/lib.so').write_bytes(b'original')
            record={'status':'passed','key':'x','path':d,'artifacts':b.artifacts(p),'images':[]}
            (p/'install/lib.so').write_bytes(b'changed')
            self.assertFalse(b.reusable(record,'x'))

    def test_resource_flags_and_no_cache_are_real_docker_arguments(self):
        with patch.dict('os.environ',{'HERMES_BUILD_POLICY':json.dumps({'jobs':2,'memory_gb':8,'no_cache':True})}):
            cmd=docker_build('id','context')
        self.assertEqual(cmd[cmd.index('--cpu-quota')+1],'200000')
        self.assertEqual(cmd[cmd.index('--memory')+1],'8g')
        self.assertIn('--no-cache',cmd)

    def test_failed_child_keeps_log_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'stage.log'
            with self.assertRaises(RuntimeError):b.execute([sys.executable,'-c','print("failure evidence"); raise SystemExit(9)'],log)
            self.assertIn('failure evidence',log.read_text())

    def test_missing_materials_reported_together(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError) as result:b.check_materials(Path(d))
            self.assertIn('sdk.zip',str(result.exception));self.assertIn('libpng.zip',str(result.exception))

    def test_changed_input_fingerprint(self):
        self.assertNotEqual(b.fingerprint({'commit':'a','assets':'x'}),b.fingerprint({'commit':'a','assets':'y'}))

if __name__=='__main__':unittest.main()
