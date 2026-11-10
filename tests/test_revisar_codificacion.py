from scripts.revisar_codificacion import inspect


def test_detecta_interrogacion_aislada_en_titulo_markdown(tmp_path):
    path = tmp_path / "reporte.md"
    path.write_bytes("# Fase 43f-7 ? cierre\n".encode("utf-8"))
    findings = inspect(path)
    assert any(kind == "pregunta_aislada_titulo" for _, kind, _ in findings)


def test_no_detecta_interrogacion_en_titulo_corregido_ni_en_bloque_de_codigo(tmp_path):
    path = tmp_path / "reporte.md"
    path.write_bytes("# Fase 43f-7 - cierre\n\n```text\n# Texto ? dentro de codigo\n```\n"
        .encode("utf-8"))
    assert inspect(path) == []


def test_detecta_letra_interrogacion_letra_en_fuente(tmp_path):
    path = tmp_path / "fuente.py"
    texto = "mensaje = \"rotaci" + chr(63) + "n Cazador\"\n"
    path.write_bytes(texto.encode("utf-8"))
    findings = inspect(path)
    assert any(kind == "letra-?-letra" for _, kind, _ in findings)
