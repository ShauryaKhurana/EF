#!/usr/bin/env bash
# THE judged command. One shot, hands off. Judges pick or modify the question.
#
#   scripts/demo.sh "Why is CS flagging churn risk on Acme?" [corpus_dir]
set -euo pipefail
QUESTION="${1:?usage: scripts/demo.sh \"<question>\" [corpus_dir]}"
CORPUS="${2:-data/corpus}"
echo "== COO Oracle =="
echo "corpus:   $CORPUS"
echo "question: $QUESTION"
mkdir -p out
python3 orchestrator.py --source corpus --corpus "$CORPUS" --dry-run
python3 agent.py "$QUESTION" | tee out/answer.md
echo "--- answer ---"
cat out/answer.md
