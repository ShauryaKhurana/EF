"""The one incident narrative, hand-authored. Everything else is texture.

DATASET_DESIGN §6 H+0:30: "pick your demo narrative — one incident chain you
personally believe in, written out longhand as six facts across four channels.
Everything else is generated; this one you own."

The fact partition (§P1) is enforced by validate.py, not hoped for:
  - every fact appears in exactly ONE artifact
  - no artifact carries more than ceil(6/3) = 2 facts
  - for every container c, facts(c) is a PROPER subset of F
  - at least one fact lands where EMP_002 (the asked_as persona) cannot see it

The vocab-disjoint hop (§P2) is slk_0041: "pool's maxed again on bill-v2. who
merged 4402" shares zero content words with "Why is CS flagging churn risk on
Acme?" — the only link is the ticket id 4402 and the service alias bill-v2.
Cosine similarity cannot make that jump. Entity traversal can.
"""

INCIDENT_DAY = "2026-05-14"
TZ = "-07:00"

# --- The six atomic facts ----------------------------------------------------

FACTS = {
    "f1": "root_cause: connection-pool exhaustion in svc-bill-v2",
    "f2": "trigger: config change merged Thu 18:40 by EMP_141",
    "f3": "customer_impact: Acme Corp saw 41min checkout downtime",
    "f4": "financial_impact: $18k SLA credit owed, hits Q3 milestone payout",
    "f5": "schedule_impact: Aegis GA slips 2 weeks",
    "f6": "true_owner: EMP_082, not the on-call who was paged",
}

# --- The gold chain ----------------------------------------------------------
# Each entry: (artifact_id, source, container_id, parent_id, sender, recipients,
#              time, text, carries_facts, hop_index)

GOLD = [
    # --- Entry point. Contains the question's own words, carries NO fact. ---
    # This is what hop 0 finds. It is deliberately not a gold artifact: knowing
    # CS is worried tells you nothing about why.
    dict(
        artifact_id="slk_0088", source="slack", container_id="#cs-escalations",
        parent_id=None, sender_id="EMP_002", recipients=[],
        time="15:40:11",
        text="Acme is flagging churn risk ahead of the QBR. i need an actual answer on the outage, not 'we're investigating'",
        carries_facts=[], hop_index=0,
    ),

    # --- f3: customer impact. The only gold artifact naming Acme. ---
    dict(
        artifact_id="eml_0032", source="email", container_id="thr_acme_incident",
        parent_id=None, sender_id="EMP_012", recipients=["EMP_002", "EMP_007"],
        time="11:20:03",
        text=("Subject: Acme — checkout unavailable this morning\n\n"
              "Acme Corp were down for 41 minutes on checkout, 09:12 to 09:53 Pacific. "
              "Their ops lead has asked for a written RCA. Tracking under ENG-4402."),
        carries_facts=["f3"], hop_index=1,
    ),

    # --- f2: trigger. Links the ticket to the config change and to EMP_141. ---
    dict(
        artifact_id="tkt_4402_c2", source="ticket", container_id="ENG-4402",
        parent_id="tkt_4402", sender_id="EMP_141", recipients=[],
        time="10:05:44",
        text=("Confirmed the trigger: the config change I merged Thursday 18:40 "
              "dropped max connections from 200 to 20. That was meant for the staging "
              "profile and went out against prod."),
        carries_facts=["f2"], hop_index=2,
    ),

    # --- f1: ROOT CAUSE. The vocab-disjoint hop, buried as a thread reply. ---
    # Zero content-word overlap with the question. Linked only by 4402 + bill-v2.
    dict(
        artifact_id="slk_0041", source="slack", container_id="#eng-billing",
        parent_id="slk_0039", sender_id="EMP_082", recipients=[],
        time="09:41:22",
        text="pool's maxed again on bill-v2. who merged 4402",
        carries_facts=["f1"], hop_index=3,
    ),

    # --- f4: financial impact. Deliberately does NOT say "Acme". ---
    # If it did, grep on the question's words would find 2 of 3 gold artifacts
    # and the multi-hop claim would be decorative.
    dict(
        artifact_id="slk_0067", source="slack", container_id="#fin-ops",
        parent_id=None, sender_id="EMP_005", recipients=[],
        time="13:55:07",
        text="credit on 4402 comes to 18k. that lands against the Q3 milestone payout, which is the one tied to AEG-2",
        carries_facts=["f4"], hop_index=4,
    ),

    # --- f5: schedule impact. 65 min before Priya commits to the old date. ---
    dict(
        artifact_id="slk_0072", source="slack", container_id="#eng-aegis",
        parent_id=None, sender_id="EMP_008", recipients=[],
        time="13:15:38",
        text="calling it: AEG-2 slips two weeks. the pool fix needs a soak week and we are not shipping on top of an open sev2",
        carries_facts=["f5"], hop_index=4,
    ),

    # --- f6: true owner. ---
    dict(
        artifact_id="tkt_4402_c3", source="ticket", container_id="ENG-4402",
        parent_id="tkt_4402", sender_id="EMP_004", recipients=[],
        time="12:30:19",
        text=("Reassigning. svc-bill-v2 belongs to EMP_082 — the page routed to "
              "whoever held the pager, which is why the first hour went sideways. "
              "Alex was never the owner here."),
        carries_facts=["f6"], hop_index=2,
    ),
]

