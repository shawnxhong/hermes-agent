#!/usr/bin/env python3
"""Offline acceptance of the packaged Gateway capability profile, no private mounts."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    import yaml
    template = json.loads(Path('/opt/hermes/im-profile.json').read_text())
    with tempfile.TemporaryDirectory(prefix='im-scope-check-') as directory:
        home = Path(directory)
        os.environ['HERMES_HOME'] = directory
        os.environ['HERMES_PLATFORM'] = 'feishu'
        # Only behavior fields. Do not turn private binding placeholders into credentials.
        cfg = {k: template[k] for k in ('skills', 'platform_toolsets', 'agent')}
        (home/'config.yaml').write_text(yaml.safe_dump(cfg))
        shutil.copytree(ROOT/'demo-runtime/gateway-state/skills', home/'skills')
        from hermes_cli.tools_config import _get_platform_tools
        from tools.skills_tool import skills_list, skill_view
        from model_tools import get_tool_definitions, handle_function_call
        disabled = {'document-to-action-items','meeting-action-items','weekly-review-planning','docx','pdf','xlsx'}
        names = {s['name'] for s in json.loads(skills_list())['skills']}
        expected = {'travel-advisor','flight-search','healthcare','shopping','home-assistant','local-media-player','email-results'}
        assert names == expected, names
        for name in disabled:
            assert not json.loads(skill_view(name)).get('success', False)
        for platform in ('feishu','weixin','telegram'):
            enabled = _get_platform_tools(cfg, platform)
            assert 'skills_readonly' in enabled and 'skills' not in enabled
            defs = get_tool_definitions(enabled_toolsets=sorted(enabled),
                disabled_toolsets=cfg['agent']['disabled_toolsets'],quiet_mode=True)
            tools = {d['function']['name'] for d in defs}
            assert {'skill_view','skills_list','read_file','write_file','patch','search_files'} <= tools
            assert 'skill_manage' not in tools
            result = handle_function_call('tool_call', {'name':'skill_manage','arguments':{}},
                                          enabled_toolsets=sorted(enabled))
            assert 'error' in json.loads(result)
        print('IM capability smoke passed: 7 skills; read-only skill toolset; file tools retained.')


if __name__ == '__main__':
    main()
