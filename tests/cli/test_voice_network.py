import json
import threading
import time

import pytest

from hermes_cli.voice_network import (
    AMBIGUOUS_DELIVERY,
    CONFIGURATION,
    CONTENT_OR_POLICY,
    REMOTE_TRANSIENT,
    SUCCESS,
    TRANSPORT_UNREACHABLE,
    VoiceTurnNetworkState,
    begin_turn,
    classify_network_result,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ({"success": True, "data": {"web": []}}, SUCCESS),
        ({"error_code": "transport_unreachable", "error": "bounded"}, TRANSPORT_UNREACHABLE),
        (json.dumps({"success": False, "error": "Temporary failure in name resolution"}), TRANSPORT_UNREACHABLE),
        ({"success": False, "error": "HTTP 429"}, REMOTE_TRANSIENT),
        ({"success": False, "error": "HTTP 503"}, REMOTE_TRANSIENT),
        ({"success": False, "error": "BRAVE_SEARCH_API_KEY is not set"}, CONFIGURATION),
        ({"success": False, "error": "HTTP 407 Proxy Authentication Required"}, CONFIGURATION),
        ({"success": False, "error": "Blocked by website policy"}, CONTENT_OR_POLICY),
        ({"error": "connection reset after DATA", "delivery_stage": "data"}, AMBIGUOUS_DELIVERY),
    ],
)
def test_classifies_supported_result_shapes(value, expected):
    assert classify_network_result(value, delivery=True) == expected


def test_extract_batch_opens_only_when_every_item_is_transport_failure():
    failed = [
        {"url": "https://a", "error": "network unreachable"},
        {"url": "https://b", "error_code": "transport_unreachable"},
    ]
    partial = [failed[0], {"url": "https://b", "content": "usable evidence"}]
    assert classify_network_result({"results": failed}) == TRANSPORT_UNREACHABLE
    assert classify_network_result({"results": partial}) == SUCCESS


def test_turn_breaker_blocks_only_network_tools_and_resets_next_turn():
    state = begin_turn("voice-session")
    assert state.observe("web_search", {"error": "network unreachable"}) == TRANSPORT_UNREACHABLE
    assert state.before_tool("web_search")["action"] == "block"
    assert state.before_tool("browser_navigate")["action"] == "block"
    assert state.before_tool("send_message",{"action":"send","target":"email:a@example.com"})["action"] == "block"
    assert state.before_tool("send_message",{"action":"list"}) is None
    for local in ("read_file", "terminal", "cron", "demo_home", "local_media"):
        assert state.before_tool(local) is None
    assert begin_turn("voice-session").before_tool("web_search") is None


def test_local_browser_sidecar_refusal_does_not_claim_device_offline():
    state=begin_turn("local-browser")
    outcome=state.observe("browser_navigate",{"error":"connection refused at 127.0.0.1"},
                          {"url":"https://example.com"})
    assert outcome != TRANSPORT_UNREACHABLE
    assert state.before_tool("web_search") is None


def test_concurrent_network_calls_wait_for_first_success_then_continue():
    state=VoiceTurnNetworkState('s')
    assert state.before_tool('web_search',{'query':'first'}) is None
    result=[]
    worker=threading.Thread(target=lambda:result.append(
        state.before_tool('web_search',{'query':'second'})))
    worker.start();time.sleep(0.03)
    assert worker.is_alive()
    state.observe('web_search',{'success':True,'data':{'web':[]}})
    worker.join(timeout=1)
    assert result==[None]


def test_concurrent_network_calls_do_not_start_after_first_transport_failure():
    state=VoiceTurnNetworkState('s')
    assert state.before_tool('web_search',{'query':'first'}) is None
    result=[]
    worker=threading.Thread(target=lambda:result.append(
        state.before_tool('web_search',{'query':'second'})))
    worker.start();time.sleep(0.03)
    assert worker.is_alive()
    state.observe('web_search',{'error_code':TRANSPORT_UNREACHABLE})
    worker.join(timeout=1)
    assert result and result[0]['action']=='block'
    assert result[0]['message'].startswith('The network is unavailable for this turn.')
