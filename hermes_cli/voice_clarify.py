"""Pure helpers for voice-first clarify prompts and spoken answers."""

from __future__ import annotations

import json
import re
from typing import Optional, Sequence


_HAN_RE = re.compile(r"[\u3400-\u9fff]")
_RECOMMENDED_RE = re.compile(r"\s*\(recommended\)\s*$", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)

_ORDINALS = {
    "1": 0,
    "一": 0,
    "one": 0,
    "first": 0,
    "2": 1,
    "二": 1,
    "两": 1,
    "two": 1,
    "second": 1,
    "3": 2,
    "三": 2,
    "three": 2,
    "third": 2,
    "4": 3,
    "四": 3,
    "four": 3,
    "fourth": 3,
}


def spoken_language(text: str) -> str:
    """Return ``zh`` when *text* contains Han characters, otherwise ``en``."""
    return "zh" if _HAN_RE.search(text or "") else "en"


def bare_choice(choice: object) -> str:
    """Remove the CLI-only recommendation suffix before speech or matching."""
    return _RECOMMENDED_RE.sub("", str(choice or "").strip()).strip()


def build_spoken_prompt(
    question: str,
    choices: Sequence[object] | None,
    *,
    multi_select: bool = False,
) -> str:
    """Build a short bilingual prompt that remains usable without a screen."""
    clean_question = " ".join(str(question or "").split())
    clean_choices = [bare_choice(item) for item in (choices or [])]
    clean_choices = [item for item in clean_choices if item]
    language = spoken_language(clean_question + "".join(clean_choices))

    if language == "zh":
        parts = [clean_question.rstrip("。！？?!") + "。"]
        for index, choice in enumerate(clean_choices, start=1):
            parts.append(f"选项{index}，{choice}。")
        if clean_choices:
            action = "可以说多个选项编号，也可以直接说答案。" if multi_select else "请说选项编号，也可以直接说答案。"
        else:
            action = "请直接回答。"
        parts.append(action)
        return "".join(parts)

    parts = [clean_question.rstrip(".?!") + "."]
    for index, choice in enumerate(clean_choices, start=1):
        parts.append(f" Option {index}, {choice}.")
    if clean_choices:
        action = " Say one or more option numbers, or answer directly." if multi_select else " Say the option number, or answer directly."
    else:
        action = " Please answer now."
    parts.append(action)
    return "".join(parts)


_APPROVAL_LABELS = {
    "once": {"zh": "仅允许这一次", "en": "Allow once"},
    "session": {"zh": "本次会话内允许", "en": "Allow for this session"},
    "always": {"zh": "始终允许", "en": "Always allow"},
    "deny": {"zh": "拒绝", "en": "Deny"},
}


def build_approval_spoken_prompt(
    command: str,
    description: str,
    choices: Sequence[str],
    *,
    language: Optional[str] = None,
) -> str:
    """Build a screenless, bilingual dangerous-operation approval prompt."""
    clean_command = " ".join(str(command or "").split())
    clean_description = " ".join(str(description or "").split())
    # A shell command can be arbitrarily large.  The visual panel retains the
    # full value; speech needs a bounded summary so the approval stays usable.
    if len(clean_command) > 220:
        clean_command = clean_command[:217] + "..."
    spoken_choices = [choice for choice in choices if choice in _APPROVAL_LABELS]
    if language not in {"zh", "en"}:
        language = spoken_language(clean_description + clean_command)

    if language == "zh":
        parts = ["需要确认一个高风险操作。"]
        if clean_description:
            parts.append(clean_description.rstrip("。！？?!") + "。")
        if clean_command:
            parts.append("操作内容，" + clean_command.rstrip("。！？?!") + "。")
        for index, choice in enumerate(spoken_choices, start=1):
            parts.append(f"选项{index}，{_APPROVAL_LABELS[choice]['zh']}。")
        parts.append("请说选项编号或选项名称。")
        return "".join(parts)

    parts = ["A dangerous operation needs your approval."]
    if clean_description:
        parts.append(" " + clean_description.rstrip(".?!") + ".")
    if clean_command:
        parts.append(" Operation: " + clean_command.rstrip(".?!") + ".")
    for index, choice in enumerate(spoken_choices, start=1):
        parts.append(f" Option {index}, {_APPROVAL_LABELS[choice]['en']}.")
    parts.append(" Say the option number or option name.")
    return "".join(parts)


