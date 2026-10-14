"""Bounded media policy probe; never executes playback or business tools."""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/opt/hermes/app')
spec = importlib.util.spec_from_file_location('media_plugin', '/opt/hermes/plugins/demo-media/__init__.py')
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
with patch('tools.playback_control.request', return_value={'success': True, 'state': 'inactive'}) as request:
    for _ in range(3):
        assert plugin.execute('stop')['success']
    assert request.call_count == 3
print('Packaged plugin: repeated stop dispatch passed', flush=True)

if '--live' in sys.argv:
    from openai import OpenAI
    client = OpenAI(base_url='http://127.0.0.1:8000/v3', api_key='local', timeout=90, max_retries=0)
    skill = Path('/opt/hermes/media-stop-skill.md').read_text()
    messages = [{'role': 'system', 'content': 'Respond directly and briefly in English.\n' + skill},
                {'role': 'user', 'content': 'Stop the music.'},
                {'role': 'assistant', 'content': 'Already stopped. No media is playing.'}]
    for prompt in ('Close the music please.', 'Stop the video.', 'Please close media again.'):
        messages.append({'role': 'user', 'content': prompt})
        response = client.chat.completions.create(model='qwen3.6-35b-a3b', messages=messages,
            tools=[{'type': 'function', 'function': plugin.SCHEMA}], temperature=0,
            max_tokens=256, extra_body={'chat_template_kwargs': {'enable_thinking': False}})
        choice = response.choices[0]
        calls = choice.message.tool_calls or []
        assert choice.finish_reason == 'tool_calls' and len(calls) == 1, 'Model skipped stop'
        assert calls[0].function.name == 'local_media'
        assert json.loads(calls[0].function.arguments)['action'] == 'stop'
        messages.append(choice.message.model_dump(exclude_none=True))
        messages.append({'role': 'tool', 'tool_call_id': calls[0].id,
                         'content': '{"success":true,"action":"stopped"}'})
        messages.append({'role': 'assistant', 'content': 'Playback stopped.'})
        print('Model selected stop despite prior stopped history', flush=True)
    client.close()
