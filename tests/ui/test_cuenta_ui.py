from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_account_screen_has_write_only_credential_controls():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    source = (ROOT / "frontend" / "cuenta.js").read_text(encoding="utf-8")
    assert 'id="screen-cuenta"' in html
    assert "testnet-credentials-form" in html
    assert "type=\"password\"" in html
    assert "cuenta-token" in source
    assert "api_key: apiKey" in source and "api_secret: apiSecret" in source
    assert "keyInput.value = ''; secretInput.value = ''" in source
    assert "TESTNET_API_KEY" not in source
    assert "TESTNET_SECRET" not in source
    for block in ("Conexión", "Balance Testnet", "Ganancias", "Conciliación contra Testnet"):
        assert block in source
    assert "aria-live=\"polite\"" in html
    assert "setOffline(" not in source
    assert "APP.apiHealthState" not in source