def _normalized(text: object) -> str:
    return _PUNCT_RE.sub("", bare_choice(text).casefold())


def _ordinal_indices(transcript: str) -> list[int]:
    text = transcript.casefold().strip()
    tokens: list[str] = []
    tokens.extend(
        match.group(1)
        for match in re.finditer(
            r"(?:第\s*|选项\s*|选择\s*|选\s*)([一二两三四1-4])(?:\s*(?:个|项|号))?",
            text,
        )
    )
    tokens.extend(
        match.group(1)
        for match in re.finditer(
            r"\b(?:option|choice)\s*(one|two|three|four|first|second|third|fourth|[1-4])\b",
            text,
        )
    )
    normalized = _normalized(text)
    if normalized in _ORDINALS:
        tokens.append(normalized)

    result: list[int] = []
    for token in tokens:
        index = _ORDINALS.get(token)
        if index is not None and index not in result:
            result.append(index)
    return result


def resolve_spoken_answer(
    transcript: str,
    choices: Sequence[object] | None,
    *,
    multi_select: bool = False,
) -> str:
    """Resolve spoken ordinals/labels while preserving unmatched free text."""
    answer = str(transcript or "").strip()
    clean_choices = [bare_choice(item) for item in (choices or [])]
    if not answer or not clean_choices:
        return answer

    indices = [index for index in _ordinal_indices(answer) if index < len(clean_choices)]
    normalized_answer = _normalized(answer)
    label_matches = [
        index
        for index, choice in enumerate(clean_choices)
        if _normalized(choice)
        and (
            normalized_answer == _normalized(choice)
            or _normalized(choice) in normalized_answer
        )
    ]
    for index in label_matches:
        if index not in indices:
            indices.append(index)

    if multi_select and indices:
        return json.dumps([clean_choices[index] for index in indices], ensure_ascii=False)
    if len(indices) == 1:
        return clean_choices[indices[0]]
    return answer


def resolve_spoken_approval(
    transcript: str,
    choices: Sequence[str],
) -> Optional[str]:
    """Resolve a spoken approval verdict, returning ``None`` when ambiguous.

    Approval is deliberately stricter than clarify: an unrecognised phrase
    must never become an implicit allow decision.
    """
    allowed = [choice for choice in choices if choice in _APPROVAL_LABELS]
    answer = str(transcript or "").strip()
    if not answer or not allowed:
        return None

    indices = [index for index in _ordinal_indices(answer) if index < len(allowed)]
    if len(indices) == 1:
        return allowed[indices[0]]

    normalized = _normalized(answer)
    aliases = {
        "deny": (
            "deny", "denied", "reject", "cancel", "no", "do not allow", "dont allow",
            "拒绝", "不允许", "不同意", "取消", "不要执行", "禁止",
        ),
        "session": (
            "allow for this session", "allow for session", "this session", "session",
            "本次会话内允许", "本次会话", "当前会话", "会话期间允许",
        ),
        "always": (
            "always allow", "allow always", "always", "permanently allow",
            "始终允许", "总是允许", "永久允许", "以后都允许",
        ),
        "once": (
            "allow once", "approve once", "just once", "once",
            "仅允许这一次", "只允许一次", "允许一次", "仅一次", "这一次",
        ),
    }
    # Denial is checked first so phrases such as "do not allow once" cannot
    # accidentally match the permissive "allow once" alias.
    for verdict in ("deny", "session", "always", "once"):
        if verdict not in allowed:
            continue
        for alias in aliases[verdict]:
            alias_normalized = _normalized(alias)
            exact_only = alias_normalized == "no"
            if normalized == alias_normalized or (
                not exact_only and alias_normalized in normalized
            ):
                return verdict
    return None
