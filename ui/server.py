"""Interactive demo UI for the COO Oracle.

Stdlib-only web server (no new dependency, requirements.txt untouched — see
CLAUDE.md's "don't add dependencies without asking"). Lives entirely under ui/, a
lane no BUILD_PLAN agent owns, and only *imports* the real pipeline components
(Index, Retriever, answer_question, World, llm) rather than editing them — those
files (src/pipeline.py, src/answer.py) are Agent 5's in-flight lane.

Mirrors src.pipeline.run()'s stage order but returns structured JSON instead of
printing to stdout / writing a mandatory out_path, so a browser can render the
same real pipeline live. Every optional stage (extract/crossref/alerts) uses the
identical try/except ImportError pattern as pipeline.py: not built yet is reported
by name, never faked, never silently skipped.

Run: python -m ui.server [--corpus data/corpus] [--port 8000] [--no-llm]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import src  # noqa: F401 - UTF-8 stdout/stderr fix, must import before anything prints

from src import llm
from src.answer import answer_question
from src.index import Index
from src.ingest import load_corpus, print_ingest_report
from src.retrieve import Retriever
from src.schema import StatusItem
from src.world import World, WorldError

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = Path(__file__).resolve().parent

# ------------------------------------------------------------------- state

class State:
    """Loaded once at boot; every request reads from here. Not reloaded per request —
    a live demo needs consistent latency, and the corpus doesn't change mid-run."""

    def __init__(self, corpus_dir: str, default_no_llm: bool):
        self.corpus_dir = corpus_dir
        self.default_no_llm = default_no_llm
        self.lock = threading.Lock()  # serialize /api/ask so double-clicks can't
        # interleave two runs into llm.py's process-wide RPM limiter

        data_dir = Path(corpus_dir).parent
        print("== COO Oracle UI: booting ==")
        print(f"corpus: {corpus_dir}")

        self.artifacts, self.failures, self.stats = load_corpus(corpus_dir)
        print_ingest_report(self.artifacts, self.failures, self.stats)

        world_path = data_dir / "world.json"
        self.world = World.from_path(world_path)  # raises WorldError, let it kill boot

        self.index = Index([a.to_dict() for a in self.artifacts], self.world.raw)

        self.gold_questions: List[Dict[str, Any]] = []
        key_path = data_dir / "answer_key.json"
        if key_path.exists():
            try:
                key = json.loads(key_path.read_text(encoding="utf-8"))
                self.gold_questions = [
                    {"question_id": q.get("question_id"), "type": q.get("type"), "question": q.get("question")}
                    for q in key.get("questions", [])
                ]
            except json.JSONDecodeError as exc:
                print(f"answer_key.json present but not valid JSON, skipping gold questions: {exc}", file=sys.stderr)
        else:
            print("answer_key.json not found — gold-question chips will be empty (not fatal)")

        print(f"ready: {len(self.artifacts)} artifacts, {len(self.failures)} parse failure(s)")


STATE: Optional[State] = None


# --------------------------------------------------------------- optional stages
# Identical contract to src/pipeline.py's _try_extract/_try_crossref/_try_alerts:
# module missing -> reported by name, degraded path continues, never faked.

def _try_extract(candidates: List[Dict[str, Any]]) -> Tuple[Optional[List[StatusItem]], int, Dict[str, Any]]:
    try:
        from src import extract as extract_mod  # type: ignore
    except ImportError:
        return None, 0, {"name": "extract", "built": False, "detail": "src/extract.py not present"}
    try:
        items, dropped = extract_mod.extract_items(candidates)
    except AttributeError as exc:
        return None, 0, {"name": "extract", "built": False, "detail": f"extract_items() missing/wrong shape: {exc}"}
    except Exception as exc:  # noqa: BLE001 - one bad stage must not kill the run
        return None, 0, {"name": "extract", "built": False, "detail": f"{exc.__class__.__name__}: {exc}"}
    return items, dropped, {"name": "extract", "built": True, "count": len(items), "dropped": dropped}


