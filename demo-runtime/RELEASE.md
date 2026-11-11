# Ubuntu demo release branch

`release/ubuntu-demo` is the integration branch for the accepted Intel Ubuntu
voice/IM product. It starts at tag `20261023.1`, application commit
`8174860c9fcabb7c88e3a235fb3914fda8e46670` (product baseline
`docker-20261023.1`). Upstream-style `main` remains unchanged.

## Status

This branch preserves the accepted application sources. A standalone Ubuntu
source-build workflow is **not yet complete**. The root Dockerfile alone does
not reproduce the full fleet deployment. Creating this branch does not install,
restart or switch any service and does not establish a new production baseline.

Current product sources:

- `state-SOUL.md`, `gateway-state-SOUL.md`: actual channel personalities;
  root `SOUL.md` and `docker/SOUL.md` are not their substitutes.
- `state/skills`, `gateway-state/skills`: profile-specific skill resources.
- `plugins/{cli,gateway,services}`: product tool/plugin implementations.
- `overlays/{gateway,services}`: role-specific source variants; do not silently
  replace them with CLI sources or blindly replay historical patches.
- `wake`: AISP wake service and bridge sources.

Configuration templates now live in [profiles](profiles/README.md), with a
standalone renderer in `build/render_profile.py`. Reusable build/host scripts
and dependency manifests still need consolidation from `shawnxhong/hermes-demo-ops`. The inventory is
`docs/MAIN_CONVERGENCE_INVENTORY_20261111.md` at ops commit `94e2853`;
its classifications remain applicable, with this release branch replacing main
as the initial integration destination. Its file/field inventories are in
`docs/main-convergence/` in that repository.

## Build closure work, in order

1. Consolidate sanitized voice/IM profiles and their schemas. Preserve channel
   differences. Fold in the three accepted voice values: wake buffer 1.0 s,
   beep volume 0.3, endpoint EOS guard 1.0 s. Keep private values out of Git.
2. Declare final role assembly, SOUL/skills/plugin sources, native-library and
   model locks. Audit services separately. Do not use historical source-lock
   snapshots as proof that all later changes are captured.
3. Parameterize reusable build, Compose and host entrypoints. Remove dependency
   on a pre-existing production runtime or historical product-image chain.
   Discover hardware bindings at startup; fixed hwmon indices are not portable.
4. Build in an isolated directory from this checkout plus declared external
   assets. Third-party OVMS/system dependencies may use pinned binary inputs;
   owned native components need explicit source recipes and provenance.
5. Compare effective configuration and role contents; smoke test then manually
   accept voice/IM, wake/recording/end phrase, media stop, delivery, scene switch,
   network fallback and reboot behavior. Preserve a production rollback path.
6. Only then publish a new immutable tag and matching release/asset manifests.

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
