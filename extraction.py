"""Extract structured status updates from corpus artifacts using the Gemini API.

Standalone: `python extraction.py` runs the sample messages in __main__.
Requires GEMINI_API_KEY in a .env file (see .env.example).

Every item carries `evidence`: verbatim spans copied from the artifacts it was drawn
from, each tagged with its artifact_id. Spans are re-checked in Python against the
source text — an item whose span is not found is dropped, not repaired. That check is
the only hallucination guarantee that survives a judge reading the output.

The Gemini client (model fallback, retries, rate limiting, usage) lives in src/llm.py.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any

from google.genai import types

from src.llm import (
    FALLBACK_MODELS,
    MODEL,
    LLMJSONError,
    QuotaExhausted,
    coerce_json,
    generate_content,
    require_api_key,
    response_text,
)
from src.schema import normalize_ws

# --- StatusItem schema -------------------------------------------------------

# Corpus sources per docs/schema.md. "ticket", "doc" and "export" appear in
# data/corpus/*.jsonl; an item may not claim a source the corpus cannot contain.
SOURCES = ("slack", "email", "ticket", "doc", "export")
STATUSES = ("on_track", "at_risk", "blocked", "unclear")
CONFIDENCES = ("high", "medium", "low")

FIELDS = ("source", "topic", "status", "owner", "blocker", "confidence", "evidence")


@dataclass
class StatusItem:
    source: str  # "slack" | "email" | "ticket" | "doc" | "export"
    topic: str  # inferred project/team name
    status: str  # "on_track" | "at_risk" | "blocked" | "unclear"
    owner: str | None
    blocker: str | None
    confidence: str  # "high" | "medium" | "low"
    # [{"artifact_id": "slk_0041", "span": "verbatim quote"}] — >=1 required.
    # docs/schema.md: no evidence -> discard the item, that's a hallucination.
    evidence: list[dict] = field(default_factory=list)


class ExtractionError(RuntimeError):
    """Raised when the model's output can't be parsed or doesn't match the schema."""


