#!/usr/bin/env python3
"""Offline warmup checks; --integration exercises real CLI/SDK/native loop.

The integration uses temporary state and an HTTP fixture, never the real model,
audio, private sessions, or tools. Run Python tests with scripts/run_tests.sh
when the development environment is provisioned.
"""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def integration():
    requests = []
    model = "qwen3.6-35b-a3b"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply({"object": "list", "data": [
                {"id": model, "object": "model", "created": 0,
                 "owned_by": "local", "context_length": 65536}]})

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if requests[-1].get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for delta, finish in [({"role": "assistant", "content": "Four."}, None), ({}, "stop")]:
                    chunk = {"id": "offline-fixture", "object": "chat.completion.chunk",
                             "created": 0, "model": model,
                             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            self.reply({"id": "offline-fixture", "object": "chat.completion",
                        "created": 0, "model": model,
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": "Four."}}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 1,
                                  "total_tokens": 101}})

    with tempfile.TemporaryDirectory(prefix="hermes-warmup-check-") as home:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}/v3"
            os.environ["HERMES_HOME"] = home
            config = {
                "model": {"default": model, "provider": "custom", "base_url": base,
                          "context_length": 65536},
                "providers": {"custom": {"api_key": "offline-fixture", "base_url": base}},
                "memory": {"memory_enabled": False, "user_profile_enabled": False},
                "agent": {"max_turns": 1, "system_prompt": "English only."},
                "compression": {"enabled": False},
                "auxiliary": {"background_review": {"enabled": False},
                              "title_generation": {"enabled": False}},
                "display": {"streaming": False, "tool_progress": "off"},
                "voice": {"llm_warmup": {"enabled": True, "timeout_seconds": 30}},
            }
            # JSON is valid YAML; no environment/config from production is used.
            (Path(home) / "config.yaml").write_text(json.dumps(config))
            from cli import HermesCLI
            from hermes_cli.voice_llm_warmup import warmup_for_cli

            cli = HermesCLI(model=model, provider="custom", base_url=base,
                            api_key="offline-fixture", toolsets=[], ignore_rules=True)
            cli._voice_mode = True
            cli._console_print = lambda *args: None
            result = warmup_for_cli(cli, config)
            assert result["status"] == "ready", result
            assert len(requests) == 1, "unexpected warmup request count"
            first = requests[0]
            assert cli.conversation_history == [], "warmup polluted history"
            assert not cli.agent.client.is_closed(), "foreground client was closed"
            assert first["max_tokens"] == 1
            assert first["messages"][-1]["content"] == "Hello."
            cli.agent.stream_delta_callback = None
            cli.agent.tool_gen_callback = None
            cli.agent.reasoning_callback = None
            reply = cli.agent.run_conversation(
                user_message="What is two plus two?", conversation_history=[])
            assert reply["final_response"] == "Four.", reply
            assert len(requests) == 2, "unexpected real-turn request count"
            assert first["messages"][0] == requests[1]["messages"][0], "system prefix mismatch"
            assert first.get("tools") == requests[1].get("tools"), "tools mismatch"
            assert not any(m.get("content") == "Hello." for m in reply["messages"])
            assert warmup_for_cli(cli, config) is result
            assert len(requests) == 2, "startup warmup repeated"
            cli.agent.close()
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
    print("REAL CLI/SDK/NATIVE LOOP WARMUP CHECK PASSED (HTTP fixture; no GPU timing)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--integration", action="store_true")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location(
        "warmup_checks", ROOT / "tests/cli/test_voice_llm_warmup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        return 1
    if args.integration:
        integration()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
