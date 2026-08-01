# Schemas (binding contract — code must match this file)

## Normalized record (all sources normalize to this)
```json
{"artifact_id":"str unique","source":"slack|email|ticket|doc|export",
 "container_id":"str channel|thread key|ticket key|doc path","parent_id":"str|null",
 "sender_id":"str canonical person id","recipients":["str canonical person ids"],
 "ts":"ISO8601 with timezone","text":"str","meta":{},"raw":{"original payload"}}
```
Rules:
- `artifact_id` is the unique identifier for matching against `answer_key.gold_artifacts`
- `parent_id` is `null` for top-level messages — thread burial depends on this
- `recipients` is `[]` for public channels — silence pairs depend on this
- `raw` is never dropped — provenance for every claim on stage
- the loaded in-memory `Artifact` carries an extra `warnings: [str]` (coercions applied at
  load time, e.g. unparseable `ts`). It is **in-memory only** — never written to
  `data/corpus/`, so corpus validators must not expect or allow it on disk.

## StatusItem (LLM extraction output — validate in code, never trust shape)
```json
{"item_id":"str","kind":"status|blocker|decision|risk|request|fyi",
 "project":"str|null","subject":"str","claim":"one sentence",
 "evidence":[{"artifact_id":"str","span":"verbatim quote from source"}],
 "people":["canonical ids"],"confidence":"high|med|low",
 "ts":"ISO8601 of the underlying record"}
```
Rules: every StatusItem needs >=1 evidence span copied verbatim from source.
No evidence -> discard the item (that's a hallucination).

## Conflict
```json
{"conflict_id":"str","subject":"str","positions":[{"claim":"str","item_ids":["..."]}],
 "resolution":"unresolved|newer_wins","note":"str"}
```
Rule: never silently resolve. Surface both positions with sources.