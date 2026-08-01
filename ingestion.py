"""Ingest raw messages from Slack / email and normalize them to a common shape.

Normalized message: {"source": "slack"|"email", "sender": str, "timestamp": str, "text": str}

Two modes:
  fixtures — load from a JSON file (default: sample_data/messages.json). Use this
             for development and for a first pass over a teammate's exported data.
  slack    — live pull via the Slack Web API. Requires SLACK_BOT_TOKEN.
"""

import base64
import json
import os
import re
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

SAMPLE_DIR = Path(__file__).parent / "sample_data"
DEFAULT_FIXTURES = SAMPLE_DIR / "messages.json"
GMAIL_FIXTURES = SAMPLE_DIR / "gmail_raw.json"

# Field-name variants we accept when normalizing, so an export that says
# "user"/"body"/"ts" instead of "sender"/"text"/"timestamp" still works.
SENDER_KEYS = ("sender", "user", "username", "user_name", "from", "author", "real_name")
TEXT_KEYS = ("text", "body", "message", "content")
TIMESTAMP_KEYS = ("timestamp", "ts", "date", "created_at", "time")


class IngestionError(RuntimeError):
    """Raised when messages can't be loaded or don't have the expected shape."""


def _first_present(record: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if record.get(key) not in (None, ""):
            return record[key]
    return None


def normalize_messages(raw_messages: list[dict], default_source: str = "slack") -> list[dict]:
    """Normalize heterogeneous message dicts into the common schema.

    Records with no usable text are dropped (with a count reported by the caller);
    everything else is coerced to strings so downstream code can rely on the shape.
    """
    normalized: list[dict] = []

    for record in raw_messages:
        if not isinstance(record, dict):
            raise IngestionError(f"expected message objects, got {type(record).__name__}")

        text = _first_present(record, TEXT_KEYS)
        if not text or not str(text).strip():
            continue  # nothing to extract from — skip silently

        source = record.get("source") or default_source
        if source not in ("slack", "email"):
            raise IngestionError(
                f"unsupported source {source!r} (expected 'slack' or 'email'). "
                "Set a 'source' field on each message, or pass default_source."
            )

        normalized.append(
            {
                "source": source,
                "sender": str(_first_present(record, SENDER_KEYS) or "unknown"),
                "timestamp": str(_first_present(record, TIMESTAMP_KEYS) or ""),
                "text": str(text).strip(),
            }
        )

    return normalized


# --- Gmail API adapter -------------------------------------------------------
#
# Turns raw `users.messages.get(format='full')` responses into normalized records,
# so a JSON dump straight out of the Gmail API can be fed to the pipeline as-is.

# "On <date> <someone> wrote:" — everything after this is quoted thread history.
_REPLY_MARKER = re.compile(
    r"^\s*On .{0,120}?\bwrote:\s*$|^\s*-{2,}\s*Original Message\s*-{2,}\s*$",
    re.MULTILINE,
)
_SIGNATURE_MARKER = re.compile(r"^--\s*$", re.MULTILINE)
_HTML_TAG = re.compile(r"<[^>]+>")


def _b64url_decode(data: str) -> str:
    """Decode Gmail's base64url body data, which usually arrives without padding."""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")


def _header(payload: dict, name: str) -> str:
    for header in payload.get("headers", []) or []:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def _walk_parts(payload: dict):
    """Yield every MIME part, depth first (Gmail nests multipart within multipart)."""
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _walk_parts(part)


def _extract_body(payload: dict) -> str:
    """Prefer text/plain; fall back to de-tagged text/html."""
    html_fallback = ""
    for part in _walk_parts(payload):
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            return _b64url_decode(data)
        if mime == "text/html" and not html_fallback:
            html_fallback = _b64url_decode(data)

    if html_fallback:
        text = _HTML_TAG.sub(" ", html_fallback)
        text = text.replace("&mdash;", "—").replace("&nbsp;", " ").replace("&amp;", "&")
        return re.sub(r"[ \t]+", " ", text)
    return ""


def _strip_quoted_history(text: str) -> str:
    """Drop quoted reply chains and signature blocks.

    Quoted history often contains stale status ("everything was green") that would
    otherwise be extracted as if it were current.
    """
    match = _REPLY_MARKER.search(text)
    if match:
        text = text[: match.start()]

    lines = [line for line in text.splitlines() if not line.lstrip().startswith(">")]
    text = "\n".join(lines)

    sig = _SIGNATURE_MARKER.search(text)
    if sig:
        text = text[: sig.start()]

    return text.strip()


def gmail_message_to_record(message: dict) -> dict | None:
    """Convert one Gmail API message into a normalized record, or None if empty."""
    payload = message.get("payload") or {}

    body = _strip_quoted_history(_extract_body(payload))
    if not body:
        body = (message.get("snippet") or "").strip()
    if not body:
        return None

    subject = _header(payload, "Subject").strip()
    text = f"Subject: {subject}\n\n{body}" if subject else body

    _, address = parseaddr(_header(payload, "From"))
    sender = address or _header(payload, "From") or "unknown"

    internal = message.get("internalDate")
    if internal:
        timestamp = (
            datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    else:
        timestamp = _header(payload, "Date")

    return {"source": "email", "sender": sender, "timestamp": timestamp, "text": text}


def normalize_gmail_messages(raw_messages: list[dict]) -> list[dict]:
    """Convert a list of raw Gmail API messages into normalized records."""
    records = []
    for message in raw_messages:
        if not isinstance(message, dict):
            raise IngestionError(f"expected Gmail message objects, got {type(message).__name__}")
        record = gmail_message_to_record(message)
        if record:
            records.append(record)
    return records


def _looks_like_gmail(records: list) -> bool:
    """Raw Gmail messages carry a 'payload' dict; normalized records don't."""
    return bool(records) and isinstance(records[0], dict) and "payload" in records[0]


def load_fixture_messages(path: str | Path = DEFAULT_FIXTURES) -> list[dict]:
    """Load messages from a JSON file.

    Accepts either already-normalized records or raw Gmail API messages; the format
    is detected automatically.
    """
    path = Path(path)
    if not path.exists():
        raise IngestionError(f"fixture file not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise IngestionError(f"{path} is not valid JSON: {e}") from e

    # Tolerate {"messages": [...]} as well as a bare list.
    if isinstance(data, dict) and "messages" in data:
        data = data["messages"]
    if not isinstance(data, list):
        raise IngestionError(f"{path} must contain a list of messages, got {type(data).__name__}")

    if _looks_like_gmail(data):
        return normalize_gmail_messages(data)
    return normalize_messages(data)


def fetch_slack_messages(channel: str, limit: int = 50) -> list[dict]:
    """Pull recent messages from one Slack channel via the Web API.

    NOTE: never exercised against a live workspace — no token was available when
    this was written. Verify before relying on it.
    """
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise IngestionError("SLACK_BOT_TOKEN is not set — cannot pull live Slack messages.")

    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    client = WebClient(token=token)
    try:
        response = client.conversations_history(channel=channel, limit=limit)
        raw = response.get("messages", [])

        # Resolve user IDs to display names where we can; fall back to the raw ID.
        names: dict[str, str] = {}
        for message in raw:
            uid = message.get("user")
            if uid and uid not in names:
                try:
                    info = client.users_info(user=uid)
                    profile = info["user"]
                    names[uid] = profile.get("real_name") or profile.get("name") or uid
                except SlackApiError:
                    names[uid] = uid
    except SlackApiError as err:
        raise IngestionError(f"Slack API error: {err.response['error']}") from err

    return normalize_messages(
        [
            {
                "source": "slack",
                "sender": names.get(m.get("user", ""), m.get("user", "unknown")),
                "timestamp": m.get("ts", ""),
                "text": m.get("text", ""),
            }
            for m in raw
            if not m.get("subtype")  # skip joins, leaves, channel_topic, etc.
        ]
    )


def fetch_gmail_messages(query: str = "newer_than:1d", limit: int = 50) -> list[dict]:
    """Pull recent email live via the Gmail API. Not implemented.

    The conversion half *is* implemented and tested — see gmail_message_to_record().
    Only the OAuth + list/get plumbing is missing, so a JSON dump of
    users.messages.get(format='full') responses works today via
    ingest(source='gmail', fixtures_path=...).
    """
    raise NotImplementedError(
        "Live Gmail ingestion is not implemented (needs OAuth credentials). "
        "Dump users.messages.get(format='full') responses to JSON and use "
        "ingest(source='gmail', fixtures_path=...), which parses them via "
        "gmail_message_to_record()."
    )


def ingest(
    source: str = "fixtures",
    fixtures_path: str | Path = DEFAULT_FIXTURES,
    channel: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return normalized messages from the requested source.

    source:
      fixtures — JSON file; auto-detects normalized records vs raw Gmail messages
      gmail    — JSON dump of raw Gmail API messages (defaults to the bundled sample)
      slack    — live Slack Web API pull (requires SLACK_BOT_TOKEN + channel)
    """
    if source == "fixtures":
        return load_fixture_messages(fixtures_path)
    if source == "gmail":
        # Compare as Path — callers (e.g. argparse) may pass the default as a str.
        path = Path(fixtures_path)
        if path == Path(DEFAULT_FIXTURES):
            path = GMAIL_FIXTURES
        return load_fixture_messages(path)
    if source == "slack":
        if not channel:
            raise IngestionError("source='slack' requires a channel (e.g. 'C0123456789').")
        return fetch_slack_messages(channel, limit=limit)
    raise IngestionError(
        f"unknown source {source!r} (expected 'fixtures', 'gmail', or 'slack')"
    )


if __name__ == "__main__":
    for label, path in (("mixed Slack + email", DEFAULT_FIXTURES), ("raw Gmail API", GMAIL_FIXTURES)):
        messages = load_fixture_messages(path)
        print(f"\n{label} — {len(messages)} message(s) from {path.name}:")
        for i, message in enumerate(messages, start=1):
            preview = message["text"].replace("\n", " ")[:64]
            print(f"  [{i}] {message['source']:5} {message['sender']:24} {preview}...")
