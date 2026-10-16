"""Media-only completion via existing native plugin APIs; no core patches."""
from collections import OrderedDict
import json
import logging
import re
import subprocess
import sys
import threading
import unicodedata

SCHEMA = {
    'name': 'local_media',
    'description': 'ALWAYS call action=stop when the user asks to stop/close media, music or video. NEVER answer already stopped without a fresh stop call: another channel can restart playback. Repeated stop is safe; it stops device media and queued speech. Also list/play Desktop files.',
    'parameters': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['list', 'play', 'mp3', 'mp4', 'stop', 'status'], 'description': 'Use stop for every stop/close request, regardless of earlier results; no status check needed.'},
        'target': {'type': 'string', 'description': 'For playback: filename/title fragment copied from the user, or empty for an unspecified file. Never invent a name.'}},
        'required': ['action'], 'additionalProperties': False},
}
MEDIA = re.compile(r'\b(?:mp\s*(?:[34]|three|four)|m\s+p\s+[34]|wav|media|video|audio|music|song|movie|clip|playback|player|desktop)\b', re.I)
VERB = re.compile(r'\b(?:play|open|show|start|put\s+on|stop|close|quit|playing|running|status|list|ls|available)\b', re.I)
BARE_STOP = re.compile(r'(?:please\s+)?(?:stop|stop it|stop playback)[.! ]*', re.I)
_lock = threading.RLock()
_recent = OrderedDict()
_completed = OrderedDict()


def outside_default_scope(text):
    # Decline unrelated/compound tasks without changing the general agent.
    return bool(re.search(
        r"https?://|file://|\b(?:then|never)\b|\band\s+(?:send|write|email|search|delete|play)\b|"
        r"\b(?:do\s+not|don['’]t)\b",
        text, re.I))


def _remember(mapping, key, value):
    with _lock:
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > 128:
            mapping.popitem(last=False)


def execute(action, target=''):
    if not isinstance(action, str) or not isinstance(target, str):
        return {'success': False, 'error': 'Media action and target must be text.'}
    if action not in ('list', 'play', 'mp3', 'mp4', 'stop', 'status'):
        return {'success': False, 'error': 'No supported media action selected.'}
    try:
        from tools.playback_control import request
        result = request(action, target)
        if not isinstance(result, dict):
            raise ValueError('Invalid player result')
        if result.get('success') is not True:
            return {'success': False, 'error': result.get('error', 'Player did not confirm success.'),
                    'files': result.get('files', [])}
        result.update(workflow_complete=True, verification_scope='player process, not physical audibility',
                      next_step='Return one short confirmation. Do not call computer_use or inspect the desktop.')
        return result
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {'success': False, 'error': 'Local player could not confirm the operation. No automatic retry.'}


def reply(action, result):
    names = [f['name'] for f in result.get('files', [])]
    choices = ', '.join(names[:6])
    if len(names) > 6:
        choices += ' …'
    if result.get('error') == 'ambiguous':
        return 'I found multiple files: ' + choices + '. Which one would you like?'
    if result.get('error') == 'not_found':
        return 'No matching media file was found on Desktop. Nothing was started.'
    if result.get('success') is not True:
        return 'The local player could not confirm the operation. I have not retried it.'
    if action == 'list':
        if not names:
            return 'There are no playable media files on Desktop.'
        return 'Desktop media: ' + choices + '. Which one would you like to play?'
    if action == 'play':
        name = result.get('file', '').rsplit('/', 1)[-1]
        return 'Playing ' + name + '.'
    if action in ('mp3', 'mp4'):
        return 'The audio is playing.' if action == 'mp3' else 'The video is playing.'
    if action == 'stop':
        return 'Playback is stopped.'
    active = result.get('state') == 'active'
    return 'The player is running.' if active else 'The player is not running.'


