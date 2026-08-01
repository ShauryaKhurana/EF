"""Hand-authored gold narratives. Everything else in the corpus is texture.

BUILD_PLAN Agent 7 scope: 15 gold questions (8 multi-hop, 3 silence,
2 abstention, 2 conflict) built from three anchor events, cut down from
DATASET_DESIGN's full 60-question/~220-event target for a same-day build.

Each event follows DATASET_DESIGN §P1/§P2:
  - every fact appears in exactly ONE artifact
  - no artifact carries more than ceil(|F|/3) facts
  - for every container, facts(container) is a PROPER subset of F
  - at least one fact lands where the question's `asked_as` persona can't see it
  - at least one hop shares zero content words with the question (vocab-disjoint,
    validated in validate.py's entity-stripped overlap check)

`generate.py` iterates EVENTS to build the corpus and QUESTIONS to build the
answer key; validate.py's checks loop over both instead of assuming one event.
"""

TZ = "-07:00"

# =====================================================================
# EVENT 1 — evt_001, 2026-05-14: the billing outage / Aegis slip.
# Kept verbatim from the original bootstrap. slk_0041 is the vocab-disjoint
# hop: "pool's maxed again on bill-v2. who merged 4402" shares zero content
# words with "Why is CS flagging churn risk on Acme?" — the only link is the
# ticket id 4402 and the service alias bill-v2.
# =====================================================================

INCIDENT_DAY = "2026-05-14"

