"""Whole-utterance, ASR-only local controls, including polite requests."""
import re
import unicodedata

CLEAR_PHRASES = frozenset({
    'clear memory', 'clear context', 'clear conversation',
    '清空上下文', '清除上下文', '清空对话',
})

# Deliberately bounded grammar, not substring/intent classification. Questions
# ABOUT clearing, negation, quotes and compound instructions cannot match.
_CLEAR_REQUEST = re.compile(
    r'(?:(?:can|could|would|will) you )?'
    r'(?:please[,，]? )?'
    r'(?:clear(?: up)?|clean(?: up)?|reset) '
    r'(?:(?:your|our|the|this) )?(?:current )?'
    r'(?:memory|context|conversation)'
    r'(?: for me)?(?:[,，]? please)?'
)


_SUSPECT_REQUEST = re.compile(
    r'(?:(?:can|could|would|will) you )?(?:please[,，]? )?'
    r'(?:clear|clean|reset|erase|delete|forget|wipe)\b.*'
    r'\b(?:memory|memories|context|conversation|chat|history|everything)\b'
)


def _normalized(text):
    if not isinstance(text, str):
        return ''
    text = unicodedata.normalize('NFKC', text).casefold().strip()
    # Strip only terminal punctuation: quotes/questions/negations in the body
    # remain significant. No substring matching or model interpretation.
    text = re.sub(r'[\s.!?。！？,，;；]+$', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def is_clear_command(text):
    text = _normalized(text)
    return (text in CLEAR_PHRASES
            or bool(_CLEAR_REQUEST.fullmatch(text))
            or text in {'请清空上下文', '请清除上下文', '请清空对话'})


def needs_clear_clarification(text):
    text = _normalized(text)
    if is_clear_command(text):
        return False
    return bool(_SUSPECT_REQUEST.match(text) or re.match(
        r'^请?(?:清空|清除|清理|删除|重置).*(?:记忆|上下文|对话|历史)', text))


def route_control(cli, transcript):
    clear = is_clear_command(transcript)
    if not clear and not needs_clear_clarification(transcript):
        return False
    from hermes_cli.voice_scenes import SceneController
    controller = getattr(cli, '_scene_controller', None)
    if controller is None:
        controller = SceneController(cli, {}, {})
        cli._scene_controller = controller
    if clear:
        controller.request_clear()
    else:
        controller.request_clear(clarify=True)
    return True
