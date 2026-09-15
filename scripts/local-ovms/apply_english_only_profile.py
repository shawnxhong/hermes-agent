#!/usr/bin/env python3
"""Apply the small, explicit English-only demo overlay to config.yaml."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

import yaml


ENGLISH_SYSTEM_PROMPT = "Respond only in English."
DEFAULT_ACK = ["Sure, let me check."]


def apply_profile(config: dict) -> list[str]:
    """Mutate a Hermes config and return the dotted paths that changed."""
    changed: list[str] = []

    def set_value(mapping: dict, key: str, value, path: str) -> None:
        if mapping.get(key) != value:
            mapping[key] = value
            changed.append(path)

    agent = config.setdefault("agent", {})
    display = config.setdefault("display", {})
    stt = config.setdefault("stt", {})
    local_stt = stt.setdefault("local", {})
    voice = config.setdefault("voice", {})
    tool_ack = voice.setdefault("tool_ack", {})

    set_value(agent, "system_prompt", ENGLISH_SYSTEM_PROMPT, "agent.system_prompt")
    set_value(display, "language", "en", "display.language")
    set_value(stt, "language", "en", "stt.language")
    set_value(local_stt, "language", "en", "stt.local.language")

    phrases = tool_ack.get("phrases")
    english = phrases.get("en") if isinstance(phrases, dict) else phrases
    if not isinstance(english, list) or not all(isinstance(item, str) and item.strip() for item in english):
        english = DEFAULT_ACK.copy()
    set_value(tool_ack, "phrases", {"en": english}, "voice.tool_ack.phrases")

    stop_phrases = voice.get("stop_phrases")
    if isinstance(stop_phrases, list):
        english_stops = [item for item in stop_phrases if isinstance(item, str) and item.strip() and item.isascii()]
        set_value(voice, "stop_phrases", english_stops or ["stop"], "voice.stop_phrases")

    ready_cue = voice.get("ready_cue")
    if isinstance(ready_cue, dict) and "intro_text" in ready_cue:
        set_value(
            ready_cue,
            "intro_text",
            "After the tone, you can answer directly.",
            "voice.ready_cue.intro_text",
        )

    travel_voice = config.get("travel_voice")
    if isinstance(travel_voice, dict):
        set_value(travel_voice, "enabled", False, "travel_voice.enabled")
    return changed


def write_atomic(path: Path, config: dict) -> None:
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=True)
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--write", action="store_true", help="write changes atomically")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise SystemExit("config root must be a mapping")
    changed = apply_profile(config)
    if args.write and changed:
        write_atomic(args.config, config)
    print("english_profile=" + ("changed" if changed else "current"))
    print("changed_paths=" + ",".join(changed))
    return 0 if args.write or not changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
