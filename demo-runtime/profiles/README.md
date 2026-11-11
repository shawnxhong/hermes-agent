# Product configuration profiles

These standalone templates capture the accepted docker-20261023.1 CLI (`state`)
and IM (`gateway-state`) configuration. They replace the normalized profile
inputs from ops commit 07c1f67c59df72f453ffe240d16faca5877b2374 for new source-build
work. They do not replace existing production configuration automatically.

The CLI template incorporates the previously separate accepted values:
`voice.wake_buffer_seconds=1.0`, `voice.beep_volume=0.3`, and
`voice.endpoint.eos_guard_seconds=1.0`. The Gateway template preserves its
existing behavior, including the absence of an explicit title-generation override.
Both templates were compared to laptop's active 20261023.1 configuration with
private bindings supplied in memory: full semantic equality, including a YAML
parser round trip. No private values were exported to the repository.

## Generate a private config

Python 3 standard library is sufficient. Copy the matching
`*.bindings.example.json` to a private directory outside the checkout, chmod it
0600, and replace every `REPLACE_ME` value. The matching bindings schema lists
all allowed fields. Each profile currently has five string bindings: mail home
channel, local provider API key, two legacy recipient settings, and the legacy
Piper voice directory. Disabled legacy settings are preserved for compatibility.
No arbitrary configuration overlay is accepted. Product behavior changes belong
in the version-controlled templates and must be reviewed.

```sh
python3 demo-runtime/build/render_profile.py --profile state \
  --bindings /private/location/state.bindings.json --check
python3 demo-runtime/build/render_profile.py --profile state \
  --bindings /private/location/state.bindings.json \
  --output /private/new-runtime/state/config.yaml
```

Create the destination directory first. Existing files and symlinks are rejected;
there is no overwrite switch. Output is JSON, a YAML subset parsed by Hermes,
with file mode 0600. The tool never prints config or binding values. For IM use
`--profile gateway-state` and its separate binding file and destination.

The schema validates the **bindings**, not the whole Hermes configuration.
Templates intentionally retain accepted compatibility fields rather than
silently pruning them. Container paths and loopback service addresses currently
assume the accepted deployment layout; host/Compose portability is the next
stage. Do not change them in a private binding file.

## Scope and remaining inputs

Rendering config.yaml does not provision the complete service. SOUL and skills
remain in the adjacent profile-specific source directories. Plugins, mail worker
configuration, SMTP/Brave/IM credentials, pairing, wake-sidecar config, models,
Compose and host bindings require their own declared assembly/provisioning.
The recipient fields here do not configure the separate mail worker's entire
recipient list. Do not treat this renderer as a full installer or copy production
queues/history to fill the remaining inputs.

Tests: `python3 -m unittest discover -s demo-runtime/build/tests -v`.
