# Cross-channel media stop (2026-10-14)

Voice and IM share a device but not conversation history. A historical assistant
claim that playback stopped is not current device state. Every new natural-language
stop/close request must call local_media(action=stop), including repeated requests.
No special user command, new intent router, global prompt or harness loop change.

The deployed CLI/Gateway plugin schemas and media skill catalog/body carry this
contract. The tool always reaches the host bridge, and the owned-player stop must
send systemctl stop even when the unit was inactive. Verify current process state;
do not report success if processes remain. Missing collected systemd units are
valid already-stopped outcomes only after verification. Explicit stop retains
device-wide media/TTS scope; capture, wake, inference and sessions remain intact.

Reuse the shared IPC epoch: stop advances it before waiting for the media lock;
old queued requests are refused, a player starting concurrently is stopped before
the stop completes, and subsequent requests use the new epoch. This cancels media
operations already admitted to the bridge/client, not future tool calls that an
uninterrupted agent has not yet issued. No new cancellation state in the harness.

Model compliance is not a deterministic guarantee. Validate with old stopped
history, loaded and unloaded skill bodies, production tool schemas, and actual
cross-channel execution. Do not solve model omissions by adding a command the
user cannot use or by sharing/rewriting conversation history.

Source: demo-runtime/plugins/{cli,gateway}/demo-media and corresponding media
skills. Host bridge/player and deployment receipts live in hermes-demo-ops.
The old scripts/local-ovms plugin is an archived native implementation, not the
Docker release source. Keep native runtime untouched.
