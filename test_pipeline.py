"""End-to-end and unit tests for the COO Agent pipeline.

Run:  venv/bin/python test_pipeline.py

Offline tests stub the Gemini calls, so the whole pipeline is exercised without an
API key or a Slack token. If GEMINI_API_KEY is set, a live smoke test also runs
against the real API; otherwise it is skipped and reported as such.
"""

import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from dotenv import load_dotenv

import agent
import extraction
import ingestion
import orchestrator
import output
import store as store_mod
import synthesis
import src.llm as llm

load_dotenv()

TMP_DBS: list[Path] = []


def temp_db() -> str:
    """A throwaway store path, cleaned up at the end of the run."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # let sqlite create it
    TMP_DBS.append(Path(path))
    return path


def fake_embed(_self, texts, task_type):
    """Deterministic pseudo-embeddings derived from the text, so search is stable.

    Similar strings land near each other because shared tokens drive shared
    dimensions — good enough to exercise the retrieval path without the network.
    """
    vectors = []
    for text in texts:
        vector = [0.0] * store_mod.EMBED_DIM
        for token in set(re.findall(r"[a-z0-9]+", text.lower())):
            vector[hash(token) % store_mod.EMBED_DIM] += 1.0
        vectors.append(vector)
    return vectors

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []
SKIPPED: list[tuple[str, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
    except AssertionError as e:
        FAILED.append((name, str(e) or "assertion failed"))
    except Exception as e:
        FAILED.append((name, f"{type(e).__name__}: {e}"))
    else:
        PASSED.append(name)


def expect_raises(exc_type, fn, contains: str | None = None) -> None:
    try:
        fn()
    except exc_type as e:
        if contains and contains not in str(e):
            raise AssertionError(f"expected {contains!r} in error, got: {e}")
        return
    raise AssertionError(f"expected {exc_type.__name__} to be raised")


# --- Fake model responses ----------------------------------------------------

# Spans below are copied verbatim out of sample_data/messages.json. They have to be:
# extract() re-checks every span against the source text and discards items whose
# quote isn't there, so a fake that paraphrases would be dropped exactly like a real
# hallucination would.
FAKE_ITEMS = {
    "items": [
        {
            "source": "slack",
            "topic": "Billing migration",
            "status": "blocked",
            "owner": "Marcus",
            "blocker": "Legal sign-off pending on the data retention change",
            "confidence": "high",
            "evidence": [
                {
                    "artifact_id": "msg_1",
                    "span": "Nothing moves until legal signs off on the data retention change",
                }
            ],
        },
        {
            "source": "email",
            "topic": "Mobile app v3",
            "status": "on_track",
            "owner": "Dan",
            "blocker": None,
            "confidence": "high",
            "evidence": [
                {
                    "artifact_id": "msg_2",
                    "span": "we're on schedule to ship to TestFlight next Wednesday",
                }
            ],
        },
        {
            "source": "slack",
            "topic": "Search reindex",
            "status": "unclear",
            "owner": None,
            "blocker": None,
            "confidence": "low",
            "evidence": [
                {
                    "artifact_id": "msg_4",
                    "span": "kind of a mess right now, still digging into it",
                }
            ],
        },
    ]
}

FAKE_BRIEFING = (
    "*COO Briefing*\n\n"
    "*Blocked*\n• *Billing migration* — legal sign-off pending (Marcus)\n\n"
    "*On track*\n• *Mobile app v3* (Dan)\n\n"
    "*Suggested focus*\n• Chase legal on the retention change."
)


def stub_models(monkey: dict) -> None:
    """Replace every network call with deterministic fakes. Records the prompts."""
    monkey["extract_calls"] = []
    monkey["synth_calls"] = []
    monkey["agent_calls"] = []

    def fake_extract_call(contents: str) -> str:
        monkey["extract_calls"].append(contents)
        return json.dumps(FAKE_ITEMS)

    def fake_synth_call(prompt: str) -> str:
        monkey["synth_calls"].append(prompt)
        return FAKE_BRIEFING

    def fake_agent_call(prompt: str, history=None) -> str:
        monkey["agent_calls"].append(prompt)
        return "Billing migration is blocked on legal sign-off (Marcus)."

    extraction._call_model = fake_extract_call
    synthesis._call_model = fake_synth_call
    agent._call_model = fake_agent_call
    store_mod.Store._embed = fake_embed


def restore_models(originals: tuple) -> None:
    extraction._call_model, synthesis._call_model, agent._call_model, store_mod.Store._embed = originals


def current_models() -> tuple:
    return (
        extraction._call_model,
        synthesis._call_model,
        agent._call_model,
        store_mod.Store._embed,
    )


# --- Ingestion ---------------------------------------------------------------


def test_fixtures_load():
    messages = ingestion.ingest()
    assert len(messages) == 10, f"expected 10 fixture messages, got {len(messages)}"
    for m in messages:
        assert set(m) == {"source", "sender", "timestamp", "text"}, f"bad keys: {set(m)}"
        assert m["source"] in ("slack", "email"), m["source"]
        assert m["text"].strip(), "empty text survived normalization"


def test_normalize_field_variants():
    raw = [
        {"source": "slack", "user": "priya", "ts": "1738400000.001", "body": "Migration is blocked"},
        {"source": "email", "from": "d@co.com", "date": "2026-08-01", "message": "All green"},
    ]
    out = ingestion.normalize_messages(raw)
    assert len(out) == 2, f"expected 2, got {len(out)}"
    assert out[0]["sender"] == "priya", out[0]
    assert out[0]["text"] == "Migration is blocked", out[0]
    assert out[1]["timestamp"] == "2026-08-01", out[1]


def test_normalize_drops_empty_and_rejects_bad_source():
    out = ingestion.normalize_messages([{"source": "slack", "sender": "a", "text": "   "}])
    assert out == [], f"blank message should be dropped, got {out}"
    expect_raises(
        ingestion.IngestionError,
        lambda: ingestion.normalize_messages([{"source": "teams", "text": "hi"}]),
        contains="unsupported source",
    )


def test_missing_fixture_file_errors():
    expect_raises(
        ingestion.IngestionError,
        lambda: ingestion.ingest(fixtures_path="does_not_exist.json"),
        contains="not found",
    )


def test_slack_source_requires_channel():
    expect_raises(
        ingestion.IngestionError,
        lambda: ingestion.ingest(source="slack"),
        contains="requires a channel",
    )


# --- Gmail adapter -----------------------------------------------------------


def test_gmail_fixture_parses():
    messages = ingestion.ingest(source="gmail")
    assert len(messages) == 4, f"expected 4 Gmail messages, got {len(messages)}"
    for m in messages:
        assert m["source"] == "email", m
        assert set(m) == {"source", "sender", "timestamp", "text"}, set(m)
        assert m["text"].startswith("Subject: "), m["text"][:40]


def test_gmail_decodes_base64url_and_walks_nested_multipart():
    messages = ingestion.ingest(source="gmail")
    # Message 2 is multipart/alternative; message 3 nests multipart inside multipart.
    sofia = next(m for m in messages if "sofia" in m["sender"])
    legal = next(m for m in messages if "legal" in m["sender"])
    assert "Snowflake pushed our provisioning window" in sofia["text"], sofia["text"]
    assert "blocked pending the external auditor" in legal["text"], legal["text"]


def test_gmail_strips_quoted_history():
    """Quoted replies carry stale status that must not be extracted as current."""
    legal = next(m for m in ingestion.ingest(source="gmail") if "legal" in m["sender"])
    assert "everything was green" not in legal["text"], "stale quoted status leaked through"
    assert "wrote:" not in legal["text"], "reply marker leaked through"
    assert ">" not in legal["text"], "quoted lines leaked through"


def test_gmail_strips_signature_block():
    dan = next(m for m in ingestion.ingest(source="gmail") if "dan" in m["sender"])
    assert "Engineering Manager" not in dan["text"], "signature leaked through"
    assert "TestFlight next Wednesday" in dan["text"], dan["text"]


def test_gmail_extracts_address_and_timestamp():
    dan = next(m for m in ingestion.ingest(source="gmail") if "dan" in m["sender"])
    assert dan["sender"] == "dan@company.com", dan["sender"]  # not "Dan Reyes <...>"
    assert dan["timestamp"] == "2026-08-01T09:40:00Z", dan["timestamp"]


def test_gmail_falls_back_to_html_then_snippet():
    html_only = {
        "id": "x",
        "internalDate": "1785663600000",
        "payload": {
            "mimeType": "text/html",
            "headers": [{"name": "From", "value": "a@b.com"}, {"name": "Subject", "value": "S"}],
            "body": {"data": ingestion.base64.urlsafe_b64encode(
                b"<p>Cutover is <b>blocked</b></p>").decode().rstrip("=")},
        },
    }
    record = ingestion.gmail_message_to_record(html_only)
    assert "Cutover is blocked" in record["text"].replace("  ", " "), record["text"]

    snippet_only = {"id": "y", "snippet": "Server migration done", "payload": {"headers": []}}
    assert "Server migration done" in ingestion.gmail_message_to_record(snippet_only)["text"]

    empty = {"id": "z", "payload": {"headers": []}}
    assert ingestion.gmail_message_to_record(empty) is None, "empty message should be dropped"


def test_fixture_format_autodetected():
    """A raw Gmail dump and a normalized file both load through the same entry point."""
    normalized = ingestion.load_fixture_messages(ingestion.DEFAULT_FIXTURES)
    gmail = ingestion.load_fixture_messages(ingestion.GMAIL_FIXTURES)
    assert len(normalized) == 10 and len(gmail) == 4
    assert {m["source"] for m in gmail} == {"email"}


def test_live_gmail_still_flagged_unimplemented():
    expect_raises(NotImplementedError, ingestion.fetch_gmail_messages, contains="not implemented")


def test_gmail_source_ignores_str_default_path():
    """Regression: argparse passes the default fixtures path as a str, not a Path.

    Comparing str against Path made --source gmail silently load the Slack fixture.
    """
    from_path = ingestion.ingest(source="gmail")
    from_str = ingestion.ingest(source="gmail", fixtures_path=str(ingestion.DEFAULT_FIXTURES))
    assert len(from_str) == 4, f"expected the Gmail fixture (4), got {len(from_str)}"
    assert from_str == from_path, "str and Path defaults must resolve identically"


def test_gmail_source_honours_explicit_path():
    """An explicit --fixtures path must still win over the Gmail default."""
    messages = ingestion.ingest(source="gmail", fixtures_path=str(ingestion.GMAIL_FIXTURES))
    assert len(messages) == 4, len(messages)


# --- Extraction --------------------------------------------------------------


def test_extraction_validator_accepts_good_payload():
    items = extraction._validate_payload(FAKE_ITEMS)
    assert len(items) == 3, len(items)
    assert items[0].owner == "Marcus"
    assert items[2].owner is None and items[2].confidence == "low"


def test_extraction_validator_rejects_bad_payloads():
    ev = '"evidence":[{"artifact_id":"msg_1","span":"x"}]'
    cases = [
        ('{"items":[{"source":"teams","topic":"X","status":"on_track","owner":null,'
         '"blocker":null,"confidence":"high",' + ev + '}]}', "expected one of"),
        ('{"items":[{"topic":"X","status":"on_track","owner":null,"blocker":null,'
         '"confidence":"high",' + ev + '}]}', "missing field"),
        ('{"items":[{"source":"slack","topic":"X","status":"on_track","owner":null,'
         '"blocker":null,"confidence":"high",' + ev + ',"eta":"fri"}]}', "unexpected field"),
        ('{"items":[{"source":"slack","topic":"","status":"on_track","owner":null,'
         '"blocker":null,"confidence":"high",' + ev + '}]}', "non-empty string"),
        ('{"items":[{"source":"slack","topic":"X","status":"on_track","owner":7,'
         '"blocker":null,"confidence":"high",' + ev + '}]}', "string or null"),
        ("[]", "Expected a JSON object"),
        ("not json", "not valid JSON"),
        # An item with no evidence is a hallucination by definition (docs/schema.md).
        ('{"items":[{"source":"slack","topic":"X","status":"on_track","owner":null,'
         '"blocker":null,"confidence":"high","evidence":[]}]}', "non-empty list"),
        ('{"items":[{"source":"slack","topic":"X","status":"on_track","owner":null,'
         '"blocker":null,"confidence":"high","evidence":[{"artifact_id":"msg_1"}]}]}',
         "non-empty verbatim quote"),
        ('{"items":[{"source":"slack","topic":"X","status":"on_track","owner":null,'
         '"blocker":null,"confidence":"high","evidence":[{"span":"x"}]}]}',
         "artifact_id: expected a non-empty string"),
    ]
    for raw, expected in cases:
        expect_raises(
            extraction.ExtractionError,
            lambda r=raw: extraction._validate_payload(extraction._parse_json(r)),
            contains=expected,
        )


def test_extraction_empty_input_skips_api():
    calls: list[str] = []
    original = extraction._call_model
    extraction._call_model = lambda c: calls.append(c) or "{}"
    try:
        assert extraction.extract([]) == [], "empty input should return []"
        assert calls == [], "empty input must not hit the API"
    finally:
        extraction._call_model = original


def test_extraction_rejects_malformed_messages():
    expect_raises(
        ValueError,
        lambda: extraction._format_messages([{"source": "slack", "sender": "a"}]),
        contains="missing required key",
    )


# --- Synthesis ---------------------------------------------------------------


def test_synthesis_groups_by_severity():
    items = [extraction.asdict(i) for i in extraction._validate_payload(FAKE_ITEMS)]
    grouped = synthesis._group_by_status(items)
    assert list(grouped) == ["blocked", "unclear", "on_track"], list(grouped)


def test_synthesis_empty_items_skips_api():
    calls: list[str] = []
    original = synthesis._call_model
    synthesis._call_model = lambda p: calls.append(p) or "x"
    try:
        briefing = synthesis.synthesize_briefing([])
        assert calls == [], "empty items must not hit the API"
        assert "No status updates found" in briefing, briefing
    finally:
        synthesis._call_model = original


def test_synthesis_prompt_carries_every_item():
    items = [extraction.asdict(i) for i in extraction._validate_payload(FAKE_ITEMS)]
    prompt = synthesis.build_synthesis_prompt(items)
    for topic in ("Billing migration", "Mobile app v3", "Search reindex"):
        assert topic in prompt, f"{topic} missing from synthesis prompt"


def test_fallback_briefing_hedges_low_confidence():
    items = [extraction.asdict(i) for i in extraction._validate_payload(FAKE_ITEMS)]
    text = synthesis.fallback_briefing(items)
    assert "low confidence" in text, text
    assert "no owner named" in text, text


# --- Output ------------------------------------------------------------------


def test_output_console_fallback_without_token():
    saved = os.environ.pop("SLACK_BOT_TOKEN", None)
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            dest = output.deliver("hello briefing", channel="#ops")
        assert dest == "console", dest
        assert "hello briefing" in buf.getvalue()
        assert "SLACK_BOT_TOKEN not set" in buf.getvalue()
    finally:
        if saved is not None:
            os.environ["SLACK_BOT_TOKEN"] = saved


def test_output_dry_run_never_posts():
    def explode(*a, **k):
        raise AssertionError("dry run must not call Slack")

    original = output.post_to_slack
    output.post_to_slack = explode
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            dest = output.deliver("x", channel="#ops", dry_run=True)
        assert dest == "console", dest
    finally:
        output.post_to_slack = original


# --- End to end --------------------------------------------------------------


# --- Store -------------------------------------------------------------------


def test_topic_key_normalizes_variants():
    key = store_mod.topic_key
    assert key("Billing migration") == key("billing Migration")
    assert key("The Billing Migration project") == key("Billing migration")
    assert key("Migration, billing") == key("Billing migration"), "word order shouldn't fragment"
    assert key("Mobile app") != key("Billing migration")


def test_store_dedupes_messages_by_content():
    with store_mod.Store(temp_db()) as s:
        messages = ingestion.ingest()
        assert s.add_messages(messages) == 10
        assert s.add_messages(messages) == 0, "re-adding identical messages must be a no-op"
        assert s.message_count() == 10
        assert s.new_messages(messages) == [], "nothing should look new the second time"


def test_store_upsert_creates_then_merges():
    items = [dict(i) for i in FAKE_ITEMS["items"]]
    with store_mod.Store(temp_db()) as s:
        result = s.upsert_items(items, seen_at="2026-08-01T10:00:00Z")
        assert result == {"created": 3, "updated": 0}, result

        # Same topic, later reading, now resolved.
        s.upsert_items(
            [{"source": "slack", "topic": "billing migration", "status": "on_track",
              "owner": "Marcus", "blocker": None, "confidence": "high"}],
            seen_at="2026-08-01T12:00:00Z",
        )
        assert s.topic_count() == 3, "a renamed-case topic must merge, not duplicate"
        billing = next(t for t in s.topics() if "illing" in t["topic"])
        assert billing["status"] == "on_track", "the later reading should win"
        assert billing["blocker"] is None
        assert billing["mentions"] == 2


def test_store_urgent_reading_wins_a_tie():
    """Same timestamp: 'blocked' must not be overwritten by a vaguer 'unclear'."""
    with store_mod.Store(temp_db()) as s:
        when = "2026-08-01T10:00:00Z"
        s.upsert_items([{"source": "slack", "topic": "Search", "status": "unclear",
                         "owner": None, "blocker": None, "confidence": "low"}], seen_at=when)
        s.upsert_items([{"source": "email", "topic": "Search", "status": "blocked",
                         "owner": "Tomas", "blocker": "Index corruption",
                         "confidence": "high"}], seen_at=when)
        topic = s.topics()[0]
        assert topic["status"] == "blocked", topic
        assert topic["owner"] == "Tomas"


def test_store_never_loses_a_known_owner():
    with store_mod.Store(temp_db()) as s:
        s.upsert_items([{"source": "slack", "topic": "Warehouse", "status": "at_risk",
                         "owner": "Sofia", "blocker": "Vendor delay",
                         "confidence": "high"}], seen_at="2026-08-01T10:00:00Z")
        s.upsert_items([{"source": "slack", "topic": "Warehouse", "status": "at_risk",
                         "owner": None, "blocker": "Vendor delay",
                         "confidence": "medium"}], seen_at="2026-08-01T11:00:00Z")
        assert s.topics()[0]["owner"] == "Sofia", "a later ownerless read must not erase the owner"


def test_store_ranks_by_urgency():
    with store_mod.Store(temp_db()) as s:
        s.upsert_items([dict(i) for i in FAKE_ITEMS["items"]], seen_at="2026-08-01T10:00:00Z")
        ranked = s.ranked_topics()
        assert ranked[0]["status"] == "blocked", [r["status"] for r in ranked]
        assert ranked[-1]["status"] in ("on_track", "unclear"), ranked[-1]


def test_store_status_items_are_json_serializable():
    """The briefing prompt JSON-dumps these — a raw embedding blob would explode."""
    originals = current_models()
    stub_models({})
    try:
        with store_mod.Store(temp_db()) as s:
            s.upsert_items([dict(i) for i in FAKE_ITEMS["items"]])
            s.reindex()
            items = s.status_items()
            json.dumps(items)  # must not raise
            assert all("embedding" not in i for i in items)
            assert all("embedding" not in t for t in s.search("billing")), "search leaked a blob"
    finally:
        restore_models(originals)


def test_store_history_is_appended():
    with store_mod.Store(temp_db()) as s:
        for i, status in enumerate(["on_track", "at_risk", "blocked"]):
            s.upsert_items([{"source": "slack", "topic": "Billing", "status": status,
                             "owner": None, "blocker": None, "confidence": "high"}],
                           seen_at=f"2026-08-0{i + 1}T10:00:00Z")
        history = s.history(store_mod.topic_key("Billing"))
        assert len(history) == 3, history
        assert history[0]["status"] == "blocked", "newest first"


# --- Retrieval and the agent --------------------------------------------------


def test_search_falls_back_to_keywords_when_unindexed():
    with store_mod.Store(temp_db()) as s:
        s.upsert_items([dict(i) for i in FAKE_ITEMS["items"]])
        hits = s.search("billing")  # no reindex() called, so nothing is embedded
        assert hits, "keyword fallback should still find the topic"
        assert "Billing" in hits[0]["topic"], hits[0]


def test_search_uses_embeddings_when_indexed():
    originals = current_models()
    stub_models({})
    try:
        with store_mod.Store(temp_db()) as s:
            s.upsert_items([dict(i) for i in FAKE_ITEMS["items"]])
            assert s.reindex() == 3, "all three topics should embed"
            assert s.reindex() == 0, "unchanged topics must not re-embed"
            hits = s.search("billing migration", k=2)
            assert hits and "score" in hits[0], hits
    finally:
        restore_models(originals)


def test_agent_refuses_on_empty_store():
    with store_mod.Store(temp_db()) as s:
        answer = agent.ask("what's blocked?", s)
        assert "empty" in answer.text.lower(), answer.text
        assert answer.sources == []


def test_agent_grounds_answer_in_retrieved_context():
    monkey: dict = {}
    originals = current_models()
    stub_models(monkey)
    try:
        with store_mod.Store(temp_db()) as s:
            s.upsert_items([dict(i) for i in FAKE_ITEMS["items"]])
            s.reindex()
            answer = agent.ask("what is happening with billing?", s)
            assert answer.sources, "an answer must carry its sources"
            prompt = monkey["agent_calls"][0]
            assert "CONTEXT" in prompt and "QUESTION" in prompt
            assert "Billing migration" in prompt, prompt[:400]
    finally:
        restore_models(originals)


def test_agent_always_surfaces_urgent_topics():
    """A vague question must still pull blocked/at-risk work into context."""
    originals = current_models()
    stub_models({})
    try:
        with store_mod.Store(temp_db()) as s:
            s.upsert_items([dict(i) for i in FAKE_ITEMS["items"]])
            s.reindex()
            context = agent.gather_context(s, "anything I should know about?")
            assert any(t["status"] == "blocked" for t in context), \
                [t["status"] for t in context]
    finally:
        restore_models(originals)


# --- Scale --------------------------------------------------------------------


def test_extract_batch_chunks_and_merges():
    originals = current_models()
    calls: list[int] = []

    def fake_extract(msgs):
        calls.append(len(msgs))
        return [{"source": "slack", "topic": "Billing migration", "status": "on_track",
                 "owner": None, "blocker": None, "confidence": "medium"}]

    saved_extract = extraction.extract
    extraction.extract = fake_extract
    try:
        messages = [{"source": "slack", "sender": "a", "timestamp": "t", "text": f"m{i}"}
                    for i in range(95)]
        items = extraction.extract_batch(messages, chunk_size=40)
        assert sorted(calls) == [15, 40, 40], calls
        assert len(items) == 1, "the same topic across chunks must merge into one item"
    finally:
        extraction.extract = saved_extract
        restore_models(originals)


def test_merge_prefers_urgent_status_and_backfills():
    merged = extraction.merge_items([
        {"source": "slack", "topic": "Billing migration", "status": "on_track",
         "owner": None, "blocker": None, "confidence": "medium"},
        {"source": "email", "topic": "billing Migration", "status": "blocked",
         "owner": None, "blocker": "Legal sign-off", "confidence": "high"},
        {"source": "slack", "topic": "The Billing Migration project", "status": "unclear",
         "owner": "Marcus", "blocker": None, "confidence": "low"},
    ])
    assert len(merged) == 1, merged
    assert merged[0]["status"] == "blocked", "a blocked report must not be lost"
    assert merged[0]["owner"] == "Marcus", "owner should be backfilled from another chunk"
    assert merged[0]["blocker"] == "Legal sign-off"


def test_extract_batch_survives_a_failing_chunk():
    originals = current_models()
    saved_extract = extraction.extract
    calls = {"n": 0}

    def flaky(msgs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated chunk failure")
        return [{"source": "slack", "topic": f"T{calls['n']}", "status": "on_track",
                 "owner": None, "blocker": None, "confidence": "high"}]

    extraction.extract = flaky
    try:
        messages = [{"source": "slack", "sender": "a", "timestamp": "t", "text": f"m{i}"}
                    for i in range(120)]
        buf = io.StringIO()
        with redirect_stdout(buf):
            items = extraction.extract_batch(messages, chunk_size=40, max_workers=1)
        assert len(items) == 2, "surviving chunks should still produce items"
        assert "1/3 chunk(s) failed" in buf.getvalue(), buf.getvalue()
    finally:
        extraction.extract = saved_extract
        restore_models(originals)


def test_briefing_caps_length_at_scale():
    """40 topics must not produce a 40-bullet briefing."""
    originals = current_models()
    monkey: dict = {}
    stub_models(monkey)
    try:
        items = [
            {"source": "slack", "topic": f"Project {i}",
             "status": "on_track" if i % 4 else "blocked",
             "owner": None, "blocker": None, "confidence": "high"}
            for i in range(40)
        ]
        briefing = synthesis.synthesize_briefing(items, max_items=12)
        prompt = monkey["synth_calls"][0]
        assert prompt.count('"topic"') == 12, "only the top 12 should reach the model"
        assert "Plus 28 more not detailed above" in briefing, briefing[-200:]
    finally:
        restore_models(originals)


def test_briefing_ranks_blocked_first():
    ranked = synthesis.rank_items([
        {"source": "slack", "topic": "A", "status": "on_track", "owner": None,
         "blocker": None, "confidence": "high"},
        {"source": "slack", "topic": "B", "status": "blocked", "owner": None,
         "blocker": None, "confidence": "low"},
        {"source": "slack", "topic": "C", "status": "at_risk", "owner": None,
         "blocker": None, "confidence": "high"},
    ])
    assert [i["topic"] for i in ranked] == ["B", "C", "A"], [i["topic"] for i in ranked]


# --- Rate limits and model fallback -------------------------------------------


class _FakeQuotaError(Exception):
    """Stands in for google.genai ClientError without needing the real class."""

    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.code = 429
        self.details = details


def _patch_rate_limit_detection():
    """Treat _FakeQuotaError as a 429 for the duration of a test.

    Also drops the cached client so a patched genai.Client is actually used, and
    guarantees a key is present since get_client() checks before dialling out.
    """
    os.environ.setdefault("GEMINI_API_KEY", "offline-test-key")
    llm._client = None
    saved = llm._is_rate_limit
    llm._is_rate_limit = lambda e: getattr(e, "code", None) == 429
    return saved


def test_daily_quota_falls_through_to_next_model():
    saved = _patch_rate_limit_detection()
    tried: list[str] = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            tried.append(model)
            if model != "model-c":
                raise _FakeQuotaError("quota exceeded", details="PerDayPerProject")
            return "ok"

    class FakeClient:
        models = FakeModels()

    saved_client = llm.genai.Client
    llm.genai.Client = lambda **kw: FakeClient()
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = llm.generate_content(
                "hi", None, models=["model-a", "model-b", "model-c"]
            )
        assert result == "ok", result
        assert tried == ["model-a", "model-b", "model-c"], tried
        assert "trying next model" in buf.getvalue()
    finally:
        llm.genai.Client = saved_client
        llm._is_rate_limit = saved
        llm._client = None


def test_daily_quota_does_not_sleep():
    """A per-day cap won't clear in seconds — retrying the same model wastes the demo."""
    saved = _patch_rate_limit_detection()
    slept: list[float] = []
    saved_sleep = llm.time.sleep
    llm.time.sleep = lambda s: slept.append(s)

    calls: list[str] = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            calls.append(model)
            raise _FakeQuotaError("quota exceeded", details="PerDayPerProject")

    class FakeClient:
        models = FakeModels()

    saved_client = llm.genai.Client
    llm.genai.Client = lambda **kw: FakeClient()
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            expect_raises(
                llm.QuotaExhausted,
                lambda: llm.generate_content("hi", None, models=["a", "b"]),
                contains="All models rate-limited",
            )
        assert slept == [], f"should not sleep on a daily quota, slept {slept}"
        assert calls == ["a", "b"], calls
    finally:
        llm.genai.Client = saved_client
        llm.time.sleep = saved_sleep
        llm._is_rate_limit = saved
        llm._client = None


