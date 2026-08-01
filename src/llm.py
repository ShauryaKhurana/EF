"""The one Gemini client. Real calls only — there is no offline/mock path by design.

Merged from two implementations: the model-fallback chain and daily-quota detection
that came in with the root pipeline (the thing most likely to save a live run when a
free-tier daily allowance runs out mid-demo), plus the process-wide RPM limiter, JSON
coercion and usage accounting from the spine.

Everything that talks to Gemini goes through `generate_content` here — extraction.py,
store.py and agent.py all import it, so there is one retry policy and one usage total.
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

load_dotenv()

# Free-tier daily request quotas are per-model and small. Overridable from .env so you
# can switch models without touching code; check your limits at aistudio.google.com/rate-limit.
#
# Verified against a live key on 2026-08-01: gemini-3.6-flash ~2.0s, gemini-3.5-flash
# ~1.0s, gemini-3.1-flash-lite ~0.5s. gemini-2.5-flash now returns 404 "no longer
# available to new users" and gemini-2.0-flash 429s, so neither is a usable fallback.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip()
FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_FALLBACK_MODELS", "gemini-3.5-flash,gemini-3.1-flash-lite"
    ).split(",")
    if m.strip() and m.strip() != MODEL
]

EMBED_MODEL = "gemini-embedding-001"

MAX_RETRIES = 3
MAX_RETRY_WAIT = 65.0        # don't sit through a multi-minute backoff mid-demo
DEFAULT_TIMEOUT_S = 60.0
# Free-tier requests per minute. Leaving this at the free-tier number only makes us
# slower, never wrong. Override with GEMINI_RPM if the key is on a paid tier.
DEFAULT_RPM = int(os.environ.get("GEMINI_RPM", "10"))

_client: Optional[genai.Client] = None
_client_lock = threading.Lock()


class LLMError(RuntimeError):
    """A call failed and no model could serve it."""


class QuotaExhausted(LLMError):
    """Every candidate model hit its rate limit."""


class LLMJSONError(ValueError):
    """The model returned text that is not usable JSON. Carries the raw text.

    Callers handle this per batch — one unparseable batch must not kill the others.
    """

    def __init__(self, message: str, raw_text: str):
        super().__init__(message)
        self.raw_text = raw_text


# ------------------------------------------------------------------ rate limiting

class _RateLimiter:
    """Sliding-window RPM gate. Thread-safe: extraction runs chunks in parallel."""

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


_limiter = _RateLimiter(DEFAULT_RPM)


# ------------------------------------------------------------------------- usage

class _Usage:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls = 0
        self.retries = 0
        self.failures = 0
        self.fallbacks = 0
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
    """One line of ugly numbers for the end of a run. Printed by the pipeline."""
    return (
        f"llm: {usage.calls} calls, {usage.retries} retries, {usage.fallbacks} model "
        f"fallbacks, {usage.failures} failed | tokens in/out: "
        f"{usage.input_tokens}/{usage.output_tokens} | api time: {usage.api_seconds:.1f}s, "
        f"rate-limit wait: {_limiter.total_wait_s:.1f}s | cost: $0.00 (Gemini free tier)"
    )


# ------------------------------------------------------------------------ client

def require_api_key() -> str:
    """Return the Gemini API key, or explain precisely what's wrong with it.

    Catches the common case of .env still holding the .env.example placeholder,
    which is non-empty and would otherwise fail later as an opaque auth error.
    """
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise LLMError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and fill in your key "
            "(get one free at https://aistudio.google.com/apikey). There is no offline "
            "fallback — every LLM call in this pipeline is real."
        )
    if key.startswith("your-") or key == "your-gemini-api-key":
        raise LLMError(
            "GEMINI_API_KEY is still the placeholder from .env.example. Open .env and "
            "replace 'your-gemini-api-key' with your real key "
            "(get one free at https://aistudio.google.com/apikey)."
        )
    return key


def get_client() -> genai.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = genai.Client(
                api_key=require_api_key(),
                http_options=types.HttpOptions(timeout=int(DEFAULT_TIMEOUT_S * 1000)),
            )
        return _client


def _is_rate_limit(error: Exception) -> bool:
    return isinstance(error, errors.ClientError) and getattr(error, "code", None) == 429


def _is_daily_quota(error: Exception) -> bool:
    """Daily caps don't recover in seconds — fall straight through to another model."""
    details = str(getattr(error, "details", "") or "")
    return "PerDay" in details or "per day" in str(error).lower()


