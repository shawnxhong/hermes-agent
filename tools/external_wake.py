"""Notification subscriber for an independently capturing wake ASR service."""
import json
import select
import socket
import threading
import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class ExternalWakeEngine:
    service_capture = True
    startup_timeout = 6.0
    frame_length = 1280  # Keep existing 80ms capture/polling; service batches ASR.

    def __init__(self, cfg):
        self.cfg = cfg
        self.path = cfg.get("external", {}).get("socket", "/ipc/wake/service.sock")
        self.sock = None
        self.buffer = b""
        self.last_match = None
        self.lock = threading.RLock()
        self.replay = None
        self.force_segment_version = 0
        self.service_instance = None

    def set_replay(self, replay):
        self.replay = replay

    def subscription(self):
        aliases = self.cfg.get("sherpa", {}).get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        request = {"aliases": [self.cfg.get("phrase", "Hello Intel"), *aliases]}
        if self.replay is not None:
            if "force_segment" in self.replay:
                metadata = Path(self.path).with_name("endpoint-config.json")
                value = json.loads(metadata.read_text()) if metadata.exists() else {}
                if value.get("force_segment_version") == 1:
                    request["force_segment"] = {"instance": value["instance"],
                        "reason": "scene_ready", "request_id": self.replay["force_segment"]}
                else:
                    logger.warning("Wake service has no force-segment support; using legacy scene resume")
            else:
                request["replay"] = self.replay
        return request

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
            s.settimeout(5.5)
            try:
                s.connect(self.path)
                request = self.subscription()
                self.replay = None
                s.sendall((json.dumps(request) + "\n").encode())
                line = b""
                while not line.endswith(b"\n") and len(line) < 4096:
                    part = s.recv(1)
                    if not part:
                        raise RuntimeError("Wake service disconnected during handshake")
                    line += part
                message = json.loads(line)
                self.force_segment_version = message.get("force_segment_version", 0)
                self.service_instance = message.get("instance")
                if not self.ready(message):
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
        self._start_subscription(callback)

    def _start_subscription(self, callback):
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
                        self.last_reason = self.last_reason or self.reason
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
    """Native EOS plus a fresh keyword stream fed only post-cue recorder PCM."""
    phrase = None
    reason = "aisp_eos"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.endpoint_cfg = cfg
        self._keyword = None
        self._keyword_config = None
        self._prepared = None
        self._audio_scope = None
        self._audio_callback = None

    def _clear_keyword(self):
        self._prepared = None
        self.stop()
        if self._keyword is not None:
            self._keyword.close()
        self._keyword = self._keyword_config = None

    def prepare(self):
        try:
            return self._prepare()
        except Exception:
            self._clear_keyword()
            raise

    def _prepare(self):
        metadata = Path(self.path).with_name("endpoint-config.json")
        if not metadata.exists():
            if self._prepared is not None:
                self._clear_keyword()
            return False  # Compatibility with an old sidecar, native path only.
        value = json.loads(metadata.read_text())
        cfg = value.get("end_phrase")
        if (value.get("recording_end_phrase_version") != 2
                or not isinstance(value.get("instance"), str)
                or not isinstance(cfg, dict) or type(cfg.get("enabled")) is not bool
                or not isinstance(cfg.get("phrase"), str) or not cfg["phrase"].strip()):
            raise ValueError("Invalid recording endpoint metadata")
        signature = (cfg["enabled"], cfg["phrase"], self.endpoint_cfg.get("model_dir"))
        if signature != self._keyword_config:
            self._prepared = None
            self.stop()
            if self._keyword is not None:
                self._keyword.close()
                self._keyword = None
            if cfg["enabled"]:
                from tools.voice_endpoint import EndPhraseDetector
                self._keyword = EndPhraseDetector({**self.endpoint_cfg, "phrase": cfg["phrase"]})
            self._keyword_config = signature
        if self._prepared is None or self._prepared["instance"] != value["instance"]:
            logger.info("recording_keyword_prepared source=recorder_pcm enabled=%s instance=%s", cfg["enabled"], value["instance"])
        self._prepared = value
        self.phrase = cfg["phrase"] if cfg["enabled"] else None
        return True

    def subscription(self):
        request = {**super().subscription(), "endpoint": "aisp-eos"}
        if self._prepared is not None:
            request.update(end_phrase_source="recorder", instance=self._prepared["instance"])
        return request

    def ready(self, message):
        if not (super().ready(message) and message.get("endpoint") == "aisp-eos"):
            return False
        cfg = message.get("end_phrase") or {}
        if self._prepared is not None:
            if (message.get("instance") != self._prepared["instance"]
                    or message.get("recording_end_phrase_version") != 2
                    or message.get("end_phrase_source") != "recorder"
                    or cfg != self._prepared["end_phrase"]):
                return False
        self.phrase = cfg.get("phrase") if cfg.get("enabled") is True else None
        return True

    def matched(self, message):
        reasons = ("aisp_eos",) if self._prepared is not None else ("aisp_eos", "end_phrase")
        if super().matched(message) and message.get("reason") in reasons:
            self.last_reason = message["reason"]
            return True
        return False

    def arm_audio(self, callback, sample_rate, threshold):
        """Called before recorder collection is enabled, with a fresh stream."""
        self.stop()
        completed = threading.Event()
        self._audio_scope = completed
        self._audio_callback = callback
        self.last_reason = None
        if self._keyword is not None:
            try:
                self._keyword.start(lambda: self._finish(completed, "end_phrase"), sample_rate, threshold)
            except Exception:
                self._keyword.close()
                self._keyword = self._keyword_config = self._prepared = None
                logger.warning("Recording keyword stream unavailable; native endpoint remains available", exc_info=True)

    def _finish(self, scope, reason):
        with self.lock:
            if scope is not self._audio_scope or scope.is_set():
                return
            scope.set()
            self.last_reason = reason
            self._stop.set()
            if self._keyword is not None:
                self._keyword.request_stop()
            callback = self._audio_callback
            if reason == "end_phrase" and self.force_segment_version == 1 and self.sock is not None:
                # A tiny nonblocking control write precedes recorder teardown,
                # which closes this subscription. SDK flush runs on the service.
                request = {"command": "force_segment", "reason": "end_phrase",
                    "instance": self.service_instance, "recording_id": self.recording_id,
                    "request_id": "end-" + str(self.recording_id)}
                payload = (json.dumps(request) + "\n").encode()
                try:
                    sent = self.sock.send(payload, socket.MSG_DONTWAIT)
                    if sent != len(payload):
                        raise OSError("Incomplete force-segment request")
                except OSError:
                    logger.warning("Recording force-segment request was not delivered", exc_info=True)
        logger.info("recording_endpoint delivered reason=%s source=%s recording_id=%s",
                    reason, "recorder_pcm" if reason == "end_phrase" else "native_asr", self.recording_id)
        callback()

    def start(self, callback, sample_rate, threshold):
        if self._audio_scope is None:
            self.arm_audio(callback, sample_rate, threshold)
        scope = self._audio_scope
        if scope.is_set():
            return
        try:
            self._start_subscription(lambda: self._finish(scope, self.last_reason or "aisp_eos"))
        except Exception:
            # A temporary native socket failure must not disable the independent
            # recorder keyword worker. The next recording retries the socket.
            super().stop()
            raise

    def feed(self, pcm):
        scope = self._audio_scope
        if self._keyword is not None and scope is not None and not scope.is_set():
            self._keyword.feed(pcm)

    def stop(self):
        scope = self._audio_scope
        if scope is not None:
            scope.set()
        self._audio_scope = None
        if self._keyword is not None:
            self._keyword.stop()
        super().stop()
