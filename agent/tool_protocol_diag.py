"""Opt-in, bounded observation of local Chat Completions tool streams.

No policy, retry, cancellation, prompt or tool-dispatch decisions live here.
Attach ProtocolCapture explicitly in a diagnostic process; production defaults
to no recorder. Even explicit wire capture stores envelopes, NEVER payload text.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urlparse


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


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
        self._start = time.monotonic()
        self.count = self.bytes = self.dropped = 0
        self._requests = {}
        self._executions = {}

    def identity(self, value):
        return hashlib.sha256(self._salt + str(value).encode()).hexdigest()[:16]

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

    def execution(self, agent, event, tool_call_id, *, status=None):
        turn = self.identity(getattr(agent, '_current_turn_id', '') or getattr(agent, 'session_id', 'diagnostic'))
        key = (turn, self.identity(tool_call_id))
        with self._lock:
            trace = self._requests.get(turn)
            if len(self._executions) >= 512:
                self._executions.pop(next(iter(self._executions)))
            execution_id = self._executions.setdefault(key, uuid.uuid4().hex[:16])
            link = trace.tools.get(key[1], {}) if trace else {}
            self.emit(event, turn_id=turn, logical_request_id=trace.request_id if trace else None,
                      tool_id=key[1], execution_id=execution_id,
                      status=status if status in {'error','success','cancelled','timeout','blocked'} else None, **link)

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
                               argument_bytes=len(args.encode()), repeated_fragment=repeat)
        except Exception:
            self.event('diagnostic_decode_error', attempt_id=attempt, layer=layer)

    def slot(self, attempt, raw_index, slot, tool_id):
        if tool_id and len(self.tools)<512:
            self.tools[self.capture.identity(tool_id)] = {'attempt_id':f'{self.invocation_id}:{attempt}','tool_index':slot}
        self.event('assembled_slot', attempt_id=attempt, raw_index=raw_index, tool_index=slot,
                   tool_id=self.capture.identity(tool_id))

    def assembled(self, attempt, calls, finish):
        for slot, tc in calls.items():
            args=tc['function']['arguments']
            try:
                valid=isinstance(json.loads(args),dict)
            except (ValueError,TypeError):
                valid=False
            self.event('assembled_arguments', attempt_id=attempt, tool_index=slot,
                       tool_id=self.capture.identity(tc['id']), argument_bytes=len(args.encode()),
                       valid_object=valid)
        self.event('assembly_end', attempt_id=attempt, missing_finish=finish is None)

    def wrap_response(self, response, attempt):
        if not self.capture.wire or response is None:
            return
        import httpx
        original = response.stream
        trace = self

        class WireTap(httpx.SyncByteStream):
            def __iter__(self):
                buffer=b''
                discarding=False
                try:
                    for data in original:
                        # Decode individual SSE data lines without retaining request
                        # bodies, headers or raw parameter strings on disk.
                        for part in data.splitlines(keepends=True):
                            if not discarding:
                                buffer+=part
                                if len(buffer)>65536:
                                    buffer=b'';discarding=True
                                    trace.event('wire_line_omitted',attempt_id=attempt)
                            if part.endswith(b'\n'):
                                if not discarding:
                                    line=buffer.strip()
                                    if line==b'data: [DONE]':
                                        trace.event('wire_done',attempt_id=attempt)
                                    elif line.startswith(b'data:'):
                                        try:trace.chunk(json.loads(line[5:]),attempt,layer='wire')
                                        except (ValueError,UnicodeError):trace.event('wire_invalid_json',attempt_id=attempt)
                                buffer=b'';discarding=False
                        yield data  # Preserve original bytes and chunk boundaries.
                finally:
                    trace.event('wire_end',attempt_id=attempt,unfinished_line=bool(buffer) or discarding)

            def close(self):
                original.close()

        response.stream=WireTap()


def begin_request(agent, kwargs):
    capture=getattr(agent,'_tool_protocol_capture',None)
    if not isinstance(capture,ProtocolCapture):return None
    if urlparse(str(getattr(agent,'base_url',''))).hostname not in {'localhost','127.0.0.1','::1'}:return None
    try:return capture.request(agent,kwargs)
    except Exception:return None


def execution_event(agent,event,tool_call_id,*,status=None):
    capture=getattr(agent,'_tool_protocol_capture',None)
    if isinstance(capture,ProtocolCapture):
        try:capture.execution(agent,event,tool_call_id,status=status)
        except Exception:pass


def retry_event(agent, *, attempt, mid_tool_call):
    capture=getattr(agent,'_tool_protocol_capture',None)
    if isinstance(capture,ProtocolCapture):
        try:
            turn=capture.identity(getattr(agent,'_current_turn_id','') or getattr(agent,'session_id','diagnostic'))
            trace=capture._requests.get(turn)
            if trace:
                trace.event('retry_scheduled',next_attempt=attempt,mid_tool_call=bool(mid_tool_call))
        except Exception:pass
