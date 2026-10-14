"""Offline smoke for the compiled bridge and Python EOS gate; no model/mic."""
import json
import logging
from unittest.mock import Mock
from service import Native, Server

flag = Native.eos_flag('/opt/qwen-wake/libwake.so')
assert flag and flag & (flag - 1) == 0, 'EOS must be a single SDK flag bit'
server = Server(logging.getLogger('eos-smoke'), ['Hello Intel'], flag)
conn = Mock()
server.arm(conn, 'recording', 'smoke', 100)
server.result(99, flag, b'Hello Intel')
server.result(101, 0, b'Question containing thank you')
conn.sendall.assert_not_called()
server.result(101, flag, b'')
server.result(101, flag, b'')
conn.sendall.assert_called_once()
assert json.loads(conn.sendall.call_args.args[0])['reason'] == 'aisp_eos'
print(json.dumps({'compiled_sdk_eos_flag': flag, 'service_gate': 'passed', 'audio': 'not used'}))
