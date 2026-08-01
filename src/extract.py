"""Extract structured status items from retrieved bootstrap artifacts.

This module is intended for Agent 4: it consumes retrieval candidates from
`src.retrieve.Retriever` and produces StatusItems grounded in exact evidence
spans pulled from artifact text. Every evidence span is validated in Python
against the referenced artifact text after whitespace normalization.

Gemini is used as the LLM backend via `google-genai`.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any

from dotenv import load_dotenv
from google.genai import types

from src.llm import generate_content

load_dotenv()

BATCH_SIZE = 10
MAX_WORKERS = 4

STATUSES = ("on_track", "at_risk", "blocked", "unclear")
CONFIDENCES = ("high", "medium", "low")
FIELDS = ("source", "topic", "status", "owner", "blocker", "confidence", "evidence")


@dataclass
class Evidence:
    record_id: str
    span: str


@dataclass
class StatusItem:
    source: str
    topic: str
    status: str
    owner: str | None
    blocker: str | None
    confidence: str
    evidence: list[Evidence]


class ExtractionError(RuntimeError):
    pass


class QuotaExhausted(RuntimeError):
    pass




RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "topic": {"type": "string"},
                    "status": {"type": "string", "enum": list(STATUSES)},
                    "owner": {"type": ["string", "null"]},
                    "blocker": {"type": ["string", "null"]},
                    "confidence": {"type": "string", "enum": list(CONFIDENCES)},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "record_id": {"type": "string"},
                                "span": {"type": "string"},
                            },
                            "required": ["record_id", "span"],
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


SYSTEM_PROMPT = """You are extracting structured status updates from retrieved artifacts.

Each artifact is a real message or ticket note. Only emit items for actual
status signals: projects, blockers, owners, and risk. Do not hallucinate
owners, blockers, or evidence spans.

Return exactly one JSON object with a single top-level key `items` whose value
is an array of extracted status records.

Each status record must have these fields:
- source: the source of the artifact supplying the evidence.
- topic: a short project or service name, title case, no trailing punctuation.
- status: one of on_track, at_risk, blocked, unclear.
- owner: the named person accountable for the work, or null.
- blocker: the blocker or risk statement, or null if there is none.
- confidence: one of high, medium, low.
- evidence: an array of objects with `record_id` and `span`.

