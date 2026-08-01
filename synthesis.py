"""Synthesize extracted StatusItems into a company-wide briefing for Slack.

Input is the list of StatusItem dicts produced by extraction.extract().
Output is Slack mrkdwn ready to post.
"""

import json
import os
from collections import Counter
from datetime import date

from dotenv import load_dotenv
from google import genai
from google.genai import types

from extraction import CONFIDENCES, STATUSES, generate_content

load_dotenv()

# Order sections by how much they need a human's attention.
STATUS_ORDER = ("blocked", "at_risk", "unclear", "on_track")
STATUS_LABELS = {
    "blocked": "Blocked",
    "at_risk": "At risk",
    "unclear": "Needs follow-up",
    "on_track": "On track",
}


class SynthesisError(RuntimeError):
    """Raised when the briefing can't be generated."""


SYSTEM_PROMPT = f"""You write a short daily operations briefing for a COO, posted to Slack.

You receive structured status items already extracted from Slack and email. Each has:
source, topic, status ({", ".join(STATUSES)}), owner (may be null),
blocker (may be null), and confidence ({", ".join(CONFIDENCES)}).

Write the briefing in Slack mrkdwn:
- *bold* uses single asterisks, NOT double. Never use markdown headers (#).
- Use "•" for bullets.

Structure:
1. One opening sentence stating the overall picture and the single most important thing
   the COO should know today.
2. A section per status group that has items, in this order: *Blocked*, *At risk*,
   *Needs follow-up*, *On track*. Skip any group with no items entirely.
3. One bullet per item: the topic in bold, then what's happening, then the owner in
   parentheses if one is known. For blocked and at-risk items, name the blocker.
4. Close with a "*Suggested focus*" line: at most two concrete things to chase today,
   drawn only from the blocked and at-risk items.

Hard rules:
- Use ONLY the information in the status items. Never invent owners, dates, systems,
  numbers, or causes. If a field is null, say so plainly ("no owner named") or omit it.
- Items with confidence "low" must be hedged — say the status is unconfirmed and that
  it needs a follow-up. Never present a low-confidence item as settled fact.
- If owner is null, do NOT guess who owns it.
- Keep the whole briefing under 220 words. Be direct; no filler, no preamble, no
  sign-off, no "let me know if you need anything"."""


def _group_by_status(status_items: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {status: [] for status in STATUS_ORDER}
    for item in status_items:
        grouped.setdefault(item["status"], []).append(item)
    return {status: items for status, items in grouped.items() if items}


def build_synthesis_prompt(status_items: list[dict]) -> str:
    """Render the status items as the user-turn prompt for the briefing."""
    counts = Counter(item["status"] for item in status_items)
    tally = ", ".join(f"{count} {status}" for status, count in counts.most_common())

    return (
        f"Date: {date.today().isoformat()}\n"
        f"{len(status_items)} status item(s): {tally}.\n\n"
        f"Status items as JSON:\n{json.dumps(status_items, indent=2)}\n\n"
        "Write the briefing."
    )


def _call_model(prompt: str) -> str:
    """Send the synthesis request to Gemini. Isolated so tests can stub it."""
    response = generate_content(
        prompt,
        types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=2048,
            # Writing prose benefits from a bit more deliberation than extraction does.
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MEDIUM),
        ),
    )

    text = (response.text or "").strip()
    if not text:
        reason = "unknown"
        candidates = getattr(response, "candidates", None)
        if candidates:
            reason = f"finish_reason={getattr(candidates[0], 'finish_reason', None)}"
        raise SynthesisError(f"Model returned an empty briefing ({reason}).")
    return text


def fallback_briefing(status_items: list[dict]) -> str:
    """Deterministic briefing, no LLM. Used when there's nothing to synthesize."""
    if not status_items:
        return (
            f"*COO Briefing — {date.today().isoformat()}*\n\n"
            "No status updates found in today's messages. "
            "Either nothing was reported, or the sources had no project content."
        )

    lines = [f"*COO Briefing — {date.today().isoformat()}*", ""]
    for status, items in _group_by_status(status_items).items():
        lines.append(f"*{STATUS_LABELS.get(status, status)}*")
        for item in items:
            owner = f" ({item['owner']})" if item["owner"] else " (no owner named)"
            blocker = f" — {item['blocker']}" if item["blocker"] else ""
            hedge = " _[low confidence, needs confirmation]_" if item["confidence"] == "low" else ""
            lines.append(f"• *{item['topic']}*{blocker}{owner}{hedge}")
        lines.append("")
    return "\n".join(lines).strip()


# A briefing listing 60 projects is as useless as no briefing. Past this many items
# we brief the most urgent ones and roll the rest into a one-line tail.
MAX_BRIEFED_ITEMS = 12

STATUS_URGENCY = {"blocked": 3, "at_risk": 2, "unclear": 1, "on_track": 0}
CONFIDENCE_URGENCY = {"high": 2, "medium": 1, "low": 0}


def rank_items(status_items: list[dict]) -> list[dict]:
    """Most urgent first, then most confident — what a COO should read top-down."""
    return sorted(
        status_items,
        key=lambda i: (
            STATUS_URGENCY.get(i["status"], 0),
            CONFIDENCE_URGENCY.get(i["confidence"], 0),
        ),
        reverse=True,
    )


def _tail_summary(remainder: list[dict]) -> str:
    """One line accounting for everything that didn't make the shortlist."""
    counts = Counter(item["status"] for item in remainder)
    parts = [
        f"{counts[status]} {STATUS_LABELS.get(status, status).lower()}"
        for status in STATUS_ORDER
        if counts.get(status)
    ]
    return f"\n\n_Plus {len(remainder)} more not detailed above: {', '.join(parts)}._"


def synthesize_briefing(status_items: list[dict], max_items: int = MAX_BRIEFED_ITEMS) -> str:
    """Turn status items into a Slack-ready briefing.

    Falls back to a deterministic summary when there are no items, so the pipeline
    never spends an API call on an empty briefing. Above `max_items`, only the most
    urgent are briefed in full and the rest are rolled into a counted tail line, so
    the briefing's length stays roughly constant no matter how much goes in.
    """
    if not status_items:
        return fallback_briefing(status_items)

    ranked = rank_items(status_items)
    briefed, remainder = ranked[:max_items], ranked[max_items:]

    briefing = _call_model(build_synthesis_prompt(briefed))
    if remainder:
        briefing += _tail_summary(remainder)
    return briefing


if __name__ == "__main__":
    from extraction import SAMPLE_MESSAGES, extract

    items = extract(SAMPLE_MESSAGES)
    print(synthesize_briefing(items))
