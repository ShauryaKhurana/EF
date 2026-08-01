from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .agent4 import run_agent4
from .answer import generate_answer
from .llm import report as llm_report


def _resolve_corpus_root(corpus_dir: str) -> Path:
    root = Path(corpus_dir)
    if root.is_dir() and not (root / "world.json").exists() and root.name == "corpus":
        root = root.parent
    return root


def _load_world(corpus_dir: str) -> dict[str, Any]:
    root = _resolve_corpus_root(corpus_dir)
    world_path = root / "world.json"
    if not world_path.exists():
        raise FileNotFoundError(f"world.json not found in {root}")
    return json.loads(world_path.read_text())


def _load_answer_key(corpus_dir: str) -> dict[str, Any] | None:
    root = _resolve_corpus_root(corpus_dir)
    key_path = root / "answer_key.json"
    if not key_path.exists():
        return None
    return json.loads(key_path.read_text())


def _compute_recall(question: str, candidates: list[dict], answer_key: dict[str, Any] | None) -> str:
    if not answer_key:
        return "n/a"

    question_obj = next((q for q in answer_key.get("questions", []) if q.get("question") == question), None)
    if not question_obj:
        return "n/a"

    gold = set(question_obj.get("gold_artifacts", []))
    if not gold:
        return "n/a"

    retrieved = {candidate.get("artifact_id") for candidate in candidates}
    found = len(gold & retrieved)
    return f"{found}/{len(gold)} ({found / len(gold):.2f})"


def _hop_count(candidates: list[dict]) -> int:
    if not candidates:
        return 0
    return max(len(candidate.get("path", [])) - 1 for candidate in candidates)


def run_pipeline(
    question: str,
    corpus_dir: str = "data",
    out_path: str = "out/answer.md",
    hops: int = 3,
    top_k: int = 50,
    batch_size: int = 10,
    max_workers: int = 4,
    only_source: str | None = None,
) -> dict[str, Any]:
    start = time.monotonic()
    world = _load_world(corpus_dir)
    answer_key = _load_answer_key(corpus_dir)

    print(f"[1/4] Retrieving candidates from {corpus_dir}...")
    result = run_agent4(
        question=question,
        corpus_dir=corpus_dir,
        hops=hops,
        top_k=top_k,
        batch_size=batch_size,
        max_workers=max_workers,
        only_source=only_source,
    )

    recall = _compute_recall(question, result["candidates"], answer_key)
    hop_count = _hop_count(result["candidates"])
    print(f"      retrieved {len(result['candidates'])} artifact(s), recall={recall}, max hops={hop_count}")

    print(f"[2/4] Extracted {len(result['items'])} status item(s) from {len(result['candidates'])} candidate artifact(s).")
    print(f"      conflicts: {len(result['conflicts'])}")
    print(f"      extraction chunks: {result['extract_stats'].get('chunks', 0)}, failed chunks: {result['extract_stats'].get('failed_chunks', 0)}")

    print("[3/4] Generating answer text...")
    answer = generate_answer(
        question=question,
        items=result["items"],
        conflicts=result["conflicts"],
        hop_paths={candidate["artifact_id"]: candidate.get("path", []) for candidate in result["candidates"]},
        world=world,
    )

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(answer.text + "\n", encoding="utf-8")

    print("[4/4] Done.")
    print(f"Answer written to {out_file}")
    print(f"LLM usage: {llm_report()}")
    wall_clock = time.monotonic() - start
    print(f"Wall clock: {wall_clock:.2f}s")
    print()
    print(answer.text)

    return {
        "question": question,
        "corpus_dir": corpus_dir,
        "recall": recall,
        "hops": hop_count,
        "candidates": len(result["candidates"]),
        "items": len(result["items"]),
        "conflicts": len(result["conflicts"]),
        "extract_stats": result["extract_stats"],
        "answer": answer,
        "out_path": str(out_file),
        "wall_clock": wall_clock,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agent 5 pipeline: retrieval, extraction, answer generation.")
    parser.add_argument("--corpus", default="data", help="Path containing data/corpus and data/world.json.")
    parser.add_argument("--question", required=True, help="Question to answer.")
    parser.add_argument("--out", required=True, help="Output path for the answer text.")
    parser.add_argument("--hops", type=int, default=3, help="Number of retrieval hops.")
    parser.add_argument("--top-k", type=int, default=50, help="Max number of artifacts to retrieve.")
    parser.add_argument("--batch-size", type=int, default=10, help="Extraction batch size.")
    parser.add_argument("--max-workers", type=int, default=4, help="Parallel extraction workers.")
    parser.add_argument(
        "--only-source",
        choices=("slack", "email", "ticket", "doc", "export"),
        default=None,
        help="Restrict retrieval and extraction to a single source.",
    )
    args = parser.parse_args(argv)

    try:
        run_pipeline(
            question=args.question,
            corpus_dir=args.corpus,
            out_path=args.out,
            hops=args.hops,
            top_k=args.top_k,
            batch_size=args.batch_size,
            max_workers=args.max_workers,
            only_source=args.only_source,
        )
    except Exception as exc:
        print(f"Pipeline failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
