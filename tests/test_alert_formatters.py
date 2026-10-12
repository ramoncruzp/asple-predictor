"""Fase 10 - pure message formatting for alerts."""
from __future__ import annotations

from decimal import Decimal

from alerts.formatters import (
    fmt, format_buy_fill, format_sell_fill, format_fills_group,
    format_buy_fill_line, format_sell_fill_line, format_operational,
    format_digest,
)


def test_fmt_avoids_scientific_notation_for_tiny_tick_sizes():
    tiny = Decimal(1) / Decimal(100_000_000)  # str(tiny) == "1E-8"
    assert str(tiny) == "1E-8"
    assert fmt(tiny) == "0.00000001"


def test_fmt_preserves_stored_precision_for_normal_prices():
    assert fmt("1.4520") == "1.4520"
    assert fmt("100.005") == "100.005"


def test_format_buy_fill_matches_the_requested_two_line_style():
    message = format_buy_fill("GRAMUSDT", 4, "12.5", "1.4520", "1.4750")
    assert message == (
        "\U0001F7E2 Compra ejecutada — GRAMUSDT #4\n"
        "Compró 12.5 a 1.4520. El bot espera vender a 1.4750."
    )


def test_format_sell_fill_matches_the_requested_two_line_style_with_commission():
    message = format_sell_fill("GRAMUSDT", 4, "12.5", "1.4750", "0.0367", "0.0073", 12)
    assert message == (
        "\U0001F4B0 Venta ejecutada — GRAMUSDT #4\n"
        "Vendió 12.5 a 1.4750. Ganancia neta: +0.0367 USDT (comisión 0.0073). Ciclos: 12."
    )


def test_format_sell_fill_omits_commission_when_not_stored():
    message = format_sell_fill("XRPUSDT", 7, "10", "1.46", "0.05", None, 3)
    assert "comisión" not in message
    assert message.endswith("Ganancia neta: +0.05 USDT. Ciclos: 3.")


def test_format_sell_fill_marks_losses_without_a_plus_sign():
    message = format_sell_fill("XRPUSDT", 7, "10", "1.40", "-0.02", None, None)
    assert "Ganancia neta: -0.02 USDT" in message


def test_format_sell_fill_close_uses_the_close_title():
    message = format_sell_fill("XRPUSDT", 7, "10", "1.40", "-0.02", None, None, is_close=True)
    assert message.startswith("\U0001F4B0 Venta (cierre) — XRPUSDT #7")


def test_format_fills_group_caps_at_ten_lines_and_counts_the_rest():
    lines = [format_buy_fill_line("1", "1.0", "1.1") for _ in range(13)]
    message = format_fills_group("XRPUSDT", 1, lines)
    body_lines = message.split("\n")
    assert body_lines[0] == "\U0001F501 Movimientos — XRPUSDT #1"
    assert len([line for line in body_lines[1:] if line.startswith("\U0001F7E2")]) == 10
    assert "y 3 más." in message


def test_format_sell_fill_line_is_compact_for_grouping():
    line = format_sell_fill_line("1", "1.1", "0.01", None, 2)
    assert line == "\U0001F4B0 Vendió 1 a 1.1: +0.01 USDT neto, ciclo 2"


def test_format_operational_uses_status_view_labels_and_includes_reason():
    event = {"event_type": "GRID_AUTO_CLOSE", "reason": "out_of_range", "details": {}}
    message = format_operational(event, "XRPUSDT #3")
    assert message == (
        "⚠️ Cierre automático a repositorio iniciado — XRPUSDT #3\n"
        "Motivo: out_of_range"
    )


def test_format_digest_lists_grids_and_health_lines():
    rows = [{"label": "XRPUSDT #1", "status": "ACTIVE", "cycles": 5,
             "pnl_today": "1.23", "pnl_total": "10.5", "open_orders": 2}]
    message = format_digest(rows, monitor_last_run="2026-10-11T08:00:00",
                             last_prediction="2026-10-11T07:55:00", last_vol="2026-10-11T07:50:00",
                             probe_ok=True)
    assert "XRPUSDT #1 (ACTIVE): 5 ciclos" in message
    assert "Sondeo Binance: OK" in message


def test_format_digest_formats_local_times_and_signed_four_decimal_pnl():
    rows = [
        {"label": "X", "status": "ACTIVE", "pnl_today": Decimal("0"), "pnl_total": Decimal("1.23456")},
        {"label": "Y", "status": "ACTIVE", "pnl_today": Decimal("-0.01234"), "pnl_total": Decimal("1e-7")},
    ]
    message = format_digest(
        rows, monitor_last_run=None, last_prediction=None, last_vol=None, probe_ok=True,
    )
    assert "+0.0000 USDT hoy / +1.2346 acumulado" in message
    assert "-0.0123 USDT hoy / +0.0000 acumulado" in message
    assert "sin datos" in message


def test_format_digest_handles_no_active_grids():
    message = format_digest([], monitor_last_run=None, last_prediction=None, last_vol=None, probe_ok=None)
    assert "sin grids activos" in message
    assert "Sondeo Binance: desconocido" in message


# -- Fase 10b item 3: a missing price must never print the word "None";
#    a missing quantity is simply omitted from the sentence -----------------

def test_fmt_of_none_never_prints_the_word_none():
    assert fmt(None) == "no disponible"
    assert "None" not in fmt(None)


def test_format_buy_fill_line_with_missing_price_says_unavailable_not_none():
    from alerts.formatters import fmt_price
    line = format_buy_fill_line("1", None, "1.1")
    assert "None" not in line
    assert "precio no disponible" in line


def test_format_buy_fill_line_with_missing_qty_omits_the_quantity():
    line = format_buy_fill_line(None, "1.0", "1.1")
    assert "None" not in line
    assert line == "\U0001F7E2 Compró a 1.0, vende a 1.1"


def test_format_sell_fill_line_with_missing_sell_price_says_unavailable():
    line = format_sell_fill_line("1", None, "0.01", None, 2)
    assert "None" not in line
    assert "precio no disponible" in line


def test_format_sell_fill_with_missing_qty_omits_the_quantity():
    message = format_sell_fill("XRPUSDT", 7, None, "1.40", "-0.02", None, None)
    assert "None" not in message
    assert "Vendió a 1.40." in message


def test_format_buy_fill_with_missing_buy_price_says_unavailable():
    message = format_buy_fill("XRPUSDT", 7, "1", None, "1.1")
    assert "None" not in message
    assert "precio no disponible" in message


# -- Fase 10b item 5: explicit literal-accent check (the audit saw apparent
#    mojibake in a PowerShell 5.1 dump; verified with Python it's not in the
#    actual file bytes -- see REPORTE_FASE10.md section on item 5) ---------

def test_format_buy_fill_uses_real_utf8_accents_and_em_dash():
    message = format_buy_fill("GRAMUSDT", 4, "12.5", "1.4520", "1.4750")
    assert "Compra ejecutada — GRAMUSDT #4" in message
    assert "Compró" in message