def _is_retryable_server_error(error: Exception) -> bool:
    code = getattr(error, "code", None)
    return isinstance(error, errors.APIError) and code in (408, 500, 502, 503, 504)


def _retry_delay(error: Exception, default: float = 5.0) -> float:
    match = re.search(r"retry in ([0-9.]+)s", str(error), re.IGNORECASE)
    if not match:
        match = re.search(r"'retryDelay': '([0-9.]+)s'", str(error))
    return min(float(match.group(1)) + 1 if match else default, MAX_RETRY_WAIT)


def generate_content(contents: Any, config: Any, models: Optional[List[str]] = None) -> Any:
    """Call Gemini, retrying transient rate limits and falling back across models.

    A per-minute limit is waited out; a per-day quota is not (it won't clear in time),
    so we move to the next model immediately. This is what keeps a live demo alive
    when the primary model's free-tier daily allowance runs out mid-run.
    """
    client = get_client()
    candidates = models or [MODEL, *FALLBACK_MODELS]
    last_error: Optional[Exception] = None

    for model_index, model in enumerate(candidates):
        if model_index > 0:
            with usage.lock:
                usage.fallbacks += 1
        for attempt in range(MAX_RETRIES):
            _limiter.acquire()
            started = time.monotonic()
            try:
                response = client.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except Exception as e:  # noqa: BLE001 - re-raised below unless retryable
                last_error = e
                if _is_rate_limit(e):
                    if _is_daily_quota(e) or attempt == MAX_RETRIES - 1:
                        if len(candidates) > 1:
                            print(f"[llm] {model} rate-limited; trying next model...")
                        break
                    delay = _retry_delay(e)
                    with usage.lock:
                        usage.retries += 1
                    print(f"[llm] {model} rate-limited; retrying in {delay:.0f}s...")
                    time.sleep(delay)
                    continue
                if _is_retryable_server_error(e) and attempt < MAX_RETRIES - 1:
                    delay = min(2.0 ** (attempt + 1) + random.uniform(0, 1), MAX_RETRY_WAIT)
                    with usage.lock:
                        usage.retries += 1
                    print(
                        f"[llm] {model} returned HTTP {getattr(e, 'code', '?')} in "
                        f"{time.monotonic() - started:.1f}s; retrying in {delay:.0f}s...",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                with usage.lock:
                    usage.failures += 1
                raise

            usage.record(response, time.monotonic() - started)
            return response

    with usage.lock:
        usage.failures += 1
    raise QuotaExhausted(
        f"All models rate-limited ({', '.join(candidates)}). "
        f"Check your limits at https://aistudio.google.com/rate-limit or set "
        f"GEMINI_MODEL in .env. Last error: {last_error}"
    )


def response_text(response: Any, label: str = "call") -> str:
    """Pull text out of a response, failing loudly with the API's own reason."""
    text = (getattr(response, "text", None) or "").strip()
    if text:
        return text
    reason = "unknown"
    candidates = getattr(response, "candidates", None)
    if candidates:
        reason = f"finish_reason={getattr(candidates[0], 'finish_reason', None)}"
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None:
        reason += f", prompt_feedback={feedback}"
    raise LLMError(f"{label}: model returned no text ({reason}).")


def generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: Optional[float] = None,
    json_mode: bool = False,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    label: str = "call",
) -> str:
    """One text completion, with the shared retry/fallback policy."""
    config = types.GenerateContentConfig(
        temperature=temperature,
        system_instruction=system,
        response_mime_type="application/json" if json_mode else None,
        http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
    )
    return response_text(generate_content(prompt, config), label=label)


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
    print(f"[selftest] primary: {MODEL} | fallbacks: {', '.join(FALLBACK_MODELS) or 'none'}")
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
