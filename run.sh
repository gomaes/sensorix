#!/usr/bin/env bash
# Convenience launcher: creates a virtualenv on first run, then starts the GUI.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "==> .venv を作成しています..."
    if command -v uv >/dev/null 2>&1; then
        uv venv .venv
        uv pip install --python .venv/bin/python -r requirements.txt
    else
        python3 -m venv .venv
        .venv/bin/pip install --upgrade pip
        .venv/bin/pip install -r requirements.txt
    fi
fi

exec .venv/bin/python -m hwmonitor "$@"
