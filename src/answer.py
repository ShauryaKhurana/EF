"""StatusItems + conflicts -> a cited answer, or a real abstention.

Ported from agent.py (root pipeline): the system prompt, citation rules, and the
fabricated-citation check (uncited_claims) are already right there and carry over
almost verbatim. What's new here: two input modes, and abstention backed by
src.world.who_would_know() — a real ownership lookup, never an LLM guess.

Two modes, chosen by the caller (pipeline.py) based on what stages actually ran:
  - extraction ran: answer from StatusItems (kind/confidence/evidence as extracted).
  - extraction didn't run (src/extract.py not built yet, or it dropped everything):
    answer from the raw retrieved artifacts directly. Each artifact is wrapped as a
    StatusItem whose claim is a verbatim excerpt of its own text and whose evidence
    span is that same excerpt — so it is not a fabricated extraction, just raw text
    carried through in the same citable shape, and it satisfies the same
    evidence-must-be-a-substring guarantee for free.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from google.genai import types

from src.llm import generate_content
from src.schema import (
    Answer,
    Evidence,
    StatusItem,
    normalize_ws,
    parse_ts,
)
from src.world import World

MAX_RAW_CLAIM_CHARS = 280

SYSTEM_PROMPT = """You are the COO's operations agent. Employees ask you what's \
happening across the company; you answer from status items extracted from Slack, \
email, and tickets.

You are given a CONTEXT block of status items. Each has: kind (status/blocker/\
decision/risk/request/fyi), project, subject, claim, confidence (high/med/low), \
people involved, and verbatim evidence quotes with the artifact id they came from.

Rules:
- Answer ONLY from the CONTEXT. It is the entirety of what you know.
- If ANY item in the CONTEXT bears on the question — even indirectly, even low-\
confidence, even if it doesn't name the exact cause — answer from it directly, citing \
it, and say what's uncertain. Do NOT preface a real, citable answer with "I don't have \
anything on that" just because no item is a perfect or literal match.
- Reserve "I don't have anything on that." for the one case where the CONTEXT contains \
NOTHING relevant to the question at all. In that exact case, say only that sentence, \
then optionally name adjacent items with citations. This phrase is a signal the caller \
checks in code to detect abstention — using it any other way misfires that check.
- Never guess, never extrapolate a status, never invent an owner, a date, or a cause.
- Items marked confidence "low" are unconfirmed or unprocessed readings — say so when \
you use one.
- Lead with the direct answer in one sentence. Add detail after, only if it changes \
what the person would do next.
- Be concise and factual. No preamble, no sign-off, no offers of further help.

