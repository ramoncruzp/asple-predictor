from pathlib import Path


def test_human_facing_messages_use_utf8_accents_without_mojibake():
    expected = {
        "tests/ui/test_frontend_format.py": "Node.js no está instalado",
        "data/volatility.py": "Faltan medidas intradía",
        "scripts/vol_research.py": "No hay rango de fechas común",
    }
    mojibake = ("Ã" + "¡", "Ã" + "í", "Ã" + "º")
    for filename, phrase in expected.items():
        text = Path(filename).read_text(encoding="utf-8")
        assert phrase in text
        assert not any(fragment in text for fragment in mojibake)
