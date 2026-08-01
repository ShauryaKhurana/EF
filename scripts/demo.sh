#!/usr/bin/env bash
# THE judged command. One shot, hands off. Judges pick or modify the question.
# Frozen as of Phase 0 — do not change this file again.
#
#   scripts/demo.sh "Why is CS flagging churn risk on Acme?" [corpus_dir]
set -euo pipefail
QUESTION="${1:?usage: scripts/demo.sh \"<question>\" [corpus_dir]}"
CORPUS="${2:-data/corpus}"
echo "== COO Oracle =="
echo "corpus:   $CORPUS"
echo "question: $QUESTION"
python3 -m src.pipeline --corpus "$CORPUS" --question "$QUESTION" --out out/answer.md
echo "--- answer ---"
cat out/answer.md
