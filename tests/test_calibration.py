import json, sqlite3
from cnq.agents import calibration as cal


def _coin(sym, prices, t0, levels, regime):
    cs = [[t0 + i * 900_000, p, p * 1.001, p * 0.999, p, 10.0] for i, p in enumerate(prices)]
    return {"symbol": sym, "price": prices[-1], "candles": cs, "levels": levels, "regime": regime}


def test_forecast_recorded_then_resolved_by_first_touch():
    conn = sqlite3.connect(":memory:")
    t0 = 1_700_000_000_000
    lv = [{"kind": "support", "lo": 98.0, "hi": 99.0, "center": 98.5, "status": "ACTIVE"}, {"kind": "resistance", "lo": 102.0, "hi": 103.0, "center": 102.5, "status": "ACTIVE"}]
    c = _coin("SOL-USDT", [100.0] * 200, t0, lv, {"mid": "LONG", "short": "UP", "long": "BULL"})
    now = c["candles"][-1][0] + 900_000
    d = cal.run(conn, {"coins": [c]}, [], now)
    assert d["recorded"] == 1 and d["stats"]["n"] == 0 and d["open"]["SOL"]["p_up"] > 0.6
    # 6 hours later the price touches the resistance edge → UP, forecast was UP → hit
    more = [100.0] * 20 + [102.5] + [101.0] * 10
    c2 = _coin("SOL-USDT", [100.0] * 200 + more, t0, lv, {"mid": "LONG", "short": "UP", "long": "BULL"})
    now2 = c2["candles"][-1][0] + 900_000
    d2 = cal.run(conn, {"coins": [c2]}, [], now2)
    assert d2["resolved"] == 1 and d2["stats"]["n"] == 1 and d2["stats"]["hit_rate"] == 1.0 and d2["stats"]["brier"] < 0.25
    # no look-ahead: a forecast made now is not resolved by candles that are not closed yet
    assert d2["recorded"] == 1 and d2["stats"]["pending"] == 1
