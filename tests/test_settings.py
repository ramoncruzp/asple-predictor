from config.settings import Settings

def test_new_margin_setting_and_deprecated_alias(monkeypatch):
    monkeypatch.setenv("GRID_MIN_MARGIN_AFTER_FEES_PCT", "0.9")
    monkeypatch.setenv("GRID_MIN_NET_MARGIN_PCT", "0.6")
    assert Settings(_env_file=None).grid_min_margin_after_fees_pct == .9
    monkeypatch.delenv("GRID_MIN_MARGIN_AFTER_FEES_PCT")
    assert Settings(_env_file=None).grid_min_margin_after_fees_pct == .6
