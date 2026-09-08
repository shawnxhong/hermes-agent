# Full-tool voice stability and latency (2026-09-08)

## Fix and boundaries

The original native system policy encouraged artifact/tool work even for pure
prose. A current-user-only delivery instruction could not reliably override
that policy: the model attempted file writes, memory replacement, template
search or Python serialization before returning an answer.

The general-voice plugin now registers a static application delivery contract
through the existing system-section API **when a new local session is built**.
The final prose is the host's report input, not a request for an intermediate
file, Python call or model-owned email. This section never changes with tasks;
it is conditional on buffered voice context and does not change typed/IM rules.
Restart the CLI and start a new conversation to load it. Resumed conversations
keep their frozen prompt; do not rebuild their historical prefix.

Sampling is deterministic (`temperature: 0`) only for a buffered turn. Simple
answers have a 512-token budget and a direct one-sentence instruction; reports
retain 4096 tokens and a normal 300–600-word target. This is not a global model
setting and is restored with the original callbacks after the turn.

The previous six-call/six-tool execution bound, duplicate/failed-tool guards,
four-search research bound, incomplete-output rejection and truthful durable
email submission remain in force. Necessary native tools and their permissions
remain available. No tool schemas are removed to obtain a passing replay.

## Evidence

240 focused tests passed, 3 non-Linux tests skipped. This includes continuation
cleanup, prompt/schema invariants, speech buffering, SQLite task isolation,
mail deduplication, cancellation, approval/clarify and existing travel/wake tests.

Real local Qwen/OVMS capture-only replays:

| Runtime / scenario | Result | Detail turn | Short follow-up |
| --- | --- | --- | --- |
| Canonical CLI / product launch (`7y414ez1`) | Seven turns passed | 31.64 s | 12.56 s |
| Staged installed CLI / workshop (`f_xuwzzg`) | Seven turns passed | 47.94 s | 27.97 s |
| Staged installed CLI / product launch (`lu4plvjs`) | Seven turns passed | 35.35 s | 6.04 s |

All three used native tools, produced actual reports, attempted no unnecessary
tools, suppressed raw draft streaming, reused exact bodies for typed resend,
and reset task/recipient on a new task. The staged runtime exposed its actual
21-tool CLI schema. System and tool-schema hashes stayed constant across turns.
The last run's new memo took 7.43 s; recipient turns took 0.02–0.03 s and zero
model calls (SMTP capture, not real mail latency).

The five-turn staged travel regression also passed (`nhqrwab3`): existing
travel strategy, native explanation, artifact adoption and typed resend.
Planning took 42.82 s including one bounded plan repair; explanation 17.11 s.

The checker now loads the real CLI toolset resolver and configured preloaded
travel prompt. It records API duration and token usage separately from total
turn duration, plus wire requests in an isolated temporary Hermes home.
`--stop-after` is diagnostic only and does **not** assert acceptance.

The measured variation is mainly inside model requests, not email/TTS overhead.
In the last run, the report's native request took 29.56 s / 1165 output tokens;
routing took 2.27 s and summarization 3.47 s. The follow-up native request took
4.15 s / 23 output tokens. Exact-request warm-cache probes were faster, so a
warm direct probe is not a valid promise about a cold full-harness turn.
No model-server cache/device setting was changed. Cold starts, concurrent
ASR/TTS load and longer research still vary; there is no hard wall-clock SLA.

Replay with the live installation after deployment:

```bash
venv/bin/python scripts/local-ovms/check_general_voice.py --live-code --native-tools
```

Run the checker from the canonical source checkout with its available Python
environment. All checker mail is captured. These tests do not establish SMTP
inbox delivery or acoustic ASR/TTS acceptance; finish with a new-session human
voice test. Never copy wire diagnostics or local credentials into Git.
