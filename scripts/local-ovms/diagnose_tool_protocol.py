#!/usr/bin/env python3
"""One real, capture-only local OVMS tool request. Never dispatches tools.

Uses the supplied profile's plugin combination, CLI toolsets and real voice
prefix. Copies repository assets and selected settings to a private profile.
No mail, audio, simulator mutation, production config edit or service restart.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--wire',action='store_true',help='Include redacted SSE envelopes, never raw content.')
    parser.add_argument('--prompt',default='Could you check if there is any electric device still live open in my bedroom?')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root))
    import yaml
    live=yaml.safe_load((args.profile/'config.yaml').read_text())
    cfg={key:live[key] for key in ('tools','toolsets') if key in live}
    # Provider/plugin secrets and real delivery addresses are not copied.
    cfg['voice_delivery']={'enabled':True,'default_recipient':'capture@example.com'}
    cfg['agent']={'system_prompt':(live.get('agent') or {}).get('system_prompt','Respond only in English.')}
    cfg['model']={'default':'qwen3.6-35b-a3b','provider':'custom','base_url':'http://127.0.0.1:8000/v3'}
    cfg['providers']={'custom':{'base_url':'http://127.0.0.1:8000/v3','api_key':'local-ovms','default_model':'qwen3.6-35b-a3b'}}
    cfg['plugins']={'enabled':(live.get('plugins') or {}).get('enabled',[])}
    cfg['memory']={'enabled':False}
    profile=Path(tempfile.mkdtemp(prefix='hermes-protocol-diag-'))
    (profile/'config.yaml').write_text(yaml.safe_dump(cfg))
    shutil.copytree(root/'skills',profile/'skills')
    shutil.copytree(root/'scripts/local-ovms/plugins',profile/'plugins',ignore=shutil.ignore_patterns('__pycache__'))
    for name,category in [('demo-home-assistant','productivity'),('local-media-player','media')]:
        shutil.copytree(root/'scripts/local-ovms/skills'/name,profile/'skills'/category/name)
    os.environ['HERMES_HOME']=str(profile)
    from run_agent import AIAgent
    from hermes_cli.tools_config import _get_platform_tools
    from hermes_cli.voice_response_policy import build_voice_turn_prefix
    from agent.tool_protocol_diag import ProtocolCapture
    agent=AIAgent(model='qwen3.6-35b-a3b',provider='custom',base_url='http://127.0.0.1:8000/v3',
                  api_key='local-ovms',api_mode='chat_completions',quiet_mode=True,skip_memory=True,
                  skip_context_files=True,platform='cli',enabled_toolsets=sorted(_get_platform_tools(live,'cli')))
    agent._current_turn_id=uuid.uuid4().hex
    agent._current_api_request_id=uuid.uuid4().hex
    messages=[{'role':'system','content':agent._build_system_prompt()},
              {'role':'user','content':build_voice_turn_prefix(followup_enabled=True)+args.prompt}]
    kwargs=agent._build_api_kwargs(messages)
    kwargs['max_tokens']=512  # Probe cap only; never alters a production setting.
    path=profile/'protocol.jsonl'
    with ProtocolCapture(path,wire=args.wire) as capture:
        agent._tool_protocol_capture=capture
        response=agent._interruptible_streaming_api_call(kwargs)
        result={'finish_reason':response.choices[0].finish_reason,
                'tool_calls':len(response.choices[0].message.tool_calls or []),
                'events':capture.count,'dropped_events':capture.dropped,
                'executed_tools':0,'trace':str(path)}
    print(json.dumps(result,indent=2))
    print('Single-request protocol probe only; not full conversation acceptance.')


if __name__=='__main__':
    main()
