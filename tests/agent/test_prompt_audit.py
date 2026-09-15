from __future__ import annotations

import json
import stat
from types import SimpleNamespace

from agent import prompt_audit


class _Completions:
    def __init__(self):
        self.calls = []

    def create(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(choices=[])


class _Client:
    def __init__(self):
        self.completions = _Completions()
        self.chat = SimpleNamespace(completions=self.completions)

    def with_options(self, *args, **kwargs):
        return self


def _agent(client=None, platform="cli"):
    return SimpleNamespace(
        client=client or _Client(),
        platform=platform,
        session_id="audit-session",
        _cached_system_prompt="cached prompt",
        ephemeral_system_prompt="voice overlay",
    )


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_marker_waits_for_matching_fresh_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(prompt_audit, "_system_sources", lambda agent: {"parts": {}})
    armed = prompt_audit.arm_next_prompt_audit("cli")
    marker = prompt_audit.audit_root() / "armed.json"

    assert prompt_audit.begin_prompt_audit_turn(
        _agent(platform="feishu"), [], "wrong platform"
    ) is None
    assert marker.exists()
    assert prompt_audit.begin_prompt_audit_turn(
        _agent(), [{"role": "user", "content": "old"}], "resumed"
    ) is None
    assert marker.exists()

    agent = _agent()
    scope = prompt_audit.begin_prompt_audit_turn(
        agent, [], "How are you?", input_modality="voice"
    )
    assert scope is not None
    assert not marker.exists()
    assert scope.platform == armed["platform"]
    assert scope.modality == "voice"
    assert _mode(scope.directory) == 0o700
    assert _mode(scope.directory / "manifest.json") == 0o600


def test_captures_direct_and_main_requests_with_sources_and_redaction(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(
        prompt_audit,
        "_system_sources",
        lambda agent: {
            "source_hints": {
                "stable": ["stable source"],
                "context": ["context source"],
                "volatile": ["volatile source"],
                "ephemeral": ["ephemeral source"],
            },
            "parts": {
                "stable": "stable bytes",
                "context": "context bytes",
                "volatile": "volatile bytes",
            },
            "ephemeral_system_prompt": "ephemeral bytes",
            "context_paths_found": ["/workspace/AGENTS.md"],
            "plugin_system_prompt_sections": [
                {"id": "voice", "plugin": "voice", "content": "plugin bytes"}
            ],
        },
    )
    original_client = _Client()
    agent = _agent(original_client)
    prompt_audit.arm_next_prompt_audit("cli")
    scope = prompt_audit.begin_prompt_audit_turn(agent, [], "How are you?")

    agent.client.with_options(timeout=1).chat.completions.create(
        model="local-model",
        messages=[
            {"role": "system", "content": "direct harness instruction"},
            {"role": "user", "content": "sk-12345678901234567890"},
        ],
    )
    prompt_audit.capture_main_request(
        agent,
        {
            "model": "local-model",
            "messages": [
                {"role": "system", "content": "complete system prompt"},
                {"role": "user", "content": "How are you?"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "web_search", "description": "search"},
                }
            ],
        },
        api_call_count=1,
    )

    report_dir = prompt_audit.finish_prompt_audit_turn(agent, status="success")
    assert report_dir == scope.directory
    assert agent.client is original_client

    first = json.loads((scope.directory / "request-01.json").read_text())
    assert first["source"] == "direct_agent_client"
    assert first["callsite"]["function"] == "test_captures_direct_and_main_requests_with_sources_and_redaction"
    assert "12345678901234567890" not in json.dumps(first)
    second = json.loads((scope.directory / "request-02.json").read_text())
    assert second["source"] == "main_loop"
    assert second["tool_sources"][0]["name"] == "web_search"

    report = (scope.directory / "prompt-report.md").read_text()
    assert "stable bytes" in report
    assert "direct harness instruction" in report
    assert "complete system prompt" in report
    assert "/workspace/AGENTS.md" in report
    assert "plugin bytes" in report

    latest = json.loads((prompt_audit.audit_root() / "latest.json").read_text())
    assert latest["request_count"] == 2
    assert latest["report"] == str(scope.directory / "prompt-report.md")
    for path in scope.directory.iterdir():
        assert _mode(path) == 0o600
    assert _mode(prompt_audit.audit_root() / "latest.json") == 0o600


def test_moa_wire_observation_does_not_duplicate_main_request(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(prompt_audit, "_system_sources", lambda agent: {"parts": {}})
    agent = _agent()
    prompt_audit.arm_next_prompt_audit()
    scope = prompt_audit.begin_prompt_audit_turn(agent, [], "question")
    payload = {"model": "local-model", "messages": [{"role": "user", "content": "q"}]}

    prompt_audit.capture_main_request(agent, payload)
    prompt_audit.capture_prompt_audit_request(
        agent,
        payload,
        source="direct_agent_client",
        callsite={"module": "agent.chat_completion_helpers", "function": "dispatch"},
    )

    assert len(scope.calls) == 1
    assert scope.calls[0]["wire_client_observed"] is True
