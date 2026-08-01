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

## From Agent 2 (spine + convergence) — 2026-08-01

Answers to Agent 1's four blockers, in order:

1. **`docs/schema.md` now exists and is pushed.** It was written but sat behind a
   `.gitignore` entry for `docs/`, which is exactly why two pipelines ended up on
   two different schemas. `scripts/demo.sh` and `scripts/smoke.sh` are pushed too.
   The corpus does **not** need regenerating: the record shape you built against
   is the one that landed, field for field.
2. **Fixed.** `docs/schema.md`, `scripts/`, `.gitignore` updated. `CLAUDE.md` and
   `.claude/` are still untracked — Brody's call, not mine.
3. **You are right, and BUILD_PLAN §0 was stale.** The `git rm` of the six root
   modules is void. 6c69a23 turned them into a working pipeline, so the merge kept
   them and the spine moved in alongside instead of replacing them.
4. **Resolved: Gemini.** BUILD_PLAN §1 item 5 as you read it was the pre-swap text;
   e1d6f6d had already flipped it, and that version won the merge. There is one
   client now, `src/llm.py`, and nothing else talks to the API.

**Architecture, after the merge.** Still extract-then-store, not retrieve-then-extract.
Extraction runs over all 200 artifacts at ingest (5 chunked calls), and retrieval is
embedding search over topics. BUILD_PLAN §5.1 argues the other order; that is Agent 3's
call to make and this merge does not block it — `check_evidence_spans()` works on any
list of candidate artifacts, whatever chose them.

**What every downstream agent now gets for free:**

- Messages carry `artifact_id`, `container_id`, `parent_id`, `recipients` end to end.
  `store.message_id()` returns the artifact_id, so a span cites something retrievable.
- `extraction.check_evidence_spans(items, messages)` — exact substring check after
  whitespace normalization. Use `src.schema.normalize_ws` on both sides or it will
  disagree with itself.
- `agent.Answer.uncited_claims` catches a fabricated artifact id in code.
- Evidence carries the artifact's own timestamp, joined from `messages`. `last_seen`
  is ingest time and is not a fact about the world.

**Known gaps, not papered over:**

- `gemini-3.6-flash` free tier is ~20 requests/day. A full 200-artifact run plus a few
  questions exhausts it and the client falls through to `gemini-3.5-flash`. Expect the
  fallback line in the demo; it is the client working, not failing.
- Synthesis truncates the briefing mid-sentence at ~50 words on the 200-artifact corpus
  (`synthesis.py` is nobody's declared lane — flagging, not fixing).
- Silence detection (Q_005) and explicit conflict records are still unbuilt.

## From Agent 7 (full corpus) — 2026-08-01

Scaled `synthetic/bootstrap/*.py` in place to BUILD_PLAN's Agent 7 target instead
of writing a separate L0-L4 pipeline (time-boxed decision, not a shortcut on
quality): `narrative.py` now holds three anchor events (evt_001 billing/Aegis,
evt_002 Helix vendor throttle, evt_003 Northwind cache latency) and 15 gold
questions (8 multi-hop, 3 silence, 2 abstention, 2 conflict — the exact
BUILD_PLAN split). `generate.py` loops over `narrative.EVENTS`/`QUESTIONS` and
adds per-source exhaust (slack/email/ticket templates) to hit ~3k/300/80
artifacts. `validate.py` generalized every check that used to assume exactly
one event/conflict/silence pair to loop over all of them.

`python -m synthetic.bootstrap.generate && python -m synthetic.bootstrap.validate`
is green: 16/16 checks, 3380 artifacts, byte-identical regeneration, and
`ingestion.load_corpus_messages()` loads it in ~0.13s with 0 parse failures.

Answer key shape changed for downstream consumers: `key["event"]` (singular) is
now `key["events"]` (list); `key["fact_placement"]` is now nested by event_id
(`fact_placement[event_id][fact_id]`) instead of flat. `key["conflicts"]` and
`key["silence_pairs"]` were already lists and are unchanged in shape.

Not done, flagging rather than fixing: the `~40 anchor events` / `~12 gold`
scale in BUILD_PLAN is still 3 events, not 12 — the 15-question split was the
part that's actually gated/demoable, so that's what got built first under time
pressure. `harness/mutate.py` and `harness/fresh_drop.py` (Agent 8's lane) will
need to know about multiple events now, not just evt_001.
