#!/usr/bin/env python3
"""
PreToolUse (Edit|Write|MultiEdit): blocks the two things that lose this
hackathon — faked outputs and silent failure — plus secret leaks.

exit 2 = blocked, stderr shown to Claude.
Deliberately narrow: false positives waste time we don't have.
"""
import json
import re
import sys

# Patterns that mean "this output isn't real"
FAKE = [
    (r"\bdemo_mode\b|\bDEMO_MODE\b", "demo_mode flag — judges pick the input; there is no demo path"),
    (r"\bMOCK_|\bmock_response\b|\bfake_|\bFAKE_", "mocked/fake data"),
    (r"#\s*(TODO|FIXME).*(demo|hardcode|fake)", "placeholder left in the demo path"),
    (r"return\s+[\"'](Success|Done|OK)[\"']\s*#", "hardcoded success return"),
]

# Silent failure
SILENT = [
    (r"except[^:\n]*:\s*\n\s*pass\b", "bare `except: pass` — fail loud and specific instead"),
    (r"except[^:\n]*:\s*\n\s*(continue|return\s+None)\s*$", "silently swallowed exception — log the reason"),
]

SECRETS = [
    (r"(xox[baprs]-[A-Za-z0-9-]{10,})", "hardcoded Slack token"),
    (r"(sk-[A-Za-z0-9]{20,})", "hardcoded API key"),
    (r"AIza[0-9A-Za-z_-]{30,}", "hardcoded Google API key"),
]


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    ti = d.get("tool_input") or {}
    text = str(ti.get("content", "")) + str(ti.get("new_string", "")) + str(ti.get("new_str", ""))
    path = str(ti.get("file_path", ""))
    if not text:
        sys.exit(0)
    # allow fixtures/generators to contain "fake" wording
    if re.search(r"(test|fixture|synthetic|generate_data|seed)", path, re.I):
        checks = SECRETS
    else:
        checks = FAKE + SILENT + SECRETS
    for pat, why in checks:
        if re.search(pat, text, re.MULTILINE):
            print(f"BLOCKED in {path}: {why}. This loses the demo — write the real "
                  f"thing, or surface the failure explicitly.", file=sys.stderr)
            sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
