"""Cálculos deterministas para las políticas nuevas del torneo."""
from __future__ import annotations

from math import exp


class TournamentDecisionEngine:
    """Enruta cada competidor a métodos explícitos del operador de Meta."""
    available = True
    version = "torneo-1"

    def __init__(self, name):
        self.name = str(name)

    def plan(self, operator, goal, *, plan_kind, allocation_free_cash=None, record=True):
        planner = getattr(operator, "_plan_tournament_policy", None)
        if not callable(planner):
            raise RuntimeError(f"La política {self.name} todavía no tiene planificador conectado.")
        return planner(goal, self.name, plan_kind=plan_kind,
            allocation_free_cash=allocation_free_cash, record=record)

    def review(self, operator, goal, valuation, plan):
        reviewer = getattr(operator, "_review_tournament_policy", None)
        if not callable(reviewer):
            raise RuntimeError(f"La política {self.name} todavía no tiene revisor conectado.")
        return reviewer(goal, valuation, plan)

    def shadow_review(self, operator, goal, valuation, plan):
        return {"decision": "solo_registro", "source": "torneo_policy_shadow"}


def ladder_orders(mid, sigma_24h, capital=48.0, min_margin_pct=.3,
        reprices_today=0, bought=(), previous=()):
    """Construye cuatro órdenes de Escalera y respeta el límite de repricios."""
    mid, sigma = float(mid), float(sigma_24h)
    if mid <= 0 or sigma <= 0:
        return []
    offsets = (.5, 1.0, 1.5, 2.0)
    unit = float(capital) / 4
    sell_move = max(sigma, float(min_margin_pct) / 100)
    orders = []
    for index, multiple in enumerate(offsets):
        prior = dict(bought[index]) if index < len(bought) and bought[index] else None
        if prior:
            orders.append({"bot": index, "buy_price": prior["buy_price"],
                "sell_price": prior["buy_price"] * (1 + sell_move), "bought": True,
                "repriced": False})
            continue
        buy = mid * exp(-multiple * sigma)
        buy = min(buy, mid * exp(-.3 * sigma))
        allowed = int(reprices_today) < 6
        if not allowed and index < len(previous) and previous[index]:
            buy = float(previous[index]["buy_price"])
        orders.append({"bot": index, "buy_price": buy,
            "sell_price": buy * (1 + sell_move), "capital": unit,
            "bought": False, "repriced": allowed})
    return orders


def follower_geometry(mid, sigma_24h, *, levels=8, inventory_pct=0, cash_pct=0):
    """Calcula rango simétrico de seguidor y sesgo del siguiente paso."""
    mid, sigma = float(mid), float(sigma_24h)
    if mid <= 0 or sigma <= 0 or int(levels) != 8:
        raise ValueError("El grid seguidor requiere precio y sigma positivos y ocho niveles.")
    spacing = .9 * sigma
    low, high = mid * exp(-4 * spacing), mid * exp(4 * spacing)
    if float(inventory_pct) > 60:
        bias = {"buy_shift_levels": -1, "sell_closer": True}
    elif float(cash_pct) > 80:
        bias = {"buy_shift_levels": 1, "sell_closer": False}
    else:
        bias = {"buy_shift_levels": 0, "sell_closer": False}
    return {"low": low, "high": high, "levels": 8,
        "spacing_sigma": .9, "bias": bias,
        "recenter_cooldown_hours": 6,
        "recenter_trigger_levels": 1}


def follower_recenter(mid, current_low, current_high, sigma_24h, *,
        last_recenter_s=None, now_s=None, inventory=()):
    """Recentra fuera de rango y reasigna inventario a ventas no deficitarias."""
    mid = float(mid)
    step = (float(current_high) / float(current_low)) ** (1 / 8) - 1
    outside = (mid < float(current_low) / (1 + step) or
        mid > float(current_high) * (1 + step))
    if not outside:
        return {"recenter": False, "inventory_sells": []}
    if last_recenter_s is not None and now_s is not None and float(now_s) - float(last_recenter_s) < 6 * 3600:
        return {"recenter": False, "reason": "cooldown", "inventory_sells": []}
    geometry = follower_geometry(mid, sigma_24h)
    spacing = (geometry["high"] / geometry["low"]) ** (1 / 8) - 1
    sells = []
    for piece in inventory:
        qty, cost = float(piece.get("quantity") or 0), float(piece.get("cost_price") or 0)
        if qty <= 0 or cost <= 0:
            continue
        sells.append({"quantity": qty, "price": max(cost * 1.003, mid * (1 + spacing))})
    return {"recenter": True, "geometry": geometry, "inventory_sells": sells}


def control_geometry(mid, sigma_24h):
    """Grid de control fijo: ocho niveles sobre ±2σ al inicio."""
    mid, sigma = float(mid), float(sigma_24h)
    if mid <= 0 or sigma <= 0:
        raise ValueError("El control requiere precio y sigma positivos.")
    return {"low": mid * exp(-2 * sigma), "high": mid * exp(2 * sigma),
        "levels": 8, "interventions": False}
