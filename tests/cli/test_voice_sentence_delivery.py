import json
import queue
import threading
from types import SimpleNamespace as NS

import pytest

from hermes_cli.voice_sentence_delivery import (
    FAILURE, SentenceDelivery, current_delivery, decoded_summary_prefix,
    make_delivery, sentences, stream_summary,
)
from hermes_cli.general_voice import _summary, _validate_summary


def sink():
    return SentenceDelivery(queue.Queue(), threading.Event())


class Stream:
    def __init__(self, parts, reason='stop', before=None):
        self.parts, self.reason, self.before, self.closed = parts, reason, before, False

    def __iter__(self):
        for part in self.parts:
            if self.before:
                self.before(part)
            yield NS(choices=[NS(delta=NS(content=part, tool_calls=None), finish_reason=None)])
        yield NS(choices=[NS(delta=NS(content=None, tool_calls=None), finish_reason=self.reason)])

    def close(self):
        self.closed = True


def run(stream, delivery):
    return stream_summary(lambda **kwargs: stream, {}, delivery, _validate_summary, lambda: False)


@pytest.mark.parametrize('text,expected', [
    ('Hi. Next?', ['Hi.', 'Next?']),
    ('Ask Dr. Brown. Pay 3.50 dollars.', ['Ask Dr. Brown.', 'Pay 3.50 dollars.']),
    ('Meet J. Smith in the U.S. today. Ready?', ['Meet J. Smith in the U.S. today.', 'Ready?']),
    ('He said "Ready." Then left.', ['He said "Ready."', 'Then left.']),
])
def test_sentences(text, expected):
    assert sentences(text, final=True) == (expected, '')


def test_never_cut_partial_decimal():
    assert sentences('Pay 3.') == ([], 'Pay 3.')
    assert sentences('Pay 3.50 dollars. Now') == (['Pay 3.50 dollars.'], 'Now')


def test_json_every_character_boundary():
    value='Visit "the bay". Enjoy café and \U0001f600.'
    raw=json.dumps({'summary':value}, ensure_ascii=True)
    for i in range(len(raw)+1):
        prefix=decoded_summary_prefix(raw[:i])
        assert value.startswith(prefix)
    assert decoded_summary_prefix(raw)==value
    assert decoded_summary_prefix('{"thinking":"secret"}')==''


def test_first_sentence_arrives_before_model_finishes_no_replay():
    delivery=sink()
    raw=json.dumps({'summary':'Take the direct train. Explore the waterfront.'})
    marker=raw.index('Explore')
    def before(part):
        if part==raw[marker:]:
            assert str(delivery.queue.queue[0])=='Take the direct train.'
    stream=Stream([raw[:marker],raw[marker:]],before=before)
    result=run(stream,delivery)
    assert delivery.committed==['Take the direct train.']
    delivery.finish(result+' The email is queued.')
    assert list(delivery.queue.queue)==['Take the direct train.','Explore the waterfront.','The email is queued.']
    assert stream.closed


@pytest.mark.parametrize('raw,reason', [
    ('{"summary":"Short incomplete', 'length'),
    ('{"summary":"Visit https://example.com. "}', 'stop'),
    ('{"summary":42}', 'stop'),
])
def test_bad_summary_before_commit_uses_existing_fallback(raw,reason):
    delivery=sink();stream=Stream([raw],reason)
    with pytest.raises(ValueError):run(stream,delivery)
    assert not delivery.committed and stream.closed


@pytest.mark.parametrize('tail,reason', [
    ('unfinished', 'length'),
    ('More info", "summary":"different"}', 'stop'),
    ('See https://example.com."}', 'stop'),
])
def test_failure_after_commit_preserves_prefix_once(tail,reason):
    delivery=sink();stream=Stream(['{"summary":"Take the train. ',tail],reason)
    result=run(stream,delivery)
    assert result=='Take the train. '+FAILURE
    delivery.finish(result)
    assert list(delivery.queue.queue)==['Take the train.',FAILURE]
    assert stream.closed


def test_cancellation_never_queues_fallback():
    delivery=sink()
    def before(part):
        if part=='more':delivery.stop.set()
    stream=Stream(['{"summary":"Take the train. ', 'more'],before=before)
    with pytest.raises(RuntimeError):run(stream,delivery)
    delivery.finish('Should not be heard.')
    assert list(delivery.queue.queue)==['Take the train.']
    assert stream.closed


def test_stale_scene_cannot_enqueue():
    delivery=sink();delivery.valid=lambda:False
    assert not delivery.claim()
    delivery.finish('Old answer.')
    assert delivery.queue.empty()


def test_scene_switch_invalidates_already_queued_item():
    delivery=sink();delivery.emit('Previous scene.')
    item=delivery.queue.get();assert item.is_current()
    delivery.valid=lambda:False
    assert not item.is_current()


def test_real_cli_final_enqueue_keeps_keyboard_silent():
    from cli import HermesCLI
    delivery=sink();cli=NS(_voice_last_tts_text='')
    HermesCLI._enqueue_voice_final_tts(cli,delivery.queue,'Hello. How can I help?',
                                     voice_input=False,interrupted=False,delivery=delivery)
    assert delivery.queue.empty()
    HermesCLI._enqueue_voice_final_tts(cli,delivery.queue,'Hello. How can I help?',
                                     voice_input=True,interrupted=False,delivery=delivery)
    assert list(delivery.queue.queue)==['Hello.','How can I help?']


