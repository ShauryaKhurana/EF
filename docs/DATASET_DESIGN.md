# Synthetic Corpus Design — "Oracle" COO Agent
### Fire Yourselves Hackathon · Dataset workstream (Brody) · v1.0

---

## 0. The reframe that changes everything

You wrote "we need a synthetic dataset." You don't. You need three artifacts, and conflating them is the most common way this workstream eats the whole day:

| Artifact | What it is | Who consumes it |
|---|---|---|
| **The World** | A believable 90-day company: Slack, email, tickets, docs, exports | The agent, at retrieval time |
| **The Answer Key** | Machine-checkable ground truth for every buried chain | You, for eval + the live demo |
| **The Harness** | CLI that mutates the world or generates a *fresh* day on demand | The judges, at 0:45 |

There is no fine-tuning today. Nothing here is training data. The corpus's only job is to be a **haystack that provably defeats the alternatives** — flat keyword search, single-channel reading, per-user inbox assistants — while containing needles you can prove were found correctly.

That last clause is the whole design. Everything below follows from it.

### Why the judging rubric dictates the architecture

> *"It runs on a fresh, ugly input live, not the one you rehearsed. We pick which staged input runs, or change one field."*
> *"What loses: a demo that only survived the one input you practiced."*

A pre-baked static JSON dump is the definition of a magic trick, and judges who read their own rules will say so. So the corpus must be **regenerable and mutation-consistent**: a judge changes one field, and every downstream artifact *and* the answer key update deterministically in seconds. That requirement is why Layer 0 below is pure Python with a fixed seed and why prose is always the last thing generated, never the source of truth.

---

## 1. Prior art (10 minutes of research, so you don't re-derive it)

Three recent systems solved almost exactly this problem. Steal their primitives, ignore their scale.

- **EnterpriseRAG-Bench** (arXiv 2605.05253) — ~500k synthetic docs across Slack, Gmail, Linear, Drive, HubSpot, Fireflies, GitHub, Jira, Confluence, plus 500 questions in ten categories. Confirms the source mix and, more usefully, that the *generation framework* is the reusable asset, not the corpus.
- **GroundEval** (arXiv 2606.22737) — a synthetic enterprise scenario, ~22.5k events over 60 days, built against a declared set of **causal link types** and **silence pair types**. Critically: the causal links are declared in config *up front* and are never inferred back out of the generated text. Steal this wholesale.
- **OrgForge** (arXiv 2603.14997) — simulates *the organizational processes that produce documents*, not the documents. Its headline contribution is **verified absence**: silence is ground truth when no simulation state warranted a message.

**Two takeaways that shape our build:**

1. **Ground truth is declared, never recovered.** You never parse your own generated text to figure out what's true. The event log is truth; text is a lossy, styled projection of it.
2. **Silence is a first-class fact.** "Nobody told Alex" is checkable ground truth. This is the entire technical basis for your proactive-alerting feature — without it, "proactive" is a vibe you can't demo.

**On Syncora specifically: skip it.** Their product is agent-orchestrated, privacy-preserving synthesis from *existing structured/tabular seed data* — DP-guaranteed synthetic twins of a real dataset you already hold. You have no real seed data, and your output is heterogeneous free text with hand-designed causal structure. It solves a different problem and would cost you an integration hour for nothing. The graph-walk pattern from your research notes is right; the vendor is not.

---

## 2. Core design principles

### P1. Fact partitioning — the mechanism that makes multi-hop *necessary*

For each anchor event, declare an atomic fact set:

```
F(evt_014) = {
  f1: root_cause          = "connection-pool exhaustion in svc-bill-v2"
  f2: trigger             = "config change merged Thu 18:40 by EMP_141"
  f3: customer_impact     = "Acme Corp saw 41min checkout downtime"
  f4: financial_impact    = "$18k SLA credit owed, hits Q3 milestone payout"
  f5: schedule_impact     = "Aegis GA slips 2 weeks"
  f6: true_owner          = "EMP_082, not the on-call who was paged"
}
```

Then enforce, at generation time, a hard constraint:

- Every fact appears in **exactly one** artifact.
- No artifact carries more than **⌈|F|/3⌉** facts.
- For **every** channel c, `facts(c) ⊊ F` — no channel is sufficient on its own.
- At least one fact lands in a channel whose audience **excludes** the person the demo question is asked as.

