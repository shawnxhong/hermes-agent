"""Real resolver/skill API coverage with hermetic profile state."""
import json
import shutil
from pathlib import Path

import pytest
import yaml

from hermes_cli.tools_config import _get_platform_tools
from model_tools import get_tool_definitions, handle_function_call
from toolsets import resolve_toolset

DISABLED = ['document-to-action-items', 'meeting-action-items',
            'weekly-review-planning', 'docx', 'pdf', 'xlsx']


@pytest.mark.parametrize('platform', ['feishu', 'weixin', 'telegram'])
def test_readonly_platform_preserves_reads_not_management(platform):
    config = {'platform_toolsets': {platform: ['skills_readonly', 'file']}}
    enabled = _get_platform_tools(config, platform)
    assert 'skills_readonly' in enabled
    assert 'skills' not in enabled
    names = {t['function']['name'] for t in get_tool_definitions(
        enabled_toolsets=sorted(enabled), quiet_mode=True, skip_tool_search_assembly=True)}
    assert {'skills_list', 'skill_view', 'read_file', 'write_file', 'patch'} <= names
    assert 'skill_manage' not in names
    assert 'skill_manage' in resolve_toolset('skills')


def test_bridge_cannot_restore_management():
    result = handle_function_call('tool_call',
        {'name': 'skill_manage', 'arguments': {'action': 'create', 'name': 'forbidden'}},
        enabled_toolsets=['skills_readonly'])
    assert 'error' in json.loads(result)


def test_disabled_skill_catalog_and_load(monkeypatch, tmp_path):
    from tools.skills_tool import skills_list, skill_view
    home = tmp_path / 'profile'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_PLATFORM', 'feishu')
    source = Path(__file__).resolve().parents[2] / 'demo-runtime/gateway-state/skills'
    shutil.copytree(source, home / 'skills')
    disabled = DISABLED + ['maps', 'powerpoint', 'youtube-content', 'email-inbox-triage']
    (home / 'config.yaml').write_text(yaml.safe_dump({'skills': {'disabled': disabled}}))
    names = {s['name'] for s in json.loads(skills_list())['skills']}
    assert not names.intersection(DISABLED)
    assert {'travel-advisor', 'flight-search', 'home-assistant', 'email-results',
            'local-media-player', 'shopping', 'healthcare'} <= names
    for name in DISABLED:
        result = json.loads(skill_view(name))
        assert not result.get('success', False), result
    assert json.loads(skill_view('travel-advisor'))['success']
