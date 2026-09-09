# Local OVMS Voice Demo Handoff

Updated: 2026-09-09
Host: `agentdemo@10.239.136.211`  
Repository: `https://github.com/shawnxhong/hermes-agent`  
Branch: `local-ovms-voice`

## Start here

2026-09-09 manual Melbourne test failed; acceptance is reopened. See
[incident and model-first reset](VOICE_MODEL_FIRST_RESET.md). User requests
local Qwen3.8-35B-A3B non-thinking first, then less task-specific harness control.
Exact model artifact needs confirmation; no runtime changes for this reset.
Do not add another destination-specific patch.

Current release: `5dc8172ba`, pushed and narrowly deployed; voice continuity is
enabled. See [release validation and rollback](VOICE_CONTINUITY_RELEASE.md).
219 focused source/merged-runtime tests passed; installed native/IM/typed isolation,
mixed-topic and email follow-up replays passed. A real validation email was
received and confirmed by the user. Qwen/OVMS remains local-only; default recipient
is `xiaoheng.hong@intel.com`. Existing CLI processes must restart normally and
start a new conversation. Prior timing fixes remain; startup is silent and the
answer-window explanation follows the first spoken question.

The checkpoints below are historical where they differ from this release.

General voice delivery is now default-enabled (2026-09-08, `061de2a84`). See
[full-tool stability/deployment evidence](GENERAL_VOICE_STABILITY.md). 240 tests
passed; installed-code native 21-tool replay passed. Restart the interactive
voice CLI and start a **new conversation** to load its frozen delivery contract.
Travel/wake, local-only model, permissions and IM configuration are preserved.

Travel demo update: see [TRAVEL_DEMO_STATUS.md](TRAVEL_DEMO_STATUS.md).
Controlled delivery update: [TRAVEL_VOICE_WORKFLOW.md](TRAVEL_VOICE_WORKFLOW.md).
Wake acknowledgement: [LOCAL_WAKE_ACK.md](LOCAL_WAKE_ACK.md), enabled with
the cached English cue "Hi, I'm here." before command recording.
The user authorized the travel-voice plugin and default recipient
`xiaoheng.hong@intel.com`; preserve typed/IM isolation and local-only inference.
The US-English-first candidate is now default-preloaded at the user's request
for manual testing, using `agent.system_prompt`; it is not yet demo-ready.
Ordinary spoken-question follow-up has focused regression coverage; real-model
travel tool use and factual grounding still fail some acceptance scenarios.

All future development for this deployment should be performed locally on the
`.211` host, using the clean checkout at:

```text
/home/agentdemo/hermes-development/hermes-agent
```

The running installation remains at:

```text
/home/agentdemo/.hermes/hermes-agent
```

Do not run `git reset`, `git clean`, or overwrite the running installation as a
whole. It is an older, heavily modified Hermes checkout containing the deployed
voice, Feishu, email, and host integration. Make a timestamped backup of every
file changed during deployment and copy only reviewed files from the clean
development checkout.

## Implemented behavior

### Current routing and demo intent (2026-09-07)

The user restored local-only inference: default `custom` / `qwen3.6-35b-a3b`
at `http://localhost:8000/v3`. The fallback chain, `DEEPSEEK_API_KEY` in the
Hermes `.env`, and DeepSeek's cached credential-pool entry were removed.
The original user-supplied key file is untouched; protected rollback copies
remain under `backups/20260907_163823-local-only/` in `hermes-ovms-setup`.
Retain the 65,536 context cap and existing voice settings.

Read [the demo requirements](LOCAL_DEMO_REQUIREMENTS.md) before scenario skill
development. Final acceptance uses only local LLM inference, with voice as the
primary interface and IM as a text-only recovery interface. Wait for the user's
complete scenario briefing before implementing skills or beginning live tests.

### Historical DeepSeek trial (superseded)

- Default provider/model: `deepseek` / `deepseek-v4-flash`, using
  `https://api.deepseek.com/v1` and `DEEPSEEK_API_KEY` in the private `.env`.
- The single `fallback_providers` entry uses `custom` / `qwen3.6-35b-a3b`
  at `http://localhost:8000/v3` with `chat_completions` and a non-secret
  `local-ovms` placeholder key. DeepSeek credentials are not sent to OVMS.
- Retain the 65,536 session context cap and existing compression threshold.
  The custom-provider model metadata also pins OVMS to 65,536 so fallback
  activation cannot discover a larger, unsafe context window.
- Direct DeepSeek API probe returned OK. A separate real Hermes agent switched
  through `_try_activate_fallback` and its new OVMS client returned
  `OVMS_FALLBACK_OK`. This checks activation and live fallback inference,
  without disrupting the primary service to simulate an outage.
- Configuration rollback copies:
  `/home/agentdemo/hermes-ovms-setup/backups/20260907_154848-deepseek-default/`.
- GitHub terminal authentication is now configured through the system keyring;
  the earlier email/launcher repair commits have been pushed successfully.

- Local OVMS serves `qwen3.6-35b-a3b` through an OpenAI-compatible endpoint.
- Local keyboard and local voice modes can be switched with the host scripts.
- Wake word is `Hi Intel`; local ASR, mixed Chinese/English TTS, verbal
  acknowledgement, barge-in, echo protection, clarify prompts, and Dangerous
  Zone approval prompts are supported in the voice path.
- TTS is enabled only for a genuine ASR turn. Typed CLI input and Gateway input
  are text-only.