This is validated programmatically, not hoped for. The payoff: you can stand in front of judges and run the **single-channel ablation** — "here's the same question answered by an agent that only reads Sarah's inbox; it gets it wrong, because f1 and f4 were never in her inbox." That ablation *is* your pitch. It's the difference between claiming an oracle is better than a per-user assistant and demonstrating it in 20 seconds.

### P2. Vocabulary-disjoint hops — the mechanism that makes the graph layer *necessary*

Plain embedding RAG will solve a chain where every hop shares vocabulary with the question. That would make your semantic/graph filtering layer look like overkill.

So: **≥40% of gold chains must contain at least one hop with zero content-word overlap with the question and with the preceding artifact.** The only thing linking them is a shared entity — a ticket ID, an employee ID, a timestamp window, a customer record.

Example: question says *"why is CS flagging churn risk on Acme?"* The root-cause artifact says *"pool's maxed, OOMKilled again on bill-v2, who merged 4402?"* Zero shared content words. Cosine similarity will not find it. An entity-linked traversal will. **This constraint is your entire technical differentiation, encoded as a dataset property.** Measure it, report the number in the doc, put it on the one slide you're allowed.

### P3. Silence pairs

Declare tuples: `(fact f, person P, deadline T, expected_channel c)` where P needed f by T and **no artifact in the corpus delivers f to P before T**. Validate the absence.

These are your proactive alerts, and their ground truth is exact. Best demo line available to you:

> *"At 14:20 Priya emailed Acme committing to the original GA date. At 13:55 — 25 minutes earlier — Finance had already decided in `#fin-ops` to defer the vendor payment that funds that release. Nobody connected them. Our agent pinged Priya at 13:56."*

### P4. Abstention set

You said you want minimal hallucination. Then you must generate questions that are **unanswerable** from the corpus — plausible, entity-grounded, and genuinely absent. Target ~15% of the gold set. An agent that answers these confidently is broken, and you want to know that before a judge finds out. Bonus: "I don't have that; here's who would know" is a *better* demo moment than a fabricated answer, and judges notice.

### P5. Realistic ugliness (parameterized, not vibes)

The hackathon brief is explicit that edge cases are the reason a human is still doing this job. Ship these as tunable knobs in `config/noise.yaml`:

- **Codename drift** — `Project Aegis` / `aegis` / `AEG-2` / `"the billing thing"` / `svc-bill-v2`. Highest-value single item: it breaks naive string matching immediately.
- **Name collisions** — two Alexes, one Alex Chen and one Alexis Chen; nicknames (`Priya` / `Pri` / `@pr`).
- **Thread-reply burial** — the actual decision lives in a threaded reply under an unrelated top-level message. Kills flat chunking.
- **Stale quoted chains** — forwarded emails where the bottom quote contradicts the top, superseded by a later Slack message.
- **Bot volume** — 25–35% of Slack is CI, alerting, standup bots, calendar noise.
- **Dangling references** — "see the sheet I dropped", no attachment. Forces honest abstention.
- **Human mess** — typos, autocorrect, half-sentences, threads that trail off unresolved, off-hours messages, timezone-split duplicate discussions.

### P6. Wall-clock and rate limits are the constraint, not cost

This is a hackathon budget, so use the **Gemini API free tier** (`gemini-2.5-flash`) instead of a paid frontier model — $0 for the entire corpus, including a full regeneration or two. That flips the arithmetic from "how many tokens can I afford" to "how many requests per minute am I allowed." Check the free tier's current RPM/TPM/RPD caps for the model you pick before sizing the run; they're tighter than a paid tier and are the actual bottleneck now.

So:
- **Do not use the Batch API.** Async/50%-off batch turnaround (hours) is fatal on stage-prep time regardless of provider.
- **Do use context caching** where the free tier supports it — the company bible + style guide as a cached prefix — so you can send full context on every call without re-paying for it or burning extra TPM budget.
- **Size concurrency to the free-tier RPM cap, not to your machine.** `asyncio` + a semaphore bounded by the rate limit, retry on 429 with backoff. Expect full corpus expansion to take longer wall-clock than a paid tier would — measure it once, then budget regenerations around that real number instead of the 25-minute figure a paid model would hit.
- **Do keep outputs short.** Slack ≤ 20 words, email body ≤ 90 words. This is for realism first (real Slack is terse), and it also means fewer output tokens per call, which helps you stay under the free tier's TPM cap.

