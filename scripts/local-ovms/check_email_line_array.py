#!/usr/bin/env python3
"""Real native Hermes -> local OVMS -> tool -> isolated queue/MIME regression.

No SMTP worker, production state, real recipient or audio is used. Run in a
candidate image with a writable temporary HERMES_HOME and local OVMS access.
"""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from email import policy
from email.parser import BytesParser


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    home = Path(os.environ['HERMES_HOME'])
    home.mkdir(parents=True, exist_ok=True)
    (home/'config.yaml').write_text('model:\n  default: qwen3.6-35b-a3b\n  provider: custom\n  base_url: http://127.0.0.1:8000/v3\n  context_length: 65536\n  max_tokens: 1024\nplugins:\n  enabled: []\n')
    root = Path(os.environ.get('EMAIL_TEST_SOURCE', '/opt/hermes/app'))
    plugin_root = Path(os.environ.get('EMAIL_TEST_PLUGIN', '/opt/hermes/plugins/email-queue'))
    tool = load('email_line_tool', plugin_root/'__init__.py')
    queue = load('email_line_queue', plugin_root/'mail_queue.py')
    from tools.registry import registry
    from hermes_cli import plugins
    manager = plugins.get_plugin_manager()
    class Context:
        def register_tool(self, **kwargs):
            registry.register(**kwargs)
        def register_hook(self, name, callback):
            manager._hooks.setdefault(name, []).append(callback)
    tool.register(Context())
    from run_agent import AIAgent
    from hermes_cli.email_render import render_email
    skill = Path(os.environ.get('EMAIL_TEST_SKILL', '/opt/hermes/skills/email/email-results/SKILL.md')).read_text()
    cases = [
        'Email me a detailed Beijing to Shanghai travel plan using only these test facts: train and flight are options; Shanghai highlights include the Bund, a museum and a park. Include transport options, highlights, a two-day sample itinerary table and notes. Do not invent prices or schedules. Send the full plan, then a short acknowledgement.',
        'Email this document preserving all lines and Markdown formatting:\n# Project report\n\n## Progress\n- Design complete\n- Testing underway\n\n## Schedule\n| Stage | Status |\n| --- | --- |\n| Review | Pending |\n\n```python\nprint("hello")\n\n# keep this blank line\n```',
        'Email me a home checklist with separate sections for morning, afternoon and evening, and three bullet items in each section. Add a short final paragraph. Use email_send to queue this checklist now. No web search is needed.',
        'Email me the travel plan details. Use the plan already in our conversation.'
    ]
    store = queue.Store(home/'mail-queue')
    evidence = []
    with queue.Server(home/'mail-queue/service.sock', store, lambda:['reader@example.invalid']) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for index, prompt in enumerate(cases):
                agent = AIAgent(model='qwen3.6-35b-a3b', provider='custom',
                    base_url='http://127.0.0.1:8000/v3',api_key='local-ovms',
                    api_mode='chat_completions',quiet_mode=True,max_iterations=6,
                    max_tokens=1024,enabled_toolsets=['email_queue'],platform='cli',
                    skip_memory=True,skip_context_files=True,skip_background_review=True,
                    session_id=f'email-array-live-{index}')
                history = None
                if index == 3:
                    history = [
                        {'role':'user','content':'Make a travel plan from Beijing to Shanghai.'},
                        {'role':'assistant','content':'# Beijing to Shanghai\n\n## Transport\n- Compare train and flight options.\n\n## Day 1\n- Visit the Bund.\n\n## Day 2\n- Visit a museum and a park.\n\n## Notes\n- Check current schedules before booking.'}
                    ]
                started=time.monotonic()
                result = agent.run_conversation(prompt, system_message=skill, conversation_history=history)
                elapsed=time.monotonic()-started
                debug_out=Path(os.environ.get('EMAIL_TEST_OUTPUT',str(home)))
                debug_out.mkdir(parents=True,exist_ok=True)
                (debug_out/f'native-case-{index}.json').write_text(json.dumps(result['messages'],ensure_ascii=False,indent=2))
                calls = []
                for message in result['messages']:
                    for call in message.get('tool_calls') or []:
                        fn = call.get('function', {})
                        value = fn.get('arguments', {})
                        args = json.loads(value) if isinstance(value,str) else value
                        if fn.get('name') == 'email_send':
                            calls.append(args)
                        elif fn.get('name') == 'tool_call' and args.get('name') == 'email_send':
                            calls.append(args['arguments'])
                assert result.get('completed'), 'Native run must complete without truncation/recovery'
                assert len(calls) == 1, 'Expected one native email tool call: '+str([(m.get('role'),str(m.get('content',''))[:400]) for m in result['messages'] if m.get('role')=='tool'])
                assert isinstance(calls[0]['body'],(list,str)), 'Expected Markdown blocks or compatible text'
                with sqlite3.connect(store.path) as db:
                    rows = db.execute('select subject,body from jobs order by created').fetchall()
                assert len(rows) == index + 1, 'Native handler must enqueue once'
                subject, body = rows[-1]
                assert body.count('\n') >= 5, 'Full native chain must retain structural newlines'
                plain, html = render_email(body)
                assert '<h' in html and ('<ul' in html or '<table' in html)
                evidence.append({'case':index,'native_tool_calls':len(calls),
                    'max_tokens':1024,'elapsed_seconds':round(elapsed,3),
                    'body_kind':type(calls[0]['body']).__name__,
                    'body_array_items':len(calls[0]['body']) if isinstance(calls[0]['body'],list) else 1,'queue_body_characters':len(body),
                    'queue_body_newlines':body.count('\n'),'html_headings':html.count('<h'),
                    'html_lists':html.count('<ul'),'html_tables':html.count('<table'),
                    'api_calls':result.get('api_calls'),'turn_exit_reason':result.get('turn_exit_reason'),
                    'completed':result.get('completed')})
                print('CASE '+json.dumps(evidence[-1]),flush=True)
                out=Path(os.environ.get('EMAIL_TEST_OUTPUT',str(home)))
                out.mkdir(parents=True,exist_ok=True)
                (out/f'case-{index}.html').write_text(html)
                (out/f'case-{index}.txt').write_text(plain)
        finally:
            server.shutdown()
            thread.join(timeout=5)
    out=Path(os.environ.get('EMAIL_TEST_OUTPUT',str(home)))
    (out/'native-evidence.json').write_text(json.dumps({'smtp_sent':False,
        'production_state':False,'native_agent':True,'model':'local OVMS','cases':evidence},indent=2)+'\n')


if __name__ == '__main__':
    main()
