# Wake-service media stop removed

The wake ASR service no longer stops playback directly. It has no `media_stop`
configuration or media-bridge RPC worker. A stop phrase during recording follows
the ordinary EOS path to Hermes; a stop phrase alone in wake mode does not wake
Hermes. Feishu and normal Hermes media tools retain their existing stop behavior.

The migration removes the ten hints previously added specifically for the local
stop shortcut and retains wake hints, aliases and unrelated user hints. The wake
container no longer mounts the host media-control socket. Historical deployments
and their matching configurations are retained only for rollback.

See [SDK configuration](SDK_0_4_1.md) for language, hotwords, VAD and AEC settings.
