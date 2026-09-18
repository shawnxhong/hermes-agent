"""Local button control for one persistent voice CLI (not the IM gateway)."""
from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import queue
import socket
import threading
import time

from hermes_constants import get_hermes_home

log = logging.getLogger(__name__)
DEFAULT_SCENES = {
    "healthcare": {"skill": "healthcare", "announcement": "Healthcare assistant ready."},
    "shopping": {"skill": "shopping", "announcement": "Shopping assistant ready."},
    "travel": {"skill": "travel-concierge", "announcement": "Travel assistant ready."},
    "home": {"skill": "demo-home-assistant", "announcement": "Home assistant ready."},
}
ACK = "One moment please"
EXIT_ACK = "Scene closed."
CLEAR_CONTEXT = '__clear_context__'
CLEAR_CLARIFY = '__clarify_clear_context__'
CLEAR_ACK = 'Conversation cleared.'
CLARIFY_ACK = "I haven't cleared anything. To clear only this conversation, say clear memory."


def switching(cli):
    return bool(getattr(cli, "_scene_switching", False))


def generation(cli):
    return getattr(cli, "_scene_generation", 0)


def socket_path():
    return get_hermes_home() / "run" / "voice-scenes.sock"


def settings():
    from hermes_cli.config import load_config
    config = load_config()
    raw = (config.get("voice") or {}).get("scenes") or {}
    return raw, config.get("tts") or {}


def prepare_audio(scenes, tts):
    from hermes_cli.voice_wake_ack import cached_audio
    texts = [ACK, EXIT_ACK, "Scene switch failed. Please try again."]
    texts.extend(item["announcement"] for item in scenes.values())
    return {text: cached_audio({"text": text, "tts": tts}) for text in texts}


