import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/local-ovms/apply_english_only_profile.py"
SPEC = importlib.util.spec_from_file_location("apply_english_only_profile", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
ENGLISH_SYSTEM_PROMPT = MODULE.ENGLISH_SYSTEM_PROMPT
apply_profile = MODULE.apply_profile


def test_profile_removes_language_branches_and_forces_english_input():
    config = {
        "agent": {"system_prompt": ""},
        "display": {},
        "stt": {"language": "", "local": {"language": ""}},
        "voice": {
            "tool_ack": {
                "phrases": {
                    "en": ["One moment."],
                    "zh": ["legacy"],
                }
            },
            "stop_phrases": ["\u505c\u6b62", "stop"],
            "ready_cue": {"intro_text": "legacy"},
        },
        "travel_voice": {"enabled": True},
    }

    changed = apply_profile(config)

    assert config["agent"]["system_prompt"] == ENGLISH_SYSTEM_PROMPT
    assert config["display"]["language"] == "en"
    assert config["stt"]["language"] == "en"
    assert config["stt"]["local"]["language"] == "en"
    assert config["voice"]["tool_ack"]["phrases"] == {"en": ["One moment."]}
    assert config["voice"]["stop_phrases"] == ["stop"]
    assert config["voice"]["ready_cue"]["intro_text"] == "After the tone, you can answer directly."
    assert config["travel_voice"]["enabled"] is False
    assert "voice.tool_ack.phrases" in changed


def test_profile_is_idempotent_and_supplies_safe_defaults():
    config = {"voice": {"tool_ack": {"phrases": {"zh": ["legacy"]}}, "stop_phrases": ["\u505c\u6b62"]}}

    apply_profile(config)

    assert config["voice"]["tool_ack"]["phrases"] == {"en": ["Sure, let me check."]}
    assert config["voice"]["stop_phrases"] == ["stop"]
    assert apply_profile(config) == []
