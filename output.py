"""Deliver the finished briefing.

Posts to Slack when a bot token and channel are available; otherwise prints to the
console so the pipeline is runnable end to end without credentials.
"""

import os

from dotenv import load_dotenv

load_dotenv()


class OutputError(RuntimeError):
    """Raised when the briefing can't be delivered."""


def post_to_slack(channel: str, text: str) -> str:
    """Post the briefing to a Slack channel. Returns the message timestamp.

    NOTE: never exercised against a live workspace — no token was available when
    this was written. Verify before relying on it.
    """
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise OutputError("SLACK_BOT_TOKEN is required to post to Slack.")

    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    client = WebClient(token=token)
    try:
        response = client.chat_postMessage(channel=channel, text=text, mrkdwn=True)
    except SlackApiError as err:
        raise OutputError(f"Slack API error: {err.response['error']}") from err
    return response["ts"]


def print_to_console(briefing: str) -> str:
    """Render the briefing to stdout. Always available, never fails."""
    rule = "=" * 72
    print(f"\n{rule}\nCOOLESS BRIEFING\n{rule}\n\n{briefing}\n\n{rule}\n")
    return "console"


def write_to_google_doc(title: str, content: str, document_id: str | None = None) -> str:
    """Write the briefing to a Google Doc. Not implemented."""
    raise NotImplementedError(
        "Google Docs output is not implemented. The briefing is delivered to Slack "
        "and/or the console."
    )


def deliver(briefing: str, channel: str | None = None, dry_run: bool = False) -> str:
    """Deliver the briefing and return a destination marker.

    Prints to the console when dry_run is set, when no channel is given, or when
    SLACK_BOT_TOKEN is missing — so this never silently no-ops.
    """
    if dry_run:
        return print_to_console(briefing)

    if not channel:
        print("[output] No channel given — printing instead of posting to Slack.")
        return print_to_console(briefing)

    if not os.environ.get("SLACK_BOT_TOKEN"):
        print("[output] SLACK_BOT_TOKEN not set — printing instead of posting to Slack.")
        return print_to_console(briefing)

    ts = post_to_slack(channel, briefing)
    print(f"[output] Posted to Slack channel {channel} (ts={ts}).")
    return ts


if __name__ == "__main__":
    deliver("*COOless Briefing — sample*\n\n• *Billing migration* — blocked on legal (Marcus)", dry_run=True)
