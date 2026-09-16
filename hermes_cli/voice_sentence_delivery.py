"""Voice-only final-summary delivery; never receives native/tool deltas."""
from contextvars import ContextVar
import json
import logging
import re
import threading
import time
import uuid

current_delivery = ContextVar('voice_sentence_delivery', default=None)
log = logging.getLogger(__name__)
FAILURE = "I couldn't finish the spoken summary."
_ABBREVIATION = re.compile(r'(?:\b(?:Mr|Mrs|Ms|Dr|Prof|St|vs|etc|e\.g|i\.e)|\b[A-Z]|(?:\b[A-Za-z]\.)+[A-Za-z])\.$', re.I)


def sentences(text, *, final=False):
    """Return complete sentences plus untouched tail; never split decimal/initials."""
    parts, start = [], 0
    for match in re.finditer(r'[.!?]+[\"\u201d\u2019\)]*(?=\s|$)', text):
        end = match.end()
        if end == len(text) and not final:
            continue  # Next token may turn a trailing decimal point into a number.
        candidate = text[start:end].strip()
        if not candidate or _ABBREVIATION.search(candidate):
            continue
        parts.append(candidate)
        start = end
    tail = text[start:].strip()
    if final and tail:
        parts.append(tail)
        tail = ''
    return parts, tail


def decoded_summary_prefix(raw):
    """Decode only a leading JSON summary string; incomplete escapes stay buffered."""
    match = re.match(r'\s*\{\s*"summary"\s*:\s*"', raw)
    if not match:
        return ''
    index = match.end()
    output = []
    escapes = {'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}
    while index < len(raw):
        char = raw[index]
        if char == '"':
            return ''.join(output)
        if char == '\\':
            if index + 1 >= len(raw):
                break
            escaped = raw[index + 1]
            if escaped == 'u':
                width = 6
                if index + width > len(raw):
                    break
                number = int(raw[index + 2:index + 6], 16)
                if 0xD800 <= number <= 0xDBFF:
                    width = 12
                    if index + width > len(raw):
                        break
                value = json.loads('"' + raw[index:index + width] + '"')
                if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
                    raise ValueError('Invalid Unicode in summary')
                output.append(value)
                index += width
                continue
            if escaped not in escapes:
                raise ValueError('Invalid JSON escape')
            output.append(escapes[escaped])
            index += 2
            continue
        if ord(char) < 32:
            raise ValueError('Invalid JSON string')
        output.append(char)
        index += 1
    return ''.join(output)


class SentenceDelivery:
    """One turn's queue owner, independent of other CLI/IM conversations."""
    def __init__(self, queue, stop, valid=lambda: True, echo=lambda text: None):
        self.queue, self.stop, self.valid, self.echo = queue, stop, valid, echo
        self.id = uuid.uuid4().hex[:12]
        self.started = time.monotonic()
        self.committed = []
        self.states = {}
        self.claimed = False
        self.closed = False
        self.lock = threading.RLock()

    def active(self):
        return not self.closed and not self.stop.is_set() and self.valid()

    def claim(self):
        with self.lock:
            if self.claimed or not self.active():
                return False
            self.claimed = True
            return True

    def emit(self, text):
        from tools.tts_tool import ImmediateTTSUtterance
        with self.lock:
            if not self.active():
                raise RuntimeError('Voice delivery cancelled')
            seq = len(self.committed) + 1
            self.committed.append(text)
            self.echo(text)
            def state(value):
                with self.lock:
                    self.states[seq] = value
                log.info('voice_segment turn=%s seq=%d state=%s elapsed=%.3f',
                         self.id, seq, value, time.monotonic() - self.started)
            item = ImmediateTTSUtterance(text)
            item.on_state = state
            item.is_current = lambda: not self.stop.is_set() and self.valid()
            state('submitted')
            self.queue.put(item)

    def finish(self, text):
        """Only queue the suffix; never replay already committed summary sentences."""
        with self.lock:
            if not self.active():
                return
            prefix = ' '.join(self.committed)
            if prefix:
                if text.startswith(prefix):
                    text = text[len(prefix):].strip()
                else:
                    text = FAILURE
            for sentence in sentences(text, final=True)[0]:
                self.emit(sentence)
            self.closed = True

    def cancel_pending(self):
        with self.lock:
            self.closed = True
            for seq, state in list(self.states.items()):
                if state not in {'played', 'failed', 'duplicate_skipped', 'cancelled'}:
                    self.states[seq] = 'cancelled'
                    log.info('voice_segment turn=%s seq=%d state=cancelled elapsed=%.3f',
                             self.id, seq, time.monotonic() - self.started)


def make_delivery(cli, queue, stop):
    """Opt-in only for an actual voice queue, never keyboard or gateway."""
    from hermes_cli.config import load_config
    from hermes_cli.voice_scenes import generation
    voice = load_config().get('voice', {})
    setting = voice.get('sentence_pipeline', {}) if isinstance(voice, dict) else {}
    if not isinstance(setting, dict) or not setting.get('enabled', False):
        return None
    if queue is None or stop is None:
        return None
    scene = generation(cli)
    session = cli.session_id
    def valid():
        return (generation(cli) == scene and cli.session_id == session
                and getattr(cli, '_voice_turn_tts_queue', None) is queue)
    def echo(text):
        cli._voice_last_tts_text = (cli._voice_last_tts_text or '') + text
    return SentenceDelivery(queue, stop, valid, echo)


def stream_summary(create, kwargs, delivery, validate, cancelled):
    """Same completion/prompt, streamed once; only validated complete sentences escape."""
    raw, finish_reason, stream = '', None, None
    try:
        stream = create(**kwargs, stream=True)
        for chunk in stream:
            if cancelled() or not delivery.active():
                raise RuntimeError('Summary cancelled')
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if getattr(choice.delta, 'tool_calls', None):
                raise ValueError('Unexpected summary tool call')
            raw += choice.delta.content or ''
            if len(raw) > 8192:
                raise ValueError('Oversized summary')
            finish_reason = choice.finish_reason or finish_reason
            prefix = decoded_summary_prefix(raw)
            complete, _ = sentences(prefix)
            # Commit first sentence only until the full JSON and finish reason pass.
            if complete and not delivery.committed:
                validate(complete[0])
                from hermes_cli.voice_response_policy import prepare_voice_tts_text
                spoken = prepare_voice_tts_text(complete[0])
                if not spoken:
                    raise ValueError('Empty spoken summary')
                delivery.emit(spoken)
        if finish_reason != 'stop':
            raise ValueError('Incomplete summary')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON field')
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(value, dict) or set(value) != {'summary'}:
            raise ValueError('Invalid summary object')
        summary = value['summary']
        validate(summary)
        if cancelled() or not delivery.active():
            raise RuntimeError('Summary cancelled')
        # Remaining sentences go through final enqueue, including host delivery status.
        return summary
    except Exception:
        if cancelled() or not delivery.active() or not delivery.committed:
            raise
        log.warning('Spoken summary failed after first sentence; no replay')
        return ' '.join(delivery.committed) + ' ' + FAILURE
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                log.debug('Summary stream close failed', exc_info=True)