def test_per_minute_limit_is_retried_after_the_stated_delay():
    saved = _patch_rate_limit_detection()
    slept: list[float] = []
    saved_sleep = llm.time.sleep
    llm.time.sleep = lambda s: slept.append(s)

    attempts = {"n": 0}

    class FakeModels:
        def generate_content(self, model, contents, config):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _FakeQuotaError("Please retry in 12.5s", details="PerMinute")
            return "ok"

    class FakeClient:
        models = FakeModels()

    saved_client = llm.genai.Client
    llm.genai.Client = lambda **kw: FakeClient()
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = llm.generate_content("hi", None, models=["a"])
        assert result == "ok"
        assert slept and 13 <= slept[0] <= 14, f"should honour the stated delay, got {slept}"
    finally:
        llm.genai.Client = saved_client
        llm.time.sleep = saved_sleep
        llm._is_rate_limit = saved
        llm._client = None


def test_non_rate_limit_errors_are_not_retried():
    calls = {"n": 0}

    class FakeModels:
        def generate_content(self, model, contents, config):
            calls["n"] += 1
            raise ValueError("bad request")

    class FakeClient:
        models = FakeModels()

    saved_client = llm.genai.Client
    llm.genai.Client = lambda **kw: FakeClient()
    try:
        expect_raises(
            ValueError,
            lambda: llm.generate_content("hi", None, models=["a", "b"]),
            contains="bad request",
        )
        assert calls["n"] == 1, f"a non-429 must fail fast, got {calls['n']} attempts"
    finally:
        llm.genai.Client = saved_client


