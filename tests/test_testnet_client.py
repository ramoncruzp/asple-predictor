from __future__ import annotations

import json
from collections import deque

import pytest
from binance.exceptions import BinanceAPIException, BinanceRequestException
from requests.exceptions import ConnectionError

from data.testnet_client import (
    TESTNET_API_URL,
    TestnetClient as _TestnetClient,
    TestnetOrderError as _TestnetOrderError,
)


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


def api_error(code: int, status: int = 400, message: str = "request failed"):
    response = _FakeResponse(json.dumps({"code": code, "msg": message}))
    return BinanceAPIException(response, status, response.text)


def order_response(**overrides):
    order = {
        "orderId": 42,
        "clientOrderId": "client-id-1",
        "symbol": "XRPUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "status": "NEW",
        "price": "0.5000",
        "origQty": "12.5",
        "executedQty": "0.0",
    }
    order.update(overrides)
    return order


class FakeTestnetHTTP:
    API_URL = TESTNET_API_URL
    testnet = True

    def __init__(self):
        self.create_results = deque()
        self.lookup_results = deque()
        self.cancel_results = deque()
        self.open_orders_result = []
        self.account_result = {"balances": []}
        self.create_calls = []
        self.lookup_calls = []
        self.cancel_calls = []
        self.open_orders_calls = []

    @staticmethod
    def _next(queue, default):
        result = queue.popleft() if queue else default
        if isinstance(result, Exception):
            raise result
        if callable(result):
            result = result()
        return result

    def create_order(self, **params):
        self.create_calls.append(params)
        return self._next(self.create_results, order_response(
            symbol=params["symbol"], side=params["side"], type=params["type"],
            clientOrderId=params["newClientOrderId"], price=params.get("price", "0"),
            origQty=params["quantity"],
        ))

    def get_order(self, **params):
        self.lookup_calls.append(params)
        return self._next(self.lookup_results, api_error(-2013, 400, "Order does not exist"))

    def cancel_order(self, **params):
        self.cancel_calls.append(params)
        return self._next(self.cancel_results, order_response(status="CANCELED"))

    def get_open_orders(self, **params):
        self.open_orders_calls.append(params)
        return self.open_orders_result

    def get_account(self):
        return self.account_result


def make_client(fake=None, **kwargs):
    return _TestnetClient(
        "safe-testnet-key", "safe-testnet-secret", client=fake or FakeTestnetHTTP(), **kwargs
    )


def test_rejects_empty_and_placeholder_credentials():
    fake = FakeTestnetHTTP()
    for key, secret in (("", "secret"), ("key", ""), ("tu_key", "secret"), ("key", "tu_secret")):
        with pytest.raises(ValueError):
            _TestnetClient(key, secret, client=fake)


def test_rejects_production_key_reuse():
    with pytest.raises(ValueError, match="differ"):
        _TestnetClient("same-key", "secret", production_api_key="same-key", client=FakeTestnetHTTP())


def test_rejects_non_testnet_endpoint():
    fake = FakeTestnetHTTP()
    fake.testnet = False
    with pytest.raises(ValueError, match="testnet=True"):
        make_client(fake)


def test_repr_does_not_disclose_credentials():
    client = _TestnetClient("private-key-sentinel", "private-secret-sentinel", client=FakeTestnetHTTP())
    assert "private-key-sentinel" not in repr(client)
    assert "private-secret-sentinel" not in repr(client)


def test_limit_order_sends_expected_parameters_and_normalizes_result():
    fake = FakeTestnetHTTP()
    client = make_client(fake)
    result = client.place_order("xrp/usdt", "buy", 12.5, 0.5, client_order_id="fixed-id")

    assert fake.create_calls == [{
        "symbol": "XRPUSDT", "side": "BUY", "type": "LIMIT", "quantity": 12.5,
        "newClientOrderId": "fixed-id", "timeInForce": "GTC", "price": 0.5,
    }]
    assert result == {
        "order_id": 42, "client_order_id": "fixed-id", "symbol": "XRPUSDT",
        "side": "BUY", "type": "LIMIT", "status": "NEW", "price": 0.5,
        "quantity": 12.5, "executed_qty": 0.0,
    }


def test_invalid_order_inputs_raise_value_error():
    client = make_client()
    invalid = (
        {"symbol": "XRPUSDT", "side": "BUY", "quantity": 1},
        {"symbol": "XRPUSDT", "side": "BUY", "quantity": 0, "price": 1},
        {"symbol": "XRPUSDT", "side": "HOLD", "quantity": 1, "price": 1},
        {"symbol": "XRPUSDT", "side": "BUY", "quantity": 1, "price": 1, "order_type": "STOP"},
    )
    for params in invalid:
        with pytest.raises(ValueError):
            client.place_order(**params)


def test_rejects_non_usdt_pair():
    with pytest.raises(ValueError, match="USDT"):
        make_client().place_order("XRP/BTC", "BUY", 1, order_type="MARKET")