def select_action(agent, text, recent):
    instruction = (
        'Select a local media action for this request. Use mp4 for generic video/MP4/MP four, '
        'mp3 for generic music/audio/MP3/MP three. Use play for a named local file/song/video. '
        'Use list for listing Desktop files/media or asking what is available. '
        'For play/mp3/mp4 set target to the title or filename fragment COPIED VERBATIM from the user; '
        'Exclude surrounding request words such as play, the, video, audio, song, please. '
        'Example: play the Sunrise video -> play target Sunrise; play left right audio -> play target left right. '
        'do not translate, fix spelling, replace it with a catalog filename, or drop a named target. '
        'For generic playback without any title set target to empty; the host handles ambiguity. '
        'Use stop or status when requested for local media. Use none for explanations, '
        'negated commands, hypothetical/quoted requests, URLs, '
        'requests to inspect a GUI, or combined requests containing any additional task. '
        'There are no fixed demo defaults. A name alone after a media question selects that name. '
        'Bare stop is media stop only if the preceding task was media: ' + str(recent) + '. '
        'Only select; the host executes the tool and returns the final response. No GUI verification.')
    response = agent.client.with_options(timeout=20, max_retries=0).chat.completions.create(
        model=agent.model, messages=[{'role': 'system', 'content': instruction}, {'role': 'user', 'content': text}],
        tools=[{'type': 'function', 'function': SCHEMA}],
        tool_choice={'type': 'function', 'function': {'name': 'local_media'}},
        max_tokens=160, temperature=0)
    calls = response.choices[0].message.tool_calls or []
    if len(calls) != 1 or calls[0].function.name != 'local_media':
        raise ValueError('No unique action')
    selected = json.loads(calls[0].function.arguments)
    action = selected['action']
    if action not in SCHEMA['parameters']['properties']['action']['enum']:
        raise ValueError('Invalid action')
    target = selected.get('target', '')
    if not isinstance(target, str):
        raise ValueError('Invalid target')
    if target and normalize(target) not in normalize(text):
        raise ValueError('Target must come from user request')
    return action, target


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def workflow(*, agent, user_message, session_id, **kwargs):
    if not isinstance(user_message, str):
        return None
    with _lock:
        recent = _recent.pop(str(session_id), None)
    if outside_default_scope(user_message):
        return None
    candidate = bool(MEDIA.search(user_message) and VERB.search(user_message))
    candidate = candidate or bool(re.search(r'^\s*(?:(?:could|can|would) you\s+)?(?:please\s+)?(?:play\b|put on\b)', user_message, re.I))
    if recent and recent.get('files'):
        query = normalize(user_message)
        candidate = candidate or any(query and query in normalize(f['name']) for f in recent['files'])
    if not candidate and not (recent and BARE_STOP.fullmatch(user_message.strip())):
        return None
    try:
        action, target = select_action(agent, user_message, bool(recent))
    except Exception:
        return {'handled': True, 'failed': True, 'api_calls': 1,
                'final_response': 'I could not confirm the media command. Please try again.'}
    if action == 'none':
        return None
    if agent._interrupt_requested:
        return {'handled': True, 'final_response': '', 'api_calls': 1}
    result = execute(action, target)
    _remember(_recent, str(session_id), result)
    # Native handled-result path ends this turn immediately. No second model
    # call exists that could ask for screenshots or loop on blocked GUI tools.
    return {'handled': True, 'failed': not result.get('success', False) and result.get('error') != 'ambiguous',
            'api_calls': 1, 'final_response': reply(action, result)}


def after_tool(*, session_id='', turn_id='', tool_name='', result=None, **kwargs):
    # Extra protection for native tool calls outside the explicit workflow.
    if tool_name != 'local_media' or not session_id or not turn_id:
        return
    try:
        result = json.loads(result) if isinstance(result, str) else result
        if isinstance(result, dict) and result.get('workflow_complete'):
            _remember(_completed, (session_id, turn_id), True)
    except ValueError:
        pass


def before_tool(*, session_id='', turn_id='', tool_name='', **kwargs):
    with _lock:
        done = _completed.get((session_id, turn_id), False)
    if done and tool_name == 'computer_use':
        return {'action': 'block', 'message': 'The media operation is complete and verified by the player. Return its result; desktop verification is not part of this media operation.'}


# Explicit stop policy stays inside the native model/tool loop. No selector LLM,
# alternate workflow, prompt rewrite, or changes to the conversation's toolset.
_STOP_COMMAND = re.compile(
    r'(?:(?:can|could|would|will) you\s+)?(?:please\s+)?'
    r'(?:stop|close|quit|turn off)\s+(?:playing\s+)?'
    r'(?:(?:the|this|current|all)\s+)?'
    r'(?:music|audio|video|videos|song|songs|media|playback|player)'
    r'(?:\s+(?:now|again|please))*[.!?]*', re.I)
_stop_turns = OrderedDict()
_stop_current = OrderedDict()
_log = logging.getLogger(__name__)
_STOP_UNCONFIRMED = 'I could not confirm that playback stopped. The stop operation did not succeed.'


