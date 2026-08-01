"""The COO agent: answers questions about company status from the stored context.

Retrieval-grounded — the model only sees topics pulled from the store, and is
instructed to say when it doesn't know rather than fill the gap. Every answer
carries the topics it drew on, so a claim can be traced back.
"""

import re
from dataclasses import dataclass, field

from google.genai import types

from src.llm import generate_content
from store import STATUS_RANK, Store

TOP_K = 8
RECENT_K = 5
MAX_HISTORY_TURNS = 8

SYSTEM_PROMPT = """You are the COO's operations agent. Employees ask you what's \
happening across the company; you answer from status data extracted from Slack and email.

You are given a CONTEXT block of status topics. Each has: topic, status \
(on_track / at_risk / blocked / unclear), owner, blocker, how many times it's been \
mentioned, and when it was last updated.

Rules:
- Answer ONLY from the CONTEXT. It is the entirety of what you know.
- If the context doesn't cover the question, say so plainly: "I don't have anything on \
that." Then say what you DO have that's adjacent, if anything. Never guess, never \
extrapolate a status, never invent an owner, a date, or a cause.
- Topics marked confidence "low" are unconfirmed readings. Say so when you use one.
- If a topic has no owner, say "no owner named" — do not guess who it might be.
- Lead with the direct answer in one sentence. Add detail after, only if it changes \
what the person would do next.
- Be concise and factual. No preamble, no sign-off, no offers of further help.
- When you reference a topic, use its exact name so the person can look it up.
- If asked about something time-sensitive, note when the topic was last updated \
— your data may be stale.

Citations:
- Topics carry evidence: verbatim quotes with the artifact id they came from and when \
that artifact was written, like [slk_0041] (2026-05-14T13:55:22+00:00). Every factual \
claim you make must end with the artifact id(s) it rests on, in square brackets.
- The evidence timestamp is when the thing actually happened; "last updated" is only \
when we ingested it. Answer date questions from the evidence timestamps.
- Cite only ids present in the CONTEXT. Never invent an id, never cite a topic name as \
though it were an id.
- If a topic you want to use has no evidence listed, you may still report it, but say \
"no source quote stored" instead of attaching a citation."""


@dataclass
class Answer:
    text: str
    topics: list[dict] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return [t["topic"] for t in self.topics]

    @property
    def cited_ids(self) -> list[str]:
        """Artifact ids the answer text actually cites, in order of first appearance.

        Handles both [slk_0088] and the grouped form [slk_0163, slk_0185] that the
        model uses when one claim rests on several artifacts.
        """
        seen: list[str] = []
        for group in re.findall(r"\[([^\[\]]+)\]", self.text):
            for token in re.split(r"[,;]\s*", group):
                token = token.strip()
                if re.fullmatch(r"[A-Za-z0-9_\-]+", token) and token not in seen:
                    seen.append(token)
        return seen

    @property
    def available_ids(self) -> list[str]:
        """Every artifact id the retrieved context offered."""
        ids: list[str] = []
        for topic in self.topics:
            for entry in topic.get("evidence") or []:
                artifact_id = entry.get("artifact_id")
                if artifact_id and artifact_id not in ids:
                    ids.append(artifact_id)
        return ids

    @property
    def uncited_claims(self) -> list[str]:
        """Ids cited in the text that the context never offered — fabricated citations.

        Checked in code rather than trusted: a made-up citation is worse than none,
        because it looks verified.
        """
        available = set(self.available_ids)
        return [i for i in self.cited_ids if i not in available]


def _render_context(topics: list[dict]) -> str:
    if not topics:
        return "(no matching topics in the store)"

    lines = []
    for topic in topics:
        parts = [
            f"- topic: {topic['topic']}",
            f"  status: {topic['status']}",
            f"  owner: {topic['owner'] or 'none named'}",
        ]
        if topic.get("blocker"):
            parts.append(f"  blocker: {topic['blocker']}")
        parts.append(f"  confidence: {topic['confidence']}")
        parts.append(f"  mentions: {topic.get('mentions', 1)}")
        parts.append(f"  last updated: {topic['last_seen']}")
        evidence = topic.get("evidence") or []
        if evidence:
            parts.append("  evidence:")
            for entry in evidence:
                span = " ".join(str(entry.get("span", "")).split())
                when = entry.get("ts")
                stamp = f" ({when})" if when else ""
                parts.append(f"    [{entry.get('artifact_id')}]{stamp} \"{span}\"")
        else:
            parts.append("  evidence: none stored")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


def gather_context(store: Store, question: str, k: int = TOP_K) -> list[dict]:
    """Semantic matches for the question, plus the most urgent topics regardless.

    The urgency floor matters: someone asking "anything I should know?" gets a
    useful answer even when nothing matches their wording semantically.
    """
    matched = store.search(question, k=k)
    seen = {t["topic_key"] for t in matched}

    for topic in store.ranked_topics(limit=RECENT_K):
        if topic["topic_key"] not in seen and STATUS_RANK.get(topic["status"], 0) >= 2:
            matched.append(topic)
            seen.add(topic["topic_key"])

    return matched


def _call_model(prompt: str, history: list[dict] | None = None) -> str:
    contents: list[types.Content] = []
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        contents.append(
            types.Content(role=turn["role"], parts=[types.Part(text=turn["text"])])
        )
    contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))

    response = generate_content(
        contents,
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
        raise RuntimeError(f"Model returned an empty answer ({reason}).")
    return text


def ask(question: str, store: Store, history: list[dict] | None = None, k: int = TOP_K) -> Answer:
    """Answer a question about company status, grounded in the store."""
    question = question.strip()
    if not question:
        return Answer("Ask me something about what's going on.", [])

    if store.topic_count() == 0:
        return Answer(
            "The store is empty — no status data has been ingested yet. "
            "Run `python orchestrator.py --dry-run` first.",
            [],
        )

    topics = gather_context(store, question, k=k)
    prompt = (
        f"CONTEXT ({len(topics)} topic(s) retrieved from the company status store):\n\n"
        f"{_render_context(topics)}\n\n"
        f"QUESTION: {question}"
    )
    return Answer(_call_model(prompt, history=history), topics)


if __name__ == "__main__":
    import sys

    question = " ".join(sys.argv[1:]) or "What's blocked right now?"
    with Store() as store:
        answer = ask(question, store)
        print(f"\n{answer.text}\n")
        if answer.sources:
            print(f"  topics: {', '.join(answer.sources)}")
        if answer.cited_ids:
            print(f"  cited artifacts: {', '.join(answer.cited_ids)}")
        if answer.uncited_claims:
            print(
                f"  WARNING fabricated citation(s) not in the retrieved context: "
                f"{', '.join(answer.uncited_claims)}"
            )
