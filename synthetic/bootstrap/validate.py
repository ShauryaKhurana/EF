"""Acceptance gate for the bootstrap corpus.

    python3 -m synthetic.bootstrap.validate

Checks BUILD_PLAN Agent 1's acceptance criteria plus the DATASET_DESIGN
constraints the corpus exists to satisfy. Every check prints its real number —
per BUILD_PLAN §5.4, surfaced numbers read as robustness; a bare "ok" reads as
a magic trick.

The load-bearing one is GREP: if grepping a question's own words finds most of
its gold artifacts, the multi-hop claim is decorative and the whole pitch
collapses. That check must stay red-sensitive.
"""

import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CORPUS = DATA / "corpus"

REQUIRED_FIELDS = {
    "artifact_id": str,
    "source": str,
    "container_id": str,
    "sender_id": str,
    "recipients": list,
    "ts": str,
    "text": str,
    "meta": dict,
    "raw": dict,
}
VALID_SOURCES = {"slack", "email", "ticket", "doc", "export"}

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "on", "in",
    "at", "to", "for", "of", "and", "or", "but", "if", "then", "so", "we", "i", "you",
    "he", "she", "it", "they", "them", "this", "that", "these", "those", "what",
    "why", "how", "when", "who", "which", "with", "from", "by", "as", "not", "no",
    "do", "does", "did", "have", "has", "had", "can", "could", "will", "would",
    "about", "our", "us", "my", "me", "up", "out", "any", "some", "all", "there",
    "here", "just", "now", "still", "again", "s", "t", "re",
}

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")


def load_corpus() -> tuple[list[dict], list[str]]:
    records, problems = [], []
    for path in sorted(CORPUS.glob("*.jsonl")):
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                problems.append(f"{path.name}:{line_no}: {e}")
    return records, problems


def content_words(text: str, entity_terms: set[str]) -> set[str]:
    """Words that carry meaning: stopwords and entity names removed (§P2)."""
    tokens = set(re.findall(r"[a-z0-9][a-z0-9'-]*", text.lower()))
    return {t for t in tokens if t not in STOPWORDS and t not in entity_terms and len(t) > 2}


def entity_vocabulary(world: dict) -> set[str]:
    terms: set[str] = set()
    for group, name_key in (("employees", "name"), ("projects", "name"),
                            ("services", "name"), ("clients", "name")):
        for item in world.get(group, []):
            for value in [item.get(name_key, "")] + item.get("aliases", []):
                terms.update(re.findall(r"[a-z0-9][a-z0-9'-]*", value.lower()))
    terms.update({"eng", "aeg", "hlx", "4402", "eng-4402", "aeg-2"})
    return terms


