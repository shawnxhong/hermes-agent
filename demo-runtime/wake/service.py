"""Local-only live-ASR wake service. Log every partial, trigger once per timestamp."""
import argparse
from collections import deque, OrderedDict
import array
import ctypes as C
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import socket
import threading
import time
import unicodedata
import uuid

import yaml

from tools.media_stop_policy import compile_media_stop_pattern
from tools.playback_control import request as playback_request
from tools.wake_audio import start_capture_with_aec


DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")


def load_config(path=DEFAULT_CONFIG):
    """Load the single wake-service vocabulary source; fail on invalid input."""
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or set(config) != {"asr", "wake", "media_stop", "audio"}:
        raise ValueError("Wake config requires exactly asr, wake, media_stop and audio sections")
    asr, wake = config["asr"], config["wake"]
    if not isinstance(asr, dict) or set(asr) != {"hotwords", "source_language"}:
        raise ValueError("asr requires hotwords and source_language")
    if not isinstance(wake, dict) or set(wake) != {"aliases"}:
        raise ValueError("wake requires aliases")
    for key, values in (("asr.hotwords", asr["hotwords"]), ("wake.aliases", wake["aliases"])):
        if not isinstance(values, list) or any(
            not isinstance(v, str) or not v.strip() or "\n" in v or "\r" in v for v in values
        ):
            raise ValueError(f"{key} must be a list of nonempty single-line strings")
    if not wake["aliases"]:
        raise ValueError("wake.aliases must not be empty")
    if asr['source_language'] not in ('auto', 'zh', 'en', 'ja', 'yue', 'ko', 'de', 'fr', 'ru', 'it', 'sp', 'pt'):
        raise ValueError("Invalid asr.source_language code")
    audio = config['audio']
    if not isinstance(audio, dict) or set(audio) != {'aec', 'aec_reference'}:
        raise ValueError("audio requires aec and aec_reference")
    if audio['aec'] not in ('os', 'none'):
        raise ValueError("audio.aec must be os or none")
    if not isinstance(audio['aec_reference'], str) or not audio['aec_reference'].strip():
        raise ValueError("audio.aec_reference must be a nonempty string")
    validate_media_stop(config["media_stop"])
    return config


def validate_media_stop(config):
    if not isinstance(config, dict) or set(config) != {"enabled", "match"}:
        raise ValueError("media_stop requires enabled and match")
    if not isinstance(config["enabled"], bool):
        raise ValueError("media_stop.enabled must be a boolean")
    match = config["match"]
    if not isinstance(match, dict) or set(match) != {"verbs", "targets"}:
        raise ValueError("media_stop.match requires verbs and targets")
    return compile_media_stop_pattern(match["verbs"], match["targets"])


def asr_context(config=None):
    """Convert configured hotwords to the SDK's one-phrase-per-line context."""
    config = load_config() if config is None else config
    return "\n".join(config["asr"]["hotwords"])


class MediaStopWorker:
    """Keep host IPC and its timeout off the native ASR callback thread."""

    def __init__(self, event, request):
        self.event, self.request = event, request
        self.pending = queue.Queue(maxsize=32)
        self.closing = threading.Event()
        self.thread = None

    def submit(self, timestamp):
        # Server.lock serializes submit calls. Start lazily: EOS-only tests and
        # sessions that never hear a stop command need no additional thread.
        try:
            self.pending.put_nowait(timestamp)
        except queue.Full:
            self.event(event="media_stop_failed", timestamp=timestamp, reason="queue_full")
            return
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, daemon=True, name="wake-media-stop")
            self.thread.start()

    def run(self):
        while not self.closing.is_set():
            try:
                timestamp = self.pending.get(timeout=.2)
            except queue.Empty:
                continue
            started = time.monotonic()
            try:
                result = self.request("stop")
                success = (isinstance(result, dict) and result.get("success") is True
                           and result.get("action") == "stopped" and result.get("state") == "inactive")
                self.event(event="media_stop_completed" if success else "media_stop_failed",
                           timestamp=timestamp, elapsed_ms=round((time.monotonic()-started)*1000),
                           reason="confirmed" if success else "unconfirmed")
            except Exception as exc:
                self.event(event="media_stop_failed", timestamp=timestamp,
                           elapsed_ms=round((time.monotonic()-started)*1000), reason=type(exc).__name__)
            finally:
                self.pending.task_done()

    def close(self):
        self.closing.set()
        if self.thread is not None:
            self.thread.join(timeout=11)


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join("".join(c for c in text if not unicodedata.category(c).startswith("P")).split())


class WakeGate:
    def __init__(self, aliases, continuous=False):
        self.aliases = [(a, normalize(a)) for a in aliases if isinstance(a, str) and normalize(a)]
        if not self.aliases:
            raise ValueError("Nonempty aliases required")
        self.triggered = set()
        self.active = True
        self.continuous = continuous
        self.recent = deque()

    def match(self, timestamp, text):
        if timestamp in self.triggered:
            return "same_sentence", None
        if not self.active:
            return "inactive", None
        norm = normalize(text)
        for alias, needle in self.aliases:
            if re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", norm):
                return "trigger", alias
        return "miss", None

    def commit(self, timestamp):
        self.triggered.add(timestamp)
        self.recent.append(timestamp)
        if len(self.recent) > 1024:
            self.triggered.discard(self.recent.popleft())
        self.active = self.continuous

    def result(self, timestamp, text):
        action, alias = self.match(timestamp, text)
        if action == "trigger":
            self.commit(timestamp)
        return action, alias