# JSON Schema handed to Gemini so the response is constrained server-side.
# The validator below still re-checks everything — belt and braces.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": list(SOURCES)},
                    "topic": {"type": "string"},
                    "status": {"type": "string", "enum": list(STATUSES)},
                    "owner": {"type": ["string", "null"]},
                    "blocker": {"type": ["string", "null"]},
                    "confidence": {"type": "string", "enum": list(CONFIDENCES)},
                    "evidence": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "artifact_id": {"type": "string"},
                                "span": {"type": "string"},
                            },
                            "required": ["artifact_id", "span"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": list(FIELDS),
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


# --- Prompt ------------------------------------------------------------------

SYSTEM_PROMPT = f"""You extract structured status updates from workplace messages \
(Slack, email, tickets) for a company-wide operations briefing.

Every message is labelled with an artifact id like [slk_0041]. You must cite them.

Return a JSON object of the form {{"items": [...]}}, where each item has exactly \
these seven keys:
- source: {" or ".join(json.dumps(s) for s in SOURCES)}. Copy it from the message the item came from.
- topic: short inferred project or team name, e.g. "Billing migration", "Mobile app". \
Title case, no trailing punctuation. Never null.
- status: one of {", ".join(json.dumps(s) for s in STATUSES)}.
- owner: the person accountable for the work, as named in the message. Use null if no \
owner is stated or implied — do NOT default to the sender just because they wrote the message.
- blocker: one short sentence naming what is impeding the work — for "blocked" items \
what is blocking them, and for "at_risk" items what is putting the date at risk (a vendor \
delay, a lost buffer, a dependency). Use null only when genuinely nothing is impeding \
progress, which should be the normal case for "on_track" items.
- confidence: one of {", ".join(json.dumps(c) for c in CONFIDENCES)} — how confident you are \
that this item accurately reflects a real status signal.
- evidence: a non-empty list of {{"artifact_id": ..., "span": ...}}. The artifact_id is \
the id in square brackets on the message. The span is a SHORT quote (one clause or \
sentence, under 200 characters) copied CHARACTER FOR CHARACTER out of that message's \
text. Do not paraphrase it, do not fix its typos, do not expand its abbreviations, do \
not add the square-bracket label or the "source=" header line to it. Cite one span per \
message that genuinely supports the item — usually one or two, and every message you \
merged into the item.

Extraction rules:
0. EVERY item needs at least one evidence span that appears verbatim in the message you \
took it from. Spans are checked against the source text in code after you answer, and \
an item whose span cannot be found is thrown away — so a paraphrased span costs you the \
whole item. If you cannot quote it, do not claim it.
1. Emit one item per distinct project/topic. If several messages discuss the same project, \
merge them into a single item rather than emitting duplicates. Messages are listed in \
chronological order, and THE MOST RECENT MESSAGE WINS for status, owner, and blocker — \
earlier messages are background only. If a later message resolves an earlier blocker \
("legal came back", "unblocked", "shipped"), the item is no longer blocked, and the \
blocker field should reflect what remains, or be null if nothing does.
2. SKIP messages with no status content. Social chatter, logistics, scheduling, thanks, jokes, \
and questions with no answer are not status updates. Do not manufacture an item for them. \
If none of the messages contain status content, return {{"items": []}}.
3. Do NOT guess. If a message clearly concerns a project but you cannot cleanly infer the \
status or the owner, emit the item with status "unclear" and/or owner null, and set \
confidence to "low". Inventing a plausible-sounding owner, blocker, or status is a failure; \
reporting low confidence is the correct behavior.
4. Use only what the messages actually say. Never introduce names, dates, systems, or \
blockers that do not appear in the text.

Confidence calibration:
- "high": the message states the status explicitly and unambiguously.
- "medium": the status is clearly implied but some detail (owner, severity) is inferred.
- "low": you are reading between the lines, or key fields came out null/"unclear"."""


def _artifact_id(msg: dict, index: int) -> str:
    """The id the model must cite. Falls back to a positional id for fixture messages."""
    value = msg.get("artifact_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"msg_{index}"


def _format_messages(messages: list[dict]) -> str:
    """Render the raw messages into a labelled block for the model.

    The label is the artifact_id, not a counter — that is what the model cites and what
    the span check keys on, so it has to be the real id from the corpus.
    """
    if not isinstance(messages, list):
        raise TypeError(f"messages must be a list, got {type(messages).__name__}")

    blocks = []
    for i, msg in enumerate(messages, start=1):
        missing = [k for k in ("source", "sender", "timestamp", "text") if k not in msg]
        if missing:
            raise ValueError(f"message {i} is missing required key(s): {', '.join(missing)}")
        blocks.append(
            f"[{_artifact_id(msg, i)}] source={msg['source']} | sender={msg['sender']} "
            f"| timestamp={msg['timestamp']}\n{msg['text']}"
        )
    return "\n\n".join(blocks)


# --- Response parsing + strict validation ------------------------------------


def _response_text(response: Any) -> str:
    """Pull the text out of the response, failing loudly if the model returned nothing."""
    try:
        return response_text(response, label="extraction")
    except Exception as e:  # normalize to this module's error type for callers
        raise ExtractionError(str(e)) from e


def _parse_json(raw: str) -> Any:
    """Parse the model's JSON. Delegates to the shared coercion in src/llm.py."""
    try:
        return coerce_json(raw)
    except LLMJSONError as e:
        raise ExtractionError(
            f"Model output was not valid JSON ({e}). First 300 chars:\n{e.raw_text[:300]}"
        ) from e


def _validate_item(obj: Any, index: int) -> StatusItem:
    """Validate one raw item against the StatusItem schema. Raises on any mismatch."""
    where = f"items[{index}]"

    if not isinstance(obj, dict):
        raise ExtractionError(f"{where}: expected an object, got {type(obj).__name__}")

    missing = [f for f in FIELDS if f not in obj]
    if missing:
        raise ExtractionError(f"{where}: missing field(s): {', '.join(missing)}")
    extra = [k for k in obj if k not in FIELDS]
    if extra:
        raise ExtractionError(f"{where}: unexpected field(s): {', '.join(extra)}")

    for field, allowed in (("source", SOURCES), ("status", STATUSES), ("confidence", CONFIDENCES)):
        if obj[field] not in allowed:
            raise ExtractionError(
                f"{where}.{field}: expected one of {list(allowed)}, got {obj[field]!r}"
            )

    if not isinstance(obj["topic"], str) or not obj["topic"].strip():
        raise ExtractionError(f"{where}.topic: expected a non-empty string, got {obj['topic']!r}")

    for key in ("owner", "blocker"):
        value = obj[key]
        if value is not None and not isinstance(value, str):
            raise ExtractionError(
                f"{where}.{key}: expected a string or null, got {type(value).__name__}"
            )
        if isinstance(value, str) and not value.strip():
            raise ExtractionError(f"{where}.{key}: empty string — use null instead")

    return StatusItem(
        source=obj["source"],
        topic=obj["topic"].strip(),
        status=obj["status"],
        owner=obj["owner"],
        blocker=obj["blocker"],
        confidence=obj["confidence"],
        evidence=_validate_evidence(obj["evidence"], where),
    )


def _validate_evidence(raw: Any, where: str) -> list[dict]:
    """Shape check on the evidence array. The substring check is separate, below."""
    if not isinstance(raw, list) or not raw:
        raise ExtractionError(
            f"{where}.evidence: expected a non-empty list of "
            f"{{artifact_id, span}}, got {raw!r} — an item with no evidence is a "
            f"hallucination by definition (docs/schema.md)"
        )

    evidence = []
    for i, entry in enumerate(raw):
        at = f"{where}.evidence[{i}]"
        if not isinstance(entry, dict):
            raise ExtractionError(f"{at}: expected an object, got {type(entry).__name__}")
        artifact_id = entry.get("artifact_id")
        span = entry.get("span")
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise ExtractionError(f"{at}.artifact_id: expected a non-empty string")
        if not isinstance(span, str) or not span.strip():
            raise ExtractionError(f"{at}.span: expected a non-empty verbatim quote")
        evidence.append({"artifact_id": artifact_id.strip(), "span": span})
    return evidence


def _validate_payload(payload: Any) -> list[StatusItem]:
    if not isinstance(payload, dict):
        raise ExtractionError(
            f"Expected a JSON object with an 'items' key, got {type(payload).__name__}"
        )
    if "items" not in payload:
        raise ExtractionError(f"Response object has no 'items' key (keys: {list(payload)})")
    if not isinstance(payload["items"], list):
        raise ExtractionError(f"'items' must be a list, got {type(payload['items']).__name__}")

    return [_validate_item(raw, i) for i, raw in enumerate(payload["items"])]


# --- Public API --------------------------------------------------------------


def _call_model(contents: str) -> str:
    """Send one extraction request to Gemini and return the raw response text.

    Isolated so tests can stub the network call.
    """
    response = generate_content(
        contents,
        types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_json_schema=RESPONSE_SCHEMA,
            max_output_tokens=8192,
            # Extraction doesn't need deep reasoning; keeps latency and free-tier
            # token burn down. Bump to MEDIUM if it starts missing implied status.
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
            # Temperature deliberately left at the model default — Gemini 3.x can
            # degrade (looping, truncation) at low temperatures.
        ),
    )
    return _response_text(response)


def check_evidence_spans(
    items: list[dict], messages: list[dict]
) -> tuple[list[dict], list[str]]:
    """Drop any item whose evidence is not literally present in its source artifact.

    Exact substring match after whitespace normalization — not fuzzy, not LLM-judged.
    An item citing an artifact that wasn't in the batch, or quoting words the artifact
    never contained, is a hallucination and is discarded rather than repaired.

    Returns (kept_items, drop_reasons). The reasons are printed, not swallowed.
    """
    texts = {
        _artifact_id(msg, i): normalize_ws(str(msg.get("text", "")))
        for i, msg in enumerate(messages, start=1)
    }

    kept: list[dict] = []
    dropped: list[str] = []

    for item in items:
        verified = []
        problems = []
        for entry in item.get("evidence", []):
            artifact_id = entry["artifact_id"]
            span = normalize_ws(entry["span"])
            source_text = texts.get(artifact_id)
            if source_text is None:
                problems.append(f"cites {artifact_id}, which was not in this batch")
                continue
            if span not in source_text:
                problems.append(f"span not found in {artifact_id}: {entry['span'][:60]!r}")
                continue
            verified.append(entry)

        if verified:
            item = dict(item, evidence=verified)
            kept.append(item)
            # Partial failure still loses the bad spans, and we say so.
            for problem in problems:
                dropped.append(f"dropped evidence from item {item['topic']!r}: {problem}")
        else:
            dropped.append(
                f"dropped hallucinated item {item.get('topic')!r}: "
                + "; ".join(problems or ["no evidence at all"])
            )

    return kept, dropped


def extract(messages: list[dict], verify: bool = True) -> list[dict]:
    """Extract StatusItems from raw artifacts.

    Args:
        messages: dicts with keys {source, sender, timestamp, text} and, for corpus
            artifacts, artifact_id. The artifact_id is what the model cites.
        verify: run the evidence-span substring check. Only ever False in tests that
            are exercising something else.

    Returns:
        A list of StatusItem dicts, each carrying verified evidence. Empty if no
        message carried status content.

    Raises:
        ExtractionError: the model's output was unparseable or off-schema.
    """
    if not messages:
        return []

    raw = _call_model(
        f"Extract status items from these {len(messages)} message(s).\n\n"
        f"{_format_messages(messages)}"
    )
    items = [asdict(item) for item in _validate_payload(_parse_json(raw))]

    if not verify:
        return items

    kept, dropped = check_evidence_spans(items, messages)
    if dropped:
        print(f"[extraction] {len(dropped)} evidence check failure(s):")
        for reason in dropped:
            print(f"  - {reason}")
    return kept


# --- Batch extraction at scale ----------------------------------------------
#
# One prompt per 10k messages is neither possible nor desirable: extraction recall
# degrades long before the context limit. Instead we chunk, extract chunks in
# parallel, and merge items that describe the same topic.

CHUNK_SIZE = 40
MAX_WORKERS = 4


def _merge_key(topic: str) -> str:
    """Loose normalization so 'Billing migration' and 'billing Migration' merge."""
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", topic.lower())
    tokens = [t for t in cleaned.split() if t not in ("the", "a", "an", "project", "team")]
    return " ".join(sorted(tokens)) or topic.lower().strip()


def _union_evidence(*groups: list[dict]) -> list[dict]:
    """Combine evidence lists, deduping on (artifact_id, normalized span)."""
    seen: set[tuple[str, str]] = set()
    combined: list[dict] = []
    for group in groups:
        for entry in group or []:
            key = (entry.get("artifact_id", ""), normalize_ws(entry.get("span", "")))
            if key in seen:
                continue
            seen.add(key)
            combined.append(entry)
    return combined


def merge_items(items: list[dict]) -> list[dict]:
    """Collapse items describing the same topic into one.

    The most urgent status wins (a 'blocked' report must not be lost behind an
    'on_track' one from another chunk); within equal urgency, higher confidence
    wins. Owner and blocker are filled in from whichever item actually has them.
    """
    merged: dict[str, dict] = {}

    for item in items:
        key = _merge_key(item["topic"])
        current = merged.get(key)
        if current is None:
            merged[key] = dict(item)
            continue

        incoming_rank = (STATUS_URGENCY[item["status"]], CONFIDENCE_URGENCY[item["confidence"]])
        current_rank = (STATUS_URGENCY[current["status"]], CONFIDENCE_URGENCY[current["confidence"]])
        if incoming_rank > current_rank:
            winner, loser = dict(item), current
        else:
            winner, loser = current, item

        # Never drop a known owner or blocker just because the winning read lacked one.
        winner["owner"] = winner["owner"] or loser["owner"]
        winner["blocker"] = winner["blocker"] or loser["blocker"]
        # Evidence is unioned, never replaced: the losing read still saw real artifacts,
        # and dropping its spans would silently narrow what the answer can cite.
        winner["evidence"] = _union_evidence(
            winner.get("evidence", []), loser.get("evidence", [])
        )
        merged[key] = winner

    return list(merged.values())


STATUS_URGENCY = {"blocked": 3, "at_risk": 2, "unclear": 1, "on_track": 0}
CONFIDENCE_URGENCY = {"high": 2, "medium": 1, "low": 0}


def extract_batch(
    messages: list[dict],
    chunk_size: int = CHUNK_SIZE,
    max_workers: int = MAX_WORKERS,
    progress: bool = False,
) -> list[dict]:
    """Extract from an arbitrarily large message list.

    Chunks the input, runs chunks concurrently, then merges duplicate topics.
    A failing chunk does not sink the run — it is reported and skipped, so one bad
    batch can't cost you the whole briefing.
    """
    if not messages:
        return []
    if len(messages) <= chunk_size:
        return extract(messages)

    chunks = [messages[i : i + chunk_size] for i in range(0, len(messages), chunk_size)]
    collected: list[dict] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(extract, chunk): i for i, chunk in enumerate(chunks)}
        for done in as_completed(futures):
            index = futures[done]
            try:
                collected.extend(done.result())
            except Exception as e:  # noqa: BLE001 - one bad chunk shouldn't kill the run
                failures.append(f"chunk {index + 1}: {type(e).__name__}: {e}")
            if progress:
                print(f"  extracted chunk {len(collected) and index + 1}/{len(chunks)}")

    if failures:
        print(f"[extraction] {len(failures)}/{len(chunks)} chunk(s) failed:")
        for failure in failures:
            print(f"  - {failure}")
        if len(failures) == len(chunks):
            raise ExtractionError(f"every chunk failed; first error: {failures[0]}")

    return merge_items(collected)


