"""Deterministic policy helpers for local, screenless voice turns.

The policy is intentionally call-local.  Callers prepend the returned prompt
to one model request and persist the original user text, preserving Hermes'
prompt cache and conversation history.
"""

from __future__ import annotations

import re


VOICE_TURN_PREFIX = "[Voice input] "

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RAW_URL_RE = re.compile(r"https?://\S+")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", re.MULTILINE)
_QUOTE_RE = re.compile(r"^\s*>\s?", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_EMPHASIS_RE = re.compile(r"(?<!\w)[*_]{1,3}|[*_]{1,3}(?!\w)")
_SENTENCE_RE = re.compile(r".+?(?:[!?]+|[.!?](?=\s|$)|$)", re.DOTALL)


def build_voice_turn_prefix(*, followup_enabled: bool = False) -> str:
    """Return the API-call-local instruction for a genuine ASR turn."""
    # Style belongs to SOUL.md; task procedures belong to skills.
    return VOICE_TURN_PREFIX


def _plain_spoken_text(text: str) -> str:
    """Remove formatting that should never be read aloud."""
    value = _CODE_BLOCK_RE.sub(" ", str(text or ""))
    value = _MARKDOWN_LINK_RE.sub(r"\1", value)
    value = _RAW_URL_RE.sub(" ", value)
    value = _HEADING_RE.sub("", value)
    value = _LIST_MARKER_RE.sub("", value)
    value = _QUOTE_RE.sub("", value)
    value = _INLINE_CODE_RE.sub(r"\1", value)
    value = _EMPHASIS_RE.sub("", value)
    value = value.replace("|", " ")
    return re.sub(r"\s+", " ", value).strip()


def prepare_voice_tts_text(
    response: str,
    *,
    max_sentences: int = 3,
    max_en_words: int = 100,
) -> str:
    """Return a short, formatting-free utterance for the final voice reply.

    Prompting supplies the preferred behavior; this deterministic cap is the
    last line of defence when a local model emits an unexpectedly long answer.
    """
    text = _plain_spoken_text(response)
    if not text:
        return ""

    sentences = [item.strip() for item in _SENTENCE_RE.findall(text) if item.strip()]
    if sentences:
        text = " ".join(sentences[: max(1, max_sentences)])

    clipped = False
    words = text.split()
    if len(words) > max_en_words:
        text = " ".join(words[:max_en_words]).rstrip()
        clipped = True

    if clipped:
        text = text.rstrip(".,;:!? ") + "..."
    return text
