# Stage 2: scene scope and direct schemas

Candidate only, 2026-09-17. Parent: `fa760c864`. Runtime remains `264348292`.
No automatic deployment, stage-three routing/summary changes or stage-four
recovery limits. The 30/60-second parameter budgets remain planned, not active.

## Implemented boundary

- Remove the home plugin's unconditional behavior prompt. Registration now
  advertises tools only; procedures live in the home skill.
- The CLI scene controller supplies an immutable session/generation snapshot
  when constructing an agent. Its capability sets come from configured scene
  items; known demo defaults stay in `hermes_cli/voice_scenes.py`, not core tools.
  `items.<scene>.tools` optionally declares that scene's dedicated tool names.
  Existing home configurations without this field inherit its two native tools.
- Restrict scene skill discovery, prompt index and actual `skill_view` loading.
  General skills remain available. Cache keys include the scene capability sets;
  disk skill snapshots remain complete and reusable across channels.
- Filter dedicated tools before bridge catalog/schema assembly. Active scene
  tools keep concrete, sanitized schemas without the describe/call indirection.
  This never enables tools excluded by the existing session toolset selection.
- Check scope at executor authorization, bridge entry and registry dispatch.
  Scene restrictions are ContextVars, propagated through existing worker
  context support. Native/IM sessions without a scope remain unchanged.
- Button acceptance revokes the old snapshot before interruption. New dispatch
  from that generation is denied; existing scene reset clears old session
  context/pending follow-ups and keeps the existing late-output/audio fences.
  Revocation cannot undo a write already dispatched or sandbox native terminal
  code. It does not introduce a replacement cancellation mechanism.
- Prompt audit metadata now records global/scene/task/history scope and the
  CLI activation source; these labels are not extra model instructions.
- Home skill queries state only for relevant requests. Read-only ambiguous
  bedroom questions report all matching devices. Ambiguous control targets
  still require a short confirmation; no state or authorization is fabricated.

## Deliberate intermediate limitation

Only the existing button-selected boundary activates scenes at this stage.
With no active scene, dedicated scene skills/tools are unavailable; ordinary
tools and skills remain available. Automatic scene selection for a new spoken
task belongs to stage 3's single model router. No keyword parser was added.
Do not deploy this intermediate candidate as the completed generalized voice
workflow. No task histories were migrated or production data cleared.

## Validation

Final combined run: **322 passed, 0 failed**, across 12 test files.

The focused scope tests exercise real skill discovery/loading/index caches,
real tool registry and sequential/concurrent dispatch, bridge denial, active
schema visibility, ordinary-toolset exclusion, revocation, and unscoped channel
isolation. The scene controller test covers home -> travel session replacement,
old-generation denial, retained general tools and unaffected unscoped IM.

Adjacent suites cover prompt construction/auditing, skill tools, bridge search,
thread context propagation and stage-one diagnostic reliability. Final command:

```bash
scripts/run_tests.sh -j 4 -q tests/agent/test_scene_scope.py \
 tests/cli/test_voice_scenes.py tests/agent/test_prompt_audit.py \
 tests/agent/test_prompt_builder.py tests/agent/test_system_prompt.py \
 tests/tools/test_skills_tool.py tests/tools/test_skills_tool_profile_scope.py \
 tests/tools/test_tool_search.py tests/tools/test_tool_search_multiquery.py \
 tests/run_agent/test_tool_executor_contextvar_propagation.py \
 tests/agent/test_tool_protocol_diag.py tests/agent/test_tool_protocol_review.py
```

### Real OVMS direct-versus-bridge probe

Both requests used Qwen3.6, candidate assets, production plugin/toolset selection,
the actual voice prefix, the same bedroom query and home skill, in isolated
temporary profiles. No tools, mail or audio were executed. Each request was
capped at 512 output tokens for this capture-only probe.

| Variant | First returned call | JSON | Attempts | Observed request time |
| --- | --- | --- | --- | --- |
| Direct concrete schemas | `demo_home_status`, `{}` | valid | 1 | 23.862 s |
| Bridge comparison | `tool_describe` | valid | 1 | 23.876 s |

Receipts (host-local, not committed):
`/tmp/hermes-protocol-diag-gw7iwqid/protocol.jsonl` and
`/tmp/hermes-protocol-diag-7xnngkkt/protocol.jsonl`.
There is no measured first-request latency improvement. The direct variant
avoided an initial discovery call in this sample; two probes cannot establish
general reliability or total-task latency. Full travel -> home -> unrelated
conversation, clarification and ASR/TTS acceptance remain later-stage gates.

Reproduce using `scripts/local-ovms/diagnose_tool_protocol.py --profile <home>
--wire --scene home`, with `--bridge` for the comparison. Only the isolated
probe changes the model-facing schema assembly for the bridge variant.
