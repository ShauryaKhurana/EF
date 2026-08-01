#!/usr/bin/env bash
# Fast sanity check — must pass before every commit. Keep under ~10s.
set -e
python3 -c "import ast,sys,pathlib;[ast.parse(p.read_text()) for p in pathlib.Path('src').rglob('*.py')]" 2>/dev/null || true
python3 -m compileall -q src scripts >/dev/null
# TODO: add one real end-to-end call on the tiny fixture once the pipeline exists:
# python3 -m src.pipeline --input data/clean/tiny --dry-run
echo "smoke: ok"
