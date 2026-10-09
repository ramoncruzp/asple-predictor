"""Thin API orchestration for guarded grid controls."""
from __future__ import annotations

import threading
import logging
import time
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from grid.policy import DEFAULT_GRID_FEE_PCT, DEFAULT_SMART_PARAMS, validate_params, plan_dust_sweep, plan_profit_close

_LOCK_INIT = threading.Lock()
CONTROL_LOCK_TIMEOUT_SECONDS = 15.0
_SLOW_LOCK_SECONDS = 3.0
logger = logging.getLogger("asple.slow")


def _d(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _testnet(client: Any) -> bool:
    return client is not None and getattr(getattr(client, "client", None), "testnet", None) is True


def _lock(request):
    monitor = getattr(request.app.state, "grid_monitor", None)
    engine = request.app.state.grid_engine
    lock = getattr(monitor, "ops_lock", None) if monitor is not None else None
    if lock is None:
        lock = getattr(request.app.state, "grid_control_ops_lock", None)
        if lock is None:
            with _LOCK_INIT:
                lock = getattr(request.app.state, "grid_control_ops_lock", None)
                if lock is None:
                    lock = threading.RLock()
                    request.app.state.grid_control_ops_lock = lock
    return lock


def run_action(request, grid_id: int, action: str, body: dict) -> dict:
    if not _testnet(getattr(request.app.state, "testnet_client", None)) \
            or getattr(request.app.state, "grid_engine", None) is None:
        raise HTTPException(503, "Grid Testnet no disponible o no confirmado.")
    lock = _lock(request)
    _acquire_lock(lock, grid_id, action)
    try:
        return _run_action_locked(request, grid_id, action, body)
    finally:
        lock.release()


def _acquire_lock(lock, grid_id: int, action: str) -> None:
    started = time.monotonic()
    if not lock.acquire(timeout=CONTROL_LOCK_TIMEOUT_SECONDS):
        waited = time.monotonic() - started
        if waited > _SLOW_LOCK_SECONDS:
            logger.warning("grid control lock wait action=%s grid_id=%s waited_seconds=%.3f",
                           action, grid_id, waited)
        raise HTTPException(409, "Hay otra operación en curso; reintenta en unos segundos.")
    waited = time.monotonic() - started
    if waited > _SLOW_LOCK_SECONDS:
        logger.warning("grid control lock wait action=%s grid_id=%s waited_seconds=%.3f",
                       action, grid_id, waited)


def _run_action_locked(request, grid_id: int, action: str, body: dict) -> dict:
    client = getattr(request.app.state, "testnet_client", None)
    engine = getattr(request.app.state, "grid_engine", None)
    db = request.app.state.db
    if not _testnet(client) or engine is None:
        raise HTTPException(503, "Grid Testnet no disponible o no confirmado.")
    grid = db.get_grid(int(grid_id))
    if grid is None:
        raise HTTPException(404, "Grid no existe.")
    levels = db.get_grid_levels(int(grid_id))
    status = str(grid.get("status", "")).upper()
    params = dict(body)
    dry_run, confirm = params.pop("dry_run", True), params.pop("confirm", False)
    confirm_text = params.pop("confirm_text", None)
    def reject(status_code: int, detail: str):
        db.add_grid_event(run_id=None, source="CLI", grid_id=grid_id,
                          event_type="GRID_ACTION_REJECTED", reason=action,
                          details={"who": "api", "action": action, "reason": detail})
        raise HTTPException(status_code, detail)
    if dry_run and confirm or not dry_run and not confirm:
        reject(422, "Ejecutar requiere dry_run=false y confirm=true.")
    if action == "pause" and status != "ACTIVE": reject(409, "Solo se puede pausar un grid ACTIVE.")
    if action == "resume" and status != "PAUSED": reject(409, "Solo se puede reanudar un grid PAUSED.")
    if action == "adjust" and status != "ACTIVE": reject(409, "Solo se puede ajustar un grid ACTIVE.")
    if action == "close" and status not in {"ACTIVE", "PAUSED"}: reject(409, "El grid ya está cerrándose o terminal.")
    runtime_params = grid.get("params") or {}
    target_plan = runtime_params.get("target_close_plan")
    max_days_plan = runtime_params.get("max_days_close_plan")
    if action == "params" and (status in {"CLOSING", "CLOSED", "HOLDING"} or
        (isinstance(target_plan, dict) and target_plan.get("phase") != "COMPLETE") or
        (isinstance(max_days_plan, dict) and max_days_plan.get("phase") == "STARTED")):
        reject(409, "No se pueden editar parámetros durante un cierre.")

    market = None
    optional_price = action == "pause" or (action == "close" and params.get("mode") in {"cancel", "repository"})
    if action in {"pause", "resume", "close", "sweep-dust", "adjust"}:
        try: market = client.get_book_ticker(grid["symbol"])
        except Exception as exc:
            if not optional_price:
                raise HTTPException(503, "No se pudo obtener precio fiable de Testnet.") from exc
    bid = _d(market.get("bid_price")) if market else Decimal(0)
    held = sum((_d(row.get("held_qty")) for row in levels), Decimal(0))
    unrealized = sum(((bid - _d(row.get("entry_price"))) * _d(row.get("held_qty"))
                      for row in levels), Decimal(0)) if market else None
    try:
        open_orders = client.get_open_orders(grid["symbol"]) if action in {"pause", "close"} else []
    except Exception as exc:
        raise HTTPException(503, "No se pudieron consultar las órdenes actuales de Testnet.") from exc
    buy_orders = [row for row in open_orders if str(row.get("side", "")).upper() == "BUY"]
    sell_orders = [row for row in open_orders if str(row.get("side", "")).upper() == "SELL"]
    plan: dict[str, Any] = {"action": action, "grid_id": int(grid_id), "status_before": status,
                            "bid_used": None if market is None else str(bid),
                            "open_orders_to_cancel": len(buy_orders) if action == "pause" else len(open_orders),
                            "open_buy_orders_to_cancel": len(buy_orders),
                            "open_sell_orders_remain": len(sell_orders) if action == "pause" else 0,
                            "held_qty": str(held),
                            "held_market_value_usdt": None if market is None else str(held * bid),
                            "price_note": None if market is not None else "precio no disponible",
                            "unrealized_pnl_usdt": None if unrealized is None else str(unrealized),
                            "testnet_warning": "Testnet no representa el mercado real."}
    if action == "pause":
        raw_reason = str(params.get("reason") or "api_pause")
        reason = "".join(char for char in raw_reason if char.isprintable())[:120] or "api_pause"
        plan.update(reason=reason, resulting_status="PAUSED")
    elif action == "resume":
        low, high = _d(grid.get("range_low")), _d(grid.get("range_high"))
        plan.update(resulting_status="ACTIVE", bid=str(bid), outside_range=not low <= bid <= high,
                    below_all_levels=bool(levels and bid < min(_d(x.get("price")) for x in levels)))
    elif action == "close":
        mode = params.get("mode")
        if mode not in {"cancel", "liquidate", "repository", "profit_repository"}: reject(422, "mode es obligatorio: cancel, liquidate, repository o profit_repository.")
        if mode == "liquidate" and (confirm_text != "LIQUIDAR" or not confirm):
            if not dry_run: reject(422, "Liquidate requiere confirm_text=LIQUIDAR.")
        plan["open_orders_to_cancel"] = len(buy_orders) if mode in {"repository", "profit_repository"} else len(open_orders)
        plan["open_sell_orders_remain"] = len(sell_orders) if mode in {"repository", "profit_repository"} else 0
        repository_cells = [row for row in levels if row.get("state") == "SELL_OPEN"
                            and row.get("order_id") is not None and _d(row.get("held_qty")) > 0]
        unmanaged_qty = sum((_d(row.get("held_qty")) for row in levels
                             if row not in repository_cells), Decimal(0))
        plan.update(mode=mode,
                    retained_qty=str(held if mode == "cancel" else unmanaged_qty if mode == "repository" else 0),
                    qty_to_repository=str(sum((_d(row.get("held_qty")) for row in repository_cells), Decimal(0)))
                                         if mode == "repository" else "0",
                    estimated_commission_usdt=str(held * bid * Decimal("0.001")) if mode == "liquidate" else "0",
                    cells_to_repository=len(repository_cells) if mode == "repository" else 0,
                    unmanaged_inventory_qty=str(unmanaged_qty) if mode == "repository" else "0",
                    unrealized_pnl_materialized=str(unrealized or 0) if mode == "liquidate" else "0",
                    resulting_status="CLOSED")
        held_cells = [row for row in levels if _d(row.get("held_qty")) > 0]
        if mode == "liquidate":
            results = [(bid * _d(row.get("held_qty")) * Decimal("0.999")
                        - _d(row.get("entry_price")) * _d(row.get("held_qty")) * Decimal("1.001"))
                       for row in held_cells]
            plan.update(cells_total=len(held_cells),
                        cells_winning=sum(value > 0 for value in results),
                        cells_losing=sum(value <= 0 for value in results),
                        net_result_usdt=str(sum(results, Decimal(0))))
        elif mode == "profit_repository":
            if bid <= 0: raise HTTPException(503, "No hay bid fiable para estimar el cierre inteligente.")
            try:
                filters, _snapshot, _avg = engine._market_context(grid["symbol"])
                preview_clock = getattr(engine, "_monotonic", time.monotonic)
                estimated_fee_cells = [0]
                cells = engine._build_profit_cells(
                    grid_id, grid["symbol"], held_cells, use_cache=True,
                    deadline=preview_clock() + 6.0, clock=preview_clock,
                    on_fee_estimated=lambda: estimated_fee_cells.__setitem__(
                        0, estimated_fee_cells[0] + 1))
                selected = plan_profit_close(cells, float(bid), filters, DEFAULT_GRID_FEE_PCT)
            except Exception as exc:
                raise HTTPException(503, "Filtros de mercado Testnet no disponibles.") from exc
            plan.update(cells_to_sell=len(selected["sell_cells"]),
                sell_gain_usdt=str(selected["net_gain_usdt"]),
                cells_to_repository=len(selected["repo_cells"]),
                repo_unrealized_usdt=str(selected["net_loss_usdt"]),
                estimated_commission_usdt=str(selected["estimated_commission_usdt"]),
                estimated_fee_cells=estimated_fee_cells[0],
                bid_used=str(selected["bid_used"]),
                sell_plan=selected["sell_cells"], repository_plan=selected["repo_cells"])
    elif action == "adjust":
        try: result = engine.preview_adjust(grid_id, params["new_low"], params["new_high"], params.get("n"))
        except Exception as exc: raise HTTPException(422, "No se pudo validar el rango.") from exc
        if not result.get("ok"): reject(422, result.get("reason") or "Rango no válido.")
        plan["adjustment"] = result.get("plan")
        plan["adjustment_reason"] = result.get("reason")
        plan["requested_range"] = {"low": str(params["new_low"]), "high": str(params["new_high"]),
                                   "n_levels": params.get("n") or grid.get("n_levels")}
    elif action == "sweep-dust":
        if bid <= 0: raise HTTPException(503, "No hay bid fiable para valorar el polvo.")
        try: filters, _, _ = engine._market_context(grid["symbol"])
        except Exception as exc: raise HTTPException(503, "Filtros de mercado Testnet no disponibles.") from exc
        cell_dust = sum((_d(row.get("held_qty")) for row in levels
                         if row.get("state") == "DONE" and _d(row.get("held_qty")) > 0), Decimal(0))
        sweepable = max(Decimal(0), _d(grid.get("dust_qty")) - cell_dust)
        plan["dust"] = plan_dust_sweep(sweepable, bid, filters, .1)
    elif action == "params":
        allowed = {"target_pct", "target_usdt", "target_basis", "max_days", "dust_sweep_threshold_pct",
                   "compound_enabled", "compound_ratio", "compound_max_growth_pct", "adjust_idle_shrink"}
        updates = {key: value for key, value in params.items() if key in allowed}
        if not updates or set(params) != set(updates): reject(422, "Parámetros no permitidos.")
        if updates.get("target_pct") is not None and updates.get("target_usdt") is not None:
            reject(422, "target_pct y target_usdt son mutuamente excluyentes.")
        if grid.get("strategy") == "simple" and set(updates) - {
            "max_days", "compound_enabled", "compound_ratio", "compound_max_growth_pct"
        }:
            reject(422, "El motor simple solo admite plazo e interés compuesto.")
        if updates.get("adjust_idle_shrink") is True and not getattr(
                request.app.state.settings, "adjust_idle_shrink_enabled", False):
            reject(422, "interruptor global apagado (ADJUST_IDLE_SHRINK_ENABLED en el .env)")
        remove = {key for key, value in updates.items() if value is None}
        if updates.get("target_pct") is not None: remove.add("target_usdt")
        if updates.get("target_usdt") is not None: remove.add("target_pct")
        business_keys = set(DEFAULT_SMART_PARAMS) | {"target_pct", "target_usdt", "target_basis",
                                                       "max_days", "dust_sweep_threshold_pct", "compound_enabled",
                                                       "compound_ratio", "compound_max_growth_pct"}
        merged = {k:v for k,v in (grid.get("params") or {}).items() if k in business_keys}
        merged.update({k:v for k,v in updates.items() if v is not None})
        for key in remove: merged.pop(key, None)
        try: validate_params(merged, int(grid.get("n_levels") or len(levels)))
        except Exception: reject(422, "Parámetros inválidos.")
        plan["updates"] = updates
        plan["remove"] = sorted(remove)
    else:
        raise HTTPException(404, "Acción no reconocida.")
    if dry_run:
        return {"dry_run": True, "plan": plan}
    lock = _lock(request)
    _acquire_lock(lock, grid_id, action)
    try:
        before = status
        try:
            if action == "pause": result = engine.pause_grid(grid_id, plan["reason"], {"source": "API"})
            elif action == "resume": result = engine.resume_grid(grid_id, "api_resume", {"source": "API"})
            elif action == "close": result = engine.close_grid(grid_id, params["mode"], close_reason="api")
            elif action == "adjust": result = engine.adjust_grid(grid_id, params["new_low"], params["new_high"], params.get("n"), reason="api", details={"source": "API"})
            elif action == "sweep-dust": result = engine.sweep_grid_dust(grid_id, bid, reason="api")
            else:
                db.merge_grid_params(grid_id, plan["updates"], remove=set(plan["remove"]),
                                      allowed=frozenset({"target_pct", "target_usdt", "target_basis", "max_days",
                                                         "dust_sweep_threshold_pct", "compound_enabled",
                                                         "compound_ratio", "compound_max_growth_pct",
                                                         "adjust_idle_shrink"}))
                result = {"ok": True, "params": db.get_grid(grid_id).get("params")}
            if isinstance(result, dict) and result.get("ok") is False:
                raise RuntimeError(result.get("reason") or "acción rechazada por el motor")
            after = (db.get_grid(grid_id) or {}).get("status")
            errors = result.get("errors") if isinstance(result, dict) else None
            expected = {"pause": "PAUSED", "resume": "ACTIVE", "close": "CLOSED",
                        "adjust": "ACTIVE", "sweep-dust": status, "params": status}[action]
            partial = bool(errors) or str(after or "").upper() == "CLOSING" or (
                isinstance(result, dict) and str(result.get("status", "")).upper() == "CLOSING") or (
                after is not None and str(after).upper() != expected)
            outcome = "partial" if partial else "completed"
            db.add_grid_event(run_id=None, source="CLI", grid_id=grid_id, event_type="GRID_ACTION_API",
                              reason=action, details={"who": "api", "action": action, "parameters": params,
                              "before": before, "after": after, "status_after": after, "outcome": outcome,
                              "errors": errors or [], "dry_run": False, "result": result})
            if hasattr(request.app.state, "grid_account_summary_cache"):
                del request.app.state.grid_account_summary_cache
            return {"dry_run": False, "result": result, "status": after,
                    "status_after": after, "outcome": outcome, "errors": errors or []}
        except HTTPException: raise
        except Exception as exc:
            db.add_grid_event(run_id=None, source="CLI", grid_id=grid_id, event_type="GRID_ACTION_REJECTED",
                              reason=action, details={"who": "api", "error": type(exc).__name__})
            raise HTTPException(409, "El motor rechazó la acción; revisa el estado actual del grid.") from None
    finally:
        lock.release()
