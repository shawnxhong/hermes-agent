# Local OVMS Voice Demo Handoff

Updated: 2026-09-07  
Host: `agentdemo@10.239.136.211`  
Repository: `https://github.com/shawnxhong/hermes-agent`  
Branch: `local-ovms-voice`

## Start here

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