class Native:
    @staticmethod
    def eos_flag(library):
        lib = C.CDLL(library)
        lib.wake_eos_flag.argtypes = []
        lib.wake_eos_flag.restype = C.c_uint32
        return lib.wake_eos_flag()

    def __init__(self, library, model, device, callback, config=None):
        config = load_config() if config is None else config
        context = asr_context(config)
        self.lib = C.CDLL(library)
        self.cb = C.CFUNCTYPE(None, C.c_int64, C.c_uint32, C.c_char_p)(callback)
        self.lib.wake_create.argtypes = [C.c_char_p, C.c_char_p, C.c_char_p, C.c_char_p, type(self.cb)]
        self.lib.wake_create.restype = C.c_void_p
        self.lib.wake_feed.argtypes = [C.c_void_p, C.POINTER(C.c_float), C.c_size_t, C.c_int64]
        self.lib.wake_flush.argtypes = [C.c_void_p]
        self.handle = self.lib.wake_create(model.encode(), device.encode(), context.encode(), config["asr"]["source_language"].encode(), self.cb)
        if not self.handle:
            raise RuntimeError("AISP live-asr initialization failed")

    def feed(self, pcm, timestamp):
        samples = array.array("h", pcm)
        data = (C.c_float * len(samples))(*(v / 32768 for v in samples))
        if self.lib.wake_feed(self.handle, data, len(data), timestamp):
            raise RuntimeError("AISP feed failed")

    def flush(self):
        if self.lib.wake_flush(self.handle):
            raise RuntimeError("AISP flush failed")

    def start_capture(self, source, aec="none", reference=""):
        self.lib.wake_start_capture.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p, C.c_char_p]
        self.lib.wake_capture_alive.argtypes = [C.c_void_p]
        if self.lib.wake_start_capture(self.handle, source.encode(), aec.encode(), reference.encode()):
            raise RuntimeError("AISP microphone capture failed")

    def stop_capture(self):
        self.lib.wake_stop_capture.argtypes = [C.c_void_p]
        if self.lib.wake_stop_capture(self.handle):
            raise RuntimeError("AISP capture cleanup failed")

    def playback_devices(self):
        result = []
        callback_type = C.CFUNCTYPE(None, C.c_int32, C.c_char_p)
        callback = callback_type(lambda number, name: result.append((number, name.decode())))
        self.lib.wake_enumerate_playback.argtypes = [callback_type]
        if self.lib.wake_enumerate_playback(callback):
            raise RuntimeError("AISP playback enumeration failed")
        return result

    def capture_alive(self):
        return bool(self.lib.wake_capture_alive(self.handle))


