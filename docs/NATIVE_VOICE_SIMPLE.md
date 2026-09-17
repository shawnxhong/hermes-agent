# Native voice simplification — laptop candidate

Base: `264348292419a4644b1953dd08a9b812caf9d03e`.
Branch: `work/laptop/native-voice-simple`. This is independent of PR 13 and
does not reintroduce the dd99230d9 routing/summary pipeline.

## Active path

ASR -> acknowledgement concurrent with inference -> native Hermes tool loop
-> assistant content streamed by sentence to existing TTS queue.

General-voice and travel-voice workflow hooks are no longer registered.
Demo-media retains its native tool, not its private model workflow. Demo-home
retains its tools without a global scenario prompt. Historical helper modules
remain for compatibility, but are not installed as active workflows.

`[Voice input]` is a modality marker, not a second task prompt. SOUL supplies
short, direct English guidance. No separate router, answer summary, automatic
email, or hard truncation of the native answer is introduced. Native retries,
permissions, tool schemas and scene cancellation remain unchanged. Native
session-title generation may still make an auxiliary request; it is not an
answer rewrite. No keyword intent router or strict scene permission boundary
is added by this change.

## Skills

- `travel-concierge`: English Wikivoyage, one focused search and at most one
  relevant extraction. Brief grouped itinerary; ask only for missing essentials.
- `flight-search`: Expedia public route/fare information. Search snippets are
  not verified availability or a date-specific booking quote.
- `demo-home-assistant`: fresh state for relevant requests, existing simulator
  tools and control confirmations; no every-turn background query.
- `local-media-player`: list Desktop, play requested item, stop owned player;
  no computer-use escalation or playback-completion polling. Player helper is
  in the plugin, not executable skill content.
- `email-results`: explicit request only, native send_message transport,
  configured defaults or a one-send recipient override, truthful delivery status.

All five workflows are Markdown instructions. Their behavioral guidance is
not a hard security boundary. Other skills remain available for general tasks.

## Validation

108 tests passed across native speech delivery, response policy, follow-up,
sentence delivery, scene switching, home and media. SDK streaming test verifies
speech is queued before stream completion and thinking/tool arguments are not
spoken. Retry-prefix tests cover no repeated speech and cancellation. Existing
legacy helper tests passing does not mean their workflow hooks remain active.

`scripts/local-ovms/check_native_voice.py --profile ~/.hermes --case mixed`
uses the actual local Qwen/OVMS, production toolset selection and real skill
discovery, but fixtures external actions and captures speech without playback.
The isolated gateway availability is explicitly simulated. It does not verify
SMTP delivery, real microphone quality, or physical interruption of playback.
Use `--case web` for real Brave queries constrained to the two chosen sites.
Both fixed-site Brave queries returned relevant results on 2026-09-17.

Initial probes exposed missing SOUL and gateway availability in the test
profile; these probes are not acceptance passes. The corrected script loads
SOUL even while omitting unrelated project context. Cold prefill remains
substantial; removing presentation-model passes is not a claim of instant
first-turn inference.

Corrected mixed-session probe: greeting (14.5 s), home status (7.0 s), unrelated
RSVP explanation (3.1 s; first sentence 2.7 s), explicitly requested email
(42.1 s). The first three made zero mail calls; the last loaded email-results
and made exactly one captured send_message call. This proves native selection
with the isolated gateway fixture, not real delivery. The greeting also caused
one native nonstreaming title request. No presentation router/summary ran.
These single-run timings are diagnostic observations, not latency benchmarks.

## Deployment / rollback requirements

Production remains on 264348292 until candidate activation. Do not merge or
deploy the unrelated diagnostic worktree. Do not change box_a or box_b.

Before activation, preserve current symlink, config, SOUL, environment mapping
and launcher paths in a restricted local rollback journal. Build app and managed
skills/plugins from an exact commit. Install this SOUL into the profile and set
`wake_word.start_new_session: false` to retain native follow-up context; scene
buttons and explicit clear still reset. Keep sentence_pipeline enabled.
Verify native email home-channel defaults match the existing recipient list;
do not overwrite addresses from an old document or copy secrets into Git.
The send_message tool requires a running gateway under the existing policy.

Do not use the old one-time activation script unchanged: it assumes a legacy
directory rather than today's symlink and reapplies a broad voice overlay.
Use a narrowly scoped reversible activation. Existing live sessions must be
restarted to load code; changing files alone does not prove deployment.

Manual acceptance: greeting, travel -> home -> unrelated question, explicit
email/default and overridden recipient, media play/stop, wake follow-up and
scene interrupt. Check acoustic latency, no duplicate speech and actual receipt.
Rollback restores the journaled profile and current pointer to 264348292;
do not reset or delete either development worktree.
