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


def test_shadow_forecast_uses_geometry_and_drops_slow_terms():
    comp = {"mid": 0.0, "short": 0.05, "long": 0.03, "btc": 0.03, "stress": 0.0, "mr": 0.0, "mom7": 0.0, "news": 0.0}
    # upper edge 3 away, lower edge 1 away → random walk touches the lower edge first 3 times in 4 → geometry 0.25
    assert abs(cal.shadow_p(100.0, 103.0, 99.0, comp) - 0.30) < 1e-9    # 0.25 + short 0.05, slow +0.06 ignored
    assert cal.shadow_p(100.0, 101.0, 90.0, {"news": 0.06}) == 0.78   # clipped like the live forecast
    assert cal.shadow_p(100.0, None, None, {}) == 0.5                  # no targets → neutral


def test_stats_report_shadow_next_to_live():
    conn = sqlite3.connect(":memory:")
    c = cal.Calibration(conn)
    comp = json.dumps({"long": 0.03, "btc": 0.03})
    for i, o in enumerate(["DOWN", "DOWN", "UP"]):
        conn.execute("INSERT INTO forecasts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"x{i}", cal.SHADOW_SINCE + i, "BTC-USDT", 0.56, 100.0, 103.0, 99.0, 86_400_000, o, 1, 100.0, comp))
    conn.commit()
    s = c.stats()["shadow"]["forward"]
    assert s["n"] == 3 and s["hit_rate"] == round(2 / 3, 3) and s["live_hit_rate"] == round(1 / 3, 3) and s["brier"] < s["live_brier"]
