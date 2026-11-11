import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'render_profile.py'
spec = importlib.util.spec_from_file_location('render_profile', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def bindings(profile):
    schema = m.read_json(m.ROOT / 'profiles' / f'{profile}.bindings.schema.json')
    return {key: 'test-value' for key in schema['required']}


class ProfileTests(unittest.TestCase):
    def test_both_profiles_render_and_preserve_channel_scope(self):
        voice = m.render('state', bindings('state'))
        gateway = m.render('gateway-state', bindings('gateway-state'))
        self.assertIn('skills', voice['platform_toolsets']['cli'])
        self.assertIn('skills_readonly', gateway['platform_toolsets']['feishu'])
        self.assertIn('cronjob', voice['agent']['disabled_toolsets'])
        self.assertNotIn('cronjob', gateway['agent']['disabled_toolsets'])
        self.assertFalse(voice['auxiliary']['title_generation']['enabled'])
        self.assertNotIn('title_generation', gateway['auxiliary'])

    def test_bindings_replace_leaves_without_mutating_template(self):
        b = bindings('state'); key = 'state.providers.custom.api_key'
        b[key] = 'private-test-marker'
        self.assertEqual(m.render('state', b)['providers']['custom']['api_key'], b[key])
        self.assertEqual(m.render('state', bindings('state'))['providers']['custom']['api_key'], 'test-value')

    def test_reject_missing_extra_wrong_type_placeholder_and_wrong_profile(self):
        base = bindings('state'); key = next(iter(base))
        for change in [dict(base, extra='x'), {k:v for k,v in base.items() if k!=key},
                       dict(base, **{key:None}), dict(base, **{key:' '}),
                       dict(base, **{key:'REPLACE_ME'}), bindings('gateway-state')]:
            with self.subTest(change=list(change)):
                with self.assertRaises(ValueError): m.render('state', change)

    def test_private_file_and_no_overwrite_or_symlink_follow(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'config.yaml'
            m.write_new(p, {'test':'x'})
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError): m.write_new(p, {})
            link=Path(directory)/'link'; link.symlink_to(p)
            with self.assertRaises(FileExistsError): m.write_new(link, {})
            self.assertEqual(json.loads(p.read_text()), {'test':'x'})

    def test_cli_check_and_invalid_input_do_not_expose_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'bindings.json'; p.write_text(json.dumps(bindings('state')))
            command=[sys.executable,str(SCRIPT),'--profile','state','--bindings',str(p),'--check']
            self.assertEqual(subprocess.run(command,capture_output=True).returncode,0)
            p.write_text('{"secret": "private-test-marker", invalid')
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn('private-test-marker',result.stdout+result.stderr)

    def test_duplicate_keys_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'input'; p.write_text('{"x":1,"x":2}')
            with self.assertRaises(ValueError): m.read_json(p)


if __name__ == '__main__': unittest.main()
