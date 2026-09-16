#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -q -r backend/requirements.txt

if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install)
fi

if [ ! -f .env ]; then
  cp .env.example .env
fi

trap 'kill 0' EXIT
(cd frontend && npm run dev) &
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8741 --reload
