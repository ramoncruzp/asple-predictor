"""Guard against visible question-mark mojibake in frontend sources."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CORRUPT = re.compile("[A-Za-z]" + chr(92) + "?[a-z]")
# Syntax false positives from ternary operators; kept explicit and narrow.
WHITELIST = ("signal?escapeHtml", "edge_gross_pct==null?null",
             "fee==null?null", "textContent=error?errorText",
             "spacing_pct==null||fee==null?null", "spacing?number",
             "lastPreview?lastPreview")

def test_frontend_user_text_has_no_question_mark_mojibake():
    for path in (ROOT / "frontend").rglob("*"):
        if path.suffix not in {".html", ".js"}:
            continue
        source = path.read_text(encoding="utf-8")
        remaining = source
        for allowed in WHITELIST:
            remaining = remaining.replace(allowed, allowed.replace("?", " "))
        assert not CORRUPT.search(remaining), f"possible mojibake in {path.relative_to(ROOT)}"