---

## 3. The pipeline

```
L0  GROUND TRUTH            pure Python, seeded, $0, <5s
    world.json              40-60 employees, 8 teams, 7 projects, 12 clients,
                            20 services, 14 channels, org chart, calendar
    events.jsonl            ~220 anchor events over 90 days, each with a
                            declared fact set F, causal links, silence pairs
              │
              ▼
L1  SPREAD PLANS            Gemini Flash + strict JSON schema, 1 call/event, ~220 calls
    plans.jsonl             per event: list of artifact STUBS —
                            {channel, author_id, recipients, ts, intent, tone,
                             carries_facts:[fid], hop_index, gist(≤15 words)}
                            ── fact-partition constraint enforced HERE ──
              │
              ▼
L2  PROSE EXPANSION         Gemini Flash, batched 8-15 stubs/call, cached prefix
    artifacts/*.jsonl       stub → actual Slack messages / email bodies /
                            ticket comments / doc sections
              │
              ▼
L3  EXHAUST                 80% Python templates ($0), 20% LLM paraphrase
    exhaust/*.jsonl         ~70% of total corpus volume: standups, lunch,
                            PTO, calendar spam, bot alerts, recruiting,
                            watercooler. Interleaved chronologically.
              │
              ▼
L4  ASSEMBLY + KEY
    corpus/                 normalized to YOUR ingestion schema, provenance
                            fields STRIPPED
    answer_key.json         gold chains, gold facts, silence pairs, abstentions
    harness/                mutate.py · fresh_drop.py · inject.py · seed_slack.py
```

**Why the L1/L2 split and not one-shot generation:** a single LLM call asked to "write the whole incident across four channels" drifts — it forgets which facts it has already placed, leaks the full answer into one artifact, and invents employees who don't exist. Splitting means the *structural* decisions (who says what, where, carrying which fact) are made in token-dense JSON you can validate and repair cheaply, and the expensive prose step is a pure styling operation with nothing left to decide. You can regenerate all prose without touching a single ground-truth relationship.

### Target scale

| | Count | Rationale |
|---|---|---|
| Employees | 50 | Enough for name collisions and real org distance |
| Projects | 7 | 3 "hot" (demo-bearing), 4 background |
| Simulated days | 90 | Long enough for staleness/supersession to matter |
| Anchor events | ~220 | ~35 with full gold chains, rest are texture |
| Slack messages | 15–18k | Grep fails; embedding index builds in ~2 min |
| Email threads | ~1.5k | ~4k individual messages |
| Tickets | ~400 | With comment histories |
| Docs / exports | ~30 / ~10 | Confluence-ish pages, CSV metric dumps |
| Gold questions | 60 | 35 multi-hop, 10 silence, 9 abstention, 6 conflict |

Resist scaling up. 500k docs is a benchmark paper's job. Yours is a corpus that ingests in minutes and can be regenerated live.

---

## 4. Contracts (write these FIRST — before any generation)

Publishing these in the first 30 minutes is the single highest-leverage act of your day. The moment they exist, your partner codes against them and you two never block each other again. Put them in `CLAUDE.md` at repo root so every agent inherits them.

**`schemas/artifact.schema.json`** — the normalized unit your ingestion layer eats:

```jsonc
{
  "artifact_id": "slk_00a41f",
  "source": "slack",              // slack | email | ticket | doc | export
  "container_id": "#eng-aegis",   // channel | thread_id | ticket key | doc path
  "parent_id": "slk_00a3f0",      // thread replies; null if top-level
  "sender_id": "EMP_141",
  "recipients": ["EMP_082"],      // [] for public channels
  "ts": "2026-05-14T13:55:22-07:00",
  "text": "pool's maxed again on bill-v2. who merged 4402",
  "meta": {}                      // attachments, reactions, ticket status
}
```

**`schemas/answer_key.schema.json`**:

```jsonc
{
  "question_id": "Q_017",
  "type": "multi_hop",            // multi_hop | silence | abstention | conflict
  "asked_as": "EMP_002",          // persona; controls permission scope
  "question": "Why is CS flagging churn risk on Acme?",
  "gold_facts": ["evt_014:f1", "evt_014:f3", "evt_014:f4"],
  "gold_artifacts": ["slk_00a41f", "eml_0032", "tkt_4402"],
  "min_hops": 3,
  "vocab_disjoint_hops": 1,
  "unanswerable_from": ["email"],  // powers the single-channel ablation
  "acceptable_answer_contains": ["connection pool", "SLA credit", "ENG-4402"],
  "must_not_contain": ["memory leak"]   // the plausible-but-wrong cause
}
```