# --- End to end --------------------------------------------------------------


def test_end_to_end_with_stubbed_models():
    monkey: dict = {}
    originals = current_models()
    stub_models(monkey)
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = orchestrator.run_pipeline(dry_run=True, db_path=temp_db())

        assert result["messages"] == 10, result["messages"]
        assert result["status_items"] == 3, result["status_items"]
        assert result["topics"] == 3, result["topics"]
        assert result["destination"] == "console", result["destination"]

        # The extraction prompt must contain the real fixture content.
        assert len(monkey["extract_calls"]) == 1, monkey["extract_calls"]
        assert "Stripe webhook replay" in monkey["extract_calls"][0]
        # The synthesis prompt must contain the stored topics, not raw messages.
        assert len(monkey["synth_calls"]) == 1
        assert "Billing migration" in monkey["synth_calls"][0]
        # The briefing must reach stdout.
        assert "COO BRIEFING" in buf.getvalue()
    finally:
        restore_models(originals)


def test_second_run_is_incremental():
    """Re-running over the same messages must not re-extract them."""
    monkey: dict = {}
    originals = current_models()
    stub_models(monkey)
    db = temp_db()
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            orchestrator.run_pipeline(dry_run=True, db_path=db)
            first = len(monkey["extract_calls"])
            result = orchestrator.run_pipeline(dry_run=True, db_path=db)
        assert len(monkey["extract_calls"]) == first, "second run should extract nothing new"
        assert result["extracted"] == 0, result
        assert result["topics"] == 3, "topics persist across runs"
    finally:
        restore_models(originals)


