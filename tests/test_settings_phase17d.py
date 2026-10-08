from config.settings import Settings


def test_scanner_auto_open_default_threshold_matches_zero_cost_weight_score():
    settings = Settings(_env_file=None)
    assert settings.scanner_auto_open_min_score == 0.538
    assert settings.scanner_weight_cost_headroom == 0.0


def test_dust_alert_defaults_are_one_percent_of_capital_and_one_usdt():
    settings = Settings(_env_file=None)
    assert settings.dust_alert_pct_capital == 1.0
    assert settings.dust_alert_usdt == 1.0
