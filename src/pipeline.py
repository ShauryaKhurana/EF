"""The judged CLI entrypoint: corpus ingest -> index -> multi-hop retrieve ->
extract StatusItems -> dedup/conflict -> cited answer or abstention.

scripts/demo.sh calls this exactly:
    python3 -m src.pipeline --corpus data/corpus --question "..." --out out/answer.md

Extract, crossref, and alerts are OPTIONAL STAGES. If the module isn't there yet
(src/extract.py, src/crossref.py, src/alerts.py), the run says so by name and
continues on the degraded path — never silently, never faked. This is what lets the
pipeline run end-to-end today while those stages are still being built, and pick them
up automatically the moment they land, with no changes here.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src import llm
from src.answer import answer_question
from src.index import Index
from src.ingest import load_corpus, print_ingest_report
from src.retrieve import Retriever
from src.schema import Answer, StatusItem, validate_answer
from src.world import World, WorldError


def _stage_missing(name: str, module: str, effect: str) -> None:
    print(f"{name}: STAGE NOT BUILT ({module}) — {effect}")


def _try_extract(candidates: List[Dict[str, Any]]) -> Tuple[Optional[List[StatusItem]], int]:
    """Returns (items or None, dropped_count). None means the stage didn't run at all.

    Expected interface (per BUILD_PLAN Agent 4): src.extract.extract_items(candidates)
    -> (items: list[StatusItem], dropped: int). If the module exists but the interface
    doesn't match, that is surfaced by name too — an integration mismatch must not
    silently fall back and pretend nothing happened.
    """
    try:
        from src import extract as extract_mod  # type: ignore
    except ImportError:
        _stage_missing("extract", "src/extract.py", "answering from raw retrieved artifacts")
        return None, 0

    try:
        items, dropped = extract_mod.extract_items(candidates)
    except AttributeError as exc:
        print(f"extract: src/extract.py exists but extract_items() is missing/wrong shape ({exc}); "
              f"answering from raw retrieved artifacts")
        return None, 0
    except Exception as exc:  # noqa: BLE001 - one bad stage must not kill the run
        print(f"extract: FAILED ({exc.__class__.__name__}: {exc}); answering from raw retrieved artifacts")
        return None, 0

    print(f"extract:   {len(items)} status item(s) | {dropped} dropped (hallucinated span or bad shape)")
    return items, dropped


def _try_crossref(items: List[StatusItem]):
    """Returns (items, conflicts). Expected interface: src.crossref.find_conflicts(items)
    -> (deduped_items, conflicts).
    """
    try:
        from src import crossref as crossref_mod  # type: ignore
    except ImportError:
        _stage_missing("crossref", "src/crossref.py", "conflicts will not be flagged")
        return items, []

    try:
        deduped, conflicts = crossref_mod.find_conflicts(items)
    except AttributeError as exc:
        print(f"crossref: src/crossref.py exists but find_conflicts() is missing/wrong shape ({exc}); "
              f"conflicts will not be flagged")
        return items, []
    except Exception as exc:  # noqa: BLE001
        print(f"crossref: FAILED ({exc.__class__.__name__}: {exc}); conflicts will not be flagged")
        return items, []

    print(f"crossref:  {len(deduped)} item(s) after dedup | {len(conflicts)} conflict(s) flagged")
    return deduped, conflicts


def _try_alerts(items: List[StatusItem], world: World, index: Index) -> List[str]:
    """Expected interface: src.alerts.find_silences(items, world, index) -> list[str] lines."""
    try:
        from src import alerts as alerts_mod  # type: ignore
    except ImportError:
        _stage_missing("alerts", "src/alerts.py", "no proactive silence check")
        return []

    try:
        lines = alerts_mod.find_silences(items, world, index)
    except AttributeError as exc:
        print(f"alerts: src/alerts.py exists but find_silences() is missing/wrong shape ({exc})")
        return []
    except Exception as exc:  # noqa: BLE001
        print(f"alerts: FAILED ({exc.__class__.__name__}: {exc})")
        return []

    print(f"alerts:    {len(lines)} silence alert(s)")
    return lines


def _recall_against_answer_key(data_dir: Path, question: str, candidate_ids: List[str]) -> Optional[str]:
    """If the question matches a bootstrap answer-key question verbatim, print real
    recall against it — the metric to quote to judges, never estimated.
    """
    key_path = data_dir / "answer_key.json"
    if not key_path.exists():
        return None
    try:
        key = json.loads(key_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  (answer_key.json is not valid JSON, skipping recall check: {exc})", file=sys.stderr)
        return None
    for q in key.get("questions", []):
        if q.get("question", "").strip() == question.strip():
            gold = set(q.get("gold_artifacts", []))
            if not gold:
                return None
            hit = gold & set(candidate_ids)
            return f"{q['question_id']}: recall@{len(candidate_ids)} = {len(hit)}/{len(gold)} (missing: {sorted(gold - hit)})"
    return None


def run(
    corpus_dir: str,
    question: str,
    out_path: str,
    only_source: Optional[str] = None,
    hops: int = 3,
    top_k: int = 50,
    no_llm: bool = False,
) -> Answer:
    started = time.monotonic()
    print("== COO Oracle ==")
    print(f"corpus:   {corpus_dir}")
    print(f"question: {question}")
    if only_source:
        print(f"ablation: --only-source {only_source}")

    data_dir = Path(corpus_dir).parent

    artifacts, failures, stats = load_corpus(corpus_dir)
    print_ingest_report(artifacts, failures, stats)

    world_path = data_dir / "world.json"
    try:
        world = World.from_path(world_path)
    except WorldError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise

    index = Index([a.to_dict() for a in artifacts], world.raw)
    retriever = Retriever(index, only_source=only_source)
    results = retriever.retrieve(question, hops=hops, top_k=top_k)
    candidate_ids = [r["artifact_id"] for r in results]
    hop_paths = {r["artifact_id"]: r["path"] for r in results}

    print(f"retrieve:  {len(results)} candidate(s) over up to {hops} hop(s)")
    recall_line = _recall_against_answer_key(data_dir, question, candidate_ids)
    if recall_line:
        print(f"  {recall_line}")

    if no_llm:
        print("(--no-llm: stopping after retrieval)")
        for r in results[:10]:
            print(f"  [{r['artifact_id']}] path={r['path']}")
        if len(results) > 10:
            print(f"  ... and {len(results) - 10} more")
        elapsed = time.monotonic() - started
        print(f"wall clock: {elapsed:.1f}s")
        return Answer(question=question, abstained=True, text="(--no-llm: no answer generated)")

    items, _dropped = _try_extract(results)
    conflicts = []
    if items:
        items, conflicts = _try_crossref(items)

    answer = answer_question(question, results, world, items=items, hop_paths=hop_paths)

    alert_lines = _try_alerts(items or [], world, index)

    ok, reason = validate_answer(answer)
    if not ok:
        print(f"answer:    FAILED VALIDATION ({reason})", file=sys.stderr)
    else:
        status = "ABSTAINED" if answer.abstained else "answered"
        print(f"answer:    {status}")

    print()
    print("--- answer ---")
    print(answer.text)
    if answer.abstained:
        who = ", ".join(world.display(e) for e in answer.who_would_know)
        print(f"\nwho would know: {who}")
    if alert_lines:
        print("\n--- proactive alerts ---")
        for line in alert_lines:
            print(f"  {line}")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# {question}\n\n{answer.text}\n")
        if answer.abstained:
            who = ", ".join(world.display(e) for e in answer.who_would_know)
            f.write(f"\n**Who would know:** {who}\n")
        if conflicts:
            f.write("\n## Conflicts\n")
            for c in conflicts:
                f.write(f"- {c.subject}: {c.resolution} — {c.note}\n")
        if alert_lines:
            f.write("\n## Proactive alerts\n")
            for line in alert_lines:
                f.write(f"- {line}\n")

    elapsed = time.monotonic() - started
    print()
    print(llm.report())
    print(f"wall clock: {elapsed:.1f}s")
    return answer


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="COO Oracle — the judged pipeline.")
    parser.add_argument("--corpus", default="data/corpus", help="directory of *.jsonl corpus files")
    parser.add_argument("--question", required=True, help="the question to answer")
    parser.add_argument("--out", default="out/answer.md", help="where to write the answer")
    parser.add_argument("--only-source", choices=["slack", "email", "ticket", "doc", "export"], default=None,
                         help="single-channel ablation: restrict retrieval to one source")
    parser.add_argument("--hops", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--no-llm", action="store_true", help="stop after retrieval; no API calls")
    args = parser.parse_args(argv)

    try:
        run(
            args.corpus, args.question, args.out,
            only_source=args.only_source, hops=args.hops, top_k=args.top_k,
            no_llm=args.no_llm,
        )
    except (FileNotFoundError, NotADirectoryError, WorldError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
