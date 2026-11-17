#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV="$HERE/.venv"

if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c 'import PyQt6, psutil' >/dev/null 2>&1; then
    "$VENV/bin/python" -m pip install -r "$HERE/requirements.txt"
fi

exec "$VENV/bin/python" -m haffop "$@"
