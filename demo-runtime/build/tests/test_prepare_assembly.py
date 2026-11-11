import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BUILD=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BUILD))
import prepare_assembly as m


class AssemblyTests(unittest.TestCase):
    def test_compose_binding_rejects_extra_missing_and_invalid_ids(self):
        b=m.read_json(m.ROOT/'deploy/host.example.json')
        for k in ('FAN_CONTROL','FAN_RPM','TEMPERATURES'):b[k]='/test/'+k
        b['RENDER_GID']='999'
        result=m.render_compose(b)
        self.assertEqual(result['services']['cli']['user'],b['UID']+':'+b['GID'])
        self.assertIn('ovms',result['services'])
        for change in [dict(b,extra='x'),dict(b,UID='root'),dict(b,RUNTIME='relative'),dict(b,PROJECT='bad project')]:
            with self.assertRaises(ValueError):m.render_compose(change)
        del b['RUNTIME']
        with self.assertRaises(ValueError):m.render_compose(b)

    def test_fan_discovery_handles_renumbering_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            fan=root/'class/thermal/cooling_device9';fan.mkdir(parents=True);(fan/'type').write_text('TFN1\n')
            hw=root/'class/hwmon/hwmon7';hw.mkdir(parents=True);(hw/'name').write_text('acpi_fan\n');(hw/'fan1_input').write_text('2000')
            (root/'devices/platform/coretemp.0').mkdir(parents=True)
            self.assertEqual(m.discover_fan(root)['FAN_RPM'],str(hw/'fan1_input'))
            other=root/'class/hwmon/hwmon8';other.mkdir();(other/'name').write_text('acpi_fan');(other/'fan1_input').write_text('0')
            with self.assertRaises(ValueError):m.discover_fan(root)

    def test_asset_validation_detects_corruption_and_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'asset').write_bytes(b'abc');manifest=root/'lock.json'
            manifest.write_text(json.dumps({'asset':{'size':3,'sha256':hashlib.sha256(b'abc').hexdigest()}}))
            m.verify_assets(manifest,root)
            (root/'asset').write_bytes(b'xyz')
            with self.assertRaises(ValueError):m.verify_assets(manifest,root)
            with self.assertRaises(ValueError):m.safe_path(root,'../elsewhere')

    def test_materialize_uses_tracked_sources_and_role_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo=root/'repo';repo.mkdir()
            subprocess.run(['git','init','-q',str(repo)],check=True)
            (repo/'core.py').write_text('old\n');(repo/'deleted.py').write_text('remove\n');(repo/'private.env').write_text('do not copy')
            subprocess.run(['git','-C',str(repo),'add','core.py','deleted.py'],check=True)
            demo=repo/'demo-runtime';(demo/'assembly').mkdir(parents=True);(demo/'override.py').write_text('new\n')
            record={'source':'demo-runtime/override.py','sha256':hashlib.sha256(b'new\n').hexdigest()}
            manifest={'roles':{'cli':{'core_overrides':{'core.py':record},'core_remove':['deleted.py'],'plugins':{},'container_helpers':{},'accepted_image_id':'test'}}}
            (demo/'assembly/roles.json').write_text(json.dumps(manifest))
            subprocess.run(['git','-C',str(repo),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture'],check=True)
            out=root/'result';m.materialize('cli',out,demo)
            self.assertEqual((out/'app/core.py').read_text(),'new\n')
            self.assertFalse((out/'app/deleted.py').exists());self.assertFalse((out/'app/private.env').exists())
            with self.assertRaises(ValueError):m.materialize('cli',out,demo)
            (demo/'override.py').write_text('corrupt')
            with self.assertRaises(ValueError):m.validate_sources(demo)

if __name__=='__main__':unittest.main()
