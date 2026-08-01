# COO Agent

COO Agent ingests Slack and email messages, extracts structured status updates using an LLM, accumulates them into a persistent company-context store, and answers questions about what's going on — plus posts a daily executive briefing to Slack.

Two surfaces over one store:

- **Briefing pipeline** — `ingest → extract → store → synthesize → deliver`, run on a schedule.
- **Chat agent** — any employee asks "what's blocked?" or "how's the warehouse cutover going?" and gets an answer grounded in the store, or an honest "I don't have anything on that."

Uses the **Gemini API** (`gemini-3.6-flash` + `gemini-embedding-001`, both free tier).

## Ask it something

```bash
python chat.py                          # interactive
python chat.py "what's blocked?"        # one-shot
```

Inside the REPL: `/topics [status]`, `/topic <name>`, `/stats`, `/reindex`, `/clear`, `/quit`.

The agent answers **only** from retrieved store context. Asked about something it has no data on, it says so and offers adjacent topics rather than inventing a status.

## Setup

1. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in `GEMINI_API_KEY`
   (free key: https://aistudio.google.com/apikey). `SLACK_BOT_TOKEN` is only
   needed to actually post to Slack.
3. Run the pipeline against the bundled sample data:
   ```bash
   python orchestrator.py --dry-run
   ```

## Demo

```bash
python demo.py            # staged walkthrough: inputs → status items → briefing
python demo.py --slow     # pause between stages, for a live audience
python demo.py --channel '#ops-briefing'   # also post to Slack
```

The demo reads Slack messages from `sample_data/messages.json` and email from
`sample_data/gmail_raw.json`, so both ingestion paths run in one pass.

## Running

```bash
# Sample data, briefing printed to the console
python orchestrator.py --dry-run

# Your own exported messages (JSON list of message objects)
python orchestrator.py --fixtures path/to/messages.json --dry-run

# A raw Gmail API dump (users.messages.get responses)
python orchestrator.py --source gmail --dry-run
python orchestrator.py --source gmail --fixtures path/to/gmail_dump.json --dry-run

# Post the briefing to Slack (needs SLACK_BOT_TOKEN)
python orchestrator.py --channel '#ops-briefing'

# Pull live from a Slack channel instead of a file
python orchestrator.py --source slack --channel C0123456789
```

Any module can also be run on its own — `python ingestion.py`, `python extraction.py`,
`python synthesis.py`, `python output.py`, `python agent4.py` — which is the fastest way to debug one stage.

## Agent 4: retrieval + extraction + cross-reference

```bash
python agent4.py --question "what's currently blocked?" --corpus data --top-k 50
```

This runs the bootstrap retrieval stack, extracts status items from candidates, dedupes overlapping subjects, and surfaces conflict records if two items disagree.

## Free-tier quotas — read this before demoing

Free-tier limits are **per model, per day**, and small: `gemini-3.6-flash` allows
**20 requests/day**. One pipeline run costs ~3 requests (extract + synthesize +
embed), and each chat question costs 1–2.

The code handles this rather than dying mid-demo: a per-minute limit is waited out,
and a per-day cap falls straight through to the next model in `GEMINI_FALLBACK_MODELS`.
Both are set in `.env`:

```
GEMINI_MODEL=gemini-3.6-flash
GEMINI_FALLBACK_MODELS=gemini-2.5-flash,gemini-2.5-flash-lite
```

Check your own limits at [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit).
If you're rehearsing a lot, set `GEMINI_MODEL` to a lighter model and save the primary
for the real run.

## Testing

```bash
python test_pipeline.py                  # offline, free, no API calls
RUN_LIVE_TESTS=1 python test_pipeline.py # + live smoke test (~3 API requests)
```

Offline tests stub every network call, so the full pipeline — including the store,
retrieval, and the rate-limit fallback logic — is exercised with no API key and no
Slack token. The live smoke test is opt-in precisely so running tests doesn't eat
your daily quota; it checks end to end that off-topic chatter never reaches the briefing.

## How it scales

The naive version — every message in one prompt, every item back — degrades well before any context limit, and produces a briefing that lists 60 projects. Three mechanisms keep it usable:

**Chunked parallel extraction.** `extract_batch()` splits messages into chunks of 40, extracts them concurrently, and merges the results. A chunk that fails is reported and skipped rather than sinking the whole run. On merge, the most urgent status wins — a `blocked` report can't be lost behind an `on_track` one from another chunk — and owner/blocker are backfilled from whichever chunk actually had them.

**Topic-level storage, not message-level.** Tens of thousands of messages collapse into hundreds of topics. Only topics are embedded and indexed, so retrieval stays fast and cheap; raw messages stay in the store for citation. Topic names are normalized (`"The Billing Migration project"` and `"billing migration"` are one topic), and repeat runs skip messages already stored.

**Ranked, capped briefings.** The briefing details the 12 most urgent items and rolls the rest into a counted tail line (`Plus 28 more not detailed above: 3 at risk, 25 on track`). Briefing length stays roughly constant regardless of input volume.

## The store

SQLite at `coo_agent.db`, built by every pipeline run:

| Table | Holds |
|---|---|
| `messages` | Every ingested message, deduped by content hash |
| `topics` | One row per project: current status, owner, blocker, mention count, embedding |
| `observations` | Append-only history of every status reading, so you can see a topic's trajectory |

When two readings collide, the newer one wins. On a tie, the more urgent status wins, then the more confident — and a later reading that names no owner never erases an owner already known.

## Input format

Messages are normalized to:

```json
{"source": "slack|email", "sender": "priya", "timestamp": "2026-08-01T09:14:00Z", "text": "..."}
```

Ingestion accepts common field-name variants — `user`/`username`/`from`/`author` for
`sender`, `body`/`message`/`content` for `text`, and `ts`/`date`/`created_at` for
`timestamp` — so most exports work without reshaping. Messages with no text are dropped.

**Raw Gmail API dumps work as-is.** If a file contains
`users.messages.get(format="full")` responses, it's detected automatically (by the
presence of a `payload` key) and converted by `gmail_message_to_record()`, which:

