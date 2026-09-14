"""Media-only completion via existing native plugin APIs; no core patches."""
from collections import OrderedDict
import json
import re
import subprocess
import sys
import threading
import unicodedata

SCHEMA = {
    'name': 'local_media',
    'description': 'List Desktop media, play by filename or name fragment, stop or check this player. Startup is verified; no desktop inspection needed.',
    'parameters': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['list', 'play', 'mp3', 'mp4', 'stop', 'status', 'none']},
        'target': {'type': 'string', 'description': 'For playback: filename/title fragment copied from the user, or empty for an unspecified file. Never invent a name.'}},
        'required': ['action'], 'additionalProperties': False},
}
MEDIA = re.compile(r'\b(?:mp\s*(?:[34]|three|four)|m\s+p\s+[34]|wav|media|video|audio|music|song|movie|clip|playback|player|desktop)\b|视频|音频|音乐|歌曲|播放|桌面', re.I)
VERB = re.compile(r'\b(?:play|open|show|start|put\s+on|stop|close|quit|playing|running|status|list|ls|available)\b|播放|放一|打开|停止|关闭|正在播|列出|哪些|有什么', re.I)
BARE_STOP = re.compile(r'(?:please\s+)?(?:stop|stop it|stop playback|停止|停止播放|别放了)[.!。！ ]*', re.I)
_lock = threading.RLock()
_recent = OrderedDict()
_completed = OrderedDict()


def outside_default_scope(text):
    # Decline unrelated/compound tasks without changing the general agent.
    return bool(re.search(
        r"https?://|file://|\b(?:then|never)\b|\band\s+(?:send|write|email|search|delete|play)\b|"
        r"\b(?:do\s+not|don['’]t)\b|不要|不用|然后|并且|同时|(?:并.{0,12}(?:发|写|查))",
        text, re.I))


def _remember(mapping, key, value):
    with _lock:
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > 128:
            mapping.popitem(last=False)


def execute(action, target=''):
    if action not in ('list', 'play', 'mp3', 'mp4', 'stop', 'status'):
        return {'success': False, 'error': 'No supported media action selected.'}
    from hermes_constants import get_hermes_home
    helper = get_hermes_home() / 'skills/media/local-media-player/scripts/play_media.py'
    try:
        done = subprocess.run([sys.executable, str(helper), action, '--target', target],
                              capture_output=True, text=True, timeout=15)
        result = json.loads(done.stdout)
        if not isinstance(result, dict):
            raise ValueError('Invalid player result')
        if done.returncode or result.get('success') is not True:
            return {'success': False, 'error': result.get('error', 'Player did not confirm success.'),
                    'files': result.get('files', [])}
        result.update(workflow_complete=True, verification_scope='player process, not physical audibility',
                      next_step='Return one short confirmation. Do not call computer_use or inspect the desktop.')
        return result
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {'success': False, 'error': 'Local player could not confirm the operation. No automatic retry.'}


def reply(action, result, zh):
    names = [f['name'] for f in result.get('files', [])]
    choices = ', '.join(names[:6])
    if len(names) > 6:
        choices += ' …'
    if result.get('error') == 'ambiguous':
        return ('找到多个文件：' + choices + '。你想播放哪一个？') if zh else (
            'I found multiple files: ' + choices + '. Which one would you like?')
    if result.get('error') == 'not_found':
        return 'Desktop 上没有找到匹配的媒体文件，没有开始播放。' if zh else 'No matching media file was found on Desktop. Nothing was started.'
    if result.get('success') is not True:
        return '本地播放器未能确认操作成功，这次不再自动重试。' if zh else 'The local player could not confirm the operation. I have not retried it.'
    if action == 'list':
        if not names:
            return 'Desktop 上没有可播放的媒体文件。' if zh else 'There are no playable media files on Desktop.'
        return ('Desktop 媒体：' + choices + '。你想播放哪一个？') if zh else (
            'Desktop media: ' + choices + '. Which one would you like to play?')
    if action == 'play':
        name = result.get('file', '').rsplit('/', 1)[-1]
        return ('已开始播放 ' + name + '。') if zh else ('Playing ' + name + '.')
    if action in ('mp3', 'mp4'):
        return ('音频已开始播放。' if action == 'mp3' else '视频已开始播放。') if zh else (
            'The audio is playing.' if action == 'mp3' else 'The video is playing.')
    if action == 'stop':
        return '播放已停止。' if zh else 'Playback is stopped.'
    active = result.get('state') == 'active'
    return ('播放器正在运行。' if active else '播放器当前未运行。') if zh else (
        'The player is running.' if active else 'The player is not running.')


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
    candidate = candidate or bool(re.search(r'^\s*(?:(?:could|can|would) you\s+)?(?:please\s+)?(?:play\b|put on\b|播放)', user_message, re.I))
    if recent and recent.get('files'):
        query = normalize(user_message)
        candidate = candidate or any(query and query in normalize(f['name']) for f in recent['files'])
    if not candidate and not (recent and BARE_STOP.fullmatch(user_message.strip())):
        return None
    zh = bool(re.search(r'[\u3400-\u9fff]', user_message))
    try:
        action, target = select_action(agent, user_message, bool(recent))
    except Exception:
        return {'handled': True, 'failed': True, 'api_calls': 1,
                'final_response': '未能确认播放指令，请再说一次。' if zh else 'I could not confirm the media command. Please try again.'}
    if action == 'none':
        return None
    if agent._interrupt_requested:
        return {'handled': True, 'final_response': '', 'api_calls': 1}
    result = execute(action, target)
    _remember(_recent, str(session_id), result)
    # Native handled-result path ends this turn immediately. No second model
    # call exists that could ask for screenshots or loop on blocked GUI tools.
    return {'handled': True, 'failed': not result.get('success', False),
            'api_calls': 1, 'final_response': reply(action, result, zh)}


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


def register(ctx):
    ctx.register_tool(name='local_media', toolset='local_media', schema=SCHEMA,
                      handler=lambda args, **kw: json.dumps(execute(args.get('action'), args.get('target', '')), ensure_ascii=False), emoji='▶')
    ctx.register_hook('run_turn_workflow', workflow)
    ctx.register_hook('pre_tool_call', before_tool)
    ctx.register_hook('post_tool_call', after_tool)
