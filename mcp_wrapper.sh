#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="${MACROPRUNER_VENV:-$SCRIPT_DIR/.venv/bin/python3}"
exec "$VENV_PYTHON" "$SCRIPT_DIR/mcp_server.py" "$@"