#!/bin/bash
# One desktop-owned interactive shell. Its lifetime, not the agent's, owns lock.
set -eu
if [[ ! -t 0 || ! -t 1 ]]; then
    printf 'Hermes desktop startup requires an interactive terminal.\n' >&2
    exit 2
fi
task_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/flock --nonblock --close "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/hermes-voice-terminal.lock" \
    /bin/bash --rcfile "$task_dir/session.bashrc" -i
