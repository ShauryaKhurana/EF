# Build Plan — COOless

Cross-reference of the code as it stands against `docs/DATASET_DESIGN.md` and
`CLAUDE.md`, turned into sequenced agent tasks with strict file ownership.

**Decisions taken (2026-08-01):**
- Product shape: **QA oracle.** Judge asks a question; agent answers with a
  cited multi-hop trace, or abstains. Briefing is *not* the judged artifact.
- Ingest: **local corpus files.** No live Slack/Gmail pull on stage.

---

## 0. Where the code actually is

Every module is scaffolding. Nothing in the repo runs end to end.

| File | Reality |
|---|---|
| `ingestion.py` | `fetch_slack_messages()` → `[]`, `fetch_gmail_messages()` → `[]`. `normalize_messages` drops `raw`. |
| `extraction.py` | `call_llm()` returns the literal string `"[]"`. `extract_status_items()` returns `[]` unconditionally. |
| `synthesis.py` | `call_llm()` returns `""`. |
| `output.py` | `write_to_google_doc()` returns `"new-google-doc-id"`. `__main__` posts `"Sample COO briefing text."` |
| `orchestrator.py` | Wires the four together. Running it posts an empty string to Slack and prints a fabricated doc ID. |

`orchestrator.py` today is a working simulation of a working pipeline. Under
CLAUDE.md's first hard rule that is the single worst state the repo can be in,
because it looks done. It all gets deleted, not patched.

### Four incompatible schemas

1. `docs/schema.md` normalized record — `id/source/ts/author/channel/thread_id/text/refs/raw`
2. `docs/schema.md` StatusItem — has `evidence[{record_id, span}]`
3. `schema.py` StatusItem — `sender/source/timestamp/category/summary/detail/action_items`. **No evidence field.** Deletes the anti-hallucination guarantee that `docs/schema.md` calls binding.
4. `DATASET_DESIGN.md` `artifact.schema.json` — `artifact_id/source/container_id/parent_id/sender_id/recipients/ts/text/meta`

No two agree. Nothing can be parallelized until one wins. See §1.

### Missing entirely

- **Retrieval.** The whole pitch (multi-hop, vocab-disjoint hops, single-channel
  ablation) is a retrieval claim. There is no retrieval code and no plan for it
  in `README.md`'s file list.
- **Dedup / conflict flagging.** Named in CLAUDE.md's pipeline, absent from
  `orchestrator.run_pipeline`, and `docs/schema.md` defines a `Conflict` type
  nothing produces.
- **Silence detection.** `DATASET_DESIGN` §P3 calls this the entire technical
  basis for proactive alerting. No code, no consumer.
- **`src/`.** `scripts/demo.sh` runs `python3 -m src.pipeline`; `scripts/smoke.sh`
  compiles `src/`. The directory does not exist. Code is flat at repo root.
- **`data/`.** No corpus, not even the 200-artifact bootstrap that
  `DATASET_DESIGN` §6 calls non-negotiable.

### The architectural mismatch worth naming

`CLAUDE.md` describes a batch summarizer: *per-unit LLM extraction over
everything → dedup → briefing*. That architecture actively destroys the
property the corpus is designed around. Pre-extracting every message
normalizes `"pool's maxed again on bill-v2"` into `"connection pool
exhaustion"` — which makes the vocab-disjoint hops (§P2) trivially searchable
and the graph layer pointless. You would spend nine hours building a dataset to
defeat flat search, then flatten it before search.

**Resolution:** extraction moves *after* retrieval. Retrieve over raw text,
extract StatusItems only from the ~40 retrieved candidates. Every module
CLAUDE.md names survives; only the order changes. `CLAUDE.md`'s pipeline line
must be updated to say so.

### Non-issues (checked, not assumed)

- `python3` (3.13.14) and bash 5.2 both resolve on this machine. `demo.sh` and
  `smoke.sh` run as written. No Windows rewrite needed.
- `.claude/hooks/no_fakes.py` relaxes to secrets-only for paths matching
  `test|fixture|synthetic|generate_data|seed`. Put the corpus generator under
  `synthetic/` so it isn't blocked for containing the word "fake".

---

## 1. Phase 0 — freeze the contracts by hand (~20 min, do not delegate)

Nothing below can start in parallel until this lands. It is four small edits.

**Normalized record — adopt the `DATASET_DESIGN` field names**, plus `raw`.
The corpus generator emits these and `answer_key.gold_artifacts` references
`artifact_id`; matching them means one rename instead of two.

