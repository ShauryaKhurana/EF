"""Agent 4 orchestration: retrieval → extraction → cross-reference.

This module ties the bootstrap retrieval stack to the Agent 4 extractor and
conflict detector. It is intentionally minimal: retrieve candidate artifacts,
extract status items from them, dedupe on subject/claim, and surface conflict
records when two items disagree.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .crossref import dedupe_status_items, detect_conflicts
from .extract import extract_candidates
from .index import Index
from .retrieve import Retriever


def run_agent4(
    question: str,
    corpus_dir: str,
    hops: int = 3,
    top_k: int = 50,
    batch_size: int = 10,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Run Agent 4 over a bootstrap corpus for one question.

    Args:
        question: natural-language query to retrieve candidate artifacts.
        corpus_dir: path containing data/corpus and data/world.json.
        hops: number of retrieval hops to expand beyond the initial seeds.
        top_k: maximum number of artifacts to retrieve.
        batch_size: inference batch size for extraction.
        max_workers: parallel extraction worker count.

    Returns:
        A dict containing the requested question, retrieved candidates,
        extracted status items, and detected conflicts.
    """
    index = Index.from_path(Path(corpus_dir))
    retriever = Retriever(index)
    candidates = retriever.retrieve(question, hops=hops, top_k=top_k)

    items = extract_candidates(candidates, batch_size=batch_size, max_workers=max_workers)
    items = dedupe_status_items(items)
    conflicts = detect_conflicts(items)

    return {
        "question": question,
        "candidates": candidates,
        "items": items,
        "conflicts": conflicts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Agent 4 over the bootstrap corpus.")
    parser.add_argument("--question", required=True, help="Question to retrieve and extract for.")
    parser.add_argument("--corpus", default="data", help="Path containing data/corpus and data/world.json.")
    parser.add_argument("--hops", type=int, default=3, help="Number of retrieval hops.")
    parser.add_argument("--top-k", type=int, default=50, help="Max number of artifacts to retrieve.")
    parser.add_argument("--batch-size", type=int, default=10, help="Extraction batch size.")
    parser.add_argument("--max-workers", type=int, default=4, help="Parallel extraction workers.")
    args = parser.parse_args(argv)

    result = run_agent4(
        question=args.question,
        corpus_dir=args.corpus,
        hops=args.hops,
        top_k=args.top_k,
        batch_size=args.batch_size,
        max_workers=args.max_workers,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
