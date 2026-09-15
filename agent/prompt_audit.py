"""One-shot first-turn prompt auditing for local diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import inspect
import json
import os
from pathlib import Path
import re
import threading
from typing import Any

from hermes_constants import get_hermes_home
from utils import atomic_json_write, atomic_write_text

_MARKER = "armed.json"
_PLATFORM = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass
class PromptAuditScope:
    directory: Path
    session_id: str
    platform: str
    modality: str
    started_at: str
    first_user_message: Any
    calls: list[dict[str, Any]] = field(default_factory=list)
    system_sources: dict[str, Any] = field(default_factory=dict)
    status: str = "capturing"
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


def audit_root() -> Path:
    return get_hermes_home() / "debug" / "prompt-audit"


def arm_next_prompt_audit(platform: str = "cli") -> dict[str, str]:
    platform = str(platform or "cli").strip().lower()
    if not _PLATFORM.fullmatch(platform):
        raise ValueError("invalid platform")
    root = audit_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    marker = root / _MARKER
    atomic_json_write(
        marker,
        {
            "schema_version": 1,
            "armed_at": datetime.now().astimezone().isoformat(),
            "platform": platform,
            "mode": "next_fresh_turn",
        },
        mode=0o600,
    )
    return {"marker": str(marker), "output_root": str(root), "platform": platform}


def _jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 24:
        return f"<max-depth:{type(value).__name__}>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v, depth + 1) for v in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _jsonable(dump(), depth + 1)
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _jsonable(
                {k: v for k, v in vars(value).items() if not str(k).startswith("_")},
                depth + 1,
            )
        except Exception:
            pass
    return str(value)


def _redact(value: Any) -> Any:
    from agent.redact import redact_sensitive_text

    raw = json.dumps(_jsonable(value), ensure_ascii=False, default=str)
    scrubbed = redact_sensitive_text(raw, force=True)
    try:
        return json.loads(scrubbed)
    except json.JSONDecodeError:
        return {"_redaction_error": True, "preview": scrubbed[:2000]}


def _safe(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "session"))
    return text.strip("._")[:80] or "session"


def _has_history(history: Any) -> bool:
    if not isinstance(history, list):
        return bool(history)
    return any(
        isinstance(row, dict) and row.get("role") in {"user", "assistant", "tool"}
        for row in history
    )


def _context_paths() -> list[str]:
    home = get_hermes_home()
    paths = [
        path
        for path in (
            home / "SOUL.md",
            home / "memories" / "MEMORY.md",
            home / "memories" / "USER.md",
        )
        if path.is_file()
    ]
    try:
        from agent.runtime_cwd import resolve_context_cwd
        cwd = Path(resolve_context_cwd() or os.getcwd()).resolve()
    except Exception:
        cwd = Path.cwd().resolve()
    names = (
        ".hermes.md", "HERMES.md", "AGENTS.override.md", "AGENTS.md",
        "agents.md", "CLAUDE.md", "claude.md", ".cursorrules",
    )
    seen: set[Path] = set()
    current = cwd
    while current not in seen:
        seen.add(current)
        for name in names:
            candidate = current / name
            if candidate.is_file() and candidate not in paths:
                paths.append(candidate)
        if current.parent == current or (current / ".git").exists():
            break
        current = current.parent
    rules = cwd / ".cursor" / "rules"
    if rules.is_dir():
        paths.extend(path for path in sorted(rules.glob("*.mdc")) if path.is_file())
    return [str(path) for path in paths]


def _system_sources(agent: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source_hints": {
            "stable": [
                "HERMES_HOME/SOUL.md or the built-in agent identity",
                "agent/system_prompt.py guidance selected by model and tools",
                "platform, environment and coding guidance when applicable",
            ],
            "context": [
                "caller system_message",
                "working-directory HERMES.md, AGENTS.md, CLAUDE.md or .cursorrules",
            ],
            "volatile": [
                "installed-skill index",
                "HERMES_HOME/memories/MEMORY.md and USER.md",
                "external memory and plugin system-prompt sections",
                "session/date/model/provider/platform metadata",
            ],
            "ephemeral": [
                "CLI personality, agent.system_prompt or explicitly preloaded skill prompt",
            ],
        },
        "context_paths_found": _context_paths(),
        "ephemeral_system_prompt": getattr(agent, "ephemeral_system_prompt", None) or "",
        "cached_system_prompt": getattr(agent, "_cached_system_prompt", None) or "",
    }
    try:
        from agent.system_prompt import build_system_prompt_parts
        parts = build_system_prompt_parts(agent)
        data["parts"] = parts
        joined = "\n\n".join(parts[name] for name in ("stable", "context", "volatile") if parts.get(name))
        data["parts_match_cached_prompt"] = joined == data["cached_system_prompt"]
    except Exception as exc:
        data["parts_error"] = f"{type(exc).__name__}: {exc}"
    try:
        from agent.system_prompt import _frozen_plugin_prompt_sections
        data["plugin_system_prompt_sections"] = [
            {
                "id": str(getattr(item, "id", "")),
                "plugin": str(getattr(item, "plugin", "")),
                "position": str(getattr(item, "position", "")),
                "content": str(getattr(item, "content", "")),
            }
            for item in _frozen_plugin_prompt_sections(agent)
        ]
    except Exception as exc:
        data["plugin_sections_error"] = f"{type(exc).__name__}: {exc}"
    return _redact(data)


def begin_prompt_audit_turn(
    agent: Any,
    conversation_history: Any,
    user_message: Any,
    input_modality: str | None = None,
) -> PromptAuditScope | None:
    if getattr(agent, "_prompt_audit_scope", None) is not None:
        return agent._prompt_audit_scope
    if _has_history(conversation_history):
        return None
    marker = audit_root() / _MARKER
    try:
        armed = json.loads(marker.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    platform = str(getattr(agent, "platform", None) or "").lower()
    if str(armed.get("platform") or "cli").lower() != platform:
        return None
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    session_id = str(getattr(agent, "session_id", None) or "session")
    directory = audit_root() / f"{stamp}-{_safe(session_id)}"
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        os.replace(marker, directory / _MARKER)
    except OSError:
        try:
            directory.rmdir()
        except OSError:
            pass
        return None
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    scope = PromptAuditScope(
        directory=directory,
        session_id=session_id,
        platform=platform,
        modality=str(input_modality or ""),
        started_at=datetime.now().astimezone().isoformat(),
        first_user_message=_redact(user_message),
    )
    scope.system_sources = _system_sources(agent)
    agent._prompt_audit_scope = scope
    _ensure_proxy(agent, scope)
    _write(scope)
    return scope


def _callsite() -> dict[str, Any]:
    own = Path(__file__).resolve()
    for frame in inspect.stack(context=0)[2:18]:
        path = Path(frame.filename).resolve()
        module = str(frame.frame.f_globals.get("__name__", ""))
        if path == own or module.startswith(("openai.", "httpx.", "httpcore.")):
            continue
        return {
            "module": module,
            "function": frame.function,
            "file": str(path),
            "line": frame.lineno,
        }
    return {}


def _tool_sources(body: dict[str, Any]) -> list[dict[str, str]]:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return []
    try:
        from tools.registry import registry
        mapping = registry.get_tool_to_toolset_map()
    except Exception:
        mapping = {}
    result = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "")
        result.append({"name": name, "toolset": str(mapping.get(name) or "(unknown)")})
    return result


def _message_sources(body: dict[str, Any], source: str) -> list[dict[str, Any]]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        messages = body.get("input")
    if not isinstance(messages, list):
        return []
    out = []
    for index, message in enumerate(messages):
        role = message.get("role") if isinstance(message, dict) else ""
        if source == "main_loop":
            if index == 0 and role in {"system", "developer"}:
                origin = "cached system prompt plus ephemeral CLI overlay"
            elif role == "user" and index == len(messages) - 1:
                origin = "current input plus turn-scoped memory/plugin/workflow context"
            else:
                origin = "session history or current tool loop"
        else:
            origin = "direct harness callsite literal and payload"
        out.append({"index": index, "role": str(role or ""), "source": origin})
    return out


def capture_prompt_audit_request(
    agent: Any,
    api_kwargs: dict[str, Any],
    *,
    source: str,
    metadata: dict[str, Any] | None = None,
    callsite: dict[str, Any] | None = None,
) -> Path | None:
    scope = getattr(agent, "_prompt_audit_scope", None)
    if not isinstance(scope, PromptAuditScope):
        return None
    body = {
        key: value
        for key, value in dict(api_kwargs or {}).items()
        if key not in {"timeout", "http_client"} and not str(key).startswith("_")
    }
    body = _redact(body)
    if not isinstance(body, dict):
        body = {"payload": body}
    with scope.lock:
        site = callsite or _callsite()
        # The main loop records the final payload before dispatch. MoA uses
        # agent.client for that dispatch, so the temporary client proxy sees
        # the same request a second time. Record one request and annotate that
        # it was also observed at the wire-facing client boundary.
        if (
            source == "direct_agent_client"
            and str(site.get("module") or "") == "agent.chat_completion_helpers"
            and scope.calls
            and scope.calls[-1].get("source") == "main_loop"
            and scope.calls[-1].get("request_body") == body
        ):
            scope.calls[-1]["wire_client_observed"] = True
            number = int(scope.calls[-1]["sequence"])
            path = scope.directory / f"request-{number:02d}.json"
            atomic_json_write(path, scope.calls[-1], mode=0o600)
            _write(scope)
            return path
        number = len(scope.calls) + 1
        record = {
            "schema_version": 1,
            "sequence": number,
            "captured_at": datetime.now().astimezone().isoformat(),
            "session_id": scope.session_id,
            "platform": scope.platform,
            "input_modality": scope.modality,
            "source": source,
            "callsite": site,
            "metadata": _redact(metadata or {}),
            "message_sources": _message_sources(body, source),
            "tool_sources": _tool_sources(body),
            "request_body": body,
            "redacted": True,
        }
        scope.calls.append(record)
        path = scope.directory / f"request-{number:02d}.json"
        atomic_json_write(path, record, mode=0o600)
        _write(scope)
        return path


def capture_main_request(agent: Any, api_kwargs: dict[str, Any], **metadata: Any) -> Path | None:
    scope = getattr(agent, "_prompt_audit_scope", None)
    if not isinstance(scope, PromptAuditScope):
        return None
    _ensure_proxy(agent, scope)
    return capture_prompt_audit_request(
        agent,
        api_kwargs,
        source="main_loop",
        metadata=metadata,
        callsite={
            "module": "agent.conversation_loop",
            "function": "pre_provider_request",
            "file": str(Path(__file__).with_name("conversation_loop.py")),
        },
    )


class _CompletionsProxy:
    def __init__(self, target: Any, agent: Any, scope: PromptAuditScope):
        self._target, self._agent, self._scope = target, agent, scope

    def create(self, *args: Any, **kwargs: Any) -> Any:
        payload = dict(kwargs)
        if args:
            payload["_positional_args"] = list(args)
        capture_prompt_audit_request(
            self._agent, payload, source="direct_agent_client", callsite=_callsite()
        )
        return self._target.create(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class _ChatProxy:
    def __init__(self, target: Any, agent: Any, scope: PromptAuditScope):
        self._target, self._agent, self._scope = target, agent, scope

    @property
    def completions(self) -> _CompletionsProxy:
        return _CompletionsProxy(self._target.completions, self._agent, self._scope)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class _ClientProxy:
    def __init__(self, target: Any, agent: Any, scope: PromptAuditScope):
        self._target, self._agent, self._scope = target, agent, scope

    @property
    def chat(self) -> _ChatProxy:
        return _ChatProxy(self._target.chat, self._agent, self._scope)

    def with_options(self, *args: Any, **kwargs: Any) -> "_ClientProxy":
        return _ClientProxy(self._target.with_options(*args, **kwargs), self._agent, self._scope)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


def _ensure_proxy(agent: Any, scope: PromptAuditScope) -> None:
    client = getattr(agent, "client", None)
    if client is not None and not isinstance(client, _ClientProxy):
        agent.client = _ClientProxy(client, agent, scope)


def _block(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    return "\n".join("    " + line for line in text.splitlines()) or "    (empty)"


def _report(scope: PromptAuditScope) -> str:
    sources = scope.system_sources or {}
    lines = [
        "# First-turn Prompt Audit", "",
        f"- Session: {scope.session_id}",
        f"- Platform: {scope.platform}",
        f"- Input modality: {scope.modality or 'unspecified'}",
        f"- Status: {scope.status}",
        f"- Captured requests: {len(scope.calls)}",
        "- Payloads are captured before dispatch and force-redacted for secrets.",
        "",
        "## Exact cached session system prompt",
        "",
        "This is the assembled base system prompt stored on the agent before the first request.",
        "",
        _block(sources.get("cached_system_prompt", "") or "(empty)"),
        "",
        "## Source map",
        "",
    ]
    for tier in ("stable", "context", "volatile", "ephemeral"):
        lines.extend([f"### {tier}", ""])
        lines.extend(f"- {hint}" for hint in (sources.get("source_hints") or {}).get(tier, []))
        value = (
            (sources.get("parts") or {}).get(tier, "")
            if tier != "ephemeral"
            else sources.get("ephemeral_system_prompt", "")
        )
        lines.extend(["", _block(value or "(empty)"), ""])
    sections = sources.get("plugin_system_prompt_sections") or []
    if sections:
        lines.extend(["## Plugin system-prompt sections", "", _block(sections), ""])
    lines.extend(["## Context source files found", ""])
    lines.extend(f"- {path}" for path in sources.get("context_paths_found", []))
    if not sources.get("context_paths_found"):
        lines.append("- None")
    lines.append("")
    for call in scope.calls:
        body = call.get("request_body") or {}
        site = call.get("callsite") or {}
        lines.extend([
            f"## Request {call['sequence']:02d}", "",
            f"- Source: {call.get('source', '')}",
            f"- Callsite: {site.get('module', '')}:{site.get('function', '')}:{site.get('line', '')}",
            f"- Model: {body.get('model', '')}", "",
        ])
        messages = body.get("messages")
        if not isinstance(messages, list):
            messages = body.get("input")
        source_by_index = {
            row.get("index"): row.get("source") for row in call.get("message_sources") or []
        }
        if isinstance(messages, list):
            for index, message in enumerate(messages):
                role = message.get("role", "") if isinstance(message, dict) else ""
                value = message.get("content") if isinstance(message, dict) else message
                serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                lines.extend([
                    f"### Message {index} - {role}", "",
                    f"Source: {source_by_index.get(index, 'unknown')}; {len(serialized):,} chars; approximately {(len(serialized)+3)//4:,} tokens.",
                    "", _block(value), "",
                ])
        tools = body.get("tools")
        if isinstance(tools, list) and tools:
            lines.extend(["### Tool definitions", ""])
            lines.extend(
                f"- {row.get('name')} - toolset {row.get('toolset')}"
                for row in call.get("tool_sources") or []
            )
            lines.extend(["", _block(tools), ""])
        other = {k: v for k, v in body.items() if k not in {"messages", "input", "tools"}}
        lines.extend(["### Other request parameters", "", _block(other), ""])
    return "\n".join(lines).rstrip() + "\n"


def _write(scope: PromptAuditScope) -> None:
    atomic_json_write(
        scope.directory / "manifest.json",
        {
            "schema_version": 1,
            "session_id": scope.session_id,
            "platform": scope.platform,
            "input_modality": scope.modality,
            "started_at": scope.started_at,
            "status": scope.status,
            "request_count": len(scope.calls),
            "request_files": [f"request-{n:02d}.json" for n in range(1, len(scope.calls) + 1)],
            "report": "prompt-report.md",
            "redacted": True,
            "first_user_message": scope.first_user_message,
            "system_sources": scope.system_sources,
        },
        mode=0o600,
    )
    atomic_write_text(scope.directory / "prompt-report.md", _report(scope), create_mode=0o600)


def finish_prompt_audit_turn(agent: Any, *, status: str = "complete") -> Path | None:
    scope = getattr(agent, "_prompt_audit_scope", None)
    if not isinstance(scope, PromptAuditScope):
        return None
    with scope.lock:
        scope.status = status
        _write(scope)
    client = getattr(agent, "client", None)
    if isinstance(client, _ClientProxy):
        agent.client = client._target
    try:
        delattr(agent, "_prompt_audit_scope")
    except AttributeError:
        pass
    atomic_json_write(
        audit_root() / "latest.json",
        {
            "schema_version": 1,
            "session_id": scope.session_id,
            "status": scope.status,
            "request_count": len(scope.calls),
            "directory": str(scope.directory),
            "report": str(scope.directory / "prompt-report.md"),
            "manifest": str(scope.directory / "manifest.json"),
        },
        mode=0o600,
    )
    return scope.directory


__all__ = [
    "arm_next_prompt_audit",
    "begin_prompt_audit_turn",
    "capture_main_request",
    "capture_prompt_audit_request",
    "finish_prompt_audit_turn",
]
