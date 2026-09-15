"""Voice-only clarify prompt and spoken-answer helpers."""

import json
import queue
import threading
import time
from unittest.mock import MagicMock, patch

from hermes_cli.voice_clarify import build_spoken_prompt, resolve_spoken_answer


def test_prompt_speaks_question_and_every_choice():
    prompt = build_spoken_prompt(
        "Which city has the best weather?",
        ["Seattle (Recommended)", "Boston", "Austin"],
    )

    assert "Which city has the best weather" in prompt
    assert "Option 1, Seattle" in prompt
    assert "Option 2, Boston" in prompt
    assert "Option 3, Austin" in prompt
    assert "Recommended" not in prompt


def test_english_prompt_is_concise_and_numbered():
    prompt = build_spoken_prompt("Which city?", ["London", "Boston"])

    assert prompt.startswith("Which city.")
    assert "Option 1, London." in prompt
    assert "Option 2, Boston." in prompt
    assert prompt.endswith("Say the option number, or answer directly.")


def test_resolves_english_ordinals():
    choices = ["Seattle (Recommended)", "Boston", "Austin"]

    assert resolve_spoken_answer("the second one", choices) == "Boston"
    assert resolve_spoken_answer("option three", choices) == "Austin"


def test_resolves_multi_select_to_clarify_json_shape():
    answer = resolve_spoken_answer(
        "option one and option three", ["Seattle", "Boston", "Austin"], multi_select=True
    )

    assert json.loads(answer) == ["Seattle", "Austin"]


def test_unmatched_free_text_is_preserved():
    assert resolve_spoken_answer("I would rather see Portland", ["Seattle", "Boston"]) == "I would rather see Portland"


def test_cli_routes_voice_answer_to_clarify_queue_not_next_turn():
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_clarify_response_queue = queue.Queue()
    cli._voice_clarify_choices = ["Continue (Recommended)", "Cancel"]
    cli._voice_clarify_multi_select = False
    cli._voice_clarify_listening = True

    with patch("cli._cprint"):
        assert cli._voice_route_clarify_transcript("option two") is True

    assert cli._voice_clarify_response_queue.get_nowait() == "Cancel"
    assert cli._voice_clarify_listening is False


def test_cli_opens_listener_only_after_tts_barrier(monkeypatch):
    from cli import HermesCLI
    from tools.tts_tool import ImmediateTTSUtterance, TTSPlaybackBarrier

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_mode = True
    cli._voice_tts = True
    # Wake-word turns are deliberately one-shot rather than continuous.  A
    # clarify inside that turn must still speak and open its dedicated ASR
    # answer window.
    cli._voice_continuous = False
    cli._voice_last_tts_text = ""
    cli._voice_fd_active = threading.Event()
    cli._voice_tool_ack_lock = threading.Lock()
    cli._voice_turn_tts_queue = queue.Queue()
    cli._voice_full_duplex_listener = MagicMock()
    cli._voice_beeps_enabled = MagicMock(return_value=False)
    cli._voice_clarify_settings = MagicMock(
        return_value={
            "enabled": True,
            "followup_timeout_seconds": 30.0,
            "playback_timeout_seconds": 2.0,
        }
    )

    cue_done = threading.Event()
    def cue(_cli):
        assert not cli._voice_full_duplex_listener.called
        cue_done.set()
    monkeypatch.setattr("hermes_cli.voice_ready_cue.play_ready_cue", cue)
    def capture(**kw):
        assert cue_done.is_set(), "Capture before cue"
    cli._voice_full_duplex_listener.side_effect = capture
    result = {}

    def _begin():
        result["started"] = cli._voice_clarify_begin(
            "Would you like to continue?", ["Continue", "Cancel"], False, queue.Queue()
        )

    worker = threading.Thread(target=_begin, daemon=True)
    worker.start()
    prompt = cli._voice_turn_tts_queue.get(timeout=1)
    barrier = cli._voice_turn_tts_queue.get(timeout=1)

    assert isinstance(prompt, ImmediateTTSUtterance)
    assert isinstance(barrier, TTSPlaybackBarrier)
    assert not cli._voice_clarify_listening
    assert not cli._voice_full_duplex_listener.called

    assert not cue_done.is_set()
    barrier.set()
    worker.join(timeout=1)
    deadline = time.monotonic() + 1
    while not cli._voice_full_duplex_listener.called and time.monotonic() < deadline:
        time.sleep(0.01)

    assert cue_done.is_set()
    assert result["started"] is True
    assert cli._voice_clarify_listening is True
    cli._voice_full_duplex_listener.assert_called_once_with(clarify_only=True)


def test_cli_voice_clarify_requires_active_voice_turn_queue():
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_mode = True
    cli._voice_tts = True
    cli._voice_continuous = False
    cli._voice_tool_ack_lock = threading.Lock()
    cli._voice_turn_tts_queue = None
    cli._voice_clarify_settings = MagicMock(
        return_value={
            "enabled": True,
            "followup_timeout_seconds": 30.0,
            "playback_timeout_seconds": 2.0,
        }
    )

    assert cli._voice_clarify_available() is False

    cli._voice_turn_tts_queue = queue.Queue()
    assert cli._voice_clarify_available() is True
