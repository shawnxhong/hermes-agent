"""Email target validation and same-turn delivery idempotency."""

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform
from tools import send_message_tool as smt


@pytest.fixture(autouse=True)
def _clear_email_turn_sends():
    with smt._EMAIL_TURN_SEND_LOCK:
        smt._EMAIL_TURN_SENDS.clear()
    yield
    with smt._EMAIL_TURN_SEND_LOCK:
        smt._EMAIL_TURN_SENDS.clear()


def _email_config():
    email_cfg = SimpleNamespace(enabled=True, token=None, extra={})
    return SimpleNamespace(
        platforms={Platform.EMAIL: email_cfg},
        get_home_channel=lambda _platform: None,
    )


def _run_async(coro):
    return asyncio.run(coro)


def _send_args(address="user@example.com", message="Detailed report"):
    return {
        "action": "send",
        "target": f"email:{address}",
        "message": message,
    }


def _send_patches(sender):
    return (
        patch.object(smt, "prepare_send_message_platforms"),
        patch.object(smt, "_current_email_turn_identity", return_value=("s1", "t1")),
        patch("gateway.config.load_gateway_config", return_value=_email_config()),
        patch("tools.interrupt.is_interrupted", return_value=False),
        patch("model_tools._run_async", side_effect=_run_async),
        patch.object(smt, "_send_to_platform", new=sender),
        patch("gateway.mirror.mirror_to_session", return_value=False),
    )


def test_explicit_email_target_is_valid_and_malformed_address_is_not():
    assert smt._parse_target_ref("email", " User.Name+tag@example.com ") == (
        "User.Name+tag@example.com",
        None,
        True,
    )
    assert smt._parse_target_ref("email", "not-an-email") == (None, None, False)


def test_same_turn_identical_success_uses_fake_adapter_once():
    sender = AsyncMock(return_value={"success": True, "message_id": "mail-1"})
    patches = _send_patches(sender)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        first = json.loads(smt.send_message_tool(_send_args()))
        second = json.loads(smt.send_message_tool(_send_args()))

    assert first["success"] is True
    assert second["success"] is True
    assert second["duplicate"] is True
    assert sender.await_count == 1


def test_different_recipient_or_body_sends_independently():
    sender = AsyncMock(return_value={"success": True})
    patches = _send_patches(sender)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        smt.send_message_tool(_send_args())
        smt.send_message_tool(_send_args(address="other@example.com"))
        smt.send_message_tool(_send_args(message="Different report"))

    assert sender.await_count == 3


def test_failed_send_is_not_cached_and_can_retry():
    sender = AsyncMock(side_effect=[{"error": "offline"}, {"success": True}])
    patches = _send_patches(sender)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        first = json.loads(smt.send_message_tool(_send_args()))
        second = json.loads(smt.send_message_tool(_send_args()))

    assert first["error"] == "offline"
    assert second["success"] is True
    assert "duplicate" not in second
    assert sender.await_count == 2


def test_concurrent_duplicate_waits_for_real_adapter_result():
    adapter_started = threading.Event()
    release_adapter = threading.Event()

    async def fake_send(*_args, **_kwargs):
        adapter_started.set()
        assert release_adapter.wait(2)
        return {"success": True, "message_id": "mail-concurrent"}

    sender = AsyncMock(side_effect=fake_send)
    patches = _send_patches(sender)
    results = []

    def call_send():
        results.append(json.loads(smt.send_message_tool(_send_args())))

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        first = threading.Thread(target=call_send)
        second = threading.Thread(target=call_send)
        first.start()
        assert adapter_started.wait(1)
        second.start()
        release_adapter.set()
        first.join(3)
        second.join(3)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sender.await_count == 1
    assert all(item["success"] is True for item in results)
    assert sum(bool(item.get("duplicate")) for item in results) == 1
