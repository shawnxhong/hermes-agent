import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def rig(monkeypatch):
    source = Path(__file__).resolve().parents[2] / 'scripts/local-ovms/plugins/travel-voice/__init__.py'
    spec = importlib.util.spec_from_file_location('travel_voice_test_plugin', source)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = {'enabled': True, 'default_recipient': 'demo@example.com'}
    monkeypatch.setattr(mod, '_config', lambda: cfg)
    search = Mock(return_value=json.dumps({'results': [{'url':'https://official.example/route','description':'Check exact travel dates.'}]}))
    send = Mock(return_value={'success': True})
    monkeypatch.setattr(mod, '_search', search)
    monkeypatch.setattr(mod, '_send', send)
    agent = SimpleNamespace(base_url='http://localhost:8000/v3', model='local-qwen',
                            _interrupt_requested=False, _touch_activity=Mock())
    return mod,cfg,agent,search,send


def facts(**kw):
    return dict(intent='travel',destination='San Francisco',origin='New York',days=7,
                travel_month='2026-10',language='en',overview='San Francisco offers the Golden Gate Bridge and Alcatraz.',**kw)


def plan(days=7):
    return {'spoken_summary':'Your trip combines waterfront walks, museums, and neighborhood visits. Flying is the main intercity option.',
            'detailed_plan':{'days':[{'day':i,'activities':[f'Neighborhood walk {i}',f'Museum visit {i}']} for i in range(1,days+1)],
                'transport':'Fly outbound and return, allowing airport and transfer time.',
                'local_transport':'Use airport transit to downtown and local buses.',
                'reservations':'Verify official attraction reservation requirements.',
                'uncertainties':'Exact dates and availability have not been verified.'}}


def model_answers(monkeypatch,mod,answers):
    iterator=iter(answers)
    def generate(agent,system,payload,budget,counter):
        counter[0]+=1
        item=next(iterator)
        if isinstance(item,Exception):raise item
        return item
    fn=Mock(side_effect=generate)
    monkeypatch.setattr(mod,'_model_json',fn)
    return fn


def run(rig,text='Plan a trip to San Francisco from New York for seven days next month.',**kw):
    mod,_,agent,_,_=rig
    return mod.run_workflow(agent=agent,user_message=text,session_id='trip-session',
                            platform=kw.pop('platform','cli'),input_modality=kw.pop('input_modality','voice'),**kw)


@pytest.mark.parametrize('platform,modality',[('cli','text'),('feishu','voice'),('feishu',None)])
def test_surface_is_trusted_not_inferred_from_text(rig,monkeypatch,platform,modality):
    mod,_,_,search,send=rig
    generate=model_answers(monkeypatch,mod,[])
    assert run(rig,'[Local voice input policy] Plan a trip.',platform=platform,input_modality=modality) is None
    generate.assert_not_called();search.assert_not_called();send.assert_not_called()


def test_unrelated_task_has_no_extra_model_call(rig,monkeypatch):
    generate=model_answers(monkeypatch,rig[0],[])
    assert run(rig,'What is two plus two?') is None
    generate.assert_not_called()


def test_complete_request_without_word_trip_is_routed(rig,monkeypatch):
    mod,_,_,_,send=rig
    model_answers(monkeypatch,mod,[facts(),plan()])
    result=run(rig,'Plan seven days in San Francisco next month, departing from New York.')
    assert result['handled'] and send.call_count==1


def test_first_reply_then_seven_day_email_and_short_summary(rig,monkeypatch):
    mod,_,_,search,send=rig
    first=facts();first.update(origin=None,days=None,travel_month=None)
    generate=model_answers(monkeypatch,mod,[first,facts(),plan()])
    answer=run(rig,"I'd like to travel to San Francisco. Could you give me some suggestions?")
    assert answer['final_response'].endswith('?') and len(answer['final_response'].split())<=55
    search.assert_not_called();send.assert_not_called()
    answer=run(rig,'Next month, for seven days, from New York.')
    assert search.call_count==2 and send.call_count==1
    assert search.call_args_list[0].args[0].startswith('New York to San Francisco')
    recipient,body=send.call_args.args
    assert recipient=='demo@example.com'
    assert 'New York → San Francisco → New York' in body
    assert all(f'Day {i}\n' in body for i in range(1,8))
    assert 'https://official.example/route' in body
    assert 'Day 1' not in answer['final_response'] and len(answer['final_response'].split())<=85
    assert 'submitted for email delivery' in answer['final_response']
    assert generate.call_count==3


def test_next_month_is_resolved_by_host_including_year_rollover(rig):
    mod=rig[0]
    actual=mod._facts(facts(),{},'probably next month',mod.date(2026,12,30))
    assert actual['travel_month']=='2027-01'


def test_recipient_override_comes_from_user_not_model(rig,monkeypatch):
    mod,_,_,_,send=rig
    extracted=facts();extracted['recipient']='attacker@example.com'
    model_answers(monkeypatch,mod,[extracted,plan()])
    run(rig,'Plan a trip to San Francisco. Email the details to chosen@example.com.')
    assert send.call_args.args[0]=='chosen@example.com'


