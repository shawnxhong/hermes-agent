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
        self.capture_source = "CAPTURE_DEVICE"
        self.capture_audio = config["audio"]
        self.capture_event = lambda **kw: None
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

    def initialize_capture(self):
        return start_capture_with_aec(self, self.capture_source, self.capture_audio, self.capture_event)

    def pause_capture(self):
        self.lib.wake_pause_capture.argtypes = [C.c_void_p]
        if self.lib.wake_pause_capture(self.handle):
            raise RuntimeError("AISP capture pause failed")

    def resume_capture(self):
        self.lib.wake_resume_capture.argtypes = [C.c_void_p]
        if self.lib.wake_resume_capture(self.handle):
            raise RuntimeError("AISP capture resume failed")

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
        self.recording_eos_guard_until = 0.0
        self.ignored_recording_eos = set()
        self.mode = "wake"
        self.recording_id = None
        self.end_phrase_source = "asr"
        self.conn = None
        self.epoch = None
        self.armed_after = float('inf')
        self.delivered = False
        self.observed = {}
        self.arm_text = {}
        self.wake_candidates = deque(maxlen=64)
        self.candidate_seen = set()
        self.wake_handoff = None
        self.split_task = None
        self.split_requests = {}
        self.capture_paused = False
        self.last_closed_segment = -1
        self.open_segments = set()
        self.segment_floor = None

    def force_segment(self, native, request):
        """One SDK flush at a time; never wait under the callback lock."""
        key = request.get("request_id")
        if (request.get("instance") != self.instance or not isinstance(key, str)
                or not 1 <= len(key) <= 128):
            raise ValueError("Invalid force-segment identity")
        with self.lock:
            task = self.split_requests.get(key)
            if task is not None:
                if task["request"] != request:
                    raise ValueError("Force-segment request ID reused")
                return task
            if self.split_task and not self.split_task["done"].is_set():
                raise RuntimeError("Previous force segment still pending")
            task = {"request": dict(request), "done": threading.Event(),
                    "events": [], "old": set(self.observed),
                    "open": set(self.open_segments), "error": None,
                    "floor": None}
            self.split_task = task
            self.split_requests[key] = task
            # Bound dedup history, retaining the currently running task.
            if len(self.split_requests) > 256:
                del self.split_requests[next(iter(self.split_requests))]
            self.wake_handoff = None
            self.wake_candidates.clear()
        def run():
            started = time.monotonic()
            self.event(event="force_segment_started", reason=request["reason"], request_id=key)
            try:
                if request["reason"] == "recording_prepare":
                    self.capture_paused = True
                    native.pause_capture()
                    self.event(event="recording_capture_paused", request_id=key)
                native.flush()
                with self.lock:
                    rows = task["events"]
                    closed = {ts for ts, flag, _ in rows if flag & self.eos_flag}
                    if task["open"] - closed:
                        raise RuntimeError("Flush did not close the active ASR segment")
                    last_eos = max((i for i, row in enumerate(rows)
                                    if row[1] & self.eos_flag), default=-1)
                    old = task["old"] | {row[0] for row in rows[:last_eos+1]}
                    if request["reason"] == "recording_prepare":
                        # pause is a submission barrier; wait drained all old input.
                        old.update(self.observed)
                        old.update(self.open_segments)
                        self.open_segments.clear()
                    task["floor"] = max(old, default=-1)
                    task["fresh"] = rows[last_eos+1:]
                    task["awaiting_arm"] = request["reason"] == "scene_ready"
            except Exception as exc:
                task["error"] = str(exc)
                self.event(event="force_segment_failed", request_id=key)
            finally:
                with self.lock:
                    task["done"].set()
                self.event(event="force_segment_finished", request_id=key,
                           seconds=round(time.monotonic()-started, 3), success=task["error"] is None)
        threading.Thread(target=run, daemon=True, name="wake-force-segment").start()
        return task

    def resume_recording_capture(self, native):
        if self.capture_paused:
            started = time.monotonic()
            native.resume_capture()
            if not native.capture_alive():
                raise RuntimeError("Capture failed to resume")
            self.capture_paused = False
            self.event(event="recording_capture_resumed", seconds=round(time.monotonic()-started, 3))

    def wait_segment(self, task):
        if task is None:
            return
        if not task["done"].wait(5):
            self.event(event="force_segment_pending", request_id=task["request"]["request_id"])
            raise RuntimeError("Force segment still pending")
        if task["error"]:
            raise RuntimeError(task["error"])

    def recording_segment(self, native, request):
        with self.lock:
            if (request.get("reason") != "end_phrase" or not self.end_phrase["enabled"]
                    or self.mode != "recording" or request.get("recording_id") != self.recording_id
                    or self.delivered):
                raise ValueError("Stale or disabled recording force segment")
            self.delivered = True  # The recorder has already finished locally.
        return self.force_segment(native, request)

    def arm(self, conn, mode, recording_id, boundary, end_phrase_source="asr"):
        """Called under lock after the client has finished its recording cue."""
        self.mode, self.recording_id = mode, recording_id
        self.end_phrase_source = end_phrase_source
        self.conn, self.epoch = conn, str(uuid.uuid4())
        self.armed_after = boundary
        self.segment_floor = None
        self.arm_text = dict(self.observed)
        self.delivered = False
        self.recording_speech = set()
        self.recording_eos_guard_until = 0.0
        self.ignored_recording_eos = set()
        self.wake_handoff = None
        self.event(event="armed", mode=mode, boundary=boundary, end_phrase_source=end_phrase_source)

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
        candidate = {"timestamp": timestamp, "alias": alias, "received_ms": now}
        if self.conn is None or self.delivered:
            self.wake_candidates.append(candidate)
            self.event(event="wake_candidate_cached", timestamp=timestamp)
        return candidate

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
        # The one-second lookback is unchanged. Include the entire post-playback
        # subscription gap, then retain this reply scope for bounded ASR decode
        # completion. A new partial may finish a phrase that began before rearm.
        self.wake_handoff = {"start_ms": start, "end_ms": end,
            "deadline_ms": end + 5000,
            "echo": normalize(str(request.get("echo_text", ""))[-1024:]),
            "turn": str(request.get("turn", ""))[:64]}
        self.event(event="wake_handoff_armed", lookback_ms=end-start,
                   gap_ms=self.armed_after-end, decode_deadline_ms=end+5000)
        for candidate in reversed(self.wake_candidates):
            if start <= candidate["received_ms"] <= self.armed_after:
                if self.deliver_handoff(candidate):
                    return

    def handoff_active(self, received_ms):
        state = self.wake_handoff
        return (self.mode == "wake" and state is not None
                and state["start_ms"] <= received_ms <= state["deadline_ms"])

    def handoff_echo(self, alias):
        return bool(re.search(r"(?<!\w)" + re.escape(normalize(alias)) + r"(?!\w)",
                              self.wake_handoff["echo"]))

    def deliver_handoff(self, candidate):
        """Only a first-observed full keyword can cross the SDK segment guard."""
        timestamp, alias = candidate["timestamp"], candidate["alias"]
        if (not self.handoff_active(candidate["received_ms"])
                or self.gate.match(timestamp, alias)[0] != "trigger"):
            return False
        if self.handoff_echo(alias):
            # Keep this rejected echo segment suppressed after the handoff
            # expires; a genuinely new SDK segment can still wake normally.
            self.gate.commit(timestamp)
            self.event(event="wake_buffer_echo_ignored", timestamp=timestamp)
            return False
        try:
            self.conn.sendall((json.dumps({"event": "wake", "alias": alias,
                "timestamp": timestamp, "recording_id": None, "reason": "wake_phrase",
                "buffered": True, "turn": self.wake_handoff["turn"]}) + "\n").encode())
        except OSError:
            self.event(event="delivery_failed")
            return False
        self.gate.commit(timestamp)
        self.delivered = True
        self.event(event="delivered", mode="wake", timestamp=timestamp, buffered=True,
                   decode_after_tts_ms=candidate["received_ms"]-self.wake_handoff["end_ms"])
        return True

    def result(self, timestamp, flag, text):
        received_at = time.monotonic()
        try:
            text = text.decode("utf-8", errors="replace")
            with self.lock:
                self.log.info("%s", text)
                if flag & self.eos_flag:
                    self.last_closed_segment = max(self.last_closed_segment, timestamp)
                    self.open_segments.discard(timestamp)
                elif text.strip():
                    self.open_segments.add(timestamp)
                task = self.split_task
                if task is not None and task.get("awaiting_arm") and task["done"].is_set():
                    if len(task["fresh"]) < 2048:
                        task["fresh"].append((timestamp, flag, text))
                    else:
                        task["error"] = "Too many callbacks awaiting rearm"
                    return
                if task is not None and not task["done"].is_set():
                    if len(task["events"]) >= 2048:
                        task["error"] = "Too many callbacks during force segment"
                    else:
                        task["events"].append((timestamp, flag, text))
                    # Keep observations for the next arm, but send no endpoint/wake.
                    if text.strip():
                        self.observed[timestamp] = normalize(text)
                    return
                norm = normalize(text)
                if norm:
                    self.observed[timestamp] = norm
                candidate = self.cache_wake(timestamp, text)
                if self.mode == "recording" and self.matches_end_phrase(norm):
                    self.event(event="end_phrase_partial", timestamp=timestamp,
                               recording_id=self.recording_id, source=self.end_phrase_source,
                               old_segment=timestamp < self.armed_after)
                if len(self.observed) > 1024:
                    oldest = next(iter(self.observed))
                    del self.observed[oldest]
                    self.candidate_seen.discard(oldest)
                if self.conn is None or self.delivered:
                    return
                # Wake-only handoff handles first recognition across rearm; native
                # recording EOS/end-phrase segmentation below stays unchanged.
                if candidate is not None and self.handoff_active(candidate["received_ms"]):
                    self.deliver_handoff(candidate)
                    return
                eligible = text
                if self.segment_floor is not None:
                    if timestamp <= self.segment_floor:
                        self.event(event="ignored_pre_split_segment", timestamp=timestamp)
                        return
                elif timestamp < self.armed_after:
                    # Native EOS still cannot finish a pre-cue segment. A wake
                    # or explicit end phrase may use only its newly appended
                    # suffix, never the text already observed when armed.
                    before = self.arm_text.get(timestamp)
                    if before is None or not norm.startswith(before):
                        self.event(event="ignored_old_segment", mode=self.mode, timestamp=timestamp)
                        return
                    eligible = norm[len(before):].lstrip()
                    if self.mode == "recording" and not (self.end_phrase_source == "asr" and self.matches_end_phrase(eligible) and self.matches_end_phrase(norm)):
                        self.event(event="ignored_old_segment", mode=self.mode, timestamp=timestamp)
                        return
                if self.mode == "recording":
                    if self.end_phrase_source == "asr" and self.matches_end_phrase(eligible):
                        action, alias, reason = "trigger", self.end_phrase["phrase"], "end_phrase"
                    else:
                        if norm:
                            self.recording_speech.add(timestamp)
                        if not (flag & self.eos_flag) or timestamp not in self.recording_speech:
                            return
                        if timestamp in self.ignored_recording_eos:
                            return
                        if received_at < self.recording_eos_guard_until:
                            self.ignored_recording_eos.add(timestamp)
                            self.event(event="recording_eos_guard_ignored", recording_id=self.recording_id,
                                       timestamp=timestamp,
                                       remaining_seconds=round(self.recording_eos_guard_until-received_at, 3))
                            return
                        action, alias, reason = "trigger", None, "aisp_eos"
                else:
                    action, alias = self.gate.match(timestamp, eligible)
                    if (action == "trigger" and self.handoff_active(time.monotonic()*1000)
                            and self.handoff_echo(alias)):
                        self.gate.commit(timestamp)
                        self.event(event="wake_buffer_echo_ignored", timestamp=timestamp)
                        return
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
        # Derived public metadata, not another configuration source. It lets
        # the recorder warm its already-bundled keyword model before the cue.
        metadata = path.with_name("endpoint-config.json")
        pending = path.with_name(".endpoint-config-" + self.instance)
        pending.write_text(json.dumps({"instance": self.instance,
            "recording_end_phrase_version": 2, "force_segment_version": 1, "recording_prepare_version": 3, "end_phrase": self.end_phrase}) + "\n")
        pending.chmod(0o600)
        pending.replace(metadata)
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
                if mode == "resume_recording":
                    task = self.split_requests.get(request.get("request_id"))
                    if (request.get("instance") != self.instance or task is None
                            or task is not self.split_task or task.get("consumed")
                            or task["request"]["reason"] != "recording_prepare"):
                        raise ValueError("Expired capture preparation")
                    self.wait_segment(task)
                    self.resume_recording_capture(native)
                    task["capture_resumed"] = True
                    conn.sendall((json.dumps({"event": "recording_capture_ready",
                        "instance": self.instance, "request_id": request["request_id"]}) + "\n").encode())
                    continue
                if mode == "prepare_recording":
                    self.wait_segment(self.split_task)
                    split = request["force_segment"]
                    if split.get("reason") != "recording_prepare":
                        raise ValueError("Invalid recording preparation")
                    task = self.force_segment(native, split)
                    self.wait_segment(task)
                    conn.sendall((json.dumps({"event": "recording_prepared",
                        "instance": self.instance, "request_id": split["request_id"]}) + "\n").encode())
                    self.event(event="recording_prepared", request_id=split["request_id"])
                    continue
                if mode not in ("wake", "recording"):
                    raise ValueError("Unknown subscription mode")
                recording_id = request.get("recording_id")
                if mode == "recording" and (not isinstance(recording_id, str) or not 1 <= len(recording_id) <= 128):
                    raise ValueError("Recording subscription requires identity")
                if mode == "recording" and request.get("endpoint") != "aisp-eos":
                    raise ValueError("Recording requires aisp-eos endpoint support")
                end_phrase_source = request.get("end_phrase_source", "asr")
                if end_phrase_source not in ("asr", "recorder"):
                    raise ValueError("Unknown end phrase source")
                if (mode == "recording" and end_phrase_source == "recorder"
                        and request.get("instance") != self.instance):
                    raise ValueError("Recording endpoint metadata is stale")
                if (mode == "wake" and self.split_task is not None
                        and self.split_task["done"].is_set() and self.split_task.get("error")
                        and self.split_task["request"]["reason"] == "recording_prepare"):
                    self.resume_recording_capture(native)
                    self.event(event="recording_prepare_abandoned", reason=self.split_task["error"])
                    self.split_task = None
                self.wait_segment(self.split_task)
                if mode == "wake":
                    self.resume_recording_capture(native)
                elif self.capture_paused:
                    raise RuntimeError("Capture must resume after the cue and before recording")
                split = request.get("force_segment")
                task = None
                if split is not None:
                    if mode != "wake" or split.get("reason") != "scene_ready":
                        raise ValueError("Invalid scene force segment")
                    task = self.force_segment(native, split)
                    self.wait_segment(task)
                prepared = request.get("recording_prepare")
                if prepared is not None:
                    if mode != "recording" or request.get("instance") != self.instance:
                        raise ValueError("Invalid recording preparation identity")
                    task = self.split_requests.get(prepared)
                    if (task is None or task is not self.split_task or task.get("consumed")
                            or task["request"]["reason"] != "recording_prepare"
                            or not task.get("capture_resumed")):
                        raise ValueError("Recording preparation expired")
                    self.wait_segment(task)
                guard_until = 0.0
                if mode == "recording" and "recording_started_at" in request:
                    started = request["recording_started_at"]
                    seconds = request.get("eos_guard_seconds", 1.0)
                    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
                           for v in (started, seconds)):
                        raise ValueError("Invalid recording EOS guard")
                    guard_until = started + seconds
                with self.lock:
                    # The service configuration owns aliases; legacy client lists cannot override it.
                    self.arm(conn, mode, recording_id, time.monotonic()*1000, end_phrase_source)
                    self.recording_eos_guard_until = guard_until
                    if guard_until:
                        self.event(event="recording_eos_guard_armed", recording_id=recording_id,
                                   seconds=seconds, remaining_seconds=round(guard_until-time.monotonic(), 3))
                    if self.split_task is not None:
                        self.split_task["awaiting_arm"] = False
                    if task is not None:
                        self.segment_floor = task["floor"]
                        if prepared is not None:
                            self.segment_floor = max(self.segment_floor, self.last_closed_segment)
                            task["consumed"] = True
                            self.event(event="recording_boundary_committed", request_id=prepared,
                                       recording_id=recording_id, floor=self.segment_floor)
                        task["awaiting_arm"] = False
                    self.event(event="notifications_armed")
                    conn.sendall((json.dumps({"event":"ready", "mode":mode, "recording_id":recording_id,
                        "wake_replay_version": 2, "force_segment_version": 1, "instance": self.instance,
                        "recording_prepare": prepared,
                        "recording_end_phrase_version": 2, "end_phrase_source": end_phrase_source,
                        "endpoint": "aisp-eos" if mode == "recording" else None,
                        "end_phrase": self.end_phrase if mode == "recording" else None})+"\n").encode())
                    if task is None:
                        self.replay_wake(request.get("replay"))
                if task is not None and mode == "wake":
                    for timestamp, flag, text in task.get("fresh", []):
                        self.result(timestamp, flag, text.encode())
                command = bytearray()
                while native.capture_alive():
                    try:
                        data = conn.recv(4096)  # Subscription only; SDK owns capture.
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    command.extend(data)
                    if len(command) > 4096:
                        raise ValueError("Control request too large")
                    if b"\n" in command:
                        message = json.loads(command)
                        if message.get("command") != "force_segment":
                            raise ValueError("Unknown subscription command")
                        self.recording_segment(native, message)
                        command.clear()
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
    native.capture_source = args.source
    native.capture_event = s.event
    native.initialize_capture()
    s.serve(native, args.socket)


if __name__ == "__main__":
    main()
