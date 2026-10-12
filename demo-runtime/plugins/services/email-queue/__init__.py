"""Minimal model surface; delivery and idempotency belong to the local service."""
import json
from pathlib import Path
import socket

SCHEMA = {
    'name': 'email_send',
    'description': 'Queue requested details for the configured default email recipients only; alternate addresses are unsupported. Supply only subject and body. Call once; queued is not delivered. Do not poll or retry.',
    'parameters': {'type': 'object', 'properties': {
        'subject': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        'body': {'type': 'string', 'minLength': 1, 'maxLength': 65536}},
        'required': ['subject', 'body'], 'additionalProperties': False},
}


def submit(args, **kwargs):
    from agent.relay_runtime import current_turn
    from hermes_constants import get_hermes_home
    turn = current_turn()
    lease = getattr(turn, 'lease', None)
    scope = [getattr(lease, 'profile_key', ''), getattr(lease, 'session_id', ''),
             getattr(turn, 'turn_id', '')]
    if (turn is None or getattr(turn, 'closed', True)
            or getattr(lease, 'released', False)
            or not all(isinstance(value, str) and value for value in scope)):
        return json.dumps({'status': 'rejected', 'error': 'No active turn identity. Nothing queued. Do not retry.'})
    if (not isinstance(args, dict) or set(args) != {'subject', 'body'}
            or not all(isinstance(args[k], str) and args[k].strip() for k in args)
            or len(args['subject']) > 200 or len(args['body']) > 65536
            or '\n' in args['subject'] or '\r' in args['subject']):
        return json.dumps({'status': 'rejected', 'error': 'Provide a nonempty single-line subject and text body only. Nothing queued.'})
    path = Path(get_hermes_home())/'mail-queue/service.sock'
    sent = False
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(2)
            sock.connect(str(path))
            if getattr(turn, 'closed', True):
                return json.dumps({'status': 'rejected', 'error': 'Turn ended. Nothing queued.'})
            sent = True
            sock.sendall((json.dumps({'scope': scope, **args})+'\n').encode())
            raw = sock.makefile('rb').readline(16384)
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get('status') not in {'queued', 'duplicate', 'rejected'}:
            raise ValueError('Invalid service response')
        return json.dumps(result)
    except (OSError, ValueError):
        return json.dumps({'status': 'unconfirmed' if sent else 'rejected',
                           'error': 'Local queue confirmation unavailable. Do not claim delivery or retry.'})


def register(ctx):
    ctx.register_tool(name='email_send', toolset='email_queue', schema=SCHEMA,
                      handler=submit, emoji='📨')
