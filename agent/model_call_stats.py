"""Bounded, content-free per-attempt model telemetry.

Missing provider measurements remain null, including cache misses: absent cache
usage is not evidence of zero cached tokens. No polling or request rewriting.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time

log = logging.getLogger(__name__)


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _number(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or (integer and int(value) != value):
        return None
    return int(value) if integer else float(value)


class ModelCallStats:
    def __init__(self, *, request_id, session_id, platform, model, provider, attempt,
                 clock=time.perf_counter):
        self.clock = clock
        self.started = clock()
        self.first_token = None
        self.closed = False
        self.lock = threading.Lock()
        self.record = dict(schema='hermes.model_call.v1', request_id=request_id,
                           session_id=session_id, platform=platform, model=model,
                           provider=provider, attempt=attempt,
                           provider_request_id=None, input_tokens=None,
                           output_tokens=None, total_tokens=None, queue_ms=None,
                           cached_input_tokens=None, queue_source='unavailable',
                           cache_source='unavailable')

    def observe_headers(self, headers):
        try:
            with self.lock:
                if self.closed:
                    return
                # Only allowlisted metadata; never retain authorization/URLs.
                rid = headers.get('x-request-id')
                if isinstance(rid, str) and len(rid) <= 200:
                    self.record['provider_request_id'] = rid
                raw = headers.get('x-queue-time-ms')
                if raw is not None:
                    value = _number(float(raw))
                    if value is not None:
                        self.record.update(queue_ms=value, queue_source='header:x-queue-time-ms')
        except Exception:
            pass

    def _usage(self, usage):
        if usage is None:
            return
        for key, raw in [('input_tokens', _get(usage, 'prompt_tokens')),
                         ('output_tokens', _get(usage, 'completion_tokens')),
                         ('total_tokens', _get(usage, 'total_tokens'))]:
            value = _number(raw, integer=True)
            if value is not None:
                self.record[key] = value
        cached = _number(_get(_get(usage, 'prompt_tokens_details'), 'cached_tokens'), integer=True)
        if cached is not None:
            self.record.update(cached_input_tokens=cached,
                               cache_source='usage.prompt_tokens_details.cached_tokens')
        # Explicit seconds-valued provider usage extension (not TTFT inference).
        queue = _number(_get(usage, 'queue_time'))
        if queue is not None:
            self.record.update(queue_ms=round(queue * 1000, 3), queue_source='usage.queue_time')

    def observe_chunk(self, chunk):
        try:
            now = self.clock()
            choices = _get(chunk, 'choices') or ()
            delta = _get(choices[0], 'delta') if choices else None
            token = any(isinstance(_get(delta, k), str) and bool(_get(delta, k))
                        for k in ('content', 'reasoning_content', 'reasoning'))
            if not token:
                token = any(_get(_get(call, 'function'), 'name') or
                            _get(_get(call, 'function'), 'arguments')
                            for call in (_get(delta, 'tool_calls') or ()))
            usage = _get(chunk, 'usage')
            if (not token or self.first_token is not None) and usage is None:
                return
            with self.lock:
                if self.closed:
                    return
                if token and self.first_token is None:
                    self.first_token = now
                self._usage(usage)
        except Exception:
            pass

    def finish(self, response=None, error_type=None):
        try:
            ended = self.clock()
            with self.lock:
                if self.closed:
                    return
                self.closed = True
                self._usage(_get(response, 'usage'))
                record = dict(self.record)
                record.update(status='error' if error_type else 'response', error_type=error_type,
                              duration_ms=round(max(0, ended - self.started) * 1000, 3),
                              ttft_ms=None if self.first_token is None else
                              round(max(0, self.first_token - self.started) * 1000, 3),
                              ttft_source='unavailable' if self.first_token is None else 'first_output_delta')
            log.info('model_call_stats %s', json.dumps(record, ensure_ascii=True, separators=(',', ':')))
        except Exception:
            # Telemetry must never retry, mask a provider error or break a turn.
            pass
