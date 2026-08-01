"""Corpus loader: data/corpus/*.jsonl -> [Artifact], plus a named failure per bad record.

Contract (BUILD_PLAN Agent 2): one malformed record never kills the run, and nothing is
dropped silently. Failures come back as structured records and get printed in the demo —
"3 records unparseable: ..." reads as robustness, a clean output with hidden drops reads
as a magic trick.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.schema import Artifact, artifact_from_dict

MAX_RAW_IN_FAILURE = 200


@dataclass
class Failure:
    """One record (or file) we could not turn into an Artifact, and exactly why."""
    file: str
    line_no: Optional[int]
    reason: str
    raw: str

    def __str__(self) -> str:
        where = f"{self.file}:{self.line_no}" if self.line_no else self.file
        return f"{where}: {self.reason} | raw: {self.raw}"

    def to_dict(self) -> Dict[str, Any]:
        return {"file": self.file, "line_no": self.line_no, "reason": self.reason, "raw": self.raw}


@dataclass
class IngestStats:
    files: int = 0
    lines_read: int = 0
    blank_lines: int = 0
    artifacts: int = 0
    by_source: Counter = field(default_factory=Counter)
    warnings: Counter = field(default_factory=Counter)   # warning text -> count
    warned_artifacts: int = 0


def _truncate(text: str) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= MAX_RAW_IN_FAILURE else text[:MAX_RAW_IN_FAILURE] + "…(truncated)"


def _read_lines(path: Path) -> Tuple[List[str], Optional[str]]:
    """Read a file as text. Returns (lines, warning). Encoding problems degrade, never drop."""
    data = path.read_bytes()
    warning = None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        text = data.decode("utf-8", errors="replace")
        warning = (
            f"{path.name} is not valid UTF-8 ({exc.reason} at byte {exc.start}); "
            f"decoded with replacement characters rather than skipping the file"
        )
    return text.splitlines(), warning


def load_corpus(corpus_dir: str) -> Tuple[List[Artifact], List[Failure], IngestStats]:
    """Load every *.jsonl under corpus_dir.

    Returns (artifacts, failures, stats). Raises only when the corpus directory itself
    is unusable — a missing corpus is a setup error worth stopping for; a bad record
    inside it is not.
    """
    root = Path(corpus_dir)
    if not root.exists():
        raise FileNotFoundError(
            f"corpus directory {root!s} does not exist (cwd: {os.getcwd()}). "
            f"Pass --corpus with a directory containing .jsonl files."
        )
    if not root.is_dir():
        raise NotADirectoryError(f"corpus path {root!s} is a file, not a directory")

    paths = sorted(root.glob("*.jsonl"))
    if not paths:
        raise FileNotFoundError(
            f"no .jsonl files in {root!s} (found: "
            f"{', '.join(p.name for p in sorted(root.iterdir())[:10]) or 'nothing'})"
        )

    artifacts: List[Artifact] = []
    failures: List[Failure] = []
    stats = IngestStats()
    seen: Dict[str, str] = {}   # artifact_id -> "file:line" of first sighting

    for path in paths:
        stats.files += 1
        try:
            lines, encoding_warning = _read_lines(path)
        except OSError as exc:
            failures.append(Failure(path.name, None, f"could not read file: {exc}", ""))
            continue
        if encoding_warning:
            stats.warnings[encoding_warning] += 1
            print(f"[ingest] WARNING {encoding_warning}", file=sys.stderr)

        source_hint = path.stem.split(".")[0].split("_")[0].lower()

        for line_no, line in enumerate(lines, start=1):
            stats.lines_read += 1
            if not line.strip():
                stats.blank_lines += 1
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                failures.append(Failure(
                    path.name, line_no,
                    f"not valid JSON — {exc.msg}, col {exc.colno}",
                    _truncate(line),
                ))
                continue

            try:
                artifact, reason = artifact_from_dict(record, source_hint=source_hint)
            except Exception as exc:  # a coercion bug must not take the corpus down
                failures.append(Failure(
                    path.name, line_no,
                    f"unexpected {exc.__class__.__name__} while normalizing: {exc}",
                    _truncate(line),
                ))
                continue

            if artifact is None:
                failures.append(Failure(path.name, line_no, reason or "unknown", _truncate(line)))
                continue

            where = f"{path.name}:{line_no}"
            if artifact.artifact_id in seen:
                failures.append(Failure(
                    path.name, line_no,
                    f"duplicate artifact_id {artifact.artifact_id!r} "
                    f"(first seen at {seen[artifact.artifact_id]}); citations would be ambiguous",
                    _truncate(line),
                ))
                continue
            seen[artifact.artifact_id] = where

            artifacts.append(artifact)
            stats.artifacts += 1
            stats.by_source[artifact.source] += 1
            if artifact.warnings:
                stats.warned_artifacts += 1
                for warning in artifact.warnings:
                    stats.warnings[warning] += 1

    return artifacts, failures, stats


def print_ingest_report(
    artifacts: List[Artifact],
    failures: List[Failure],
    stats: IngestStats,
    *,
    stream=sys.stdout,
    max_failures: int = 25,
) -> None:
    """The ugly numbers, printed. pipeline.py reuses this verbatim."""
    print(
        f"ingest: {stats.artifacts} artifacts from {stats.files} file(s) "
        f"({stats.lines_read} lines, {stats.blank_lines} blank) | "
        f"{len(failures)} unparseable | {stats.warned_artifacts} loaded with warnings",
        file=stream,
    )
    if stats.by_source:
        by_source = ", ".join(f"{src}={n}" for src, n in sorted(stats.by_source.items()))
        print(f"  by source: {by_source}", file=stream)

    no_ts = [a for a in artifacts if a.ts is None]
    if no_ts:
        print(
            f"  {len(no_ts)} artifact(s) kept with an unusable timestamp: "
            f"{', '.join(a.artifact_id for a in no_ts[:8])}",
            file=stream,
        )

    if stats.warnings:
        print(f"  warnings ({sum(stats.warnings.values())}):", file=stream)
        for warning, count in stats.warnings.most_common(12):
            print(f"    [{count}x] {warning}", file=stream)

    if failures:
        print(f"  unparseable records ({len(failures)}):", file=stream)
        for failure in failures[:max_failures]:
            print(f"    - {failure}", file=stream)
        if len(failures) > max_failures:
            print(f"    … and {len(failures) - max_failures} more", file=stream)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Load a corpus and report what happened.")
    parser.add_argument("--corpus", default="data/corpus", help="directory of .jsonl files")
    parser.add_argument("--json", action="store_true", help="dump loaded artifacts as JSON")
    args = parser.parse_args(argv)

    try:
        artifacts, failures, stats = load_corpus(args.corpus)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    # With --json, stdout is machine-readable and the report goes to stderr.
    print_ingest_report(artifacts, failures, stats, stream=sys.stderr if args.json else sys.stdout)
    if args.json:
        print(json.dumps([a.to_dict() for a in artifacts], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
