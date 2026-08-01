---
name: synthetic-data
description: Generating the synthetic company dataset (Slack, email, support tickets, press) that the COO oracle ingests. Use when creating, extending, or messing up test data, and when building the fixture set used to prove robustness.
---

# Synthetic Company Dataset

Goal: a small fictional company whose Slack/email/support/press contain
**real, checkable structure** — so the oracle's answers can be verified as
correct on stage, not just plausible.

## Design it backwards from the demo questions
Before generating anything, write the 5-8 questions the oracle must answer
("what's blocking the Q3 launch?", "who should know about the outage?",
"what did support hear that eng hasn't seen?"). Then plant the evidence for
each answer across MULTIPLE sources. A question whose answer lives in one
message doesn't demonstrate cross-source synthesis.

## Ground truth file
Every generated fact goes in `data/ground_truth.json`: the answer to each
demo question + the exact message/email ids that support it. This is what
makes correctness checkable and turns "looks right" into "is right".

## Structure to plant deliberately
- **Cross-source chains**: a customer emails support → support posts in Slack →
  eng discusses in a different channel → nobody tells sales. That gap is the
  product's whole pitch.
- **Conflicts**: two people state different launch dates. Oracle must flag,
  not resolve.
- **Staleness**: an old decision superseded by a newer thread. Oracle should
  prefer the newer and say so.
- **Noise**: lunch chatter, memes, out-of-office autoreplies, newsletters.
  Signal-to-noise ratio is the actual difficulty.
- **Named entities that recur** with inconsistent spelling/handles
  (`@jchen`, `Jen Chen`, `jennifer.chen@`) — identity resolution is realistic
  and demoable.

## Realism cheats that matter (cheap, high impact)
- Timestamps that cluster in work hours, with threads spanning days.
- Slack: threads, reactions, edits, `<@U123>` mention syntax, code blocks.
- Email: reply chains with quoted history, forwards, cc lists, signatures.
- Support: templated ticket fields + freeform customer prose that contradicts
  the dropdown category.

## Generate a MESSY variant too
Produce `data/clean/` and `data/messy/`. Messy = the mutation list from the
demo-hardening skill applied programmatically (missing fields, encoding junk,
format drift, duplicates). Rehearse on messy. Judges' input will be messy.

## Volume
Enough to be non-trivial, small enough to iterate: ~300-600 Slack messages,
~60 emails, ~30 tickets, ~5 press items. Generation must be re-runnable with a
`--seed` so a bad run is reproducible.