def test_no_persist_skips_the_store():
    monkey: dict = {}
    originals = current_models()
    stub_models(monkey)
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = orchestrator.run_pipeline(dry_run=True, persist=False)
        assert result["topics"] == 3, result
        assert "persistence disabled" in buf.getvalue()
    finally:
        restore_models(originals)


def test_end_to_end_with_custom_fixture_file():
    monkey: dict = {}
    originals = current_models()
    stub_models(monkey)
    payload = [{"source": "email", "from": "x@co.com", "date": "2026-08-01", "body": "Ship it"}]
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            path = f.name
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = orchestrator.run_pipeline(
                fixtures_path=path, dry_run=True, db_path=temp_db()
            )
        assert result["messages"] == 1, result["messages"]
    finally:
        restore_models(originals)
        Path(path).unlink(missing_ok=True)


def test_cli_exits_cleanly_on_bad_fixture():
    buf, err = io.StringIO(), io.StringIO()
    saved_err = sys.stderr
    sys.stderr = err
    try:
        with redirect_stdout(buf):
            code = orchestrator.main(["--fixtures", "nope.json", "--dry-run"])
    finally:
        sys.stderr = saved_err
    assert code == 1, f"expected exit code 1, got {code}"
    assert "Pipeline failed" in err.getvalue(), err.getvalue()


