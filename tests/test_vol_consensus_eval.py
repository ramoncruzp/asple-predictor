from scripts.vol_consensus_eval import _test_is_virgin


def test_consensus_test_virgin_is_false_after_a_prior_symbol_output(tmp_path):
    output = tmp_path / "consensus_ada.json"
    assert _test_is_virgin("ADAUSDT", output) is True
    output.write_text("{}", encoding="utf-8")
    assert _test_is_virgin("ADAUSDT", output) is False
    assert _test_is_virgin("XRPUSDT", tmp_path / "missing_xrp.json") is False
