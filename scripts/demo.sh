#!/usr/bin/env bash
# THE judged command. One shot, hands off. Judges pick or modify the question.
#
#   scripts/demo.sh "Why is CS flagging churn risk on Acme?" [corpus_dir]
#
# Frozen per BUILD_PLAN §1.4 — this is the only line that changes when a stage
# (extract/crossref/alerts) lands; src/pipeline.py picks it up automatically.
set -euo pipefail
QUESTION="${1:?usage: scripts/demo.sh \"<question>\" [corpus_dir]}"
CORPUS="${2:-data/corpus}"
mkdir -p out
python3 -m src.pipeline --corpus "$CORPUS" --question "$QUESTION" --out out/answer.md
