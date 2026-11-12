#!/usr/bin/env python3
"""Download pinned public model artifacts, normalize product text, verify locks."""
import argparse
import concurrent.futures
import fcntl
import hashlib
import http.client
import json
from pathlib import Path
import tarfile
import time
import urllib.request
from render_profile import ROOT,read_json
from build_native import sha
from prepare_assembly import safe_path,verify_assets


def atomic_bytes(path,data):
    tmp=path.with_name(path.name+'.tmp');tmp.write_bytes(data);tmp.replace(path)


def normalize_template(raw,patch):
    if raw.count(patch['before'])!=1:raise ValueError('Unexpected upstream chat template')
    return raw.replace(patch['before'],patch['after'])


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        redirect=super().redirect_request(req,fp,code,msg,headers,newurl)
        from urllib.parse import urlparse
        if redirect and urlparse(req.full_url).netloc!=urlparse(newurl).netloc:
            redirect.remove_header('Authorization')
        return redirect


def download(url,path,expected,timeout=60,headers=None):
    if path.is_symlink():raise ValueError('Refusing symlink output')
    if path.exists():
        if sha(path)==expected['sha256']:return {'status':'verified','network_bytes':0}
        raise ValueError('Existing model differs from lock: '+path.name+'; use a new destination')
    path.parent.mkdir(parents=True,exist_ok=True)
    part=path.with_name(path.name+'.part');network=0
    if part.is_symlink():raise ValueError('Refusing symlink partial file')
    for attempt in range(4):
        try:
            offset=part.stat().st_size if part.exists() else 0
            size=expected.get('bytes',expected.get('size'))
            if size and offset==size:
                if sha(part)==expected['sha256']:part.replace(path);return {'status':'downloaded','network_bytes':network}
                part.unlink();offset=0
            request=urllib.request.Request(url+('&' if '?' in url else '?')+'download=true&attempt='+str(time.time_ns()),headers={**(headers or {}),'User-Agent':'hermes-model-fetch/1',**({'Range':f'bytes={offset}-'} if offset else {})})
            with urllib.request.build_opener(SafeRedirect()).open(request,timeout=timeout) as response:
                append=offset>0 and response.status==206
                if append and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):raise ValueError('Invalid range response')
                with part.open('ab' if append else 'wb') as out:
                    last=time.monotonic()
                    while chunk:=response.read(4*1024*1024):
                        out.write(chunk);network+=len(chunk)
                        if size and out.tell()>size:raise ValueError('Download larger than locked size')
                        if time.monotonic()-last>=30:
                            print(f'DOWNLOADING {path.parent.name}/{path.name}: {out.tell()}/{size} bytes',flush=True);last=time.monotonic()
            if (size and part.stat().st_size!=size) or sha(part)!=expected['sha256']:raise ValueError('Downloaded file hash/size mismatch')
            part.replace(path);print('VERIFIED '+str(path.parent.name+'/'+path.name),flush=True)
            return {'status':'downloaded','network_bytes':network}
        except (OSError,ValueError,http.client.HTTPException) as error:
            if isinstance(error,ValueError) and part.exists():part.unlink()
            if attempt==3:raise RuntimeError('Download failed: '+path.name+' ('+type(error).__name__+')') from None
            time.sleep(attempt+1)
    raise RuntimeError('Unreachable')


def download_large(url,path,expected,connections=4,chunk_size=256*1024*1024):
    """Bounded public HTTP range downloads, merged in order; final hash is mandatory."""
    if path.exists():return download(url,path,expected)
    path.parent.mkdir(parents=True,exist_ok=True)
    part=path.with_name(path.name+'.part')
    if part.is_symlink():raise ValueError('Refusing symlink partial file')
    total=expected.get('bytes',expected.get('size'));offset=part.stat().st_size if part.exists() else 0
    if offset>total:raise ValueError('Partial model exceeds expected size')
    network=0
    def chunk_job(start,end):
        chunk_path=path.with_name(path.name+f'.range-{start}-{end}')
        if chunk_path.is_symlink():raise ValueError('Refusing symlink range file')
        for attempt in range(4):
            try:
                req=urllib.request.Request(url+('&' if '?' in url else '?')+'download=true&attempt='+str(time.time_ns()),headers={'Range':f'bytes={start}-{end}','User-Agent':'hermes-model-fetch/1'})
                with urllib.request.build_opener(SafeRedirect()).open(req,timeout=60) as response:
                    if response.status!=206 or response.headers.get('Content-Range')!=f'bytes {start}-{end}/{total}':raise ValueError('Server did not honor bounded range')
                    count=0
                    with chunk_path.open('wb') as out:
                        while block:=response.read(4*1024*1024):
                            count+=len(block)
                            if count>end-start+1:raise ValueError('Oversized range')
                            out.write(block)
                if count!=end-start+1:raise ValueError('Incomplete range')
                return chunk_path,count
            except (OSError,ValueError,http.client.HTTPException):
                if attempt==3:raise RuntimeError('Bounded range download failed') from None
                time.sleep(attempt+1)
    # At most connections chunks on disk/in flight, never an extra model-sized copy.
    with concurrent.futures.ThreadPoolExecutor(max_workers=connections) as pool:
        while offset<total:
            batch=[(start,min(start+chunk_size,total)-1) for start in range(offset,min(offset+connections*chunk_size,total),chunk_size)]
            futures=[pool.submit(chunk_job,start,end) for start,end in batch]
            for future in futures:
                chunk_path,count=future.result()
                with part.open('ab') as out,chunk_path.open('rb') as source:
                    while block:=source.read(4*1024*1024):out.write(block)
                chunk_path.unlink();offset+=count;network+=count
                print(f'DOWNLOADING {path.parent.name}/{path.name}: {offset}/{total} bytes',flush=True)
    if sha(part)!=expected['sha256']:raise ValueError('Completed ranged model hash mismatch; remove partial and retry')
    part.replace(path);print('VERIFIED '+str(path.parent.name+'/'+path.name),flush=True)
    return {'status':'downloaded','network_bytes':network,'connections':connections}


