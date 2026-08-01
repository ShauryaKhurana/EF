#!/usr/bin/env bash
# Fast sanity check — must pass before every commit. Keep under ~10s.
set -e
python3 -m compileall -q src scripts *.py >/dev/null
echo "smoke: ok"
