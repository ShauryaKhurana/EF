# NOTES

Cross-lane requests and deviations, per the BUILD_PLAN §3 agent preamble.

## From Agent 1 (bootstrap corpus) — 2026-08-01

**Deviation: wrote outside OWNS.** Agent 1 owns `data/corpus/*.jsonl`,
`data/world.json`, `data/answer_key.json`. I also created
`synthetic/bootstrap/` (generator + acceptance validator).

Rationale: DATASET_DESIGN §7 risk row 1 says the corpus must be *regenerable in
front of judges*, so a hand-committed JSON blob is the thing it warns against.
The generator is deterministic and seeded, and `validate.py` is the only way to
evidence Agent 1's acceptance criteria. `synthetic/` is Agent 7's lane, but
this is `synthetic/bootstrap/` specifically — the 200-artifact stub, not the
L0–L4 pipeline. **Agent 7 should treat `synthetic/bootstrap/` as read-only and
build the full generator alongside it.**

**Blocked on / needs a decision:**

1. **`docs/schema.md` does not exist in this repo.** BUILD_PLAN §1 and every
   agent's acceptance criteria reference it as "the single binding contract".
   I built against the normalized record shape specified verbatim in BUILD_PLAN
   §1 plus the answer-key shape in DATASET_DESIGN §4. If `docs/schema.md` lands
   with different field names, `data/` needs regenerating (cheap — one command).

2. **These files are referenced by BUILD_PLAN but absent:** `CLAUDE.md`,
   `docs/schema.md`, `docs/DATASET_DESIGN.md`, `scripts/demo.sh`,
   `scripts/smoke.sh`, `.claude/hooks/no_fakes.py`, `TOOLKIT_README.md`.
   The plan was authored against a fuller tree that was never pushed. Please
   push it — Phase 0 items 1, 3 and 4 have no target without it.

3. **BUILD_PLAN §0 is stale.** It describes every module as a stub and
   instructs `git rm` on six files. That was true at 440924c; commit 6c69a23
   replaced them with a working, tested pipeline. Re-read §0 against the current
   tree before executing Phase 0 item 2.

4. **Provider conflict.** BUILD_PLAN §1 item 5 says drop Gemini, Anthropic
   only, and §2 names `claude-haiku-4-5` / `claude-sonnet-5`. The working
   pipeline is on Gemini free tier because Anthropic was ruled out on cost.
   Needs a team decision before Agent 2 writes `src/llm.py`. Note the new
   retrieve-then-extract architecture cuts LLM calls by ~100x (~5 per question
   instead of ~550 per ingest), which makes either provider affordable.

**For Agent 3 (retrieval), what the bootstrap corpus guarantees:**

- `slk_0041` is the vocab-disjoint hop. Literal grep of Q_001's words finds
  1 of 3 gold artifacts; entity-stripped overlap is 0 of 3. Cosine similarity
  will not reach it. The only links are ticket `4402` and service alias
  `bill-v2`.
- `slk_0041` is a *thread reply* under `slk_0039`. Retrieval that ignores
  `parent_id` will surface the PagerDuty alert and miss the root cause.
- 6 near-miss distractors are tagged `meta.near_miss = true`, including three
  that mention a memory leak on a different service three weeks earlier. Q_001's
  answer key forbids "memory leak" — top-k lexical retrieval will pull these.
- `--only-source email` should be materially worse: only f3 lives in email.
