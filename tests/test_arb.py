"""Arbitrage watch on fake books: net spread after both fees, window counting, sanity cap."""
import sqlite3

from cnq.agents import arb_watch as aw


def test_best_spread_net_of_fees_and_cap():
    books = {"Binance": {"BTC": (100.0, 100.01)}, "Bybit": {"BTC": (100.3, 100.31)}, "Gate": {"ETH": (10.0, 10.01)}, "MEXC": {"ETH": (11.0, 11.01)}}
    rows = aw.best_spreads(books)
    btc = next(r for r in rows if r["coin"] == "BTC")
    assert btc["buy"] == "Binance" and btc["sell"] == "Bybit"
    assert abs(btc["net"] - (100.3 / 100.01 - 1 - 0.002)) < 1e-6
    assert not any(r["coin"] == "ETH" for r in rows)          # ~10 % gross → treated as a different token, ignored


def test_run_counts_windows_and_virtual_pnl():
    conn = sqlite3.connect(":memory:")
    books = {"Binance": {"BTC": (100.0, 100.0)}, "MEXC": {"BTC": (100.5, 100.5)}}   # +0.5 % gross, −0.15 % fees → +0.35 %
    d = aw.run(conn, now=1_700_000_000_000, fetch=lambda: books, samples=2, pause=0)
    assert d["day"]["samples"] == 2 and d["day"]["samples_with_window"] == 2 and d["day"]["windows"] == 1   # same route twice = once
    assert abs(d["day"]["virtual_pnl_usd"] - 1000 * (0.005 - 0.0015)) < 0.05
    assert set(d["venues_ok"]) == {"Binance", "MEXC"}


def test_repeating_route_counted_once_per_day():
    conn = sqlite3.connect(":memory:")
    books = {"Binance": {"BTC": (100.0, 100.0)}, "MEXC": {"BTC": (100.5, 100.5)}}
    aw.run(conn, now=1_700_000_000_000, fetch=lambda: books, samples=1, pause=0)
    d = aw.run(conn, now=1_700_000_000_000 + 1_800_000, fetch=lambda: books, samples=2, pause=0)
    assert d["day"]["windows"] == 1 and d["day"]["stuck_routes"] == 1
    assert d["stuck"] and d["stuck"][0]["coin"] == "BTC"
