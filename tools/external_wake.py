"""Notification subscriber for an independently capturing wake ASR service."""
import json
import select
import socket
import threading
import logging
import uuid

logger = logging.getLogger(__name__)


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

    def subscription(self):
        aliases = self.cfg.get("sherpa", {}).get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        return {"aliases": [self.cfg.get("phrase", "Hello Intel"), *aliases]}

    def ready(self, message):
        return message.get("event") == "ready"

    def matched(self, message):
        if message.get("event") == "wake":
            self.last_match = (message["alias"], "")
            return True
        return False

    def reset(self):
        with self.lock:
            self.close()
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(3)
            try:
                s.connect(self.path)
                s.sendall((json.dumps(self.subscription()) + "\n").encode())
                line = b""
                while not line.endswith(b"\n") and len(line) < 4096:
                    part = s.recv(1)
                    if not part:
                        raise RuntimeError("Wake service disconnected during handshake")
                    line += part
                if not self.ready(json.loads(line)):
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
                if self.matched(msg):
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


class ExternalEndPhraseDetector(ExternalWakeEngine):
    """One recording-scoped subscription; audio remains owned by the sidecar."""
    phrase = "thank you"
    reason = "end_phrase"
    def __init__(self, cfg):
        super().__init__({"external": {"socket": cfg.get("socket", "/ipc/wake/service.sock")}})
        self.recording_id = None
        self.last_reason = None
        self._stop = threading.Event()
        self._thread = None

    def subscription(self):
        return {"mode": "recording", "recording_id": self.recording_id}

    def ready(self, message):
        return (super().ready(message) and message.get("mode") == "recording"
                and message.get("recording_id") == self.recording_id)

    def matched(self, message):
        return (message.get("event") == "end_recording"
                and message.get("recording_id") == self.recording_id)

    def start(self, callback, sample_rate, threshold):
        self.stop()
        with self.lock:
            self.recording_id = uuid.uuid4().hex
            self.last_reason = None
            self._stop = threading.Event()
            self.reset()  # Ready only after server has armed this recording.
            self._thread = threading.Thread(target=self._run_endpoint,
                args=(self._stop, callback), daemon=True, name="external-recording-end")
            self._thread.start()

    def _run_endpoint(self, stopped, callback):
        try:
            while not stopped.wait(.04):
                with self.lock:
                    if stopped.is_set() or self.sock is None:
                        return
                    if self.process(None):
                        self.last_reason = self.reason
                        stopped.set()
                        callback()
                        return
        except Exception:
            logger.warning("External recording-end service unavailable; manual/silence stop remains available", exc_info=True)
        finally:
            with self.lock:
                if stopped is self._stop:
                    self.close()

    def feed(self, pcm):
        pass  # Never duplicate the microphone stream into the control socket.

    def stop(self):
        with self.lock:
            self._stop.set()
            self.close()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)


class ExternalEOSDetector(ExternalEndPhraseDetector):
    """Recording-scoped native AISP endpoint; no keyword or second capture."""
    phrase = None
    reason = "aisp_eos"

    def subscription(self):
        return {**super().subscription(), "endpoint": "aisp-eos"}

    def ready(self, message):
        return super().ready(message) and message.get("endpoint") == "aisp-eos"

    def matched(self, message):
        return super().matched(message) and message.get("reason") == "aisp_eos"
