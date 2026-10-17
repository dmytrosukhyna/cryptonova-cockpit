"""Arbitrage watch on fake books: net spread after both fees, window counting, sanity cap, depth, stuck anomalies, episodes."""
import sqlite3

from cnq.agents import arb_watch as aw

T = 1_700_000_000_000


def test_best_spread_net_of_fees_and_cap():
    books = {"Binance": {"BTC": (100.0, 100.01)}, "Bybit": {"BTC": (100.3, 100.31)}, "Gate": {"ETH": (10.0, 10.01)}, "MEXC": {"ETH": (11.0, 11.01)}}
    rows = aw.best_spreads(books)
    btc = next(r for r in rows if r["coin"] == "BTC")
    assert btc["buy"] == "Binance" and btc["sell"] == "Bybit"
    assert abs(btc["net"] - (100.3 / 100.01 - 1 - 0.002)) < 1e-6
    assert btc["size_usd"] is None                            # no sizes published → unknown depth
    assert not any(r["coin"] == "ETH" for r in rows)          # ~10 % gross → treated as a different token, ignored


def test_run_counts_windows_and_virtual_pnl():
    conn = sqlite3.connect(":memory:")
    books = {"Binance": {"BTC": (100.0, 100.0)}, "MEXC": {"BTC": (100.5, 100.5)}}   # +0.5 % gross, −0.15 % fees → +0.35 %
    d = aw.run(conn, now=T, fetch=lambda: books, samples=2, pause=0)
    assert d["day"]["samples"] == 2 and d["day"]["samples_with_window"] == 2 and d["day"]["windows"] == 1   # same route twice = once
    assert abs(d["day"]["virtual_pnl_usd"] - 1000 * (0.005 - 0.0015)) < 0.05
    assert set(d["venues_ok"]) == {"Binance", "MEXC"}
    assert "виртуальный счёт" in d["explain_ru"]


def test_repeating_route_counted_once_per_day():
    conn = sqlite3.connect(":memory:")
    books = {"Binance": {"BTC": (100.0, 100.0)}, "MEXC": {"BTC": (100.5, 100.5)}}
    aw.run(conn, now=T, fetch=lambda: books, samples=1, pause=0)
    d = aw.run(conn, now=T + 1_800_000, fetch=lambda: books, samples=2, pause=0)
    assert d["day"]["windows"] == 1 and d["day"]["stuck_routes"] == 1
    assert d["stuck"] and d["stuck"][0]["coin"] == "BTC"


def test_depth_caps_the_virtual_ticket():
    # 2 coins at the ask on Binance ($200) and 50 coins at the bid on MEXC → only $200 can be done
    books = {"Binance": {"XRP": (100.0, 100.0, 9.0, 2.0)}, "MEXC": {"XRP": (100.5, 100.6, 50.0, 1.0)}}
    r = aw.best_spreads(books)[0]
    assert r["buy"] == "Binance" and r["sell"] == "MEXC" and r["size_usd"] == 200.0
    conn = sqlite3.connect(":memory:")
    d = aw.run(conn, now=T, fetch=lambda: books, samples=1, pause=0)
    assert abs(d["day"]["virtual_pnl_usd"] - 200 * r["net"]) < 0.01 and d["day"]["median_size_usd"] == 200.0
    assert d["day"]["virtual_pnl_naive_usd"] == round(1000 * r["net"], 2)


def test_same_venue_anomaly_via_other_routes_is_stuck():
    """INJ cheap on HTX: HTX→MEXC first, later HTX→OKX — one anomaly, counted once; another coin stays fresh."""
    conn = sqlite3.connect(":memory:")
    a = {"HTX": {"INJ": (9.9, 10.0)}, "MEXC": {"INJ": (10.1, 10.11)}, "OKX": {"INJ": (10.05, 10.06)}}
    aw.run(conn, now=T, fetch=lambda: a, samples=1, pause=0)
    b = {"HTX": {"INJ": (9.9, 10.0), "OP": (1.0, 1.0)}, "MEXC": {"INJ": (10.0, 10.08)}, "OKX": {"INJ": (10.1, 10.11), "OP": (1.01, 1.01)}}
    d = aw.run(conn, now=T + 1_800_000, fetch=lambda: b, samples=1, pause=0)
    rw = {(w["coin"], w["buy"], w["sell"]) for w in d["recent_windows"]}
    assert ("INJ", "HTX", "MEXC") in rw and ("INJ", "HTX", "OKX") not in rw and ("OP", "HTX", "OKX") in rw
    assert d["day"]["windows"] == 2 and d["day"]["virtual_pnl_naive_usd"] > d["day"]["virtual_pnl_usd"]
    assert d["by_venue"]["HTX"] == 3


def test_legacy_rows_reclassified():
    conn = sqlite3.connect(":memory:")
    aw._ensure(conn)
    for i in range(3):   # written by the code before the dedupe existed: fresh defaulted to 1
        conn.execute("INSERT INTO arb_windows(time, coin, buy, sell, net) VALUES(?,?,?,?,?)", (T + i * 7000, "INJ", "HTX", "MEXC", 0.011))
    rows = aw.classify(conn)
    assert [r["fresh"] for r in rows] == [1, 0, 0]
    assert [f for (f,) in conn.execute("SELECT fresh FROM arb_windows ORDER BY time")] == [1, 0, 0]


def test_episodes_split_on_gaps():
    samples = [T, T + 7000, T + 14000, T + 1_800_000, T + 3_600_000]
    rows = [{"time": t, "coin": "BTC", "buy": "A", "sell": "B", "net": 0.001} for t in (T, T + 7000)] + \
           [{"time": t, "coin": "ETH", "buy": "A", "sell": "B", "net": 0.002} for t in (T + 1_800_000, T + 3_600_000)]
    e = aw.episodes(samples, rows)
    assert e["count"] == 2 and e["within_run"] == 1 and e["next_run"] == 1 and e["longest"]["coin"] == "ETH"


def test_binance_falls_back_to_market_data_mirror():
    def get(url):
        if "api.binance.com" in url: raise OSError("451")
        if "binance.vision" in url: return [{"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "100.1", "bidQty": "1", "askQty": "1"}]
        raise OSError("offline")
    books = aw.fetch_books(get)
    assert books == {"Binance": {"BTC": (100.0, 100.1, 1.0, 1.0)}}
