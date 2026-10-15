"""Isolated Gateway media contract checks; never executes real tools."""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/opt/hermes/app')
spec = importlib.util.spec_from_file_location('media_policy', '/opt/hermes/plugins/demo-media/__init__.py')
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
soul = Path('/opt/hermes/gateway-media-SOUL.md').read_text()
assert 'in the current\nturn' in soul
assert 'even if an earlier stop succeeded' in soul
assert 'Only an explicit request to send email' in soul
with patch('tools.playback_control.request', return_value={'success': True, 'state': 'inactive'}) as request:
    for action in ('mp3', 'stop', 'mp4', 'stop', 'stop'):
        assert plugin.execute(action)['success']
    assert [c.args[0] for c in request.call_args_list] == ['mp3', 'stop', 'mp4', 'stop', 'stop']
with patch('tools.playback_control.request', return_value={'success': False, 'error': 'fixture failure'}):
    result = plugin.execute('stop')
    assert not result['success']
    assert 'could not confirm' in plugin.reply('stop', result)
with patch('tools.playback_control.request', side_effect=OSError('fixture unavailable')):
    assert not plugin.execute('stop')['success']
print('Gateway policy and installed media dispatch: PASS (7 mocked operations)', flush=True)

if '--live' in sys.argv:
    # Generate the full tool catalog/system prompt from isolated copies of the
    # production configuration and skills. Never run the agent loop or tools.
    import yaml
    from hermes_constants import get_hermes_home
    from run_agent import AIAgent
    from openai import OpenAI
    config = yaml.safe_load((get_hermes_home() / 'config.yaml').read_text())
    agent = AIAgent(model='qwen3.6-35b-a3b', base_url='http://127.0.0.1:8000/v3',
                    api_key='local', platform='feishu', quiet_mode=True,
                    enabled_toolsets=config['platform_toolsets']['feishu'],
                    disabled_toolsets=config['agent'].get('disabled_toolsets', []),
                    max_iterations=1, max_tokens=256)
    prompt = agent._build_system_prompt()
    assert 'even if an earlier stop succeeded' in prompt
    tools = agent.tools
    names = [t['function']['name'] for t in tools]
    assert {'local_media', 'email_send', 'demo_home_status'} <= set(names), names
    client = OpenAI(base_url='http://127.0.0.1:8000/v3', api_key='local', timeout=120, max_retries=0)
    messages = [{'role': 'system', 'content': prompt}]
    for text in ('Stop the music.', 'Stop the video.', 'Stop playback again.'):
        messages.append({'role': 'user', 'content': text})
        choice = client.chat.completions.create(model='qwen3.6-35b-a3b', messages=messages,
            tools=tools, temperature=0, max_tokens=256,
            extra_body={'chat_template_kwargs': {'enable_thinking': False}}).choices[0]
        calls = choice.message.tool_calls or []
        assert choice.finish_reason == 'tool_calls' and len(calls) == 1, (text, choice.finish_reason)
        assert calls[0].function.name == 'local_media', calls[0].function.name
        assert json.loads(calls[0].function.arguments)['action'] == 'stop'
        messages.append(choice.message.model_dump(exclude_none=True))
        messages.append({'role': 'tool', 'tool_call_id': calls[0].id,
                         'content': '{"success":true,"state":"inactive"}'})
        messages.append({'role': 'assistant', 'content': 'Playback stopped.'})
        print('Full Gateway schema model probe: fresh stop selected', flush=True)
    client.close()
