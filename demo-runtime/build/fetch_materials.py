#!/usr/bin/env python3
"""Fetch public release assets anonymously; authentication is optional."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from download_models import download,download_large,atomic_bytes
from render_profile import ROOT,read_json


def extract(archive,destination):
    # Only the expected installation subtrees may be written. Links are resolved
    # by Python's data filter; devices and escapes are rejected.
    allowed={'dependencies','runtime-assets','resources','aec','sdk.zip','libpng.zip','images.tar'}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            path=Path(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] not in allowed:
                raise ValueError('Unexpected build-material archive path')
        tar.extractall(destination,filter='data')


def asset_request(manifest,part,token=''):
    from urllib.parse import quote
    expected='https://github.com/'+manifest['repository']+'/releases/download/'+quote(manifest['tag'],safe='')+'/'+quote(part['name'],safe='')
    if part['browser_url']!=expected:
        raise ValueError('Unexpected public asset endpoint')
    if not token:
        return expected,{}
    api=part['api_url']
    prefix='https://api.github.com/repos/'+manifest['repository']+'/releases/assets/'
    if not api.startswith(prefix) or not api[len(prefix):].isdigit():
        raise ValueError('Unexpected authenticated asset endpoint')
    return api,{'Authorization':'Bearer '+token,'Accept':'application/octet-stream'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--manifest',type=Path,default=ROOT/'model-download/materials.json')
    p.add_argument('--git-credential',action='store_true',help='Read current user GitHub credential helper')
    p.add_argument('--token-stdin',action='store_true',help='Read GitHub token from stdin for automated setup')
    a=p.parse_args();root=a.output.resolve()
    if root.is_relative_to(ROOT.parent):raise ValueError('Assets must be outside checkout')
    token=sys.stdin.readline().strip() if a.token_stdin else os.environ.get('GH_TOKEN',os.environ.get('GITHUB_TOKEN',''))
    if a.git_credential:
        r=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',capture_output=True,text=True,check=True)
        token=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x).get('password','')
    root.mkdir(parents=True,exist_ok=True);cache=root/'.archives';cache.mkdir(exist_ok=True)
    manifest=read_json(a.manifest)
    for part in manifest['assets']:
        path=cache/part['name']
        url,headers=asset_request(manifest,part,token)
        fetch=download_large if part['bytes']>128*1024*1024 else download
        fetch(url,path,part,headers=headers)
        extract(path,root)
    atomic_bytes(root/'materials-receipt.json',a.manifest.read_bytes())
    print('PASS: public source-build materials downloaded and checksum verified.')

if __name__=='__main__':main()