class Server:
    def __init__(self, log, aliases, eos_flag, stop_request=playback_request, *, media_stop_config=None):
        policy = load_config()["media_stop"] if media_stop_config is None else media_stop_config
        self.stop_pattern = validate_media_stop(policy)
        self.stop_enabled = policy["enabled"]
        self.log = log
        self.lock = threading.Lock()
        self.instance = str(uuid.uuid4())
        self.gate = WakeGate(aliases, continuous=True)
        self.eos_flag = eos_flag
        self.recording_speech = set()
        self.mode = "wake"
        self.recording_id = None
        self.conn = None
        self.epoch = None
        self.armed_after = float('inf')
        self.delivered = False
        self.observed = {}
        self.arm_text = {}
        self.stop_sentences = OrderedDict()
        self.media_stop = MediaStopWorker(self.event, stop_request)

    def close(self):
        self.media_stop.close()

    def arm(self, conn, mode, recording_id, boundary):
        """Called under lock after the client has finished its recording cue."""
        self.mode, self.recording_id = mode, recording_id
        self.conn, self.epoch = conn, str(uuid.uuid4())
        self.armed_after = boundary
        self.arm_text = dict(self.observed)
        self.delivered = False
        self.recording_speech = set()
        self.event(event="armed", mode=mode, boundary=boundary)

    def event(self, **data):
        logging.getLogger("wake-status").info("%s", " ".join(f"{k}={v}" for k, v in data.items()))

    def result(self, timestamp, flag, text):
        try:
            text = text.decode("utf-8", errors="replace")
            with self.lock:
                self.log.info("%s", text)
                norm = normalize(text)
                if norm:
                    self.observed[timestamp] = norm
                if len(self.observed) > 1024:
                    del self.observed[next(iter(self.observed))]
                # Device-wide control is independent of subscription/arming and
                # EOS. Consume the whole segment, including later revisions and
                # flag-only EOS, so this command never becomes a Hermes event.
                if timestamp in self.stop_sentences:
                    return
                if self.stop_enabled and self.stop_pattern.fullmatch(text.strip()):
                    self.stop_sentences[timestamp] = None
                    if len(self.stop_sentences) > 1024:
                        self.stop_sentences.popitem(last=False)
                    self.recording_speech.discard(timestamp)
                    self.event(event="media_stop_matched", timestamp=timestamp)
                    self.media_stop.submit(timestamp)
                    return
                if self.conn is None or self.delivered:
                    return
                eligible = text
                if timestamp < self.armed_after:
                    # Recording must never stop from a pre-beep segment. For wake,
                    # only a newly appended phrase in an already observed segment
                    # can cross the arming boundary; never replay its old prefix.
                    before = self.arm_text.get(timestamp)
                    if self.mode != "wake" or before is None or not norm.startswith(before):
                        self.event(event="ignored_old_segment", mode=self.mode, timestamp=timestamp)
                        return
                    eligible = norm[len(before):].lstrip()
                if self.mode == "recording":
                    if norm:
                        self.recording_speech.add(timestamp)
                    if not (flag & self.eos_flag) or timestamp not in self.recording_speech:
                        return
                    action, alias = "trigger", None
                else:
                    action, alias = self.gate.match(timestamp, eligible)
                if action == "trigger":
                    try:
                        self.conn.sendall((json.dumps({"event": "end_recording" if self.mode == "recording" else "wake",
                            "alias": alias, "timestamp": timestamp, "recording_id": self.recording_id,
                            "reason": "aisp_eos" if self.mode == "recording" else "wake_phrase"})+"\n").encode())
                    except OSError:
                        self.event(event="delivery_failed")
                    else:
                        if self.mode == "wake":
                            self.gate.commit(timestamp)
                        self.delivered = True
                        self.event(event="delivered", mode=self.mode, timestamp=timestamp, recording_id=self.recording_id)
        except Exception:
            self.log.exception("ASR callback failure")

    def serve(self, native, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(1)
        listener.settimeout(1)
        self.event(event="ready")
        while True:
            if not native.capture_alive():
                raise RuntimeError("Continuous audio source stopped")
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            conn.settimeout(2)
            try:
                line = b""
                while not line.endswith(b"\n") and len(line) < 8192:
                    part = conn.recv(1)
                    if not part:
                        raise ConnectionError("incomplete handshake")
                    line += part
                request = json.loads(line)
                mode = request.get("mode", "wake")
                if mode not in ("wake", "recording"):
                    raise ValueError("Unknown subscription mode")
                recording_id = request.get("recording_id")
                if mode == "recording" and (not isinstance(recording_id, str) or not 1 <= len(recording_id) <= 128):
                    raise ValueError("Recording subscription requires identity")
                if mode == "recording" and request.get("endpoint") != "aisp-eos":
                    raise ValueError("Recording requires aisp-eos endpoint support")
                with self.lock:
                    # The service configuration owns aliases; legacy client lists cannot override it.
                    self.arm(conn, mode, recording_id, time.monotonic()*1000)
                    self.event(event="notifications_armed")
                    conn.sendall((json.dumps({"event":"ready", "mode":mode, "recording_id":recording_id,
                        "endpoint": "aisp-eos" if mode == "recording" else None})+"\n").encode())
                while native.capture_alive():
                    try:
                        data = conn.recv(1)  # Subscription only; SDK owns capture.
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    raise ValueError("Wake subscription does not accept audio")
            except (OSError, ValueError, KeyError, RuntimeError):
                self.event(event="connection_end")
            finally:
                with self.lock:
                    self.conn = None
                conn.close()
                self.event(event="notifications_disarmed_capture_continues")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--library", default="/opt/aisp-wake/libwake.so")
    p.add_argument("--model", default="/models/qwen3-asr")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="Wake service YAML configuration")
    p.add_argument("--device", default="GPU")
    p.add_argument("--socket", default="/ipc/wake/service.sock")
    p.add_argument("--log", default="/ipc/wake/partials.log")
    p.add_argument("--source", default="CAPTURE_DEVICE")
    args = p.parse_args()
    config = load_config(args.config)
    os.umask(0o077)
    Path(args.socket).unlink(missing_ok=True)  # No stale readiness across restart.
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("aisp-wake")
    log.setLevel(logging.INFO)
    log.addHandler(RotatingFileHandler(args.log, maxBytes=5*1024*1024, backupCount=4, encoding="utf-8"))
    status = logging.getLogger("wake-status")
    status.setLevel(logging.INFO)
    handler = RotatingFileHandler(str(Path(args.log).with_name("events.log")), maxBytes=2*1024*1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    status.addHandler(handler)
    s = Server(log, config["wake"]["aliases"], Native.eos_flag(args.library),
               media_stop_config=config["media_stop"])
    try:
        native = Native(args.library, args.model, args.device, s.result, config=config)
        start_capture_with_aec(native, args.source, config["audio"], s.event)
        s.serve(native, args.socket)
    finally:
        s.close()


if __name__ == "__main__":
    main()
