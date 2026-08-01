"""Generate the bootstrap corpus: data/corpus/*.jsonl, world.json, answer_key.json.

Templated, seeded, no LLM, no network. Regenerating with the same seed produces
byte-identical output.

    python3 -m synthetic.bootstrap.generate

BUILD_PLAN Agent 7 scope: three anchor events (narrative.EVENTS), 15 gold
questions, scaled up from the original 200-artifact stub toward the reduced
target (~3k Slack, ~300 email, ~80 ticket, 30 days). Ugly and small was
correct for H+0:30; this is the same generator scaled, not a rewrite.

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
CORPUS_START = date(2026, 4, 20)
CORPUS_END = date(2026, 5, 20)

# Reduced from DATASET_DESIGN's ~18k/~1.5k/~400 to a same-day-buildable scale
# per BUILD_PLAN Agent 7 (~3k Slack, ~300 email, ~80 ticket).
SLACK_TARGET = 3000
EMAIL_TARGET = 300
TICKET_TARGET = 80


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


def _event_artifacts(event: dict) -> list[dict]:
    """All hand-authored artifacts for one narrative event, provenance attached."""
    specs = list(event["gold"]) + list(event["scaffold"])
    if event.get("conflict"):
        specs += event["conflict"]["artifacts"]
    if event.get("silence"):
        specs += [event["silence"]["artifact"]]

    out = []
    for spec in specs:
        out.append(artifact(
            spec["artifact_id"], spec["source"], spec["container_id"],
            spec["parent_id"], spec["sender_id"], spec["recipients"],
            ts(event["day"], spec["time"]), spec["text"],
            prov={
                "event_id": event["event_id"],
                "fact_ids": spec.get("carries_facts", []),
                "hop_index": spec.get("hop_index"),
            },
        ))
    return out


# --- Exhaust ------------------------------------------------------------------
# The bulk of volume. Entity-realistic: it name-drops real people, projects and
# services at plausible rates, so it is genuinely confusable with signal.
# Exhaust that never mentions Aegis/Helix is not a distractor.

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
# answer a gold question and do not. A DIFFERENT outage, a DIFFERENT service,
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

EMAIL_EXHAUST = [
    "Subject: Weekly eng notes\n\n{proj} status: on track. {svc} nothing new to report.",
    "Subject: Re: PTO request\n\nApproved — enjoy the time off, {who} has the pager.",
    "Subject: Invoice due\n\nInvoice for {client} is due end of month, PO number needed.",
    "Subject: Great meeting you\n\nFollowing up from the call — let's reconnect next week.",
    "Subject: Company update\n\nAll-hands recap attached. Kudos board is open for nominations.",
    "Subject: Re: onboarding checklist\n\nLooks good, {who} will finish the laptop setup Monday.",
    "Subject: {client} check-in\n\nQuarterly check-in scheduled, agenda attached, nothing urgent on {proj}.",
    "Subject: Benefits enrollment reminder\n\nOpen enrollment closes Friday, see the HR portal.",
]

TICKET_EXHAUST = [
    "Reproduced on staging, low priority. Assigning to {who}.",
    "Cannot reproduce on {svc}, closing as not-a-bug.",
    "Duplicate of an earlier ticket, closing and linking.",
    "Waiting on customer response, no update in 5 days.",
    "{svc}: minor UI polish requested, backlog.",
    "Docs typo in the {proj} runbook, fixed.",
]


def working_days() -> list[str]:
    days, cursor = [], CORPUS_START
    while cursor <= CORPUS_END:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _id_factory(prefix: str, start: int):
    counter = start

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"{prefix}_{counter:04d}"

    return next_id


def build_slack_exhaust(rng: random.Random, world: dict, needed: int) -> list[dict]:
    people = [e["employee_id"] for e in world["employees"] if e["employee_id"] != "EMP_002"]
    proj_aliases = [a for p in world["projects"] for a in p["aliases"]]
    svc_aliases = [a for s in world["services"] for a in s["aliases"]]
    client_aliases = [a for c in world["clients"] for a in c["aliases"]]
    days = working_days()
    next_id = _id_factory("slk", 1000)

    out: list[dict] = []

    for i, (channel, sender, template) in enumerate(NEAR_MISS):
        day = days[rng.randrange(0, 12)]
        out.append(artifact(
            next_id(), "slack", channel, None, sender, [],
            ts(day, f"{9 + i % 7:02d}:{(13 * i) % 60:02d}:0{i % 10}"),
            template.format(d=rng.randrange(11, 26)),
            meta={"near_miss": True},
        ))

    pools = [
        ("standup", STANDUP, ["#general", "#eng-billing", "#eng-aegis", "#eng-helix"]),
        ("bot", BOT, ["#eng-billing", "#eng-aegis", "#eng-helix", "#general"]),
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
            next_id(), "slack", channel, None, sender, [],
            ts(day, f"{hour:02d}:{minute:02d}:{second:02d}"), text,
        ))

    return out


def build_email_exhaust(rng: random.Random, world: dict, needed: int) -> list[dict]:
    people = [e["employee_id"] for e in world["employees"]]
    proj_aliases = [a for p in world["projects"] for a in p["aliases"]]
    svc_aliases = [a for s in world["services"] for a in s["aliases"]]
    client_aliases = [a for c in world["clients"] for a in c["aliases"]]
    days = working_days()
    next_id = _id_factory("eml", 1000)
    next_thread = _id_factory("thr_exh", 1000)

    out: list[dict] = []
    while len(out) < needed:
        template = rng.choice(EMAIL_EXHAUST)
        sender, recipient = rng.sample(people, 2)
        day = rng.choice(days)
        text = template.format(
            proj=rng.choice(proj_aliases), svc=rng.choice(svc_aliases),
            client=rng.choice(client_aliases), who=rng.choice(people),
        )
        out.append(artifact(
            next_id(), "email", next_thread(), None, sender, [recipient],
            ts(day, f"{rng.randrange(8, 18):02d}:{rng.randrange(0, 60):02d}:{rng.randrange(0, 60):02d}"),
            text,
        ))
    return out


def build_ticket_exhaust(rng: random.Random, world: dict, needed: int) -> list[dict]:
    people = [e["employee_id"] for e in world["employees"]]
    proj_aliases = [a for p in world["projects"] for a in p["aliases"]]
    svc_aliases = [a for s in world["services"] for a in s["aliases"]]
    days = working_days()
    next_ticket_key = _id_factory("TKT", 1000)
    next_id = _id_factory("tkt", 6000)

    out: list[dict] = []
    while len(out) < needed:
        template = rng.choice(TICKET_EXHAUST)
        sender = rng.choice(people)
        day = rng.choice(days)
        text = template.format(svc=rng.choice(svc_aliases), proj=rng.choice(proj_aliases),
                                who=rng.choice(people))
        out.append(artifact(
            next_id(), "ticket", next_ticket_key(), None, sender, [],
            ts(day, f"{rng.randrange(8, 18):02d}:{rng.randrange(0, 60):02d}:{rng.randrange(0, 60):02d}"),
            text,
        ))
    return out


# --- Assembly -----------------------------------------------------------------


def build_corpus() -> tuple[list[dict], dict]:
    rng = random.Random(SEED)
    world = build_world()

    gold: list[dict] = []
    for event in N.EVENTS:
        gold.extend(_event_artifacts(event))

    by_source: dict[str, int] = {}
    for a in gold:
        by_source[a["source"]] = by_source.get(a["source"], 0) + 1

    slack_needed = max(0, SLACK_TARGET - by_source.get("slack", 0))
    email_needed = max(0, EMAIL_TARGET - by_source.get("email", 0))
    ticket_needed = max(0, TICKET_TARGET - by_source.get("ticket", 0))

    exhaust = (
        build_slack_exhaust(rng, world, slack_needed)
        + build_email_exhaust(rng, world, email_needed)
        + build_ticket_exhaust(rng, world, ticket_needed)
    )
    everything = sorted(gold + exhaust, key=lambda a: (a["ts"], a["artifact_id"]))
    return everything, world


def build_answer_key(artifacts: list[dict]) -> dict:
    by_id = {a["artifact_id"]: a for a in artifacts}

    fact_placement = {}
    for event in N.EVENTS:
        placement = {}
        for fid in event["facts"]:
            placement[fid] = [
                a["artifact_id"] for a in artifacts
                if a.get("_prov", {}).get("event_id") == event["event_id"]
                and fid in a.get("_prov", {}).get("fact_ids", [])
            ]
        fact_placement[event["event_id"]] = placement

    conflicts = [event["conflict"]["pair"] for event in N.EVENTS if event.get("conflict")]
    silence_pairs = [event["silence"]["pair"] for event in N.EVENTS if event.get("silence")]

    return {
        "generated_by": "synthetic/bootstrap/generate.py",
        "seed": SEED,
        "events": [
            {
                "event_id": e["event_id"], "day": e["day"], "type": e["type"],
                "severity": e["severity"], "project": e["project"],
                "primary_owner": e["primary_owner"], "impacted": e["impacted"],
                "facts": e["facts"],
            }
            for e in N.EVENTS
        ],
        "fact_placement": fact_placement,
        "conflicts": conflicts,
        "silence_pairs": silence_pairs,
        "questions": N.QUESTIONS,
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
          f"{len(key['conflicts'])} conflict(s), {len(key['silence_pairs'])} silence pair(s), "
          f"{len(key['events'])} anchor event(s)")
    print(f"  total artifacts: {len(artifacts)}")


if __name__ == "__main__":
    main()
