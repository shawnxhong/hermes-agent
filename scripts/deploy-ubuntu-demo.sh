#!/bin/sh
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 "$REPO/demo-runtime/build/deploy.py" "$@"
