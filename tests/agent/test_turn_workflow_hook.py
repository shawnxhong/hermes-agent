from unittest.mock import Mock

from hermes_cli import plugins


def test_first_handler_wins_without_running_later_side_effects():
    manager=plugins.PluginManager()
    later=Mock()
    manager._hooks['run_turn_workflow']=[lambda **kw:None,
        lambda **kw:{'handled':True,'final_response':'Done.'},later]
    result=manager.invoke_hook('run_turn_workflow',input_modality='voice')
    assert result==[{'handled':True,'final_response':'Done.'}]
    later.assert_not_called()


def test_exception_fails_closed_instead_of_running_generic_fallback():
    manager=plugins.PluginManager()
    later=Mock()
    def broken(**kw):raise RuntimeError('private internal detail')
    manager._hooks['run_turn_workflow']=[broken,later]
    result=manager.invoke_hook('run_turn_workflow')
    assert result[0]['handled'] and result[0]['failed']
    assert 'private internal detail' not in result[0]['final_response']
    later.assert_not_called()


def test_native_agent_preserves_clean_history_and_persistence(tmp_path,monkeypatch):
    from hermes_state import SessionDB
    from run_agent import AIAgent
    manager=plugins.PluginManager()
    received=[]
    def handler(*,input_modality,user_message,**kw):
        received.append((input_modality,user_message))
        return {'handled':True,'final_response':'A short question?' if len(received)==1 else 'The email was submitted.', 'api_calls':1}
    manager._hooks['run_turn_workflow']=[handler]
    monkeypatch.setattr(plugins,'_plugin_manager',manager)
    db=SessionDB(tmp_path/'sessions.db')
    agent=AIAgent(model='local-test',provider='custom',base_url='http://127.0.0.1:1/v1',api_key='test',
                  api_mode='chat_completions',quiet_mode=True,max_iterations=8,enabled_toolsets=['web'],
                  skip_memory=True,skip_context_files=True,session_db=db,session_id='workflow-test',platform='cli')
    agent.client.chat.completions.create=Mock(side_effect=AssertionError('Generic inference must not execute'))
    delta=Mock();agent.stream_delta_callback=delta
    first=agent.run_conversation('API-local prefix: first',persist_user_message='first',input_modality='voice')
    second=agent.run_conversation('API-local prefix: second',persist_user_message='second',
                                  conversation_history=first['messages'],input_modality='voice')
    assert received==[('voice','first'),('voice','second')]
    assert first['completed'] and second['completed'] and second['turn_exit_reason']=='plugin_workflow'
    transcript=[(r['role'],r['content']) for r in db.get_messages(agent.session_id) if r['role'] in {'user','assistant'}]
    assert transcript==[('user','first'),('assistant','A short question?'),('user','second'),('assistant','The email was submitted.')]
    agent.client.chat.completions.create.assert_not_called()
    assert not [c for c in delta.call_args_list if c.args and c.args[0]]


def test_existing_narrow_hook_signatures_still_work():
    manager=plugins.PluginManager()
    manager._hooks['run_turn_workflow']=[lambda user_message:{'handled':True,'final_response':user_message}]
    assert manager.invoke_hook('run_turn_workflow',user_message='hello',input_modality='voice',future_field=1)[0]['final_response']=='hello'
