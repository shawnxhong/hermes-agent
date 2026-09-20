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
import re
import socket
import threading
import tempfile
import time
import unicodedata
import uuid


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join("".join(c for c in text if not unicodedata.category(c).startswith("P")).split())


def english_config(generation):
    result = dict(generation)
    # Bundled GenAI ignores JSON language/task, and AISP does not forward the
    # language property to Whisper.generate. Restrict actual language-token
    # candidates, which GenAI DOES load and use, rather than filtering text.
    en = generation["lang_to_id"]["<|en|>"]
    result.update(language="<|en|>", task="transcribe", lang_to_id={"<|en|>": en})
    return result


class WakeGate:
    def __init__(self, aliases, continuous=False):
        self.aliases = [(a, normalize(a)) for a in aliases if isinstance(a, str) and normalize(a)]
        if not self.aliases:
            raise ValueError("Nonempty aliases required")
        self.triggered = set()
        self.active = True
        self.continuous = continuous
        self.recent = deque()

    def result(self, timestamp, text):
        if timestamp in self.triggered:
            return "same_sentence", None
        if not self.active:
            return "inactive", None
        norm = normalize(text)
        for alias, needle in self.aliases:
            if re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", norm):
                self.triggered.add(timestamp)
                self.recent.append(timestamp)
                if len(self.recent) > 1024:
                    self.triggered.discard(self.recent.popleft())
                self.active = self.continuous
                return "trigger", alias
        return "miss", None


class Native:
    def __init__(self, library, model, device, callback):
        self.lib = C.CDLL(library)
        self.cb = C.CFUNCTYPE(None, C.c_int64, C.c_uint32, C.c_char_p)(callback)
        self.lib.wake_create.argtypes = [C.c_char_p, C.c_char_p, type(self.cb)]
        self.lib.wake_create.restype = C.c_void_p
        self.lib.wake_feed.argtypes = [C.c_void_p, C.POINTER(C.c_float), C.c_size_t, C.c_int64]
        self.lib.wake_flush.argtypes = [C.c_void_p]
        self.handle = self.lib.wake_create(model.encode(), device.encode(), self.cb)
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

    def start_capture(self, source):
        self.lib.wake_start_capture.argtypes = [C.c_void_p, C.c_char_p]
        self.lib.wake_capture_alive.argtypes = [C.c_void_p]
        if self.lib.wake_start_capture(self.handle, source.encode()):
            raise RuntimeError("AISP microphone capture failed")

    def capture_alive(self):
        return bool(self.lib.wake_capture_alive(self.handle))


class Server:
    def __init__(self, log, aliases):
        self.log = log
        self.lock = threading.Lock()
        self.instance = str(uuid.uuid4())
        self.gate = WakeGate(aliases, continuous=True)
        self.conn = None
        self.epoch = None
        self.armed_after = float('inf')
        self.delivered = False

    def event(self, **data):
        logging.getLogger("wake-status").info("%s", data.get("event", "status"))

    def result(self, timestamp, flag, text):
        try:
            text = text.decode("utf-8", errors="replace")
            with self.lock:
                action, alias = self.gate.result(timestamp, text) if self.gate else ("inactive", None)
                self.log.info("%s", text)
                if (action == "trigger" and self.conn is not None and not self.delivered
                        and timestamp >= self.armed_after):
                    self.delivered = True
                    try:
                        self.conn.sendall((json.dumps({"event": "wake", "alias": alias, "timestamp": timestamp})+"\n").encode())
                    except OSError:
                        self.event(event="delivery_failed")
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
        self.event(event="ready", language="en")
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
                aliases = WakeGate(json.loads(line)["aliases"]).aliases
                with self.lock:
                    self.gate.aliases = aliases
                    self.conn, self.epoch = conn, str(uuid.uuid4())
                    self.armed_after = time.monotonic()*1000
                    self.delivered = False
                    self.event(event="notifications_armed")
                conn.sendall(b'{"event":"ready"}\n')
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
    p.add_argument("--model", default="/models/whisper")
    p.add_argument("--device", default="GPU")
    p.add_argument("--socket", default="/ipc/wake/service.sock")
    p.add_argument("--log", default="/ipc/wake/partials.log")
    p.add_argument("--aliases", required=True, help="JSON file containing configured phrase and aliases")
    p.add_argument("--source", default="CAPTURE_DEVICE")
    args = p.parse_args()
    os.umask(0o077)
    Path(args.socket).unlink(missing_ok=True)  # No stale readiness across restart.
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("aisp-wake")
    log.setLevel(logging.INFO)
    log.addHandler(RotatingFileHandler(args.log, maxBytes=5*1024*1024, backupCount=4, encoding="utf-8"))
    s = Server(log, json.loads(Path(args.aliases).read_text()))
    # Supplied Whisper implementation does not forward m_language into its
    # generation config. Pin that config too, without changing shared models.
    with tempfile.TemporaryDirectory(prefix="whisper-en-") as view:
        source = Path(args.model).resolve()
        for entry in source.iterdir():
            if entry.name != "generation_config.json":
                (Path(view) / entry.name).symlink_to(entry)
        generation = json.loads((source / "generation_config.json").read_text())
        generation = english_config(generation)
        (Path(view) / "generation_config.json").write_text(json.dumps(generation))
        native = Native(args.library, view, args.device, s.result)
        native.start_capture(args.source)
        s.serve(native, args.socket)


if __name__ == "__main__":
    main()