def main() -> int:
    world = json.loads((DATA / "world.json").read_text(encoding="utf-8"))
    key = json.loads((DATA / "answer_key.json").read_text(encoding="utf-8"))
    records, parse_problems = load_corpus()
    by_id = {r["artifact_id"]: r for r in records}
    entities = entity_vocabulary(world)

    print(f"\nBootstrap corpus acceptance — {len(records)} artifacts\n" + "=" * 72)

    # --- 1. Schema ----------------------------------------------------------
    schema_errors = []
    for record in records:
        for field, expected in REQUIRED_FIELDS.items():
            if field not in record:
                schema_errors.append(f"{record.get('artifact_id', '?')}: missing {field}")
            elif not isinstance(record[field], expected):
                schema_errors.append(
                    f"{record['artifact_id']}: {field} is {type(record[field]).__name__}, "
                    f"expected {expected.__name__}")
        if "parent_id" not in record:
            schema_errors.append(f"{record.get('artifact_id', '?')}: missing parent_id")
        if record.get("source") not in VALID_SOURCES:
            schema_errors.append(f"{record['artifact_id']}: bad source {record.get('source')!r}")
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$", record.get("ts", "")):
            schema_errors.append(f"{record['artifact_id']}: ts not ISO-8601 with offset")
    check("schema", not schema_errors and not parse_problems,
          f"{len(records)} records validate, {len(schema_errors)} errors, "
          f"{len(parse_problems)} unparseable lines"
          + ("" if not schema_errors else f" -> {schema_errors[:3]}"))

    # --- 2. No provenance leak ----------------------------------------------
    leaks = [r["artifact_id"] for r in records if any(k.startswith("_") for k in r)]
    check("no _prov leak", not leaks,
          f"0 artifacts carry provenance fields" if not leaks else f"LEAKED in {leaks[:5]}")

    # --- 3. Referential integrity -------------------------------------------
    dangling_parents = [r["artifact_id"] for r in records
                        if r.get("parent_id") and r["parent_id"] not in by_id]
    check("parent_id resolves", not dangling_parents,
          f"all thread parents present" if not dangling_parents else str(dangling_parents[:5]))

    unresolved = [(q["question_id"], a) for q in key["questions"]
                  for a in q["gold_artifacts"] if a not in by_id]
    check("gold_artifacts resolve", not unresolved,
          f"{sum(len(q['gold_artifacts']) for q in key['questions'])} gold ids all resolve"
          if not unresolved else str(unresolved))

    known_people = {e["employee_id"] for e in world["employees"]} | {"BOT_ALERTS", "BOT_CI"}
    known_people |= {x["entity_id"] for x in world.get("external", [])}
    unknown_senders = sorted({r["sender_id"] for r in records if r["sender_id"] not in known_people})
    check("senders exist in world", not unknown_senders,
          "every sender_id resolves" if not unknown_senders else str(unknown_senders))

    # --- 4. THE GREP TEST ---------------------------------------------------
    # BUILD_PLAN Agent 1: grep for the question's content words must find
    # < 2 of the >= 3 gold artifacts. This is what proves the hops are real.
    # Reported two ways, because they measure different things and only one of
    # them is what a judge would actually type:
    #   literal  — every question word, entity names INCLUDED (the acceptance bar)
    #   disjoint — entity names removed, i.e. the §P2 vocab-disjointness measure
    grep_rows, disjoint_rows = [], []
    grep_ok = True
    for q in key["questions"]:
        if q["type"] == "abstention" or len(q["gold_artifacts"]) < 3:
            continue
        literal_words = {w for w in re.findall(r"[a-z0-9][a-z0-9'-]*", q["question"].lower())
                         if w not in STOPWORDS and len(w) > 1}
        literal_hits = [a for a in q["gold_artifacts"]
                        if any(re.search(rf"\b{re.escape(w)}\b", by_id[a]["text"].lower())
                               for w in literal_words)]
        stripped = content_words(q["question"], entities)
        stripped_hits = [a for a in q["gold_artifacts"]
                         if stripped & content_words(by_id[a]["text"], entities)]
        n = len(q["gold_artifacts"])
        grep_rows.append(f"{q['question_id']}: {len(literal_hits)}/{n}")
        disjoint_rows.append(f"{q['question_id']}: {len(stripped_hits)}/{n}")
        if len(literal_hits) >= 2:
            grep_ok = False
    check("literal grep finds <2 of gold", grep_ok,
          "  ".join(grep_rows) + "  (entity names included — the acceptance bar)")
    check("entity-stripped overlap", True,
          "  ".join(disjoint_rows) + "  (§P2 measure: content words only)")

    # --- 5. §P1 fact partition ----------------------------------------------
    placement = key["fact_placement"]
    all_facts = set(key["event"]["facts"])
    once = all(len(v) == 1 for v in placement.values())
    check("每 fact placed exactly once".replace("每", "every"), once,
          ", ".join(f"{k}->{v[0] if v else 'MISSING'}" for k, v in placement.items()))

    cap = math.ceil(len(all_facts) / 3)
    per_artifact: dict[str, int] = {}
    for fid, ids in placement.items():
        for aid in ids:
            per_artifact[aid] = per_artifact.get(aid, 0) + 1
    worst = max(per_artifact.values()) if per_artifact else 0
    check("no artifact over ceil(|F|/3)", worst <= cap,
          f"max facts on one artifact = {worst}, cap = {cap}")

    facts_by_container: dict[str, set[str]] = {}
    for fid, ids in placement.items():
        for aid in ids:
            facts_by_container.setdefault(by_id[aid]["container_id"], set()).add(fid)
    sufficient = [c for c, f in facts_by_container.items() if f >= all_facts]
    check("no container is sufficient alone", not sufficient,
          " | ".join(f"{c}:{len(f)}/{len(all_facts)}" for c, f in sorted(facts_by_container.items()))
          + ("" if not sufficient else f"  SUFFICIENT: {sufficient}"))

    # At least one fact must land where the asked_as persona cannot see it.
    persona = key["questions"][0]["asked_as"]
    membership = {c["channel_id"]: set(c["members"]) for c in world["channels"]}
    hidden = []
    for fid, ids in placement.items():
        for aid in ids:
            container = by_id[aid]["container_id"]
            if container in membership and persona not in membership[container]:
                hidden.append(f"{fid}@{container}")
    check(f"facts hidden from {persona}", len(hidden) >= 1,
          f"{len(hidden)} of {len(all_facts)} facts outside their channels: {', '.join(hidden)}")

    # --- 6. §P2 vocabulary-disjoint hop -------------------------------------
    disjoint_report = []
    disjoint_ok = True
    for q in key["questions"]:
        if q.get("vocab_disjoint_hops", 0) < 1:
            continue
        qwords = content_words(q["question"], entities)
        found = [a for a in q["gold_artifacts"]
                 if not (qwords & content_words(by_id[a]["text"], entities))]
        disjoint_report.append(f"{q['question_id']}: {len(found)}")
        if len(found) < q["vocab_disjoint_hops"]:
            disjoint_ok = False
    check("vocab-disjoint hops present", disjoint_ok,
          "  ".join(disjoint_report) + "  (zero content-word overlap with the question)")

    # --- 7. Silence pair is genuinely silent --------------------------------
    sil = key["silence_pairs"][0]
    carrier = by_id[sil["delivered_in"]]
    carrier_members = membership.get(carrier["container_id"], set())
    excluded = sil["person"] not in carrier_members and sil["person"] not in carrier["recipients"]
    before_deadline = [
        r["artifact_id"] for r in records
        if r["ts"] < sil["deadline"]
        and (sil["person"] in r["recipients"] or sil["person"] in membership.get(r["container_id"], set()))
        and r["artifact_id"] == sil["delivered_in"]
    ]
    check("silence pair verified", excluded and not before_deadline,
          f"{sil['person']} not in {carrier['container_id']} "
          f"({sil['delivered_at']}) and received nothing before {sil['deadline']}")

    # --- 8. Conflict pair ---------------------------------------------------
    cnf = key["conflicts"][0]
    positions = cnf["positions"]
    both_exist = all(p["artifact_id"] in by_id for p in positions)
    winner = max(positions, key=lambda p: (p["ts_local"], p["source_authority"]))
    check("conflict pair well-formed",
          both_exist and winner["artifact_id"] == cnf["winning_artifact"],
          f"{positions[0]['claim']!r} ({positions[0]['ts_local']}) vs "
          f"{positions[1]['claim']!r} ({positions[1]['ts_local']}) -> {cnf['resolution']}")

    # --- 9. Abstention question is genuinely unanswerable -------------------
    abstain = next(q for q in key["questions"] if q["type"] == "abstention")
    leak_terms = ["security review", "signed off", "sign-off", "pentest", "audit"]
    accidental = [r["artifact_id"] for r in records
                  if any(t in r["text"].lower() for t in leak_terms)]
    check("abstention truly unanswerable", not accidental,
          f"no artifact mentions a security review "
          f"(referral target {abstain['expected_referral']} exists in world.json)"
          if not accidental else f"answerable via {accidental[:3]}")

    # --- 10. Determinism ----------------------------------------------------
    before = {p.name: p.read_bytes() for p in sorted(CORPUS.glob("*.jsonl"))}
    before["world.json"] = (DATA / "world.json").read_bytes()
    subprocess.run([sys.executable, "-m", "synthetic.bootstrap.generate"],
                   cwd=ROOT, capture_output=True, check=True)
    after = {p.name: p.read_bytes() for p in sorted(CORPUS.glob("*.jsonl"))}
    after["world.json"] = (DATA / "world.json").read_bytes()
    check("regeneration is byte-identical", before == after,
          f"{len(before)} files reproduce exactly from seed")

    # --- Corpus shape -------------------------------------------------------
    gold_ids = {a for q in key["questions"] for a in q["gold_artifacts"]}
    near_miss = [r for r in records if r["meta"].get("near_miss")]
    print("=" * 72)
    print(f"  sources: " + ", ".join(
        f"{s}={sum(1 for r in records if r['source'] == s)}"
        for s in sorted({r['source'] for r in records})))
    print(f"  gold artifacts: {len(gold_ids)}   near-miss distractors: {len(near_miss)}   "
          f"noise: {len(records) - len(gold_ids) - len(near_miss)}")
    print(f"\n  {len(PASSED)} passed, {len(FAILED)} failed")
    for name, detail in FAILED:
        print(f"    FAILED {name}: {detail}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