def test_network_failure_twice_then_success_uses_expected_backoff(monkeypatch):
    fake = FakeTestnetHTTP()
    fake.create_results.extend([ConnectionError("temporary"), ConnectionError("temporary"), order_response()])
    waits = []
    monkeypatch.setattr("data.testnet_client.time.sleep", waits.append)

    result = make_client(fake).place_order("XRPUSDT", "BUY", 1, 0.5)

    assert result["order_id"] == 42
    assert len(fake.create_calls) == 3
    assert waits == [1, 2]


def test_persistent_network_failure_stops_after_four_attempts(monkeypatch):
    fake = FakeTestnetHTTP()
    fake.create_results.extend([BinanceRequestException("offline") for _ in range(4)])
    waits = []
    monkeypatch.setattr("data.testnet_client.time.sleep", waits.append)

    with pytest.raises(RuntimeError, match="after 3 retries"):
        make_client(fake).place_order("XRPUSDT", "BUY", 1, 0.5)
    assert len(fake.create_calls) == 4
    assert waits == [1, 2, 4]


def test_business_error_is_not_retried_and_preserves_code():
    fake = FakeTestnetHTTP()
    fake.create_results.append(api_error(-2010, 400, "insufficient balance"))

    with pytest.raises(_TestnetOrderError) as caught:
        make_client(fake).place_order("XRPUSDT", "BUY", 1, 0.5)
    assert caught.value.code == -2010
    assert len(fake.create_calls) == 1


def test_place_order_retries_reuse_same_client_order_id(monkeypatch):
    fake = FakeTestnetHTTP()
    fake.create_results.extend([ConnectionError("temporary"), order_response()])
    waits = []
    monkeypatch.setattr("data.testnet_client.time.sleep", waits.append)

    make_client(fake).place_order("XRPUSDT", "BUY", 1, 0.5, client_order_id="one-id")

    assert [call["newClientOrderId"] for call in fake.create_calls] == ["one-id", "one-id"]
    assert fake.lookup_calls[0]["origClientOrderId"] == "one-id"


def test_existing_order_after_lost_response_is_returned_without_duplicate(monkeypatch):
    fake = FakeTestnetHTTP()
    fake.create_results.append(ConnectionError("response was lost"))
    existing = order_response(orderId=77, clientOrderId="one-id", status="NEW")
    fake.lookup_results.append(existing)
    monkeypatch.setattr("data.testnet_client.time.sleep", lambda _seconds: None)

    result = make_client(fake).place_order("XRPUSDT", "BUY", 1, 0.5, client_order_id="one-id")

    assert result["order_id"] == 77
    assert len(fake.create_calls) == 1


def test_cancel_order_returns_normalized_response():
    fake = FakeTestnetHTTP()
    fake.cancel_results.append(order_response(status="CANCELED"))

    result = make_client(fake).cancel_order("XRP/USDT", 42)

    assert fake.cancel_calls == [{"symbol": "XRPUSDT", "orderId": 42}]
    assert result["status"] == "CANCELED"
    assert result["quantity"] == 12.5


def test_cancel_retry_unknown_order_returns_actual_status(monkeypatch):
    fake = FakeTestnetHTTP()
    fake.cancel_results.extend([ConnectionError("temporary"), api_error(-2011, 400, "Unknown order sent")])
    fake.lookup_results.append(order_response(status="CANCELED"))
    waits = []
    monkeypatch.setattr("data.testnet_client.time.sleep", waits.append)

    result = make_client(fake).cancel_order("XRPUSDT", 42)

    assert result["status"] == "CANCELED"
    assert fake.lookup_calls == [{"symbol": "XRPUSDT", "orderId": 42}]
    assert waits == [1]


def test_get_open_orders_with_and_without_symbol_normalizes_rows():
    fake = FakeTestnetHTTP()
    fake.open_orders_result = [order_response()]
    client = make_client(fake)

    with_symbol = client.get_open_orders("XRP/USDT")
    all_symbols = client.get_open_orders()

    assert fake.open_orders_calls == [{"symbol": "XRPUSDT"}, {}]
    assert with_symbol[0]["order_id"] == 42
    assert all_symbols[0]["quantity"] == 12.5


def test_get_balance_normalizes_omits_zero_and_filters_asset():
    fake = FakeTestnetHTTP()
    fake.account_result = {"balances": [
        {"asset": "USDT", "free": "12.25", "locked": "0"},
        {"asset": "XRP", "free": "1.5", "locked": "2"},
        {"asset": "BTC", "free": "0", "locked": "0"},
    ]}
    client = make_client(fake)

    assert client.get_balance() == {
        "USDT": {"free": 12.25, "locked": 0.0},
        "XRP": {"free": 1.5, "locked": 2.0},
    }
    assert client.get_balance("usdt") == {"USDT": {"free": 12.25, "locked": 0.0}}


