#!/usr/bin/env bash
# Fast sanity check — must pass before every commit. Keep under ~10s, no API calls.
set -e
python3 -m compileall -q src scripts *.py >/dev/null
echo "smoke: compiles ok"

python3 -m src.ingest --corpus data/corpus >/tmp/smoke_ingest.out 2>&1 || {
  echo "smoke: FAILED - src.ingest crashed on the bootstrap corpus"; cat /tmp/smoke_ingest.out; exit 1;
}
grep -q "^ingest: 3380 artifacts" /tmp/smoke_ingest.out || {
  echo "smoke: FAILED - expected 3380 artifacts from the bootstrap corpus, got:"; cat /tmp/smoke_ingest.out; exit 1;
}
echo "smoke: ingest ok (3380 artifacts)"

python3 -m unittest tests.test_bootstrap_retrieval >/tmp/smoke_retrieve.out 2>&1 || {
  echo "smoke: FAILED - bootstrap retrieval regression"; cat /tmp/smoke_retrieve.out; exit 1;
}
echo "smoke: retrieval regression test ok"

python3 -m src.pipeline --question "Why is CS flagging churn risk on Acme?" --no-llm >/tmp/smoke_pipeline.out 2>&1 || {
  echo "smoke: FAILED - pipeline crashed with --no-llm"; cat /tmp/smoke_pipeline.out; exit 1;
}
grep -q "recall@50 = 3/3" /tmp/smoke_pipeline.out || {
  echo "smoke: FAILED - Q_001 recall regressed, expected 3/3, got:"; cat /tmp/smoke_pipeline.out; exit 1;
}
echo "smoke: pipeline (--no-llm) ok, Q_001 recall 3/3"

echo "smoke: ok"
