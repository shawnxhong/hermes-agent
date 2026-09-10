#!/usr/bin/env python3
"""Real local-Qwen travel-skill check; every email is captured, never sent."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--surface", choices=["voice", "im"], default="voice")
parser.add_argument("--search-failure", action="store_true")
parser.add_argument("--email-failure", action="store_true")
parser.add_argument("--complete-request", action="store_true")
parser.add_argument("--cross-task", action="store_true")
args = parser.parse_args()
if args.surface == "im" and args.email_failure:
    parser.error("--email-failure applies only to host-owned voice delivery")

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
home = Path(tempfile.mkdtemp(prefix="hermes-travel-skill-check-"))

import yaml
from dotenv import dotenv_values

config = yaml.safe_load(Path("/home/agentdemo/.hermes/config.yaml").read_text())
config["model"] = {
    "default": "qwen3.6-35b-a3b",
    "provider": "custom",
    "base_url": "http://localhost:8000/v3",
    "context_length": 65536,
    "max_tokens": 6144,
}
config["fallback_providers"] = []
config["plugins"] = {"enabled": ["general-voice"]}
config["voice_delivery"] = {
    "enabled": True,
    "continuity": {"enabled": True},
    "default_recipient": "demo@example.com",
}
config["memory"] = {"enabled": False}
config.setdefault("tools", {}).setdefault("tool_search", {})["enabled"] = "off"
(home / "config.yaml").write_text(yaml.safe_dump(config))
shutil.copytree(
    repo / "skills/productivity/travel-concierge",
    home / "skills/travel-concierge",
)
shutil.copytree(
    repo / "scripts/local-ovms/plugins/general-voice",
    home / "plugins/general-voice",
)

os.environ["HERMES_HOME"] = str(home)
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"
for key, value in dotenv_values("/home/agentdemo/.hermes/.env").items():
    if key in {"BRAVE_API_KEY", "BRAVE_SEARCH_API_KEY"} and value:
        os.environ[key] = value
os.environ["EMAIL_ADDRESS"] = "test-sender@example.com"
os.environ["EMAIL_PASSWORD"] = "test-only-never-used"
os.environ["EMAIL_SMTP_HOST"] = "invalid.example"

from agent.skill_commands import build_preloaded_skills_prompt
from hermes_cli import plugins
from hermes_cli.voice_response_policy import build_voice_turn_prefix
from run_agent import AIAgent
from tools.registry import registry
import tools.send_message_tool

skill_prompt, loaded, missing = build_preloaded_skills_prompt(["travel-concierge"])
assert loaded == ["travel-concierge"] and not missing
assert "For an ordinary season-neutral itinerary, prefer stable knowledge" in skill_prompt

model_tools = []


def wrap(entry):
    original = entry.handler

    def handler(params, **kwargs):
        model_tools.append({"name": entry.name, "args": params})
        print("MODEL_TOOL", entry.name, flush=True)
        if entry.name == "send_message":
            return json.dumps(
                {"error": "The buffered voice host owns result delivery."}
            )
        if entry.name in {"web_search", "web_extract"}:
            if args.search_failure:
                return json.dumps(
                    {"error": "Simulated network unavailable. Do not retry."}
                )
            cap = 2 if entry.name == "web_search" else 1
            if sum(call["name"] == entry.name for call in model_tools) > cap:
                return json.dumps({"error": "Evaluation call budget exceeded."})
            return original(params, **kwargs)
        if entry.name in {"skill_view", "skills_list"}:
            return original(params, **kwargs)
        return json.dumps({"error": "Tool blocked in isolated skill evaluation."})

    return handler


registry.get_entry("send_message").check_fn = lambda: True
for entry in registry._snapshot_entries():
    entry.handler = wrap(entry)

manager = plugins.get_plugin_manager()
callbacks = manager._hooks.get("run_turn_workflow", [])
assert len(callbacks) == 1, "general-voice workflow was not loaded"
namespace = callbacks[0].__globals__
host_mail = []


def capture_email(recipient, body):
    host_mail.append({"recipient": recipient, "body": body})
    if args.email_failure:
        return {"error": "Simulated SMTP failure"}
    return {"success": True, "platform": "email", "test_capture_only": True}


namespace["_send"] = capture_email

platform = "cli" if args.surface == "voice" else "feishu"
toolsets = ["web", "voice_delivery"] if args.surface == "voice" else ["web"]
agent = AIAgent(
    model="qwen3.6-35b-a3b",
    provider="custom",
    base_url="http://localhost:8000/v3",
    api_key="local-ovms",
    api_mode="chat_completions",
    quiet_mode=True,
    max_iterations=8,
    request_overrides={"temperature": 0},
    enabled_toolsets=toolsets,
    skip_memory=True,
    skip_context_files=True,
    ephemeral_system_prompt=skill_prompt,
    platform=platform,
)
print("EXPOSED_TOOLS", sorted(agent.valid_tool_names), flush=True)
assert "web_search" in agent.valid_tool_names

inputs = (
    ["Plan a five-day trip to Melbourne from Sydney."
     + (" Check current attraction opening status." if args.search_failure else "")]
    if args.complete_request
    else [
        "I want to travel to Melbourne. Could you give me some advice?",
        "I will be traveling from Sydney and I will have five days."
        + (" Please check current attraction opening status." if args.search_failure else ""),
    ]
)
if args.cross_task:
    inputs.append("What is two plus two? Reply with only the number.")
history = []
receipts = []
for message in inputs:
    started = time.monotonic()
    tool_offset = len(model_tools)
    mail_offset = len(host_mail)
    modality = "voice" if args.surface == "voice" else "text"
    presented = (
        build_voice_turn_prefix(followup_enabled=True) + message
        if args.surface == "voice"
        else message
    )
    result = agent.run_conversation(
        user_message=presented,
        conversation_history=history,
        persist_user_message=message,
        input_modality=modality,
    )
    history = result.get("messages", [])
    receipt = {
        "input": message,
        "reply": result.get("final_response") or "",
        "seconds": round(time.monotonic() - started, 2),
        "reason": result.get("turn_exit_reason"),
        "model_tools": model_tools[tool_offset:],
        "host_mail": host_mail[mail_offset:],
    }
    receipts.append(receipt)
    print(json.dumps(receipt, ensure_ascii=False), flush=True)

report = home / "receipt.json"
report.write_text(json.dumps(receipts, indent=2, ensure_ascii=False))
print("RECEIPT", report, flush=True)

assert not any(call["name"] == "clarify" for call in model_tools)
assert not any(call["name"] == "skill_manage" for call in model_tools)
assert not any(call["name"] == "send_message" for call in model_tools)
search_count = sum(call["name"] == "web_search" for call in model_tools)
assert search_count <= (1 if args.search_failure else 3)
assert sum(call["name"] == "web_extract" for call in model_tools) <= (
    0 if args.search_failure else 1
)

if not args.complete_request:
    first = receipts[0]
    assert not first["model_tools"] and not first["host_mail"]
    assert first["reply"].rstrip().endswith("?")
    assert len(first["reply"].split()) <= 45
    assert re.search(r"\b(?:how many days|how long|duration)\b", first["reply"], re.I)
    assert re.search(r"\b(?:from|depart(?:ing|ure)?)\b", first["reply"], re.I)
    assert not re.search(r"\b(?:month|date|budget|hotel)\b", first["reply"], re.I)

travel_index = 0 if args.complete_request else 1
travel_reply = receipts[travel_index]["reply"]
detail = host_mail[0]["body"] if args.surface == "voice" else travel_reply
assert "Sydney" in detail and "Melbourne" in detail
assert "```" not in detail
assert re.search(
    r"(?mi)^\|\s*day\s*\|\s*area(?:\s+or\s+theme)?\s*\|"
    r"\s*morning\s*\|\s*afternoon\s*\|\s*evening(?:\s+and\s+logistics)?\s*\|",
    detail,
)
assert re.search(r"(?i)transport", detail)
for day in range(1, 6):
    assert re.search(rf"(?mi)^\|\s*(?:day\s*)?{day}\s*\|", detail), day

if args.surface == "voice":
    assert len(host_mail) == 1
    assert host_mail[0]["recipient"] == "demo@example.com"
    assert "|" not in travel_reply
    assert len(travel_reply.split()) <= 100
    if args.email_failure:
        assert "not confirmed" in travel_reply.lower()
    else:
        assert "submitted for email delivery" in travel_reply.lower()
else:
    assert not host_mail

if args.cross_task:
    final = receipts[-1]
    assert not final["model_tools"] and not final["host_mail"]
    assert final["reply"].strip().rstrip(".") == "4"

print(
    "PASS: two-stage travel skill, tabular plan, surface-specific delivery; "
    "mail captured only.",
    flush=True,
)