```jsonc
{
  "artifact_id": "slk_00a41f",
  "source": "slack",            // slack | email | ticket | doc | export
  "container_id": "#eng-aegis", // channel | thread key | ticket key | doc path
  "parent_id": "slk_00a3f0",    // null if top-level — thread burial depends on this
  "sender_id": "EMP_141",
  "recipients": ["EMP_082"],    // [] for public channels — silence pairs depend on this
  "ts": "2026-05-14T13:55:22-07:00",
  "text": "pool's maxed again on bill-v2. who merged 4402",
  "meta": {},
  "raw": {}                     // never dropped
}
```

**StatusItem — `docs/schema.md`'s version wins verbatim.** The `evidence`
array is the anti-hallucination guarantee and the thing you point at on stage.

**Actions:**
1. Rewrite `docs/schema.md` to the above; it stays the single binding contract.
2. `git rm schema.py ingestion.py extraction.py synthesis.py output.py
   orchestrator.py` — they are replaced wholesale under `src/`, and leaving them
   importable invites an agent to wire the fakes back in.
3. Edit `CLAUDE.md`: pipeline line becomes
   `corpus ingest → index → multi-hop retrieve → extract StatusItems → dedup/conflict → cited answer or abstention`.
4. Rewrite `scripts/demo.sh` **once, now**, then freeze it forever:
   ```bash
   python3 -m src.pipeline --corpus "${2:-data/corpus}" --question "$1" --out out/answer.md
   ```
   It currently takes only an input path, which cannot express a question. This
   is the last time it changes.
5. `requirements.txt`: drop `anthropic`, `google-api-python-client`,
   `google-auth-oauthlib`. Gemini only — two providers is dead weight, the
   Gemini API has a usable free tier (no card required for a hackathon), and
   the Anthropic branches in `extraction.py`/`synthesis.py` are dead code.

---

## 2. Target layout

```
src/
  schema.py      Artifact, StatusItem, Conflict, Answer + validators
  llm.py         one Gemini client: retries, JSON coercion, cost log
  ingest.py      corpus/*.jsonl → Artifact, per-record fail-loud
  index.py       alias table + entity index + BM25 + thread/time indexes
  retrieve.py    multi-hop entity traversal, returns candidates + hop paths
  extract.py     candidates → StatusItems with verbatim evidence spans
  crossref.py    dedup, conflict flagging, supersession/staleness
  answer.py      StatusItems → cited answer, or abstention + who-would-know
  alerts.py      silence detection (proactive)
  pipeline.py    CLI entrypoint
synthetic/       corpus generator (DATASET_DESIGN L0–L4)
data/
  corpus/*.jsonl  world.json  answer_key.json
harness/         mutate.py, inject.py
```

---

## 3. Agents

Strict file ownership — no two agents write the same path, so anything marked
parallel can run in a separate terminal with zero merge pain. Preamble for
every agent:

> Read `CLAUDE.md`, `docs/schema.md`, and `docs/BUILD_PLAN.md` first. You own
> only the paths under OWNS; never edit outside them — write requests to
> `NOTES.md` and continue. Fail loud and specific; never `except: pass`; never
> drop a record that doesn't parse — surface it as "couldn't parse X because Y".
> One bad record must not kill the run. You are done only when your acceptance
> command passes and you have pasted its real output.

### Agent 1 — Bootstrap corpus  ⟵ **start first, blocks everything**
**Owns:** `data/corpus/*.jsonl`, `data/world.json`, `data/answer_key.json`

`DATASET_DESIGN` §6 H+0:30, the item it calls non-negotiable. ~200 artifacts,
templated not LLM-generated, covering **one** incident narrative end to end:
six facts partitioned across four channels per §P1, one vocab-disjoint hop, one
conflict pair, one silence pair, one deliberately unanswerable question. Plus a
`world.json` with ~15 employees (one first-name collision), 2 projects with
drift aliases, 4 services, 2 clients, 5 channels with membership. Plus 6
questions with full answer-key records per §4.

Ugly and small is correct. The point is that Agents 2–6 build against real
files today instead of waiting on the 90-day corpus.

**Acceptance:** every line of every `.jsonl` validates against `docs/schema.md`;
no `_prov` field anywhere in `data/corpus/`; every `gold_artifacts` id resolves;
`grep` for the question's content words finds < 2 of the ≥3 gold artifacts
(proving the multi-hop is real, not decorative).

### Agent 2 — Spine: schema, LLM client, ingest  *(after Phase 0)*
**Owns:** `src/schema.py`, `src/llm.py`, `src/ingest.py`

- `schema.py`: dataclasses + a `validate_*` per type that returns
  `(ok, reason)`. Assume every field is missing, null, wrong-typed, or weirdly
  formatted — timestamps especially (epoch floats, `Z` vs offset, naive strings
  all appear in real Slack/Gmail exports). Normalize to tz-aware UTC; an
  unparseable ts is a surfaced warning on the record, not a dropped record.
