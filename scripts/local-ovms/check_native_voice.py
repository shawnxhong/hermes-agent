#!/usr/bin/env python3
"""Isolated native OVMS acceptance. Captures TTS/mail; never performs actions."""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import tempfile
import threading
import time

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--case', choices=['greeting', 'mixed', 'travel', 'flights', 'media', 'web', 'email'], default='greeting')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    live = yaml.safe_load((args.profile/'config.yaml').read_text())
    home = Path(tempfile.mkdtemp(prefix='hermes-native-voice-'))
    cfg = {k: live[k] for k in ('tools','toolsets','web','tool_search','skills','auxiliary') if k in live}
    cfg.update(plugins={'enabled':(live.get('plugins') or {}).get('enabled', [])},
               memory={'enabled':False}, model={'context_length':65536},
               voice_delivery={'enabled':False}, travel_voice={'enabled':False})
    (home/'config.yaml').write_text(yaml.safe_dump(cfg))
    shutil.copy2(root/'SOUL.md', home/'SOUL.md')
    shutil.copytree(root/'skills', home/'skills')
    shutil.copytree(root/'scripts/local-ovms/plugins', home/'plugins', ignore=shutil.ignore_patterns('__pycache__'))
    for name, category in [('demo-home-assistant','productivity'),('local-media-player','media')]:
        shutil.copytree(root/'scripts/local-ovms/skills'/name, home/'skills'/category/name)
    os.environ['HERMES_HOME'] = str(home)
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = 'localhost,127.0.0.1'
    if args.case == 'web':
        from dotenv import dotenv_values
        key = dotenv_values(args.profile/'.env').get('BRAVE_SEARCH_API_KEY')
        if key:
            os.environ['BRAVE_SEARCH_API_KEY'] = key
        from tools.web_tools import web_search_tool
        from hermes_cli.plugins import discover_plugins
        discover_plugins()
        for query in ('site:en.wikivoyage.org San Francisco highlights',
                      'site:expedia.com New York San Francisco flights'):
            result = json.loads(web_search_tool(query, limit=2))
            print(json.dumps({'query':query, 'result':result}, ensure_ascii=True), flush=True)
        return

    from run_agent import AIAgent
    import run_agent
    from hermes_state import SessionDB
    from hermes_cli.tools_config import _get_platform_tools
    from hermes_cli.native_voice import NativeSpeechDelivery
    from hermes_cli.voice_response_policy import build_voice_turn_prefix
    from hermes_cli.plugins import get_plugin_manager
    from openai.resources.chat.completions import Completions
    from unittest.mock import patch
    # Force old presentation helpers to fail if any active path still reaches them.
    from hermes_cli import general_voice, voice_continuity_router
    def forbidden(*a, **kw):
        raise AssertionError('Retired voice router/summary was invoked')
    general_voice._summary = forbidden
    general_voice.route_task = forbidden
    voice_continuity_router.route = forbidden
    db = SessionDB(home/'state.db')
    # The isolated profile has no gateway.pid. Model the production gateway's
    # availability only; dispatch below still captures every outbound send.
    from gateway import status as gateway_status
    gateway_status.is_gateway_running = lambda: True
    agent = AIAgent(model='qwen3.6-35b-a3b', provider='custom',
                    base_url='http://localhost:8000/v3', api_key='local-ovms',
                    api_mode='chat_completions', quiet_mode=True, max_iterations=8,
                    max_tokens=1024, skip_memory=True, skip_context_files=True,
                    load_soul_identity=True,
                    platform='cli', session_db=db, enabled_toolsets=sorted(_get_platform_tools(live,'cli')))
    # Real tool discovery, schema validation and skills; only external effects
    # are fixtures. Bridge unwrapping still uses the native executor.
    calls, api_calls, mails = [], [], []
    original = run_agent.handle_function_call
    def dispatch(name, arguments, *a, **kw):
        calls.append(name)
        if name in {'skill_view','skills_list','tool_search','tool_describe'}:
            return original(name, arguments, *a, **kw)
        if name == 'demo_home_status':
            return json.dumps({'success':True,'simulated':True,'devices':[
                {'id':'master_bedroom_ac','name':'Master bedroom AC','state':'off'},
                {'id':'second_bedroom_ac','name':'Second bedroom AC','state':'off'}]})
        if name == 'send_message':
            mails.append(arguments)
            return json.dumps({'success':True,'platform':'email','test_only':True})
        if name == 'local_media':
            return json.dumps({'success':True,'state':'inactive','files':[
                {'name':'Sample Video.mp4','kind':'video'}],'test_only':True})
        if name in {'web_search','web_extract'}:
            return json.dumps({'error':'Network unavailable in this isolated fixture.'})
        return json.dumps({'error':'External action disabled in this acceptance fixture.'})
    original_create = Completions.create
    def create(client, *a, **kw):
        api_calls.append({'stream':kw.get('stream'), 'structured':bool(kw.get('response_format'))})
        return original_create(client, *a, **kw)
    cases = {
        'greeting':['How are you?'],
        'email':['Please email the following message to my default email addresses: Native email skill validation.'],
        'mixed':['How are you?', 'Are the bedroom air conditioners off?',
                 'What does RSVP mean?', 'Please email that explanation to test@example.com.'],
        'travel':['Plan three days in San Francisco. Keep it brief.', 'What is two plus two?'],
        'flights':['Find one-way flights from New York to San Francisco on October 10, 2026.'],
        'media':['List the videos on Desktop.', 'Stop the media player.'],
    }[args.case]
    history, records = [], []
    with patch.object(run_agent, 'handle_function_call', dispatch), patch.object(Completions, 'create', create):
        for text in cases:
            started = time.monotonic()
            first = []
            speech = NativeSpeechDelivery(queue.Queue(), threading.Event(),
                       echo=lambda value: first.append(time.monotonic()-started) if not first else None)
            n, t, m = len(api_calls), len(calls), len(mails)
            result = agent.run_conversation(build_voice_turn_prefix()+text,
                conversation_history=history, persist_user_message=text, input_modality='voice',
                task_id=agent.session_id, stream_callback=speech)
            speech.finish(result.get('final_response',''))
            history = result.get('messages', history)
            record = {'request':text,'response':result.get('final_response'),
                      'completed':result.get('completed'), 'failed':result.get('failed'),
                      'seconds':round(time.monotonic()-started,3),
                      'first_sentence_seconds':round(first[0],3) if first else None,
                      'api_calls':api_calls[n:],'tools':calls[t:],'mail_count':len(mails)-m,
                      'spoken':speech.committed}
            records.append(record)
            (home/'receipt.json').write_text(json.dumps(records, indent=2))
            print(json.dumps(record), flush=True)
    print('Receipt:', home/'receipt.json', flush=True)
    if args.case == 'email':
        assert len(mails) == 1, 'Expected exactly one captured email send'
        sent = json.loads(mails[0]) if isinstance(mails[0], str) else mails[0]
        assert sent.get('target') == 'email', sent
        assert sent.get('action', 'send') == 'send', sent
        assert 'Native email skill validation' in sent.get('message', ''), sent
        print('PASS: one captured send, target=email, requested body retained.', flush=True)
    print('No audio or real email/control/media operations were performed.', flush=True)


if __name__ == '__main__':
    main()
