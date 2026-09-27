from scripts.analyze_model_a import binomial_p_value, nonoverlapping_signal_indices


def test_nonoverlapping_signal_indices_skip_horizon_rows():
    signals = [True, True, True, True, True, False, False, False, True]

    assert nonoverlapping_signal_indices(signals, horizon=4) == [0, 4, 8]


def test_binomial_p_value_matches_known_upper_tail():
    assert binomial_p_value(10, 10, 0.5) == 1 / 1024
