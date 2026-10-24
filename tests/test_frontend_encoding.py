"""Guard against visible question-mark mojibake in frontend sources."""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORRUPT = re.compile("[A-Za-z]" + chr(92) + "?[a-z]")
# Syntax false positives from ternary operators; kept explicit and narrow.
WHITELIST = ("signal?escapeHtml", "edge_gross_pct==null?null",
             "fee==null?null", "textContent=error?errorText",
             "spacing_pct==null||fee==null?null", "spacing?number",
             "lastPreview?lastPreview", "summary?symbol=",
             "ledger?page=")

def _assert_frontend_sources_clean(paths, root):
    for path in paths:
        if path == root / "frontend" / "vendor" or root / "frontend" / "vendor" in path.parents:
            continue
        if path.suffix not in {".html", ".js"}:
            continue
        source = path.read_text(encoding="utf-8")
        remaining = source
        for allowed in WHITELIST:
            remaining = remaining.replace(allowed, allowed.replace("?", " "))
        assert not CORRUPT.search(remaining), f"possible mojibake in {path.relative_to(root)}"


def test_frontend_user_text_has_no_question_mark_mojibake():
    paths = {ROOT / "frontend/index.html", *(ROOT / "frontend").rglob("*")}
    _assert_frontend_sources_clean(paths, ROOT)


def test_mojibake_guard_rejects_question_mark_mutation_in_owned_frontend_file(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    source = frontend / "phase29c.js"
    source.write_text("const label = 'señal';\n", encoding="utf-8", newline="\n")
    source.write_bytes(source.read_bytes().replace("ñ".encode("utf-8"), b"?"))
    with pytest.raises(AssertionError) as error:
        _assert_frontend_sources_clean({source}, tmp_path)
    assert "possible mojibake in frontend" in str(error.value)