**Provenance rule:** every artifact carries `_prov: {event_id, fact_ids, hop_index}` internally through L1–L3. `L4/assemble.py` **strips it into the answer key** and asserts it never appears in `corpus/`. A leak here means the agent cheats and you won't notice until a judge asks a question you didn't rehearse.

---

## 5. Claude Code agent tasks

Six agents. **Strict file ownership** — no two agents write the same path, which means you can run them concurrently in separate terminals (or worktrees) with zero merge pain. Given your existing subagent + hooks setup, wire the acceptance test of each as a post-edit hook so an agent can't declare done on red.

Give every agent this preamble:

> Read `CLAUDE.md` and `schemas/` first. You own only the files listed under OWNS. Never edit files outside it — if you need a change there, write the request to `NOTES.md` and continue. Your task is complete only when your acceptance test passes. Do not generate prose content; that is agent D's job. Use a fixed random seed. No network calls except the Gemini API.

---

### Agent A — World Builder
**Owns:** `l0_world/`, `out/world.json`
**Reads:** `schemas/`, `config/company.yaml`

Pure Python + Faker, seeded. Emits `world.json`: employees (id, name, role, team, manager, timezone, start date, Slack handle, email, 2–3 alias forms), teams, 7 projects with codename + 3 drift aliases each, 12 clients with contract value and SLA terms, 20 services with owning team, 14 channels with declared membership, a 90-day working calendar with holidays/PTO, and an org-distance matrix.

Deliberately plant: two employees sharing a first name; one employee who leaves at day 62 (ownership lapse → silence pairs); one contractor with narrow channel access.

**Acceptance:** `pytest l0_world/` green; `world.json` validates; regenerating with the same seed is byte-identical; ≥1 first-name collision; every service has an owner; every channel has ≥4 members.

---

### Agent B — Event & Causality Engine
**Owns:** `l0_events/`, `out/events.jsonl`
**Reads:** `out/world.json`, `config/causal_links.yaml`

The most important agent. Emits ~220 anchor events over the 90 days. Each event:

```jsonc
{
  "event_id": "evt_014", "day": 34, "ts": "...", "type": "incident",
  "severity": 3, "project": "aegis", "primary_owner": "EMP_141",
  "impacted": ["svc_bill", "CUST_991"],
  "facts": { "f1": {...}, "f2": {...} },     // atomic, one sentence each
  "causal_links": [{"to": "evt_019", "type": "escalates_to", "lag_h": 6}],
  "silence_pairs": [{"fact": "f4", "person": "EMP_002",
                     "deadline": "...", "expected_channel": "email"}],
  "is_gold": true
}
```

Implement ≥12 causal link types (`incident→postmortem`, `escalation→credit`, `credit→milestone_risk`, `departure→ownership_lapse`, `decision→supersedes_prior_decision`, `commitment→resource_conflict`, …) and ≥6 silence pair types. **Declare them in config; never infer them.** Also emit ~20 explicit **conflict pairs**: two artifacts that will disagree, with a declared resolution rule (later timestamp + higher source authority wins) recorded in the key — these feed your DEDUP/CROSS-REF layer's live demo.

**Acceptance:** `validate_events.py` green — DAG is acyclic; every `impacted` entity exists in `world.json`; no effect precedes its cause; ≥35 events flagged gold; every gold event has |F| ≥ 4; every silence pair's person genuinely lacks channel access to where the fact lands.

---

### Agent C — Spread Planner (L1)
**Owns:** `l1_plans/`, `out/plans.jsonl`
**Reads:** `out/world.json`, `out/events.jsonl`

One Gemini Flash call per event with a strict JSON output schema. Input: the event, the relevant slice of the world, the channel roster. Output: 6–20 artifact **stubs**. No prose beyond a ≤15-word gist.

