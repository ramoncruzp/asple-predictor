"""Fase 26 - book_mid / book_is_two_sided: single source of truth for a valid book."""
from __future__ import annotations

from decimal import Decimal

from grid.market_book import book_is_two_sided, book_mid


def test_one_sided_ask_zero_is_invalid():
    book = {"bid_price": Decimal("4.15e-06"), "ask_price": Decimal("0")}
    assert book_is_two_sided(book) is False
    assert book_mid(book) is None


def test_one_sided_bid_zero_is_invalid():
    book = {"bid_price": Decimal("0"), "ask_price": Decimal("4.58e-06")}
    assert book_is_two_sided(book) is False
    assert book_mid(book) is None


def test_crossed_book_is_invalid():
    book = {"bid_price": Decimal("4.58e-06"), "ask_price": Decimal("4.15e-06")}
    assert book_is_two_sided(book) is False
    assert book_mid(book) is None


def test_normal_book_returns_exact_average():
    book = {"bid_price": Decimal("99.99"), "ask_price": Decimal("100.01")}
    assert book_is_two_sided(book) is True
    assert book_mid(book) == Decimal("100.00")


def test_incident_regression_book_from_2026_10_11():
    # grid #1 PEPEUSDT: bidPrice 0.00000415, askPrice 0.00000000.
    book = {"bid_price": Decimal("0.00000415"), "ask_price": Decimal("0.00000000")}
    assert book_is_two_sided(book) is False
    assert book_mid(book) is None