EVENT_1 = dict(
    event_id="evt_001", day=INCIDENT_DAY, type="incident", severity=2,
    project="PROJ_AEGIS", primary_owner="EMP_082",
    impacted=["svc_bill", "svc_checkout", "CUST_991"],
    facts={
        "f1": "root_cause: connection-pool exhaustion in svc-bill-v2",
        "f2": "trigger: config change merged Thu 18:40 by EMP_141",
        "f3": "customer_impact: Acme Corp saw 41min checkout downtime",
        "f4": "financial_impact: $18k SLA credit owed, hits Q3 milestone payout",
        "f5": "schedule_impact: Aegis GA slips 2 weeks",
        "f6": "true_owner: EMP_082, not the on-call who was paged",
    },
    gold=[
        dict(artifact_id="slk_0088", source="slack", container_id="#cs-escalations",
             parent_id=None, sender_id="EMP_002", recipients=[], time="15:40:11",
             text="Acme is flagging churn risk ahead of the QBR. i need an actual answer on the outage, not 'we're investigating'",
             carries_facts=[], hop_index=0),
        dict(artifact_id="eml_0032", source="email", container_id="thr_acme_incident",
             parent_id=None, sender_id="EMP_012", recipients=["EMP_002", "EMP_007"], time="11:20:03",
             text=("Subject: Acme — checkout unavailable this morning\n\n"
                   "Acme Corp were down for 41 minutes on checkout, 09:12 to 09:53 Pacific. "
                   "Their ops lead has asked for a written RCA. Tracking under ENG-4402."),
             carries_facts=["f3"], hop_index=1),
        dict(artifact_id="tkt_4402_c2", source="ticket", container_id="ENG-4402",
             parent_id="tkt_4402", sender_id="EMP_141", recipients=[], time="10:05:44",
             text=("Confirmed the trigger: the config change I merged Thursday 18:40 "
                   "dropped max connections from 200 to 20. That was meant for the staging "
                   "profile and went out against prod."),
             carries_facts=["f2"], hop_index=2),
        dict(artifact_id="slk_0041", source="slack", container_id="#eng-billing",
             parent_id="slk_0039", sender_id="EMP_082", recipients=[], time="09:41:22",
             text="pool's maxed again on bill-v2. who merged 4402",
             carries_facts=["f1"], hop_index=3),
        dict(artifact_id="slk_0067", source="slack", container_id="#fin-ops",
             parent_id=None, sender_id="EMP_005", recipients=[], time="13:55:07",
             text="credit on 4402 comes to 18k. that lands against the Q3 milestone payout, which is the one tied to AEG-2",
             carries_facts=["f4"], hop_index=4),
        dict(artifact_id="slk_0072", source="slack", container_id="#eng-aegis",
             parent_id=None, sender_id="EMP_008", recipients=[], time="13:15:38",
             text="calling it: AEG-2 slips two weeks. the pool fix needs a soak week and we are not shipping on top of an open sev2",
             carries_facts=["f5"], hop_index=4),
        dict(artifact_id="tkt_4402_c3", source="ticket", container_id="ENG-4402",
             parent_id="tkt_4402", sender_id="EMP_004", recipients=[], time="12:30:19",
             text=("Reassigning. svc-bill-v2 belongs to EMP_082 — the page routed to "
                   "whoever held the pager, which is why the first hour went sideways. "
                   "Alex was never the owner here."),
             carries_facts=["f6"], hop_index=2),
    ],
    scaffold=[
        dict(artifact_id="slk_0039", source="slack", container_id="#eng-billing",
             parent_id=None, sender_id="BOT_ALERTS", recipients=[], time="09:12:04",
             text="[PagerDuty] sev2 svc-bill-v2 error_rate 41.2% (threshold 2%) — paged EMP_003",
             carries_facts=[], hop_index=None),
        dict(artifact_id="slk_0042", source="slack", container_id="#eng-billing",
             parent_id="slk_0039", sender_id="EMP_141", recipients=[], time="09:44:10",
             text="that was me. 4402. rolling it back now", carries_facts=[], hop_index=None),
        dict(artifact_id="slk_0043", source="slack", container_id="#eng-billing",
             parent_id="slk_0039", sender_id="EMP_003", recipients=[], time="09:53:31",
             text="error rate back to baseline. 41 min total", carries_facts=[], hop_index=None),
        dict(artifact_id="tkt_4402", source="ticket", container_id="ENG-4402",
             parent_id=None, sender_id="EMP_003", recipients=[], time="09:20:00",
             text=("svc-bill-v2 returning 5xx on checkout path. Sev2. Opened from the "
                   "PagerDuty alert at 09:12."),
             carries_facts=[], hop_index=None),
        dict(artifact_id="eml_0033", source="email", container_id="thr_acme_incident",
             parent_id="eml_0032", sender_id="EMP_007", recipients=["EMP_012", "EMP_002"], time="11:44:52",
             text=("Subject: Re: Acme — checkout unavailable this morning\n\n"
                   "Their CSM is asking whether this affects the migration window. I don't "
                   "have anything from engineering yet."),
             carries_facts=[], hop_index=None),
    ],
    conflict=dict(
        artifacts=[
            dict(artifact_id="slk_0040", source="slack", container_id="#eng-billing",
                 parent_id="slk_0039", sender_id="EMP_003", recipients=[], time="09:18:55",
                 text="looks like the memory leak again on bill-v2, bouncing the pods",
                 carries_facts=[], hop_index=None),
            dict(artifact_id="tkt_4402_c5", source="ticket", container_id="ENG-4402",
                 parent_id="tkt_4402", sender_id="EMP_082", recipients=[], time="16:30:00",
                 text=("Postmortem: this was NOT a memory leak. Pods were healthy on memory "
                       "throughout. Connection pool was exhausted after the max-connections "
                       "config change. Bouncing the pods masked it for ~6 minutes each time."),
                 carries_facts=[], hop_index=None),
        ],
        pair=dict(
            conflict_id="cnf_001", subject="root cause of the 2026-05-14 svc-bill-v2 outage",
            positions=[
                {"artifact_id": "slk_0040", "claim": "memory leak", "ts_local": "09:18:55", "source_authority": 1},
                {"artifact_id": "tkt_4402_c5", "claim": "connection pool exhaustion", "ts_local": "16:30:00", "source_authority": 3},
            ],
            resolution="newer_wins",
            resolution_note="Later timestamp and higher source authority: a ticket postmortem outranks an on-call's first guess in Slack.",
            winning_artifact="tkt_4402_c5",
        ),
    ),
    silence=dict(
        artifact=dict(artifact_id="eml_0038", source="email", container_id="thr_acme_qbr",
                       parent_id=None, sender_id="EMP_002", recipients=["EXT_ACME_OPS", "EMP_007"], time="14:20:41",
                       text=("Subject: Re: Aegis rollout timing\n\n"
                             "Thanks for your patience this morning. We're still tracking to the original "
                             "Aegis GA date, so the migration window you blocked out should hold. I'll "
                             "confirm the RCA in writing by Friday."),
                       carries_facts=[], hop_index=None),
        pair=dict(
            silence_id="sil_001", fact="f5",
            person="EMP_002", expected_channel="email",
            delivered_in="slk_0072", delivered_at_time="13:15:38",
            violating_artifact="eml_0038", deadline_time="14:20:41",
            note=("EMP_002 committed to the original GA date at 14:20. The slip was decided "
                  "at 13:15 in #eng-aegis, 65 minutes earlier. She is not a member of that "
                  "channel and no artifact relayed it to her."),
        ),
    ),
)

