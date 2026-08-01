"""Persistent company-context store: messages, topics, and topic embeddings.

Design note — we embed and index *topics*, not raw messages. Tens of thousands of
messages collapse into hundreds of topics, so retrieval stays fast and cheap while
raw messages remain linked for citation.

Tables
    messages     every ingested message, deduped by content hash
    topics       one row per project/workstream, carrying its current status,
                 a rolling summary, and an embedding for semantic search
    observations append-only history of every status reading for a topic
"""

import hashlib
import json
import os
import re
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from dotenv import load_dotenv
from google import genai
from google.genai import types

from extraction import MODEL, require_api_key

load_dotenv()

DEFAULT_DB = Path(__file__).parent / "coo_agent.db"

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768  # gemini-embedding-001 supports MRL truncation; 768 keeps the DB small
EMBED_BATCH = 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    sender      TEXT NOT NULL,
    timestamp   TEXT,
    text        TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    topic_key    TEXT PRIMARY KEY,
    topic        TEXT NOT NULL,
    status       TEXT NOT NULL,
    owner        TEXT,
    blocker      TEXT,
    confidence   TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    mentions     INTEGER NOT NULL DEFAULT 0,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    embedding    BLOB,
    embedded_for TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_key  TEXT NOT NULL REFERENCES topics(topic_key),
    status     TEXT NOT NULL,
    owner      TEXT,
    blocker    TEXT,
    confidence TEXT NOT NULL,
    source     TEXT NOT NULL,
    seen_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topics_status ON topics(status);
CREATE INDEX IF NOT EXISTS idx_topics_last_seen ON topics(last_seen);
CREATE INDEX IF NOT EXISTS idx_obs_topic ON observations(topic_key);
"""

# Higher = more urgent. Used for ranking and for deciding which reading wins.
STATUS_RANK = {"blocked": 3, "at_risk": 2, "unclear": 1, "on_track": 0}
CONFIDENCE_RANK = {"high": 2, "medium": 1, "low": 0}

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_STOPWORDS = {"the", "a", "an", "project", "workstream", "initiative", "team"}


class StoreError(RuntimeError):
    """Raised when the store can't complete an operation."""


def topic_key(topic: str) -> str:
    """Normalize a topic name so 'Billing Migration' and 'billing migration' merge.

    Deliberately conservative: lowercase, strip punctuation, drop filler words,
    sort the remaining tokens so word order doesn't fragment a topic.
    """
    cleaned = _PUNCT.sub(" ", topic.lower())
    tokens = [t for t in cleaned.split() if t and t not in _STOPWORDS]
    return " ".join(sorted(tokens)) or topic.lower().strip()


def message_id(message: dict) -> str:
    """Stable content hash, so re-ingesting the same message is a no-op."""
    raw = f"{message.get('source')}|{message.get('sender')}|{message.get('timestamp')}|{message.get('text')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _pack(vector: Iterable[float]) -> bytes:
    values = list(vector)
    return struct.pack(f"{len(values)}f", *values)


def _unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


