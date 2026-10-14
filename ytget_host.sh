#!/bin/bash
set -e
export PYTHONUNBUFFERED=1
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$DIR/venv/bin/python" ]; then
    PY="$DIR/venv/bin/python"
elif [ -f "$DIR/.venv/bin/python" ]; then
    PY="$DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    PY="/usr/bin/python3"
fi

exec "$PY" -u "$DIR/ytget_host.py" "$@"
