from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _node_check(node: str, path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [node, "--check", str(path)], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )


def test_all_frontend_javascript_files_parse_with_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js no está instalado; se omite la comprobación de sintaxis")
    files = sorted((ROOT / "frontend").glob("*.js"))
    assert files, "No hay archivos frontend/*.js"
    failures = []
    for path in files:
        result = _node_check(node, path)
        if result.returncode:
            failures.append(f"{path.relative_to(ROOT)}\n{result.stderr}")
    assert not failures, "Errores de sintaxis JavaScript:\n" + "\n".join(failures)


def test_node_syntax_check_rejects_a_missing_function_brace(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js no está instalado; se omite la prueba de mutación")
    source_path = ROOT / "frontend" / "cuenta.js"
    source = source_path.read_text(encoding="utf-8")
    boundary = "\n  }\n  function render(connection, data) {"
    assert source.count(boundary) == 1
    mutated = source.replace(boundary, "\n  function render(connection, data) {", 1)
    mutated_path = tmp_path / "cuenta_missing_brace.js"
    mutated_path.write_text(mutated, encoding="utf-8", newline="\n")
    result = _node_check(node, mutated_path)
    assert result.returncode != 0, "node --check aceptó la mutación que elimina la llave"
