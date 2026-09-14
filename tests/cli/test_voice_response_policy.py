"""Behavior contracts for concise local-voice final responses."""

import queue

from hermes_cli.voice_response_policy import (
    build_voice_turn_prefix,
    prepare_voice_tts_text,
)


def test_voice_prompt_authorizes_direct_email_but_requires_truthful_status():
    prompt = build_voice_turn_prefix()

    assert "2-3 short" in prompt
    assert "exactly one send_message call" in prompt
    assert "no extra authorization or confirmation" in prompt
    assert "only when the tool result reports success" in prompt
    assert "does not require a preceding target-list call" in prompt
    assert "use the clarify tool" in prompt


def test_local_cli_includes_delivery_tool_without_changing_feishu_bundle():
    from toolsets import resolve_toolset

    assert "send_message" in resolve_toolset("hermes-cli")
    assert "send_message" not in resolve_toolset("hermes-feishu")


def test_short_mixed_language_response_is_preserved_for_tts():
    response = "成都适合三天慢游。I emailed the full itinerary to you."

    assert prepare_voice_tts_text(response) == (
        "成都适合三天慢游。 I emailed the full itinerary to you."
    )


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
        "简短结果。The email was sent successfully.",
        voice_input=True,
        interrupted=False,
    )
    queued = output.get_nowait()
    assert isinstance(queued, ImmediateTTSUtterance)
    assert str(queued) == "简短结果。 The email was sent successfully."
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
    output.put(ImmediateTTSUtterance("好的，我来处理。"))

    cli._enqueue_voice_final_tts(
        output,
        "已经完成，详细内容已发送。",
        voice_input=True,
        interrupted=False,
    )

    assert str(output.get_nowait()) == "好的，我来处理。"
    assert str(output.get_nowait()) == "已经完成，详细内容已发送。"
