import os
from typing import List

from schema import StatusItem

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


def build_synthesis_prompt(status_items: List[StatusItem]) -> str:
    prompt = (
        "You are a COO assistant. Synthesize the following structured status updates into a concise, executive-level company briefing. "
        "Include key progress highlights, risks, and next steps.\n\n"
    )
    for item in status_items:
        prompt += f"Sender: {item.sender}\n"
        prompt += f"Source: {item.source}\n"
        prompt += f"Timestamp: {item.timestamp}\n"
        prompt += f"Category: {item.category}\n"
        prompt += f"Summary: {item.summary}\n"
        prompt += f"Detail: {item.detail}\n"
        prompt += f"Action items: {item.action_items}\n\n"
    prompt += "Briefing:"
    return prompt


def call_llm(prompt: str) -> str:
    if ANTHROPIC_API_KEY:
        return ""
    if GEMINI_API_KEY:
        return ""
    raise RuntimeError("No LLM API key found. Set ANTHROPIC_API_KEY or GEMINI_API_KEY.")


def synthesize_briefing(status_items: List[StatusItem]) -> str:
    prompt = build_synthesis_prompt(status_items)
    return call_llm(prompt)


if __name__ == "__main__":
    from extraction import extract_status_items
    from ingestion import ingest

    items = extract_status_items(ingest())
    briefing = synthesize_briefing(items)
    print(briefing)