Then run `enforce_partition.py`, which is deterministic Python, not an LLM:
- every fact assigned exactly once → else reassign or re-prompt
- no artifact over ⌈|F|/3⌉ facts → else split
- for each channel, `facts(channel) ⊊ F` → else move a fact to a channel with disjoint membership
- ≥40% of gold events have a hop pair with zero content-word overlap between gists (stopwords + entity names removed) → else re-prompt that hop with an explicit "use only informal engineering shorthand, never the customer-facing framing" instruction

**Acceptance:** partition enforcement passes on 100% of gold events; every `author_id` has membership in the target channel at that timestamp; `hop_index` chains are connected; artifact count within 20% of target.

---

### Agent D — Prose Expander (L2)
**Owns:** `l2_expand/`, `out/artifacts/*.jsonl`
**Reads:** `out/plans.jsonl`, `out/world.json`, `config/voice.yaml`

Async Gemini Flash workers, concurrency bounded by the free tier's RPM cap, cached prefix containing the company bible (culture, jargon dictionary, service names, project alias table) and per-persona voice cards (~40 words each: seniority, verbosity, emoji use, typo rate, punctuation habits).

Hard rules in the prompt: Slack ≤20 words and lowercase-leaning; email body ≤90 words; **never restate a fact not in `carries_facts`**; refer to projects using the alias sampled for that speaker; only mention people/services/tickets present in the provided roster.

Post-pass `repair.py`: entity-closure check against `world.json` — any invented name/ticket/service triggers a single re-prompt of that artifact, then falls back to a template. Also injects the noise knobs from `config/noise.yaml` (typos, thread burial, stale quote chains, dangling references) mechanically, so noise level is tunable without re-calling the LLM.

