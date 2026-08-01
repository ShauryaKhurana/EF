"""COOless.

Force UTF-8 on stdout/stderr with replacement rather than failure. The corpus and any
text a judge pastes in will contain emoji, smart quotes and non-Latin characters; the
default Windows console codepage raises UnicodeEncodeError on those, which would kill a
run mid-print. Degrading a glyph is acceptable, crashing the demo is not.
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")
