"""Shared SMTP timeout scope, independent of plugin import aliases."""
from contextlib import contextmanager
from contextvars import ContextVar

SMTP_CONNECT_TIMEOUT = 30
_TIMEOUT = ContextVar("voice_smtp_timeout", default=None)


@contextmanager
def smtp_connect_timeout(seconds):
    token = _TIMEOUT.set(max(1.0, float(seconds)))
    try:
        yield
    finally:
        _TIMEOUT.reset(token)


def standalone_smtp_connect_timeout():
    return _TIMEOUT.get() or SMTP_CONNECT_TIMEOUT