# --- Live smoke test (only with a real key) ----------------------------------


def live_smoke_test() -> None:
    """Real Gemini calls, real fixtures, console output."""
    result = orchestrator.run_pipeline(dry_run=True, verbose=True)
    assert result["messages"] == 10, result["messages"]
    assert result["status_items"] >= 4, (
        f"expected at least 4 real projects, got {result['status_items']}"
    )
    assert result["status_items"] <= 8, (
        f"got {result['status_items']} items from 10 messages — noise may be leaking through"
    )
    briefing = result["briefing"]
    assert briefing.strip(), "empty briefing"
    assert "coffee" not in briefing.lower(), "banter leaked into the briefing"
    assert "lunch" not in briefing.lower(), "banter leaked into the briefing"


def main() -> int:
    print("Running offline tests (Gemini calls stubbed)...\n")

    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)

    # Free-tier daily quotas are small (20/day on gemini-3.6-flash), so the live test
    # is opt-in — running the suite must not quietly consume your demo budget.
    if not os.environ.get("RUN_LIVE_TESTS"):
        SKIPPED.append(
            ("live_smoke_test", "set RUN_LIVE_TESTS=1 to run it (costs ~3 API requests)")
        )
    else:
        try:
            extraction.require_api_key()
        except RuntimeError as e:
            SKIPPED.append(("live_smoke_test", f"{e} — no real API call was made"))
        else:
            print("RUN_LIVE_TESTS set — running live smoke test against the real API...\n")
            check("live_smoke_test", live_smoke_test)

    for path in TMP_DBS:
        path.unlink(missing_ok=True)

    print(f"\n{'=' * 72}")
    for name in PASSED:
        print(f"  PASS  {name}")
    for name, reason in SKIPPED:
        print(f"  SKIP  {name}  ({reason})")
    for name, reason in FAILED:
        print(f"  FAIL  {name}\n          {reason}")
    print(f"{'=' * 72}")
    print(f"{len(PASSED)} passed, {len(FAILED)} failed, {len(SKIPPED)} skipped")

    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
