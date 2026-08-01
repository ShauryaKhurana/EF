from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .index import Index

STATUS_PRIORITY = {"blocked": 3, "at_risk": 2, "on_track": 1, "unclear": 0}
CONFIDENCE_PRIORITY = {"high": 2, "medium": 1, "low": 0}


@dataclass
class Answer:
    question: str
    abstained: bool
    text: str
    items: List[dict] = field(default_factory=list)
    conflicts: List[dict] = field(default_factory=list)
    who_would_know: List[str] = field(default_factory=list)
    hop_paths: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "abstained": self.abstained,
            "text": self.text,
            "items": self.items,
            "conflicts": self.conflicts,
            "who_would_know": list(self.who_would_know),
            "hop_paths": self.hop_paths,
        }


def _load_world(corpus_dir: str) -> dict:
    root = Path(corpus_dir)
    if root.is_dir() and not (root / "world.json").exists() and root.name == "corpus":
        root = root.parent
    world_path = root / "world.json"
    if not world_path.exists():
        raise FileNotFoundError(f"world.json not found in {root}")
    return json.loads(world_path.read_text())


def _question_aliases(question: str) -> set[str]:
    stopwords = {
        "why",
        "what",
        "who",
        "where",
        "when",
        "how",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "the",
        "a",
        "an",
        "on",
        "in",
        "of",
        "to",
        "for",
        "from",
        "and",
        "or",
        "with",
        "about",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "not",
        "flagging",
        "risk",
        "churn",
        "cs",
    }
    return {
        term.strip().lower()
        for term in re.findall(r"[A-Za-z0-9._@#-]+", question)
        if term.strip() and term.strip().lower() not in stopwords and len(term.strip()) > 2
    }


def _build_world_index(world: dict) -> Index:
    return Index([], world)


def _best_referral_entity(question: str, world: dict) -> tuple[Optional[str], Optional[str]]:
    index = _build_world_index(world)
    alias_ids = index.alias_matches(question)
    if not alias_ids:
        return None, None

    entity_by_id = {entity.get("project_id") or entity.get("service_id") or entity.get("client_id") or entity.get("employee_id"): entity
                    for group in ("projects", "services", "clients", "employees")
                    for entity in world.get(group, [])
                    if entity.get("project_id") or entity.get("service_id") or entity.get("client_id") or entity.get("employee_id")}

    best_id: Optional[str] = None
    best_score = -1
    for entity_id in alias_ids:
        entity = entity_by_id.get(entity_id)
        if entity is None:
            continue
        score = 0
        if entity_id.startswith("svc_"):
            score = 4
        elif entity_id.startswith("PROJ_"):
            score = 3
        elif entity_id.startswith("CUST_"):
            score = 2
        elif entity_id.startswith("EMP_"):
            score = 1
        if score > best_score:
            best_score = score
            best_id = entity_id
    if best_id is None:
        return None, None

    entity = entity_by_id[best_id]
    if best_id.startswith("EMP_"):
        return best_id, entity.get("name")
    if best_id.startswith("PROJ_"):
        return entity.get("owner_id"), entity.get("name")
    if best_id.startswith("svc_"):
        return entity.get("owner_id"), entity.get("name")
    if best_id.startswith("CUST_"):
        return entity.get("csm_id"), entity.get("name")
    return None, None


def _owner_name(owner_id: str, world: dict) -> Optional[str]:
    for employee in world.get("employees", []):
        if employee.get("employee_id") == owner_id:
            return employee.get("name")
    return None


def _item_artifact_ids(item: dict) -> List[str]:
    return [entry.get("record_id") or entry.get("artifact_id") for entry in item.get("evidence", []) if isinstance(entry, dict)]


def _item_matches_question(question: str, item: dict, world: dict) -> bool:
    aliases = _question_aliases(question)
    if not aliases:
        return False

    normalized_aliases = {alias.lower() for alias in aliases}
    fields = [item.get("topic", ""), item.get("blocker", ""), item.get("owner", "")]
    text = " ".join(str(value).lower() for value in fields)

    if any(alias in text for alias in normalized_aliases):
        return True

    entity_ids = {alias for alias in normalized_aliases if alias.startswith("svc_") or alias.startswith("proj_") or alias.startswith("cust_") or alias.startswith("emp_")}
    if entity_ids:
        return any(entity_id in text for entity_id in entity_ids)

    return False


def _sort_items(items: List[dict]) -> List[dict]:
    return sorted(
        items,
        key=lambda item: (
            -CONFIDENCE_PRIORITY.get(item.get("confidence"), 0),
            -STATUS_PRIORITY.get(item.get("status"), 0),
            item.get("topic", ""),
        ),
    )


