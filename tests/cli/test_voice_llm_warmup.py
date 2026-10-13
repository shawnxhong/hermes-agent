"""Discarded warmup requests must preserve the foreground conversation."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hermes_cli import voice_llm_warmup as warm


class WarmupTests(unittest.TestCase):
    def setUp(self):
        self.original_client = Mock()
        self.request_client = Mock()
        self.agent = SimpleNamespace(
            provider="custom", api_mode="chat_completions",
            base_url="http://localhost:8000/v3", model="local-model",
            client=self.original_client, _cached_system_prompt="native prompt",
            ephemeral_system_prompt="English only.",
            prefill_messages=[{"role": "user", "content": "example"},
                              {"role": "assistant", "content": "example reply"}],
            tools=[{"type": "function", "function": {"name": "terminal"}}],
            max_tokens=1024, _max_tokens_param=lambda n: {"max_tokens": n},
            stream_delta_callback=Mock(), run_conversation=Mock(),
        )
        self.agent._build_api_kwargs = lambda messages: {
            "model": self.agent.model, "messages": messages,
            "tools": self.agent.tools, "max_tokens": self.agent.max_tokens,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
        self.cli = SimpleNamespace(
            provider="custom", api_mode="chat_completions",
            base_url=self.agent.base_url, _voice_mode=True,
            _single_query_mode=False, agent=self.agent,
            conversation_history=[], _init_agent=Mock(return_value=True),
            _console_print=Mock(),
        )
        self.prepare = patch.object(warm, "_prepare_prompt")
        self.prepare_mock = self.prepare.start()
        self.factory = patch.object(warm, "_make_client", return_value=self.request_client)
        self.factory_mock = self.factory.start()
        self.addCleanup(self.prepare.stop)
        self.addCleanup(self.factory.stop)

    def run_warmup(self, **settings):
        return warm.warmup_for_cli(self.cli, {"voice": {"llm_warmup": settings}})

    def test_exact_prefix_and_tools_without_history_or_callbacks(self):
        history = [{"role": "user", "content": "prior"},
                   {"role": "assistant", "content": "answer"}]
        self.cli.conversation_history = deepcopy(history)
        tools_before = deepcopy(self.agent.tools)
        prefills_before = deepcopy(self.agent.prefill_messages)
        self.assertEqual(self.run_warmup()["status"], "ready")
        sent = self.request_client.chat.completions.create.call_args.kwargs
        self.assertEqual(sent["messages"], [
            {"role": "system", "content": "native prompt\n\nEnglish only."},
            *prefills_before, *history, {"role": "user", "content": "Hello."}])
        self.assertEqual(sent["tools"], tools_before)
        self.assertNotIn("tool_choice", sent)
        self.assertEqual(sent["max_tokens"], 1)
        self.assertIs(sent["stream"], False)
        self.assertEqual(sent["extra_body"],
                         {"chat_template_kwargs": {"enable_thinking": False}})
        self.assertGreater(sent["timeout"], 0)
        self.assertLessEqual(sent["timeout"], 90)
        self.assertEqual(self.cli.conversation_history, history)
        self.assertEqual(self.agent.prefill_messages, prefills_before)
        self.assertEqual(self.agent.tools, tools_before)
        self.assertEqual(self.agent.max_tokens, 1024)
        self.agent.stream_delta_callback.assert_not_called()
        self.agent.run_conversation.assert_not_called()
        self.original_client.close.assert_not_called()
        self.request_client.close.assert_called_once()
        self.prepare_mock.assert_called_once_with(self.agent, self.cli.conversation_history)

    def test_once_per_startup(self):
        first = self.run_warmup()
        self.assertIs(self.run_warmup(), first)
        self.cli._init_agent.assert_called_once()
        self.request_client.chat.completions.create.assert_called_once()

    def test_disabled_does_not_initialize(self):
        self.assertEqual(self.run_warmup(enabled=False)["status"], "skipped")
        self.cli._init_agent.assert_not_called()

    def test_text_or_single_query_does_not_initialize(self):
        self.cli._voice_mode = False
        self.assertEqual(self.run_warmup()["status"], "skipped")
        self.cli._voice_mode = True
        self.cli._single_query_mode = True
        self.assertEqual(self.run_warmup()["status"], "skipped")
        self.cli._init_agent.assert_not_called()

    def test_only_exact_loopback_ovms_routes(self):
        for base in ("https://example.com/v3", "http://192.168.1.2:8000/v3",
                     "http://localhost:8000/v1", "http://localhost.evil/v3",
                     "http://user:password@localhost/v3", "invalid", "http://[bad"):
            with self.subTest(base=base):
                self.cli.base_url = base
                self.assertEqual(self.run_warmup()["status"], "skipped")
        self.cli._init_agent.assert_not_called()
        for base in ("http://127.0.0.1:8000/v3/", "http://[::1]:8000/v3"):
            self.cli.base_url = base
            self.assertTrue(warm.is_local_ovms(self.cli))

    def test_other_providers_and_transports_are_skipped(self):
        self.cli.provider = "openai"
        self.assertEqual(self.run_warmup()["status"], "skipped")
        self.cli.provider = "custom"
        self.cli.api_mode = "codex_responses"
        self.assertEqual(self.run_warmup()["status"], "skipped")
        self.cli._init_agent.assert_not_called()

    def test_resolved_cloud_route_is_never_called(self):
        self.agent.base_url = "https://example.com/v3"
        self.assertEqual(self.run_warmup()["status"], "skipped")
        self.factory_mock.assert_not_called()
        self.prepare_mock.assert_not_called()

    def test_invalid_timeouts_fail_open_without_initialization(self):
        for value in (0, -1, 301, "bad", float("nan"), float("inf")):
            with self.subTest(value=value):
                self.cli._llm_warmup_result = None
                self.assertEqual(self.run_warmup(timeout_seconds=value)["status"], "failed")
        self.cli._init_agent.assert_not_called()

    def test_malformed_settings_skip(self):
        for value in (False, "bad", [], None):
            config = {"voice": {"llm_warmup": value}}
            self.assertEqual(warm.warmup_for_cli(self.cli, config)["status"], "skipped")
        self.cli._init_agent.assert_not_called()

    def test_timeout_is_remaining_budget(self):
        with patch.object(warm.time, "monotonic", side_effect=[0, 12, 13]):
            result = self.run_warmup(timeout_seconds=30)
        self.assertEqual(result["elapsed_seconds"], 13)
        self.assertEqual(self.factory_mock.call_args.args[1], 18)

    def test_expired_preparation_does_not_send_request(self):
        with patch.object(warm.time, "monotonic", side_effect=[0, 31, 31]):
            self.assertEqual(self.run_warmup(timeout_seconds=30)["status"], "failed")
        self.factory_mock.assert_not_called()

    def test_failed_initialization_does_not_send_request(self):
        self.cli._init_agent.return_value = False
        self.assertEqual(self.run_warmup()["status"], "failed")
        self.factory_mock.assert_not_called()

    def test_request_failure_is_clean_and_does_not_retry(self):
        self.request_client.chat.completions.create.side_effect = TimeoutError("private prompt")
        with self.assertLogs(warm.logger, level="WARNING") as logs:
            result = self.run_warmup()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_type"], "TimeoutError")
        self.assertNotIn("private prompt", " ".join(logs.output))
        self.assertIs(self.run_warmup(), result)
        self.request_client.chat.completions.create.assert_called_once()
        self.request_client.close.assert_called_once()

    def test_keyboard_interrupt_propagates_and_closes_only_request_client(self):
        self.request_client.chat.completions.create.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_warmup()
        self.request_client.close.assert_called_once()
        self.original_client.close.assert_not_called()

    def test_completion_token_parameter_does_not_conflict(self):
        self.agent._max_tokens_param = lambda n: {"max_completion_tokens": n}
        self.run_warmup()
        sent = self.request_client.chat.completions.create.call_args.kwargs
        self.assertNotIn("max_tokens", sent)
        self.assertEqual(sent["max_completion_tokens"], 1)


if __name__ == "__main__":
    unittest.main()
