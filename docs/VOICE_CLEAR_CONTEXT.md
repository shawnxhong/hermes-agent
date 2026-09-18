# Voice context clear

After Hello Intel (or Ctrl+B), say `clear memory`, `clear context` or
`clear conversation`. End with `thank you` when that recording endpoint is
enabled. Recognition is local after ASR; no model tool or model request is used.
The parser also accepts 清空上下文 / 清除上下文 / 清空对话, but recognition of
Chinese audio depends on the configured ASR language (the demo is English).

Polite requests also work: `please clear up your memory`, `clear memory please`,
`could you please clear your memory?`, and `clear the current context`.
`clean`, `clean up`, and `reset` are accepted in the same bounded grammar,
including `please clean up your memory` and `reset this conversation`.
These are handled locally, not by asking the LLM to invoke a tool. Supported
English forms use clear (optionally up), memory/context/conversation, optional
your/our/the/this/current, please, can/could/would/will you and for me.
Chinese 请清空上下文 / 请清除上下文 / 请清空对话 are also parsed.

Only a whole command matches, ignoring case, whitespace and terminal punctuation.
Questions such as `What does clear memory mean?`, negations and longer requests
do not match. Typed messages are unchanged. Playback echo rejection still runs
before the barge-in command recognizer.

An imperative resembling a memory/context clearing request but outside the
accepted grammar is consumed locally and clarified, not forwarded to the model.
For example `clean up all your memories`, `forget everything`, and
`clear memory and send an email` do NOT clear anything or execute another task.
The old turn is interrupted at the same safe boundary; context/scene are kept,
stale queued continuation input is dropped, and the voice says:
"I haven't cleared anything. To clear only this conversation, say clear memory."
There is no pending yes/no confirmation: repeat an explicit clear command to act.
Negations, quotations and questions ABOUT clearing remain ordinary conversation.
This remains a bounded recognizer, not arbitrary semantic intent understanding.

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
