#!/usr/bin/env python3
"""Revisa errores de codificación en archivos fuente y Markdown."""
from __future__ import annotations

import re
import sys
from pathlib import Path

WORD_QUESTION = re.compile(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]\?[A-Za-zÁÉÍÓÚáéíóúÑñ]")
URL = re.compile(r"https?://\S+")
BAD_TEXT = (chr(0x00c3), chr(0x00c2), chr(0x00e2) + chr(0x20ac), chr(0xfffd), chr(0xfeff))


def _visible_text(line, markdown, in_fence):
    if markdown:
        if line.lstrip().startswith("```"):
            return "", not in_fence
        if in_fence:
            return "", True
    return URL.sub("", line), in_fence


def inspect(path):
    path = Path(path)
    if path.suffix.lower() not in {".md", ".py", ".js"}:
        return []
    raw = path.read_bytes()
    findings = []
    if raw.startswith(b"\xef\xbb\xbf"):
        findings.append((1, "BOM", "UTF-8 BOM"))
    text = raw.decode("utf-8")
    if b"\r\n" in raw:
        findings.append((1, "CRLF", "fin de línea CRLF"))
    in_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        visible, in_fence = _visible_text(line, path.suffix.lower() == ".md", in_fence)
        for match in WORD_QUESTION.finditer(visible):
            findings.append((number, "letra-?-letra", line[max(0, match.start()-24):match.end()+24]))
        for pattern in BAD_TEXT:
            if pattern in visible:
                findings.append((number, repr(pattern), line[:180]))
    return findings


def main(argv=None):
    findings = []
    for name in (argv if argv is not None else sys.argv[1:]):
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
