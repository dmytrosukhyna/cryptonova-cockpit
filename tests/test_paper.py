"""Paper desk on synthetic candles: limit fill at a zone, ladder exits, stop-first, equity accounting."""
import sqlite3

from cnq.agents import paper_desk as pd


def mk(t0, prices, step=900_000):
    out = []
    for i, p in enumerate(prices):
        out.append([t0 + i * step, p, p * 1.004, p * 0.996, p, 100.0])
    return out


def coin(symbol, prices, levels, t0=1_700_000_000_000):
    return {"symbol": symbol, "candles": mk(t0, prices), "levels": levels, "regime": {"long": "BULL"}}


def test_limit_fill_targets_and_stop():
    conn = sqlite3.connect(":memory:")
    desk = pd.PaperDesk(conn, classes={"A", "B", "R", "C"})   # class C is observation-only on the real desks; enable it here to test limit fills
    t0 = 1_700_000_000_000
    flat = [100.0] * 300
    c = coin("BTC-USDT", flat, [{"id": "L1", "kind": "support", "lo": 99.6, "hi": 99.9, "center": 99.75, "touches": 3, "status": "ACTIVE"}])
    now = t0 + 300 * 900_000
    res = desk.step([c], True, now)
    assert res["pending"] and res["pending"][0]["side"] == "LONG" and abs(res["pending"][0]["entry"] - 99.9) < 1e-9
    # price dips into the zone, then rallies through all three targets
    entry, stop = res["pending"][0]["entry"], res["pending"][0]["stop"]
    r = entry - stop
    path = flat + [99.85] + [entry + r * 1.2, entry + r * 2.2, entry + r * 3.2]
    c2 = coin("BTC-USDT", path, c["levels"])
    now2 = t0 + len(path) * 900_000 + 1
    res2 = desk.step([c2], True, now2)
    assert res2["stats"]["trades"] == 1 and res2["closed"][0]["exit_kind"] == "TARGET"
    assert res2["closed"][0]["pnl_r"] > 1.2 and res2["equity"] > 10_000
    # new signal, stopped out: equity goes down by ~1 % of equity
    eq_before = res2["equity"]
    path3 = path + [100.0] * 3 + [99.95]            # price back at the zone edge → new limit order
    res3 = desk.step([coin("BTC-USDT", path3, c["levels"])], True, t0 + len(path3) * 900_000 + 1)
    assert res3["pending"], "second limit order expected at the zone"
    path4 = path3 + [99.85, stop * 0.99]             # fills, then crashes through the stop
    res3 = desk.step([coin("BTC-USDT", path4, c["levels"])], True, t0 + len(path4) * 900_000 + 1)
    assert res3["stats"]["trades"] == 2 and res3["closed"][0]["exit_kind"] == "STOP"
    assert -1.2 < res3["closed"][0]["pnl_r"] < -0.9 and res3["equity"] < eq_before
    assert res3["stats"]["winrate"] == 0.5 and res3["equity_history"]


def test_alt_longs_blocked_in_bear_and_cascade_market_entry():
    conn = sqlite3.connect(":memory:")
    desk = pd.PaperDesk(conn)
    t0 = 1_700_000_000_000
    flat = [10.0] * 300
    c = coin("SOL-USDT", flat, [{"id": "L1", "kind": "support", "lo": 9.96, "hi": 9.99, "center": 9.975, "touches": 2, "status": "ACTIVE"}])
    assert not desk.step([c], False, t0 + 300 * 900_000)["pending"]
    # cascade: a −6 % candle with a long lower wick → market long
    prices = [10.0] * 300 + [10.0]
    cs = mk(t0, prices)
    cs[-2] = [cs[-2][0], 10.0, 10.0, 9.2, 9.6, 500.0]   # open 10, low 9.2, close 9.6 → lower wick 50 % of range
    cs[-1] = [cs[-1][0], 9.6, 9.65, 9.55, 9.6, 100.0]
    c2 = {"symbol": "SOL-USDT", "candles": cs, "levels": [], "regime": {"long": "BULL"}}
    res = desk.step([c2], True, cs[-1][0] + 1)
    assert res["open"] and res["open"][0]["grade"] == "B" and res["open"][0]["side"] == "LONG"


