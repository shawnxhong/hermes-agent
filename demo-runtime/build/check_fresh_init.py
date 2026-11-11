#!/usr/bin/env python3
"""Initialize both profiles without models, secrets, hardware or old state."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import yaml
from render_profile import ROOT,render,read_json,write_new


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--roles-build',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    roles=read_json(a.roles_build/'receipt.json')['roles'];results={}
    for profile,role in [('state','cli'),('gateway-state','gateway')]:
        home=a.output/profile;home.mkdir()
        bindings=read_json(ROOT/'profiles'/f'{profile}.bindings.example.json')
        bindings={k:('nobody@example.invalid' if 'recipient' in k or 'chat_id' in k else 'unused-test-value') for k in bindings}
        config=render(profile,bindings);write_new(home/'config.yaml',config)
        subprocess.run(['docker','run','--pull=never','--rm','--network','none','--cpus','1','--memory','2g',
                        '--user',f'{os.getuid()}:{os.getgid()}','-v',f'{home.resolve()}:/state',roles[role]['image'],'init'],check=True)
        assert yaml.safe_load((home/'config.yaml').read_text())==config
        assert (home/'SOUL.md').read_bytes()==(ROOT/f'{profile}-SOUL.md').read_bytes()
        assert not (home/'state.db').exists()
        results[profile]={'initialized':True,'config_parity':True,'soul_parity':True,'network':'none'}
    write_new(a.output/'receipt.json',results)
    print('Fresh profiles initialized with dummy bindings; no credentials or model files used.')

if __name__=='__main__':main()
