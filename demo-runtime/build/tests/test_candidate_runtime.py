import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import candidate_ctl as ctl
from prepare_runtime import bindings_from_config,unit_arg

class RuntimeTests(unittest.TestCase):
    def test_only_declared_private_leaves_are_extracted(self):
        template={'a':{'$binding':'private.a'},'b':['product']}
        actual={'a':'private-value','b':['product'],'unrelated':'ignored'}
        self.assertEqual(bindings_from_config(template,actual),{'private.a':'private-value'})
        with self.assertRaises(ValueError):bindings_from_config(template,{'a':'x','b':[]})

    def test_readiness_rejects_exited_helper_and_waits_for_health(self):
        items=[{'Id':name,'Image':'x','Mounts':[],
                'Config':{'Labels':{'com.docker.compose.service':name}},
                'State':{'Running':True,'Status':'running'}} for name in ctl.BACKGROUND_SERVICES]
        self.assertTrue(ctl.services_ready(items))
        items[0]['State']['Health']={'Status':'starting'}
        self.assertFalse(ctl.services_ready(items))
        items[0]['State']={'Running':False,'Status':'exited'}
        with self.assertRaises(RuntimeError):ctl.services_ready(items)
        self.assertFalse(ctl.services_ready([]))

    def test_private_drift_blocks_switch_without_changes(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config';path.write_bytes(b'old')
            c={'reference_hashes':{str(path):hashlib.sha256(b'old').hexdigest()}}
            ctl.reference_ok(c);path.write_bytes(b'new')
            with self.assertRaises(RuntimeError):ctl.reference_ok(c)
            self.assertEqual(path.read_bytes(),b'new')

    def test_pending_queue_read_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/'state/mail-queue/queue.sqlite';path.parent.mkdir(parents=True)
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE jobs(status TEXT)');db.executemany('INSERT INTO jobs VALUES(?)',[('queued',),('sending',),('sent',)])
            self.assertEqual(ctl.pending(root),2)
            with sqlite3.connect(path) as db:self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],3)

    def test_systemd_escaping_does_not_expand_percent_or_break_quotes(self):
        self.assertEqual(unit_arg('/tmp/a% b"c'), '"/tmp/a%% b\\"c"')

    def test_rollback_missing_candidate_units_still_restores_original_ids(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);item={'Id':'original','Image':'sha256:test','Mounts':[],'Config':{'Labels':{'com.docker.compose.project':'prod','com.docker.compose.service':'tts'}}}
            old=json.loads(json.dumps(ctl.identity(item)));journal={'phase':'suspending','containers':[old],'active_units':[]};config={'reference_hashes':{},'project':'test','units':['absent.service']}
            calls=[]
            class Result:stdout='not-found\n'
            with patch.object(ctl,'ROOT',root),patch.object(ctl,'stop_candidate_writers'),patch.object(ctl,'drain'),patch.object(ctl,'inspect',return_value=[item]),patch.object(ctl,'run',side_effect=lambda args,**kw:calls.append([str(x) for x in args])),patch.object(ctl.subprocess,'run',return_value=Result()):
                ctl.restore(config,journal)
            self.assertIn(['docker','start','original'],calls)
            self.assertFalse(any(c[:3]==['systemctl','--user','stop'] for c in calls))
            self.assertEqual(json.loads((root/'switch.json').read_text())['phase'],'restored')

    def test_rollback_refuses_changed_original_container(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);old={'id':'original','image':'sha256:expected','mounts':[],'project':'prod','service':'tts'}
            item={'Id':'original','Image':'sha256:changed','Mounts':[],'Config':{'Labels':{'com.docker.compose.project':'prod','com.docker.compose.service':'tts'}}};calls=[]
            with patch.object(ctl,'ROOT',root),patch.object(ctl,'stop_candidate_writers'),patch.object(ctl,'drain'),patch.object(ctl,'inspect',return_value=[item]),patch.object(ctl,'run',side_effect=lambda args,**kw:calls.append([str(x) for x in args])):
                with self.assertRaises(RuntimeError):ctl.restore({'reference_hashes':{},'project':'test','units':[]},{'phase':'starting','containers':[old],'active_units':[]})
            self.assertFalse(any(c[:2]==['docker','start'] for c in calls))

if __name__=='__main__':unittest.main()
