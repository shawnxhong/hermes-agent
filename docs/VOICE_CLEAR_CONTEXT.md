# Voice context clear

After Hello Intel (or Ctrl+B), say `clear memory`, `clear context` or
`clear conversation`. End with `thank you` when that recording endpoint is
enabled. Recognition is local after ASR; no model tool or model request is used.
The parser also accepts 清空上下文 / 清除上下文 / 清空对话, but recognition of
Chinese audio depends on the configured ASR language (the demo is English).

Only a whole command matches, ignoring case, whitespace and terminal punctuation.
Questions such as `What does clear memory mean?`, negations and longer requests
do not match. Typed messages are unchanged. Playback echo rejection still runs
before the barge-in command recognizer.

The request cancels current inference/playback and waits for the serialized CLI
processing boundary. It then uses the same context-reset helper as `/clear`,
clears old queued input and speaks `Conversation cleared.` before allowing the
wake listener to resume. Current scene/skills remain selected. A concurrent scene
switch supersedes an older pending clear, and duplicate pending clear callbacks
coalesce. A failed spoken confirmation does not leave wake listening blocked.

This starts a new conversation context; it does not delete memory files, archived
history, mail queues or other machines' state. Native `/clear` session-boundary
hooks remain unchanged. The voice request is an explicit local control; typed
`/clear` keeps its existing confirmation policy. Durable jobs already enqueued
are not recalled or replayed. Human acoustic acceptance is separate from parser,
ASR replay and session-reset tests.
