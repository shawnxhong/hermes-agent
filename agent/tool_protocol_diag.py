"""Opt-in, bounded observation of local Chat Completions tool streams.

No policy, retry, cancellation, prompt or tool-dispatch decisions live here.
Attach ProtocolCapture explicitly in a diagnostic process; production defaults
to no recorder. Even explicit wire capture stores envelopes, NEVER payload text.
"""
from __future__ import annotations

import hashlib
import contextvars
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import wraps
import json
import os
import re
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urlparse


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def observe(observer, method, *args, **kwargs):
    """Isolate the ENTIRE observation, including encoding and method lookup."""
    try:
        if observer is not None:
            return getattr(observer, method)(*args, **kwargs)
    except Exception:
        return None


def _best_effort(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None
    return wrapped


def _byte_length(text):
    # Estimate malformed Unicode without altering the parameter passed onward.
    return len(text.encode('utf-8', errors='replace'))


@dataclass(frozen=True)
class ToolOrigin:
    turn_id: str
    logical_request_id: str
    attempt_id: str
    tool_index: int
    tool_id: str
    identity: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class Execution:
    origin: ToolOrigin | None
    identity: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    started: bool = False
    terminal: bool = False


# Immutable per-batch mapping propagated by the executor's existing thread
# context support. Late workers retain THEIR snapshot, not mutable agent state.
_execution_scope = contextvars.ContextVar('tool_protocol_execution_scope', default={})


def trace_tool_batch(fn):
    @wraps(fn)
    def wrapped(agent, assistant_message, *args, **kwargs):
        token = None
        try:
            cap = getattr(agent, '_tool_protocol_capture', None)
            if isinstance(cap, ProtocolCapture):
                scope = {}
                for tc in assistant_message.tool_calls:
                    key = (id(cap), cap.identity(_get(tc, 'id', '')))
                    frozen = getattr(tc, '_tool_protocol_origin', None)
                    origin = frozen[1] if (isinstance(frozen, tuple) and len(frozen) == 2
                        and frozen[0] == cap.capture_id and isinstance(frozen[1], ToolOrigin)) else cap.origin(_get(tc, 'id', ''))
                    # Duplicate IDs without unique dispatch identity are ambiguous.
                    scope[key] = Execution(origin) if key not in scope else None
                token = _execution_scope.set(scope)
        except Exception:
            pass
        try:
            return fn(agent, assistant_message, *args, **kwargs)
        finally:
            if token is not None:
                try:
                    _execution_scope.reset(token)
                except Exception:
                    pass
    return wrapped


class ProtocolCapture:
    """One private JSONL file, exclusive creation, capped at 2 MiB/4k events."""

    def __init__(self, path, *, wire=False, max_events=4000, max_bytes=2*1024*1024):
        self.path = Path(path)
        self.wire = wire
        self.max_events = min(max(1, max_events), 4000)
        self.max_bytes = min(max(1024, max_bytes), 2*1024*1024)
        self._file = os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')
        self._lock = threading.RLock()
        self._salt = os.urandom(32)
        self.capture_id = uuid.uuid4().hex
        self._start = time.monotonic()
        self.count = self.bytes = self.dropped = 0
        self._requests = {}
        self._executions = OrderedDict()
        self._origins = OrderedDict()
        self._origin_history_complete = True

    def identity(self, value):
        return hashlib.sha256(self._salt + str(value).encode('utf-8', errors='surrogatepass')).hexdigest()[:16]

    @_best_effort
    def emit(self, event, **data):
        # Callers supply allowlisted metadata, never prompts, args, results,
        # exception messages, URLs, headers or arbitrary provider extras.
        with self._lock:
            row = {'event': event, 'elapsed_ms': round((time.monotonic()-self._start)*1000, 3), **data}
            line = json.dumps(row, ensure_ascii=True, separators=(',', ':'))+'\n'
            size = len(line.encode())
            if self.count >= self.max_events or self.bytes+size > self.max_bytes:
                self.dropped += 1
                return
            self._file.write(line)
            self._file.flush()
            self.count += 1
            self.bytes += size

    def request(self, agent, kwargs):
        turn = self.identity(getattr(agent, '_current_turn_id', '') or getattr(agent, 'session_id', 'diagnostic'))
        request = getattr(agent, '_current_api_request_id', '') or uuid.uuid4().hex
        trace = RequestTrace(self, turn, self.identity(request), kwargs)
        with self._lock:
            prior = self._requests.get(turn)
            # Bounded bookkeeping, independent of log exhaustion.
            if len(self._requests) >= 128:
                self._requests.pop(next(iter(self._requests)))
            self._requests[turn] = trace
        trace.event('request_start', previous_request_id=prior.request_id if prior else None)
        return trace

    def register_origin(self, origin):
        with self._lock:
            self._origins[origin.identity] = origin
            if len(self._origins) > 512:
                self._origins.popitem(last=False)
                # Never infer uniqueness after forgetting an older reuse of an ID.
                self._origin_history_complete = False

    def origin(self, tool_call_id):
        with self._lock:
            if not self._origin_history_complete:
                return None
            matches = [o for o in self._origins.values() if o.tool_id == self.identity(tool_call_id)]
            return matches[0] if len(matches) == 1 else None

    @_best_effort
    def execution(self, agent, event, tool_call_id, *, status=None):
        tool_id = self.identity(tool_call_id)
        with self._lock:
            scope = _execution_scope.get()
            key = (id(self), tool_id)
            if key in scope:
                record = scope[key]
                origin = record.origin if record else None
            else:
                origin = self.origin(tool_call_id)
                record = None
                if origin is not None:
                    record = self._executions.setdefault(origin.identity, Execution(origin))
                    if len(self._executions) > 512:
                        self._executions.popitem(last=False)
            if record is not None:
                if event == 'execution_terminal':
                    if record.terminal:
                        return
                    record.terminal = True
                elif event == 'execution_start':
                    if record.started or record.terminal:
                        return
                    record.started = True
            self.emit(event, correlation='known' if origin else 'unknown',
                      turn_id=origin.turn_id if origin else None,
                      logical_request_id=origin.logical_request_id if origin else None,
                      attempt_id=origin.attempt_id if origin else None,
                      tool_index=origin.tool_index if origin else None,
                      tool_id=tool_id, execution_id=record.identity if record else None,
                      status=status if status in {'ok','error','success','cancelled','timeout','blocked'} else 'unknown')

    @_best_effort
    def close(self):
        with self._lock:
            self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class RequestTrace:
    def __init__(self, capture, turn, request, kwargs):
        self.capture, self.turn_id, self.request_id = capture, turn, request
        self.invocation_id = uuid.uuid4().hex[:12]
        self.known_tools = {t.get('function', {}).get('name') for t in (kwargs.get('tools') or []) if isinstance(t, dict)}
        self.tools = {}
        self._first = set()
        self._last_fragment = {}

    def event(self, event, **data):
        try:
            if type(data.get('attempt_id')) is int:
                data['attempt_id'] = f'{self.invocation_id}:{data["attempt_id"]}'
            self.capture.emit(event, turn_id=self.turn_id, logical_request_id=self.request_id, **data)
        except Exception:
            pass  # Observation must not affect inference or dispatch.

    @_best_effort
    def chunk(self, chunk, attempt, *, layer='sdk'):
        try:
            choices = _get(chunk, 'choices', []) or []
            for choice in choices:
                delta = _get(choice, 'delta', {}) or {}
                finish = _get(choice, 'finish_reason')
                if finish is not None:
                    self.event('finish_marker', attempt_id=attempt, layer=layer,
                               reason=finish if finish in {'stop','length','tool_calls','content_filter'} else 'other')
                content = _get(delta, 'content') or _get(delta, 'reasoning_content')
                if content or _get(delta, 'tool_calls'):
                    key=(attempt,layer)
                    if key not in self._first:
                        self._first.add(key)
                        self.event('first_token', attempt_id=attempt, layer=layer)
                for tc in _get(delta, 'tool_calls', []) or []:
                    fn = _get(tc, 'function', {}) or {}
                    args = _get(fn, 'arguments', '') or ''
                    index = _get(tc, 'index')
                    index = index if type(index) is int else None
                    name = _get(fn, 'name')
                    tool_id = self.capture.identity(_get(tc, 'id')) if _get(tc, 'id') else None
                    key = (attempt,layer,index)
                    fingerprint = self.capture.identity(args)
                    repeat = bool(args) and self._last_fragment.get(key)==fingerprint
                    if len(self._last_fragment)<512 or key in self._last_fragment:
                        self._last_fragment[key]=fingerprint
                    self.event('tool_delta', attempt_id=attempt, layer=layer, tool_index=index,
                               tool_id=tool_id, name=name if name in self.known_tools else ('unknown' if name else None),
                               argument_bytes=_byte_length(args), repeated_fragment=repeat)
        except Exception:
            self.event('diagnostic_decode_error', attempt_id=attempt, layer=layer)

    @_best_effort
    def slot(self, attempt, raw_index, slot, tool_id):
        key = (attempt, slot)
        if tool_id and key not in self.tools and len(self.tools)<512:
            origin = ToolOrigin(self.turn_id, self.request_id, f'{self.invocation_id}:{attempt}',
                                slot, self.capture.identity(tool_id))
            self.tools[key] = origin
            self.capture.register_origin(origin)
        self.event('assembled_slot', attempt_id=attempt, raw_index=raw_index, tool_index=slot,
                   tool_id=self.capture.identity(tool_id))

    @_best_effort
    def bind_call(self, call, attempt, slot):
        origin = self.tools.get((attempt, slot))
        if origin is not None:
            call._tool_protocol_origin = (self.capture.capture_id, origin)

    @_best_effort
    def assembled(self, attempt, calls, finish):
        for slot, tc in calls.items():
            args=tc['function']['arguments']
            try:
                valid=isinstance(json.loads(args),dict)
            except (ValueError,TypeError):
                valid=False
            self.event('assembled_arguments', attempt_id=attempt, tool_index=slot,
                       tool_id=self.capture.identity(tc['id']), argument_bytes=_byte_length(args),
                       valid_object=valid)
        self.event('assembly_end', attempt_id=attempt, missing_finish=finish is None)

    @_best_effort
    def wrap_response(self, response, attempt):
        if not self.capture.wire or response is None:
            return
        import httpx
        original = response.stream
        trace = self

        class WireTap(httpx.SyncByteStream):
            def __iter__(self):
                try:
                    decoder = BoundedSSEObserver(trace, attempt)
                except Exception:
                    decoder = None
                try:
                    for data in original:
                        observe(decoder, 'feed', data)
                        yield data  # Preserve original bytes and chunk boundaries.
                finally:
                    if decoder is not None:
                        observe(trace, 'event', 'wire_end', attempt_id=attempt,
                                unfinished_line=bool(decoder.line) or decoder.omitted or decoder.event_bytes > 0)

            def close(self):
                original.close()

        response.stream=WireTap()


class BoundedSSEObserver:
    """Bound framing memory, then use the installed SDK's event-field decoder."""

    def __init__(self, trace, attempt):
        from openai._streaming import SSEDecoder
        self.decoder_type = SSEDecoder
        self.decoder = SSEDecoder()
        self.trace, self.attempt = trace, attempt
        self.line = bytearray()
        self.event_bytes = 0
        self.omitted = self.after_cr = self.line_nonempty = False

    def _part(self, part):
        self.line_nonempty = self.line_nonempty or bool(part)
        self.event_bytes += len(part)
        if self.event_bytes > 65536 and not self.omitted:
            self.omitted = True
            self.line.clear()
            self.decoder = self.decoder_type()
            observe(self.trace, 'event', 'wire_event_omitted', attempt_id=self.attempt)
        if not self.omitted:
            self.line.extend(part)

    def _end_line(self):
        blank = not self.line_nonempty
        try:
            if not self.omitted:
                event = self.decoder.decode(self.line.decode('utf-8'))
                if event and event.data:
                    if event.data.strip() == '[DONE]':
                        observe(self.trace, 'event', 'wire_done', attempt_id=self.attempt)
                    else:
                        try:
                            value = json.loads(event.data)
                        except ValueError:
                            observe(self.trace, 'event', 'wire_invalid_json', attempt_id=self.attempt)
                        else:
                            observe(self.trace, 'chunk', value, self.attempt, layer='wire')
        except Exception:
            self.omitted = True
            self.decoder = self.decoder_type()
            observe(self.trace, 'event', 'wire_observation_error', attempt_id=self.attempt)
        finally:
            self.line.clear()
            self.line_nonempty = False
            if blank:
                self.event_bytes = 0
                self.omitted = False
                self.decoder = self.decoder_type()

    def feed(self, data):
        if not data:
            return
        if self.after_cr:
            if data.startswith(b'\n'):
                data = data[1:]
            self.after_cr = False
        offset = 0
        for match in re.finditer(b'\r\n|\r|\n', data):
            self._part(data[offset:match.start()])
            self._end_line()
            offset = match.end()
            self.after_cr = match.group() == b'\r' and offset == len(data)
        self._part(data[offset:])


@_best_effort
def begin_request(agent, kwargs):
    capture=getattr(agent,'_tool_protocol_capture',None)
    if not isinstance(capture,ProtocolCapture):return None
    if urlparse(str(getattr(agent,'base_url',''))).hostname not in {'localhost','127.0.0.1','::1'}:return None
    try:return capture.request(agent,kwargs)
    except Exception:return None


@_best_effort
def execution_event(agent,event,tool_call_id,*,status=None):
    capture=getattr(agent,'_tool_protocol_capture',None)
    if isinstance(capture,ProtocolCapture):
        try:capture.execution(agent,event,tool_call_id,status=status)
        except Exception:pass


@_best_effort
def terminal_event(agent, tool_call_id, function_name, result, status):
    if isinstance(getattr(agent, '_tool_protocol_capture', None), ProtocolCapture):
        if status is None:
            from model_tools import _tool_result_observer_fields
            status, _, _ = _tool_result_observer_fields(function_name, result)
        execution_event(agent, 'execution_terminal', tool_call_id, status=status)


@_best_effort
def retry_event(agent, *, attempt, mid_tool_call):
    capture=getattr(agent,'_tool_protocol_capture',None)
    if isinstance(capture,ProtocolCapture):
        try:
            turn=capture.identity(getattr(agent,'_current_turn_id','') or getattr(agent,'session_id','diagnostic'))
            trace=capture._requests.get(turn)
            if trace:
                trace.event('retry_scheduled',next_attempt=attempt,mid_tool_call=bool(mid_tool_call))
        except Exception:pass
