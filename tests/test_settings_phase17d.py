from config.settings import Settings


def test_scanner_auto_open_default_threshold_is_point_seven():
    assert Settings(_env_file=None).scanner_auto_open_min_score == 0.7


def test_dust_alert_defaults_are_one_percent_of_capital_and_one_usdt():
    settings = Settings(_env_file=None)
    assert settings.dust_alert_pct_capital == 1.0
    assert settings.dust_alert_usdt == 1.0
