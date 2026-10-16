"""Exact English media-stop commands shared by Feishu and local wake ASR."""
import re


_STOP_COMMAND = re.compile(
    r'(?:(?:can|could|would|will) you\s+)?(?:please\s+)?'
    r'(?:stop|close|quit|turn off)\s+(?:playing\s+)?'
    r'(?:(?:the|this|current|all)\s+)?'
    r'(?:music|audio|video|videos|song|songs|media|playback|player)'
    r'(?:\s+(?:now|again|please))*[.!?]*', re.I)


def is_media_stop_command(text):
    return isinstance(text, str) and bool(_STOP_COMMAND.fullmatch(text.strip()))
