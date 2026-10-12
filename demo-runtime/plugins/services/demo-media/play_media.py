#!/usr/bin/env python3
"""Container-only transport adapter; host owns actual desktop processes."""
import argparse,json,socket,os
p=argparse.ArgumentParser();p.add_argument('action',choices=['list','play','mp3','mp4','stop','status']);p.add_argument('--target',default='');a=p.parse_args()
try:
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(25);s.connect(os.environ.get('HERMES_MEDIA_BRIDGE','/ipc/media/service.sock'))
        s.sendall((json.dumps(vars(a))+'\n').encode());reply=json.loads(s.makefile('rb').readline(65536))
except Exception as e:reply={'success':False,'error':'Host media bridge unavailable: '+type(e).__name__}
print(json.dumps(reply));raise SystemExit(0 if reply.get('success') else 1)
