import copy
from unittest.mock import Mock
import pytest
from openai.types.chat import ChatCompletion
from agent.turn_workflow import TurnContinuation,current,for_session,request_messages


def response(text,tools=None):
    return ChatCompletion.model_validate({'id':'completion','created':1,'object':'chat.completion','model':'local-test',
        'choices':[{'index':0,'finish_reason':'tool_calls' if tools else 'stop',
                    'message':{'role':'assistant','content':text,'tool_calls':tools}}],
        'usage':{'prompt_tokens':20,'completion_tokens':20,'total_tokens':40}})


@pytest.fixture
def native(tmp_path,monkeypatch):
    from hermes_cli import plugins
    from hermes_state import SessionDB
    from run_agent import AIAgent
    monkeypatch.setattr('agent.title_generator.maybe_auto_title',lambda *a,**kw:None)
    manager=plugins.PluginManager();monkeypatch.setattr(plugins,'_plugin_manager',manager)
    db=SessionDB(tmp_path/'sessions.db')
    agent=AIAgent(model='local-test',provider='custom',base_url='http://127.0.0.1:1/v1',api_key='test',
        api_mode='chat_completions',quiet_mode=True,max_iterations=10,enabled_toolsets=['web'],
        skip_memory=True,skip_context_files=True,session_db=db,session_id='native-policy',platform='cli')
    agent.client=Mock(spec=['chat','is_closed','close'])
    agent.client.is_closed=False
    agent.client.chat.completions.create=Mock(return_value=response('Full report with all the detail.'))
    return agent,manager,db


def test_native_execution_buffered_finalized_persisted_and_restored(native):
    agent,manager,db=native
    delta=Mock();interim=Mock();agent.stream_delta_callback=delta;agent.interim_assistant_callback=interim
    before_tools=copy.deepcopy(agent.tools)
    finalize=Mock(return_value={'final_response':'Brief delivered result.','api_calls':1})
    manager._hooks['run_turn_workflow']=[lambda **kw:{'continuation':TurnContinuation('Specific task context.',finalize)}]
    result=agent.run_conversation('Write a report.',input_modality='voice')
    assert result['completed'] and result['final_response']=='Brief delivered result.'
    finalize.assert_called_once()
    assert finalize.call_args.kwargs['response_text']=='Full report with all the detail.'
    request=agent.client.chat.completions.create.call_args.kwargs
    assert 'Specific task context.' in request['messages'][-1]['content']
    assert request['max_tokens']==4096
    assert agent.tools==before_tools
    assert agent.stream_delta_callback is delta and agent.interim_assistant_callback is interim
    assert current(agent) is None and for_session(agent.session_id) is None
    assert not [c for c in delta.call_args_list if c.args and c.args[0]]
    assert not interim.called
    rows=[(m['role'],m['content']) for m in db.get_messages(agent.session_id)]
    assert rows==[('user','Write a report.'),('assistant','Brief delivered result.')]
    assert 'Specific task context.' not in str(result['messages'])


def test_continuation_excludes_later_workflow_handlers(native):
    agent,manager,_=native;later=Mock()
    manager._hooks['run_turn_workflow']=[lambda **kw:{'continuation':TurnContinuation('Context',lambda **kw:{'final_response':'Done.'})},later]
    assert agent.run_conversation('Hello',input_modality='voice')['completed']
    later.assert_not_called()


def test_finalization_failure_is_short_and_restores_callbacks(native):
    agent,manager,_=native;delta=Mock();agent.stream_delta_callback=delta
    manager._hooks['run_turn_workflow']=[lambda **kw:{'continuation':TurnContinuation('Context',Mock(side_effect=RuntimeError('private failure')))}]
    result=agent.run_conversation('Hello',input_modality='voice')
    assert result['failed'] and 'private failure' not in result['final_response']
    assert agent.stream_delta_callback is delta and current(agent) is None


def test_request_context_is_copy_only_and_multimodal_safe(native):
    agent,_,_=native;policy=TurnContinuation('Artifact',lambda **kw:None);policy.begin(agent)
    try:
        original=[{'role':'system','content':'Stable'},{'role':'user','content':[{'type':'text','text':'Question'}]}]
        snapshot=copy.deepcopy(original);changed=request_messages(agent,original)
        assert original==snapshot and changed[0]==original[0]
        assert changed[-1]['content'][-1]['text'].endswith('Artifact')
    finally:policy.close()


def test_current_turn_display_prefix_is_removed_only_from_wire_copy(native):
    agent,_,_=native
    policy=TurnContinuation('Host delivery',lambda **kw:None,input_prefixes=('Legacy display policy: ',))
    policy.begin(agent)
    try:
        original=[{'role':'user','content':'Past question'}, {'role':'assistant','content':'Past answer'},
                  {'role':'user','content':'Legacy display policy: Actual request'}]
        snapshot=copy.deepcopy(original)
        changed=request_messages(agent,original)
        assert original==snapshot and changed[:2]==original[:2]
        assert changed[-1]['content'].endswith('Actual request')
        assert 'Legacy display policy' not in changed[-1]['content']
    finally:policy.close()


