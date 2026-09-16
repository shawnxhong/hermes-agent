# Audio cancellation during scene switching

An explicit `stop_playback()` is not a backend failure. Previously, terminating
ffplay produced a nonzero exit code and caused playback to retry through aplay.
This could replay the speech that a scene button was intended to interrupt.

Playback now captures a cancellation generation. Stop increments it under the
same lock used to start/register players. A cancelled call returns a handled
outcome without trying another backend. Genuine player failures retain fallback.
An old worker only clears its own process reference, and cancelled sounddevice
workers cannot stop a newer scene announcement during cleanup.

INFO logs record process interruption and suppressed fallback. DEBUG logs record
backend, process ID and generation without speech contents or credentials.
No prompt, model, skill, scene protocol or IM behavior changes are included.

Regression coverage includes fake-player failure/cancellation, sounddevice
cancellation, process ownership and actual OS-child termination without audio.
Run through `scripts/run_tests.sh tests/tools/test_voice_playback_cancel.py`.
Scene contracts remain in `tests/cli/test_voice_scenes.py`.

The original box_b acoustic incident cannot be conclusively attributed until
retested on that hardware; it was rebooted before diagnosis and is offline for
BIOS work. This fix addresses a reproducible playback cancellation defect.