Each evidence span must be copied verbatim from the referenced artifact's text.
Do not invent evidence. Use only the artifact text that is listed in the prompt.
If an artifact has no status update, omit it entirely.
"""


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _format_candidates(candidates: list[dict]) -> str:
    if not isinstance(candidates, list):
        raise TypeError(f"candidates must be a list, got {type(candidates).__name__}")

    parts: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        artifact = candidate.get("artifact")
        if not artifact or "artifact_id" not in artifact or "text" not in artifact:
            raise ValueError("each candidate must include an artifact with artifact_id and text")
        fields = [
            f"artifact_id={artifact['artifact_id']}",
            f"source={artifact.get('source', '')}",
            f"container_id={artifact.get('container_id', '')}",
            f"parent_id={artifact.get('parent_id', '')}",
            f"sender_id={artifact.get('sender_id', '')}",
            f"ts={artifact.get('ts', '')}",
        ]
        parts.append(
            f"[{index}] {' | '.join(fields)}\n{artifact['text']}"
        )
    return "\n\n".join(parts)


def _response_text(response: Any) -> str:
    text = (response.text or "").strip()
    if text:
        return text
    candidates = getattr(response, "candidates", None)
    reason = "unknown"
    if candidates:
        reason = f"finish_reason={getattr(candidates[0], 'finish_reason', None)}"
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None:
        reason += f", prompt_feedback={feedback}"
    raise ExtractionError(f"Model returned no text ({reason}).")


def _parse_json(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractionError(
            f"Model output was not valid JSON ({e}). First 300 chars:\n{raw[:300]}"
        ) from e


def _validate_item(obj: Any, index: int, artifacts_by_id: dict[str, dict]) -> StatusItem:
    where = f"items[{index}]"
    if not isinstance(obj, dict):
        raise ExtractionError(f"{where}: expected an object, got {type(obj).__name__}")

    missing = [f for f in FIELDS if f not in obj]
    if missing:
        raise ExtractionError(f"{where}: missing field(s): {', '.join(missing)}")
    extra = [k for k in obj if k not in FIELDS]
    if extra:
        raise ExtractionError(f"{where}: unexpected field(s): {', '.join(extra)}")

    if not isinstance(obj["source"], str) or not obj["source"].strip():
        raise ExtractionError(f"{where}.source: expected a non-empty string")
    if not isinstance(obj["topic"], str) or not obj["topic"].strip():
        raise ExtractionError(f"{where}.topic: expected a non-empty string")
    if obj["status"] not in STATUSES:
        raise ExtractionError(f"{where}.status: invalid value {obj['status']!r}")
    if obj["confidence"] not in CONFIDENCES:
        raise ExtractionError(f"{where}.confidence: invalid value {obj['confidence']!r}")

    for field in ("owner", "blocker"):
        value = obj[field]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ExtractionError(f"{where}.{field}: expected a non-empty string or null")

    if not isinstance(obj["evidence"], list) or not obj["evidence"]:
        raise ExtractionError(f"{where}.evidence: expected a non-empty list")

    evidence: list[Evidence] = []
    for ev_index, raw_ev in enumerate(obj["evidence"]):
        ev_where = f"{where}.evidence[{ev_index}]"
        if not isinstance(raw_ev, dict):
            raise ExtractionError(f"{ev_where}: expected object, got {type(raw_ev).__name__}")
        if "record_id" not in raw_ev or "span" not in raw_ev:
            raise ExtractionError(f"{ev_where}: missing record_id or span")
        if not isinstance(raw_ev["record_id"], str) or not raw_ev["record_id"].strip():
            raise ExtractionError(f"{ev_where}.record_id: expected non-empty string")
        if not isinstance(raw_ev["span"], str) or not raw_ev["span"].strip():
            raise ExtractionError(f"{ev_where}.span: expected non-empty string")
        evidence.append(Evidence(record_id=raw_ev["record_id"].strip(), span=raw_ev["span"].strip()))

    item = StatusItem(
        source=obj["source"].strip(),
        topic=obj["topic"].strip(),
        status=obj["status"],
        owner=obj["owner"].strip() if isinstance(obj["owner"], str) else None,
        blocker=obj["blocker"].strip() if isinstance(obj["blocker"], str) else None,
        confidence=obj["confidence"],
        evidence=evidence,
    )

    for evidence_entry in item.evidence:
        artifact = artifacts_by_id.get(evidence_entry.record_id)
        if artifact is None:
            raise ExtractionError(
                f"{where}.evidence: unknown record_id {evidence_entry.record_id!r}"
            )
        normalized_text = _normalize_whitespace(artifact.get("text", ""))
        normalized_span = _normalize_whitespace(evidence_entry.span)
        if normalized_span not in normalized_text:
            raise ExtractionError(
                f"{where}.evidence: span not found in {evidence_entry.record_id!r}"
            )

    return item


def _validate_payload(payload: Any, artifacts_by_id: dict[str, dict]) -> list[StatusItem]:
    if not isinstance(payload, dict):
        raise ExtractionError(f"Expected a JSON object with an 'items' key, got {type(payload).__name__}")
    if "items" not in payload:
        raise ExtractionError(f"Response object has no 'items' key (keys: {list(payload)})")
    if not isinstance(payload["items"], list):
        raise ExtractionError(f"'items' must be a list, got {type(payload['items']).__name__}")

    items: list[StatusItem] = []
    for i, raw_item in enumerate(payload["items"]):
        items.append(_validate_item(raw_item, i, artifacts_by_id))
    return items


def _call_model(contents: str) -> str:
    response = generate_content(
        contents,
        types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_json_schema=RESPONSE_SCHEMA,
            max_output_tokens=8192,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        ),
    )
    return _response_text(response)


def _extract_chunk(candidates: list[dict]) -> list[dict]:
    artifacts_by_id = {candidate["artifact"]["artifact_id"]: candidate["artifact"] for candidate in candidates}
    prompt = (
        f"Extract status items from these {len(candidates)} artifact(s).\n\n"
        f"{_format_candidates(candidates)}"
    )
    raw = _call_model(prompt)
    items = _validate_payload(_parse_json(raw), artifacts_by_id)
    return [asdict(item) for item in items]


def extract_candidates(
    candidates: list[dict],
    batch_size: int = BATCH_SIZE,
    max_workers: int = MAX_WORKERS,
    progress: bool = False,
    return_stats: bool = False,
) -> list[dict] | tuple[list[dict], dict[str, Any]]:
    if not candidates:
        if return_stats:
            return [], {"chunks": 0, "failed_chunks": 0, "failed_details": []}
        return []
    if len(candidates) <= batch_size:
        items = _extract_chunk(candidates)
        if return_stats:
            return items, {"chunks": 1, "failed_chunks": 0, "failed_details": []}
        return items

    batches = [candidates[i : i + batch_size] for i in range(0, len(candidates), batch_size)]
    extracted: list[dict] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_extract_chunk, batch): idx for idx, batch in enumerate(batches)}
        for done in as_completed(futures):
            idx = futures[done]
            try:
                extracted.extend(done.result())
            except Exception as exc:  # noqa: BLE001
                failures.append(f"chunk {idx + 1}: {type(exc).__name__}: {exc}")
            if progress:
                print(f"  extracted chunk {idx + 1}/{len(batches)}")

    if failures:
        print(f"[extract] {len(failures)}/{len(batches)} chunk(s) failed:")
        for failure in failures:
            print(f"  - {failure}")
        if len(failures) == len(batches):
            raise ExtractionError(f"every chunk failed; first error: {failures[0]}")

    if return_stats:
        return extracted, {"chunks": len(batches), "failed_chunks": len(failures), "failed_details": failures}
    return extracted


if __name__ == "__main__":
    print("src.extract is a library module; import extract_candidates() from it.")
