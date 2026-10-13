"""Warm the local OVMS prefix before interactive voice input starts.

This is a discarded inference request, not a conversation turn. Use the native
prompt and request builders; never invoke the agent loop or execute its output.
"""
from __future__ import annotations

from copy import deepcopy
import logging
import math
import time
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def is_local_ovms(runtime) -> bool:
    """Limit automatic inference to the demo's loopback OVMS API."""
    try:
        url = urlsplit(runtime.base_url or "")
        return (
            runtime.provider == "custom"
            and runtime.api_mode == "chat_completions"
            and url.scheme == "http"
            and url.hostname in {"localhost", "127.0.0.1", "::1"}
            and url.path.rstrip("/") == "/v3"
            and url.username is None
            and url.password is None
        )
    except (AttributeError, ValueError):
        return False


def _prepare_prompt(agent, history):
    from agent.conversation_loop import _restore_or_build_system_prompt

    if agent._cached_system_prompt is None:
        # The native restore/build path owns the session-start hook and prompt
        # persistence. Running it now means the first turn reuses these bytes.
        agent._ensure_db_session()
        _restore_or_build_system_prompt(agent, None, history)


def _make_client(agent, timeout):
    from openai import OpenAI

    # with_options()/copy() share the original HTTP client by default. Closing
    # such a copy would break the foreground agent's next request.
    return OpenAI(
        api_key=agent.client.api_key,
        base_url=agent.client.base_url,
        default_headers=agent.client.default_headers,
        default_query=agent.client.default_query,
        timeout=timeout,
        max_retries=0,
    )


def warmup_for_cli(cli, config) -> dict:
    """Run once, synchronously, before the CLI starts accepting voice input.

    The request uses its own SDK client with retries disabled and a bounded
    timeout. It leaves histories, callbacks, tools and inference settings alone.
    Exceptions fail open; KeyboardInterrupt still cancels startup normally.
    """
    prior = getattr(cli, "_llm_warmup_result", None)
    if prior is not None:
        return prior
    voice = config.get("voice") or {}
    settings = voice.get("llm_warmup", {}) if isinstance(voice, dict) else None
    result = {"status": "skipped", "elapsed_seconds": 0.0}
    if (
        not isinstance(settings, dict)
        or settings.get("enabled", True) is not True
        or not getattr(cli, "_voice_mode", False)
        or getattr(cli, "_single_query_mode", False)
        or not is_local_ovms(cli)
    ):
        return result

    started = time.monotonic()
    result["status"] = "warming"
    cli._llm_warmup_result = result
    client = None
    try:
        timeout = float(settings.get("timeout_seconds", 90))
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError("voice.llm_warmup.timeout_seconds must be in (0, 300]")
        cli._console_print("[dim]Preparing the local assistant…[/]")
        if not cli._init_agent():
            raise RuntimeError("agent initialization failed")
        agent = cli.agent
        # Recheck after credential/provider resolution; never warm a cloud route.
        if not is_local_ovms(agent):
            result["status"] = "skipped"
            return result
        _prepare_prompt(agent, cli.conversation_history)
        system = agent._cached_system_prompt or ""
        if agent.ephemeral_system_prompt:
            system = (system + "\n\n" + agent.ephemeral_system_prompt).strip()
        messages = [{"role": "system", "content": system}] if system else []
        messages.extend(deepcopy(agent.prefill_messages or []))
        messages.extend(deepcopy(cli.conversation_history))
        messages.append({"role": "user", "content": "Hello."})
        # Preserve tools and template-affecting reasoning settings exactly.
        # In particular, do not remove tools or set tool_choice=none: those can
        # change the rendered system prefix. Any generated call is discarded.
        kwargs = deepcopy(agent._build_api_kwargs(messages))
        kwargs.pop("max_tokens", None)
        kwargs.pop("max_completion_tokens", None)
        kwargs.update(agent._max_tokens_param(1))
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("warmup preparation exceeded its time budget")
        kwargs["timeout"] = remaining
        kwargs["stream"] = False
        client = _make_client(agent, remaining)
        client.chat.completions.create(**kwargs)
        result["status"] = "ready"
    except KeyboardInterrupt:
        result["status"] = "cancelled"
        raise
    except Exception as exc:
        result["status"] = "failed"
        # No exception body: provider errors may contain request/prompt text.
        result["error_type"] = type(exc).__name__
        logger.warning("Local LLM warmup failed (%s); continuing normally", type(exc).__name__)
        cli._console_print("[dim]Assistant preparation failed; continuing normally.[/]")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.debug("warmup client cleanup failed")
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        logger.info("local_llm_warmup status=%s elapsed_seconds=%.3f",
                    result["status"], result["elapsed_seconds"])
    return result