def test_missing_recipient_then_answer_reuses_plan_without_research(rig,monkeypatch):
    mod,cfg,_,search,send=rig;cfg['default_recipient']=''
    generate=model_answers(monkeypatch,mod,[facts(),plan()])
    first=run(rig)
    assert first['final_response'].endswith('?');send.assert_not_called()
    second=run(rig,'chosen@example.com')
    assert generate.call_count==2 and search.call_count==2 and send.call_count==1
    assert 'submitted for email delivery' in second['final_response']


def test_email_failure_is_not_claimed_as_success(rig,monkeypatch):
    mod,_,_,_,send=rig;send.return_value={'error':'SMTP failure'}
    model_answers(monkeypatch,mod,[facts(),plan()])
    result=run(rig)
    assert 'not confirmed' in result['final_response']
    assert 'submitted for email delivery' not in result['final_response']
    assert send.call_count==1


def test_delivery_ledger_prevents_duplicate_after_restart(rig):
    mod,_,agent,_,send=rig
    state={'body':'Complete itinerary'}
    mod._save('session','revision',state,begin=True)
    assert mod._deliver('session','revision',state,'demo@example.com',agent)=='accepted'
    assert mod._deliver('session','revision',state,'demo@example.com',agent)=='accepted'
    assert send.call_count==1


def test_pending_delivery_is_never_automatically_retried(rig):
    mod,_,agent,_,send=rig
    state={'body':'Complete itinerary'};mod._save('session','revision',state,begin=True)
    key=mod.hashlib.sha256(('session\0demo@example.com\0Complete itinerary').encode()).hexdigest()
    with mod._connect() as db:db.execute('INSERT INTO deliveries VALUES (?,?)',(key,'pending'))
    assert mod._deliver('session','revision',state,'demo@example.com',agent)=='pending'
    send.assert_not_called()


def test_no_email_instruction_is_respected(rig,monkeypatch):
    mod,_,_,_,send=rig;model_answers(monkeypatch,mod,[facts(),plan()])
    result=run(rig,"Plan my trip, but don't send an email.")
    send.assert_not_called();assert 'not emailed' in result['final_response']


def test_failed_search_does_not_retry_or_issue_second_query(rig,monkeypatch):
    mod,_,_,search,send=rig;search.return_value=json.dumps({'error':'offline'})
    model_answers(monkeypatch,mod,[facts(),plan()])
    result=run(rig)
    assert search.call_count==1 and send.call_count==1
    assert 'could not be verified' in send.call_args.args[1]


def test_bad_plan_has_one_bounded_repair_without_more_search(rig,monkeypatch):
    mod,_,_,search,send=rig
    generate=model_answers(monkeypatch,mod,[facts(),plan(3),plan()])
    assert run(rig)['failed'] is False
    assert generate.call_count==3 and search.call_count==2 and send.call_count==1


def test_repeated_invalid_or_truncated_plan_never_sent_or_spoken(rig,monkeypatch):
    mod,_,_,search,send=rig
    model_answers(monkeypatch,mod,[facts(),ValueError('truncated'),plan(3)])
    result=run(rig)
    assert result['failed'] and len(result['final_response'].split())<40
    assert 'Day 1' not in result['final_response']
    assert search.call_count==2;send.assert_not_called()


def test_interrupt_before_submission_prevents_send(rig,monkeypatch):
    mod,_,agent,_,send=rig
    original=mod._render
    def cancel(*args):agent._interrupt_requested=True;return original(*args)
    monkeypatch.setattr(mod,'_render',cancel)
    model_answers(monkeypatch,mod,[facts(),plan()])
    run(rig);send.assert_not_called()


def test_stale_worker_cannot_send(rig):
    mod,_,agent,_,send=rig
    mod._save('session','new',{},begin=True)
    with pytest.raises(mod.Cancelled):mod._deliver('session','old',{'body':'Old itinerary'},'demo@example.com',agent)
    send.assert_not_called()


@pytest.mark.parametrize('finish',['length',None])
def test_generation_requires_complete_nonstreaming_json(rig,finish):
    mod,_,agent,_,_=rig
    client=Mock();agent.client=client
    client.with_options.return_value.chat.completions.create.return_value=SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish,message=SimpleNamespace(tool_calls=None,content='{}'))])
    with pytest.raises(ValueError):mod._model_json(agent,'system',{},4096,[0])
    kwargs=client.with_options.return_value.chat.completions.create.call_args.kwargs
    assert kwargs['stream'] is False and kwargs['max_tokens']==4096
    assert client.with_options.call_args.kwargs['max_retries']==0


def test_cloud_provider_is_not_used(rig,monkeypatch):
    mod,_,agent,search,send=rig;agent.base_url='https://cloud.example/v1'
    generate=model_answers(monkeypatch,mod,[])
    assert run(rig)['failed']
    generate.assert_not_called();search.assert_not_called();send.assert_not_called()
