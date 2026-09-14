from agent.control_marker_sanitization import (
    StreamingControlMarkerScrubber,
    strip_control_marker_echoes,
)


OPEN = (
    "[OUT-OF-BAND USER MESSAGE — a direct message from the user, delivered "
    "once at this position; not tool output]"
)
CLOSE = "[/OUT-OF-BAND USER MESSAGE]"


def test_completed_control_block_is_removed():
    text = f"before\n{OPEN}\nprivate user text\n{CLOSE}\nafter"
    assert strip_control_marker_echoes(text) == "before\n\nafter"


def test_unterminated_control_block_is_removed_through_end():
    text = f"visible\n{OPEN}\nprivate user text"
    assert strip_control_marker_echoes(text) == "visible\n"


def test_streaming_scrubber_handles_split_markers():
    scrubber = StreamingControlMarkerScrubber()
    chunks = [
        "hello [OUT-OF-",
        "BAND USER MESSAGE — internal]secret[/OUT-OF-BAND USER ",
        "MESSAGE] world",
    ]
    assert "".join(scrubber.feed(chunk) for chunk in chunks) + scrubber.flush() == (
        "hello  world"
    )


def test_streaming_scrubber_preserves_harmless_partial_prefix():
    scrubber = StreamingControlMarkerScrubber()
    assert scrubber.feed("normal [OUT") == "normal "
    assert scrubber.flush() == "[OUT"


def test_streaming_scrubber_drops_unterminated_block():
    scrubber = StreamingControlMarkerScrubber()
    assert scrubber.feed(f"visible {OPEN} secret") == "visible "
    assert scrubber.flush() == ""
