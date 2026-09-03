"""Regression tests for screenless approvals and same-turn clarify loops."""

import json
import queue
import threading
import time
from unittest.mock import MagicMock, patch

from hermes_cli.voice_clarify import (
    build_approval_spoken_prompt,
    resolve_spoken_approval,
)
from tools.clarify_tool import ReusedClarifyResponse, clarify_tool


def test_approval_prompt_uses_turn_language_and_lists_every_safe_choice():
    prompt = build_approval_spoken_prompt(
        "computer_use: click element 1",
        "Allow computer_use to click?",
        ["once", "session", "always", "deny"],
        language="zh",
    )
    assert "需要确认一个高风险操作" in prompt
    assert "选项1，仅允许这一次" in prompt
    assert "选项4，拒绝" in prompt


def test_spoken_approval_resolves_bilingual_answers_and_rejects_ambiguity():
    choices = ["once", "session", "always", "deny"]
    assert resolve_spoken_approval("选项一", choices) == "once"
    assert resolve_spoken_approval("本次会话允许", choices) == "session"
    assert resolve_spoken_approval("always allow", choices) == "always"
    assert resolve_spoken_approval("do not allow once", choices) == "deny"
    assert resolve_spoken_approval("something unclear", choices) is None


def test_cli_unknown_voice_approval_fails_closed():
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_approval_response_queue = queue.Queue()
    cli._voice_approval_choices = ["once", "session", "always", "deny"]
    cli._voice_approval_listening = True

    with patch("cli._cprint"):
        assert cli._voice_route_approval_transcript("something unclear") is True
    assert cli._voice_approval_response_queue.get_nowait() == "deny"
    assert cli._voice_approval_listening is False


def test_cli_opens_approval_listener_only_after_tts_barrier():
    from cli import HermesCLI
    from tools.tts_tool import ImmediateTTSUtterance, TTSPlaybackBarrier

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_mode = True
    cli._voice_tts = True
    cli._voice_continuous = False
    cli._voice_last_tts_text = ""
    cli._voice_tool_ack_language = "zh"
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

    result = {}

    def _begin():
        result["started"] = cli._voice_approval_begin(
            "computer_use: click element 1",
            "Allow this action?",
            ["once", "session", "always", "deny"],
            queue.Queue(),
        )

    worker = threading.Thread(target=_begin, daemon=True)
    worker.start()
    prompt = cli._voice_turn_tts_queue.get(timeout=1)
    barrier = cli._voice_turn_tts_queue.get(timeout=1)
    assert isinstance(prompt, ImmediateTTSUtterance)
    assert isinstance(barrier, TTSPlaybackBarrier)
    assert not cli._voice_approval_listening

    barrier.set()
    worker.join(timeout=1)
    deadline = time.monotonic() + 1
    while not cli._voice_full_duplex_listener.called and time.monotonic() < deadline:
        time.sleep(0.01)
    assert result["started"] is True
    assert cli._voice_approval_listening is True
    cli._voice_full_duplex_listener.assert_called_once_with(clarify_only=True)


def test_duplicate_clarify_result_preserves_answer_and_adds_loop_notice():
    result = json.loads(
        clarify_tool(
            question="Which destination?",
            choices=["Tahoe", "Yosemite"],
            callback=lambda *args, **kwargs: ReusedClarifyResponse(
                "Tahoe (Recommended)"
            ),
        )
    )
    assert result["user_response"] == "Tahoe"
    assert "do not ask the same question again" in result["notice"]


def test_cli_reuses_exact_clarify_but_not_a_different_question():
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli._clarify_answer_cache = {}
    cli._persist_prompt_summary = MagicMock()
    first = cli._clarify_cache_signature("Which?", ["A", "B"], False)
    other = cli._clarify_cache_signature("Where?", ["A", "B"], False)
    cli._remember_clarify_answer(first, "A (Recommended)")

    reused = cli._reuse_clarify_answer(first)
    assert isinstance(reused, ReusedClarifyResponse)
    assert reused.answer == "A (Recommended)"
    assert cli._reuse_clarify_answer(other) is None
