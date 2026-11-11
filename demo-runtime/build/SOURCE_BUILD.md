# Offline source-build candidates

This workflow builds candidates without changing a running Hermes installation.
It builds product Python code, AISP pause/resume-v2 SDK, wake bridge and patched
AEC module. Ubuntu packages, CPython, Python wheels, OpenVINO, GPU userspace,
models and selected resource/native binary assets remain pinned external inputs.
It does not claim that the entire OS or every third-party library is compiled
from source. No credentials, IM pairing, queues or session databases are inputs.

## Required inputs

Use a dedicated directory outside the checkout. The scripts refuse to reuse
existing output directories; retain failed logs and choose a new output path.
Requirements on the build host: Docker, Git, Python 3.11+ with tar extraction
filter support, dpkg-deb and patch. Docker's offline base images must already be
loaded. No script starts production services or mounts the microphone.

- `locks/os-build.json`: Ubuntu 24.04/26.04 base image IDs, offline deb paths and
  hashes, complete expected package inventories. Load the exact base images
  from a verified local archive. Build receipts permit new image IDs when the
  package inventory matches; do not assume a local sha256 ID is pullable.
- `locks/python-build.json`: CPython archive plus pinned wheels, under the
  declared relative `dependencies/` paths in a materials directory.
- `locks/sdk-build.json`: separately authorized proprietary SDK ZIP, each
  SDK dependency source/binary file, and libpng 1.6.43 ZIP URL and hash.
  The SDK source payload is intentionally NOT committed. Dependency directories
  are verified then copied into the build workspace because CMake may patch them.
- `build/aec-reference/inputs.lock.json`: upstream echo-cancel source, wavfile
  header and development debs supplied through an AEC input directory.
- `locks/runtime-assets.json`: a separate asset-only directory with GPU driver
  files, cue/KWS resources and the previously accepted patched PortAudio binary.
  No old product image or live runtime is read by the application builder.
  PortAudio is retained as a verified binary in this phase, not newly compiled.
- Model locks in `locks/`: needed for later runtime acceptance, not image build.
  OVMS remains the accepted external 2026.2.1 binary image.

Source/dependency access and redistribution rights must be supplied separately.
The libpng URL is sourced from the authorized SDK's CMake dependency declaration;
its downloaded ZIP is hash-locked before offline compilation.

## Build sequence

Commands run from the application checkout. Replace `/materials`, `/inputs` and
`/build` with local directories; do not use the live runtime as output.

```sh
python3 demo-runtime/build/build_os.py --target cli --materials /materials --output /build/os-cli
python3 demo-runtime/build/build_os.py --target wake --materials /materials --output /build/os-wake
python3 demo-runtime/build/build_os.py --target sdk-toolchain --materials /materials --output /build/os-sdk

python3 demo-runtime/build/build_python.py --materials /materials --os-build /build/os-cli --output /build/python
python3 demo-runtime/build/build_native.py --sdk-source /inputs/aisp_sdk_src_pause_resume_v2.zip --dependencies /materials/dependencies/sdk-inputs/dependencies --libpng /inputs/libpng-1.6.43.zip --toolchain-build /build/os-sdk --output /build/native --jobs 2
python3 demo-runtime/build/build_roles.py --python-build /build/python --assets /inputs/runtime-assets --output /build/roles
python3 demo-runtime/build/build_wake.py --native-build /build/native --aec-inputs /inputs/aec --materials /materials --os-build /build/os-wake --output /build/wake
python3 demo-runtime/build/check_candidates.py --roles-build /build/roles --wake-build /build/wake --gpu --output /build/checks.json
```

For cached dependency builds, omitting `--os-build` / `--toolchain-build` uses
explicitly pinned, previously rebuilt OS dependency images, not Hermes product
parents. All Docker builds and temporary build containers use network=none.
The native build uses two CPU cores and 8GB memory by default. Each stage records
input/recipe identities and results in its output directory. Successful images
are identified by `image.id` / `receipt.json`; no moving production tag is set.

Three Python environments are recreated with hash-checked wheels and no
implicit dependency resolution. `--ignore-installed` prevents pip imported from
a wheel from being mistaken for a wheel installed in the new environment.
The accepted core environment intentionally has no pip package: app source
registration uses a temporary pinned pip wheel, rather than adding pip silently.
The known Sherpa/ONNX Runtime library link is explicit in the role recipe.

The four role assemblies from `assembly/roles.json` remain distinct. The builder
includes the current profile SOUL/skills but no config with resolved private
bindings. Use `render_profile.py` during later runtime provisioning. Entry-point
legacy `init` without a provisioned config is not a supported new install flow.

Wake includes the freshly compiled SDK/bridge and an AEC module compiled with
the accepted quiesce-before-destroy patch. These binaries need not match the
old production hashes; reference hashes remain in accepted-images.json and new
hashes are recorded as candidate artifacts. A successful dlopen/EOS-flag check
is not evidence of microphone quality, EOS timing or crash-free live AEC.

## Validation and deployment boundary

`check_candidates.py` checks exact package-name/version parity in every role,
role import-smoke receipts, wake bridge/AEC loading, and optional ASR GPU device
enumeration. It does not load LLM weights, send mail/IM, capture audio or play TTS.
Import and package checks do not establish end-to-end product equivalence.

A separate next stage must generate the runtime, wire host units and hardware
bindings, provision private settings, load candidates and perform voice/IM/media/
mail acceptance with an independently verified rollback path. Do not overwrite
active selectors or declare a new production baseline from build success alone.

Tests: `python3 -m unittest discover -s demo-runtime/build/tests -v`.

## 2026-11-11 build result

See [results/20261111.json](results/20261111.json) for immutable candidate image
IDs and checks. All four application roles and wake built successfully. All
three Python inventories match in each role; ASR enumerated CPU and GPU. The
fresh AEC module exactly matches the accepted
`7ab3fde10685dd0422295a4576c0ca04ffbbb527128017b150078622431c7819` hash.
SDK/bridge output hashes differ and remain candidates pending live acceptance.

The application and native candidates used the pinned previously rebuilt OS
layers. The new OS build script also rebuilt all three targets and independently
passed exact package parity, but the entire candidate chain has not been rerun
on those new OS image IDs. This distinction matters when auditing reproducibility.
Initial Python bootstrap/role build failures were fixed by explicit pip-wheel
handling; failed/superseded local artifacts are not the images in this receipt.
No production selector, runtime configuration or container was changed.
