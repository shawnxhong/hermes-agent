"""One voice presentation boundary; execution and authorization remain native."""
from contextvars import ContextVar
import logging
import re

turn_presentation = ContextVar('voice_presentation', default=None)
log = logging.getLogger(__name__)


def is_brief(text):
    from hermes_cli.voice_response_policy import _plain_spoken_text
    from hermes_cli.voice_sentence_delivery import sentences
    plain = _plain_spoken_text(text)
    if re.search(r'```|~~~|^\s*\|.+\|\s*$', text, re.M):
        return False
    return bool(plain and len(plain.split()) <= 60 and len(sentences(plain, final=True)[0]) <= 3)


def spoken_body(agent, body, request, *, evidence=None):
    """Do not rewrite a reply that already fits; otherwise compress exactly once."""
    from hermes_cli import general_voice as base
    if is_brief(body):
        return body, 0
    try:
        return base._summary(agent, body, evidence, request, early_delivery=True), 1
    except Exception:
        if getattr(agent, '_interrupt_requested', False):
            raise
        log.warning('Spoken compression failed; keeping bounded original content')
        return base._fallback_summary(body), 1


def mark_presented():
    state = turn_presentation.get()
    if state is not None:
        state['handled'] = True


def native_decision(decision):
    state = turn_presentation.get()
    if state is not None:
        state['decision'] = dict(decision)


def native_report(body, messages):
    """Automatic email can contain final prose, not source or copied tool output."""
    if re.search(r'```|~~~', body):
        return None
    for message in messages or []:
        raw = message.get('content')
        if message.get('role') == 'tool' and isinstance(raw, str) and len(raw.strip()) >= 80:
            if raw.strip() in body:
                return None
    # Bare code is sometimes emitted without Markdown fences.
    import ast
    try:
        tree = ast.parse(body)
    except SyntaxError:
        pass
    else:
        if any(not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Constant)
               for node in tree.body):
            return None
    return body


def finish_native(agent, result, request, session):
    """A presentation failure must not turn completed native work into failure."""
    from hermes_cli.voice_sentence_delivery import current_delivery
    delivery = current_delivery.get()
    if delivery is not None and not delivery.active():
        return result
    try:
        return _finish_native(agent, result, request, session)
    except Exception:
        log.exception('Native voice presentation failed; native result retained')
        if not isinstance(result, dict):
            return result
        from hermes_cli.general_voice import _fallback_summary
        text = _fallback_summary(result.get('final_response') or '')
        if delivery is not None and delivery.committed:
            from hermes_cli.voice_sentence_delivery import FAILURE
            text = ' '.join(delivery.committed) + ' ' + FAILURE
        return dict(result, final_response=text)


def _finish_native(agent, result, request, session):
    """CLI-only fallback for coding/actions that bypass buffered content workflow.

    Native history/tools are left intact; the spoken transcript is recorded in
    the voice store separately. No whole-file or raw-tool attachment is created.
    """
    state = turn_presentation.get()
    if state is None or state.get('handled') or not isinstance(result, dict):
        return result
    state['handled'] = True
    if result.get('interrupted') or getattr(agent, '_interrupt_requested', False):
        return result
    body = result.get('final_response') or ''
    if not body.strip():
        return result
    from hermes_cli import general_voice as base
    from hermes_cli.voice_continuity_store import ContinuityStore
    from hermes_cli.voice_network import current_turn
    decision = state.get('decision') or {}
    safe_body = native_report(body, result.get('messages'))
    # Preserve code locally; avoid reading long source aloud.
    source = body if safe_body is not None else re.sub(r'```[\s\S]*?```|~~~[\s\S]*?~~~', '', body).strip()
    if safe_body is None and (source == body.strip() or not source):
        source = 'The detailed output is available in this session; I have not emailed source code or raw tool output.'
    speech, calls = spoken_body(agent, source, request)
    from hermes_cli.voice_sentence_delivery import current_delivery
    delivery = current_delivery.get()
    if getattr(agent, '_interrupt_requested', False) or (delivery is not None and not delivery.active()):
        return result
    updated = dict(result, final_response=speech,
                   api_calls=int(result.get('api_calls', 0)) + calls, voice_presented=True)
    if result.get('failed') or result.get('partial') or not result.get('completed', False):
        return updated
    store = ContinuityStore()
    task = store.start(session, request)
    detailed = bool(decision.get('detail'))
    task, version = store.save_result(session, task, body=body, summary=speech, detailed=detailed)
    messages = list(result.get('messages') or [])
    start = max((i for i,m in enumerate(messages) if m.get('role')=='user'), default=-1)
    native_mail = any(
        call.get('function', {}).get('name') == 'send_message'
        for m in messages[start+1:] for call in (m.get('tool_calls') or [])
        if isinstance(call, dict))
    if detailed and safe_body is not None and not native_mail and not base.NO_EMAIL.search(request):
        network = current_turn(session)
        if network is not None and network.offline:
            updated['final_response'] += ' ' + base._status('offline')
        else:
            from hermes_cli.voice_outbox import enqueue
            recipient = base.config().get('default_recipient', '')
            from hermes_cli.voice_delivery import valid_email_recipients
            if valid_email_recipients(recipient):
                status = enqueue(store, session, task, recipient, version=version,
                                 interrupted=lambda:agent._interrupt_requested)
                updated['final_response'] += ' ' + base._status(status)
            else:
                store.pend(session, task, 'recipient', {'version':version,'recipient':'',
                           'question':'Which email address should receive these details?'})
                updated['final_response'] += ' Which email address should receive these details?'
    store.record_turn(session, request, updated['final_response'])
    return updated
