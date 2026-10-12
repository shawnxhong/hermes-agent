"""Bounded local-only socket protocol; never transmits audio off-host."""
import json
import socket
import struct

MAX_AUDIO_BYTES = 24 * 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024


def receive(sock, limit):
    def exact(size):
        chunks = bytearray()
        while len(chunks) < size:
            part = sock.recv(min(size - len(chunks), 65536))
            if not part:
                raise ConnectionError('ASR connection closed before complete frame')
            chunks.extend(part)
        return bytes(chunks)
    size = struct.unpack('!I', exact(4))[0]
    if size > limit:
        raise ValueError('ASR frame exceeds size limit')
    return exact(size)


def send(sock, payload):
    sock.sendall(struct.pack('!I', len(payload)) + payload)


def request(path, audio=b'', timeout=30):
    if len(audio) > MAX_AUDIO_BYTES:
        raise ValueError('Audio exceeds 24 MiB limit')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        send(sock, audio)
        result = json.loads(receive(sock, MAX_RESPONSE_BYTES))
    if not isinstance(result, dict) or not isinstance(result.get('success'), bool):
        raise ValueError('Invalid ASR response')
    return result
