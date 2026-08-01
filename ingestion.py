import os
from typing import List, Dict, Any

from schema import StatusItem


def fetch_slack_messages() -> List[Dict[str, Any]]:
    """Pull recent messages from Slack using the Slack Web API."""
    # Placeholder: implement Slack API message retrieval using slack_sdk.WebClient
    return []


def fetch_gmail_messages() -> List[Dict[str, Any]]:
    """Pull recent email messages from Gmail using the Gmail API."""
    # Placeholder: implement Gmail API retrieval using google-api-python-client
    return []


def normalize_messages(raw_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize Slack and email data into a common message schema."""
    normalized = []
    for message in raw_messages:
        normalized.append({
            "source": message.get("source", "unknown"),
            "sender": message.get("sender", "unknown"),
            "timestamp": message.get("timestamp", ""),
            "text": message.get("text", message.get("body", "")),
        })
    return normalized


def ingest() -> List[Dict[str, Any]]:
    slack_messages = fetch_slack_messages()
    email_messages = fetch_gmail_messages()
    raw_messages = slack_messages + email_messages
    return normalize_messages(raw_messages)


if __name__ == "__main__":
    messages = ingest()
    print(f"Ingested {len(messages)} normalized messages.")