@pytest.mark.live
def test_live_binance_testnet_far_limit_roundtrip():
    from config.settings import Settings
    from data.binance_client import BinanceClient as PublicMarketClient
    from data.testnet_client import TestnetClient as LiveTestnetClient

    settings = Settings()
    api_key = (settings.testnet_api_key or "").strip()
    api_secret = (settings.testnet_api_secret or "").strip()
    if (
        not api_key
        or not api_secret
        or api_key.casefold().startswith("tu_")
        or api_secret.casefold().startswith("tu_")
    ):
        pytest.skip("faltan credenciales de Testnet")

    try:
        testnet = LiveTestnetClient(
            api_key,
            api_secret,
            production_api_key=settings.binance_api_key,
        )
    except Exception as exc:
        pytest.fail(f"Binance Testnet client initialization failed ({type(exc).__name__})", pytrace=False)
    market = PublicMarketClient(None, None)
    market_price = market.get_current_price("XRPUSDT")["price"]

    # Binance rejects a LIMIT price that is too far from the current price
    # (PERCENT_PRICE_BY_SIDE). Stay just inside the allowed band, on the low
    # side, so the order rests without filling instead of guessing a fixed
    # percentage that may be banned on a given day/symbol.
    symbol_info = testnet.client.get_symbol_info("XRPUSDT")
    percent_filter = next(
        (f for f in symbol_info.get("filters", []) if f.get("filterType") == "PERCENT_PRICE_BY_SIDE"),
        None,
    )
    if percent_filter is not None:
        multiplier_down = float(percent_filter["bidMultiplierDown"])
        safe_ratio = min(multiplier_down * 1.02, 0.95)
    else:
        safe_ratio = 0.9
    safe_price = round(market_price * safe_ratio, 4)

    order_id = None
    cancel_verified = False
    try:
        order = testnet.place_order(
            "XRPUSDT",
            "BUY",
            quantity=20.0,
            price=safe_price,
            order_type="LIMIT",
        )
        order_id = order["order_id"]
        open_orders = testnet.get_open_orders("XRPUSDT")
        assert any(open_order["order_id"] == order_id for open_order in open_orders)

        canceled = testnet.cancel_order("XRPUSDT", order_id)
        assert canceled["status"] == "CANCELED"
        cancel_verified = True
    finally:
        if order_id is not None and not cancel_verified:
            try:
                testnet.cancel_order("XRPUSDT", order_id)
            except Exception:
                pytest.fail("No se pudo cancelar la orden Testnet durante la limpieza.")


def test_decimal_order_values_are_sent_as_plain_strings():
    from decimal import Decimal

    fake = FakeTestnetHTTP()
    make_client(fake).place_order(
        "XRPUSDT", "BUY", Decimal("0.00001"), Decimal("1E-5"),
    )
    assert fake.create_calls[0]["quantity"] == "0.00001"
    assert fake.create_calls[0]["price"] == "0.00001"


def test_float_order_values_keep_float_parameters():
    fake = FakeTestnetHTTP()
    make_client(fake).place_order("XRPUSDT", "BUY", 2.5, 0.125)
    assert fake.create_calls[0]["quantity"] == 2.5
    assert fake.create_calls[0]["price"] == 0.125


def test_extended_read_methods_normalize_exchange_data():
    from decimal import Decimal

    fake = FakeTestnetHTTP()
    fake.get_symbol_info = lambda symbol: {"symbol": symbol}
    fake.get_avg_price = lambda symbol: {"mins": 5, "price": "1.2345"}
    fake.get_orderbook_ticker = lambda symbol: {
        "symbol": symbol, "bidPrice": "1.2", "bidQty": "3", "askPrice": "1.3", "askQty": "4",
    }
    fake.lookup_results.append(order_response(
        executedQty="2", cummulativeQuoteQty="2.5",
    ))
    fake.get_my_trades = lambda **params: [{
        "price": "1.25", "qty": "2", "commission": "0.002", "commissionAsset": "XRP",
    }]
    client = make_client(fake)

    assert client.get_symbol_info("xrp/usdt") == {"symbol": "XRPUSDT"}
    assert client.get_avg_price("XRPUSDT") == Decimal("1.2345")
    assert client.get_book_ticker("XRPUSDT") == {
        "bid_price": Decimal("1.2"), "bid_qty": Decimal("3"),
        "ask_price": Decimal("1.3"), "ask_qty": Decimal("4"),
    }
    order = client.get_order("XRPUSDT", order_id=42)
    assert order["executed_qty"] == Decimal("2")
    assert order["cummulative_quote_qty"] == Decimal("2.5")
    assert client.get_my_trades("XRPUSDT", 42) == [{
        "price": Decimal("1.25"), "qty": Decimal("2"),
        "commission": Decimal("0.002"), "commission_asset": "XRP",
    }]


def test_find_order_by_client_id_returns_none_for_unknown_order():
    fake = FakeTestnetHTTP()
    fake.lookup_results.append(api_error(-2013))
    assert make_client(fake).find_order_by_client_id("XRPUSDT", "missing") is None


def test_get_order_requires_exactly_one_identifier():
    with pytest.raises(ValueError, match="exactly one"):
        make_client().get_order("XRPUSDT")
    with pytest.raises(ValueError, match="exactly one"):
        make_client().get_order("XRPUSDT", order_id=42, client_order_id="id")
