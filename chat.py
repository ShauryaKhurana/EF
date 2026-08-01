"""Chat with the COO agent.

    python chat.py                      # interactive
    python chat.py "what's blocked?"    # one-shot

Commands inside the REPL:
    /topics [status]   list tracked topics, optionally filtered (blocked, at_risk, ...)
    /topic <name>      show one topic's full history
    /stats             what's in the store
    /reindex           re-embed topics for semantic search
    /clear             forget the conversation history
    /help              show commands
    /quit              exit
"""

import argparse
import sys

from dotenv import load_dotenv

from agent import ask
from store import STATUS_RANK, Store, topic_key

load_dotenv()

COLOR = sys.stdout.isatty()
BOLD = "\033[1m" if COLOR else ""
DIM = "\033[2m" if COLOR else ""
RESET = "\033[0m" if COLOR else ""
CYAN = "\033[36m" if COLOR else ""
RED = "\033[31m" if COLOR else ""
YELLOW = "\033[33m" if COLOR else ""
GREEN = "\033[32m" if COLOR else ""
BLUE = "\033[34m" if COLOR else ""

STATUS_COLOR = {"blocked": RED, "at_risk": YELLOW, "unclear": BLUE, "on_track": GREEN}

BANNER = f"""{BOLD}COO Agent{RESET} {DIM}— ask me what's going on{RESET}
{DIM}/topics  /topic <name>  /stats  /reindex  /clear  /help  /quit{RESET}"""


def show_topics(store: Store, status: str | None = None) -> None:
    topics = store.ranked_topics()
    if status:
        topics = [t for t in topics if t["status"] == status]
    if not topics:
        print(f"  {DIM}no topics{' with status ' + status if status else ''}{RESET}")
        return

    for topic in topics:
        color = STATUS_COLOR.get(topic["status"], "")
        owner = topic["owner"] or f"{DIM}unowned{RESET}"
        flag = f" {YELLOW}(unconfirmed){RESET}" if topic["confidence"] == "low" else ""
        print(f"  {color}{topic['status']:9}{RESET} {topic['topic'][:44]:44} {owner}{flag}")
    print(f"\n  {DIM}{len(topics)} topic(s){RESET}")


def show_topic(store: Store, name: str) -> None:
    key = topic_key(name)
    matches = [t for t in store.topics() if t["topic_key"] == key]
    if not matches:
        matches = [t for t in store.topics() if name.lower() in t["topic"].lower()]
    if not matches:
        print(f"  {DIM}no topic matching {name!r}{RESET}")
        return

    for topic in matches[:3]:
        color = STATUS_COLOR.get(topic["status"], "")
        print(f"\n  {BOLD}{topic['topic']}{RESET}")
        print(f"    status      {color}{topic['status']}{RESET}")
        print(f"    owner       {topic['owner'] or '— none named —'}")
        if topic["blocker"]:
            print(f"    blocker     {topic['blocker']}")
        print(f"    confidence  {topic['confidence']}")
        print(f"    mentions    {topic['mentions']}")
        print(f"    first seen  {topic['first_seen']}")
        print(f"    last seen   {topic['last_seen']}")

        history = store.history(topic["topic_key"], limit=6)
        if len(history) > 1:
            print(f"    {DIM}history:{RESET}")
            for row in history:
                print(f"      {DIM}{row['seen_at'][:19]}  {row['status']:9} "
                      f"{row['owner'] or '—'}{RESET}")


def show_stats(store: Store) -> None:
    stats = store.stats()
    print(f"  messages  {stats['messages']}")
    print(f"  topics    {stats['topics']}  ({stats['indexed']} embedded for search)")
    for status in sorted(stats["by_status"], key=lambda s: -STATUS_RANK.get(s, 0)):
        color = STATUS_COLOR.get(status, "")
        print(f"    {color}{status:9}{RESET} {stats['by_status'][status]}")


def handle_command(line: str, store: Store, history: list[dict]) -> bool:
    """Handle a /command. Returns False if the REPL should exit."""
    parts = line.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command in ("/quit", "/exit", "/q"):
        return False
    if command == "/help":
        print(__doc__.split("Commands inside the REPL:")[1])
    elif command == "/topics":
        show_topics(store, argument or None)
    elif command == "/topic":
        if argument:
            show_topic(store, argument)
        else:
            print(f"  {DIM}usage: /topic <name>{RESET}")
    elif command == "/stats":
        show_stats(store)
    elif command == "/reindex":
        count = store.reindex(force=True)
        print(f"  re-embedded {count} topic(s)")
    elif command == "/clear":
        history.clear()
        print(f"  {DIM}conversation history cleared{RESET}")
    else:
        print(f"  {DIM}unknown command {command} — try /help{RESET}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chat with the COO agent.")
    parser.add_argument("question", nargs="*", help="Ask one question and exit.")
    parser.add_argument("--db", default=None, help="Path to the store database.")
    args = parser.parse_args(argv)

    store = Store(args.db) if args.db else Store()
    history: list[dict] = []

    try:
        if args.question:
            answer = ask(" ".join(args.question), store)
            print(f"\n{answer.text}\n")
            if answer.sources:
                print(f"{DIM}sources: {', '.join(answer.sources)}{RESET}")
            return 0

        print(f"\n{BANNER}\n")
        stats = store.stats()
        print(f"{DIM}{stats['topics']} topics from {stats['messages']} messages"
              f"{' — run /reindex for semantic search' if not stats['indexed'] else ''}{RESET}\n")

        while True:
            try:
                line = input(f"{CYAN}you ▸{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line:
                continue
            if line.startswith("/"):
                if not handle_command(line, store, history):
                    break
                print()
                continue

            try:
                answer = ask(line, store, history=history)
            except Exception as e:  # noqa: BLE001 - keep the REPL alive
                print(f"  {RED}error:{RESET} {type(e).__name__}: {e}\n")
                continue

            print(f"\n{BOLD}coo ▸{RESET} {answer.text}\n")
            if answer.sources:
                print(f"{DIM}      sources: {', '.join(answer.sources[:5])}{RESET}\n")

            history.append({"role": "user", "text": line})
            history.append({"role": "model", "text": answer.text})
    finally:
        store.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