# --- The conflict pair -------------------------------------------------------
# Resolution rule (declared, per DATASET_DESIGN §5-Agent-B): later timestamp AND
# higher source authority wins. A ticket postmortem outranks a Slack guess.

CONFLICT = [
    dict(
        artifact_id="slk_0040", source="slack", container_id="#eng-billing",
        parent_id="slk_0039", sender_id="EMP_003", recipients=[],
        time="09:18:55",
        text="looks like the memory leak again on bill-v2, bouncing the pods",
        carries_facts=[], hop_index=None,
    ),
    dict(
        artifact_id="tkt_4402_c5", source="ticket", container_id="ENG-4402",
        parent_id="tkt_4402", sender_id="EMP_082", recipients=[],
        time="16:30:00",
        text=("Postmortem: this was NOT a memory leak. Pods were healthy on memory "
              "throughout. Connection pool was exhausted after the max-connections "
              "config change. Bouncing the pods masked it for ~6 minutes each time."),
        carries_facts=[], hop_index=None,
    ),
]

CONFLICT_PAIR = dict(
    conflict_id="cnf_001",
    subject="root cause of the 2026-05-14 svc-bill-v2 outage",
    positions=[
        {"artifact_id": "slk_0040", "claim": "memory leak", "ts_local": "09:18:55",
         "source_authority": 1},
        {"artifact_id": "tkt_4402_c5", "claim": "connection pool exhaustion",
         "ts_local": "16:30:00", "source_authority": 3},
    ],
    resolution="newer_wins",
    resolution_note=("Later timestamp and higher source authority: a ticket postmortem "
                     "outranks an on-call's first guess in Slack."),
    winning_artifact="tkt_4402_c5",
)

# --- The silence pair --------------------------------------------------------
# EMP_002 (Priya) commits to the original GA date at 14:20. Engineering had
# already slipped it at 13:15 in #eng-aegis — a channel she is not in. No
# artifact delivers f5 to her before the deadline. Validated, not assumed.

SILENCE_ARTIFACT = dict(
    artifact_id="eml_0038", source="email", container_id="thr_acme_qbr",
    parent_id=None, sender_id="EMP_002", recipients=["EXT_ACME_OPS", "EMP_007"],
    time="14:20:41",
    text=("Subject: Re: Aegis rollout timing\n\n"
          "Thanks for your patience this morning. We're still tracking to the original "
          "Aegis GA date, so the migration window you blocked out should hold. I'll "
          "confirm the RCA in writing by Friday."),
    carries_facts=[], hop_index=None,
)

SILENCE_PAIR = dict(
    silence_id="sil_001",
    fact="f5",
    fact_text=FACTS["f5"],
    person="EMP_002",
    deadline=f"{INCIDENT_DAY}T14:20:41{TZ}",
    expected_channel="email",
    delivered_in="slk_0072",
    delivered_at=f"{INCIDENT_DAY}T13:15:38{TZ}",
    violating_artifact="eml_0038",
    note=("EMP_002 committed to the original GA date at 14:20. The slip was decided "
          "at 13:15 in #eng-aegis, 65 minutes earlier. She is not a member of that "
          "channel and no artifact relayed it to her."),
)

# --- Thread scaffolding the gold artifacts hang off --------------------------
# slk_0039 is the top-level message; the root cause is a REPLY under it (§P5
# thread burial). A retriever that chunks flat and ignores parent_id will pull
# the alert and miss the reply that matters.

SCAFFOLD = [
    dict(
        artifact_id="slk_0039", source="slack", container_id="#eng-billing",
        parent_id=None, sender_id="BOT_ALERTS", recipients=[],
        time="09:12:04",
        text="[PagerDuty] sev2 svc-bill-v2 error_rate 41.2% (threshold 2%) — paged EMP_003",
        carries_facts=[], hop_index=None,
    ),
    dict(
        artifact_id="slk_0042", source="slack", container_id="#eng-billing",
        parent_id="slk_0039", sender_id="EMP_141", recipients=[],
        time="09:44:10",
        text="that was me. 4402. rolling it back now",
        carries_facts=[], hop_index=None,
    ),
    dict(
        artifact_id="slk_0043", source="slack", container_id="#eng-billing",
        parent_id="slk_0039", sender_id="EMP_003", recipients=[],
        time="09:53:31",
        text="error rate back to baseline. 41 min total",
        carries_facts=[], hop_index=None,
    ),
    dict(
        artifact_id="tkt_4402", source="ticket", container_id="ENG-4402",
        parent_id=None, sender_id="EMP_003", recipients=[],
        time="09:20:00",
        text=("svc-bill-v2 returning 5xx on checkout path. Sev2. Opened from the "
              "PagerDuty alert at 09:12."),
        carries_facts=[], hop_index=None,
    ),
    dict(
        artifact_id="eml_0033", source="email", container_id="thr_acme_incident",
        parent_id="eml_0032", sender_id="EMP_007", recipients=["EMP_012", "EMP_002"],
        time="11:44:52",
        text=("Subject: Re: Acme — checkout unavailable this morning\n\n"
              "Their CSM is asking whether this affects the migration window. I don't "
              "have anything from engineering yet."),
        carries_facts=[], hop_index=None,
    ),
]
