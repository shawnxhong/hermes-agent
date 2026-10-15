"""Trusted tool-completion policies use normal native persistence/finalization."""
from unittest.mock import Mock
import pytest
from openai.types.chat import ChatCompletion


def response(text='', tools=None):
    return ChatCompletion.model_validate({
        'id':'completion','created':1,'object':'chat.completion','model':'local-test',
        'choices':[{'index':0,'finish_reason':'tool_calls' if tools else 'stop',
                    'message':{'role':'assistant','content':text,'tool_calls':tools}}],
        'usage':{'prompt_tokens':20,'completion_tokens':20,'total_tokens':40}})


@pytest.fixture
def native(tmp_path, monkeypatch):
    from hermes_cli import plugins
    from hermes_state import SessionDB
    from run_agent import AIAgent
    from tools import web_tools
    monkeypatch.setattr('agent.title_generator.maybe_auto_title',lambda *a,**kw:None)
    manager=plugins.PluginManager();monkeypatch.setattr(plugins,'_plugin_manager',manager)
    db=SessionDB(tmp_path/'sessions.db')
    agent=AIAgent(model='local-test',provider='custom',base_url='http://127.0.0.1:1/v1',api_key='test',
        api_mode='chat_completions',quiet_mode=True,max_iterations=6,enabled_toolsets=['web'],
        skip_memory=True,skip_context_files=True,skip_background_review=True,
        session_db=db,session_id='tool-completion',platform='cli')
    agent.client=Mock(spec=['chat','is_closed','close']);agent.client.is_closed=False
    calls=[{'id':'call-search','type':'function','function':{'name':'web_search','arguments':'{"query":"example"}'}}]
    agent.client.chat.completions.create=Mock(side_effect=[response(tools=calls),response('Normal answer.')])
    monkeypatch.setattr(web_tools,'web_search_tool',lambda **kw:'{"results":[]}')
    return agent,manager,db


def test_policy_finishes_without_followup_inference_and_persists(native):
    agent,manager,db=native
    hook=Mock(return_value={'final_response':'Operation accepted.', 'speak_final_response': True})
    manager._hooks['finish_tool_turn']=[hook]
    result=agent.run_conversation('Search example.')
    assert result['completed'] and result['final_response']=='Operation accepted.'
    assert result['turn_exit_reason']=='plugin_tool_completion'
    assert result['speak_final_response'] is True
    assert agent.client.chat.completions.create.call_count==1
    results=hook.call_args.kwargs['tool_results']
    assert len(results)==1 and results[0]['name']=='web_search'
    rows=db.get_messages(agent.session_id)
    assert [r['role'] for r in rows]==['user','assistant','tool','assistant']
    assert rows[-1]['content']=='Operation accepted.'


@pytest.mark.parametrize('mode',['absent','decline','exception','invalid'])
def test_unhandled_policy_keeps_native_followup(native, mode):
    agent,manager,_=native
    if mode!='absent':
        hook=Mock(side_effect=RuntimeError('synthetic')) if mode=='exception' else Mock(return_value=None if mode=='decline' else {'final_response':12})
        manager._hooks['finish_tool_turn']=[hook]
    result=agent.run_conversation('Search example.')
    assert result['completed'] and result['final_response']=='Normal answer.'
    assert agent.client.chat.completions.create.call_count==2


def test_failed_persistence_precedes_completion_policy(native, monkeypatch):
    agent,manager,_=native
    hook=Mock(return_value={'final_response':'Must not report success.'})
    manager._hooks['finish_tool_turn']=[hook]
    execute=agent._execute_tool_calls
    def fail(*args,**kwargs):
        execute(*args,**kwargs)
        agent._incremental_persistence_failed=True
    monkeypatch.setattr(agent,'_execute_tool_calls',fail)
    result=agent.run_conversation('Search example.')
    hook.assert_not_called()
    assert result['failed'] and result['turn_exit_reason']=='session_persistence_failed'
