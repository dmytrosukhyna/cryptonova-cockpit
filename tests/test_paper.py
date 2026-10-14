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
    desk = pd.PaperDesk(conn)
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