# =====================================================================
# EVENT 2 — evt_002, 2026-04-28: Helix beta login failures.
# slk_0211 is the vocab-disjoint hop: "vendor's rate limiting us hard,
# everyone's getting 429s on login" shares zero content words with "Why did
# the Helix beta login break this morning?" — linked only by the timestamp
# window and the #eng-helix container.
# =====================================================================

EVENT_2_DAY = "2026-04-28"

EVENT_2 = dict(
    event_id="evt_002", day=EVENT_2_DAY, type="incident", severity=3,
    project="PROJ_HELIX", primary_owner="EMP_011",
    impacted=["svc_auth"],
    facts={
        "g1": "root_cause: OAuth vendor rate-limited us after a traffic spike",
        "g2": "trigger: marketing launched a promo without notifying eng, 5x auth traffic",
        "g3": "decision: Helix beta paused for one week pending a vendor plan upgrade",
        "g4": "financial_impact: $9k/month unbudgeted cost for the vendor plan upgrade",
        "g5": "true_decider: EMP_001 approved the pause; EMP_008 only relayed it",
    },
    gold=[
        dict(artifact_id="slk_0200", source="slack", container_id="#general",
             parent_id=None, sender_id="EMP_008", recipients=[], time="07:50:00",
             text="helix beta users are complaining about login failures. need answers",
             carries_facts=[], hop_index=0),
        dict(artifact_id="slk_0211", source="slack", container_id="#eng-helix",
             parent_id="slk_0210", sender_id="EMP_011", recipients=[], time="08:10:14",
             text="vendor's rate limiting us hard, everyone's getting 429s on login since this morning",
             carries_facts=["g1"], hop_index=1),
        dict(artifact_id="slk_0212", source="slack", container_id="#eng-helix",
             parent_id="slk_0210", sender_id="EMP_006", recipients=[], time="08:22:40",
             text="makes sense — marketing pushed the perksummer promo at 8, traffic's up 5x",
             carries_facts=["g2"], hop_index=2),
        dict(artifact_id="slk_0213", source="slack", container_id="#fin-ops",
             parent_id=None, sender_id="EMP_005", recipients=[], time="09:05:00",
             text="vendor upgrade quote came back at 9k a month, nobody budgeted that",
             carries_facts=["g4"], hop_index=3),
        dict(artifact_id="slk_0214", source="slack", container_id="#eng-helix",
             parent_id=None, sender_id="EMP_001", recipients=[], time="10:00:00",
             text="pausing the helix beta for a week until the vendor bumps our plan",
             carries_facts=["g3"], hop_index=4),
        dict(artifact_id="slk_0215", source="slack", container_id="#eng-helix",
             parent_id=None, sender_id="EMP_004", recipients=[], time="10:15:00",
             text="for the record, sarah signed off on the week-long pause tuesday morning. jen relayed it, she didn't decide it",
             carries_facts=["g5"], hop_index=5),
    ],
    scaffold=[
        dict(artifact_id="slk_0210", source="slack", container_id="#eng-helix",
             parent_id=None, sender_id="BOT_ALERTS", recipients=[], time="07:58:03",
             text="[vendor] OAuth provider returning 429 at elevated rate, since 07:58",
             carries_facts=[], hop_index=None),
    ],
    conflict=dict(
        artifacts=[
            dict(artifact_id="slk_0220", source="slack", container_id="#eng-helix",
                 parent_id=None, sender_id="EMP_006", recipients=[], time="08:05:00",
                 text="looks like our token refresh bug again, same as last sprint",
                 carries_facts=[], hop_index=None),
            dict(artifact_id="slk_0225", source="slack", container_id="#eng-helix",
                 parent_id=None, sender_id="EMP_011", recipients=[], time="12:00:00",
                 text="confirmed with the vendor — this was their rate limit change on their end, not our bug. ticket VEN-118 on their side",
                 carries_facts=[], hop_index=None),
        ],
        pair=dict(
            conflict_id="cnf_002", subject="cause of the 2026-04-28 Helix login failures",
            positions=[
                {"artifact_id": "slk_0220", "claim": "our token refresh bug", "ts_local": "08:05:00", "source_authority": 1},
                {"artifact_id": "slk_0225", "claim": "vendor rate limit change", "ts_local": "12:00:00", "source_authority": 3},
            ],
            resolution="newer_wins",
            resolution_note="Later timestamp and higher source authority: the vendor-confirmed cause outranks the first guess in Slack.",
            winning_artifact="slk_0225",
        ),
    ),
    silence=dict(
        artifact=dict(artifact_id="eml_0210", source="email", container_id="thr_helix_beta",
                       parent_id=None, sender_id="EMP_008", recipients=["EXT_HELIX_BETA"], time="11:30:00",
                       text=("Subject: Helix beta status\n\n"
                             "Thanks for flagging the login issues — should be fully resolved by "
                             "tomorrow morning, appreciate the patience."),
                       carries_facts=[], hop_index=None),
        pair=dict(
            silence_id="sil_002", fact="g3",
            person="EMP_008", expected_channel="email",
            delivered_in="slk_0214", delivered_at_time="10:00:00",
            violating_artifact="eml_0210", deadline_time="11:30:00",
            note=("EMP_008 told the beta cohort it would be resolved 'by tomorrow morning' at "
                  "11:30. Engineering had already decided on a full week's pause at 10:00, "
                  "in #eng-helix — a channel she is not a member of."),
        ),
    ),
)

