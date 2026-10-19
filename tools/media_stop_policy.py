"""Exact English media-stop grammar shared by Feishu and local wake ASR."""
import re


def compile_media_stop_pattern(verbs, targets):
    """Compile literal vocabulary with the existing polite-command grammar."""
    def alternatives(words):
        if not isinstance(words, list) or not words or any(
            not isinstance(word, str) or not word.strip() or "\n" in word or "\r" in word
            for word in words
        ):
            raise ValueError("Media stop vocabulary must be nonempty lists of single-line strings")
        return "(?:" + "|".join(re.escape(word.strip()) for word in words) + ")"

    return re.compile(
        r'(?:(?:can|could|would|will) you\s+)?(?:please\s+)?'
        + alternatives(verbs) + r'\s+(?:playing\s+)?'
        r'(?:(?:the|this|current|all)\s+)?'
        + alternatives(targets)
        + r'(?:\s+(?:now|again|please))*[.!?]*', re.I)


# Feishu retains its established default vocabulary. The wake service supplies
# its own config vocabulary to the same compiler; ASR hints never feed this list.
_STOP_COMMAND = compile_media_stop_pattern(
    ['stop', 'close', 'quit', 'turn off'],
    ['music', 'audio', 'video', 'videos', 'song', 'songs', 'media', 'playback', 'player'])


def is_media_stop_command(text):
    return isinstance(text, str) and bool(_STOP_COMMAND.fullmatch(text.strip()))
