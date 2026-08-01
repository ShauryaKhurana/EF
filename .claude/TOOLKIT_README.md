# Hackathon .claude setup — COO Oracle

Lean by design: 2 hooks, 2 agents, 2 skills, 2 commands. Nothing here needs
maintenance; everything targets the one thing you're judged on — a live run on
an input the judges pick or modify.

## Install (60 seconds)
Copy this whole folder into your repo root. Edit CLAUDE.md's first 4 lines if
scope changed. That's it. Restart Claude Code; `/agents` and `/hooks` to verify.

## What each piece is for
- **CLAUDE.md** — the demo-first rules. The "no fake anything / fail loud"
  section is the important part; it's what keeps the agent from writing a
  magic trick under time pressure.
- **hooks/no_fakes.py** (PreToolUse) — blocks `demo_mode`, mock/fake data,
  hardcoded success returns, `except: pass`, silent swallows, and hardcoded
  Slack/Google/OpenAI-style tokens. Relaxed to secrets-only for files whose
  path looks like a fixture/generator, so your synthetic-data work isn't
  blocked. Tested: 5/5 cases.
- **hooks/smoke.py** (PostToolUse) — py_compile on every edited .py so syntax
  errors surface instantly instead of during a run.
- **skills/demo-hardening** — the 10-item mutation list + handling rules +
  LLM-output validation rules. Loads on demand when hardening or testing.
- **skills/synthetic-data** — your piece: design data backwards from the demo
  questions, plant cross-source chains/conflicts/staleness, keep
  `data/ground_truth.json` so correctness is checkable, and ship a messy variant.
- **agents/breaker** (sonnet) + `/break` — mutates inputs, runs the pipeline,
  reports what breaks ranked by likelihood-a-judge-triggers-it. Never fixes.
- **agents/scout** (haiku) + inline use — cheap API/codebase lookups
  (endpoint, scopes, rate limits, response shape) without polluting main context.
- **/rehearse** — full timed dry run on an unrehearsed input, reports only.
- **NOTES.md** — one line per decision/blocker. Replaces any memory system;
  you have hours, not weeks.
- **docs/schema.md** — normalized record + StatusItem + Conflict contracts.
  The evidence-span requirement is your anti-hallucination guarantee.
- **scripts/demo.sh** — THE judged command. Keep it boring and stable.
- **scripts/smoke.sh** — pre-commit sanity, <10s.

## Permissions
Allow-listed python/pytest/scripts/git-basics so you're not tapping approve all
day. Ask-gated on `pip install` and `git push`. Denies reading .env/credentials.

## Workflow for today
1. Plan mode for each chunk, approve, execute, commit. One session per chunk;
   `/clear` between — compaction mid-hackathon loses details you need.
2. After ingest/parsers exist: `/break`, fix top 2-3 only.
3. Last 90 minutes: stop building. `/rehearse` on unrehearsed inputs, 3x.
4. Freeze `scripts/demo.sh` early and never touch it again.