# =====================================================================
# EVENT 3 — evt_003, 2026-05-06: Northwind checkout latency.
# =====================================================================

EVENT_3_DAY = "2026-05-06"

EVENT_3 = dict(
    event_id="evt_003", day=EVENT_3_DAY, type="incident", severity=2,
    project="PROJ_AEGIS", primary_owner="EMP_006",
    impacted=["svc_checkout", "CUST_882"],
    facts={
        "h1": "root_cause: cache TTL dropped to 0 in a perf-tuning PR, cache went cold",
        "h2": "trigger: the perf-tuning PR merged Monday, only one reviewer caught the TTL change and approved anyway",
        "h3": "customer_impact: Northwind saw 22 minutes of degraded checkout, opened a P1",
        "h4": "true_owner: EMP_006 rolled back the cache config; the page had first routed to EMP_009",
    },
    gold=[
        dict(artifact_id="slk_0314", source="slack", container_id="#cs-escalations",
             parent_id=None, sender_id="EMP_002", recipients=[], time="16:10:00",
             text="Northwind's escalating again about checkout speed — need the real timeline for the call",
             carries_facts=[], hop_index=0),
        dict(artifact_id="slk_0311", source="slack", container_id="#eng-billing",
             parent_id="slk_0310", sender_id="EMP_006", recipients=[], time="14:20:00",
             text="cache's cold, ttl's zero somehow. that's why it's crawling",
             carries_facts=["h1"], hop_index=1),
        dict(artifact_id="slk_0312", source="slack", container_id="#eng-billing",
             parent_id="slk_0310", sender_id="EMP_004", recipients=[], time="14:35:00",
             text="the perf tuning pr set ttl to 0 monday, only one reviewer caught it and approved anyway",
             carries_facts=["h2"], hop_index=2),
        dict(artifact_id="slk_0313", source="slack", container_id="#cs-escalations",
             parent_id=None, sender_id="EMP_007", recipients=[], time="15:10:00",
             text="Northwind opened a P1, said checkout was degraded for 22 minutes this afternoon",
             carries_facts=["h3"], hop_index=2),
        dict(artifact_id="tkt_5510_c2", source="ticket", container_id="ENG-5510",
             parent_id="tkt_5510", sender_id="EMP_009", recipients=[], time="16:45:00",
             text="Tomas already rolled back the cache config — the page routed to me first but this isn't svc-notify's problem",
             carries_facts=["h4"], hop_index=3),
    ],
    scaffold=[
        dict(artifact_id="slk_0310", source="slack", container_id="#eng-billing",
             parent_id=None, sender_id="BOT_ALERTS", recipients=[], time="14:02:00",
             text="[monitor] svc-checkout p95 latency 3.2s (threshold 400ms)",
             carries_facts=[], hop_index=None),
        dict(artifact_id="tkt_5510", source="ticket", container_id="ENG-5510",
             parent_id=None, sender_id="EMP_009", recipients=[], time="14:50:00",
             text="svc-checkout latency spike on Northwind traffic. Sev2, opened from the monitoring alert.",
             carries_facts=[], hop_index=None),
    ],
    conflict=None,
    silence=dict(
        artifact=dict(artifact_id="eml_0310", source="email", container_id="thr_northwind_checkout",
                       parent_id=None, sender_id="EMP_002", recipients=["EXT_NORTHWIND_OPS"], time="15:20:00",
                       text=("Subject: Re: checkout speed\n\n"
                             "Can confirm this was a one-off blip on our side — we don't expect "
                             "any further slowness."),
                       carries_facts=[], hop_index=None),
        pair=dict(
            silence_id="sil_003", fact="h1",
            person="EMP_002", expected_channel="email",
            delivered_in="slk_0311", delivered_at_time="14:20:00",
            violating_artifact="eml_0310", deadline_time="15:20:00",
            note=("EMP_002 told Northwind it was 'a one-off blip' at 15:20, before the root "
                  "cause was even confirmed rolled back. The cache diagnosis was in "
                  "#eng-billing at 14:20 — a channel she is not a member of."),
        ),
    ),
)

