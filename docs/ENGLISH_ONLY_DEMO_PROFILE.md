# English-only Box demo profile

Date: 2026-09-15  
Branch: `local-ovms-voice`

## Goal

Keep the Intel AI Box demo general-purpose while making its active voice and
agent-control surface English-only. Remove bilingual branches from the small
local voice harness and demo tools, reduce instructions presented to the local
Qwen model, and retain the existing model, wake word, ASR/TTS timing, email,
search, simulated-home and IM capabilities.

This is an active-profile change, not a destructive fork of upstream Hermes.
Generic Unicode file handling and the Weixin/Feishu transport adapters remain
installed. Inactive upstream locale catalogs and platform-specific modules are
not loaded into the local voice model prompt and are left intact for safe
upstream maintenance.

## Runtime behavior

- The active profile fixes display, local STT, static prompts, tool
  acknowledgements, clarification, approval, routing, summaries and failure
  messages to English.
- A minimal global instruction, `Respond only in English.`, also covers typed
  CLI and IM responses without adding scenario behavior to the generic harness.
- The general voice harness still owns only routing, short speech, result
  continuity and truthful host-side email delivery. It does not encode
  destination- or task-specific answer templates.
- A new independent turn can enter the travel domain only when that turn itself
  contains travel-planning evidence. This prevents an unfinished trip from
  contaminating a later general question without special-casing that question.
- Travel, simulated-home and local-media rules remain isolated in their skills.
  Each skill now contains one concise English contract.
- The disabled legacy `travel-voice` plugin is archived from the deployed plugin
  directory. The current travel skill remains enabled through normal skill
  loading.
- Old local voice continuity caches and `MEMORY.md`/`USER.md` are archived during
  deployment so bilingual test state is not injected into a new demo session.

The profile overlay is repeatable:

```bash
python scripts/local-ovms/apply_english_only_profile.py \
  --config ~/.hermes/config.yaml --write
```

It changes only these supported fields when necessary:

- `agent.system_prompt`
- `display.language`
- `stt.language` and `stt.local.language`
- `voice.tool_ack.phrases`
- `voice.stop_phrases`
- `voice.ready_cue.intro_text`
- `travel_voice.enabled`

It writes atomically, preserves the config file mode, does not print values, and
is idempotent. Deployment still creates a protected copy of the original config.

## Prompt reduction

Counts use the deployed Qwen3.6 tokenizer. They compare the prior baseline to
this change; not every item is injected on every turn.

| Component | Before | After | Saved |
|---|---:|---:|---:|
| General voice contract | 250 | 133 | 117 |
| Voice fallback prefix | 271 | 261 | 10 |
| Continuity router prompt | 1,095 | 334 | 761 |
| Continuity router schema | 256 | 234 | 22 |
| Task router prompt | 520 | 190 | 330 |
| Task router schema | 148 | 126 | 22 |
| Travel skill | 2,089 | 712 | 1,377 |
| Home skill | 1,058 | 429 | 629 |
| Media skill | 913 | 332 | 581 |
| **Measured total** | **6,600** | **2,751** | **3,849 (58%)** |

This reduces instruction competition and latency pressure. It does not prove or
guarantee a particular hallucination rate; behavioral regression and live local
model checks remain the acceptance criteria.

## Compatibility boundaries

- Keep `custom/qwen3.6-35b-a3b` on local OVMS. Do not add a cloud LLM fallback.
- Keep `Hi Intel`, `That's all`, four-second silence fallback and the current
  working audio-device/TTS implementation unchanged.
- Keep email recipients and credentials unchanged. Never copy secrets into Git.
- Keep Gateway platform connectivity. English-only generation does not require
  removing Weixin or Feishu.
- Do not activate Yuanbao, DingTalk, QQ, or other unrelated platform toolsets in
  the Box local voice profile.

## Deployment and rollback

Before deployment, back up every replaced source/config/state file under a new
timestamped directory. Stop the local voice process, replace only reviewed files,
apply the profile overlay, archive the disabled `travel-voice` plugin and old
voice continuity state, then restart Gateway, the simulated-home service and the
interactive voice launcher.

The home simulator migrates its legacy bilingual table to `id`, `name`, `state`
while preserving every device's on/off value. Back up its SQLite database before
the first post-upgrade start.

Rollback restores the timestamped files and databases, removes newly copied
files, and restarts the same services. Never reset or overwrite the whole runtime
checkout.

## Acceptance

- The focused suite passed **248 tests in 16 files**. It covers English routing, cross-topic continuity, email
  follow-up, spoken clarification/approval, TTS truncation, demo media and the
  simulated-home schema migration.
- Active deployed source and skill paths contain no Chinese literals or `zh`
  response branches.
- The applied config reports English display/STT, an English-only acknowledgement
  catalog, English-only stop phrases and disabled legacy travel routing.
- OVMS, Gateway and the home simulator are healthy after restart, and the voice
  process reaches wake-word standby.

## 2026-09-15 Box deployment result

Canonical commits `2ebf3bdf2` and `999b5cd07` are pushed to
`origin/local-ovms-voice`. The reviewed runtime overlay is deployed to
`intel@192.168.1.34`. Its protected rollback directory is:

```text
/home/intel/hermes-ovms-setup/backups/20260915_140643-english-only
```

The Box runtime branch predates two unrelated canonical CLI APIs. Replacing the
whole current `cli.py` therefore failed fast with missing imports. The backup was
used immediately: the deployed `cli.py` and `config_defaults.py` are based on the
Box's own working baseline with only the English tool-ack/approval edits applied.
All dedicated voice modules matched the canonical pre-change baseline byte for
byte and safely received the full reviewed replacements.

Post-deployment evidence:

- The active config overlay is idempotently current and contains no Chinese text.
- An AST scan found zero Chinese string literals in active voice/demo runtime
  files; all three installed scenario skills also passed the text scan.
- The disabled legacy travel plugin, old local memory files and old voice
  continuity databases are archived, not deleted.
- The home service preserved all six states and now returns only `id`, `name` and
  `state`, with English names.
- Live Qwen routing passed travel intake, travel detail completion, unrelated RSVP
  isolation, coding passthrough, unrelated continuity isolation and reuse of a
  saved itinerary for explicit email delivery. Every decision completed in one
  router call.
- `hermes-demo-home.service`, `hermes-gateway.service`, local OVMS with
  `qwen3.6-35b-a3b`, and `hermes-box-voice` are healthy; voice reports `ready`.

The working Kokoro TTS implementation and its internal legacy provider/model
identifier remain unchanged. It receives English-only host output and adds no LLM
prompt or bilingual routing branch. Replacing that audio engine is intentionally
outside this harness/tool cleanup because it would risk the already accepted
ASR/TTS path.
