"""Host-owned, turn-scoped network failure state for local voice turns.

The state is deliberately memory-only and never changes the model-visible tool
schema or system prompt.  Unknown failures stay unknown: only a definite
device-level transport failure opens the breaker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import threading
import time

SUCCESS = "success"
TRANSPORT_UNREACHABLE = "transport_unreachable"
REMOTE_TRANSIENT = "remote_transient"
CONFIGURATION = "configuration"
CONTENT_OR_POLICY = "content_or_policy"
AMBIGUOUS_DELIVERY = "ambiguous_delivery"
UNKNOWN = "unknown"

_KNOWN = {
    SUCCESS,
    TRANSPORT_UNREACHABLE,
    REMOTE_TRANSIENT,
    CONFIGURATION,
    CONTENT_OR_POLICY,
    AMBIGUOUS_DELIVERY,
    UNKNOWN,
}
_CONFIG = re.compile(
    r"(?:api[_ -]?key.*(?:not set|missing|required)|not configured|authentication|"
    r"unauthori[sz]ed|forbidden|invalid (?:api )?key|certificate verify|"
    r"certificate_verify_failed|ssl: certificate|tls configuration|proxy authentication|"
    r"http\s+(?:401|403|407)\b)",
    re.I,
)
_REMOTE = re.compile(
    r"(?:http\s+(?:429|5\d\d)\b|rate.?limit|too many requests|read timeout|"
    r"readtimeout|response timeout|server disconnected|connection reset|"
    r"temporar(?:y|ily) unavailable|service unavailable|bad gateway|gateway timeout)",
    re.I,
)
_POLICY = re.compile(
    r"(?:blocked by (?:website )?policy|private or internal network|invalid url|"
    r"malformed url|robots\.txt|not found|http\s+4(?:00|04|05|10)\b|"
    r"search-only backend|cannot extract|empty (?:page|content|result))",
    re.I,
)
_TRANSPORT = re.compile(
    r"(?:\boffline\b|network is unreachable|network unreachable|no route to host|"
    r"temporary failure in name resolution|name or service not known|"
    r"nodename nor servname provided|dns (?:failure|error|resolution)|"
    r"connect(?:ion)? timeout|connecttimeout|could not reach|unable to connect|"
    r"failed to connect|connection refused|proxy(?:error| connect(?:ion)? (?:failed|refused))|"
    r"errno\s*(?:-?2|101|111)|getaddrinfo failed|err_(?:internet_disconnected|"
    r"name_not_resolved|network_changed|proxy_connection_failed|connection_refused|address_unreachable))",
    re.I,
)


def _from_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return UNKNOWN
    # Misconfiguration must not become a device-offline claim merely because
    # its prose also contains words such as "connect" or "proxy".
    if _CONFIG.search(text):
        return CONFIGURATION
    if _REMOTE.search(text):
        return REMOTE_TRANSIENT
    if _POLICY.search(text):
        return CONTENT_OR_POLICY
    if _TRANSPORT.search(text):
        return TRANSPORT_UNREACHABLE
    return UNKNOWN


def classify_network_result(value: object, *, delivery: bool = False) -> str:
    """Normalize exceptions and common tool result shapes.

    A list is transport-unreachable only when every member failed that way.
    Any usable member makes the aggregate successful, preserving partial web
    extraction evidence.  ``delivery=True`` honors an adapter's DATA-stage
    ambiguity marker before generic transport prose.
    """
    if isinstance(value, BaseException):
        return _from_text(f"{type(value).__name__}: {value}")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in {"{", "["}:
            try:
                return classify_network_result(json.loads(stripped), delivery=delivery)
            except (TypeError, ValueError):
                pass
        return _from_text(stripped)
    if isinstance(value, (list, tuple)):
        if not value:
            return UNKNOWN
        outcomes = [classify_network_result(item, delivery=delivery) for item in value]
        if SUCCESS in outcomes:
            return SUCCESS
        if outcomes and all(item == TRANSPORT_UNREACHABLE for item in outcomes):
            return TRANSPORT_UNREACHABLE
        for preferred in (AMBIGUOUS_DELIVERY, CONFIGURATION, REMOTE_TRANSIENT, CONTENT_OR_POLICY):
            if preferred in outcomes:
                return preferred
        return UNKNOWN
    if not isinstance(value, dict):
        return UNKNOWN

    if isinstance(value.get("result"), (dict, list, tuple, str)):
        nested_result = classify_network_result(value["result"], delivery=delivery)
        if nested_result != UNKNOWN:
            return nested_result
    code = str(value.get("error_code") or value.get("outcome") or "").strip().casefold()
    if code in _KNOWN:
        return code
    if delivery and (value.get("delivery_stage") == "data" or value.get("accepted_unknown")):
        return AMBIGUOUS_DELIVERY
    if value.get("success") is True:
        return SUCCESS

    for key in ("results", "deliveries"):
        nested = value.get(key)
        if isinstance(nested, list):
            outcome = classify_network_result(nested, delivery=delivery)
            if outcome != UNKNOWN:
                return outcome

    if value.get("error") or value.get("success") is False:
        return _from_text(value.get("error") or value)

    # Provider result entries often omit a success flag.  Nonempty content or
    # search data is usable evidence; an otherwise clean mapping is also a
    # successful tool result rather than an outage signal.
    if value.get("content") or value.get("raw_content") or value.get("data"):
        return SUCCESS
    return SUCCESS if value else UNKNOWN


def is_network_tool(name: str, args: object = None) -> bool:
    folded = str(name or "").casefold()
    if folded in {"web_search", "web_extract"} or folded.startswith("browser_"):
        return True
    if folded == "send_message":
        payload = args if isinstance(args, dict) else {}
        return str(payload.get("action", "send")).casefold() == "send"
    return False


_BLOCK_MESSAGE = (
    "The network is unavailable for this turn. Do not try another web provider, "
    "browser action, or guessed URL. Continue with local tools or finish from "
    "stable knowledge, and do not claim current facts were verified."
)
_PENDING_MESSAGE = (
    "Another network operation is still pending for this voice turn. Do not "
    "start a duplicate connection; continue with its other result or local work."
)
_FIRST_RESULT_WAIT_SECONDS = 20.0


@dataclass
class VoiceTurnNetworkState:
    session_id: str
    offline: bool = False
    successful_tools: set[str] = field(default_factory=set)
    failed_tool: str = ""
    failure_outcome: str = UNKNOWN
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _first_result_known: bool = field(default=False, repr=False)
    _first_call_in_flight: bool = field(default=False, repr=False)
    _gate: threading.Condition = field(init=False, repr=False)

    def __post_init__(self):
        self._gate = threading.Condition(self._lock)

    def before_tool(self, name: str, args: object = None):
        if not is_network_tool(name,args):
            return None
        with self._gate:
            deadline = time.monotonic() + _FIRST_RESULT_WAIT_SECONDS
            while self._first_call_in_flight and not self._first_result_known and not self.offline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"action": "block", "message": _PENDING_MESSAGE}
                self._gate.wait(remaining)
            if self.offline and is_network_tool(name,args):
                return {"action": "block", "message": _BLOCK_MESSAGE}
            if not self._first_result_known:
                self._first_call_in_flight = True
        return None

    def observe(self, name: str, result: object, args: object = None) -> str:
        if not is_network_tool(name,args):
            return UNKNOWN
        outcome = classify_network_result(result)
        # A local browser sidecar/CDP refusal is not evidence that the device's
        # Internet transport is down.  Leave it as an ordinary browser error.
        if str(name).casefold().startswith("browser_") and outcome == TRANSPORT_UNREACHABLE:
            material = json.dumps({'args':args,'result':result},default=str).casefold()
            if re.search(r"(?:localhost|127\.0\.0\.1|\[?::1\]?)",material):
                outcome = UNKNOWN
        with self._gate:
            if outcome == SUCCESS:
                self.successful_tools.add(str(name))
            elif outcome == TRANSPORT_UNREACHABLE:
                self.offline = True
                self.failed_tool = str(name)
                self.failure_outcome = outcome
            if outcome != UNKNOWN:
                self._first_result_known = True
            self._first_call_in_flight = False
            self._gate.notify_all()
        return outcome


_lock = threading.RLock()
_turns: dict[str, VoiceTurnNetworkState] = {}


def begin_turn(session_id: object) -> VoiceTurnNetworkState:
    state = VoiceTurnNetworkState(str(session_id or ""))
    with _lock:
        _turns[state.session_id] = state
        while len(_turns) > 128:
            _turns.pop(next(iter(_turns)))
    return state


def current_turn(session_id: object) -> VoiceTurnNetworkState | None:
    with _lock:
        return _turns.get(str(session_id or ""))


def clear_turn(session_id: object) -> None:
    with _lock:
        _turns.pop(str(session_id or ""), None)
