"""Image smoke: local partial stop, dedup and no Hermes delivery; no audio/IPC."""
import logging
import threading
from unittest.mock import Mock

from service import Server, context_with_hotwords


done = threading.Event()
calls = []


def stop(action):
    calls.append(action)
    done.set()
    return {'success': True, 'action': 'stopped', 'state': 'inactive'}


server = Server(logging.getLogger('media-stop-smoke'), ['Hello Intel'], 2, stop)
try:
    context = context_with_hotwords('custom-domain')
    assert context.startswith('custom-domain\n') and 'stop playback' in context and 'stop music' in context
    assert all(phrase in context for phrase in ('Hello Intel', 'Hi Intel', 'Hey Intel', 'Intel Intel'))
    server.result(101, 0, b'stop music')  # No subscriber or EOS required.
    assert done.wait(2)
    conn = Mock()
    server.arm(conn, 'recording', 'smoke', 100)
    server.result(101, 2, b'')
    server.result(101, 2, b'Hello Intel')
    conn.sendall.assert_not_called()
    assert calls == ['stop']
    server.result(102, 0, b'What time is it?')
    server.result(102, 2, b'')
    conn.sendall.assert_called_once()
    print('local media stop, dedup and following EOS: passed')
finally:
    server.close()
