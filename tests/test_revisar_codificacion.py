from scripts.revisar_codificacion import inspect


def test_detecta_interrogacion_aislada_en_titulo_markdown(tmp_path):
    path = tmp_path / "reporte.md"
    path.write_bytes("# Fase 43f-7 ? cierre\n".encode("utf-8"))
    findings = inspect(path)
    assert any(kind == "interrogación aislada en título" for _, kind, _ in findings)


def test_no_detecta_interrogacion_en_titulo_corregido_ni_en_bloque_de_codigo(tmp_path):
    path = tmp_path / "reporte.md"
    path.write_bytes("# Fase 43f-7 - cierre\n\n```text\n# Texto ? dentro de codigo\n```\n"
        .encode("utf-8"))
    assert inspect(path) == []