def test_native_tool_execution_and_result_reach_delivery(native,monkeypatch):
    from tools import web_tools
    agent,manager,_=native
    search=Mock(return_value='{"results":[{"title":"Verified","url":"https://example.org"}]}')
    monkeypatch.setattr(web_tools,'web_search_tool',search)
    calls=[{'id':'call-search','type':'function','function':{'name':'web_search','arguments':'{"query":"example"}'}}]
    agent.client.chat.completions.create.side_effect=[response('',calls),response('Result from verified search.')]
    finalize=Mock(return_value={'final_response':'Short researched result.'})
    manager._hooks['run_turn_workflow']=[lambda **kw:{'continuation':TurnContinuation('Research this.',finalize)}]
    result=agent.run_conversation('Research example.',input_modality='voice')
    assert result['completed'] and result['final_response']=='Short researched result.'
    search.assert_called_once()
    assert any(m['role']=='tool' for m in finalize.call_args.kwargs['messages'])


def test_native_budget_exit_is_failed_and_does_not_start_another_inference(native,monkeypatch):
    from tools import web_tools
    agent,manager,_=native
    monkeypatch.setattr(web_tools,'web_search_tool',lambda **kw:'{"results":[]}')
    calls=[{'id':'search','type':'function','function':{'name':'web_search','arguments':'{"query":"example"}'}}]
    agent.client.chat.completions.create.return_value=response('',calls)
    finalize=Mock(return_value={'final_response':'The execution limit was reached.','failed':True})
    manager._hooks['run_turn_workflow']=[lambda **kw:{'continuation':TurnContinuation('Context',finalize,max_api_calls=1)}]
    result=agent.run_conversation('Research example.',input_modality='voice')
    assert result['failed'] and result['turn_exit_reason']=='workflow_execution_budget'
    assert agent.client.chat.completions.create.call_count==1
    assert finalize.call_args.kwargs['failed']
    assert current(agent) is None


def test_cancelled_delivery_never_invokes_sender_and_session_rotation_cleans_registry(native):
    from agent.turn_workflow import finish
    agent,_,_=native
    finalize=Mock();policy=TurnContinuation('Context',finalize);policy.begin(agent)
    original_session=agent.session_id
    agent.session_id='rotated-session'
    try:
        text,_,_=finish(agent,'Private draft',interrupted=True,failed=False,reason='interrupted',messages=[])
        assert text==''
        finalize.assert_not_called()
    finally:policy.close()
    assert for_session(original_session) is None and current(agent) is None


def test_core_exception_always_restores_turn_callbacks(native,monkeypatch):
    from agent.turn_workflow import run_scoped_conversation
    agent,_,_=native;delta=Mock();agent.stream_delta_callback=delta
    def fail(*a,**kw):
        TurnContinuation('Context',Mock()).begin(agent)
        raise RuntimeError('core failed')
    monkeypatch.setattr('agent.conversation_loop.run_conversation',fail)
    with pytest.raises(RuntimeError):run_scoped_conversation(agent,'Task')
    assert agent.stream_delta_callback is delta and current(agent) is None


def test_incomplete_generation_does_not_autocontinue_or_persist_raw_draft(native):
    agent,manager,db=native
    partial=response('Incomplete private draft')
    partial.choices[0].finish_reason='length'
    agent.client.chat.completions.create.return_value=partial
    finalize=Mock(return_value={'final_response':'The result was incomplete.','failed':True})
    manager._hooks['run_turn_workflow']=[lambda **kw:{'continuation':TurnContinuation('Context',finalize)}]
    result=agent.run_conversation('Draft a report.',input_modality='voice')
    assert result['failed'] and result['turn_exit_reason']=='workflow_incomplete_output'
    assert agent.client.chat.completions.create.call_count==1
    assert 'Incomplete private draft' not in str(db.get_messages(agent.session_id))


def test_sampling_is_turn_scoped_without_changing_tool_schemas(native):
    agent,_,_=native
    messages=[{'role':'system','content':'Stable'},{'role':'user','content':'Draft'}]
    baseline=agent._build_api_kwargs(copy.deepcopy(messages))
    policy=TurnContinuation('Context',lambda **kw:None,temperature=0.0)
    policy.begin(agent)
    try:
        changed=agent._build_api_kwargs(copy.deepcopy(messages))
        assert changed['temperature']==0.0 and changed['tools']==baseline['tools']
    finally:policy.close()
    restored=agent._build_api_kwargs(copy.deepcopy(messages))
    assert restored==baseline
