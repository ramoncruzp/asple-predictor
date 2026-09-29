from __future__ import annotations

from decimal import Decimal

from data.exchange_filters import SymbolFilters
from data.testnet_client import TestnetOrderError


def fake_symbol_info():
    return {
        "symbol": "XRPUSDT",
        "filters": [
            {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "100000", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "minQty": "0.1", "maxQty": "100000", "stepSize": "0.1"},
            {"filterType": "NOTIONAL", "minNotional": "5", "applyMinToMarket": True},
            {"filterType": "MAX_NUM_ORDERS", "maxNumOrders": 200},
            {"filterType": "PERCENT_PRICE_BY_SIDE", "bidMultiplierUp": "1.2", "bidMultiplierDown": "0.5",
             "askMultiplierUp": "2", "askMultiplierDown": "0.8"},
        ],
    }


class FakeExchange:
    def __init__(self, fee_rate="0.001", fee_asset="XRP"):
        self.filters = SymbolFilters.from_symbol_info(fake_symbol_info())
        self.bid = Decimal("100")
        self.ask = Decimal("100.01")
        self.avg = Decimal("100")
        self.free_usdt = Decimal("100000")
        self.fee_rate = Decimal(fee_rate)
        self.fee_asset = fee_asset
        self.orders = {}
        self.trades = {}
        self.next_order_id = 1
        self.create_calls = []
        self.fail_on_create = None
        self.create_failure = None
        self.lose_next_response = False

    def get_symbol_info(self, symbol):
        assert symbol == "XRPUSDT"
        return fake_symbol_info()

    def get_book_ticker(self, symbol):
        return {"bid_price": self.bid, "ask_price": self.ask, "bid_qty": Decimal("100"), "ask_qty": Decimal("100")}

    def get_avg_price(self, symbol):
        return self.avg

    def get_balance(self, asset=None):
        return {"USDT": {"free": float(self.free_usdt), "locked": 0.0}}

    def _normalize(self, order):
        return {key: value for key, value in order.items() if key != "_trades"}

    def place_order(self, symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        self.create_calls.append((symbol, side, quantity, price, client_order_id))
        if self.fail_on_create is not None and len(self.create_calls) == self.fail_on_create:
            raise self.create_failure or RuntimeError("injected exchange rejection")
        if client_order_id in self.orders_by_client:
            return self._normalize(self.orders_by_client[client_order_id])
        qty, price = Decimal(str(quantity)), Decimal(str(price))
        self.filters.validate_order(side, price, qty, self.avg)
        order = {
            "order_id": self.next_order_id,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "status": "NEW",
            "price": price,
            "quantity": qty,
            "executed_qty": Decimal(0),
            "cummulative_quote_qty": Decimal(0),
            "_trades": [],
        }
        self.next_order_id += 1
        self.orders[order["order_id"]] = order
        self.orders_by_client[client_order_id] = order
        if self.lose_next_response:
            self.lose_next_response = False
            raise RuntimeError("simulated lost response after accepting order")
        return self._normalize(order)

    @property
    def orders_by_client(self):
        if not hasattr(self, "_orders_by_client"):
            self._orders_by_client = {}
        return self._orders_by_client

    def get_open_orders(self, symbol=None):
        return [
            self._normalize(order) for order in self.orders.values()
            if order["status"] in {"NEW", "PARTIALLY_FILLED"}
            and (symbol is None or order["symbol"] == symbol)
        ]

    def find_order_by_client_id(self, symbol, client_order_id):
        order = self.orders_by_client.get(client_order_id)
        return self._normalize(order) if order and order["symbol"] == symbol else None

    def get_order(self, symbol, order_id=None, client_order_id=None):
        order = self.orders.get(int(order_id)) if order_id is not None else self.orders_by_client.get(client_order_id)
        if order is None or order["symbol"] != symbol:
            raise KeyError("unknown order")
        return self._normalize(order)

    def get_my_trades(self, symbol, order_id):
        return list(self.orders[int(order_id)]["_trades"])

    def cancel_order(self, symbol, order_id):
        order = self.orders[int(order_id)]
        if order["status"] == "FILLED":
            return self._normalize(order)
        order["status"] = "CANCELED"
        return self._normalize(order)

    def fill(self, order_id, partial=False):
        order = self.orders[int(order_id)]
        qty = order["quantity"] / 2 if partial else order["quantity"]
        order["executed_qty"] = qty
        order["cummulative_quote_qty"] = qty * order["price"]
        order["status"] = "PARTIALLY_FILLED" if partial else "FILLED"
        commission = qty * self.fee_rate
        order["_trades"] = [{
            "price": order["price"], "qty": qty,
            "commission": commission, "commission_asset": self.fee_asset,
        }]
        return self._normalize(order)

    def move_price(self, bid, ask, avg=None):
        self.bid, self.ask = Decimal(str(bid)), Decimal(str(ask))
        self.avg = Decimal(str(avg if avg is not None else (self.bid + self.ask) / 2))
        for order in list(self.orders.values()):
            if order["status"] != "NEW":
                continue
            if (order["side"] == "BUY" and self.ask <= order["price"]) or (
                order["side"] == "SELL" and self.bid >= order["price"]
            ):
                self.fill(order["order_id"])

    @staticmethod
    def insufficient_balance():
        return TestnetOrderError(-2010, "insufficient balance")
