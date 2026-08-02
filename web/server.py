"""COOless web frontend — stdlib only, no new dependencies.

    venv/bin/python web/server.py
    -> http://localhost:8765

Wraps the same src.pipeline the CLI demo uses. Questions are serialized through
a lock because the free-tier rate limiter is global anyway — two concurrent
questions would just queue on the API.
"""

import io
import json
import sys
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # so `import src.*` works when run as web/server.py
INDEX_HTML = Path(__file__).parent / "index.html"
PORT = 8765

_pipeline_lock = threading.Lock()

VALID_SOURCES = {"", "slack", "email", "ticket", "doc", "export"}


def _split_answer(text: str) -> tuple[str, list[str]]:
    """Separate the prose answer from the 'How I got there:' trace block."""
    marker = "\nHow I got there:\n"
    if marker in text:
        prose, _, trace = text.partition(marker)
        lines = [line.strip() for line in trace.splitlines() if line.strip()]
        return prose.strip(), lines
    return text.strip(), []


def run_question(question: str, only_source: str | None) -> dict:
    from src.llm import report
    from src.pipeline import run_pipeline

    log = io.StringIO()
    with redirect_stdout(log):
        result = run_pipeline(
            question=question,
            corpus_dir="data/corpus",
            out_path="out/web_answer.md",
            only_source=only_source,
        )

    answer = result["answer"]
    prose, trace = _split_answer(answer.text)
    return {
        "question": question,
        "only_source": only_source,
        "answer": prose,
        "trace": trace,
        "abstained": answer.abstained,
        "who_would_know": list(getattr(answer, "who_would_know", []) or []),
        "recall": result["recall"],
        "candidates": result["candidates"],
        "items": result["items"],
        "conflicts": result["conflicts"],
        "wall_clock": round(result["wall_clock"], 1),
        "usage": report(),
        "log": log.getvalue(),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if self.path != "/api/ask":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = str(payload.get("question", "")).strip()
            only_source = str(payload.get("only_source", "") or "").strip()
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json(400, {"error": f"bad request: {e}"})
            return

        if not question:
            self._send_json(400, {"error": "question is required"})
            return
        if len(question) > 500:
            self._send_json(400, {"error": "question too long (500 chars max)"})
            return
        if only_source not in VALID_SOURCES:
            self._send_json(400, {"error": f"only_source must be one of {sorted(VALID_SOURCES - {''})}"})
            return

        try:
            with _pipeline_lock:
                result = run_question(question, only_source or None)
        except Exception as e:  # noqa: BLE001 - surface, don't crash the server
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
            return
        self._send_json(200, result)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[web] {self.address_string()} {fmt % args}")


def main() -> None:
    import os
    os.chdir(ROOT)  # pipeline paths (data/corpus, out/) are repo-relative
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"COOless web UI -> http://localhost:{PORT}   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
