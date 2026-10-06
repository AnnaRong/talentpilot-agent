#!/bin/sh
set -eu

cd "$(dirname "$0")"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[setup] Created .env from .env.example. Add the approved LLM API key before using Grill Me."
fi
if [ ! -d .venv ]; then
  ./install.sh
fi
. .venv/bin/activate
if ! python -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  echo "[error] The existing .venv was created with Python < 3.10. Remove it and run ./install.sh again."
  exit 1
fi
python -m talentpilot.cli serve