def test_queue_generation_and_session_ownership(monkeypatch):
    from hermes_cli import config
    monkeypatch.setattr(config,'load_config',lambda:{'voice':{'sentence_pipeline':{'enabled':True}}})
    q=queue.Queue();cli=NS(session_id='s',_scene_generation=0,_voice_turn_tts_queue=q,_voice_last_tts_text='')
    delivery=make_delivery(cli,q,threading.Event());assert delivery.active()
    delivery.emit('Hello.');assert cli._voice_last_tts_text=='Hello.'
    cli._scene_generation=1;assert not delivery.active()
    cli._scene_generation=0;cli.session_id='new';assert not delivery.active()
    cli.session_id='s';cli._voice_turn_tts_queue=queue.Queue();assert not delivery.active()


def test_pending_segments_cancel_without_erasing_played():
    delivery=sink();delivery.emit('First.');delivery.emit('Second.')
    delivery.queue.get().on_state('played')
    delivery.stop.set();delivery.cancel_pending()
    assert delivery.states=={1:'played',2:'cancelled'}


def test_stale_synthesis_never_plays_and_barrier_waits(monkeypatch):
    from tools import tts_tool,voice_mode
    from pathlib import Path
    started=threading.Event();release=threading.Event();valid=[True];played=[];states=[]
    def synth(text,output_path):
        started.set();assert release.wait(3)
        Path(output_path).write_bytes(b'audio')
        return json.dumps({'success':True,'file_path':output_path})
    monkeypatch.setattr(tts_tool,'text_to_speech_tool',synth)
    monkeypatch.setattr(voice_mode,'play_audio_file',lambda path:played.append(path))
    pipeline=tts_tool._SyncSentencePipeline(threading.Event())
    marker=tts_tool.TTSPlaybackBarrier()
    pipeline.speak('Old scene.',states.append,lambda:valid[0]);pipeline.barrier(marker)
    assert started.wait(3) and not marker.event.is_set()
    valid[0]=False;release.set();pipeline.close()
    assert not played and marker.event.is_set() and states[-1]=='cancelled'


def test_direct_answer_and_empty_tail():
    delivery=sink();delivery.finish('I am well. How can I help?')
    assert list(delivery.queue.queue)==['I am well.','How can I help?']
    delivery.finish('I am well. How can I help?')
    assert len(delivery.committed)==2


def test_cli_keeps_three_sentence_body_and_host_delivery_status():
    from cli import HermesCLI
    delivery=sink()
    text='I drafted your report. It covers the key findings. The recommendations are included. The details are queued for email delivery.'
    spoken=HermesCLI._enqueue_voice_final_tts(
        NS(), delivery.queue, text, voice_input=True, interrupted=False, delivery=delivery)
    assert spoken==text
    assert list(delivery.queue.queue)[-1]=='The details are queued for email delivery.'


def test_keyboard_im_and_disabled_flag_do_not_create_sink(monkeypatch):
    from hermes_cli import config
    monkeypatch.setattr(config,'load_config',lambda:{'voice':{'sentence_pipeline':{'enabled':True}}})
    assert make_delivery(NS(),None,None) is None
    monkeypatch.setattr(config,'load_config',lambda:{})
    assert make_delivery(NS(),queue.Queue(),threading.Event()) is None
    assert current_delivery.get() is None


def test_existing_summary_prompt_and_budget_unchanged():
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        if kwargs['stream']:return Stream(['{"summary":"Take the train. Explore town."}'])
        return NS(choices=[NS(finish_reason='stop',message=NS(tool_calls=None,content='{"summary":"Take the train. Explore town."}'))])
    client=NS(with_options=lambda **kw:NS(chat=NS(completions=NS(create=create))))
    agent=NS(client=client,model='local',_interrupt_requested=False,_touch_activity=lambda *a:None)
    expected=_summary(agent,'Some result',early_delivery=True)
    token=current_delivery.set(sink())
    try:assert _summary(agent,'Some result',early_delivery=True)==expected
    finally:current_delivery.reset(token)
    assert calls[0].pop('stream') is False
    assert calls[1].pop('stream') is True
    assert calls[0]==calls[1]


def test_real_sync_pipeline_overlaps_and_reports(monkeypatch,tmp_path):
    from tools import tts_tool,voice_mode
    playing=threading.Event();second_ready=threading.Event();spoken=[];states={1:[],2:[]}
    def synth(text,output_path):
        from pathlib import Path
        Path(output_path).write_bytes(text.encode())
        if text=='Second.':
            assert playing.wait(3)
            second_ready.set()
        return json.dumps({'success':True,'file_path':output_path})
    def play(path):
        from pathlib import Path
        text=Path(path).read_text();spoken.append(text)
        if text=='First.':
            playing.set();assert second_ready.wait(3)
        return True
    monkeypatch.setattr(tts_tool,'text_to_speech_tool',synth)
    monkeypatch.setattr(voice_mode,'play_audio_file',play)
    pipeline=tts_tool._SyncSentencePipeline(threading.Event())
    pipeline.speak('First.',states[1].append);pipeline.speak('Second.',states[2].append)
    pipeline.close()
    assert spoken==['First.','Second.']
    assert states[1]==states[2]==['synthesizing','ready','playing','played']
