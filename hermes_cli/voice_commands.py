"""Exact, ASR-only local controls. Never send these commands to the model."""
import re
import unicodedata

CLEAR_PHRASES = frozenset({
    'clear memory', 'clear context', 'clear conversation',
    '清空上下文', '清除上下文', '清空对话',
})


def is_clear_command(text):
    if not isinstance(text, str):
        return False
    text = unicodedata.normalize('NFKC', text).casefold().strip()
    # Strip only terminal punctuation: quotes/questions/negations in the body
    # remain significant. No substring matching or model interpretation.
    text = re.sub(r'[\s.!?。！？,，;；]+$', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text in CLEAR_PHRASES


def route_control(cli, transcript):
    if not is_clear_command(transcript):
        return False
    from hermes_cli.voice_scenes import SceneController
    controller = getattr(cli, '_scene_controller', None)
    if controller is None:
        controller = SceneController(cli, {}, {})
        cli._scene_controller = controller
    controller.request_clear()
    return True
