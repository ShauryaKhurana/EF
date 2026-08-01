"""COOless pipeline: ingest -> extract -> synthesize -> deliver.

Examples:
    python orchestrator.py --dry-run
    python orchestrator.py --fixtures sample_data/messages.json --dry-run
    python orchestrator.py --channel '#ops-briefing'
    python orchestrator.py --source slack --channel C0123456789
"""

import argparse
import sys

from extraction import extract_batch
from ingestion import DEFAULT_CORPUS, DEFAULT_FIXTURES, ingest
from output import deliver
from store import Store
from synthesis import synthesize_briefing


def run_pipeline(
    source: str = "corpus",
    fixtures_path: str = str(DEFAULT_FIXTURES),
    corpus_dir: str = str(DEFAULT_CORPUS),
    channel: str | None = None,
    dry_run: bool = False,
    verbose: bool = True,
    persist: bool = True,
    db_path: str | None = None,
    incremental: bool = True,
) -> dict:
    """Run the full pipeline. Returns a summary dict of what happened at each stage.

    With persist=True the run accumulates into the store, so the chat agent has
    company context across runs. incremental=True skips messages already stored,
    which keeps repeat runs cheap on large corpora.
    """

    def log(message: str) -> None:
        if verbose:
            print(message)

    store = Store(db_path) if db_path else (Store() if persist else None)

    try:
        log(f"[1/5] Ingesting from {source}...")
        messages = ingest(
            source=source,
            fixtures_path=fixtures_path,
            channel=channel,
            corpus_dir=corpus_dir,
        )
        log(f"      {len(messages)} message(s) ingested.")

        if store and incremental:
            fresh = store.new_messages(messages)
            if len(fresh) != len(messages):
                log(f"      {len(messages) - len(fresh)} already seen; "
                    f"extracting {len(fresh)} new message(s).")
            messages_to_extract = fresh
        else:
            messages_to_extract = messages

        log("[2/5] Extracting status items...")
        status_items = extract_batch(messages_to_extract) if messages_to_extract else []
        log(f"      {len(status_items)} status item(s) from {len(messages_to_extract)} message(s).")

        log("[3/5] Updating the company store...")
        if store:
            store.add_messages(messages)
            merge = store.upsert_items(status_items)
            indexed = store.reindex()
            log(f"      {merge['created']} new topic(s), {merge['updated']} updated, "
                f"{indexed} re-embedded. {store.topic_count()} topic(s) tracked.")
            briefing_items = store.status_items()
        else:
            log("      (skipped — persistence disabled)")
            briefing_items = status_items

        log("[4/5] Synthesizing briefing...")
        briefing = synthesize_briefing(briefing_items)
        log(f"      {len(briefing.split())} word briefing generated.")

        log("[5/5] Delivering...")
        destination = deliver(briefing, channel=channel, dry_run=dry_run)

        return {
            "messages": len(messages),
            "extracted": len(messages_to_extract),
            "status_items": len(status_items),
            "topics": store.topic_count() if store else len(status_items),
            "briefing": briefing,
            "destination": destination,
        }
    finally:
        if store:
            store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the COOless briefing pipeline.")
    parser.add_argument(
        "--source",
        choices=("corpus", "fixtures", "gmail", "slack"),
        default="corpus",
        help=(
            "Where to read messages from (default: corpus). "
            "'corpus' reads data/corpus/*.jsonl and carries artifact_id through to "
            "the evidence spans; 'gmail' reads a JSON dump of raw Gmail API messages."
        ),
    )
    parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS),
        help="Directory of .jsonl artifacts, used when --source=corpus.",
    )
    parser.add_argument(
        "--fixtures",
        default=str(DEFAULT_FIXTURES),
        help="Path to a JSON file of messages, used when --source=fixtures.",
    )
    parser.add_argument(
        "--channel",
        default=None,
        help="Slack channel to post the briefing to (and to read from when --source=slack).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the briefing instead of posting it to Slack.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Don't write to the company store (one-shot briefing only).",
    )
    parser.add_argument("--db", default=None, help="Path to the store database.")
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Re-extract messages already in the store instead of skipping them.",
    )
    args = parser.parse_args(argv)

    try:
        result = run_pipeline(
            source=args.source,
            fixtures_path=args.fixtures,
            corpus_dir=args.corpus,
            channel=args.channel,
            dry_run=args.dry_run,
            persist=not args.no_persist,
            db_path=args.db,
            incremental=not args.reprocess,
        )
    except Exception as e:
        print(f"\nPipeline failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(
        f"Pipeline complete: {result['messages']} message(s) -> "
        f"{result['status_items']} status item(s) -> {result['topics']} tracked topic(s) "
        f"-> {result['destination']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