Citations:
- Every factual claim you make must end with the artifact id(s) it rests on, in square \
brackets, like [slk_0041] or [slk_0163, slk_0185] when one claim rests on several.
- Cite only ids present in the CONTEXT. Never invent an id.
- If conflicting positions are present in the CONTEXT for the same subject, report \
both positions with their sources — never silently pick one."""


def _artifact_as_item(artifact: Dict[str, Any]) -> StatusItem:
    """Wrap one raw retrieved artifact as a low-confidence StatusItem.

    Used only when extraction hasn't run. The claim is a verbatim slice of the
    artifact's own text, so the evidence span is trivially a real substring — this
    carries raw data through the same citable contract, it does not extract anything.
    """
    text = normalize_ws(artifact.get("text") or "")
    claim = text[:MAX_RAW_CLAIM_CHARS] or "(empty message)"
    ts, _ = parse_ts(artifact.get("ts"))
    people = [p for p in ([artifact.get("sender_id")] + list(artifact.get("recipients") or [])) if p]
    return StatusItem(
        item_id=f"raw_{artifact['artifact_id']}",
        kind="fyi",
        project=None,
        subject=artifact.get("container_id") or artifact.get("source") or "unknown",
        claim=claim,
        evidence=[Evidence(artifact_id=artifact["artifact_id"], span=claim)],
        people=people,
        confidence="low",
        ts=ts,
    )


def _render_items(items: List[StatusItem]) -> str:
    if not items:
        return "(no matching status items)"
    lines = []
    for item in items:
        parts = [
            f"- kind: {item.kind}",
            f"  project: {item.project or 'none'}",
            f"  subject: {item.subject}",
            f"  claim: {item.claim}",
            f"  confidence: {item.confidence}",
        ]
        if item.people:
            parts.append(f"  people: {', '.join(item.people)}")
        parts.append("  evidence:")
        for e in item.evidence:
            when = f" ({item.ts.isoformat()})" if item.ts else ""
            parts.append(f"    [{e.artifact_id}]{when} \"{e.span}\"")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


def _cited_ids(text: str) -> List[str]:
    """Artifact ids the answer text actually cites, in first-appearance order.

    Handles both [slk_0088] and the grouped [slk_0163, slk_0185] form.
    """
    seen: List[str] = []
    for group in re.findall(r"\[([^\[\]]+)\]", text):
        for token in re.split(r"[,;]\s*", group):
            token = token.strip()
            if re.fullmatch(r"[A-Za-z0-9_\-]+", token) and token not in seen:
                seen.append(token)
    return seen


def _call_model(prompt: str) -> str:
    response = generate_content(
        [types.Content(role="user", parts=[types.Part(text=prompt)])],
        types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=2048,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        ),
    )
    text = (response.text or "").strip()
    if not text:
        reason = "unknown"
        candidates = getattr(response, "candidates", None)
        if candidates:
            reason = f"finish_reason={getattr(candidates[0], 'finish_reason', None)}"
        raise RuntimeError(f"model returned an empty answer ({reason})")
    return text


def answer_question(
    question: str,
    candidates: List[Dict[str, Any]],
    world: World,
    *,
    items: Optional[List[StatusItem]] = None,
    hop_paths: Optional[Dict[str, List[str]]] = None,
    min_confidence_ok: bool = True,
) -> Answer:
    """Produce a cited Answer, or a real abstention.

    candidates: retrieved artifact dicts (from Retriever.retrieve), used for
    abstention lookups and as the raw-item fallback.
    items: extracted StatusItems, if src/extract.py ran and produced any. None means
    the stage hasn't run (or dropped everything) — fall back to raw artifacts.
    """
    question = question.strip()
    hop_paths = hop_paths or {}

    if not candidates and not items:
        who = world.who_would_know([question])
        return Answer(
            question=question,
            abstained=True,
            text="I don't have anything on that in the corpus.",
            who_would_know=[e["employee_id"] for e in who] or ["EMP_001"],
        )

    used_items = items if items else [_artifact_as_item(c["artifact"]) for c in candidates]
    degraded = items is None

    prompt = (
        f"CONTEXT ({len(used_items)} status item(s){' — raw retrieved text, extraction stage not run' if degraded else ''}):\n\n"
        f"{_render_items(used_items)}\n\n"
        f"QUESTION: {question}"
    )
    text = _call_model(prompt)

    available_ids = {e.artifact_id for item in used_items for e in item.evidence}
    cited = _cited_ids(text)
    uncited = [i for i in cited if i not in available_ids]
    if uncited:
        text += (
            f"\n\n[WARNING: fabricated citation(s) not present in retrieved context: "
            f"{', '.join(uncited)} — dropped from evidence trust, verify manually]"
        )

    # The system prompt explicitly tells the model to lead with "I don't have anything
    # on that" and THEN name adjacent context with citations — so citations alone don't
    # mean it answered. Abstention is signaled by how the answer opens, not by whether
    # it cites anything.
    abstained = text.strip().lower().startswith("i don't have anything")
    who_would_know: List[str] = []
    if abstained:
        subjects = [item.subject for item in used_items] or [question]
        who = world.who_would_know(subjects + [question])
        who_would_know = [e["employee_id"] for e in who]
        if not who_would_know:
            # No owner resolves from world.json for this subject. Still name a real,
            # looked-up person rather than leaving the abstention dangling — VP Eng is
            # the actual root of world.json's manager chain (manager_id: null), so this
            # is a real escalation path, not a guess.
            vp = next((e for e in world.employees.values() if e.get("manager_id") is None), None)
            who_would_know = [vp["employee_id"]] if vp else []

    return Answer(
        question=question,
        abstained=abstained,
        text=text,
        items=used_items if not abstained else [],
        who_would_know=who_would_know,
        hop_paths=hop_paths,
    )
