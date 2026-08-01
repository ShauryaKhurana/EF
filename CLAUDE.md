# COO Oracle — Fire Yourselves hackathon (Aug 1)

One agent fed all company Slack/email/support/press. Answers employee questions
and proactively alerts people to things in threads they aren't on.
Pipeline: corpus ingest → index → multi-hop retrieve → extract StatusItems → dedup/conflict → cited answer or abstention.

## Judged on ONE live run against an input the judges pick or modify.
Everything below follows from that.

## Hard rules
- **No fake anything.** No mocked outputs, no hardcoded demo values, no
  `if demo_mode`, no placeholder returns. A simulated action = instant loss.
  If something can't be done for real, say so — don't paper over it.
- **Fail loud and specific, never silent.** No bare `except: pass`, no silent
  defaults, no dropping records that don't parse. Unparseable input gets
  surfaced as "couldn't parse X because Y", which reads as robustness on stage.
- **Never crash the pipeline on one bad record.** Per-record try/except that
  logs the failure and continues. One malformed Slack message must not kill
  the run.
- **Assume every field is missing, null, wrong-typed, or weirdly formatted.**
  The edge cases ARE the demo.
- **Never claim it works without running it.** Run the actual command, show
  the actual output.

## Speed rules
- Simplest thing that runs. No abstraction layers, no config systems, no
  premature generalization. We have hours, not weeks.
- Don't refactor unless it's blocking. Don't write tests beyond
  `scripts/smoke.sh`. Don't add dependencies without asking.
- Small commits, often. `git commit` after anything that works.

## Pointers
- Demo run: `scripts/demo.sh` — the single command judges will see
- Smoke test: `scripts/smoke.sh` — must pass before any commit
- Notes/decisions/blockers: `NOTES.md` (append one line, don't rewrite)
- Data shapes: `docs/schema.md` — the StatusItem contract is binding