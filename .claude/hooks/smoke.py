#!/usr/bin/env python3
"""
PostToolUse (Edit|Write) on .py: syntax check only (fast).
Then Stop hook reminds Claude to run the smoke test if code changed.
"""
import json, py_compile, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
p = str((d.get("tool_input") or {}).get("file_path", ""))
if not p.endswith(".py"):
    sys.exit(0)
try:
    py_compile.compile(p, doraise=True)
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR in {p} — fix now:\n{e}", file=sys.stderr); sys.exit(2)
except FileNotFoundError:
    pass
sys.exit(0)
