"""Native, turn-scoped buffered continuation for workflow plugins.

No prompt/toolset mutation or secondary agent. Request context is copied onto
the current user message at the wire boundary; the durable transcript stays clean.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import threading

_lock = threading.RLock()
_sessions = {}


@dataclass
class TurnContinuation:
    context: str
    finalize: object
    max_api_calls: int = 6
    max_output_tokens: int = 4096
    initial_api_calls: int = 0
    before_tool: object = None
    after_tool: object = None
    input_prefixes: tuple = ()
    _saved: dict = field(default_factory=dict, init=False)

    def begin(self, agent):
        if not callable(self.finalize) or not 1 <= self.max_api_calls <= 12:
            raise ValueError('Invalid native continuation')
        if not 128 <= self.max_output_tokens <= 8192:
            raise ValueError('Invalid continuation output budget')
        self._agent = agent
        self._session = str(agent.session_id)
        for name in ('stream_delta_callback','_stream_callback','interim_assistant_callback','quiet_mode'):
            self._saved[name] = getattr(agent,name,None)
            setattr(agent,name,True if name=='quiet_mode' else None)
        agent._active_turn_workflow = self
        with _lock:
            _sessions[self._session] = self

    def close(self):
        agent = getattr(self,'_agent',None)
        if agent is None:
            return
        for name,value in self._saved.items():
            setattr(agent,name,None if name=='_stream_callback' else value)
        agent._active_turn_workflow = None
        with _lock:
            if _sessions.get(self._session) is self:
                _sessions.pop(self._session,None)


def current(agent):
    value = getattr(agent,'_active_turn_workflow',None)
    return value if isinstance(value,TurnContinuation) else None


def for_session(session):
    with _lock:
        return _sessions.get(str(session))


def request_messages(agent, messages):
    policy = current(agent)
    if policy is None:
        return messages
    copied = deepcopy(messages)
    for message in reversed(copied):
        if message.get('role')=='user':
            context = '\n\n[Current-turn execution context]\n'+policy.context
            content = message.get('content')
            if isinstance(content,str):
                for prefix in policy.input_prefixes:
                    if prefix and content.startswith(prefix):
                        content=content[len(prefix):]
                        break
                message['content'] = context+'\n\n[Current user request]\n'+content
            elif isinstance(content,list):
                message['content'].append({'type':'text','text':context})
            break
    return copied


def finish(agent, response, *, interrupted, failed, reason, messages):
    policy = current(agent)
    if policy is None:
        return response,failed,0
    policy._finished = True
    if interrupted or agent._interrupt_requested:
        return '',failed,policy.initial_api_calls
    try:
        result = policy.finalize(response_text=response or '',failed=failed,
                                 turn_exit_reason=reason,messages=messages)
        text = result['final_response']
        if not isinstance(text,str) or not text.strip():
            raise ValueError('Empty workflow delivery')
        failed = failed or bool(result.get('failed'))
        count = policy.initial_api_calls+int(result.get('api_calls',0))
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Native workflow delivery failed closed')
        text,failed,count = 'I could not finish the response safely. Please try again.',True,policy.initial_api_calls
    # Replace only this turn's final assistant row, never prior conversation.
    if messages and messages[-1].get('role')=='assistant' and not messages[-1].get('tool_calls'):
        from agent.context_compressor import _DB_PERSISTED_MARKER
        messages[-1]['content'] = text
        messages[-1].pop(_DB_PERSISTED_MARKER,None)
        agent._db_flush_scan_prefix = None
    agent._response_was_previewed = False
    return text,failed,count


def run_scoped_conversation(agent,*args,**kwargs):
    from agent.conversation_loop import run_conversation
    try:
        result = run_conversation(agent,*args,**kwargs)
        policy = current(agent)
        if policy is not None and not getattr(policy,'_finished',False) and isinstance(result,dict):
            text,failed,calls = finish(agent,result.get('final_response',''),
                interrupted=bool(result.get('interrupted')),failed=True,
                reason=result.get('turn_exit_reason','early_return'),messages=result.get('messages',[]))
            result.update(final_response=text,failed=failed,completed=False,
                          api_calls=int(result.get('api_calls',0))+calls,
                          turn_exit_reason=result.get('turn_exit_reason','workflow_early_return'))
        return result
    finally:
        policy = current(agent)
        if policy is not None:
            policy.close()
