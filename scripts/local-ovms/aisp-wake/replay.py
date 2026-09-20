"""Replay WAV into the real external service; no microphone or speaker access."""
import argparse
import audioop
import json
import select
import socket
import time
import wave

p = argparse.ArgumentParser()
p.add_argument("--socket", required=True)
p.add_argument("files", nargs="+")
a = p.parse_args()
for path in a.files:
    with wave.open(path) as wav:
        assert wav.getsampwidth() == 2 and wav.getnchannels() == 1
        pcm = wav.readframes(wav.getnframes())
        pcm, _ = audioop.ratecv(pcm, 2, 1, wav.getframerate(), 16000, None)
    pcm = bytes(16000) + pcm + bytes(64000)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect(a.socket)
        s.sendall(b'{"aliases":["Hello Intel","Hi Intel","High Intel","Hi Inter","Hello Inter","Hello Indel","Hey Intel","Hey Inter","Hey Indel"]}\n')
        assert s.recv(4096).startswith(b'{"event":"ready"}')
        start = time.monotonic()
        fired = None
        for offset in range(0, len(pcm), 16000):
            if select.select([s], [], [], 0)[0]:
                fired = s.recv(4096).decode()
                break
            s.sendall(pcm[offset:offset+16000].ljust(16000,b'\0'))
            if select.select([s], [], [], .5)[0]:
                fired = s.recv(4096).decode()
                break
        print(json.dumps({"file":path,"seconds":round(time.monotonic()-start,3),"event":fired}),flush=True)
    time.sleep(1)
