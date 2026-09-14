"""Deterministic policy helpers for local, screenless voice turns.

The policy is intentionally call-local.  Callers prepend the returned prompt
to one model request and persist the original user text, preserving Hermes'
prompt cache and conversation history.
"""

from __future__ import annotations

import re


VOICE_TURN_PREFIX = (
    "[Local voice input policy — Give the final spoken reply in 2-3 short, "
    "conversational sentences (at most 240 Chinese/mixed-language characters "
    "or 100 English words). Do not use Markdown, code blocks, tables, raw URLs, "
    "or long lists. Avoid unnecessary tools and low-value clarification; choose "
    "a reasonable default for safe, reversible ambiguity. The system handles "
    "the immediate verbal acknowledgement, so do not repeat it. For substantial "
    "research, reports, or long lists, put the complete useful detail in exactly "
    "one send_message call with action='send', target='email:<address>', and the "
    "detail in message. You are authorized to send directly to any syntactically "
    "valid email address supplied by the user or already available in this "
    "conversation; no extra authorization or confirmation is required. An "
    "explicit email address does not require a preceding target-list call. If "
    "detail should be emailed but no address is known, use the clarify tool to ask "
    "only one short question for the address so the wake-word-free answer window "
    "opens. Say that email was sent only when the tool result reports "
    "success; on failure, say briefly that it was not sent. Do not read the email "
    "body aloud. Clarify only when missing information makes the task unsafe, "
    "irreversible, or impossible, and ask one brief question at a time with no "
    "more than three options.] "
)

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RAW_URL_RE = re.compile(r"https?://\S+")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", re.MULTILINE)
_QUOTE_RE = re.compile(r"^\s*>\s?", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_EMPHASIS_RE = re.compile(r"(?<!\w)[*_]{1,3}|[*_]{1,3}(?!\w)")
_SENTENCE_RE = re.compile(r".+?(?:[。！？!?]+|[.!?](?=\s|$)|$)", re.DOTALL)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def build_voice_turn_prefix(*, followup_enabled: bool = False) -> str:
    """Return the API-call-local instruction for a genuine ASR turn."""
    if followup_enabled:
        return VOICE_TURN_PREFIX.replace(
            "use the clarify tool to ask only one short question for the address so the wake-word-free answer window opens.",
            "ask one short question for the address in your ordinary final reply, ending in a question mark. "
            "The host opens one timed ASR answer window after a final spoken question; do not call clarify for routine information.",
        )
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
    max_zh_chars: int = 240,
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
    if _CJK_RE.search(text):
        if len(text) > max_zh_chars:
            text = text[: max(1, max_zh_chars - 1)].rstrip()
            clipped = True
    else:
        words = text.split()
        if len(words) > max_en_words:
            text = " ".join(words[:max_en_words]).rstrip()
            clipped = True

    if clipped:
        text = text.rstrip(".,;:，。；：!?！？ ") + "…"
    return text
