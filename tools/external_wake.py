"""Notification subscriber for an independently capturing wake ASR service."""
import json
import select
import socket
import threading


class ExternalWakeEngine:
    service_capture = True
    frame_length = 1280  # Keep existing 80ms capture/polling; service batches ASR.

    def __init__(self, cfg):
        self.cfg = cfg
        self.path = cfg.get("external", {}).get("socket", "/ipc/wake/service.sock")
        self.sock = None
        self.buffer = b""
        self.last_match = None
        self.lock = threading.RLock()

    def reset(self):
        with self.lock:
            self.close()
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(3)
            try:
                s.connect(self.path)
                aliases = self.cfg.get("sherpa", {}).get("aliases", [])
                if isinstance(aliases, str):
                    aliases = [aliases]
                s.sendall((json.dumps({"aliases": [self.cfg.get("phrase", "Hello Intel"), *aliases]}) + "\n").encode())
                line = b""
                while not line.endswith(b"\n") and len(line) < 4096:
                    part = s.recv(1)
                    if not part:
                        raise RuntimeError("Wake service disconnected during handshake")
                    line += part
                if json.loads(line).get("event") != "ready":
                    raise RuntimeError("Wake service is not ready")
            except Exception:
                s.close()
                raise
            self.sock = s
            s.settimeout(0.5)
            self.buffer = b""

    def process(self, frame):
        with self.lock:
            if self.sock is None:
                return False
            return self._receive()

    def _receive(self):
        while select.select([self.sock], [], [], 0)[0]:
            part = self.sock.recv(4096)
            if not part:
                self.close()
                raise RuntimeError("Wake ASR service disconnected")
            self.buffer += part
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                msg = json.loads(line)
                if msg.get("event") == "wake":
                    self.last_match = (msg["alias"], "")  # Not a Hermes profile.
                    self.close()
                    return True
        return False

    def close(self):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()
                self.sock = None

    pause = close
