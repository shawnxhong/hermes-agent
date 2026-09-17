"""Native assistant text -> sentence TTS, with no model calls or task routing."""
import logging
import re

from hermes_cli.voice_sentence_delivery import SentenceDelivery, sentences
from hermes_cli.voice_response_policy import _plain_spoken_text

log = logging.getLogger(__name__)


def spoken_text(text):
    # Do not speak a partially streamed code fence while waiting for its close.
    value = str(text or '')
    opened = None
    for match in re.finditer(r'```|~~~', value):
        if opened is None:
            opened = match
        elif match.group() == opened.group():
            opened = None
    if opened is not None:
        value = value[:opened.start()]
    return _plain_spoken_text(value)


class NativeSpeechDelivery(SentenceDelivery):
    """Request-local replay tracking; tools/think text never enter this callback.

    Already spoken text cannot be retracted. If a retry changes that prefix,
    stop speaking that request rather than replaying contradictory fragments.
    A subsequent native tool iteration starts a separate response normally.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = None
        self.raw = ''
        self.request_spoken = []
        self.diverged = False
        self.last_finished = None

    def on_stream_start(self, request_id):
        with self.lock:
            if request_id != self.request:
                self.request_spoken = []
                self.diverged = False
            self.request = request_id
            self.raw = ''
            self.last_finished = None

    def _offer(self, text, *, final=False):
        plain = spoken_text(text)
        if not final and str(text).endswith((' ', '\n', '\t')):
            plain += ' '
        complete, _ = sentences(plain, final=final)
        shared = min(len(complete), len(self.request_spoken))
        if complete[:shared] != self.request_spoken[:shared]:
            self.diverged = True
            log.warning('native_voice replay changed spoken prefix; remaining request speech suppressed')
        if self.diverged:
            return
        for sentence in complete[len(self.request_spoken):]:
            self.emit(sentence)
            self.request_spoken.append(sentence)

    def __call__(self, delta):
        with self.lock:
            if not self.active() or not isinstance(delta, str):
                return
            self.raw += delta
            self._offer(self.raw)

    def on_stream_end(self, *, final_text, finished, error=None):
        with self.lock:
            if not self.active():
                return
            if finished:
                self._offer(final_text, final=True)
                self.last_finished = spoken_text(final_text)

    def finish(self, text):
        with self.lock:
            if not self.active():
                return
            if spoken_text(text) != self.last_finished:
                # Also supports native providers that return text without deltas.
                self._offer(text, final=True)
            self.closed = True
