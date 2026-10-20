"""Local-only live-ASR wake service. Log every partial, trigger once per timestamp."""
import argparse
from collections import deque
import array
import ctypes as C
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import math
import re
import socket
import threading
import time
import unicodedata
import uuid

import yaml

from tools.wake_audio import start_capture_with_aec
from tools.recording_quiet import validate_config as validate_quiet


DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")
SPLIT_SENSITIVITY = {"low": 0, "medium": 1, "high": 2}


def validate_end_phrase(raw):
    if (not isinstance(raw, dict) or set(raw) != {"enabled", "phrase"}
            or type(raw["enabled"]) is not bool
            or not isinstance(raw["phrase"], str) or not normalize(raw["phrase"])
            or any(c in raw["phrase"] for c in "\r\n")):
        raise ValueError("end_phrase requires boolean enabled and a nonempty single-line phrase")
    return dict(raw)


def load_config(path=DEFAULT_CONFIG):
    """Load the single wake-service vocabulary source; fail on invalid input."""
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    required = {"asr", "wake", "vad", "audio", "recording_quiet"}
    if not isinstance(config, dict) or not required <= set(config) or set(config) - required - {"end_phrase"}:
        raise ValueError("Wake config requires asr, wake, vad, audio, recording_quiet; optional end_phrase")
    config["end_phrase"] = validate_end_phrase(config.get("end_phrase", {"enabled": True, "phrase": "thank you"}))
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
    validate_quiet(config['recording_quiet'])
    vad = config['vad']
    required = {'threshold', 'block_rms_threshold'}
    if not isinstance(vad, dict) or not required <= set(vad) or set(vad) - required - {'split_sensitivity'}:
        raise ValueError("vad requires threshold and block_rms_threshold; split_sensitivity is optional")
    sensitivity = vad.setdefault('split_sensitivity', 'medium')
    if not isinstance(sensitivity, str) or sensitivity not in SPLIT_SENSITIVITY:
        raise ValueError("vad.split_sensitivity must be low, medium or high")
    for key in required:
        value = vad[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError(f"vad.{key} must be a finite number between 0 and 1")
    return config


def asr_context(config=None):
    """Convert configured hotwords to the SDK's one-phrase-per-line context."""
    config = load_config() if config is None else config
    return "\n".join(config["asr"]["hotwords"])


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
        self.lib.wake_create_with_split.argtypes = [C.c_char_p, C.c_char_p, C.c_char_p, C.c_char_p, C.c_float, C.c_float, C.c_int32, type(self.cb)]
        self.lib.wake_create_with_split.restype = C.c_void_p
        self.lib.wake_feed.argtypes = [C.c_void_p, C.POINTER(C.c_float), C.c_size_t, C.c_int64]
        self.lib.wake_flush.argtypes = [C.c_void_p]
        self.handle = self.lib.wake_create_with_split(model.encode(), device.encode(), context.encode(), config["asr"]["source_language"].encode(),
                                            config["vad"]["threshold"], config["vad"]["block_rms_threshold"],
                                            SPLIT_SENSITIVITY[config["vad"].get("split_sensitivity", "medium")], self.cb)
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
    def __init__(self, log, aliases, eos_flag, end_phrase=None):
        self.log = log
        self.lock = threading.Lock()
        self.instance = str(uuid.uuid4())
        self.gate = WakeGate(aliases, continuous=True)
        self.eos_flag = eos_flag
        self.end_phrase = validate_end_phrase(end_phrase if end_phrase is not None else {"enabled": True, "phrase": "thank you"})
        self.end_pattern = re.compile(r"(?<!\w)" + re.escape(normalize(self.end_phrase["phrase"])) + r"(?!\w)")
        self.recording_speech = set()
        self.mode = "wake"
        self.recording_id = None
        self.conn = None
        self.epoch = None
        self.armed_after = float('inf')
        self.delivered = False
        self.observed = {}
        self.arm_text = {}
        self.wake_candidates = deque(maxlen=64)
        self.candidate_seen = set()

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

    def cache_wake(self, timestamp, text):
        now = time.monotonic() * 1000
        while self.wake_candidates and now - self.wake_candidates[0]["received_ms"] > 5000:
            self.wake_candidates.popleft()
        action, alias = self.gate.match(timestamp, text)
        if action != "trigger" or timestamp in self.candidate_seen:
            return
        self.candidate_seen.add(timestamp)
        # Never refresh a keyword from an already observed sentence. Active
        # recording/wake subscriptions keep their existing semantics.
        if self.conn is None or self.delivered:
            self.wake_candidates.append({"timestamp": timestamp, "alias": alias, "received_ms": now})
            self.event(event="wake_candidate_cached", timestamp=timestamp)

    def replay_wake(self, request):
        """Under the lock, after ready; recording EOS never uses this path."""
        if self.mode != "wake" or self.delivered or not isinstance(request, dict):
            return
        start, end = request.get("start_ms"), request.get("end_ms")
        if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in (start, end))
                or not 0 < end - start <= 2000.001
                or not end <= self.armed_after <= end + 5000):
            return
        echo = normalize(str(request.get("echo_text", ""))[-1024:])
        for candidate in reversed(self.wake_candidates):
            if not start <= candidate["received_ms"] <= end:
                continue
            timestamp, alias = candidate["timestamp"], candidate["alias"]
            if self.gate.match(timestamp, alias)[0] != "trigger":
                continue
            if re.search(r"(?<!\w)" + re.escape(normalize(alias)) + r"(?!\w)", echo):
                self.event(event="wake_buffer_echo_ignored", timestamp=timestamp)
                continue
            try:
                self.conn.sendall((json.dumps({"event": "wake", "alias": alias,
                    "timestamp": timestamp, "recording_id": None, "reason": "wake_phrase",
                    "buffered": True, "turn": str(request.get("turn", ""))[:64]}) + "\n").encode())
            except OSError:
                self.event(event="delivery_failed")
                return
            self.gate.commit(timestamp)
            self.delivered = True
            self.event(event="delivered", mode="wake", timestamp=timestamp, buffered=True)
            return

    def result(self, timestamp, flag, text):
        try:
            text = text.decode("utf-8", errors="replace")
            with self.lock:
                self.log.info("%s", text)
                norm = normalize(text)
                if norm:
                    self.observed[timestamp] = norm
                self.cache_wake(timestamp, text)
                if len(self.observed) > 1024:
                    oldest = next(iter(self.observed))
                    del self.observed[oldest]
                    self.candidate_seen.discard(oldest)
                if self.conn is None or self.delivered:
                    return
                eligible = text
                if timestamp < self.armed_after:
                    # Native EOS still cannot finish a pre-cue segment. A wake
                    # or explicit end phrase may use only its newly appended
                    # suffix, never the text already observed when armed.
                    before = self.arm_text.get(timestamp)
                    if before is None or not norm.startswith(before):
                        self.event(event="ignored_old_segment", mode=self.mode, timestamp=timestamp)
                        return
                    eligible = norm[len(before):].lstrip()
                    if self.mode == "recording" and not (self.matches_end_phrase(eligible) and self.matches_end_phrase(norm)):
                        self.event(event="ignored_old_segment", mode=self.mode, timestamp=timestamp)
                        return
                if self.mode == "recording":
                    if self.matches_end_phrase(eligible):
                        action, alias, reason = "trigger", self.end_phrase["phrase"], "end_phrase"
                    else:
                        if norm:
                            self.recording_speech.add(timestamp)
                        if not (flag & self.eos_flag) or timestamp not in self.recording_speech:
                            return
                        action, alias, reason = "trigger", None, "aisp_eos"
                else:
                    action, alias = self.gate.match(timestamp, eligible)
                    reason = "wake_phrase"
                if action == "trigger":
                    try:
                        self.conn.sendall((json.dumps({"event": "end_recording" if self.mode == "recording" else "wake",
                            "alias": alias, "timestamp": timestamp, "recording_id": self.recording_id,
                            "reason": reason})+"\n").encode())
                    except OSError:
                        self.event(event="delivery_failed")
                    else:
                        if self.mode == "wake":
                            self.gate.commit(timestamp)
                        self.delivered = True
                        self.event(event="delivered", mode=self.mode, timestamp=timestamp, recording_id=self.recording_id, reason=reason)
        except Exception:
            self.log.exception("ASR callback failure")

    def matches_end_phrase(self, text):
        return self.end_phrase["enabled"] and bool(self.end_pattern.search(normalize(text)))

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
                        "endpoint": "aisp-eos" if mode == "recording" else None,
                        "end_phrase": self.end_phrase if mode == "recording" else None})+"\n").encode())
                    self.replay_wake(request.get("replay"))
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
    s = Server(log, config["wake"]["aliases"], Native.eos_flag(args.library), config["end_phrase"])
    s.event(event="vad_config", **config["vad"])
    s.event(event="end_phrase_config", enabled=config["end_phrase"]["enabled"])
    native = Native(args.library, args.model, args.device, s.result, config=config)
    start_capture_with_aec(native, args.source, config["audio"], s.event)
    s.serve(native, args.socket)


if __name__ == "__main__":
    main()
