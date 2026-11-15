"""Exchange sintético determinista para recorridos E2E del modo Meta."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import math

from data.exchange_filters import SymbolFilters


class FakeExchange:
    testnet = True
    fee_rate = Decimal("0.001")
    fee_asset = "USDT"
    symbols = ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")
    scales = {"AAAUSDT": 1.0, "BBBUSDT": .72, "CCCUSDT": .48, "DDDUSDT": .36}

    def __init__(self, initial_usdt=350, *, slippage_pct=.001):
        self.free = {"USDT": Decimal(str(initial_usdt))}
        self.locked = {"USDT": Decimal(0)}
        self.prices = {symbol: Decimal(str(100 * (index + 1)))
            for index, symbol in enumerate(self.symbols)}
        self.slippage = Decimal(str(slippage_pct))
        self.orders = {}
        self.client_orders = {}
        self.trades = {}
        self.next_order_id = 1
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.step = 0
        self.scenario = "oscillation"
        self.market_frames = {}
        self.unavailable_symbols = set()
        self.filters = {symbol: SymbolFilters.from_symbol_info(self.get_symbol_info(symbol))
            for symbol in self.symbols}

    def get_symbol_info(self, symbol):
        return {"symbol": symbol, "filters": [
            {"filterType": "PRICE_FILTER", "minPrice": "0.0001", "maxPrice": "1000000", "tickSize": "0.0001"},
            {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "100000000", "stepSize": "0.001"},
            {"filterType": "NOTIONAL", "minNotional": "5", "applyMinToMarket": True},
            {"filterType": "MAX_NUM_ORDERS", "maxNumOrders": 200},
            {"filterType": "PERCENT_PRICE_BY_SIDE", "bidMultiplierUp": "2", "bidMultiplierDown": ".5",
                "askMultiplierUp": "2", "askMultiplierDown": ".5"},
        ]}

    def get_book_ticker(self, symbol):
        mid = self.prices[symbol]
        spread = mid * Decimal("0.0002")
        bid = (mid - spread).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        ask = (mid + spread).quantize(Decimal("0.0001"), rounding=ROUND_UP)
        return {"bid_price": bid, "ask_price": ask,
            "bid_qty": Decimal("100000"), "ask_qty": Decimal("100000")}

    def get_book_ticker_depth(self, symbol, limit=100):
        return self.get_order_book_depth(symbol, limit)

    def get_order_book_depth(self, symbol, limit=100):
        if symbol in self.unavailable_symbols:
            return {"bids": [], "asks": [], "read_at": self.now}
        book = self.get_book_ticker(symbol)
        bids, asks = [], []
        for index in range(max(5, min(int(limit), 100))):
            factor = Decimal(1) - Decimal(index) * Decimal("0.0001")
            bids.append((book["bid_price"] * factor, Decimal("100000")))
            asks.append((book["ask_price"] / factor, Decimal("100000")))
        return {"bids": bids, "asks": asks, "read_at": self.now}

    def get_avg_price(self, symbol):
        return self.prices[symbol]

    def get_balance(self, asset=None):
        assets = set(self.free) | set(self.locked)
        result = {key: {"free": float(self.free.get(key, 0)),
            "locked": float(self.locked.get(key, 0))} for key in assets}
        return result if asset is None or asset == "USDT" else {asset: result.get(asset,
            {"free": 0.0, "locked": 0.0})}

    def _view(self, order):
        return {key: value for key, value in order.items() if key != "_reserved"}

    def get_open_orders(self, symbol=None):
        return [self._view(order) for order in self.orders.values()
            if order["status"] in {"NEW", "PARTIALLY_FILLED"}
            and (symbol is None or order["symbol"] == symbol)]

    def place_order(self, symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        existing = self.client_orders.get(client_order_id)
        if existing is not None:
            return self._view(existing)
        side = str(side).upper()
        order_type = str(order_type).upper()
        filters = self.filters[symbol]
        qty = filters.round_qty_down(Decimal(str(quantity)))
        ticker = self.get_book_ticker(symbol)
        mid = self.prices[symbol]
        limit_price = Decimal(str(price if price is not None else
            (ticker["ask_price"] if side == "BUY" else ticker["bid_price"])))
        filters.validate_order(side, limit_price, qty, mid)
        order = {"order_id": self.next_order_id, "client_order_id": client_order_id,
            "symbol": symbol, "side": side, "type": order_type, "status": "NEW",
            "price": limit_price, "quantity": qty, "executed_qty": Decimal(0),
            "cummulative_quote_qty": Decimal(0), "_reserved": Decimal(0)}
        self.next_order_id += 1
        self.orders[order["order_id"]] = order
        self.client_orders[client_order_id] = order
        self.trades[order["order_id"]] = []
        if side == "BUY" and order_type == "LIMIT":
            reserve = qty * limit_price * (1 + self.fee_rate)
            if self.free.get("USDT", Decimal(0)) < reserve:
                del self.orders[order["order_id"]]
                del self.client_orders[client_order_id]
                raise RuntimeError("insufficient USDT in synthetic exchange")
            self.free["USDT"] -= reserve
            self.locked["USDT"] = self.locked.get("USDT", Decimal(0)) + reserve
            order["_reserved"] = reserve
        if order_type == "MARKET":
            fill_price = (ticker["ask_price"] if side == "BUY" else ticker["bid_price"])
            fill_price *= (1 + self.slippage if side == "BUY" else 1 - self.slippage)
            self._fill(order, fill_price, qty, market=True)
        return self._view(order)

    def _fill(self, order, fill_price, qty, *, market=False):
        quote = Decimal(str(fill_price)) * Decimal(str(qty))
        fee = quote * self.fee_rate
        if order["side"] == "BUY":
            if not market:
                reserved = order["_reserved"]
                self.locked["USDT"] -= reserved
                self.free["USDT"] += max(Decimal(0), reserved - quote - fee)
                order["_reserved"] = Decimal(0)
            else:
                total = quote + fee
                if self.free.get("USDT", 0) < total:
                    order["status"] = "REJECTED"
                    return
                self.free["USDT"] -= total
            asset = order["symbol"].removesuffix("USDT")
            self.free[asset] = self.free.get(asset, Decimal(0)) + Decimal(str(qty))
        else:
            asset = order["symbol"].removesuffix("USDT")
            available = self.free.get(asset, Decimal(0))
            if available < Decimal(str(qty)):
                order["status"] = "REJECTED"
                return
            self.free[asset] -= Decimal(str(qty))
            self.free["USDT"] = self.free.get("USDT", Decimal(0)) + quote - fee
        order["executed_qty"] = Decimal(str(qty))
        # Simula la respuesta de Testnet para MARKET; get_my_trades conserva quote real.
        order["cummulative_quote_qty"] = Decimal(0) if market else quote
        order["status"] = "FILLED"
        self.trades[order["order_id"]] = [{"symbol": order["symbol"],
            "orderId": order["order_id"], "price": str(fill_price), "qty": str(qty),
            "quoteQty": str(quote), "commission": str(fee),
            "commission_asset": "USDT", "commissionAsset": "USDT"}]

    def get_order(self, symbol, order_id=None, client_order_id=None):
        order = (self.orders.get(int(order_id)) if order_id is not None else
            self.client_orders.get(client_order_id))
        if order is None or order["symbol"] != symbol:
            raise KeyError(f"order no encontrado: {symbol} {order_id or client_order_id}")
        return self._view(order)

    def find_order_by_client_id(self, symbol, client_order_id):
        order = self.client_orders.get(client_order_id)
        return self._view(order) if order and order["symbol"] == symbol else None

    def get_my_trades(self, symbol, order_id):
        order = self.orders[int(order_id)]
        return list(self.trades[order["order_id"]]) if order["symbol"] == symbol else []

    def cancel_order(self, symbol, order_id):
        order = self.orders[int(order_id)]
        if order["status"] in {"FILLED", "CANCELED"}:
            return self._view(order)
        if order["side"] == "BUY" and order["_reserved"]:
            self.locked["USDT"] -= order["_reserved"]
            self.free["USDT"] += order["_reserved"]
            order["_reserved"] = Decimal(0)
        order["status"] = "CANCELED"
        return self._view(order)

    def advance(self, scenario, step):
        self.scenario, self.step = str(scenario), int(step)
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc).replace() + __import__(
            "datetime").timedelta(minutes=5 * int(step))
        t = int(step) / (3 * 24 * 12)
        noise = .0015 * math.sin(step * .71) + .001 * math.sin(step * .193)
        if scenario == "subida":
            common = .08 * t + noise
        elif scenario == "caida":
            common = -.25 * t + noise
        else:
            common = .04 * math.sin(2 * math.pi * 3 * t) + noise
        for index, symbol in enumerate(self.symbols):
            scale = self.scales[symbol]
            # Monedas con σ distintas para que el filtro relativo de Cazador sea verificable.
            excursion = common * scale + .0006 * math.sin(step * (.11 + index * .037))
            initial = Decimal(str(100 * (index + 1)))
            mid = initial * Decimal(str(1 + excursion))
            previous = self.prices[symbol]
            self.prices[symbol] = mid
            ticker = self.get_book_ticker(symbol)
            for order in list(self.orders.values()):
                if order["symbol"] != symbol or order["status"] != "NEW" or order["type"] != "LIMIT":
                    continue
                crossed = ((order["side"] == "BUY" and ticker["ask_price"] <= order["price"])
                    or (order["side"] == "SELL" and ticker["bid_price"] >= order["price"]))
                if crossed:
                    px = min(order["price"], ticker["ask_price"]) if order["side"] == "BUY" else max(
                        order["price"], ticker["bid_price"])
                    self._fill(order, px, order["quantity"])
        return {symbol: float(value) for symbol, value in self.prices.items()}

    def advance_15m(self, scenario, step, duration_days=2):
        """Avanza una vela de quince minutos y ejecuta cruces por su rango OHLC."""
        self.scenario, self.step = str(scenario), int(step)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.now = start + timedelta(minutes=15 * int(step))
        total_bars = int(duration_days) * 24 * 12
        end_bar = int(step) * 3
        self.last_candles = {}
        for index, symbol in enumerate(self.symbols):
            initial = Decimal(str(100 * (index + 1)))
            previous = self.prices[symbol]
            prices = [previous]
            for bar in range(end_bar - 2, end_bar + 1):
                t = max(0, bar) / total_bars
                noise = .0015 * math.sin(bar * .71) + .001 * math.sin(bar * .193)
                if scenario == "subida":
                    common = .08 * t + noise
                elif scenario == "caida":
                    common = -.25 * t + noise
                else:
                    common = .04 * math.sin(2 * math.pi * 2 * t) + noise
                excursion = common * self.scales[symbol] + .0006 * math.sin(
                    bar * (.11 + index * .037))
                prices.append(initial * Decimal(str(1 + excursion)))
            current = prices[-1]
            candle = {"open": float(previous), "high": float(max(prices)),
                "low": float(min(prices)), "close": float(current),
                "start_bar": end_bar - 3}
            self.last_candles[symbol] = candle
            self.prices[symbol] = current
            for order in list(self.orders.values()):
                if order["symbol"] != symbol or order["status"] != "NEW" or order["type"] != "LIMIT":
                    continue
                crossed = ((order["side"] == "BUY" and
                    Decimal(str(candle["low"])) <= order["price"])
                    or (order["side"] == "SELL" and
                    Decimal(str(candle["high"])) >= order["price"]))
                if crossed:
                    self._fill(order, order["price"], order["quantity"])
        return {symbol: float(value) for symbol, value in self.prices.items()}

    def get_historical_klines(self, symbol, interval="5m", limit=10000):
        import pandas as pd
        cached = self.market_frames.get((symbol, interval, self.scenario, self.step))
        if cached is not None:
            return cached
        count = 21 * 24 * 12 + self.step + 1
        starts = self.step - count + 1
        closes = []
        for position in range(starts, self.step + 1):
            t = max(0, position) / (3 * 24 * 12)
            noise = .0015 * math.sin(position * .71) + .001 * math.sin(position * .193)
            common = ({"subida": .08 * t + noise, "caida": -.25 * t + noise}.get(
                self.scenario, .04 * math.sin(2 * math.pi * 3 * t) + noise))
            index = self.symbols.index(symbol)
            change = common * self.scales[symbol] + .0006 * math.sin(position * (.11 + index * .037))
            closes.append(100 * (index + 1) * (1 + change))
        timestamps = pd.date_range(end=self.now, periods=len(closes), freq="5min", tz="UTC")
        frame = pd.DataFrame({"timestamp": timestamps, "open": closes,
            "high": [value * 1.001 for value in closes],
            "low": [value * .999 for value in closes], "close": closes})
        self.market_frames[(symbol, interval, self.scenario, self.step)] = frame
        return frame
