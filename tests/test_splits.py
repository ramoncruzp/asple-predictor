import pytest

from config.models_config import TARGET_HORIZON_CANDLES
from data.splits import chronological_split


def test_chronological_split_preserves_order_and_embargo():
    train, val, test = chronological_split(1000)
    assert train.start == 0
    assert train.stop <= val.start
    assert val.stop <= test.start
    assert val.start - train.stop == TARGET_HORIZON_CANDLES
    assert test.start - val.stop == TARGET_HORIZON_CANDLES
    assert train.stop <= val.start < val.stop <= test.start < test.stop
    assert min(train.stop - train.start, val.stop - val.start, test.stop - test.start) >= 50


def test_chronological_split_rejects_insufficient_partitions():
    with pytest.raises(ValueError, match="al menos 50"):
        chronological_split(300)


def test_chronological_split_rejects_invalid_proportions():
    with pytest.raises(ValueError, match="proporciones"):
        chronological_split(1000, train=0.8, val=0.2)