class Store:
    """SQLite-backed company context. Safe to open repeatedly; schema is idempotent."""

    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- messages ------------------------------------------------------------

    def add_messages(self, messages: list[dict]) -> int:
        """Insert messages, skipping ones already stored. Returns the new count."""
        now = _now()
        rows = [
            (message_id(m), m["source"], m["sender"], m.get("timestamp", ""), m["text"], now)
            for m in messages
        ]
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR IGNORE INTO messages (id, source, sender, timestamp, text, ingested_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def new_messages(self, messages: list[dict]) -> list[dict]:
        """Return only the messages not already in the store (for incremental runs)."""
        known = {row["id"] for row in self.conn.execute("SELECT id FROM messages")}
        return [m for m in messages if message_id(m) not in known]

    def message_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]

    # --- topics --------------------------------------------------------------

    def upsert_items(self, items: list[dict], seen_at: str | None = None) -> dict[str, int]:
        """Merge status items into the topic table.

        A newer reading replaces the stored one. When two readings share a
        timestamp, the more urgent status wins, then the more confident — so a
        'blocked' report is never silently overwritten by a vaguer 'unclear'.
        """
        seen_at = seen_at or _now()
        created = updated = 0

        for item in items:
            key = topic_key(item["topic"])
            row = self.conn.execute(
                "SELECT * FROM topics WHERE topic_key = ?", (key,)
            ).fetchone()

            self.conn.execute(
                "INSERT INTO observations (topic_key, status, owner, blocker, confidence,"
                " source, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, item["status"], item["owner"], item["blocker"],
                 item["confidence"], item["source"], seen_at),
            )

            if row is None:
                self.conn.execute(
                    "INSERT INTO topics (topic_key, topic, status, owner, blocker, confidence,"
                    " mentions, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (key, item["topic"], item["status"], item["owner"], item["blocker"],
                     item["confidence"], seen_at, seen_at),
                )
                created += 1
                continue

            if _supersedes(item, row, seen_at):
                self.conn.execute(
                    "UPDATE topics SET topic = ?, status = ?, owner = COALESCE(?, owner),"
                    " blocker = ?, confidence = ?, mentions = mentions + 1, last_seen = ?,"
                    " embedded_for = NULL WHERE topic_key = ?",
                    (item["topic"], item["status"], item["owner"], item["blocker"],
                     item["confidence"], seen_at, key),
                )
                updated += 1
            else:
                self.conn.execute(
                    "UPDATE topics SET mentions = mentions + 1 WHERE topic_key = ?", (key,)
                )

        self.conn.commit()
        return {"created": created, "updated": updated}

    @staticmethod
    def _clean(row: sqlite3.Row | dict) -> dict:
        """Drop binary columns so rows are safe to JSON-serialize into a prompt."""
        item = dict(row)
        item.pop("embedding", None)
        item.pop("embedded_for", None)
        return item

    def topics(self, status: str | None = None, limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM topics"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY last_seen DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._clean(row) for row in self.conn.execute(sql, params)]

    def status_items(self, limit: int | None = None) -> list[dict]:
        """Topics projected back into the StatusItem shape the briefing expects."""
        return [
            {
                "source": "store",
                "topic": row["topic"],
                "status": row["status"],
                "owner": row["owner"],
                "blocker": row["blocker"],
                "confidence": row["confidence"],
                "mentions": row["mentions"],
                "last_seen": row["last_seen"],
            }
            for row in self.ranked_topics(limit=limit)
        ]

    def topic_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM topics").fetchone()["n"]

    def ranked_topics(self, limit: int | None = None) -> list[dict]:
        """Topics ordered by urgency, then recency — the briefing's shortlist."""
        rows = self.topics()
        rows.sort(
            key=lambda r: (STATUS_RANK.get(r["status"], 0), r["last_seen"], r["mentions"]),
            reverse=True,
        )
        return rows[:limit] if limit else rows

    def history(self, key: str, limit: int = 20) -> list[dict]:
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM observations WHERE topic_key = ? ORDER BY seen_at DESC LIMIT ?",
                (key, limit),
            )
        ]

    # --- embeddings & retrieval ---------------------------------------------

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        client = genai.Client(api_key=require_api_key())
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start : start + EMBED_BATCH]
            response = client.models.embed_content(
                model=EMBED_MODEL,
                contents=batch,
                config=types.EmbedContentConfig(
                    task_type=task_type, output_dimensionality=EMBED_DIM
                ),
            )
            vectors.extend([e.values for e in response.embeddings])
        return vectors

    @staticmethod
    def _topic_text(row: dict) -> str:
        """The text we embed for a topic — what a question would plausibly match."""
        parts = [row["topic"], f"status: {row['status']}"]
        if row.get("owner"):
            parts.append(f"owner: {row['owner']}")
        if row.get("blocker"):
            parts.append(f"blocker: {row['blocker']}")
        if row.get("summary"):
            parts.append(row["summary"])
        return ". ".join(parts)

    def reindex(self, force: bool = False) -> int:
        """Embed topics whose text changed since they were last embedded."""
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM topics")]
        stale = [
            r for r in rows
            if force or r["embedding"] is None or r["embedded_for"] != self._topic_text(r)
        ]
        if not stale:
            return 0

        texts = [self._topic_text(r) for r in stale]
        vectors = self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

        self.conn.executemany(
            "UPDATE topics SET embedding = ?, embedded_for = ? WHERE topic_key = ?",
            [
                (_pack(vector), text, row["topic_key"])
                for row, text, vector in zip(stale, texts, vectors)
            ],
        )
        self.conn.commit()
        return len(stale)

    def search(self, question: str, k: int = 8) -> list[dict]:
        """Semantic search over topics. Falls back to keyword matching if unindexed."""
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM topics")]
        if not rows:
            return []

        indexed = [r for r in rows if r["embedding"] is not None]
        if not indexed:
            return self._keyword_search(question, rows, k)

        query = np.asarray(self._embed([question], task_type="RETRIEVAL_QUERY")[0], dtype=np.float32)
        matrix = np.vstack([_unpack(r["embedding"]) for r in indexed])

        # Cosine similarity; guard against zero vectors.
        norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
        norms[norms == 0] = 1e-9
        scores = (matrix @ query) / norms

        order = np.argsort(-scores)[:k]
        results = []
        for i in order:
            row = self._clean(indexed[int(i)])
            row["score"] = float(scores[int(i)])
            results.append(row)
        return results

    @staticmethod
    def _keyword_search(question: str, rows: list[dict], k: int) -> list[dict]:
        terms = {t for t in _PUNCT.sub(" ", question.lower()).split() if t not in _STOPWORDS}
        scored = []
        for row in rows:
            haystack = f"{row['topic']} {row.get('owner') or ''} {row.get('blocker') or ''}".lower()
            overlap = sum(1 for t in terms if t in haystack)
            if overlap:
                row = Store._clean(row)
                row["score"] = overlap / max(len(terms), 1)
                scored.append(row)
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:k]

    def stats(self) -> dict:
        by_status = {
            row["status"]: row["n"]
            for row in self.conn.execute(
                "SELECT status, COUNT(*) AS n FROM topics GROUP BY status"
            )
        }
        indexed = self.conn.execute(
            "SELECT COUNT(*) AS n FROM topics WHERE embedding IS NOT NULL"
        ).fetchone()["n"]
        return {
            "messages": self.message_count(),
            "topics": self.topic_count(),
            "indexed": indexed,
            "by_status": by_status,
        }


def _supersedes(item: dict, row: sqlite3.Row, seen_at: str) -> bool:
    """Should this reading replace the stored one?"""
    if seen_at > row["last_seen"]:
        return True
    if seen_at < row["last_seen"]:
        return False
    if STATUS_RANK.get(item["status"], 0) != STATUS_RANK.get(row["status"], 0):
        return STATUS_RANK.get(item["status"], 0) > STATUS_RANK.get(row["status"], 0)
    return CONFIDENCE_RANK.get(item["confidence"], 0) > CONFIDENCE_RANK.get(row["confidence"], 0)


if __name__ == "__main__":
    with Store() as store:
        print(json.dumps(store.stats(), indent=2))
        for topic in store.ranked_topics(limit=10):
            owner = topic["owner"] or "unowned"
            print(f"  {topic['status']:9} {topic['topic'][:40]:40} {owner}")