- Voice answers are sanitized and capped to three sentences, 240 Chinese/mixed
  characters, or 100 English words before final TTS playback.
- For a substantial voice request, the model may use the existing
  `send_message` email adapter and then speak a short result. Any syntactically
  valid recipient may be used without an extra authorization or confirmation
  step. If no address is known, one spoken `clarify` request opens the
  wake-word-free answer window.
- Identical email sends in one agent turn are deduplicated by session, turn,
  normalized recipient, and body hash. Failed sends remain retryable.
- Feishu keeps the native Hermes text response policy. Its session toolset does
  not expose the local voice-delivery `send_message` tool.

The implementation commits are:

```text
38979436007b7ed4a08b23f62704ef4bd0c8840a  local OVMS voice-first interaction
9d6df7060c77bcf7a9b999f967337d294b925254  concise voice + dynamic email delivery
```

## Services and configuration

### Personal-network email repair (2026-09-07)

- Use the 163 SMTP authorization token as `EMAIL_PASSWORD`; the web-login
  password is not used for SMTP. Credentials remain outside the repository.
- SMTP uses verified implicit TLS on port 465. Deploy the reviewed current
  `plugins/platforms/email/adapter.py`; the older running copy forced STARTTLS.
- Standalone SMTP now has a 30-second socket timeout and always closes its
  connection. Failure during QUIT after accepted DATA does not report failure
  and trigger duplicate delivery.
- Clash's active profile prepends exact-domain DIRECT rules for `smtp.163.com`
  and `imap.163.com`. Its previous catch-all proxy route stalled SMTP.
- Per-file rollback copies for this repair are under
  `/home/agentdemo/hermes-ovms-setup/backups/20260907_151949-email-repair/`.
- Focused email regressions: 60 tests passed using `scripts/run_tests.sh`.
- Real deployed `send_message` returned success for the user-designated test
  recipient with receipt marker `HERMES-SMTP-20260907-01`. This proves SMTP
  acceptance; recipient inbox receipt still needs recipient confirmation.
- The canonical host launcher is now `scripts/local-ovms/hermes-mode`, deployed
  to `/home/agentdemo/.local/bin/hermes-mode`. It inherits caller proxy settings,
  honors an explicitly supplied `HERMES_PROXY_URL`, and bypasses proxies for
  localhost/loopback. It no longer defaults to the Intel company proxy.
- GitHub HTTPS credentials were not migrated to this host. Repair commits are
  local; push them to `origin local-ovms-voice` once authentication is restored.
  This repair was deployed with per-file backups before remote push so the
  user's requested local email restoration could complete.

- Gateway: system service `hermes-gateway.service`, enabled at boot.
- Model server: Docker container `ovms-qwen36`, restart policy
  `unless-stopped`.
- OVMS health endpoint: `http://127.0.0.1:8000/v3/models`.
- Hermes configuration: `/home/agentdemo/.hermes/config.yaml`.
- Credentials: `/home/agentdemo/.hermes/.env`; never commit or copy its values
  into logs or documentation.
- Host setup, launchers, docs, and backups:
  `/home/agentdemo/hermes-ovms-setup`.

Useful read-only checks:

```bash
systemctl is-active hermes-gateway.service
systemctl is-enabled hermes-gateway.service
docker inspect -f '{{.State.Status}} {{.HostConfig.RestartPolicy.Name}}' ovms-qwen36
curl -fsS http://127.0.0.1:8000/v3/models
```

Restart the Gateway only after tests and after saving deployment-file backups:

```bash
sudo systemctl restart hermes-gateway.service
journalctl -u hermes-gateway.service -n 100 --no-pager
```

## Development and deployment workflow

1. Work only in `/home/agentdemo/hermes-development/hermes-agent`.
2. Keep the branch `local-ovms-voice` and use the HTTPS GitHub remote. The SSH
   identity previously resolved to the wrong GitHub account.
3. Read `AGENTS.md` before making code changes.
4. Use `scripts/run_tests.sh`; do not invoke `pytest` directly.
5. Commit and push source, tests, and non-secret docs to
   `shawnxhong/hermes-agent:local-ovms-voice`.
6. Back up each destination file under
   `/home/agentdemo/hermes-ovms-setup/backups/<timestamp>/`.
7. Copy only the tested changed files into the running installation.
8. Restart the affected service and verify Gateway, OVMS, Feishu, keyboard, and
   voice behavior.

The last focused regression run against the actual merged deployment passed 87
tests. The company proxy previously prevented a reliable live SMTP test; the
machine is now expected to use a personal network, so real delivery should be
retested without changing the tested fake-adapter coverage.

## Migrated reference material

Historical work products are preserved under:

```text
/home/agentdemo/hermes-development/handoff/
```

They include host migration scripts, wake-word and clarify experiments,
mixed-language TTS work, the last remote merge staging tree, pre-email rollback
copies, the response-policy plan, and the final email patch. These directories
are reference/rollback material; the Git branch is the canonical source for new
development.

## Immediate smoke tests

Voice, simple answer:

```text
Hi Intel，今天星期几？
```

Voice, detailed email:

```text
Hi Intel，请整理一份成都三日旅行计划，把详细内容发送到 user@example.com。
```

Expected order: immediate verbal acknowledgement, any required tools, exactly
one email attempt, then one short final TTS utterance. Without an address, the
agent should speak one clarify question and accept the next answer without the
wake word.
