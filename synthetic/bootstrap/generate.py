"""Generate the bootstrap corpus: data/corpus/*.jsonl, world.json, answer_key.json.

Templated, seeded, no LLM, no network. Regenerating with the same seed produces
byte-identical output.

    python3 -m synthetic.bootstrap.generate

DATASET_DESIGN §6 H+0:30 calls this non-negotiable: ~200 artifacts covering one
incident end to end, so Agents 2-6 build against real files today instead of
waiting on the full 90-day corpus. Ugly and small is correct.

Provenance (_prov) is carried internally and STRIPPED at assembly — it lands in
the answer key instead. A leak means the agent can cheat and you won't notice
until a judge asks a question you didn't rehearse.
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

from synthetic.bootstrap import narrative as N
from synthetic.bootstrap.world import build_world

SEED = 20260514
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CORPUS = DATA / "corpus"

TZ = N.TZ
INCIDENT_DAY = N.INCIDENT_DAY
CORPUS_START = date(2026, 4, 20)
CORPUS_END = date(2026, 5, 20)

TARGET_TOTAL = 200


def ts(day: str, time_local: str) -> str:
    return f"{day}T{time_local}{TZ}"


def artifact(
    artifact_id, source, container_id, parent_id, sender_id, recipients,
    ts_value, text, meta=None, prov=None,
) -> dict:
    """Build one artifact in the BUILD_PLAN §1 normalized shape."""
    record = {
        "artifact_id": artifact_id,
        "source": source,
        "container_id": container_id,
        "parent_id": parent_id,
        "sender_id": sender_id,
        "recipients": list(recipients),
        "ts": ts_value,
        "text": text,
        "meta": meta or {},
        "raw": {},
    }
    if prov:
        record["_prov"] = prov
    return record


# --- Exhaust ------------------------------------------------------------------
# ~70% of volume. Entity-realistic: it name-drops real people, projects and
# services at plausible rates, so it is genuinely confusable with signal.
# Exhaust that never mentions Aegis is not a distractor.

STANDUP = [
    "yesterday: {proj} tickets. today: same. no blockers",
    "y: reviewed the {svc} PR. t: {proj} planning. blocked on nothing",
    "standup: still chasing the flaky test in {svc}. otherwise fine",
    "y/ {proj} docs. t/ {proj} docs. b/ none",
    "catching up after PTO, going through {svc} backlog today",
]

BOT = [
    "[CI] build #{n} passed on main ({svc})",
    "[CI] build #{n} FAILED on branch feat/{proj}-cleanup — 2 tests",
    "[deploy] {svc} v1.{n} rolled out to prod",
    "[PagerDuty] resolved: {svc} latency p99 (auto-resolved after 4m)",
    "[calendar] {proj} sync moved to 10:30 Thursday",
    "[github] PR #{n} merged into main by {who}",
    "[snyk] 1 new low-severity advisory in {svc} dependencies",
]

CHATTER = [
    "does anyone have the wifi password for the 4th floor",
    "coffee machine on 3 is dead again",
    "lunch order going in, last call for thai",
    "whoever left the whiteboard markers uncapped, we need to talk",
    "is the standup doc link still the old one?",
    "happy friday everyone",
    "who do I ping about a broken badge reader",
    "the 2pm room is double-booked again",
    "reminder: expense reports due end of week",
    "anyone else's VPN dropping every 20 min",
]

PTO = [
    "OOO Thursday and Friday, {who} has the pager",
    "taking a half day, back after lunch",
    "on PTO next week — {proj} handover doc is in the drive",
    "wfh today, plumber",
]

CS_NOISE = [
    "{client} asking about the roadmap deck again",
    "renewal call with {client} moved to next Tuesday",
    "{client} raised a P3 about slow report exports",
    "got a nice note from {client}'s CSM about the onboarding",
]

FIN_NOISE = [
    "Q3 forecast draft is in the sheet, comments by Friday",
    "vendor invoice for the load-testing tool needs an approver",
    "headcount plan review moved to Monday",
    "reminder: PO numbers on all invoices over 5k",
]

# Near-miss distractors (§5-Agent-E): threads that look exactly like they would
# answer the gold question and do not. A DIFFERENT outage, a DIFFERENT service,
# three weeks earlier — and one of them mentions a memory leak, which is the
# plausible-but-wrong cause the answer key forbids.
NEAR_MISS = [
    ("#eng-billing", "EMP_009",
     "postmortem for the {d}/4 svc-notify incident is up. root cause was a memory leak in the retry worker"),
    ("#eng-billing", "EMP_006",
     "svc-notify OOMKilled twice overnight. memory leak in the same retry path as last month"),
    ("#eng-billing", "EMP_003",
     "reminder that the svc-notify memory leak is still open, we just restart it nightly"),
    ("#cs-escalations", "EMP_007",
     "Northwind saw 12 min of degraded notifications on the {d}th. not checkout, notifications"),
    ("#eng-aegis", "EMP_141",
     "the connection pool tuning doc for svc-checkout is done, unrelated to bill-v2"),
    ("#fin-ops", "EMP_143",
     "Northwind SLA credit from April was 2k, already booked against Q2"),
]


def working_days() -> list[str]:
    days, cursor = [], CORPUS_START
    while cursor <= CORPUS_END:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def build_exhaust(rng: random.Random, world: dict, needed: int) -> list[dict]:
    people = [e["employee_id"] for e in world["employees"] if e["employee_id"] != "EMP_002"]
    proj_aliases = [a for p in world["projects"] for a in p["aliases"]]
    svc_aliases = [a for s in world["services"] for a in s["aliases"]]
    client_aliases = [a for c in world["clients"] for a in c["aliases"]]
    days = working_days()

    out: list[dict] = []
    counter = 100

    def next_id(prefix: str) -> str:
        nonlocal counter
        counter += 1
        return f"{prefix}_{counter:04d}"

    # Near-miss distractors first, so they always survive the target trim.
    for i, (channel, sender, template) in enumerate(NEAR_MISS):
        day = days[rng.randrange(0, 12)]
        out.append(artifact(
            next_id("slk"), "slack", channel, None, sender, [],
            ts(day, f"{9 + i % 7:02d}:{(13 * i) % 60:02d}:0{i % 10}"),
            template.format(d=rng.randrange(11, 26)),
            meta={"near_miss": True},
        ))

    pools = [
        ("standup", STANDUP, ["#general", "#eng-billing", "#eng-aegis"]),
        ("bot", BOT, ["#eng-billing", "#eng-aegis", "#general"]),
        ("chatter", CHATTER, ["#general"]),
        ("pto", PTO, ["#general", "#eng-billing"]),
        ("cs", CS_NOISE, ["#cs-escalations"]),
        ("fin", FIN_NOISE, ["#fin-ops"]),
    ]
    weights = [30, 28, 18, 8, 9, 7]

    while len(out) < needed:
        kind, templates, channels = rng.choices(pools, weights=weights, k=1)[0]
        template = rng.choice(templates)
        channel = rng.choice(channels)
        day = rng.choice(days)
        hour = rng.randrange(8, 19)
        minute = rng.randrange(0, 60)
        second = rng.randrange(0, 60)

        sender = "BOT_CI" if kind == "bot" else rng.choice(people)
        members = next(c["members"] for c in world["channels"] if c["channel_id"] == channel)
        if sender != "BOT_CI" and sender not in members:
            sender = rng.choice(members)

        text = template.format(
            proj=rng.choice(proj_aliases),
            svc=rng.choice(svc_aliases),
            client=rng.choice(client_aliases),
            who=rng.choice(people),
            n=rng.randrange(100, 999),
        )

        # §P5 human mess: occasional typos and lowercase drift, mechanically applied.
        if kind in ("standup", "chatter") and rng.random() < 0.18:
            text = text.replace("the ", "teh ", 1)
        if rng.random() < 0.12:
            text = text.lower()

        out.append(artifact(
            next_id("slk"), "slack", channel, None, sender, [],
            ts(day, f"{hour:02d}:{minute:02d}:{second:02d}"), text,
        ))

    return out


# --- Assembly -----------------------------------------------------------------


def build_corpus() -> tuple[list[dict], dict]:
    rng = random.Random(SEED)
    world = build_world()

    gold: list[dict] = []
    for spec in N.GOLD + N.CONFLICT + N.SCAFFOLD + [N.SILENCE_ARTIFACT]:
        gold.append(artifact(
            spec["artifact_id"], spec["source"], spec["container_id"],
            spec["parent_id"], spec["sender_id"], spec["recipients"],
            ts(INCIDENT_DAY, spec["time"]), spec["text"],
            prov={
                "event_id": "evt_001",
                "fact_ids": spec.get("carries_facts", []),
                "hop_index": spec.get("hop_index"),
            },
        ))

    exhaust = build_exhaust(rng, world, TARGET_TOTAL - len(gold))
    everything = sorted(gold + exhaust, key=lambda a: (a["ts"], a["artifact_id"]))
    return everything, world


def build_answer_key(artifacts: list[dict]) -> dict:
    """Six questions: 3 multi-hop, 1 silence, 1 conflict, 1 abstention."""
    return {
        "generated_by": "synthetic/bootstrap/generate.py",
        "seed": SEED,
        "event": {
            "event_id": "evt_001",
            "day": INCIDENT_DAY,
            "type": "incident",
            "severity": 2,
            "project": "PROJ_AEGIS",
            "primary_owner": "EMP_082",
            "impacted": ["svc_bill", "svc_checkout", "CUST_991"],
            "facts": N.FACTS,
        },
        "fact_placement": {
            fid: [a["artifact_id"] for a in artifacts
                  if fid in a.get("_prov", {}).get("fact_ids", [])]
            for fid in N.FACTS
        },
        "conflicts": [N.CONFLICT_PAIR],
        "silence_pairs": [N.SILENCE_PAIR],
        "questions": [
            {
                "question_id": "Q_001",
                "type": "multi_hop",
                "asked_as": "EMP_002",
                "question": "Why is CS flagging churn risk on Acme?",
                "gold_facts": ["evt_001:f3", "evt_001:f1", "evt_001:f4"],
                "gold_artifacts": ["eml_0032", "slk_0041", "slk_0067"],
                "min_hops": 3,
                "vocab_disjoint_hops": 1,
                "unanswerable_from": ["email"],
                "acceptable_answer_contains": ["connection pool", "credit", "ENG-4402"],
                "must_not_contain": ["memory leak"],
            },
            {
                "question_id": "Q_002",
                "type": "multi_hop",
                "asked_as": "EMP_002",
                "question": "What is holding up the Aegis GA date?",
                "gold_facts": ["evt_001:f5", "evt_001:f1"],
                "gold_artifacts": ["slk_0072", "slk_0041"],
                "min_hops": 2,
                "vocab_disjoint_hops": 1,
                "unanswerable_from": ["email"],
                "acceptable_answer_contains": ["two weeks", "connection pool"],
                "must_not_contain": ["memory leak"],
            },
            {
                "question_id": "Q_003",
                "type": "multi_hop",
                "asked_as": "EMP_001",
                "question": "Who actually owns svc-bill-v2, and who got paged?",
                "gold_facts": ["evt_001:f6"],
                "gold_artifacts": ["tkt_4402_c3", "slk_0039"],
                "min_hops": 2,
                "vocab_disjoint_hops": 0,
                "unanswerable_from": ["email"],
                "acceptable_answer_contains": ["EMP_082", "EMP_003"],
                "must_not_contain": [],
            },
            {
                "question_id": "Q_004",
                "type": "conflict",
                "asked_as": "EMP_001",
                "question": "What caused the billing outage on May 14?",
                "gold_facts": ["evt_001:f1"],
                "gold_artifacts": ["slk_0040", "tkt_4402_c5", "slk_0041"],
                "min_hops": 2,
                "vocab_disjoint_hops": 0,
                "unanswerable_from": [],
                "acceptable_answer_contains": ["connection pool"],
                "must_not_contain": [],
                "expects_conflict": "cnf_001",
                "note": ("Both positions must be surfaced. An answer that says 'memory "
                         "leak' full stop is wrong; one that reports only the resolved "
                         "cause without noting the superseded claim is incomplete."),
            },
            {
                "question_id": "Q_005",
                "type": "silence",
                "asked_as": "EMP_001",
                "question": "Is anyone about to promise Acme something we can't deliver?",
                "gold_facts": ["evt_001:f5"],
                "gold_artifacts": ["slk_0072", "eml_0038"],
                "min_hops": 2,
                "vocab_disjoint_hops": 0,
                "unanswerable_from": ["slack"],
                "acceptable_answer_contains": ["EMP_002", "GA"],
                "must_not_contain": [],
                "expects_silence_pair": "sil_001",
            },
            {
                "question_id": "Q_006",
                "type": "abstention",
                "asked_as": "EMP_002",
                "question": "Who signed off on the svc-auth security review?",
                "gold_facts": [],
                "gold_artifacts": [],
                "min_hops": 0,
                "vocab_disjoint_hops": 0,
                "unanswerable_from": ["slack", "email", "ticket"],
                "acceptable_answer_contains": [],
                "must_not_contain": ["signed off", "approved"],
                "expected_behaviour": "abstain",
                "expected_referral": "EMP_011",
                "note": ("svc-auth exists and is owned by EMP_011, but no artifact in the "
                         "corpus mentions a security review. A confident answer here is a "
                         "hallucination. The win condition is 'I don't have that — "
                         "svc-auth is owned by Omar Haddad, ask him.'"),
            },
        ],
    }


def main() -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    artifacts, world = build_corpus()
    key = build_answer_key(artifacts)

    # Strip provenance. It lives in the answer key, never in the corpus.
    by_source: dict[str, list[dict]] = {}
    for record in artifacts:
        clean = {k: v for k, v in record.items() if k != "_prov"}
        by_source.setdefault(record["source"], []).append(clean)

    (DATA / "world.json").write_text(json.dumps(world, indent=2) + "\n", encoding="utf-8")
    (DATA / "answer_key.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")

    for source, records in sorted(by_source.items()):
        path = CORPUS / f"{source}.jsonl"
        path.write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8"
        )
        print(f"  {path.relative_to(ROOT)}: {len(records)} artifacts")

    print(f"\n  data/world.json: {len(world['employees'])} employees, "
          f"{len(world['channels'])} channels")
    print(f"  data/answer_key.json: {len(key['questions'])} questions, "
          f"{len(key['conflicts'])} conflict(s), {len(key['silence_pairs'])} silence pair(s)")
    print(f"  total artifacts: {len(artifacts)}")


if __name__ == "__main__":
    main()