def require_stop_tool(*, request, platform='', session_id='', turn_id='', **kwargs):
    """Force a fresh native media call for an unambiguous Feishu stop command."""
    if platform != 'feishu' or not session_id or not turn_id:
        return None
    key = (session_id, turn_id)
    with _lock:
        if _stop_current.get(session_id) == key:
            current = _stop_turns.get(key)
        else:
            _stop_current.pop(session_id, None)
            current = None
    if current is None:
        messages = request.get('messages') or []
        message = next((m for m in reversed(messages) if m.get('role') == 'user'), {})
        content = message.get('content')
        if not isinstance(content, str) or not _STOP_COMMAND.fullmatch(content.strip()):
            return None
        _remember(_stop_turns, key, {})
        _remember(_stop_current, session_id, key)
        current = {}
    if current.get('tool_call_id'):
        return None
    if not any(t.get('function', {}).get('name') == 'local_media' for t in request.get('tools') or []):
        return None  # The final response gate reports unavailable, never success.
    outgoing = dict(request)
    outgoing['tool_choice'] = {'type': 'function', 'function': {'name': 'local_media'}}
    _log.info('media_stop_policy required session=%s turn=%s', session_id, turn_id)
    return {'request': outgoing, 'source': 'demo-media', 'reason': 'explicit media stop needs a fresh tool result'}


def validate_stop_completion(*, request, next_call, session_id='', turn_id='', **kwargs):
    """Reject text-only success before native history persistence as well."""
    response = next_call(request)
    with _lock:
        active = _stop_current.get(session_id) == (session_id, turn_id)
    if active and getattr(response, 'choices', None):
        message = response.choices[0].message
        if not getattr(message, 'tool_calls', None):
            message.content = _STOP_UNCONFIRMED
            _log.warning('media_stop_policy provider_skipped_tool session=%s turn=%s', session_id, turn_id)
    return response


def constrain_stop_args(*, tool_name, args, session_id='', turn_id='', **kwargs):
    with _lock:
        active = _stop_current.get(session_id) == (session_id, turn_id)
    if active and tool_name == 'local_media':
        return {'args': {'action': 'stop'}, 'source': 'demo-media', 'reason': 'preserve explicit stop intent'}
    return None


def restrict_stop_tools(*, tool_name, session_id='', turn_id='', **kwargs):
    with _lock:
        active = _stop_current.get(session_id) == (session_id, turn_id)
    if active and tool_name != 'local_media':
        return {'action': 'block', 'message': 'This request only authorizes local_media stop.'}
    return None


def record_stop_receipt(*, tool_name, args, result, session_id='', turn_id='', tool_call_id='', **kwargs):
    key = (session_id, turn_id)
    with _lock:
        active = _stop_current.get(session_id) == key
    if not active or tool_name != 'local_media' or args.get('action') != 'stop' or not tool_call_id:
        return
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        data = None
    stopped = (isinstance(data, dict) and data.get('success') is True
               and data.get('state') == 'inactive' and data.get('action') == 'stopped')
    _remember(_stop_turns, key, {'tool_call_id': tool_call_id, 'stopped': stopped})
    _log.info('media_stop_policy receipt session=%s turn=%s stopped=%s', session_id, turn_id, stopped)


def finish_stop_tool(*, session_id='', tool_results=(), **kwargs):
    with _lock:
        receipt = _stop_turns.get(_stop_current.get(session_id), {})
    if not receipt.get('tool_call_id'):
        return None
    # The native executor must have durably recorded THIS call, not a result
    # from history, another chat or a timed-out worker.
    if not any(m.get('tool_call_id') == receipt['tool_call_id'] for m in tool_results):
        return None
    return {'final_response': 'Playback is stopped.' if receipt['stopped'] else _STOP_UNCONFIRMED}


def guard_stop_confirmation(*, response_text, platform='', session_id='', **kwargs):
    if platform != 'feishu':
        return None
    with _lock:
        key = _stop_current.pop(session_id, None)
        receipt = _stop_turns.pop(key, {}) if key else None
    if receipt is None:
        return None
    # Also catches a provider that ignores tool_choice and returns text alone.
    return 'Playback is stopped.' if receipt.get('stopped') else _STOP_UNCONFIRMED


def register(ctx):
    ctx.register_tool(name='local_media', toolset='local_media', schema=SCHEMA,
                      handler=lambda args, **kw: json.dumps(execute(args.get('action'), args.get('target', '')), ensure_ascii=False), emoji='▶')

    ctx.register_middleware('llm_request', require_stop_tool)
    ctx.register_middleware('llm_execution', validate_stop_completion)
    ctx.register_middleware('tool_request', constrain_stop_args)
    ctx.register_hook('pre_tool_call', restrict_stop_tools)
    ctx.register_hook('post_tool_call', record_stop_receipt)
    ctx.register_hook('finish_tool_turn', finish_stop_tool)
    ctx.register_hook('transform_llm_output', guard_stop_confirmation)
