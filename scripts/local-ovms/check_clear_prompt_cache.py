#!/usr/bin/env python3
"""Isolated real CLI/Agent/SQLite smoke; no model requests or production state."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-clear-smoke-") as root:
        home = Path(root)
        os.environ["HERMES_HOME"] = root
        os.environ["TERMINAL_CWD"] = root
        os.chdir(root)
        home.joinpath("SOUL.md").write_text("You are a concise English assistant.\n")
        home.joinpath("config.yaml").write_text(
            "model:\n  default: qwen3.6-35b-a3b\n  provider: custom\n"
            "memory:\n  memory_enabled: false\n  user_profile_enabled: false\n"
            "agent:\n  environment_probe: false\n"
            "auxiliary:\n  background_review:\n    enabled: false\n"
        )
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from cli import HermesCLI
        from run_agent import AIAgent
        from hermes_state import SessionDB
        from agent.conversation_loop import _restore_or_build_system_prompt

        cli = HermesCLI(model="qwen3.6-35b-a3b", provider="custom",
                        api_key="isolated-smoke", base_url="http://127.0.0.1:9/v1",
                        toolsets=[])
        cli._session_db = SessionDB(db_path=home / "smoke.db")
        cli._session_db.create_session(session_id=cli.session_id, source="cli", model=cli.model)
        agent = AIAgent(model=cli.model, provider="custom", api_key="isolated-smoke",
                        base_url="http://127.0.0.1:9/v1", enabled_toolsets=[],
                        disabled_toolsets=["all"], quiet_mode=True,
                        skip_context_files=True, load_soul_identity=True,
                        skip_memory=True, skip_background_review=True,
                        platform="cli", session_id=cli.session_id,
                        session_db=cli._session_db,
                        ephemeral_system_prompt="Current scene: travel")
        cli.agent = agent
        agent.session_start = cli.session_start
        _restore_or_build_system_prompt(agent, None, [])
        old_prompt = agent._cached_system_prompt
        client = agent.client
        cli.conversation_history = [{"role": "user", "content": "OLD_TASK_SENTINEL"}]
        started = time.monotonic()
        cli._clear_conversation_context()
        clear_ms = (time.monotonic() - started) * 1000
        assert cli.agent is agent and agent.client is client
        assert cli.conversation_history == []
        assert agent._cached_system_prompt == old_prompt
        assert agent.ephemeral_system_prompt == "Current scene: travel"
        assert "OLD_TASK_SENTINEL" not in agent._cached_system_prompt
        assert cli._session_db.get_session(cli.session_id)["system_prompt"] == old_prompt
        assert agent.session_api_calls == 0

        # A real file edit must be observed at the next clear boundary.
        home.joinpath("SOUL.md").write_text("You are an updated concise assistant.\n")
        cli._clear_conversation_context()
        assert "updated concise assistant" in agent._cached_system_prompt
        assert "You are a concise English assistant" not in agent._cached_system_prompt
        agent.pass_session_id = True
        previous_id = cli.session_id
        cli._clear_conversation_context()
        assert cli.session_id in agent._cached_system_prompt
        assert previous_id not in agent._cached_system_prompt
        assert agent.session_api_calls == 0
        print(json.dumps({"result": "PASS", "clear_ms": round(clear_ms, 2),
                          "model_requests": 0, "prompt_chars": len(old_prompt)}))


if __name__ == "__main__":
    main()