- decodes base64url bodies, including unpadded ones;
- walks nested multipart trees, preferring `text/plain`, falling back to de-tagged
  `text/html`, then `snippet`;
- **strips quoted reply chains and signature blocks** — quoted history often contains
  stale status ("everything was green") that would otherwise be extracted as current;
- pulls the bare address out of `From: Name <addr>` and converts `internalDate` to ISO 8601;
- prefixes the subject line so the model sees it.

Live Gmail fetching (OAuth) is *not* implemented — dump the API responses to JSON and
use `--source gmail`. See `sample_data/gmail_raw.json` for the expected shape.

## Status item schema

Extraction produces items with exactly these fields:

| Field | Type | Notes |
|---|---|---|
| `source` | `"slack"` \| `"email"` | Which channel the item came from |
| `topic` | string | Inferred project/team name |
| `status` | `"on_track"` \| `"at_risk"` \| `"blocked"` \| `"unclear"` | |
| `owner` | string \| null | `null` when no owner is named — never guessed |
| `blocker` | string \| null | `null` when nothing is blocking |
| `confidence` | `"high"` \| `"medium"` \| `"low"` | `"low"` signals an unconfirmed reading |

The model is instructed to skip off-topic messages rather than invent items, and to
report `low` confidence rather than hallucinate a status or owner. The output is
constrained by a JSON schema server-side *and* re-validated strictly in `extraction.py`.

## Files

- `ingestion.py` — load messages from a JSON fixture, a raw Gmail dump, or the Slack API; normalize them.
- `extraction.py` — call Gemini to convert raw messages into structured status items; chunked batch extraction and topic merging. Self-contained.
- `store.py` — SQLite company context: messages, topics, observations, embeddings, semantic search.
- `agent.py` — retrieval-grounded Q&A over the store.
- `chat.py` — chat REPL for talking to the agent.
- `synthesis.py` — turn status items into a Slack-formatted COO briefing, ranked and capped.
- `output.py` — post to Slack, or print to the console when credentials are absent.
- `orchestrator.py` — CLI that chains the pipeline end to end.
- `demo.py` — staged walkthrough for presenting the pipeline.
- `schema.py` — re-exports the canonical schema from `extraction.py`.
- `test_pipeline.py` — offline + live tests.
- `sample_data/messages.json` — 10 normalized sample messages (7 Slack, 3 email), real updates plus deliberate noise.
- `sample_data/gmail_raw.json` — 4 raw Gmail API messages exercising multipart, HTML, quoted replies, and an automated digest.
- `ailog.md` — log of AI-assisted changes.

## Known gaps

- **Live** Gmail fetching (OAuth) is not implemented; the Gmail *parsing* half is
  implemented and tested, so JSON dumps work via `--source gmail`.
- Google Docs output is not implemented; the briefing goes to Slack and/or the console.
- The live Slack read/post paths have not been run against a real workspace.
- The chat agent runs locally; there is no Slack bot surface yet. `agent.ask()` is
  interface-agnostic, so a Slack handler is a thin wrapper over it.
- Topic merging is string-normalized, not semantic — "Billing migration" and
  "Payments migration" stay separate topics even if they're the same work.
- Vector search is brute-force cosine over topics in numpy. Fine into the low tens of
  thousands of topics; past that it wants a real index.
