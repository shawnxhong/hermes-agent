"""Startup-only capability probe; run before the CLI owns its subscription."""
import json
import socket
import uuid

identity = 'startup-' + uuid.uuid4().hex
with socket.socket(socket.AF_UNIX) as s:
    s.settimeout(3)
    s.connect('/ipc/wake/service.sock')
    s.sendall((json.dumps({'mode': 'recording', 'endpoint': 'aisp-eos',
                           'recording_id': identity})+'\n').encode())
    data = b''
    while not data.endswith(b'\n') and len(data) < 4096:
        part = s.recv(1)
        if not part:
            raise ConnectionError('Wake service closed handshake')
        data += part
    result = json.loads(data)
    assert result.get('event') == 'ready'
    assert result.get('endpoint') == 'aisp-eos'
    assert result.get('recording_id') == identity
print('Live EOS subscription ready; probe closed before CLI startup')
