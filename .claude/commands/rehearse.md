---
description: Full dry run of the 3-minute judged demo
---

Run the demo exactly as the judges will see it$ARGUMENTS:

1. Pick an input from `data/mutants/` or `data/messy/` that we have NOT run
   before (state which one you chose).
2. Run `scripts/demo.sh` end to end, hands off, no edits mid-run. Time it.
3. Report: wall-clock seconds, whether output was correct against
   `data/ground_truth.json`, any records skipped and whether the skip was
   surfaced in the output.
4. List anything that looked shaky but didn't fail — those break next time.

Do NOT fix anything during the rehearsal. Report first.
