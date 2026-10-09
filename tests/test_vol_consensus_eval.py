from scripts.vol_consensus_eval import _test_is_virgin


def _manifest(train_start="2024-01-01T00:00:00Z", train_end="2024-01-10T00:00:00Z",
              test_start="2024-01-11T00:00:00Z", test_end="2024-01-12T00:00:00Z"):
    return {"horizons": {"4": {"split_ranges": {
        "train": {"start": train_start, "end": train_end},
        "test": {"start": test_start, "end": test_end},
    }}}}


def test_consensus_test_virgin_uses_manifest_ranges():
    assert _test_is_virgin("ADAUSDT", _manifest()) is True
    assert _test_is_virgin("ADAUSDT", _manifest(test_start="2024-01-10T00:00:00Z")) is False
    assert _test_is_virgin("ADAUSDT", {"horizons": {"4": {}}}) is None
    assert _test_is_virgin("XRPUSDT", _manifest()) is False
