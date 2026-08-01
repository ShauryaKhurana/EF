"""Extract structured status updates from Slack/email messages using the Gemini API.

Standalone: `python extraction.py` runs the sample messages in __main__.
Requires GEMINI_API_KEY in a .env file (see .env.example).

Uses gemini-3.6-flash, which has a free tier. Other free-tier options if you hit
rate limits or want something lighter/older:
    gemini-3.5-flash, gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-2.5-flash
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

load_dotenv()

# Free-tier daily request quotas are per-model and small (gemini-3.6-flash allows 20
# requests/day at time of writing). Both are overridable from .env so you can switch
# models without touching code; check your own limits at aistudio.google.com/rate-limit.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip()
FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_FALLBACK_MODELS", "gemini-2.5-flash,gemini-2.5-flash-lite"
    ).split(",")
    if m.strip() and m.strip() != MODEL
]

MAX_RETRIES = 3
MAX_RETRY_WAIT = 65.0  # don't sit through a multi-minute backoff mid-demo

# --- StatusItem schema -------------------------------------------------------

SOURCES = ("slack", "email")
STATUSES = ("on_track", "at_risk", "blocked", "unclear")
CONFIDENCES = ("high", "medium", "low")

FIELDS = ("source", "topic", "status", "owner", "blocker", "confidence")


@dataclass
class StatusItem:
    source: str  # "slack" | "email"
    topic: str  # inferred project/team name
    status: str  # "on_track" | "at_risk" | "blocked" | "unclear"
    owner: str | None
    blocker: str | None
    confidence: str  # "high" | "medium" | "low"


class ExtractionError(RuntimeError):
    """Raised when the model's output can't be parsed or doesn't match the schema."""


class QuotaExhausted(RuntimeError):
    """Every candidate model hit its rate limit."""


def _is_rate_limit(error: Exception) -> bool:
    return isinstance(error, errors.ClientError) and getattr(error, "code", None) == 429


def _is_daily_quota(error: Exception) -> bool:
    """Daily caps don't recover in seconds — fall straight through to another model."""
    return "PerDay" in str(getattr(error, "details", "") or "") or "per day" in str(error).lower()


def _retry_delay(error: Exception, default: float = 5.0) -> float:
    match = re.search(r"retry in ([0-9.]+)s", str(error), re.IGNORECASE)
    if not match:
        match = re.search(r"'retryDelay': '([0-9.]+)s'", str(error))
    return min(float(match.group(1)) + 1 if match else default, MAX_RETRY_WAIT)


def generate_content(contents: Any, config: Any, models: list[str] | None = None) -> Any:
    """Call Gemini, retrying transient rate limits and falling back across models.

    A per-minute limit is waited out; a per-day quota is not (it won't clear in time),
    so we move to the next model immediately. This is what keeps a live demo alive
    when the primary model's free-tier daily allowance runs out mid-run.
    """
    client = genai.Client(api_key=require_api_key())
    candidates = models or [MODEL, *FALLBACK_MODELS]
    last_error: Exception | None = None

    for model in candidates:
        for attempt in range(MAX_RETRIES):
            try:
                return client.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except Exception as e:  # noqa: BLE001 - re-raised below unless retryable
                if not _is_rate_limit(e):
                    raise
                last_error = e
                if _is_daily_quota(e) or attempt == MAX_RETRIES - 1:
                    if len(candidates) > 1:
                        print(f"[gemini] {model} rate-limited; trying next model...")
                    break
                delay = _retry_delay(e)
                print(f"[gemini] {model} rate-limited; retrying in {delay:.0f}s...")
                time.sleep(delay)

    raise QuotaExhausted(
        f"All models rate-limited ({', '.join(candidates)}). "
        f"Check your limits at https://aistudio.google.com/rate-limit or set "
        f"GEMINI_MODEL in .env. Last error: {last_error}"
    )


def require_api_key() -> str:
    """Return the Gemini API key, or explain precisely what's wrong with it.

    Catches the common case of .env still holding the .env.example placeholder,
    which is non-empty and would otherwise fail later as an opaque auth error.
    """
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()

    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and fill in your key "
            "(get one free at https://aistudio.google.com/apikey)."
        )
    if key.startswith("your-") or key == "your-gemini-api-key":
        raise RuntimeError(
            "GEMINI_API_KEY is still the placeholder from .env.example. Open .env and "
            "replace 'your-gemini-api-key' with your real key "
            "(get one free at https://aistudio.google.com/apikey)."
        )
    return key


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
(Slack and email) for a company-wide operations briefing.

Return a JSON object of the form {{"items": [...]}}, where each item has exactly \
these six keys:
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

Extraction rules:
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


def _format_messages(messages: list[dict]) -> str:
    """Render the raw messages into a numbered block for the model."""
    if not isinstance(messages, list):
        raise TypeError(f"messages must be a list, got {type(messages).__name__}")

    blocks = []
    for i, msg in enumerate(messages, start=1):
        missing = [k for k in ("source", "sender", "timestamp", "text") if k not in msg]
        if missing:
            raise ValueError(f"message {i} is missing required key(s): {', '.join(missing)}")
        blocks.append(
            f"[{i}] source={msg['source']} | sender={msg['sender']} | timestamp={msg['timestamp']}\n"
            f"{msg['text']}"
        )
    return "\n\n".join(blocks)


# --- Response parsing + strict validation ------------------------------------


def _response_text(response: Any) -> str:
    """Pull the text out of the response, failing loudly if the model returned nothing."""
    text = (response.text or "").strip()
    if text:
        return text

    # Empty response: surface whatever the API said about why.
    reason = "unknown"
    candidates = getattr(response, "candidates", None)
    if candidates:
        reason = f"finish_reason={getattr(candidates[0], 'finish_reason', None)}"
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None:
        reason += f", prompt_feedback={feedback}"
    raise ExtractionError(f"Model returned no text ({reason}).")


def _parse_json(raw: str) -> Any:
    """Parse the model's JSON, tolerating markdown fences but nothing else."""
    text = raw.strip()
    if text.startswith("```"):
        # ```json\n{...}\n```  ->  {...}
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractionError(
            f"Model output was not valid JSON ({e}). First 300 chars:\n{raw[:300]}"
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

    for field in ("owner", "blocker"):
        value = obj[field]
        if value is not None and not isinstance(value, str):
            raise ExtractionError(
                f"{where}.{field}: expected a string or null, got {type(value).__name__}"
            )
        if isinstance(value, str) and not value.strip():
            raise ExtractionError(f"{where}.{field}: empty string — use null instead")

    return StatusItem(
        source=obj["source"],
        topic=obj["topic"].strip(),
        status=obj["status"],
        owner=obj["owner"],
        blocker=obj["blocker"],
        confidence=obj["confidence"],
    )


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


def extract(messages: list[dict]) -> list[dict]:
    """Extract StatusItems from raw messages.

    Args:
        messages: dicts with keys {source, sender, timestamp, text}.

    Returns:
        A list of StatusItem dicts. Empty if no message carried status content.

    Raises:
        ExtractionError: the model's output was unparseable or off-schema.
    """
    if not messages:
        return []

    raw = _call_model(
        f"Extract status items from these {len(messages)} message(s).\n\n"
        f"{_format_messages(messages)}"
    )
    items = _validate_payload(_parse_json(raw))
    return [asdict(item) for item in items]


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