def test_retest_scan_and_signal():
    """Synthetic 4h series: flat 30 bars, breakout bar on volume, then a retest bar that closes back above the level."""
    from cnq.agents.paper_desk import PaperDesk, agg4h, retest_scan
    import sqlite3
    H = 14_400_000
    b4 = []
    t0 = 1_700_000_000_000 - (1_700_000_000_000 % H)
    for i in range(60):
        b4.append([t0 + i * H, 100.0, 101.0, 99.0, 100.0, 10.0])       # flat, ATR ≈ 2
    b4.append([t0 + 60 * H, 100.0, 104.0, 99.5, 103.0, 20.0])          # breakout: close 103 > 101 + 0.3·ATR, vol 2×
    b4.append([t0 + 61 * H, 103.0, 103.5, 102.5, 103.0, 10.0])
    rt = retest_scan(b4)
    assert rt and rt["side"] == "LONG" and rt["level"] == 101.0 and rt["i_retest"] is None
    b4.append([t0 + 62 * H, 103.0, 103.2, 101.5, 102.5, 10.0])          # retest: low within 0.5 ATR of 101, close above
    rt = retest_scan(b4)
    assert rt and rt["i_retest"] == 62 and rt["bars_since_retest"] == 0
    # 15m expansion for the desk: each 4h bar → 16 identical 15m candles
    c15 = []
    for b in b4:
        for k in range(16):
            c15.append([b[0] + k * 900_000, b[1], b[2], b[3], b[4], b[5] / 16])
    now = c15[-1][0] + 900_000 + H   # next 4h bar has started, last retest bar is closed
    desk = PaperDesk(sqlite3.connect(":memory:"))
    sigs = desk.signals({"symbol": "ETH-USDT", "candles": c15, "levels": []}, True, now)
    r = [x for x in sigs if x["grade"] == "R"]
    assert len(r) == 1 and r[0]["side"] == "LONG" and abs(r[0]["stop"] - (101.0 - rt["atr"])) < 1e-9 and r[0]["order"] == "MARKET"
    assert agg4h(c15)[-1][0] == b4[-1][0]


def test_three_profiles_share_signals_but_differ():
    """Same market: class C (zone limit) is observation-only — none of the three desks trades it; run() returns all three."""
    conn = sqlite3.connect(":memory:")
    t0 = 1_700_000_000_000
    flat = [10.0] * 300
    c = coin("BTC-USDT", flat, [{"id": "L1", "kind": "support", "lo": 9.96, "hi": 9.99, "center": 9.975, "touches": 3, "status": "ACTIVE"}])
    c["regime"] = {"long": "BULL"}
    out = pd.run(conn, {"coins": [c]}, now=t0 + 300 * 900_000)
    assert set(out["profiles"]) == {"cons", "bal", "aggr"} and out["profile"] == "bal"
    assert not any(out["profiles"][k]["pending"] for k in ("cons", "bal", "aggr"))
    lab = pd.PaperDesk(conn, "aggr", classes={"A", "B", "R", "C"}).step([c], True, t0 + 300 * 900_000)
    assert lab["pending"]   # the same zone would be traded only if class C were enabled
    assert out["profiles"]["cons"]["rules"].startswith("риск 0.5%")
    # tables are separate per profile
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"paper_orders", "paper_bal_orders", "paper_cons_orders"} <= names


def test_cli_candles_long_conversion_from_store_objects():
    """The store returns Candle objects; the desk's 4h scan needs plain rows (regression: server step crashed silently)."""
    from cnq.bingx.rest import Candle
    from cnq.agents.paper_desk import agg4h
    t0 = 1_700_000_000_000 - (1_700_000_000_000 % 14_400_000)
    cs = [Candle(open_time=t0 + i * 900_000, open=1.0, high=1.1, low=0.9, close=1.0, volume=1.0, close_time=t0 + i * 900_000 + 899_999, quote_volume=1.0, trades=1, taker_buy_volume=0.5, taker_buy_quote=0.5) for i in range(32)]
    rows = [[k.open_time, k.open, k.high, k.low, k.close, k.volume] for k in cs]
    b4 = agg4h(rows)
    assert len(b4) == 2 and b4[0][5] == 16.0 and b4[0][2] == 1.1