- `llm.py`: real Gemini client (`google-generativeai`), on the free tier.
  Extraction and answering both on `gemini-2.5-flash` (latency matters more
  than depth on stage, and the free tier's per-minute request cap is the real
  constraint — one model keeps the budget simple). Retry on 429/5xx with
  backoff that respects the free-tier RPM limit, hard timeout, running cost
  counter printed at end of run (should print `$0.00`, since this is the free
  tier — treat a non-zero cost as a bug, not a feature).
- `ingest.py`: read `data/corpus/*.jsonl`. Per-record try/except. Returns
  `(artifacts, failures)` where each failure is `{line_no, file, reason, raw}`.
  **Failures are printed in the demo output** — "3 records unparseable: …"
  reads as robustness, not as a bug.

**Acceptance:** `python3 -m src.ingest --corpus data/corpus` prints real counts
and the failure list; feed it a file with a truncated JSON line, a null `ts`, a
missing `text`, and an integer `sender_id` — all four surface by name and the
run completes.

### Agent 3 — Index + multi-hop retrieval  *(parallel with 4; **the differentiator**)*
**Owns:** `src/index.py`, `src/retrieve.py`

Pure Python, no LLM, no vector DB, no new dependencies. This is the code that
makes the dataset's design pay off, and it is the piece with no prior art in
the repo — budget accordingly.

`index.py` builds, from artifacts + `world.json`:
- **alias table** — every project codename and drift alias, person name /
  nickname / handle / email, service name, client name; plus regex-mined ticket
  keys (`[A-Z]{2,4}-\d+`) and PR numbers. Case-insensitive, word-boundary.
- `entity_index: entity_id → {artifact_id}`
- `token_index` for BM25 (fallback path only)
- `thread_index: container_id → [artifact_id]` and `parent_id` links —
  retrieving a buried reply must pull its parent and siblings, or §P5's
  thread-burial noise defeats you
- `time_index` sorted by ts

`retrieve.py`:
- hop 0 — BM25 + alias match on the question → seed set (~15)
- hops 1..3 — expand the frontier by *shared entity*, thread membership, and
  ±6h time window on the same service/customer. Cap ~15 per hop.
- returns ~40–60 candidates, **each annotated with the hop path that reached
  it**. That path is what you render on stage; it is the demo.
- `--only-source {slack,email,…}` flag restricts the whole traversal to one
  channel. This is the single-channel ablation from §P1 and it costs about
  four lines. Do not skip it — it is the pitch.

**Acceptance:** on `data/answer_key.json`, recall of `gold_artifacts` at k=50 is
100% across all 6 bootstrap questions; the same measurement under
`--only-source email` is materially lower; both numbers are printed. Print
recall as a real number every run — it is the metric you quote to judges.

### Agent 4 — Extraction + cross-reference  *(parallel with 3)*
**Owns:** `src/extract.py`, `src/crossref.py`

- `extract.py`: batch retrieved candidates ~10 per Haiku call, calls in
  parallel. Output StatusItems per `docs/schema.md`. **Validate every evidence
  span in Python: `span` must be an exact substring of the referenced
  artifact's `text` after whitespace normalization.** Not fuzzy, not
  LLM-judged. Fails the check → discard the item and log
  `dropped hallucinated item: span not found in <artifact_id>`. That log line
  is a feature; print the count.
- `crossref.py`: dedup StatusItems by (subject, claim) similarity; emit
  `Conflict` records per `docs/schema.md` when two items make opposing claims
  about the same subject. **Never silently resolve** — surface both positions
  with sources and mark `unresolved`, or `newer_wins` with the note explaining
  why. Also flag supersession: a later item that contradicts an earlier one on
  the same subject.

**Acceptance:** run against Agent 1's corpus; the planted conflict pair is
detected and both positions are printed with artifact ids; an item whose
evidence span you manually corrupt is dropped with a named reason; malformed
LLM JSON on one batch does not kill the other batches.

### Agent 5 — Answer, abstention, CLI  *(after 3 + 4)*
**Owns:** `src/answer.py`, `src/pipeline.py`, `out/`

- `answer.py`: StatusItems + conflicts → a cited answer. Every claim carries
  its `artifact_id`. Renders the hop trace from Agent 3.
- **Abstention.** If no item covers the question's subject at `high`/`med`
  confidence, say so and name who would know — looked up from `world.json`
  service/project ownership, a real lookup, never an LLM guess. §P4 is right
  that a clean "I don't have that, ask Priya" is a stronger moment than a
  fabricated answer, and the judges' unanswerable question is coming.
- `pipeline.py`: the `--corpus / --question / --out` CLI `demo.sh` already
  calls. Prints, in order: ingest counts + parse failures, retrieval recall +
  hop count, items extracted / hallucinations dropped, the answer, total cost
  and wall clock.

