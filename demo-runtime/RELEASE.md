# Ubuntu demo release branch

`release/ubuntu-demo` is the integration branch for the accepted Intel Ubuntu
voice/IM product. It starts at tag `20261023.1`, application commit
`8174860c9fcabb7c88e3a235fb3914fda8e46670` (product baseline
`docker-20261023.1`). Upstream-style `main` remains unchanged.

## Unified build release — ubuntu-demo-20261111.2

Use [UNIFIED_BUILD.md](build/UNIFIED_BUILD.md) for a single-command build.
The complete chain was tested by a newly created non-sudo Ubuntu user, cloning
from GitHub and reading declared shared assets, with Docker build cache disabled.
All stages passed; a real `--resume` reused all seven stages and repeated final
checks. Production container identities, start times and configuration hashes
were unchanged. See [the validation receipt](build/results/20261111-unified-build.json).
This test uses the same host/Docker daemon, not a clean VM; no live audio or
production service switch was performed. The previous source tag remains intact.

## Status

This developer source release preserves the accepted product behavior and adds
explicit offline build recipes plus fresh-host provisioning. Start with
[the online installer guide](build/ONLINE_DEPLOY.md). The installer downloads
pinned public materials and models and builds the application from source.
It does not automatically upgrade an existing production deployment. Desktop audio, GPU drivers and panel software remain host prerequisites.

Current product sources:

- `state-SOUL.md`, `gateway-state-SOUL.md`: actual channel personalities;
  root `SOUL.md` and `docker/SOUL.md` are not their substitutes.
- `state/skills`, `gateway-state/skills`: profile-specific skill resources.
- `plugins/{cli,gateway,services}`: product tool/plugin implementations.
- `overlays/{gateway,services}`: role-specific source variants; do not silently
  replace them with CLI sources or blindly replay historical patches.
- `wake`: AISP wake service and bridge sources.

Configuration templates now live in [profiles](profiles/README.md), with a
standalone renderer in `build/render_profile.py`. Role assembly, reference Compose, host sources and asset locks are now captured
in [assembly](assembly/README.md). Offline native/dependency and role-image build recipes are now in
[build/SOURCE_BUILD.md](build/SOURCE_BUILD.md). An explicit candidate runtime/switch flow is documented in
[build/CANDIDATE_RUNTIME.md](build/CANDIDATE_RUNTIME.md). The initial candidate passed user acceptance on 2026-11-11. Fresh-host
provisioning is now available; unattended boot installation is outside this release. Their provenance comes from `shawnxhong/hermes-demo-ops`
and the accepted images. The inventory is
`docs/MAIN_CONVERGENCE_INVENTORY_20261111.md` at ops commit `94e2853`;
its classifications remain applicable, with this release branch replacing main
as the initial integration destination. Its file/field inventories are in
`docs/main-convergence/` in that repository.

## Release evidence and boundaries

The 2026-11-11 candidate was manually accepted, then rolled back successfully.
Subsequent changes fix shared fan socket permissions, check background container
health, and allow private-file provisioning without a reference runtime. Product
prompts, tools and model selection were not changed.

The final full build chain and its validation are recorded in
[results/20261111-release.json](build/results/20261111-release.json).
This is a source release, not a claim of identical image hashes across rebuilds.
SDK/bridge and editable package build artifacts can differ while locked inputs
and package inventories are verified. The earlier manually accepted image set
remains separately identified. The final rebuilt images require target-hardware
acceptance before becoming a fleet production baseline.

No running production selectors are changed by publication. Runtime start/stop
is explicit. Existing device autostart and panel integrations are not replaced.

Models, restricted SDK distributions, service credentials, recipient lists,
recordings, queues and session databases must not be committed here. Supply
licensed external assets by verified download or local import. Existing panel
implementations remain independently managed; preserve their command interface.
OVMS remains the accepted 2026.2.1 version during this consolidation.

## Development and release workflow

Clone this branch explicitly:

```sh
git clone --branch release/ubuntu-demo https://github.com/shawnxhong/hermes-agent.git
```

For each task, fetch the release branch and use a new task branch/worktree:

```sh
git fetch origin
git worktree add -b work/laptop/my-task ../hermes-my-task origin/release/ubuntu-demo
```

Use the actual stable machine name instead of `laptop` on other machines.
Develop only in the task checkout, not in live containers or runtime trees.
Validate locally, then open a PR targeting `release/ubuntu-demo`. Do not merge
unrelated main changes or force-push shared branches. Deployment follows an
accepted tag, never automatically the moving branch HEAD. Fleet-specific
receipts, private bindings and rollback paths remain in the ops repository.