class SceneController:
    """Accept off-thread; perform reset on the CLI's serialized process loop.

    Do not rotate a live agent object under its worker. Hard interruption makes
    the normal chat path unwind before apply_pending runs. Non-cancellable work
    cannot contaminate a new scene because readiness waits for that boundary.
    """

    def __init__(self, cli, scenes, audio, *, play=None):
        self.cli, self.scenes, self.audio = cli, scenes, audio
        if play is None:
            from tools.voice_mode import play_audio_file
            play = play_audio_file
        self.play = play
        self.lock = threading.RLock()
        self.audio_lock = threading.Lock()
        self.pending = None
        self.active = None
        self.error = None
        self.ack_thread = None
        self.last_press = (None, 0.0)
        self.base_prompt = None
        self.server = None
        self.lock_file = None
        self.requests = {}

    def status(self):
        with self.lock:
            return {"pid": os.getpid(), "scene": self.active,
                    "switching": switching(self.cli), "generation": generation(self.cli),
                    "pending": self.pending[1] if self.pending else None, "error": self.error}

    def request(self, name=None, *, action="switch", request_id=None):
        from agent.skill_commands import build_preloaded_skills_prompt
        if action not in {"switch", "toggle", "clear"}:
            raise ValueError("Unknown scene action: " + str(action))
        if action != "clear" and name not in self.scenes:
            raise ValueError("Unknown scene: " + str(name))
        if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128):
            raise ValueError("Invalid request ID")
        with self.lock:
            if request_id in self.requests:
                prior_action, prior_name, version = self.requests[request_id]
                if (action, name) != (prior_action, prior_name):
                    raise ValueError("Request ID already used for another action")
                return {**self.status(), "request_generation": version}
            original_name = name
            selected = self.pending[1] if self.pending is not None else self.active
            if selected in (CLEAR_CONTEXT, CLEAR_CLARIFY):
                selected = self.active  # context clear does not change the scene
            if action == "clear" or (action == "toggle" and selected == name):
                name = None
            prompt, loaded = "", []
            if name is not None:
                prompt, loaded, missing = build_preloaded_skills_prompt([self.scenes[name]["skill"]])
                if missing or not prompt or not loaded:
                    raise ValueError("Scene skill is unavailable: " + self.scenes[name]["skill"])
            now = time.monotonic()
            if action == "switch" and self.last_press[0] == name and now - self.last_press[1] < .3:
                return self.status()
            self.last_press = (name, now)
            self.cli._scene_generation = generation(self.cli) + 1
            self.cli._scene_switching = True
            self.pending = (generation(self.cli), name, prompt, loaded)
            if request_id is not None:
                self.requests[request_id] = (action, original_name, generation(self.cli))
                if len(self.requests) > 256:
                    del self.requests[next(iter(self.requests))]
            self.error = None
            self._interrupt()
            if self.ack_thread is None or not self.ack_thread.is_alive():
                self.ack_thread = threading.Thread(target=self._ack, daemon=True,
                                                   name="scene-ack")
                self.ack_thread.start()
            from hermes_cli.voice_outbox import cancel_session
            cancel_session(self.cli.session_id)
            log.info("voice_scene accepted scene=%s generation=%s", name, generation(self.cli))
            return {**self.status(), "request_generation": generation(self.cli)}

    def request_clear(self, *, clarify=False):
        """ASR control: rotate only on the serialized CLI loop, never here."""
        with self.lock:
            if getattr(self.cli, '_should_exit', False):
                return self.status()
            control = CLEAR_CLARIFY if clarify else CLEAR_CONTEXT
            if self.pending and self.pending[1] == control:
                return self.status()
            self.cli._scene_generation = generation(self.cli) + 1
            self.cli._scene_switching = True
            self.pending = (generation(self.cli), control, None, None)
            self.error = None
            self._interrupt()
            return self.status()

    def _apply_clear(self, version, *, clarify=False):
        cli = self.cli
        # A still-running worker owns the old session. Do not rotate under it.
        if getattr(cli, '_agent_running', False):
            return False
        try:
            self.mic_thread.join(timeout=3)
            if self.mic_thread.is_alive():
                raise RuntimeError('Microphone is still releasing')
            if self.ack_thread:
                self.ack_thread.join(timeout=10)
                if self.ack_thread.is_alive():
                    raise RuntimeError('Previous announcement is still active')
            with self.lock:
                if version != generation(cli):
                    return True
                from tools.voice_mode import stop_playback
                stop_playback()
                for pending_queue in (cli._pending_input, cli._interrupt_queue):
                    while True:
                        try:
                            pending_queue.get_nowait()
                        except queue.Empty:
                            break
                # Same state reset as /clear; no file/database deletion.
                if not clarify:
                    cli._clear_conversation_context()
                cli._voice_followup_resume = None
                cli._voice_continuity_ended = False
                if not clarify:
                    cli._attached_images.clear()
                cli._reset_stream_state()
                if not clarify and getattr(cli, '_app', None):
                    cli._app.output.erase_screen()
                    cli._app.output.cursor_goto(0, 0)
                    cli._app.output.flush()
                acknowledgement = CLARIFY_ACK if clarify else CLEAR_ACK
                print(acknowledgement, flush=True)
                from hermes_cli.voice_wake_ack import cached_audio
                _, tts = settings()
                self.audio[acknowledgement] = cached_audio({'text': acknowledgement, 'tts': tts})
                self._speak(acknowledgement)
                log.info('voice_context action=%s generation=%s', 'clarify' if clarify else 'clear', version)
        except Exception as exc:
            self.error = str(exc)
            log.exception('Voice context clear failed')
        finally:
            with self.lock:
                if version == generation(cli):
                    self.pending = None
                    cli._scene_switching = False
                    cli._voice_processing = False
                    cli._voice_tts_done.set()
                    cli._wake_suspended = True
        return True

    def _interrupt(self):
        from agent.interrupt_compat import request_hard_interrupt
        from hermes_cli.voice_followup import cancel_followup
        from tools import voice_mode
        cli = self.cli
        cancel_followup(cli)
        cli._voice_continuous = False
        cli._voice_recording = False
        stop = getattr(cli, "_voice_tts_stop", None)
        if stop is not None:
            stop.set()
        voice_mode.stop_thinking_sound()
        # Do not cut our own acknowledgement when a second button arrives.
        if self.ack_thread is None or not self.ack_thread.is_alive():
            voice_mode.stop_playback()
        if getattr(cli, "agent", None) is not None:
            request_hard_interrupt(cli.agent)
        cli._clear_active_overlays_for_interrupt()
        # Microphone cleanup may wait on a device driver; never delay the ack.
        def release_mic():
            try:
                if getattr(cli, "_wake_word_active", False):
                    from tools.wake_word import pause_listening
                    pause_listening(owner=cli)
                    cli._wake_suspended = True
                recorder = getattr(cli, "_voice_recorder", None)
                if recorder is not None:
                    recorder.cancel()
            except Exception:
                log.exception("Scene microphone cleanup failed")
        self.mic_thread = threading.Thread(target=release_mic, daemon=True)
        self.mic_thread.start()

    def _speak(self, text):
        with self.audio_lock:
            self.cli._voice_last_tts_text = text
            if not self.play(str(self.audio[text])):
                raise RuntimeError("Scene announcement playback failed")

    def _ack(self):
        try:
            self._speak(ACK)
        except Exception:
            log.exception("Scene acknowledgement failed")

    def apply_pending(self):
        with self.lock:
            item = self.pending
        if item is None:
            return False
        version, name, prompt, loaded = item
        cli = self.cli
        if name in (CLEAR_CONTEXT, CLEAR_CLARIFY):
            return self._apply_clear(version, clarify=name == CLEAR_CLARIFY)
        try:
            self.mic_thread.join(timeout=3)
            if self.mic_thread.is_alive():
                raise RuntimeError("Microphone is still releasing")
            cli.finalize_preloaded_skills()
            if self.base_prompt is None:
                prior = getattr(cli, "_preloaded_skills_prompt", "")
                self.base_prompt = cli.system_prompt or ""
                if prior and self.base_prompt.endswith(prior):
                    self.base_prompt = self.base_prompt[:-len(prior)].rstrip()
            # Drop queued input from the old generation, including goal continuations.
            for pending_queue in (cli._pending_input, cli._interrupt_queue):
                while True:
                    try:
                        pending_queue.get_nowait()
                    except queue.Empty:
                        break
            from hermes_cli.voice_outbox import cancel_session
            cancel_session(cli.session_id)
            cli.new_session(silent=True)
            # Rebuild lazily on the next user turn with only the selected skill.
            cli.agent = None
            cli.system_prompt = "\n\n".join(p for p in (self.base_prompt, prompt) if p)
            cli.preloaded_skills = loaded
            cli._preloaded_skills_prompt = prompt
            cli._pending_skills_reload_note = None
            cli._voice_followup_resume = None
            cli._voice_continuity_ended = False
            cli._pending_title = None
            cli._attached_images.clear()
            cli._reset_stream_state()
            if getattr(cli, "_app", None):
                cli._app.output.erase_screen()
                cli._app.output.cursor_goto(0, 0)
                cli._app.output.flush()
            if self.ack_thread:
                self.ack_thread.join(timeout=10)
                if self.ack_thread.is_alive():
                    raise RuntimeError("Acknowledgement playback is still active")
            with self.lock:
                if version != generation(cli):
                    return True
                self.active = name
            self._speak(self.scenes[name]["announcement"] if name is not None else EXIT_ACK)
            time.sleep(.25)
            log.info("voice_scene ready scene=%s generation=%s", name, version)
        except Exception as exc:
            log.exception("Scene switch failed")
            self.error = str(exc)
            if version == generation(cli):
                try:
                    self._speak("Scene switch failed. Please try again.")
                except Exception:
                    log.exception("Scene failure announcement unavailable")
        finally:
            with self.lock:
                if version == generation(cli):
                    self.pending = None
                    cli._scene_switching = False
                    cli._voice_processing = False
                    cli._voice_tts_done.set()
                    # Watchdog restores the paused listener after stable idle.
                    cli._wake_suspended = True
        return True

    def start(self):
        import fcntl
        path = socket_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_file = open(path.with_suffix(".lock"), "a")
        fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path.unlink(missing_ok=True)
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(path))
        path.chmod(0o600)
        self.server.listen(4)
        self.server.settimeout(.5)
        atexit.register(self.close)
        threading.Thread(target=self._serve, daemon=True, name="voice-scene-control").start()

    def _serve(self):
        server = self.server
        while not getattr(self.cli, "_should_exit", False):
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                try:
                    data = bytearray()
                    while b"\n" not in data and len(data) < 4096:
                        chunk = conn.recv(1024)
                        if not chunk:
                            break
                        data.extend(chunk)
                    req = json.loads(data)
                    action = req.get("action")
                    result = self.status() if action == "status" else self.request(
                        req.get("scene"), action=action, request_id=req.get("request_id"))
                    reply = {"ok": True, **result}
                except Exception as exc:
                    reply = {"ok": False, "error": str(exc)}
                try:
                    conn.sendall((json.dumps(reply) + "\n").encode())
                except OSError:
                    pass

    def close(self):
        if self.server:
            self.server.close()
            self.server = None
            socket_path().unlink(missing_ok=True)
        if self.lock_file:
            self.lock_file.close()
            self.lock_file = None


