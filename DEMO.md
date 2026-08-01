# Demo runbook — COO Oracle

One command, three beats, ~135 seconds. Everything below was run for real before
this file was written; expected outputs are pasted from actual runs.

## Setup (once, before you're on stage)

```bash
cd EF
venv/bin/python -m synthetic.bootstrap.validate   # 16/16 — run it in front of them if asked
```

Needs `GEMINI_API_KEY` in `.env`. Every call prints `cost: $0.00 (Gemini free tier)`.

## Beat 1 — the multi-hop answer (~40s)

```bash
bash scripts/demo.sh "Why is CS flagging churn risk on Acme?"
```

What they see: recall printed as a real number, then an answer that assembles a
causal chain no single channel contains:

> Acme Corp experienced a 41-minute checkout outage on May 14, 2026, prompting their
> ops lead to request a written RCA [eml_0032]. The outage was caused by a Sev2
> incident on `svc-bill-v2` where error rates hit 41.2% due to a maxed connection
> pool linked to ENG-4402 [slk_0039, slk_0041]. The incident resulted in an $18,000
> billing credit that lands against Acme's Q3 milestone payout tied to AEG-2 [slk_0067].
>
> How I got there:
>   eml_0032   (email · thr_acme_incident · 0 hops)
>   eml_0032 → slk_0039   (slack · #eng-billing · 1 hop)
>   eml_0032 → slk_0041   (slack · #eng-billing · 1 hop)
>   eml_0032 → slk_0067   (slack · #fin-ops · 1 hop)

**Say:** the root cause lives in a Slack reply that shares *zero words* with the
question — "pool's maxed again on bill-v2. who merged 4402". Keyword search can't
get there; grep the question's words yourself and you find 1 of 3 sources. The
oracle got there through the ticket ID.

## Beat 2 — the ablation (~20s, this is the pitch)

```bash
venv/bin/python -m src.pipeline --corpus data/corpus \
  --question "Why is CS flagging churn risk on Acme?" --only-source email --out out/ablation.md
```

(`demo.sh` is frozen to question+corpus, so the ablation flag goes through the
pipeline module directly. Same code path.)

Retrieval numbers, same question, one channel:

| | candidates | gold recall |
|---|---|---|
| full traversal | 50 | **3/3** |
| `--only-source email` | 3 | **1/3** |

**Say:** this is a per-inbox assistant — Sarah's email bot. It knows there was an
outage. It cannot know the root cause or the financial hit, because those facts
were never in any inbox. That's not a model gap; the information genuinely lives
in four places. This is why it's an oracle and not an email plugin.

## Beat 3 — the trap question (~20s)

```bash
bash scripts/demo.sh "Who signed off on the svc-auth security review?"
```

> I don't have enough confirmed information on svc-auth. Omar Haddad would know.

**Say:** that question has no answer in the corpus — deliberately. A confident
answer here would be a hallucination. It abstains and routes you to the real owner
from the org data. (The referral is a lookup in world.json, not an LLM guess.)

## If a judge changes the question

Fine — any phrasing that names an entity (Acme, Aegis, bill-v2, 4402, a person)
traverses from there. Rehearsed variants that work:

- "What is holding up the Aegis GA date?" → two-week slip + open Sev2, cited
- "Who actually owns svc-bill-v2, and who got paged?" → EMP_082 vs EMP_003, cited
- "What caused the billing outage on May 14?" → surfaces the memory-leak red herring
  *and* the postmortem that corrected it, both cited

## Rate-limit reality (read before stage time)

- Free tier is limited per-model per-day, plus ~10 req/min. One question ≈ 6 calls.
- The client waits out per-minute limits (it prints what it's doing) and falls
  through to `GEMINI_FALLBACK_MODELS` on daily caps. It will not die mid-demo,
  but back-to-back questions can add a visible ~40s wait.
- **Leave ~60s between questions on stage.** Rehearse on a fallback model:
  `GEMINI_MODEL=gemini-3.5-flash bash scripts/demo.sh "..."` and save the primary's
  daily budget for the real run.

## Known limits (don't get caught claiming otherwise)

- Q_005 (the silence question) retrieves the slip but doesn't detect the
  contradiction with Priya's email — that's Agent 6 (silence detection), cut for time.
- `harness/mutate.py` and `inject.py` (Agent 8) are not built. Don't offer them.
- The corpus is the 200-artifact bootstrap, not the full 30-day corpus.