def download_kws(cache,destination,source):
    archive=cache/'legacy-kws.tar.bz2'
    download(source['url'],archive,source)
    records=read_json(ROOT/'locks/runtime-assets.json')['files']
    prefix='opt/hermes/resources/wakewords/'
    selected={k:v for k,v in records.items() if k.startswith(prefix)}
    with tarfile.open(archive,'r:bz2') as tar:
        for name,expected in selected.items():
            member=tar.getmember(name[len(prefix):])
            if not member.isfile():raise ValueError('Non-file KWS archive member')
            data=tar.extractfile(member).read()
            if hashlib.sha256(data).hexdigest()!=expected['sha256']:raise ValueError('KWS hash mismatch')
            path=safe_path(destination.resolve(),name);path.parent.mkdir(parents=True,exist_ok=True)
            atomic_bytes(path,data)
            path.chmod(0o755 if expected.get('executable') else 0o644)
    print('PASS: legacy KWS downloaded and verified.',flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=4);p.add_argument('--runtime-assets',type=Path)
    a=p.parse_args();root=a.output.resolve()
    if not 1<=a.workers<=8:p.error('workers must be 1..8')
    if root.is_relative_to(ROOT.parent):p.error('Models must be outside the source checkout')
    root.mkdir(parents=True,exist_ok=True)
    with (root/'.download.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        sources=read_json(ROOT/'model-download/sources.json');jobs=[];results={}
        for model,source in sources['huggingface'].items():
            records=read_json(ROOT/'locks'/f'{model}.json')
            for name,record in records.items():
                if (model,name) in [('llm','chat_template.jinja'),('wake-asr','generation_config.json')]:continue
                upstream='chat_template.jinja' if name=='chat_template.jinja.bak_think' else name
                url=f"https://huggingface.co/{source['repo']}/resolve/{source['revision']}/{upstream}"
                jobs.append((model+'/'+name,url,safe_path(root,model+'/'+name),record))
        for name,record in read_json(ROOT/'locks/tts.json').items():jobs.append(('tts/'+name,sources['tts'][name],root/'tts'/name,record))
        def work(job):
            name,url,path,record=job;fetch=download_large if record.get('size',0)>1024**3 else download;return name,fetch(url,path,record)
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
            for name,result in pool.map(work,jobs):results[name]=result
        if a.runtime_assets:download_kws(root,a.runtime_assets,sources['kws'])
        patch=read_json(ROOT/'model-download/llm-template.patch.json')
        rendered=normalize_template((root/'llm/chat_template.jinja.bak_think').read_text(),patch).encode()
        expected=read_json(ROOT/'locks/llm.json')['chat_template.jinja']['sha256']
        if hashlib.sha256(rendered).hexdigest()!=expected:raise ValueError('Normalized template hash mismatch')
        atomic_bytes(root/'llm/chat_template.jinja',rendered)
        atomic_bytes(root/'wake-asr/generation_config.json',(ROOT/'model-download/wake-generation_config.json').read_bytes())
        for model in ['llm','question-asr','wake-asr','tts']:verify_assets(ROOT/'locks'/f'{model}.json',root/model)
        receipt={'status':'verified','sources':sources,'models':['llm','question-asr','wake-asr','tts'],'files':results,'normalizations':['llm-default-thinking-off','wake-max-new-tokens-128'],'production_modified':False}
        atomic_bytes(root/'download-receipt.json',(json.dumps(receipt,indent=2)+'\n').encode())
        print('PASS: all four model directories match release file hashes.',flush=True)

if __name__=='__main__':main()
