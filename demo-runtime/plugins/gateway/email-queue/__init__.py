"""Minimal model surface; delivery and idempotency belong to the local service."""
import json
from pathlib import Path
import socket

from hermes_cli.email_render import normalize_email_body

SCHEMA = {
    'name': 'email_send',
    'description': 'Queue requested details for the configured default email recipients only; alternate addresses are unsupported. Supply subject and Markdown body blocks. Reuse the prepared text; do not rewrite it to fix line layout. For a new plan, prefer a compact useful draft unless a longer one is explicitly requested. Call once, then stop; queued is not delivered.',
    'parameters': {'type': 'object', 'properties': {
        'subject': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        'body': {'type': 'array', 'minItems': 1, 'maxItems': 4096,
                 'items': {'type': 'string', 'maxLength': 65536},
                 'description': 'Markdown text blocks. Existing text with real newlines can be a single item; multiline paragraphs, lists, tables and code are valid. Items are joined with newlines; empty items add blank lines. Keep a newly composed draft concise (about 150-220 words unless asked for more). For new drafts use unnumbered headings and bullet lists (-), not numbered paragraphs. Keep emphasis markers paired; never use standalone ** lines. No HTML, line-by-line conversion or format-repair narration.'}},
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
            or not isinstance(args['subject'], str) or not 0 < len(args['subject'].strip())
            or len(args['subject']) > 200
            or '\n' in args['subject'] or '\r' in args['subject']):
        return json.dumps({'status': 'rejected', 'error': 'Provide a nonempty single-line subject and Markdown body blocks. Nothing queued.'})
    try:
        body = normalize_email_body(args['body'])
    except ValueError as error:
        return json.dumps({'status': 'rejected', 'error': str(error) + ' Nothing queued.'})
    # Preserve the queue's stable string protocol and the caller's original args.
    request = {'scope': scope, 'subject': args['subject'], 'body': body}
    path = Path(get_hermes_home())/'mail-queue/service.sock'
    sent = False
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(2)
            sock.connect(str(path))
            if getattr(turn, 'closed', True):
                return json.dumps({'status': 'rejected', 'error': 'Turn ended. Nothing queued.'})
            sent = True
            sock.sendall((json.dumps(request)+'\n').encode())
            raw = sock.makefile('rb').readline(16384)
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get('status') not in {'queued', 'duplicate', 'rejected'}:
            raise ValueError('Invalid service response')
        return json.dumps(result)
    except (OSError, ValueError):
        return json.dumps({'status': 'unconfirmed' if sent else 'rejected',
                           'error': 'Local queue confirmation unavailable. Do not claim delivery or retry.'})


def finish_email_turn(*, tool_results, **kwargs):
    # A lone email result is terminal; acknowledge its actual queue status.
    # Mixed batches remain in the native loop for normal handling.
    if len(tool_results) != 1 or tool_results[0].get('name') != 'email_send':
        return None
    try:
        result = json.loads(tool_results[0].get('content', ''))
    except (ValueError, TypeError):
        return None
    if (isinstance(result, dict) and result.get('status') in {'queued', 'duplicate'}
            and isinstance(result.get('job_id'), str) and result['job_id']):
        return {'final_response': 'Your email has been queued for sending.', 'speak_final_response': True}
    if isinstance(result, dict) and result.get('status') in {'rejected', 'unconfirmed'}:
        message = ('Your email could not be queued.' if result['status'] == 'rejected'
                   else 'I could not confirm whether your email was queued.')
        return {'final_response': message, 'speak_final_response': True}
    return None


def register(ctx):
    ctx.register_tool(name='email_send', toolset='email_queue', schema=SCHEMA,
                      handler=submit, emoji='📨')
    ctx.register_hook('finish_tool_turn', finish_email_turn)
