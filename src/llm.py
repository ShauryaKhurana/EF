"""The one Gemini client. Real calls only — there is no offline/mock path by design.

Free tier: gemini-2.5-flash for both extraction and answering. The binding constraint is
the free tier's requests-per-minute cap, not cost, so this module owns a process-wide
rate limiter (extract.py calls in parallel) and a usage counter that prints at end of run.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from collections import deque
from typing import Any, Deque, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

# BUILD_PLAN named gemini-2.5-flash; that model now 404s for new API keys
# ("no longer available to new users"). gemini-3.5-flash is the current free-tier flash
# and answers in ~1s. Pinned deliberately rather than using the gemini-flash-latest
# alias — the demo must not change model under us mid-hackathon.
MODEL = "gemini-3.5-flash"
DEFAULT_TIMEOUT_S = 60.0
MAX_ATTEMPTS = 4
# Free-tier RPM for the flash tier. Override with GEMINI_RPM if the key is on a
# paid tier; leaving it at the free-tier number just makes us slower, never wrong.
DEFAULT_RPM = 10
RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}

_ENV_LOADED = False
_client: Optional[genai.Client] = None
_client_lock = threading.Lock()


class LLMError(RuntimeError):
    """A call failed after exhausting retries."""


class LLMJSONError(ValueError):
    """The model returned text that is not usable JSON. Carries the raw text.

    Callers handle this per batch — one unparseable batch must not kill the others.
    """

    def __init__(self, message: str, raw_text: str):
        super().__init__(message)
        self.raw_text = raw_text


# ------------------------------------------------------------------ rate limiting

class _RateLimiter:
    """Sliding-window RPM gate. Thread-safe: extract.py batches run in parallel."""

    def __init__(self, rpm: int):
        self.rpm = max(1, rpm)
        self._calls: Deque[float] = deque()
        self._lock = threading.Lock()
        self.total_wait_s = 0.0

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= 60.0:
                    self._calls.popleft()
                if len(self._calls) < self.rpm:
                    self._calls.append(now)
                    return
                sleep_for = 60.0 - (now - self._calls[0]) + 0.05
            print(
                f"[llm] rate limit: {self.rpm} req/min reached, waiting {sleep_for:.1f}s",
                file=sys.stderr,
            )
            time.sleep(sleep_for)
            with self._lock:
                self.total_wait_s += sleep_for


_limiter = _RateLimiter(int(os.environ.get("GEMINI_RPM", DEFAULT_RPM)))


# ------------------------------------------------------------------------- usage

class _Usage:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls = 0
        self.retries = 0
        self.failures = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.api_seconds = 0.0

    def record(self, response: Any, elapsed: float) -> None:
        meta = getattr(response, "usage_metadata", None)
        with self.lock:
            self.calls += 1
            self.api_seconds += elapsed
            if meta is not None:
                self.input_tokens += getattr(meta, "prompt_token_count", None) or 0
                self.output_tokens += (
                    (getattr(meta, "candidates_token_count", None) or 0)
                    + (getattr(meta, "thoughts_token_count", None) or 0)
                )


usage = _Usage()


def report() -> str:
    """One line of ugly numbers for the end of a run. Printed in the demo."""
    return (
        f"llm: {usage.calls} calls, {usage.retries} retries, {usage.failures} failed | "
        f"tokens in/out: {usage.input_tokens}/{usage.output_tokens} | "
        f"api time: {usage.api_seconds:.1f}s, rate-limit wait: {_limiter.total_wait_s:.1f}s | "
        f"cost: $0.00 (Gemini free tier, {MODEL})"
    )


# ------------------------------------------------------------------------ client

def get_client() -> genai.Client:
    """Build the shared client. Fails loud and specific if the key is absent."""
    global _client, _ENV_LOADED
    with _client_lock:
        if not _ENV_LOADED:
            load_dotenv()
            _ENV_LOADED = True
        if _client is None:
            api_key = os.environ.get("GEMINI_API_KEY", "").strip()
            if not api_key:
                raise LLMError(
                    "GEMINI_API_KEY is not set. Put it in the .env file at the repo root "
                    "(GEMINI_API_KEY=...) or export it in the shell. "
                    "There is no offline fallback — every LLM call in this pipeline is real."
                )
            _client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=int(DEFAULT_TIMEOUT_S * 1000)),
            )
        return _client


def _retry_delay_from(exc: Exception) -> Optional[float]:
    """Honor the server's own RetryInfo when it sends one."""
    blob = getattr(exc, "details", None)
    text = json.dumps(blob) if blob is not None else str(exc)
    match = re.search(r'"?retryDelay"?[:=\s"]+(\d+(?:\.\d+)?)s', text)
    if match:
        return float(match.group(1))
    return None


def _status_code(exc: Exception) -> Optional[int]:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status
    return None


def generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: str = MODEL,
    temperature: float = 0.0,
    json_mode: bool = False,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_attempts: int = MAX_ATTEMPTS,
    label: str = "call",
) -> str:
    """One text completion. Retries 429/5xx with backoff; raises LLMError on give-up."""
    client = get_client()
    config = types.GenerateContentConfig(
        temperature=temperature,
        system_instruction=system,
        response_mime_type="application/json" if json_mode else None,
        http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
    )

    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        _limiter.acquire()
        started = time.monotonic()
        try:
            response = client.models.generate_content(
                model=model, contents=prompt, config=config
            )
        except errors.APIError as exc:
            elapsed = time.monotonic() - started
            code = _status_code(exc)
            last_error = exc
            if code not in RETRYABLE_CODES or attempt == max_attempts:
                with usage.lock:
                    usage.failures += 1
                raise LLMError(
                    f"{label}: Gemini call failed after {attempt} attempt(s) "
                    f"[HTTP {code}] in {elapsed:.1f}s: {exc}"
                ) from exc
            delay = _retry_delay_from(exc) or min(2.0 ** attempt + random.uniform(0, 1), 30.0)
            with usage.lock:
                usage.retries += 1
            print(
                f"[llm] {label}: HTTP {code} on attempt {attempt}/{max_attempts}, "
                f"retrying in {delay:.1f}s ({exc.__class__.__name__})",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        except Exception as exc:  # transport/timeout errors surface with their real type
            elapsed = time.monotonic() - started
            last_error = exc
            if attempt == max_attempts:
                with usage.lock:
                    usage.failures += 1
                raise LLMError(
                    f"{label}: Gemini call raised {exc.__class__.__name__} after "
                    f"{attempt} attempt(s) in {elapsed:.1f}s: {exc}"
                ) from exc
            delay = min(2.0 ** attempt + random.uniform(0, 1), 30.0)
            with usage.lock:
                usage.retries += 1
            print(
                f"[llm] {label}: {exc.__class__.__name__} on attempt {attempt}/{max_attempts}, "
                f"retrying in {delay:.1f}s: {exc}",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue

        usage.record(response, time.monotonic() - started)
        text = getattr(response, "text", None)
        if not text or not text.strip():
            finish = "unknown"
            candidates = getattr(response, "candidates", None) or []
            if candidates:
                finish = str(getattr(candidates[0], "finish_reason", "unknown"))
            raise LLMError(
                f"{label}: Gemini returned no text (finish_reason={finish}, "
                f"prompt_feedback={getattr(response, 'prompt_feedback', None)})"
            )
        return text

    raise LLMError(f"{label}: exhausted {max_attempts} attempts: {last_error}")


# -------------------------------------------------------------------- JSON coercion

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def coerce_json(text: str) -> Any:
    """Parse JSON out of model output. Raises LLMJSONError carrying the raw text."""
    if not isinstance(text, str) or not text.strip():
        raise LLMJSONError("model returned empty text, expected JSON", text or "")

    candidate = text.strip()
    fenced = _FENCE.match(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as direct_error:
        first_error = direct_error

    extracted = _first_balanced(candidate)
    if extracted is not None:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError as nested_error:
            raise LLMJSONError(
                f"model output is not valid JSON: {first_error.msg} at line "
                f"{first_error.lineno} col {first_error.colno}; the embedded "
                f"{extracted[:1]!r}-block also failed ({nested_error.msg})",
                text,
            ) from nested_error

    raise LLMJSONError(
        f"model output is not valid JSON and contains no JSON object or array "
        f"({first_error.msg} at line {first_error.lineno} col {first_error.colno})",
        text,
    ) from first_error


def _first_balanced(text: str) -> Optional[str]:
    """Pull the first balanced {...} or [...] out of prose, respecting strings."""
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return None
    start = min(starts)
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def generate_json(prompt: str, **kwargs: Any) -> Any:
    """Text completion parsed as JSON. Raises LLMJSONError (with raw text) on garbage."""
    kwargs.setdefault("json_mode", True)
    return coerce_json(generate(prompt, **kwargs))


# ---------------------------------------------------------------------- self test

def _selftest(prompt: str) -> int:
    print(f"[selftest] model: {MODEL}")
    print(f"[selftest] prompt: {prompt}")
    started = time.monotonic()
    text = generate(prompt, label="selftest")
    print(f"[selftest] text response ({time.monotonic() - started:.1f}s):")
    print(f"  {text.strip()}")

    data = generate_json(
        'Return a JSON object with exactly two keys: "ok" (boolean true) and '
        '"model" (a string). No prose.',
        label="selftest-json",
    )
    print(f"[selftest] json round-trip -> {type(data).__name__}: {json.dumps(data)[:200]}")

    print("[selftest] coerce_json on fenced / prose-wrapped output:")
    for sample in ['```json\n{"a": 1}\n```', 'Sure! Here you go: [{"b": "}"}] hope that helps']:
        print(f"  {sample[:42]!r} -> {coerce_json(sample)}")
    try:
        coerce_json("I'm afraid I can't do that.")
    except LLMJSONError as exc:
        print(f"  unparseable case surfaced as: {exc}")

    print(report())
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Gemini client for the COO Oracle.")
    parser.add_argument(
        "--selftest", action="store_true", help="make a real Gemini call and print usage"
    )
    parser.add_argument(
        "--prompt",
        default="Reply with exactly: COO Oracle LLM client online.",
        help="prompt to send with --selftest",
    )
    args = parser.parse_args(argv)
    if not args.selftest:
        parser.error("nothing to do — pass --selftest")
    try:
        return _selftest(args.prompt)
    except LLMError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        print(report(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
