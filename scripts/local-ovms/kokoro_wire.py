"""Small bounded Unix-socket framing shared by the local TTS client/server."""
import struct


def receive(sock, limit):
    def exact(size):
        data = bytearray()
        while len(data) < size:
            part = sock.recv(min(65536, size-len(data)))
            if not part:
                raise ConnectionError('Incomplete local TTS response')
            data.extend(part)
        return bytes(data)
    size = struct.unpack('!I', exact(4))[0]
    if size > limit:
        raise ValueError('Local TTS frame exceeds limit')
    return exact(size)


def send(sock, data):
    sock.sendall(struct.pack('!I', len(data)) + data)