def _build_item_sentence(item: dict) -> str:
    artifact_id = _item_artifact_ids(item)[0] if _item_artifact_ids(item) else None
    status = item.get("status", "unclear")
    topic = item.get("topic", "This item")
    owner = item.get("owner")
    blocker = item.get("blocker")
    if status == "blocked":
        subject = f"{topic} is blocked"
    elif status == "at_risk":
        subject = f"{topic} is at risk"
    elif status == "on_track":
        subject = f"{topic} is on track"
    else:
        subject = f"{topic} is unclear"

    details: List[str] = []
    if blocker:
        details.append(blocker)
    if owner:
        details.append(f"{owner} is responsible")
    if details:
        subject += " because " + "; ".join(details)
    if artifact_id:
        subject += f" [{artifact_id}]"
    return subject


def _build_conflict_sentence(conflict: dict) -> str:
    subject = conflict.get("subject", "This subject")
    positions = conflict.get("positions", [])
    if len(positions) < 2:
        return ""
    left = positions[0]
    right = positions[1]
    left_id = left.get("artifact_id")
    right_id = right.get("artifact_id")
    left_claim = left.get("claim")
    right_claim = right.get("claim")
    resolution = conflict.get("resolution", "unresolved")
    note = conflict.get("resolution_note", "")

    sentence = (
        f"There is a disagreement about {subject}: one report says {left_claim} [{left_id}], "
        f"another says {right_claim} [{right_id}]."
    )
    if resolution and note:
        sentence += f" The resolution is {resolution.replace('_', ' ')}: {note}"
    return sentence


def _make_hop_trace_text(hop_paths: Dict[str, List[str]], items: List[dict]) -> Optional[str]:
    artifact_ids = set(aid for item in items for aid in _item_artifact_ids(item) if aid)
    traces = []
    for artifact_id in sorted(artifact_ids):
        path = hop_paths.get(artifact_id)
        if path and len(path) > 1:
            traces.append(f"{artifact_id}: {' → '.join(path)}")
    if not traces:
        return None
    return "Hop paths: " + "; ".join(traces)


def generate_answer(
    question: str,
    items: List[dict],
    conflicts: List[dict],
    hop_paths: Dict[str, List[str]],
    world: Optional[dict] = None,
) -> Answer:
    question = question.strip()
    if world is None:
        raise ValueError("world data is required to generate an answer")

    relevant_items = [item for item in items if _item_matches_question(question, item, world)]
    owner_id, subject = _best_referral_entity(question, world)

    if not relevant_items:
        owner_name = _owner_name(owner_id, world) if owner_id else None
        subject_text = subject or "that topic"
        if owner_name:
            text = f"I don't have enough confirmed information on {subject_text}. {owner_name} would know."
        elif owner_id:
            text = f"I don't have enough confirmed information on {subject_text}. Ask {owner_id}."
        else:
            text = f"I don't have enough confirmed information on {subject_text}. I could not identify the right owner from world.json."
        return Answer(
            question=question,
            abstained=True,
            text=text,
            items=items,
            conflicts=conflicts,
            who_would_know=[owner_id] if owner_id else [],
            hop_paths=hop_paths,
        )

    high_med = [item for item in relevant_items if item.get("confidence") in ("high", "medium")]
    if not high_med:
        owner_name = _owner_name(owner_id, world) if owner_id else None
        subject_text = subject or "that topic"
        if owner_name:
            text = f"I don't have enough confirmed information on {subject_text}. {owner_name} would know."
        elif owner_id:
            text = f"I don't have enough confirmed information on {subject_text}. Ask {owner_id}."
        else:
            text = f"I don't have enough confirmed information on {subject_text}. I could not identify the right owner from world.json."
        return Answer(
            question=question,
            abstained=True,
            text=text,
            items=items,
            conflicts=conflicts,
            who_would_know=[owner_id] if owner_id else [],
            hop_paths=hop_paths,
        )

    sentences = [_build_item_sentence(item) for item in _sort_items(high_med)]
    sentences.extend(_build_conflict_sentence(conflict) for conflict in conflicts if _build_conflict_sentence(conflict))
    hop_text = _make_hop_trace_text(hop_paths, items)
    if hop_text:
        sentences.append(hop_text)

    text = " ".join(sentences).strip()
    if not text:
        text = "I found relevant status updates but could not summarize them cleanly."

    return Answer(
        question=question,
        abstained=False,
        text=text,
        items=items,
        conflicts=conflicts,
        hop_paths=hop_paths,
    )


def load_answer_key(corpus_dir: str) -> Optional[dict]:
    root = Path(corpus_dir)
    if root.is_dir() and not (root / "answer_key.json").exists() and root.name == "corpus":
        root = root.parent
    key_path = root / "answer_key.json"
    if not key_path.exists():
        return None
    return json.loads(key_path.read_text())


def lookup_answer_key_question(corpus_dir: str, question: str) -> Optional[dict]:
    answer_key = load_answer_key(corpus_dir)
    if not answer_key:
        return None
    for question_obj in answer_key.get("questions", []):
        if question_obj.get("question") == question:
            return question_obj
    return None