**Acceptance:** `bash scripts/demo.sh "Why is CS flagging churn risk on Acme?"`
completes under 20s wall clock and emits a cited answer; the bootstrap
unanswerable question produces an abstention with a named owner, not an answer.

### Agent 6 — Silence detection / proactive alerts  *(after 5)*
**Owns:** `src/alerts.py`

§P3, and the second half of the product promise ("proactively alerts people to
things in threads they aren't on"). For each StatusItem, compute who the fact
materially affects (owner of the impacted service/project/client from
`world.json`), then check whether any artifact delivered that fact to that
person before the relevant deadline — using `recipients` and channel
membership. Absence is the alert.

Output the §P3 line shape: *"Priya committed to the GA date at 14:20; Finance
had already deferred the funding at 13:55 in `#fin-ops`, which she is not in.
Nobody connected them."*

**Acceptance:** the silence pair Agent 1 planted is detected with correct
person and timestamps; no alert fires for a fact the person demonstrably
received.

### Agent 7 — Full corpus  *(runs alongside 2–6 the whole time; Brody's lane)*
**Owns:** `synthetic/`, and regenerates `data/` only once Agents 2–6 are green

`DATASET_DESIGN` §3's L0→L4, **scoped down**. The doc's target (18k Slack
messages, 220 events, 60 gold questions, six sub-agents) is a full-day
workstream sized for a fine-grained benchmark. You are judged on one live run.

Cut to: ~3k Slack messages, ~300 emails, ~80 tickets, 30 days, ~40 anchor
events of which ~12 gold, 15 questions (8 multi-hop, 3 silence, 2 abstention,
2 conflict). Keep L0/L1's structure exactly — declared ground truth, fact
partitioning, the deterministic `enforce_partition.py` — because those are what
make the corpus defensible. Drop the 90-day span, the 500-doc scale, the
Confluence/export sources, and `seed_slack.py` (superseded by the local-files
decision).

**Acceptance:** `validate.py` green on all §5-Agent-F gates that still apply —
no `_prov` leak, no gold question answerable by single-channel grep,
single-channel oracle < 20% on gold, ≥40% vocab-disjoint rate, no temporal
inversions; regeneration from seed is byte-identical.

### Agent 8 — Harden  *(last, and stop building when it starts)*
**Owns:** `harness/mutate.py`, `harness/inject.py`, `scripts/smoke.sh`

- `mutate.py --field owner --project aegis --value EMP_310` — re-derives
  affected events and the answer key, < 45s. This is what you run *in front of*
  the judges before answering, per §7's first risk row: show the world change,
  then show the answer change.
- `inject.py` — accepts arbitrary pasted judge text as a live artifact. Assume
  it is garbage: wrong encoding, no timestamp, unknown sender, emoji, 8000
  words, empty. Each case surfaces a specific message and the run continues.
- `smoke.sh` — replace the TODO with the real end-to-end call on the bootstrap
  corpus. It currently only compiles `src/` and prints `ok`, which will pass
  while the pipeline is entirely broken.

Then `/break` and `/rehearse` ×3 per `TOOLKIT_README` §4. Fix the top 2–3
findings only.

---

## 4. Ordering

```
Phase 0 (you, 20 min)
   ├─→ Agent 1  (corpus bootstrap)  ─┐
   └─→ Agent 2  (spine)             ─┤
                                     ├─→ Agent 3 (retrieval) ─┐
                                     └─→ Agent 4 (extract)   ─┴─→ Agent 5 (answer/CLI)
                                                                      └─→ Agent 6 (alerts)
Agent 7 (full corpus) runs alongside throughout
Agent 8 (harden) last — building stops when it starts
```

Agent 3 is the long pole and the only piece with no scaffolding to build on.
If time runs short, cut Agent 6 and Agent 7's scale before cutting anything
from Agent 3 — retrieval quality *is* the demo.

## 5. Design details that decide the demo

1. **Extraction after retrieval, never before.** Pre-flattening the corpus
   defeats the property the corpus exists to have.
2. **Evidence spans validated by exact substring match in Python.** The only
   hallucination guarantee that survives a judge reading the output.
3. **`--only-source` ablation ships in Agent 3, not "if there's time."** It is
   20 seconds of stage time and it is the entire differentiation argument.
4. **Print the ugly numbers.** Parse failures, dropped hallucinations, recall,
   cost, wall clock. Per CLAUDE.md, surfaced failure reads as robustness; a
   clean output with hidden drops reads as a magic trick.
5. **Abstention is a win condition.** Rehearse it deliberately.
6. **No vector DB, no embeddings, no new dependencies.** Entity-linked
   traversal over an alias table is both cheaper and *more* correct here,
   because §P2 built the hops to be cosine-invisible on purpose.
7. **`demo.sh` changes exactly once (Phase 0) and is then frozen.**
