"""Narrow runtime deployments may still have the pre-rename dispatcher."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import model_tools
from agent import tool_executor as executor


@pytest.mark.parametrize('name', ['web_search', 'terminal', 'cronjob', 'process', 'tour', 'tip'])
def test_legacy_dispatcher_preserves_installed_registry_names(monkeypatch, name):
    monkeypatch.delattr(model_tools, '_LEGACY_TOOL_ALIASES')
    assert executor._resolve_tool_dispatch_name(name) == name


def test_current_dispatcher_aliases_are_preserved():
    for old, new in model_tools._LEGACY_TOOL_ALIASES.items():
        assert executor._resolve_tool_dispatch_name(old) == new


def test_legacy_todo_still_reaches_agent_owned_branch(monkeypatch):
    monkeypatch.delattr(model_tools, '_LEGACY_TOOL_ALIASES')
    assert executor._resolve_tool_dispatch_name('todo') == 'todo_list'


@pytest.mark.parametrize('dispatch', [executor.execute_tool_calls_sequential,
                                     executor.execute_tool_calls_concurrent])
def test_real_executor_can_call_tool_without_alias_table(monkeypatch, tmp_path, dispatch):
    from run_agent import AIAgent
    schema={'type':'function','function':{'name':'web_extract','description':'Test',
                                         'parameters':{'type':'object','properties':{}}}}
    with (patch('run_agent.get_tool_definitions',return_value=[schema]),
          patch('run_agent.check_toolset_requirements',return_value={}),
          patch('run_agent.OpenAI'), patch('run_agent._hermes_home',tmp_path),
          patch('agent.model_metadata.fetch_model_metadata',return_value={})):
        agent=AIAgent(api_key='test-key',base_url='http://localhost:8000/v3',
                      quiet_mode=True,skip_context_files=True,skip_memory=True)
    agent._flush_messages_to_session_db=MagicMock(return_value=True)
    agent._append_guardrail_observation=MagicMock(side_effect=lambda n,a,r,**kw:r)
    agent._record_file_mutation_result=MagicMock()
    agent._subdirectory_hints.check_tool_call=MagicMock(return_value='')
    agent._tool_result_content_for_active_model=MagicMock(side_effect=lambda n,r:r)
    # Old model_tools has no alias-table reference inside its dispatcher.
    # Exercise both real executors against that old dispatch contract.
    dispatch_stub=MagicMock(return_value='{"result":"COMPAT_TOOL_OK"}')
    agent._invoke_tool=dispatch_stub
    monkeypatch.setattr('run_agent.handle_function_call',dispatch_stub)
    monkeypatch.delattr(model_tools,'_LEGACY_TOOL_ALIASES')
    call=SimpleNamespace(id='compat-call',function=SimpleNamespace(name='web_extract',arguments='{}'))
    messages=[]
    dispatch(agent,SimpleNamespace(tool_calls=[call]),messages,'isolated-test')
    dispatch_stub.assert_called_once()
    assert any('COMPAT_TOOL_OK' in str(m.get('content')) for m in messages)
