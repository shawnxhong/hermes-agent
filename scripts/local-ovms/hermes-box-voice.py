#!/usr/bin/env python3
"""Linux button API + PTY supervisor. No model-facing tools or prompts."""
import argparse
import errno
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import pty
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import termios
import time

UNIT = "hermes-box-voice.service"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def notify(message):
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.connect(address)
        s.sendall(message.encode())


class VoiceProcess:
    """Own exactly one CLI process group; never signal the independent gateway."""
    def __init__(self, command, goodbye, env=None, ready_timeout=90, stop_timeout=12,
                 send_notify=notify, log=None):
        self.command, self.goodbye, self.env = command, goodbye, env
        self.ready_timeout, self.stop_timeout = ready_timeout, stop_timeout
        self.send_notify = send_notify
        self.log = log or logging.getLogger("box-voice")
        self.stopping = False
        self.ready = False
        self.pid = None
        self.fd = None
        self.exit_status = None
        self.buffer = ""

    def request_stop(self, *_):
        self.stopping = True

    def read_output(self, timeout=.1):
        if not select.select([self.fd], [], [], timeout)[0]:
            return
        try:
            chunk = os.read(self.fd, 65536)
        except OSError as e:
            if e.errno == errno.EIO:
                return
            raise
        if b"\x1b[6n" in chunk:
            os.write(self.fd, b"\x1b[1;1R")
        text = chunk.decode("utf-8", errors="replace")
        self.log.debug(ANSI.sub("", text))
        if not self.ready:
            self.buffer = (self.buffer + text)[-16384:]
            if "Wake word listening" in ANSI.sub("", self.buffer):
                self.ready = True
                self.send_notify("READY=1\nSTATUS=Voice ready; waiting for Hi Intel")
                self.log.info("voice_ready")

    def poll(self):
        if self.exit_status is None:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.exit_status = os.waitstatus_to_exitcode(status)
        return self.exit_status

    def stop_child(self):
        if self.poll() is None:
            try:
                os.killpg(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        until = time.monotonic() + self.stop_timeout
        while self.poll() is None and time.monotonic() < until:
            self.read_output()
        # Kill only the owned process group, including leftover audio children.
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if self.exit_status is None:
            _, status = os.waitpid(self.pid, 0)
            self.exit_status = os.waitstatus_to_exitcode(status)

    def run(self):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
                os.execvpe(self.command[0], self.command, self.env or os.environ.copy())
            finally:
                os._exit(127)
        deadline = time.monotonic() + self.ready_timeout
        try:
            while not self.stopping:
                if self.poll() is not None:
                    raise RuntimeError("Voice CLI exited unexpectedly: " + str(self.exit_status))
                self.read_output()
                if not self.ready and time.monotonic() > deadline:
                    raise RuntimeError("Wake listener did not become ready")
            self.send_notify("STOPPING=1\nSTATUS=Stopping local voice")
        finally:
            self.stop_child()
            os.close(self.fd)
        # Never announce a goodbye for startup failure or unexpected CLI exit.
        if self.ready and self.stopping:
            self.goodbye()
            self.log.info("goodbye_played")
        return 0


def run_service():
    import yaml
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    if not (cfg.get("wake_word", {}).get("enabled") and cfg.get("stt", {}).get("enabled")
            and cfg.get("voice", {}).get("auto_tts")):
        raise RuntimeError("Voice is not configured; run hermes-mode voice first")
    startup = Path(cfg.get("voice", {}).get("startup_cue_file") or "")
    goodbye = home / "cache" / "box-voice" / "goodbye.wav"
    if not startup.is_file() or not goodbye.is_file():
        raise RuntimeError("Box voice cues have not been prepared")
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / "box-voice.log", maxBytes=2_000_000, backupCount=2)
    logger = logging.getLogger("box-voice")
    logger.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    env = os.environ.copy()
    env.update(HERMES_CLI_VOICE_AUTO_START="1", HERMES_CLI_WAKE_READY_CUE="1",
               HERMES_CLI_PTT_ONESHOT="1", TERM="xterm-256color", PYTHONUNBUFFERED="1")
    # Start neutral; scene buttons explicitly select skills after startup.
    command = [str(Path.home() / ".local/bin/hermes"), "--cli"]
    controller = VoiceProcess(command,
        lambda: subprocess.run(["pw-play", str(goodbye)], check=True, timeout=15), env=env, log=logger)
    signal.signal(signal.SIGTERM, controller.request_stop)
    signal.signal(signal.SIGINT, controller.request_stop)
    try:
        return controller.run()
    except Exception:
        logger.exception("voice_failed")
        raise


def service_status():
    result = subprocess.run(["systemctl", "--user", "show", UNIT,
        "--property=ActiveState,SubState,MainPID,Result"], capture_output=True, text=True, check=True)
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    phase = {"active": "ready", "activating": "starting", "deactivating": "stopping",
             "failed": "failed", "inactive": "off"}.get(props.get("ActiveState"), "unavailable")
    return {"state": phase, "pid": int(props.get("MainPID", "0")), "result": props.get("Result", "")}


def control(action):
    if action == "status":
        return service_status()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/" + str(os.getuid())))
    with (runtime / "hermes-box-voice-control.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = service_status()["state"]
        if action == "toggle":
            action = "stop" if state in {"starting", "ready"} else "start"
        if action == "start" and state == "ready":
            return dict(service_status(), changed=False)
        if action == "stop" and state in {"off", "failed"}:
            return dict(service_status(), changed=False)
        subprocess.run(["systemctl", "--user", action, UNIT], check=True, timeout=125)
        return dict(service_status(), changed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "toggle", "status", "run"])
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "run":
        return run_service()
    try:
        if args.action == "start" and sys.stdin.isatty() and sys.stdout.isatty():
            return run_interactive()
        print(json.dumps(control(args.action)), flush=True)
        return 0
    except Exception as e:
        print(json.dumps({"state": "error", "error": str(e)}), flush=True)
        return 1


def run_interactive():
    """Terminal start enters the real CLI; button calls remain headless."""
    # Release the background microphone before opening the foreground CLI.
    control("stop")
    env = os.environ.copy()
    env.update(HERMES_CLI_VOICE_AUTO_START="1", HERMES_CLI_WAKE_READY_CUE="1",
               HERMES_CLI_PTT_ONESHOT="1")
    command = [str(Path.home() / ".local/bin/hermes"), "--cli"]
    try:
        return subprocess.run(command, env=env).returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
