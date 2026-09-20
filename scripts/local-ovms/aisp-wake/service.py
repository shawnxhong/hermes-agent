"""Local-only live-ASR wake service. Log every partial, trigger once per timestamp."""
import argparse
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
    def __init__(self, aliases):
        self.aliases = [(a, normalize(a)) for a in aliases if isinstance(a, str) and normalize(a)]
        if not self.aliases:
            raise ValueError("Nonempty aliases required")
        self.triggered = set()
        self.active = True

    def result(self, timestamp, text):
        if timestamp in self.triggered:
            return "same_sentence", None
        if not self.active:
            return "inactive", None
        norm = normalize(text)
        for alias, needle in self.aliases:
            if re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", norm):
                self.triggered.add(timestamp)
                self.active = False
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


class Server:
    def __init__(self, log):
        self.log = log
        self.lock = threading.Lock()
        self.instance = str(uuid.uuid4())
        self.gate = None
        self.conn = None
        self.epoch = None

    def event(self, **data):
        self.log.info(json.dumps({"received_at": time.time(), "instance": self.instance,
                                  "listen_epoch": self.epoch, **data}, ensure_ascii=True))

    def result(self, timestamp, flag, text):
        try:
            text = text.decode("utf-8", errors="replace")
            with self.lock:
                action, alias = self.gate.result(timestamp, text) if self.gate else ("inactive", None)
                self.event(event="asr", timestamp=timestamp, flag=flag, final=bool(flag & 2), text=text, action=action, alias=alias)
                if action == "trigger" and self.conn is not None:
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
        self.event(event="ready", language="en")
        while True:
            conn, _ = listener.accept()
            conn.settimeout(2)
            try:
                line = b""
                while not line.endswith(b"\n") and len(line) < 8192:
                    part = conn.recv(1)
                    if not part:
                        raise ConnectionError("incomplete handshake")
                    line += part
                gate = WakeGate(json.loads(line)["aliases"])
                with self.lock:
                    self.gate, self.conn, self.epoch = gate, conn, str(uuid.uuid4())
                    self.event(event="armed", aliases=[a for a, _ in gate.aliases])
                conn.sendall(b'{"event":"ready"}\n')
                pending = b""
                timestamp = int(time.time()*1000)
                while gate.active:
                    data = conn.recv(16000-len(pending))
                    if not data:
                        break
                    pending += data
                    if len(pending) == 16000:
                        native.feed(pending, timestamp)
                        timestamp += 500
                        pending = b""
            except (OSError, ValueError, KeyError, RuntimeError):
                self.event(event="connection_end")
            finally:
                with self.lock:
                    if self.gate:
                        self.gate.active = False
                    self.conn = None
                conn.close()
                # Drain old callbacks before accepting a new listening epoch.
                # Flush resets the SDK VAD state while retaining loaded models.
                native.flush()
                self.event(event="paused")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--library", default="/opt/aisp-wake/libwake.so")
    p.add_argument("--model", default="/models/whisper")
    p.add_argument("--device", default="GPU")
    p.add_argument("--socket", default="/ipc/wake/service.sock")
    p.add_argument("--log", default="/ipc/wake/partials.jsonl")
    args = p.parse_args()
    os.umask(0o077)
    Path(args.socket).unlink(missing_ok=True)  # No stale readiness across restart.
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("aisp-wake")
    log.setLevel(logging.INFO)
    log.addHandler(RotatingFileHandler(args.log, maxBytes=5*1024*1024, backupCount=4))
    s = Server(log)
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
        s.serve(native, args.socket)


if __name__ == "__main__":
    main()
