"""Deduplicate StatusItems and detect conflicts between opposing claims.

This module is responsible for Agent 4 cross-reference logic: merge duplicate
status reads on the same subject, surface conflict records when items make
opposing claims, and label newer-wins resolutions or unresolved disagreements.
"""

import re
from dataclasses import asdict, dataclass
from typing import Any

STATUS_ORDER = {"blocked": 3, "at_risk": 2, "unclear": 1, "on_track": 0}


@dataclass
class ConflictPosition:
    artifact_id: str
    claim: str
    source: str
    owner: str | None
    status: str


@dataclass
class Conflict:
    subject: str
    positions: list[ConflictPosition]
    resolution: str
    resolution_note: str
    winning_artifact: str | None


class CrossrefError(RuntimeError):
    pass


def _normalize_subject(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", topic.lower()).strip()


def _normalize_claim(claim: str) -> str:
    return re.sub(r"\s+", " ", claim.lower().strip())


def _subject_key(item: dict) -> str:
    if not isinstance(item.get("topic"), str):
        raise CrossrefError("StatusItem missing topic")
    return _normalize_subject(item["topic"])


def _claim_key(item: dict) -> str:
    if not isinstance(item.get("blocker"), str) or not item["blocker"].strip():
        return ""
    return _normalize_claim(item["blocker"])


def dedupe_status_items(items: list[dict]) -> list[dict]:
    subjects: dict[str, dict] = {}

    for item in items:
        subject = _subject_key(item)
        if subject not in subjects:
            subjects[subject] = dict(item)
            continue

        current = subjects[subject]
        current_rank = (STATUS_ORDER.get(current["status"], 0), 
                        1 if current.get("confidence") == "high" else 0)
        incoming_rank = (STATUS_ORDER.get(item["status"], 0), 
                         1 if item.get("confidence") == "high" else 0)

        if incoming_rank > current_rank:
            winner, loser = item, current
        else:
            winner, loser = current, item

        winner = dict(winner)
        winner["owner"] = winner["owner"] or loser.get("owner")
        winner["blocker"] = winner["blocker"] or loser.get("blocker")
        winner["evidence"] = winner.get("evidence", []) + loser.get("evidence", [])
        subjects[subject] = winner

    return list(subjects.values())


def _claim_similarity(a: str, b: str) -> float:
    a_norm = _normalize_claim(a)
    b_norm = _normalize_claim(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0
    a_tokens = set(a_norm.split())
    b_tokens = set(b_norm.split())
    if not a_tokens or not b_tokens:
        return 0.0
    overlap = len(a_tokens & b_tokens)
    return overlap / max(len(a_tokens), len(b_tokens))


def _is_opposing_claim(a: dict, b: dict) -> bool:
    claim_a = a.get("blocker") or ""
    claim_b = b.get("blocker") or ""
    if not claim_a or not claim_b:
        return False
    if claim_a == claim_b:
        return False
    similarity = _claim_similarity(claim_a, claim_b)
    if similarity > 0.7:
        return False
    return True


def _subject_similarity(a: dict, b: dict) -> bool:
    return _subject_key(a) == _subject_key(b)


def _evidence_artifact_id(entry: Any) -> str | None:
    if isinstance(entry, dict):
        return entry.get("record_id")
    return getattr(entry, "record_id", None)


def _choose_resolution(a: dict, b: dict) -> tuple[str, str, str | None]:
    if a.get("source") == b.get("source"):
        winner = b if b.get("confidence") == "high" and a.get("confidence") != "high" else a
        winner = b if a.get("status") == "blocked" and b.get("status") != "blocked" else winner
        loser = a if winner is b else b
    else:
        winner = b if b.get("confidence") == "high" and a.get("confidence") != "high" else a
        loser = a if winner is b else b

    artifact_id = None
    if winner.get("evidence"):
        last_entry = winner["evidence"][-1]
        artifact_id = _evidence_artifact_id(last_entry)

    return (
        "newer_wins",
        "Newer or higher-confidence evidence takes precedence when two claims disagree.",
        artifact_id,
    )


def detect_conflicts(items: list[dict]) -> list[dict]:
    conflicts: list[dict] = []
    visited: set[tuple[str, str]] = set()

    for i, a in enumerate(items):
        for j, b in enumerate(items[i + 1 :], start=i + 1):
            if not _subject_similarity(a, b):
                continue
            if not _is_opposing_claim(a, b):
                continue

            a_key = (_subject_key(a), _normalize_claim(a.get("blocker", "")))
            b_key = (_subject_key(b), _normalize_claim(b.get("blocker", "")))
            if a_key == b_key or (b_key, a_key) in visited:
                continue

            visited.add((a_key, b_key))
            resolution, resolution_note, winning_artifact = _choose_resolution(a, b)
            positions = [
                ConflictPosition(
                    artifact_id=_evidence_artifact_id(a["evidence"][0]) or "",
                    claim=a.get("blocker") or "",
                    source=a.get("source"),
                    owner=a.get("owner"),
                    status=a.get("status"),
                ),
                ConflictPosition(
                    artifact_id=_evidence_artifact_id(b["evidence"][0]) or "",
                    claim=b.get("blocker") or "",
                    source=b.get("source"),
                    owner=b.get("owner"),
                    status=b.get("status"),
                ),
            ]
            conflict = Conflict(
                subject=a.get("topic", ""),
                positions=positions,
                resolution=resolution,
                resolution_note=resolution_note,
                winning_artifact=winning_artifact,
            )
            conflicts.append(asdict(conflict))
    return conflicts


if __name__ == "__main__":
    print("src.crossref is a library module; import dedupe_status_items() and detect_conflicts().")
