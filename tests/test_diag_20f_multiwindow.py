from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.diag_20f_multiwindow import analyze_frames, build_windows, paired_statistics, main


def _frames(window_days=7, windows=4):
    total_days = 35 + window_days * windows
    start = pd.Timestamp('2025-01-01T00:00:00Z')
    periods_5m = total_days * 288
    stamps_5m = pd.date_range(start, periods=periods_5m, freq='5min')
    x5 = np.arange(periods_5m, dtype=float)
    close5 = 100 * np.exp(0.00015 * np.sin(x5 / 9) + 0.00004 * np.sin(x5 / 47))
    five = pd.DataFrame({'timestamp': stamps_5m, 'open': close5, 'high': close5 * 1.001,
                         'low': close5 * 0.999, 'close': close5})
    periods_1h = total_days * 24
    stamps_1h = pd.date_range(start, periods=periods_1h, freq='1h')
    rng = np.random.default_rng(20260908)
    close1 = 100 * np.exp(np.cumsum(rng.normal(0, 0.005, periods_1h)))
    one = pd.DataFrame({'timestamp': stamps_1h, 'open': close1, 'high': close1 * 1.002,
                        'low': close1 * 0.998, 'close': close1})
    return five, one


def test_nonoverlapping_windows_use_prior_30_days_and_have_expected_bounds():
    five, one = _frames()
    windows, discarded = build_windows(five, one, 7)
    assert discarded == 5
    assert len(windows) == 4
    assert windows[0]['start'] == five['timestamp'].iloc[35 * 288]
    assert windows[0]['end'] == windows[0]['start'] + pd.Timedelta(days=7)
    assert windows[1]['start'] == windows[0]['end']
    assert all(left['end'] <= right['start'] for left, right in zip(windows, windows[1:]))
    assert all(np.isfinite(item['sigma24']) and item['sigma24'] > 0 for item in windows)


def test_paired_statistics_match_hand_calculated_sample_sd_and_t():
    result = paired_statistics({'simple': [1, 2, 3, 4], 'smart': [0, 2, 2, 3]})[0]
    assert result['left'] == 'simple' and result['right'] == 'smart'
    assert result['n'] == 4
    assert result['mean_difference'] == pytest.approx(.75)
    assert result['sample_sd'] == pytest.approx(.5)
    assert result['paired_t'] == pytest.approx(3.0)
    assert result['wins'] == 3


def test_csv_fixture_runs_end_to_end_and_writes_only_out(tmp_path):
    five, one = _frames()
    data_dir = tmp_path / 'input'
    data_dir.mkdir()
    five.to_csv(data_dir / 'xrp_5m.csv', index=False)
    one.to_csv(data_dir / 'xrp_1h.csv', index=False)
    out = tmp_path / 'result.json'
    before = {p.relative_to(tmp_path) for p in tmp_path.rglob('*')}
    payload = main(['--symbol', 'XRPUSDT', '--window-days', '7', '--variants', 'simple',
                    '--data-dir', str(data_dir), '--out', str(out)])
    after = {p.relative_to(tmp_path) for p in tmp_path.rglob('*')}
    assert payload['valid_windows'] == 4
    assert payload['header'] == '\u03c3 = EWMA interna; sin pr\u00e9stamos; sin reapertura tras CLOSE_REPOSITORY; una moneda; resultados por ventana, no anualizados'
    assert out.exists()
    assert after - before == {Path('result.json')}
    assert len(payload['variant_summary']) == 1
    assert payload['variant_summary'][0]['n'] == 4


def test_rejects_window_shorter_than_seven_days_and_too_few_valid_windows():
    five, one = _frames(windows=3)
    with pytest.raises(ValueError, match='at least 7'):
        build_windows(five, one, 6)
    with pytest.raises(ValueError, match='fewer than 4'):
        analyze_frames('XRPUSDT', five, one, 7, ['simple'])


def test_stop_loss_none_uses_documented_large_threshold_sentinel():
    from scripts.diag_20f_multiwindow import parse_variant
    variant = parse_variant("smart:h=24,sl=none")
    assert variant["params"]["stop_loss_pct"] == 1000000.0
