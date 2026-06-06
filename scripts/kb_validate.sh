#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -x "$HOME/.hermes/hermes-agent/venv/bin/python3" ]]; then
  PYTHON_BIN="$HOME/.hermes/hermes-agent/venv/bin/python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=src "$PYTHON_BIN" -m hermes_cgm_agent.cli kb-validate
