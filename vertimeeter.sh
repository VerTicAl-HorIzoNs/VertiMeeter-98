#!/usr/bin/env bash
# VertiMeeter launcher
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [VertiMeeter] [ERROR]: no venv, run ./install.sh first" >&2
    exit 1
fi
PYTHONPATH="$DIR/src" exec "$DIR/.venv/bin/python" -m vertimeeter "$@"
