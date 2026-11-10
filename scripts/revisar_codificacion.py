#!/usr/bin/env python3
"""Revisa errores de codificación en archivos fuente y Markdown."""
from __future__ import annotations

import re
import sys
from pathlib import Path

WORD_QUESTION = re.compile(r"[^\W\d_]\?[^\W\d_]", re.UNICODE)
ISOLATED_HEADING_QUESTION = re.compile(r"^#+ .* \? ")
URL = re.compile(r"https?://\S+")
BAD_TEXT = (chr(0x00c3), chr(0x00c2), chr(0x00e2) + chr(0x20ac), chr(0xfffd), chr(0xfeff))


def _source_fragments(path, text):
    """Devuelve solo cadenas y comentarios para evitar falsos positivos de codigo."""
    if path.suffix.lower() == ".py":
        import io
        import tokenize
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type in {tokenize.STRING, tokenize.COMMENT}:
                yield token.start[0], token.string
    elif path.suffix.lower() == ".js":
        # Lexer pequeno: ignora codigo (incluidos ternarios) y extrae cadenas/comentarios.
        i = 0
        line = 1
        n = len(text)
        while i < n:
            if text.startswith("//", i):
                start, end = i + 2, text.find("\n", i + 2)
                if end < 0: end = n
                yield line, text[start:end]
                line += text[i:end].count("\n"); i = end
            elif text.startswith("/*", i):
                start, end = i + 2, text.find("*/", i + 2)
                if end < 0: end = n
                yield line, text[start:end]
                line += text[i:end + 2].count("\n"); i = end + 2
            elif text[i] in "\"'`":
                quote = text[i]; start = i + 1; i += 1; escaped = False
                while i < n:
                    char = text[i]
                    if char == "\n": line += 1
                    if escaped: escaped = False
                    elif char == "\\": escaped = True
                    elif char == quote: break
                    i += 1
                yield line, text[start:i]
                i += 1
            else:
                if text[i] == "\n": line += 1
                i += 1


def inspect(path):
    path = Path(path)
    if path.suffix.lower() not in {".md", ".py", ".js"}:
        return []
    raw = path.read_bytes()
    findings = []
    if raw.startswith(b"\xef\xbb\xbf"):
        findings.append((1, "BOM", "UTF-8 BOM"))
    text = raw.decode("utf-8")
    if path.suffix.lower() == ".md":
        in_fence = False
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence: continue
            visible = URL.sub("", line)
            if ISOLATED_HEADING_QUESTION.search(visible):
                findings.append((number, "pregunta_aislada_titulo", line[:180]))
            for match in WORD_QUESTION.finditer(visible):
                findings.append((number, "letra-?-letra", line[max(0, match.start()-24):match.end()+24]))
            for pattern in BAD_TEXT:
                if pattern in visible:
                    findings.append((number, repr(pattern), line[:180]))
        return findings
    # Estos tests contienen codigo de ejemplo o cadenas corruptas a proposito.
    intentional_fixtures = {"tests/test_frontend_encoding.py",
        "tests/test_frontend_scanner.py", "tests/test_vol_mojibake.py",
        "tests/test_intelligence_api.py"}
    normalized = path.as_posix().lstrip("./")
    if normalized in intentional_fixtures:
        return findings
    for number, fragment in _source_fragments(path, text):
        visible = URL.sub("", fragment)
        for match in WORD_QUESTION.finditer(visible):
            # Los parametros URL y el texto de expresiones de prueba no son errores de tilde.
            if re.match(r"\?[^\s=&]+(?:=|&)", visible[match.start()+1:]):
                continue
            findings.append((number, "letra-?-letra", visible[max(0, match.start()-24):match.end()+24]))
        for pattern in BAD_TEXT:
            if pattern in visible:
                findings.append((number, repr(pattern), visible[:180]))
    return findings

def main(argv=None):
    findings = []
    names = argv if argv is not None else sys.argv[1:]
    if not names:
        import subprocess
        tracked = subprocess.run(["git", "ls-files", "-z", "--", "*.py", "*.js"],
            check=True, capture_output=True).stdout.decode("utf-8").split("\0")
        names = [name for name in tracked if name and not name.replace("\\", "/").startswith("frontend/vendor/")]
    for name in names:
        try:
            findings.extend((name, *item) for item in inspect(name))
        except (OSError, UnicodeDecodeError) as exc:
            findings.append((name, 0, "lectura", str(exc)))
    for name, line, kind, fragment in findings:
        print(f"{name}:{line}: {kind}: {fragment}")
    if findings:
        print(f"HALLAZGOS={len(findings)}")
        return 1
    print("HALLAZGOS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
