"""Prevent internal mid-turn control markers from leaking as assistant text.

The steer marker is input-only protocol metadata.  Smaller/local models can
occasionally copy the marker (and the user text inside it) into their response.
This module provides both whole-message and streaming scrubbers so such echoes
never reach a UI, TTS consumer, or the durable conversation transcript.
"""

from __future__ import annotations


_OPEN_PREFIX = "[OUT-OF-BAND USER MESSAGE"
_CLOSE_MARKER = "[/OUT-OF-BAND USER MESSAGE]"


def strip_control_marker_echoes(text: str) -> str:
    """Remove echoed OUT-OF-BAND blocks from completed assistant content.

    An unterminated block is removed through end-of-string.  That is the safe
    choice because everything after an input-only opening marker may contain a
    duplicated user message or other internal protocol text.
    """
    if not isinstance(text, str) or not text:
        return text

    result = text
    while True:
        lowered = result.lower()
        start = lowered.find(_OPEN_PREFIX.lower())
        if start < 0:
            return result
        end = lowered.find(_CLOSE_MARKER.lower(), start + len(_OPEN_PREFIX))
        if end < 0:
            return result[:start]
        result = result[:start] + result[end + len(_CLOSE_MARKER):]


class StreamingControlMarkerScrubber:
    """Stateful scrubber for OUT-OF-BAND blocks split across stream deltas."""

    def __init__(self) -> None:
        self._in_span = False
        self._buf = ""

    def reset(self) -> None:
        self._in_span = False
        self._buf = ""

    @staticmethod
    def _max_partial_suffix(buf: str, marker: str) -> int:
        marker_lower = marker.lower()
        buf_lower = buf.lower()
        for size in range(min(len(buf_lower), len(marker_lower) - 1), 0, -1):
            if marker_lower.startswith(buf_lower[-size:]):
                return size
        return 0

    def feed(self, text: str) -> str:
        if not text:
            return ""

        buf = self._buf + text
        self._buf = ""
        visible: list[str] = []

        while buf:
            lowered = buf.lower()
            if self._in_span:
                end = lowered.find(_CLOSE_MARKER.lower())
                if end < 0:
                    held = self._max_partial_suffix(buf, _CLOSE_MARKER)
                    self._buf = buf[-held:] if held else ""
                    return "".join(visible)
                buf = buf[end + len(_CLOSE_MARKER):]
                self._in_span = False
                continue

            start = lowered.find(_OPEN_PREFIX.lower())
            if start < 0:
                held = self._max_partial_suffix(buf, _OPEN_PREFIX)
                if held:
                    visible.append(buf[:-held])
                    self._buf = buf[-held:]
                else:
                    visible.append(buf)
                return "".join(visible)

            if start:
                visible.append(buf[:start])
            buf = buf[start + len(_OPEN_PREFIX):]
            self._in_span = True

        return "".join(visible)

    def flush(self) -> str:
        """Emit a harmless partial prefix, or discard an unfinished block."""
        if self._in_span:
            self.reset()
            return ""
        tail = self._buf
        self._buf = ""
        return tail
