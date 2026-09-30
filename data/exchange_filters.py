"""Exact Decimal handling for Binance symbol trading filters."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class BandRules:
    bid_up: Decimal
    bid_down: Decimal
    ask_up: Decimal
    ask_down: Decimal


class FilterViolation(ValueError):
    def __init__(self, filter_name: str, message: str):
        self.filter_name = filter_name
        super().__init__(f"{filter_name}: {message}")


@dataclass(frozen=True)
class SymbolFilters:
    tick_size: Decimal
    min_price: Decimal
    max_price: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal
    apply_min_to_market: bool
    max_num_orders: int | None
    band: BandRules | None = None

    @classmethod
    def from_symbol_info(cls, info: dict[str, Any]) -> "SymbolFilters":
        by_type = {item["filterType"]: item for item in info.get("filters", [])}
        price = by_type["PRICE_FILTER"]
        lot = by_type["LOT_SIZE"]
        notional = by_type.get("NOTIONAL") or by_type.get("MIN_NOTIONAL")
        if notional is None:
            raise ValueError("Symbol information has no NOTIONAL or MIN_NOTIONAL filter.")
        if "NOTIONAL" in by_type:
            min_notional = notional.get("minNotional", "0")
            apply_min = bool(notional.get("applyMinToMarket", False))
        else:
            min_notional = notional.get("minNotional", "0")
            apply_min = bool(notional.get("applyToMarket", False))

        by_side = by_type.get("PERCENT_PRICE_BY_SIDE")
        common = by_type.get("PERCENT_PRICE")
        if by_side:
            band = BandRules(
                bid_up=_decimal(by_side["bidMultiplierUp"]),
                bid_down=_decimal(by_side["bidMultiplierDown"]),
                ask_up=_decimal(by_side["askMultiplierUp"]),
                ask_down=_decimal(by_side["askMultiplierDown"]),
            )
        elif common:
            band = BandRules(
                bid_up=_decimal(common["multiplierUp"]),
                bid_down=_decimal(common["multiplierDown"]),
                ask_up=_decimal(common["multiplierUp"]),
                ask_down=_decimal(common["multiplierDown"]),
            )
        else:
            band = None

        max_filter = by_type.get("MAX_NUM_ORDERS")
        max_orders = None if max_filter is None else int(max_filter.get("maxNumOrders", 0))
        return cls(
            tick_size=_decimal(price["tickSize"]),
            min_price=_decimal(price["minPrice"]),
            max_price=_decimal(price["maxPrice"]),
            step_size=_decimal(lot["stepSize"]),
            min_qty=_decimal(lot["minQty"]),
            max_qty=_decimal(lot["maxQty"]),
            min_notional=_decimal(min_notional),
            apply_min_to_market=apply_min,
            max_num_orders=max_orders,
            band=band,
        )

    def round_price(self, price: Decimal | str | float, mode: str = "nearest") -> Decimal:
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive.")
        rounding = {"nearest": ROUND_HALF_UP, "down": ROUND_FLOOR, "up": ROUND_CEILING}.get(mode)
        if rounding is None:
            raise ValueError("mode must be nearest, down, or up.")
        units = (_decimal(price) / self.tick_size).to_integral_value(rounding=rounding)
        return units * self.tick_size

    def round_qty_down(self, qty: Decimal | str | float) -> Decimal:
        if self.step_size <= 0:
            raise ValueError("step_size must be positive.")
        units = (_decimal(qty) / self.step_size).to_integral_value(rounding=ROUND_FLOOR)
        return units * self.step_size

    def validate_order(
        self,
        side: str,
        price: Decimal | str | float,
        qty: Decimal | str | float,
        avg_price: Decimal | str | float,
    ) -> None:
        side = str(side).upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL.")
        price_d, qty_d, avg_d = _decimal(price), _decimal(qty), _decimal(avg_price)
        if self.tick_size > 0 and price_d % self.tick_size != 0:
            raise FilterViolation("PRICE_FILTER", "price is not a tickSize multiple")
        if price_d < self.min_price or (self.max_price > 0 and price_d > self.max_price):
            raise FilterViolation("PRICE_FILTER", "price is outside minPrice/maxPrice")
        if qty_d <= 0 or (self.step_size > 0 and qty_d % self.step_size != 0):
            raise FilterViolation("LOT_SIZE", "quantity is not a positive stepSize multiple")
        if qty_d < self.min_qty or (self.max_qty > 0 and qty_d > self.max_qty):
            raise FilterViolation("LOT_SIZE", "quantity is outside minQty/maxQty")
        if price_d * qty_d < self.min_notional:
            raise FilterViolation("NOTIONAL", "price * quantity is below minNotional")
        if self.band is not None:
            if avg_d <= 0:
                raise FilterViolation("PERCENT_PRICE_BY_SIDE", "avg_price must be positive")
            low, high = (
                (avg_d * self.band.bid_down, avg_d * self.band.bid_up)
                if side == "BUY"
                else (avg_d * self.band.ask_down, avg_d * self.band.ask_up)
            )
            if not low <= price_d <= high:
                raise FilterViolation("PERCENT_PRICE_BY_SIDE", "price is outside the average-price band")
