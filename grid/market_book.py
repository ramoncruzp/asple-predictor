"""Pure helpers for validating a Binance-style book ticker before using it as a price.

A one-sided book (bid or ask <= 0, e.g. Testnet with no counter-side liquidity)
or a crossed book (ask < bid) must never be averaged into a midpoint -- that
silently produces a fabricated price. See REPORTE_FASE26.md for the incident
that motivated this (grid #1, PEPEUSDT, 2026-10-11).
"""
from __future__ import annotations

from decimal import Decimal


def book_is_two_sided(book: dict) -> bool:
    """True iff both sides of the book are present, positive and not crossed."""
    bid = Decimal(str(book["bid_price"]))
    ask = Decimal(str(book["ask_price"]))
    return bid > 0 and ask > 0 and ask >= bid


def book_mid(book: dict) -> Decimal | None:
    """Exact midpoint, or None if the book is one-sided or crossed."""
    if not book_is_two_sided(book):
        return None
    bid = Decimal(str(book["bid_price"]))
    ask = Decimal(str(book["ask_price"]))
    return (bid + ask) / Decimal(2)