# --- Manual test -------------------------------------------------------------

SAMPLE_MESSAGES = [
    {
        "source": "slack",
        "sender": "priya",
        "timestamp": "2026-08-01T09:14:00Z",
        "text": (
            "Billing migration update: we're still stuck on the Stripe webhook replay. "
            "Nothing moves until legal signs off on the data retention change — Marcus owns "
            "that and said he'd chase it today. Realistically we slip past Friday."
        ),
    },
    {
        "source": "email",
        "sender": "dan@company.com",
        "timestamp": "2026-08-01T09:40:00Z",
        "text": (
            "Subject: Mobile app v3 — weekly\n\n"
            "All green. Onboarding rewrite merged Tuesday, QA signed off this morning, "
            "and we're on schedule to ship to TestFlight next Wednesday. No blockers. "
            "I'll keep owning the release."
        ),
    },
    {
        "source": "slack",
        "sender": "aisha",
        "timestamp": "2026-08-01T10:02:00Z",
        "text": "anyone know if the good coffee machine on 3 is fixed yet ☕️ asking for a friend",
    },
    {
        "source": "slack",
        "sender": "tomas",
        "timestamp": "2026-08-01T10:26:00Z",
        "text": (
            "re: the search reindex — kind of a mess right now, still digging into it. "
            "will know more after I look at the logs"
        ),
    },
]


if __name__ == "__main__":
    items = extract(SAMPLE_MESSAGES)
    print(f"Extracted {len(items)} status item(s) from {len(SAMPLE_MESSAGES)} message(s):\n")
    print(json.dumps(items, indent=2))
