"""Staged, presentable walkthrough of the COO Agent pipeline.

    python demo.py                     # live run, briefing printed to the console
    python demo.py --channel '#ops'    # also post the briefing to Slack
    python demo.py --slow              # pause between stages (for a live audience)

Pulls Slack messages from sample_data/messages.json and email from
sample_data/gmail_raw.json, so both ingestion paths are exercised in one run.
"""

import argparse
import sys
import time

from dotenv import load_dotenv

from extraction import extract_batch
from ingestion import DEFAULT_FIXTURES, GMAIL_FIXTURES, load_fixture_messages
from output import deliver
from store import Store
from synthesis import STATUS_LABELS, synthesize_briefing

load_dotenv()

COLOR = sys.stdout.isatty()
BOLD = "\033[1m" if COLOR else ""
DIM = "\033[2m" if COLOR else ""
RESET = "\033[0m" if COLOR else ""
RED = "\033[31m" if COLOR else ""
YELLOW = "\033[33m" if COLOR else ""
GREEN = "\033[32m" if COLOR else ""
BLUE = "\033[34m" if COLOR else ""

STATUS_COLOR = {"blocked": RED, "at_risk": YELLOW, "unclear": BLUE, "on_track": GREEN}
WIDTH = 78


def rule(char: str = "─") -> None:
    print(DIM + char * WIDTH + RESET)


def stage(number: int, title: str) -> None:
    print(f"\n{BOLD}[{number}/5] {title}{RESET}")
    rule()


def pause(slow: bool) -> None:
    if slow:
        input(f"{DIM}   ↵ continue{RESET}")


def load_demo_messages() -> list[dict]:
    """Slack from the normalized fixture, email via the raw Gmail adapter."""
    slack = [m for m in load_fixture_messages(DEFAULT_FIXTURES) if m["source"] == "slack"]
    email = load_fixture_messages(GMAIL_FIXTURES)
    return slack + email


def show_inputs(messages: list[dict]) -> None:
    for i, message in enumerate(messages, start=1):
        first_line = message["text"].replace("\n", " ").strip()
        if first_line.startswith("Subject: "):
            first_line = first_line[len("Subject: "):]
        preview = first_line[:52] + ("…" if len(first_line) > 52 else "")
        tag = f"{BLUE}slack{RESET}" if message["source"] == "slack" else f"{YELLOW}email{RESET}"
        print(f"  {DIM}{i:2}{RESET}  {tag}  {message['sender'][:22]:22}  {preview}")


def show_items(items: list[dict]) -> None:
    if not items:
        print(f"  {DIM}(no status content found){RESET}")
        return
    for item in items:
        color = STATUS_COLOR.get(item["status"], "")
        label = STATUS_LABELS.get(item["status"], item["status"]).upper()
        print(f"\n  {color}{BOLD}{label:16}{RESET}{BOLD}{item['topic']}{RESET}")
        print(f"    owner      {item['owner'] or DIM + '— none named —' + RESET}")
        if item["blocker"]:
            print(f"    blocker    {item['blocker']}")
        conf = item["confidence"]
        flag = f"  {YELLOW}← unconfirmed, needs follow-up{RESET}" if conf == "low" else ""
        print(f"    confidence {conf}{flag}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a staged COO Agent demo.")
    parser.add_argument("--channel", default=None, help="Slack channel to post the briefing to.")
    parser.add_argument("--slow", action="store_true", help="Pause between stages.")
    args = parser.parse_args(argv)

    print(f"\n{BOLD}COO AGENT{RESET} {DIM}— Slack + email in, executive briefing out{RESET}")
    rule("═")

    try:
        # 1 ─ Ingest
        stage(1, "Ingesting raw messages")
        t0 = time.perf_counter()
        messages = load_demo_messages()
        slack_n = sum(1 for m in messages if m["source"] == "slack")
        email_n = len(messages) - slack_n
        show_inputs(messages)
        print(
            f"\n  {DIM}{len(messages)} messages "
            f"({slack_n} Slack, {email_n} email parsed from raw Gmail API payloads) "
            f"in {time.perf_counter() - t0:.2f}s{RESET}"
        )
        pause(args.slow)

        # 2 ─ Extract
        stage(2, "Extracting structured status items (Gemini)")
        t0 = time.perf_counter()
        items = extract_batch(messages)
        elapsed = time.perf_counter() - t0
        show_items(items)
        print(
            f"\n  {DIM}{len(items)} status items from {len(messages)} messages "
            f"in {elapsed:.1f}s — off-topic chatter skipped, and messages about the "
            f"same project merged. Nothing was invented to fill the gaps.{RESET}"
        )
        pause(args.slow)

        # 3 ─ Persist, so the chat agent has company context afterwards
        stage(3, "Updating the company store")
        with Store() as store:
            store.add_messages(messages)
            merge = store.upsert_items(items)
            indexed = store.reindex()
            tracked = store.topic_count()
        print(
            f"  {merge['created']} new topic(s), {merge['updated']} updated, "
            f"{indexed} embedded for search"
        )
        print(f"  {DIM}{tracked} topics tracked across all runs{RESET}")
        pause(args.slow)

        # 4 ─ Synthesize
        stage(4, "Synthesizing the briefing (Gemini)")
        t0 = time.perf_counter()
        briefing = synthesize_briefing(items)
        print(
            f"  {DIM}{len(briefing.split())} words in {time.perf_counter() - t0:.1f}s{RESET}"
        )
        pause(args.slow)

        # 5 ─ Deliver
        stage(5, "Delivering")
        destination = deliver(briefing, channel=args.channel, dry_run=args.channel is None)

    except Exception as e:
        print(f"\n{RED}Demo failed:{RESET} {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    rule("═")
    print(
        f"{BOLD}Done.{RESET} {len(messages)} messages → {len(items)} status items → "
        f"1 briefing → {destination}"
    )
    print(f"{DIM}Now ask it anything:  python chat.py{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
