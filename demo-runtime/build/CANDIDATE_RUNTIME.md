# Isolated candidate runtime and explicit switch

This stage prepares local human acceptance. It is not an unattended new-machine
installer. `prepare_runtime.py` takes a source-defined profile and extracts only
its declared private binding leaves from a reference runtime. It refuses behavior
drift rather than copying arbitrary reference config. The only private files
copied are each profile's `.env`; they stay outside Git with restrictive modes.
No history, database, queue, pending messages, auth cache or pairing requests are
copied. Existing model directories are mounted read-only.

Preparation requires Python with PyYAML, the previously built images, existing
model/resource assets and a user systemd manager. A unique output directory and
Compose project are required. Example:

```sh
python3 demo-runtime/build/prepare_runtime.py \
  --reference /path/to/production-runtime \
  --output /path/to/new-candidate-runtime \
  --models /path/to/models --resources /path/to/resources \
  --checks /path/to/candidate-checks.json \
  --wake-build /path/to/wake-build --project hermes-source-test
```

The output contains separate voice/IM state, IPC, workspace, config, host helpers,
user-unit definitions and an explicit controller. The `.env` allowlist currently
retains the reference's existing environment settings for acceptance parity;
it is not an approved generic distribution of private inputs. Users needing IM
pairing state must provision it separately. The laptop reference has no files in
its pairing directories, so none were needed for this preparation.

## Laptop test commands

A new `hermes-source-test` wrapper invokes the candidate controller. The original
`hermes-box-voice start` remains untouched and still starts production.
Close any production voice CLI before activation. Then run:

```sh
hermes-source-test check
hermes-source-test activate
hermes-source-test voice
```

`activate` is the explicit switch action: it snapshots currently running
production container IDs/images/mounts and active helper units, stops producers,
drains mail, stops production, links/starts only candidate helper units and starts
the candidate background stack. It does not alter production selectors or files.
After readiness, `voice` opens the interactive CLI. Gateway is included in the
candidate only after production has stopped; never run both entrypoints during
the test. Candidate mail uses a new empty queue and the configured real recipient
list: only ask it to send when a real test delivery is intended.

When done, Ctrl+C the candidate voice terminal and run:

```sh
hermes-source-test rollback
```

Rollback closes candidate writers, waits for its queue to drain, stops its
containers/helpers/media, verifies original container identities and configuration
hashes, then restarts the original containers and previously active units. It
never copies or replays either queue. If delivery remains queued/sending, rollback
refuses to stop the worker or start a second stack. Resolve the queue first.
If startup fails after suspension began, run rollback; the saved switch journal
exists before the first stop operation. A changed original config/container
causes a refusal rather than guessed recovery. A missing candidate unit during
partial activation does not prevent restoring production.

`status` shows switch phase and candidate containers. The controller serializes
operations and detects a voice-launch process still in progress; close that
terminal before rollback. Manual actions outside this controller are not covered
by its serialization or rollback contract.

## Startup and reboot boundaries

- Fan bindings are discovered by identity at each candidate check/activation.
  The original production fan-bind issue is not repaired by this work.
- Candidate containers use `restart: no`; candidate user units are linked only
  on activation and never enabled for login. They must not restart automatically
  beside the original production autostart after a reboot.
- A reboot is not an acceptance/rollback test. Run rollback before rebooting.
- Host audio routing, panel, original autostart and original command wrappers
  are not modified. The candidate uses the same physical audio devices only
  after the production stack is stopped.

## Source-build startup probe fix

The accepted host `aisp_startup.py` executes `/opt/qwen-wake/check_startup.py`.
That file was missing from the first source-built wake image. Its accepted source
is now in `demo-runtime/wake/check_startup.py` and included by `build_wake.py`.
A new wake candidate was built; the probe was executed against an isolated fake
Unix-socket responder, without accessing production or the microphone. This test
checks packaging and handshake behavior, not actual ASR readiness/quality.

## Preparation evidence

- 20 unit tests pass, including config drift refusal, non-mutating queue reads,
  partial-activation rollback and original-container identity mismatch.
- Docker Compose and user-unit definitions pass static validation.
- Voice/IM init ran in disposable, no-dependency candidate containers; profile
  config and SOUL still match production and histories/queues remain empty.
- Production containers kept their original identities and uptime.
- Real activation, rollback, microphone, TTS, live IM and delivery acceptance
  have NOT been performed in this preparation phase.

## Post-acceptance fixes

The user accepted the initial candidate and restored production on 2026-11-11.
Review found the fan helper had exited: root without capabilities could not
access the owner-only IPC directory. Preparation now grants host-group access
(mode 2770), verified with the actual capability-dropped helper container and a
socket request. Activation now checks every required background container and
its Docker health state, rejecting an exited component instead of accepting
stale socket paths. Fresh-host provisioning is documented in FRESH_HOST.md.
