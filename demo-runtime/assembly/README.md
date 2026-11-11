# Accepted role assembly and deployment preparation

This is the next source-build consolidation stage, based on docker-20261023.1.
It does not install services or build a complete image. It depends on the profile
renderer introduced in PR #16. No production runtime has been changed.

## Why new role manifests

The original `demo-runtime/overlays` and `source-lock.json` predate later accepted
image changes. Even the final application tag and CLI image differ in a few
files (including toolsets.py). Using only those old overlays would silently
change effective sources. `roles.json` records explicit per-role replacements,
removals, plugin files and container helpers recovered from the accepted image
IDs, with SHA256 for every captured file. Sources are text, not image layers.
These are preservation inputs, not new harness modifications. Byte-identical
records reuse existing source paths (or a shared captured file); the manifest
is authoritative. 161 records resolve to 37 newly captured files plus existing
sources, avoiding 124 duplicate copies.

Reference image IDs are in `../locks/accepted-images.json`. CLI, Gateway,
services (ASR/TTS/home), and mail have separate assemblies. The common source is
the application's Git-tracked tree; accepted differences apply last. Historical
`overlays/email-voice-cli.patch` and old overlay directories are NOT applied as
a second layer; only files explicitly named in the new manifest are used.
When changing a file listed as an override, modify its role source deliberately
and refresh its hash, or explicitly remove the override after equivalence tests.
A root-only edit to an overridden file will not affect that role.

The capture compared tracked baseline application paths; it does not promise
byte equality for cache files, untracked files, whole image filesystems, Python
environments or libraries. Tests/docs absent from reference images are recorded
as removals for faithful assembly, not deleted from the developer checkout.
A later cleanup can deduplicate role sources after behavior verification.

## Preparation commands (Python standard library)

From the repository root:

```sh
python3 demo-runtime/build/prepare_assembly.py check-sources
python3 demo-runtime/build/prepare_assembly.py role --role cli --output /tmp/hermes-cli-source
python3 demo-runtime/build/prepare_assembly.py discover-fan
python3 demo-runtime/build/prepare_assembly.py compose \
  --bindings /private/host.json --output /private/compose.json
python3 demo-runtime/build/prepare_assembly.py verify-assets \
  --manifest demo-runtime/locks/wake-asr.json --directory /path/to/wake-asr
```

Role output must not exist and its parent must exist. The output contains app,
plugins, container helpers and an assembly receipt. It is build input, not a
runnable environment. Private/untracked working files are not copied.
Do not point output at a running runtime. Hash verification detects accidental
changes to the role snapshots; intentional changes require reviewed lock updates.

## Compose and host scope

`../deploy/compose.template.json` comes from the effective accepted composition,
not the obsolete normalized snapshot. It includes OVMS and quiet-fan and pins
accepted image IDs for comparison. Rendering is therefore a reference deployment
preparation step, not proof that new images have been built from source.
`host.example.json` lists all host bindings. Complete every placeholder and use a
unique project/runtime for experiments. JSON output is accepted by Compose.

The only intentional Compose policy adjustment is `create_host_path=false` on
bind mounts: missing sockets/devices must fail instead of becoming directories.
No directories, device nodes, services or credentials are created by rendering.
The GPU/port/networking defaults otherwise preserve the baseline. Host fields
are strict; behavior overrides cannot be smuggled through them.

Fan discovery identifies TFN1 and acpi_fan by device type/name, rather than a
fixed cooling_device/hwmon number. It refuses ambiguous or unsupported hardware.
Run discovery immediately before rendering/start after a reboot. Automatic boot
integration of rediscovery is still pending; a once-rendered Compose can become
stale. This does NOT repair the currently failed production fan bind. Fan control
remains disabled in wake configuration; the accepted helper is still represented.

`../host` preserves controller, media, recording-quiet and desktop-autostart
sources with provenance. The lifecycle script is the accepted runtime version;
ops later added host_control_defaults to fresh installs. Its legacy kit prepare
path is NOT a new source installer. A versioned host installer, systemd template
rendering, private mail/IM provisioning and automatic hardware rediscovery remain
to be wired. Do not execute these host scripts as a generic install procedure.
Existing Dragonfly panel code is not included or overwritten.

## External inputs and remaining build work

`../locks/{llm,question-asr,wake-asr,tts,prompt-audio,lifecycle-audio}.json` contain
per-file sizes/hashes from normalized accepted assets. They are asset checks,
not embedded weights or a guarantee of public download availability. Full large
asset rehash was not repeated in this phase; the verifier is tested on fixtures.

The image/dependency lock includes accepted SDK, bridge and AEC identities and
OVMS 2026.2.1. AISP archive/source access and redistribution rights must be
explicitly provisioned. There is no assumption that a SHA256 image ID can be
pulled from a public registry. Needed external groups remain:

- LLM/ASR/TTS weights, tokenizers, generation configuration and voices;
- AISP pause/resume-v2 SDK source/distribution and build prerequisites;
- Python wheels/environments, OS packages, OpenVINO/GPU libraries, PortAudio;
- licensed cue audio and legacy KWS resources;
- private SMTP/Brave/IM configuration and fresh runtime data directories.

`../build/aec-reference` preserves the accepted AEC patch and input lock. Its
historical Docker parent still requires replacement by an explicit dependency
recipe before a clean build can be claimed. It is not executed by preparation.
The wake service/bridge/config remain in `../wake`; fan/user quiet helper sources
are in `../host/recording-quiet`. Native-library builds and source-to-image
integration are the next phase, followed by isolated runtime acceptance.

## Validation in this phase

- 10 profile/assembly tests pass (binding rejection, scope preservation, private
  output, tracked-only assembly, role overrides/removals, corruption/path escape,
  fan renumbering and ambiguous hardware).
- 161 captured role source records pass checksum verification.
- All four roles materialize successfully into disposable directories.
- Rendered Compose equals accepted effective Compose with the single bind-policy
  change stated above; Docker Compose validates the result without starting it.
- Host Python and shell syntax checks pass. No real mail/IM/audio actions performed.
- Captured source whitespace is retained byte-for-byte to preserve accepted
  hashes; inherited trailing whitespace is not silently reformatted. New
  preparation code and documentation pass whitespace checks.
