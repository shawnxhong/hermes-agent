"""Behavior contracts for concise local-voice final responses."""

import queue

from hermes_cli.voice_response_policy import (
    build_voice_turn_prefix,
    prepare_voice_tts_text,
)


def test_voice_prefix_only_marks_modality():
    prompt = build_voice_turn_prefix()

    assert prompt == '[Voice input] '


def test_local_cli_includes_delivery_tool_without_changing_feishu_bundle():
    from toolsets import resolve_toolset

    assert "send_message" in resolve_toolset("hermes-cli")
    assert "send_message" not in resolve_toolset("hermes-feishu")


def test_short_english_response_is_preserved_for_tts():
    response = "Boston is well suited to a three-day visit. I emailed the full itinerary to you."

    assert prepare_voice_tts_text(response) == response


def test_voice_tts_text_removes_markdown_urls_and_caps_long_output():
    response = (
        "# Summary\n"
        "**Here is the result.** See [the report](https://example.com/report). "
        "Second sentence. Third sentence. Fourth sentence must not be spoken. "
        + "extra " * 150
    )

    spoken = prepare_voice_tts_text(response)

    assert "https://" not in spoken
    assert "**" not in spoken
    assert "Fourth sentence" not in spoken
    assert len(spoken.split()) <= 100


def test_only_voice_input_enables_tts_and_final_is_queued_once():
    from cli import HermesCLI
    from tools.tts_tool import ImmediateTTSUtterance

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_tts = True
    cli._voice_last_tts_text = ""
    output = queue.Queue()

    assert cli._voice_tts_enabled_for_turn(True) is True
    assert cli._voice_tts_enabled_for_turn(False) is False
    assert cli._enqueue_voice_final_tts(
        output,
        "The summary is ready. The email was sent successfully.",
        voice_input=True,
        interrupted=False,
    )
    queued = output.get_nowait()
    assert isinstance(queued, ImmediateTTSUtterance)
    assert str(queued) == "The summary is ready. The email was sent successfully."
    assert output.empty()


def test_keyboard_and_interrupted_turns_do_not_queue_final_tts():
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_tts = True
    cli._voice_last_tts_text = ""
    output = queue.Queue()

    assert cli._enqueue_voice_final_tts(
        output, "typed response", voice_input=False, interrupted=False
    ) == ""
    assert cli._enqueue_voice_final_tts(
        output, "interrupted response", voice_input=True, interrupted=True
    ) == ""
    assert output.empty()


def test_verbal_ack_stays_before_buffered_final_response():
    from cli import HermesCLI
    from tools.tts_tool import ImmediateTTSUtterance

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_last_tts_text = ""
    output = queue.Queue()
    output.put(ImmediateTTSUtterance("I am working on it."))

    cli._enqueue_voice_final_tts(
        output,
        "The work is complete, and the details were sent.",
        voice_input=True,
        interrupted=False,
    )

    assert str(output.get_nowait()) == "I am working on it."
    assert str(output.get_nowait()) == "The work is complete, and the details were sent."