def _try_crossref(items: List[StatusItem]):
    try:
        from src import crossref as crossref_mod  # type: ignore
    except ImportError:
        return items, [], {"name": "crossref", "built": False, "detail": "src/crossref.py not present"}
    try:
        deduped, conflicts = crossref_mod.find_conflicts(items)
    except AttributeError as exc:
        return items, [], {"name": "crossref", "built": False, "detail": f"find_conflicts() missing/wrong shape: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return items, [], {"name": "crossref", "built": False, "detail": f"{exc.__class__.__name__}: {exc}"}
    return deduped, conflicts, {"name": "crossref", "built": True, "count": len(deduped), "conflicts": len(conflicts)}


def _try_alerts(items: List[StatusItem], world: World, index: Index):
    try:
        from src import alerts as alerts_mod  # type: ignore
    except ImportError:
        return [], {"name": "alerts", "built": False, "detail": "src/alerts.py not present"}
    try:
        lines = alerts_mod.find_silences(items, world, index)
    except AttributeError as exc:
        return [], {"name": "alerts", "built": False, "detail": f"find_silences() missing/wrong shape: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return [], {"name": "alerts", "built": False, "detail": f"{exc.__class__.__name__}: {exc}"}
    return lines, {"name": "alerts", "built": True, "count": len(lines)}


# ------------------------------------------------------------------------ ask

def handle_ask(payload: Dict[str, Any]) -> Dict[str, Any]:
    state = STATE
    assert state is not None
    question = (payload.get("question") or "").strip()
    if not question:
        raise ValueError("question is empty")
    no_llm = bool(payload.get("no_llm", state.default_no_llm))
    only_source = payload.get("only_source") or None
    hops = int(payload.get("hops") or 3)
    top_k = int(payload.get("top_k") or 50)

    started = time.monotonic()
    stages: List[Dict[str, Any]] = []

    t0 = time.monotonic()
    retriever = Retriever(state.index, only_source=only_source)
    results = retriever.retrieve(question, hops=hops, top_k=top_k)
    hop_paths = {r["artifact_id"]: r["path"] for r in results}
    stages.append({"name": "retrieve", "built": True, "count": len(results), "ms": round((time.monotonic() - t0) * 1000)})

    if no_llm:
        elapsed = time.monotonic() - started
        return {
            "answer": None,
            "no_llm": True,
            "candidates": results,
            "stages": stages,
            "who_would_know": [],
            "llm": llm.report(),
            "elapsed_s": round(elapsed, 2),
        }

    t0 = time.monotonic()
    items, _dropped, extract_stage = _try_extract(results)
    extract_stage["ms"] = round((time.monotonic() - t0) * 1000)
    stages.append(extract_stage)

    conflicts_out: List[Dict[str, Any]] = []
    if items:
        t0 = time.monotonic()
        items, conflicts, crossref_stage = _try_crossref(items)
        crossref_stage["ms"] = round((time.monotonic() - t0) * 1000)
        stages.append(crossref_stage)
        conflicts_out = [c.to_dict() for c in conflicts]
    else:
        stages.append({"name": "crossref", "built": False, "detail": "skipped: no extracted items"})

    t0 = time.monotonic()
    answer = answer_question(question, results, state.world, items=items, hop_paths=hop_paths)
    stages.append({
        "name": "answer",
        "built": True,
        "count": len(answer.items),
        "abstained": answer.abstained,
        "ms": round((time.monotonic() - t0) * 1000),
    })

    t0 = time.monotonic()
    alert_lines, alerts_stage = _try_alerts(items or [], state.world, state.index)
    alerts_stage["ms"] = round((time.monotonic() - t0) * 1000)
    stages.append(alerts_stage)

    who_would_know = [{"id": e, "display": state.world.display(e)} for e in answer.who_would_know]

    elapsed = time.monotonic() - started
    body = answer.to_dict()
    body["conflicts"] = conflicts_out or body.get("conflicts", [])
    return {
        "answer": body,
        "no_llm": False,
        "candidates": results,
        "stages": stages,
        "alerts": alert_lines,
        "who_would_know": who_would_know,
        "llm": llm.report(),
        "elapsed_s": round(elapsed, 2),
    }


# --------------------------------------------------------------------- handler

class Handler(BaseHTTPRequestHandler):
    server_version = "COOOracleUI/1"

    def log_message(self, fmt, *args):  # noqa: A002 - keep console readable during demo
        sys.stderr.write(f"[ui] {self.address_string()} {fmt % args}\n")

    def _send_json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        state = STATE
        assert state is not None
        if self.path == "/" or self.path == "/index.html":
            html = (UI_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        if self.path == "/api/health":
            self._send_json(200, {
                "artifacts": len(state.artifacts),
                "by_source": dict(state.stats.by_source),
                "failures": len(state.failures),
                "warned_artifacts": state.stats.warned_artifacts,
                "llm_model": llm.MODEL,
                "llm": llm.report(),
            })
            return
        if self.path == "/api/questions":
            self._send_json(200, state.gold_questions)
            return
        self._send_json(404, {"error": "not found", "detail": self.path})

    def do_POST(self):  # noqa: N802
        if self.path != "/api/ask":
            self._send_json(404, {"error": "not found", "detail": self.path})
            return
        state = STATE
        assert state is not None
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": "bad request", "detail": f"invalid JSON body: {exc}"})
            return

        with state.lock:  # serialize against llm.py's process-wide RPM limiter
            try:
                result = handle_ask(payload)
                self._send_json(200, result)
            except Exception as exc:  # noqa: BLE001 - CLAUDE.md: fail loud and specific
                traceback.print_exc()
                self._send_json(500, {
                    "error": exc.__class__.__name__,
                    "detail": str(exc),
                })


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="COO Oracle — interactive demo UI (stdlib only).")
    parser.add_argument("--corpus", default="data/corpus")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-llm", action="store_true", help="default the UI toggle to retrieval-only")
    args = parser.parse_args(argv)

    global STATE
    try:
        STATE = State(args.corpus, args.no_llm)
    except (FileNotFoundError, NotADirectoryError, WorldError) as exc:
        print(f"FAILED to boot: {exc}", file=sys.stderr)
        return 1

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"listening: http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
