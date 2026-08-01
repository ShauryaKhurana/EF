import os
from typing import List, Dict, Any

from schema import StatusItem

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


def build_extraction_prompt(messages: List[Dict[str, Any]]) -> str:
    prompt = (
        "Extract structured status updates from the following messages. "
        "Return a JSON list of items with sender, source, timestamp, category, summary, detail, and action_items.\n\n"
    )

    for message in messages:
        prompt += f"Source: {message['source']}\n"
        prompt += f"Sender: {message['sender']}\n"
        prompt += f"Timestamp: {message['timestamp']}\n"
        prompt += f"Message: {message['text']}\n\n"

    prompt += "Structured JSON output:"
    return prompt


def call_llm(prompt: str) -> str:
    if ANTHROPIC_API_KEY:
        # Placeholder for an Anthropics-compatible call
        return "[]"
    if GEMINI_API_KEY:
        # Placeholder for a Gemini-compatible call
        return "[]"
    raise RuntimeError("No LLM API key found. Set ANTHROPIC_API_KEY or GEMINI_API_KEY.")


def extract_status_items(messages: List[Dict[str, Any]]) -> List[StatusItem]:
    prompt = build_extraction_prompt(messages)
    response = call_llm(prompt)

    # Placeholder: parse response into StatusItem objects
    return []


if __name__ == "__main__":
    from ingestion import ingest

    normalized_messages = ingest()
    items = extract_status_items(normalized_messages)
    print(f"Extracted {len(items)} status items.")