**Acceptance:** zero entity-closure violations; zero artifacts containing a fact string outside `carries_facts` (checked by fuzzy match on the fact's key nouns); length caps respected in ≥95%; full run completes without tripping the free-tier rate limit (retries/backoff visible in the log, not silent stalls); cost logged and $0.

---

### Agent E — Exhaust Generator (L3)
**Owns:** `l3_exhaust/`, `out/exhaust/*.jsonl`
**Reads:** `out/world.json`, calendar

~70% of final corpus volume, ~90% of it template-driven with zero LLM cost: standup posts, PR/CI bot spam, deploy notifications, calendar invites and reschedules, PTO requests, expense pings, recruiting threads, lunch coordination, sports/weather chatter, newsletter blasts, onboarding checklists, "does anyone have the wifi password."

The 10% LLM slice paraphrases templates so they don't read as obviously repetitive. Crucially, exhaust must be **entity-realistic** — it name-drops real projects and people at plausible rates so it's genuinely confusable with signal. Exhaust that never mentions Aegis is not a distractor.

Include ~30 **near-miss distractors**: threads that look exactly like they'd answer a gold question and don't (a *different* outage, on a different service, three weeks earlier). This is what breaks lazy top-k retrieval.

**Acceptance:** volume ratio 65–75% of corpus; ≥20% of exhaust mentions a real project/employee; carries zero gold facts (validated against `events.jsonl`); ≥30 near-miss distractors tagged in the key.

---

### Agent F — Assembly, Validation & Judge Harness
**Owns:** `l4_assemble/`, `harness/`, `out/corpus/`, `out/answer_key.json`
**Reads:** everything upstream

1. **`assemble.py`** — merge all artifacts chronologically, normalize to `artifact.schema.json`, strip `_prov` into the key, assign stable IDs, emit `corpus/{slack,email,tickets,docs,exports}.jsonl` + a SQLite index.
2. **`gen_questions.py`** — build 60 gold questions from `events.jsonl` (multi-hop, silence, abstention, conflict), each with the full key record from §4.
3. **`validate.py`** — the gate. Fails loudly on: any `_prov` leak into corpus; any gold question answerable by single-channel grep; single-channel oracle scoring >20% on gold; <40% vocab-disjoint rate; temporal inversions; entity-closure violations; abstention questions that are accidentally answerable.
4. **`harness/mutate.py`** — `--field owner --project aegis --value EMP_310`, re-runs L0→L4 for affected events **only**, regenerates the key, completes in <45s.
5. **`harness/fresh_drop.py --day 91`** — generates a fully unseen day of traffic from a held-out event bundle, at demo time.
6. **`harness/inject.py`** — accepts arbitrary pasted text from a judge as a live artifact.
7. **`harness/seed_slack.py`** — pushes a subset (~1.5k messages, the demo-relevant channels) into a real free Slack workspace and a real Gmail account, so the pipeline runs against **actual APIs** per the architecture diagram rather than a local file read.

**Acceptance:** all validators green; `mutate.py` round-trips under 45s with a consistent key; `seed_slack.py` completes without hitting rate limits.

---

## 6. Your runbook (things only you should do)

Hour offsets from your start, assuming ~9 working hours before the dataset must freeze.

**H+0:00 → H+0:30 — Write the contracts by hand. Do not delegate this.**
Author `schemas/artifact.schema.json`, `schemas/answer_key.schema.json`, `config/company.yaml`, and `CLAUDE.md`. Then pick your **demo narrative** — one incident chain you personally believe in, written out longhand as six facts across four channels. Everything else is generated; this one you own. Commit and tell your partner the artifact schema is frozen.

**H+0:30 → H+1:00 — Unblock your partner immediately.**
Hand-write or template ~200 artifacts covering your demo narrative and dump them as `corpus/*.jsonl` in the real schema. Ugly, small, correct shape. Your partner now builds the entire ingestion → extraction → dedup → synthesis pipeline against real files while you build the real corpus. **Your partner should never be idle waiting on you.** If you do one thing from this document, do this.

**H+1:00 → H+3:00 — Launch A, B, E in parallel.** Review B's `causal_links.yaml` yourself before it generates — this is the file that determines whether your demo is impressive or trivial. C depends on B; start it when B is green.

**H+3:00 → H+4:00 — Review 20 stubs by hand.** Read them as a human. Do they read like a company? Is the fact partition doing something interesting or has the model quietly put the whole story in one Slack channel? Fix here, before you pay for 18k messages of prose.

**H+4:00 → H+5:00 — Run D. While it runs, hand-write your 6 demo questions.** Write the questions you *want* asked, plus the three you're most afraid of. Add all nine to the gold set.

**H+5:00 → H+6:00 — Read 50 random artifacts.** No tooling. Just read. You are checking for the tells: every message the same length, everyone writing in the same voice, nobody ever confused, no thread ever unresolved, project names never abbreviated. Tune `config/noise.yaml` and re-run D if it reads like an LLM wrote a company.

**H+6:00 → H+7:00 — Run `validate.py` and fix red.** Then run the **single-channel ablation** yourself and record the number. If a single-channel oracle scores above 20% on gold, your fact partition is too weak and the entire pitch is undermined — go fix L1, not the agent.

**H+7:00 → H+7:30 — Seed the real Slack workspace + Gmail.** Confirm your partner's pipeline reads from live APIs, not files.

**H+7:30 — FREEZE.** Tag the commit. From here the corpus does not change; only the harness does. Move to the agent.

**Remaining time — rehearse the live run three ways:** (1) judge picks a different staged question, (2) judge mutates a field via `mutate.py`, (3) judge pastes their own messy text via `inject.py`. Time each. You have 135 seconds total.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Judges call the synthetic corpus a magic trick | Lead the demo by running `fresh_drop.py` or `mutate.py` **in front of them**, before answering. Show the world changing, then show the answer changing. |
| Corpus reads obviously LLM-generated | The H+5:00 manual read. Voice cards + noise knobs + mechanical typo injection. Terse beats verbose every time. |
| Fact partition too weak → plain RAG solves everything → your graph layer looks pointless | Single-channel ablation as a hard gate at H+6:00, not an afterthought |
| Dataset slips and partner is blocked | The 200-artifact stub at H+0:30. Non-negotiable. |
| Agent hallucinates confidently on stage | The abstention set. Rehearse an unanswerable question deliberately — a clean "I don't know, ask Priya" is a strong moment, not a weak one. |
| Ingest too slow to demo live | Pre-index the 90 days; only the fresh drop or mutation is ingested live. Budget <20s. |

---

## 8. What to say in the first 45 seconds

The brief asks for the job, the human time, and how many of you have seen it. Your answer:

> The job is *"go find out what's actually happening."* Someone at every company spends 6–10 hours a week chasing status across Slack, email, and tickets to assemble a picture no single system holds. It's not a reporting problem — it's that the information genuinely lives in four places and nobody is on all four channels.

Then run the ablation live: same question, per-inbox assistant fails, oracle succeeds, here's the four-hop trace with citations. The dataset exists to make those 20 seconds provable.
