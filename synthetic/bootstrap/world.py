"""World definition for the bootstrap corpus. Hand-authored, no randomness.

~15 employees (with a deliberate first-name collision), 2 projects with drift
aliases, 4 services, 2 clients, 5 channels with declared membership.

Channel membership is load-bearing, not decoration: it is what makes the silence
pair checkable and what makes the single-channel ablation mean something.
EMP_002 — the persona the demo question is asked as — is deliberately absent
from #eng-billing, #fin-ops and #eng-aegis, which is where 3 of the 6 facts live.
"""

EMPLOYEES = [
    # id, name, role, team, manager, tz, aliases
    ("EMP_001", "Sarah Kim", "VP Engineering", "eng", None, "America/Los_Angeles",
     ["sarah", "@sarah", "sarah.kim@company.com", "skim"]),
    ("EMP_002", "Priya Raman", "Customer Success Lead", "cs", "EMP_001", "America/New_York",
     ["priya", "pri", "@pr", "priya.raman@company.com"]),
    ("EMP_003", "Alex Torres", "SRE (on-call)", "sre", "EMP_004", "America/Los_Angeles",
     ["alex", "@alex", "alex.torres@company.com", "atorres"]),
    ("EMP_004", "Ken Nakamura", "Engineering Manager", "eng", "EMP_001", "America/Los_Angeles",
     ["ken", "@ken", "ken.nakamura@company.com"]),
    ("EMP_005", "Sofia Marin", "Finance Director", "finance", None, "America/New_York",
     ["sofia", "@sofia", "sofia.marin@company.com"]),
    ("EMP_006", "Tomas Reyes", "Engineer", "eng", "EMP_004", "Europe/Lisbon",
     ["tomas", "@tomas", "tomas.reyes@company.com"]),
    ("EMP_007", "Aisha Bello", "Customer Success Manager", "cs", "EMP_002", "America/New_York",
     ["aisha", "@aisha", "aisha.bello@company.com"]),
    ("EMP_008", "Jen Park", "Product Manager", "product", "EMP_001", "America/Los_Angeles",
     ["jen", "@jen", "jen.park@company.com"]),
    ("EMP_009", "Raj Patel", "SRE", "sre", "EMP_004", "Asia/Kolkata",
     ["raj", "@raj", "raj.patel@company.com"]),
    ("EMP_010", "Leah Cohen", "Counsel", "legal", None, "America/New_York",
     ["leah", "@leah", "leah.cohen@company.com"]),
    ("EMP_011", "Omar Haddad", "Engineer", "eng", "EMP_004", "Europe/Berlin",
     ["omar", "@omar", "omar.haddad@company.com"]),
    ("EMP_012", "Nina Volkov", "Support Engineer", "support", "EMP_002", "Europe/Berlin",
     ["nina", "@nina", "nina.volkov@company.com"]),
    # --- deliberate first-name collision: two Danas, both plausibly "@dana" ---
    ("EMP_141", "Dana Chen", "Engineer", "eng", "EMP_004", "America/Los_Angeles",
     ["dana", "dana chen", "@dana", "dchen", "dana.chen@company.com"]),
    ("EMP_143", "Dana Whitfield", "Financial Analyst", "finance", "EMP_005", "America/New_York",
     ["dana", "dana whitfield", "@dana", "dwhitfield", "dana.whitfield@company.com"]),
    ("EMP_082", "Marcus Webb", "Staff Engineer", "eng", "EMP_004", "America/Los_Angeles",
     ["marcus", "@marcus", "marcus.webb@company.com", "mwebb"]),
]

# Codename drift (§P5, highest-value noise item — breaks naive string matching)
PROJECTS = [
    ("PROJ_AEGIS", "Project Aegis", "EMP_008",
     ["aegis", "Aegis", "AEG-2", "the billing thing", "billing rewrite"]),
    ("PROJ_HELIX", "Project Helix", "EMP_011",
     ["helix", "Helix", "HLX", "the auth revamp"]),
]

SERVICES = [
    ("svc_bill", "svc-bill-v2", "EMP_082", "PROJ_AEGIS", ["bill-v2", "billing service", "svc-bill"]),
    ("svc_auth", "svc-auth", "EMP_011", "PROJ_HELIX", ["auth service", "svc-auth-v1"]),
    ("svc_checkout", "svc-checkout", "EMP_006", "PROJ_AEGIS", ["checkout service", "co-svc"]),
    ("svc_notify", "svc-notify", "EMP_009", None, ["notify", "notification service"]),
]

CLIENTS = [
    ("CUST_991", "Acme Corp", 2_400_000, "99.9%", "EMP_007",
     ["Acme", "ACME", "Acme Corp", "acme"]),
    ("CUST_882", "Northwind Ltd", 600_000, "99.5%", "EMP_007",
     ["Northwind", "northwind", "NW"]),
]

# EMP_002 is NOT in #eng-billing, #fin-ops, or #eng-aegis. That is the point.
CHANNELS = [
    ("#eng-billing", ["EMP_082", "EMP_141", "EMP_003", "EMP_004", "EMP_006", "EMP_009"]),
    ("#fin-ops", ["EMP_005", "EMP_143", "EMP_001", "EMP_010"]),
    ("#eng-aegis", ["EMP_082", "EMP_141", "EMP_004", "EMP_008", "EMP_001"]),
    ("#cs-escalations", ["EMP_002", "EMP_007", "EMP_012", "EMP_001"]),
    ("#general", [e[0] for e in EMPLOYEES]),
]


def build_world() -> dict:
    return {
        "generated_by": "synthetic/bootstrap/generate.py",
        "seed": 20260514,
        "employees": [
            {
                "employee_id": eid, "name": name, "role": role, "team": team,
                "manager_id": mgr, "timezone": tz, "aliases": aliases,
                "email": f"{name.split()[0].lower()}.{name.split()[1].lower()}@company.com",
                "slack_handle": f"@{name.split()[0].lower()}",
            }
            for eid, name, role, team, mgr, tz, aliases in EMPLOYEES
        ],
        "projects": [
            {"project_id": pid, "name": name, "owner_id": owner, "aliases": aliases}
            for pid, name, owner, aliases in PROJECTS
        ],
        "services": [
            {"service_id": sid, "name": name, "owner_id": owner,
             "project_id": proj, "aliases": aliases}
            for sid, name, owner, proj, aliases in SERVICES
        ],
        "clients": [
            {"client_id": cid, "name": name, "contract_value_usd": value,
             "sla": sla, "csm_id": csm, "aliases": aliases}
            for cid, name, value, sla, csm, aliases in CLIENTS
        ],
        "channels": [
            {"channel_id": ch, "members": members} for ch, members in CHANNELS
        ],
        "external": [
            {"entity_id": "EXT_ACME_OPS", "name": "Acme Corp operations",
             "client_id": "CUST_991"}
        ],
    }
