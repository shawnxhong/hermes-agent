"""Offline wake/EOS smoke: stop phrases are ordinary text, no local shortcut."""
import json
import logging
from unittest.mock import Mock
from service import Server, load_config

config = load_config()
server = Server(logging.getLogger('wake-smoke'), config['wake']['aliases'], 2)
conn = Mock()
server.arm(conn, 'wake', None, 0)
server.result(1, 0, b'stop playback')
conn.sendall.assert_not_called()
server.result(2, 0, config['wake']['aliases'][0].encode())
assert json.loads(conn.sendall.call_args.args[0])['event'] == 'wake'
conn.reset_mock()
server.arm(conn, 'recording', 'test', 0)
server.result(3, 0, b'stop music')
server.result(3, 2, b'')
assert json.loads(conn.sendall.call_args.args[0])['event'] == 'end_recording'
print('Wake and ordinary stop-phrase EOS passed; no local media shortcut')
