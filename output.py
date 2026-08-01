import os
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def post_to_slack(channel: str, text: str) -> None:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN is required to post to Slack.")

    client = WebClient(token=token)
    try:
        client.chat_postMessage(channel=channel, text=text)
    except SlackApiError as err:
        raise RuntimeError(f"Slack API error: {err.response['error']}") from err


def write_to_google_doc(title: str, content: str, document_id: Optional[str] = None) -> str:
    # Placeholder: implement Google Docs creation/update via google-api-python-client
    if document_id:
        return document_id
    return "new-google-doc-id"


def output_briefing(briefing: str, slack_channel: str, google_doc_id: Optional[str] = None) -> str:
    post_to_slack(slack_channel, briefing)
    return write_to_google_doc("COO Briefing", briefing, document_id=google_doc_id)


if __name__ == "__main__":
    briefing = "Sample COO briefing text."
    print("Posting briefing to Slack and writing to Google Doc...")
    output_briefing(briefing, "#general")
