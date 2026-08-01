#!/usr/bin/env bash
# Launch the interactive demo UI. Stdlib only — no new dependency.
set -euo pipefail
CORPUS="${1:-data/corpus}"
PORT="${2:-8000}"
python3 -m ui.server --corpus "$CORPUS" --port "$PORT"
