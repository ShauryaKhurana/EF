"""Binding data contract — mirrors docs/schema.md. Every other module imports from here.

Design rule (CLAUDE.md): assume every field is missing, null, wrong-typed, or weirdly
formatted. Coerce what can be coerced and record a warning; hard-fail only when the
record is genuinely unusable. Never drop silently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

VALID_SOURCES = ("slack", "email", "ticket", "doc", "export")
VALID_KINDS = ("status", "blocker", "decision", "risk", "request", "fyi")
VALID_CONFIDENCE = ("high", "med", "low")
VALID_RESOLUTIONS = ("unresolved", "newer_wins")

_WS = re.compile(r"\s+")
# Slack ts: "1747250122.000200". Plain epoch: "1747250122" or "1747250122000" (ms).
_EPOCH_STR = re.compile(r"^-?\d+(\.\d+)?$")


def normalize_ws(s: str) -> str:
    """Collapse all whitespace runs to a single space and strip.

    Shared by extract.py's evidence-span substring check and anything else comparing
    text across a source and an LLM's copy of it. Both sides MUST use this function so
    the check can stay exact rather than fuzzy.
    """
    if not isinstance(s, str):
        s = str(s)
    return _WS.sub(" ", s).strip()


# --------------------------------------------------------------------------- time

def parse_ts(value: Any) -> Tuple[Optional[datetime], Optional[str]]:
    """Parse anything that has ever claimed to be a timestamp into tz-aware UTC.

    Returns (datetime|None, warning|None). Never raises. An unparseable timestamp is a
    warning carried on the record, NOT a reason to drop the record.

    Handles: datetime (naive or aware), epoch int/float, epoch-as-string including
    Slack's "1747250122.000200", epoch milliseconds, ISO-8601 with 'Z' or numeric
    offset, naive ISO strings (assumed UTC), and RFC-2822 email Date headers.
    """
    if value is None:
        return None, "ts is null"

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc), "ts was naive datetime; assumed UTC"
        return value.astimezone(timezone.utc), None

    if isinstance(value, bool):  # bool is an int subclass; catch before the number path
        return None, f"ts is a bool ({value!r})"

    if isinstance(value, (int, float)):
        return _from_epoch(value)

    if not isinstance(value, str):
        return None, f"ts has unsupported type {type(value).__name__} ({value!r})"

    raw = value.strip()
    if not raw:
        return None, "ts is an empty string"

    if _EPOCH_STR.match(raw):
        return _from_epoch(float(raw))

    iso = raw
    if iso.endswith(("Z", "z")):
        iso = iso[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        dt = None
    if dt is not None:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc), f"ts {raw!r} had no timezone; assumed UTC"
        return dt.astimezone(timezone.utc), None

    try:
        dt = parsedate_to_datetime(raw)  # RFC-2822, e.g. "Thu, 14 May 2026 13:55:22 -0700"
    except (TypeError, ValueError):
        dt = None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc), f"ts {raw!r} had no timezone; assumed UTC"
        return dt.astimezone(timezone.utc), None

    return None, f"ts {raw!r} is not a recognizable timestamp"


def _from_epoch(num: float) -> Tuple[Optional[datetime], Optional[str]]:
    warning = None
    # > 1e11 seconds is year 5138 — in practice that is milliseconds.
    if abs(num) > 1e11:
        num = num / 1000.0
        warning = "ts looked like epoch milliseconds; divided by 1000"
    try:
        return datetime.fromtimestamp(num, tz=timezone.utc), warning
    except (OverflowError, OSError, ValueError) as exc:
        return None, f"ts {num!r} is out of range for a date ({exc})"


def ts_to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else None


# ----------------------------------------------------------------------- artifact

@dataclass
class Artifact:
    """One normalized record from the corpus. See docs/schema.md."""
    artifact_id: str
    source: str
    container_id: Optional[str]
    parent_id: Optional[str]
    sender_id: Optional[str]
    recipients: List[str]
    ts: Optional[datetime]          # tz-aware UTC, or None when unparseable
    ts_raw: Any                     # exactly what the file said, for provenance
    text: str
    meta: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)   # in-memory only, never written

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "source": self.source,
            "container_id": self.container_id,
            "parent_id": self.parent_id,
            "sender_id": self.sender_id,
            "recipients": list(self.recipients),
            "ts": ts_to_iso(self.ts),
            "text": self.text,
            "meta": self.meta,
            "raw": self.raw,
        }


def _as_optional_str(value: Any, name: str, warnings: List[str]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        warnings.append(f"{name} was {type(value).__name__} {value!r}; coerced to string")
        return str(value)
    warnings.append(f"{name} had unsupported type {type(value).__name__} ({value!r}); coerced to string")
    return str(value)


def artifact_from_dict(
    record: Any,
    source_hint: Optional[str] = None,
) -> Tuple[Optional[Artifact], Optional[str]]:
    """Coerce one decoded JSON record into an Artifact.

    Returns (artifact, None) on success or (None, reason) when the record cannot be
    used at all. Hard failure is narrow on purpose: only a non-object, or a record with
    no artifact_id — without that id nothing can reference it (answer_key.gold_artifacts,
    evidence spans, hop paths all key on it), so it is unusable by definition.
    Everything else is coerced and recorded in artifact.warnings.
    """
    if not isinstance(record, dict):
        return None, f"record is {type(record).__name__}, not a JSON object"

    warnings: List[str] = []

    artifact_id = _as_optional_str(record.get("artifact_id"), "artifact_id", warnings)
    if not artifact_id:
        return None, "missing or empty artifact_id (record cannot be referenced or cited)"

    source = record.get("source")
    if source is None or (isinstance(source, str) and not source.strip()):
        if source_hint:
            source = source_hint
            warnings.append(f"source missing; inferred {source_hint!r} from filename")
        else:
            source = "unknown"
            warnings.append("source missing and no filename hint; set to 'unknown'")
    else:
        source = _as_optional_str(source, "source", warnings) or "unknown"
        if source not in VALID_SOURCES:
            warnings.append(
                f"source {source!r} is not one of {VALID_SOURCES}; kept verbatim"
            )

    ts, ts_warning = parse_ts(record.get("ts"))
    if ts_warning:
        warnings.append(ts_warning)

    text = record.get("text")
    if text is None:
        text = ""
        warnings.append("text missing or null; treated as empty")
    elif not isinstance(text, str):
        warnings.append(f"text was {type(text).__name__}; coerced to string")
        text = str(text)

    recipients_raw = record.get("recipients")
    recipients: List[str] = []
    if recipients_raw is None:
        pass  # [] is correct for public channels — silence detection depends on it
    elif isinstance(recipients_raw, str):
        warnings.append("recipients was a bare string; wrapped in a list")
        if recipients_raw.strip():
            recipients = [recipients_raw.strip()]
    elif isinstance(recipients_raw, (list, tuple)):
        for entry in recipients_raw:
            coerced = _as_optional_str(entry, "recipients entry", warnings)
            if coerced:
                recipients.append(coerced)
    else:
        warnings.append(
            f"recipients had unsupported type {type(recipients_raw).__name__}; treated as []"
        )

    meta = record.get("meta")
    if meta is None:
        meta = {}
    elif not isinstance(meta, dict):
        warnings.append(f"meta was {type(meta).__name__}, not an object; kept under meta['value']")
        meta = {"value": meta}

    raw = record.get("raw")
    if not isinstance(raw, dict):
        if raw is not None:
            warnings.append(f"raw was {type(raw).__name__}, not an object; kept under raw['value']")
            raw = {"value": raw}
        else:
            raw = dict(record)  # never lose provenance

    return Artifact(
        artifact_id=artifact_id,
        source=source,
        container_id=_as_optional_str(record.get("container_id"), "container_id", warnings),
        parent_id=_as_optional_str(record.get("parent_id"), "parent_id", warnings),
        sender_id=_as_optional_str(record.get("sender_id"), "sender_id", warnings),
        recipients=recipients,
        ts=ts,
        ts_raw=record.get("ts"),
        text=text,
        meta=meta,
        raw=raw,
        warnings=warnings,
    ), None


def validate_artifact(obj: Any) -> Tuple[bool, str]:
    """Strict post-hoc check. Coercion happens in artifact_from_dict; this reports."""
    if not isinstance(obj, Artifact):
        return False, f"not an Artifact (got {type(obj).__name__})"
    if not obj.artifact_id:
        return False, "artifact_id is empty"
    if obj.source not in VALID_SOURCES:
        return False, f"source {obj.source!r} not in {VALID_SOURCES}"
    if obj.ts is None:
        return False, f"ts unparseable (raw: {obj.ts_raw!r})"
    if not isinstance(obj.recipients, list):
        return False, "recipients is not a list"
    if not obj.text.strip():
        return False, "text is empty"
    return True, "ok"


# --------------------------------------------------------------------- statusitem

@dataclass
class Evidence:
    artifact_id: str
    span: str

    def to_dict(self) -> Dict[str, Any]:
        return {"artifact_id": self.artifact_id, "span": self.span}


@dataclass
class StatusItem:
    item_id: str
    kind: str
    project: Optional[str]
    subject: str
    claim: str
    evidence: List[Evidence]
    people: List[str]
    confidence: str
    ts: Optional[datetime]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "project": self.project,
            "subject": self.subject,
            "claim": self.claim,
            "evidence": [e.to_dict() for e in self.evidence],
            "people": list(self.people),
            "confidence": self.confidence,
            "ts": ts_to_iso(self.ts),
        }


def status_item_from_dict(record: Any) -> Tuple[Optional[StatusItem], Optional[str]]:
    """Coerce raw LLM JSON into a StatusItem. Never trust the shape.

    Hard-fails on anything that would let a claim reach the stage uncited: no evidence
    list, no artifact_id on an evidence entry, or an empty span. The *substring* check
    against the source text is extract.py's job — this only guarantees the shape.
    """
    if not isinstance(record, dict):
        return None, f"status item is {type(record).__name__}, not a JSON object"

    warnings: List[str] = []

    subject = record.get("subject")
    claim = record.get("claim")
    if not isinstance(claim, str) or not claim.strip():
        return None, "claim is missing or empty"
    if not isinstance(subject, str) or not subject.strip():
        return None, f"subject is missing or empty (claim: {claim[:60]!r})"

    evidence_raw = record.get("evidence")
    if not isinstance(evidence_raw, list) or not evidence_raw:
        return None, f"no evidence array on item about {subject!r} (that's a hallucination)"

    evidence: List[Evidence] = []
    for i, entry in enumerate(evidence_raw):
        if not isinstance(entry, dict):
            return None, f"evidence[{i}] is {type(entry).__name__}, not an object"
        art = entry.get("artifact_id")
        span = entry.get("span")
        if not isinstance(art, str) or not art.strip():
            return None, f"evidence[{i}] has no artifact_id"
        if not isinstance(span, str) or not span.strip():
            return None, f"evidence[{i}] has an empty span (artifact {art})"
        evidence.append(Evidence(artifact_id=art.strip(), span=span))

    kind = record.get("kind")
    if kind not in VALID_KINDS:
        warnings.append(f"kind {kind!r} not in {VALID_KINDS}")
        kind = "fyi"

    confidence = record.get("confidence")
    if confidence not in VALID_CONFIDENCE:
        warnings.append(f"confidence {confidence!r} not in {VALID_CONFIDENCE}")
        confidence = "low"

    people_raw = record.get("people")
    people: List[str] = []
    if isinstance(people_raw, str):
        people = [people_raw] if people_raw.strip() else []
    elif isinstance(people_raw, (list, tuple)):
        people = [str(p).strip() for p in people_raw if str(p).strip()]

    ts, _ = parse_ts(record.get("ts"))

    item_id = record.get("item_id")
    if not isinstance(item_id, str) or not item_id.strip():
        item_id = f"item_{evidence[0].artifact_id}_{abs(hash(claim)) % 10**6:06d}"

    project = record.get("project")
    if project is not None and not isinstance(project, str):
        project = str(project)

    item = StatusItem(
        item_id=item_id.strip(),
        kind=kind,
        project=project,
        subject=subject.strip(),
        claim=claim.strip(),
        evidence=evidence,
        people=people,
        confidence=confidence,
        ts=ts,
    )
    return item, None


def validate_status_item(obj: Any) -> Tuple[bool, str]:
    if not isinstance(obj, StatusItem):
        return False, f"not a StatusItem (got {type(obj).__name__})"
    if not obj.evidence:
        return False, "no evidence spans — every StatusItem needs >=1 verbatim quote"
    for i, e in enumerate(obj.evidence):
        if not e.artifact_id:
            return False, f"evidence[{i}] has no artifact_id"
        if not e.span.strip():
            return False, f"evidence[{i}] has an empty span"
    if obj.kind not in VALID_KINDS:
        return False, f"kind {obj.kind!r} not in {VALID_KINDS}"
    if obj.confidence not in VALID_CONFIDENCE:
        return False, f"confidence {obj.confidence!r} not in {VALID_CONFIDENCE}"
    if not obj.claim.strip():
        return False, "claim is empty"
    return True, "ok"


# ------------------------------------------------------------------------ conflict

@dataclass
class Position:
    claim: str
    item_ids: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {"claim": self.claim, "item_ids": list(self.item_ids)}


@dataclass
class Conflict:
    conflict_id: str
    subject: str
    positions: List[Position]
    resolution: str          # unresolved | newer_wins — never silently resolved
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "subject": self.subject,
            "positions": [p.to_dict() for p in self.positions],
            "resolution": self.resolution,
            "note": self.note,
        }


def validate_conflict(obj: Any) -> Tuple[bool, str]:
    if not isinstance(obj, Conflict):
        return False, f"not a Conflict (got {type(obj).__name__})"
    if len(obj.positions) < 2:
        return False, f"a conflict needs >=2 positions, got {len(obj.positions)}"
    for i, p in enumerate(obj.positions):
        if not p.item_ids:
            return False, f"positions[{i}] cites no item_ids"
    if obj.resolution not in VALID_RESOLUTIONS:
        return False, f"resolution {obj.resolution!r} not in {VALID_RESOLUTIONS}"
    if obj.resolution == "newer_wins" and not obj.note.strip():
        return False, "newer_wins requires a note explaining why (never resolve silently)"
    return True, "ok"


# -------------------------------------------------------------------------- answer

@dataclass
class Answer:
    """What the pipeline emits. abstained=True means we refused to guess."""
    question: str
    abstained: bool
    text: str
    items: List[StatusItem] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    who_would_know: List[str] = field(default_factory=list)   # canonical person ids
    hop_paths: Dict[str, List[str]] = field(default_factory=dict)  # artifact_id -> hops

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "abstained": self.abstained,
            "text": self.text,
            "items": [i.to_dict() for i in self.items],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "who_would_know": list(self.who_would_know),
            "hop_paths": self.hop_paths,
        }


def validate_answer(obj: Any) -> Tuple[bool, str]:
    if not isinstance(obj, Answer):
        return False, f"not an Answer (got {type(obj).__name__})"
    if not obj.text.strip():
        return False, "answer text is empty"
    if obj.abstained:
        if not obj.who_would_know:
            return False, "abstention must name who would know (looked up, never guessed)"
        return True, "ok"
    if not obj.items:
        return False, "non-abstaining answer cites no StatusItems"
    for item in obj.items:
        ok, reason = validate_status_item(item)
        if not ok:
            return False, f"item {getattr(item, 'item_id', '?')}: {reason}"
    return True, "ok"