def start_for_cli(cli):
    raw, tts = settings()
    if raw.get("enabled") is not True or not getattr(cli, "_wake_word_active", False):
        return
    scenes = raw.get("items") or DEFAULT_SCENES
    controller = SceneController(cli, scenes, prepare_audio(scenes, tts))
    controller.start()
    cli._scene_controller = controller
    log.info("Voice scene controls available: %s", ", ".join(scenes))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["switch", "toggle", "clear", "status", "prepare"])
    parser.add_argument("scene", nargs="?")
    parser.add_argument("--request-id", help="Deduplicate a control request within this CLI process")
    args = parser.parse_args()
    if args.action == "prepare":
        raw, tts = settings()
        prepare_audio(raw.get("items") or DEFAULT_SCENES, tts)
        print("Scene announcements cached.")
        return
    if args.action in {"switch", "toggle"} and not args.scene:
        parser.error(args.action + " requires a scene ID")
    try:
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(5)
            conn.connect(str(socket_path()))
            conn.sendall((json.dumps(vars(args)) + "\n").encode())
            response = json.loads(conn.makefile().readline(16384))
        print(json.dumps(response))
        if not response.get("ok"):
            raise SystemExit(1)
    except OSError as exc:
        parser.exit(1, "Voice CLI control unavailable: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
