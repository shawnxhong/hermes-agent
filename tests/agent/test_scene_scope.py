"""Scene isolation through real discovery, skill loading and dispatch paths."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agent.scene_scope import SceneScope, bind, check_call, for_agent


def scope(scene):
    return SceneScope('test-session', 1, scene,
                      frozenset({'home-skill', 'travel-skill'}),
                      frozenset({'scene_status', 'scene_set'}),
                      frozenset({scene + '-skill'}) if scene else frozenset(),
                      frozenset({'scene_status', 'scene_set'}) if scene == 'home' else frozenset())


@pytest.fixture
def local_tools(monkeypatch):
    from tools.registry import registry
    called = []
    for name in ('scene_status', 'scene_set'):
        monkeypatch.setitem(registry._tools, name, None)
        registry._tools.pop(name)
        registry.register(name, 'scene_test',
                          {'name': name, 'description': name, 'parameters': {
                              'type': 'object', 'properties': {}, 'additionalProperties': False}},
                          lambda args, **kw: called.append(args) or '{"ok":true}')
    return registry, called


@pytest.mark.parametrize('scene', [None, 'home', 'travel'])
def test_skill_index_list_and_load_agree(tmp_path, monkeypatch, scene):
    from tools.skills_tool import skills_list, skill_view
    from agent.prompt_builder import build_skills_system_prompt
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    for name in ('home-skill', 'travel-skill', 'general-skill'):
        folder = tmp_path / 'skills' / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: fixture\n---\n{name} body')
    selected = scope(scene)
    with bind(selected):
        index = build_skills_system_prompt(skills_dir_override=tmp_path / 'skills')
        listed = {s['name'] for s in json.loads(skills_list())['skills']}
        for name in selected.skills | {'general-skill'}:
            allowed = selected.allows_skill(name)
            assert (name in index) == allowed
            assert (name in listed) == allowed
            assert json.loads(skill_view(name, preprocess=False))['success'] == allowed
        if scene != 'home':
            assert not json.loads(skill_view('any/home-skill', preprocess=False))['success']
    # Cache and context must not leak into an unscoped channel.
    assert {'home-skill', 'travel-skill'} <= {s['name'] for s in json.loads(skills_list())['skills']}
    assert 'home-skill' in build_skills_system_prompt(skills_dir_override=tmp_path / 'skills')


def test_direct_and_bridge_dispatch_denied_before_handler(local_tools):
    from model_tools import handle_function_call
    registry, called = local_tools
    with bind(scope('travel')):
        assert 'error' in json.loads(registry.dispatch('scene_status', {}))
        assert 'error' in json.loads(handle_function_call('scene_status', {}))
        assert 'error' in json.loads(handle_function_call('tool_call', {'name': 'scene_status', 'arguments': {}}))
    assert not called
    with bind(scope('home')):
        assert json.loads(registry.dispatch('scene_status', {}))['ok']
    assert len(called) == 1


@pytest.mark.parametrize('mode', ['sequential', 'concurrent'])
def test_real_executor_propagates_scope_and_revocation(local_tools, mode):
    from run_agent import AIAgent
    import agent.tool_executor as executor
    registry, called = local_tools
    selected = scope('home')
    with bind(selected):
        agent = AIAgent(api_key='local', provider='custom', base_url='http://localhost:8000',
                        model='local', quiet_mode=True, skip_memory=True, skip_context_files=True)
    agent.valid_tool_names = {'scene_status'}
    call = NS(id='test', type='function', function=NS(name='scene_status', arguments='{}'))
    dispatch = for_agent(getattr(executor, 'execute_tool_calls_' + mode))
    messages = []
    dispatch(agent, NS(tool_calls=[call]), messages, 'test')
    assert len(called) == 1
    selected.revoked.set()
    dispatch(agent, NS(tool_calls=[call]), messages, 'test')
    assert len(called) == 1
    assert 'unavailable' in messages[-1]['content']
    # A separate IM context has no inherited CLI restriction.
    assert check_call('scene_status') is None


def test_active_schemas_bypass_bridge_without_widening_tools(local_tools, monkeypatch):
    import model_tools
    from tools.tool_search import load_config
    from dataclasses import replace
    config = replace(load_config(), enabled='on')
    monkeypatch.setattr('tools.tool_search.load_config', lambda: config)
    definitions = model_tools.get_tool_definitions(enabled_toolsets=['scene_test'], quiet_mode=True)
    names = {td['function']['name'] for td in definitions}
    assert 'tool_call' in names and 'scene_status' not in names
    # Real definition filtering uses registry toolset membership.
    with bind(scope('home')):
        definitions = model_tools.get_tool_definitions(enabled_toolsets=['scene_test'], quiet_mode=True)
        names = {td['function']['name'] for td in definitions}
        assert {'scene_status', 'scene_set'} <= names
        assert next(td for td in definitions if td['function']['name'] == 'scene_set')['function']['parameters']['additionalProperties'] is False
    with bind(scope('travel')):
        definitions = model_tools.get_tool_definitions(enabled_toolsets=['scene_test'], quiet_mode=True)
        assert not {'scene_status', 'scene_set'} & {td['function']['name'] for td in definitions}
    with bind(scope('home')):
        definitions = model_tools.get_tool_definitions(enabled_toolsets=['terminal'], quiet_mode=True)
        assert not {'scene_status', 'scene_set'} & {td['function']['name'] for td in definitions}


def test_home_plugin_registers_capabilities_without_behavior_prompt():
    path = Path(__file__).resolve().parents[2] / 'scripts/local-ovms/plugins/demo-home/__init__.py'
    spec = importlib.util.spec_from_file_location('scene_test_home_plugin', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tools, prompts = [], []
    ctx = NS(get_config=lambda key, default: default,
             register_tool=lambda **kw: tools.append(kw),
             register_system_prompt_section=lambda *a, **kw: prompts.append(a))
    module.register(ctx)
    assert len(tools) == 2 and not prompts