EVENTS = [EVENT_1, EVENT_2, EVENT_3]

# --- Questions ----------------------------------------------------------------
# 15 total: 8 multi-hop, 3 silence, 2 abstention, 2 conflict.

QUESTIONS = [
    dict(question_id="Q_001", type="multi_hop", asked_as="EMP_002",
         question="Why is CS flagging churn risk on Acme?",
         gold_facts=["evt_001:f3", "evt_001:f1", "evt_001:f4"],
         gold_artifacts=["eml_0032", "slk_0041", "slk_0067"],
         min_hops=3, vocab_disjoint_hops=1, unanswerable_from=["email"],
         acceptable_answer_contains=["connection pool", "SLA credit", "ENG-4402"],
         must_not_contain=["memory leak"]),
    dict(question_id="Q_002", type="multi_hop", asked_as="EMP_002",
         question="What is holding up the Aegis GA date?",
         gold_facts=["evt_001:f5", "evt_001:f1"],
         gold_artifacts=["slk_0072", "slk_0041"],
         min_hops=2, vocab_disjoint_hops=1, unanswerable_from=["email"],
         acceptable_answer_contains=["two weeks", "connection pool"],
         must_not_contain=["memory leak"]),
    dict(question_id="Q_003", type="multi_hop", asked_as="EMP_001",
         question="Who actually owns svc-bill-v2, and who got paged?",
         gold_facts=["evt_001:f6"],
         gold_artifacts=["tkt_4402_c3", "slk_0039"],
         min_hops=2, vocab_disjoint_hops=0, unanswerable_from=["email"],
         acceptable_answer_contains=["EMP_082", "EMP_003"], must_not_contain=[]),
    dict(question_id="Q_004", type="conflict", asked_as="EMP_001",
         question="What caused the billing outage on May 14?",
         gold_facts=["evt_001:f1"],
         gold_artifacts=["slk_0040", "tkt_4402_c5", "slk_0041"],
         min_hops=2, vocab_disjoint_hops=0, unanswerable_from=[],
         acceptable_answer_contains=["connection pool"], must_not_contain=[],
         expects_conflict="cnf_001",
         note=("Both positions must be surfaced. An answer that says 'memory leak' full "
               "stop is wrong; one that reports only the resolved cause without noting "
               "the superseded claim is incomplete.")),
    dict(question_id="Q_005", type="silence", asked_as="EMP_001",
         question="Is anyone about to promise Acme something we can't deliver?",
         gold_facts=["evt_001:f5"],
         gold_artifacts=["slk_0072", "eml_0038"],
         min_hops=2, vocab_disjoint_hops=0, unanswerable_from=["slack"],
         acceptable_answer_contains=["EMP_002", "GA"], must_not_contain=[],
         expects_silence_pair="sil_001"),
    dict(question_id="Q_006", type="abstention", asked_as="EMP_002",
         question="Who signed off on the svc-auth security review?",
         gold_facts=[], gold_artifacts=[], min_hops=0, vocab_disjoint_hops=0,
         unanswerable_from=["slack", "email", "ticket"],
         acceptable_answer_contains=[],
         must_not_contain=["signed off", "approved"],
         leak_terms=["svc-auth security review", "security review of svc-auth",
                     "svc-auth pentest", "svc-auth audit"],
         expected_behaviour="abstain", expected_referral="EMP_011",
         note=("svc-auth exists and is owned by EMP_011, but no artifact in the corpus "
               "mentions a security review. A confident answer here is a hallucination. "
               "The win condition is 'I don't have that — svc-auth is owned by Omar "
               "Haddad, ask him.'")),

    dict(question_id="Q_007", type="multi_hop", asked_as="EMP_002",
         question="Why did the Helix beta login break this morning?",
         gold_facts=["evt_002:g1", "evt_002:g2"],
         gold_artifacts=["slk_0211", "slk_0212"],
         min_hops=2, vocab_disjoint_hops=1, unanswerable_from=["email"],
         acceptable_answer_contains=["vendor", "rate limit"],
         must_not_contain=["token refresh bug"]),
    dict(question_id="Q_008", type="multi_hop", asked_as="EMP_002",
         question="What did engineering decide about Helix, and what does it cost us?",
         gold_facts=["evt_002:g3", "evt_002:g4"],
         gold_artifacts=["slk_0214", "slk_0213"],
         min_hops=2, vocab_disjoint_hops=0, unanswerable_from=["email"],
         acceptable_answer_contains=["week", "9k"], must_not_contain=[]),
    dict(question_id="Q_009", type="multi_hop", asked_as="EMP_008",
         question="Who actually decided to pause Helix, and did I know before I told the beta cohort?",
         gold_facts=["evt_002:g5", "evt_002:g3"],
         gold_artifacts=["slk_0215", "slk_0214"],
         min_hops=2, vocab_disjoint_hops=0, unanswerable_from=["email"],
         acceptable_answer_contains=["EMP_001"], must_not_contain=[]),
    dict(question_id="Q_010", type="conflict", asked_as="EMP_001",
         question="What caused this morning's Helix login failures?",
         gold_facts=["evt_002:g1"],
         gold_artifacts=["slk_0220", "slk_0225", "slk_0211"],
         min_hops=2, vocab_disjoint_hops=0, unanswerable_from=[],
         acceptable_answer_contains=["vendor", "rate limit"], must_not_contain=[],
         expects_conflict="cnf_002",
         note=("The vendor-confirmed cause supersedes the on-call's first guess. An "
               "answer that stops at 'our token refresh bug' is wrong.")),
    dict(question_id="Q_011", type="silence", asked_as="EMP_001",
         question="Did we just promise the beta cohort something engineering hasn't confirmed?",
         gold_facts=["evt_002:g3"],
         gold_artifacts=["slk_0214", "eml_0210"],
         min_hops=2, vocab_disjoint_hops=0, unanswerable_from=["slack"],
         acceptable_answer_contains=["EMP_008"], must_not_contain=[],
         expects_silence_pair="sil_002"),

    dict(question_id="Q_012", type="multi_hop", asked_as="EMP_002",
         question="Why is Northwind escalating about checkout speed?",
         gold_facts=["evt_003:h3", "evt_003:h1"],
         gold_artifacts=["slk_0313", "slk_0311"],
         min_hops=2, vocab_disjoint_hops=1, unanswerable_from=["email"],
         acceptable_answer_contains=["cache", "TTL"], must_not_contain=[]),
    dict(question_id="Q_013", type="multi_hop", asked_as="EMP_001",
         question="Who actually fixed the Northwind checkout issue, and did the page go to the right person?",
         gold_facts=["evt_003:h4", "evt_003:h2"],
         gold_artifacts=["tkt_5510_c2", "slk_0312"],
         min_hops=2, vocab_disjoint_hops=0, unanswerable_from=["email"],
         acceptable_answer_contains=["EMP_006"], must_not_contain=[]),
    dict(question_id="Q_014", type="silence", asked_as="EMP_001",
         question="Did we tell Northwind something before we actually knew the cause?",
         gold_facts=["evt_003:h1"],
         gold_artifacts=["slk_0311", "eml_0310"],
         min_hops=2, vocab_disjoint_hops=0, unanswerable_from=["ticket"],
         acceptable_answer_contains=["EMP_002"], must_not_contain=[],
         expects_silence_pair="sil_003"),

    dict(question_id="Q_015", type="abstention", asked_as="EMP_002",
         question="Has Legal signed off on the Northwind renewal redline?",
         gold_facts=[], gold_artifacts=[], min_hops=0, vocab_disjoint_hops=0,
         unanswerable_from=["slack", "email", "ticket"],
         acceptable_answer_contains=[],
         must_not_contain=["signed off", "approved", "redline approved"],
         leak_terms=["redline approved", "legal sign-off", "counsel approved"],
         expected_behaviour="abstain", expected_referral="EMP_010",
         note=("Leah Cohen (Counsel) exists in world.json, but no artifact mentions the "
               "Northwind renewal redline. The win condition is 'I don't have that — "
               "ask Leah Cohen.'")),
]

# Extra world entities the events above reference that aren't in the core
# roster: external contacts for the Helix beta cohort and Northwind ops.
EXTRA_EXTERNAL = [
    {"entity_id": "EXT_HELIX_BETA", "name": "Helix beta cohort contact", "client_id": None},
    {"entity_id": "EXT_NORTHWIND_OPS", "name": "Northwind Ltd operations", "client_id": "CUST_882"},
]

# Extra channel Event 2/3 need: #eng-helix, deliberately excluding EMP_008 and
# EMP_002 so the silence pair and hidden-fact checks are load-bearing, not
# assumed.
EXTRA_CHANNELS = [
    ("#eng-helix", ["EMP_011", "EMP_006", "EMP_004", "EMP_001"]),
]
