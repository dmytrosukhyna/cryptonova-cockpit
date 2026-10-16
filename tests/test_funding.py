"""Funding desk (paper funding carry): offline, fake fetch."""
import json
import sqlite3

from cnq.agents import funding_desk as fd

T0 = 1_700_006_400_000 - 1_700_006_400_000 % fd.H8 + 3_600_000      # 1 h after a settlement boundary
BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK"]


def fake(rates, extra=None):
    def f(bases):
        out = {"binance": dict(rates), "bybit": {}, "okx": {}, "bingx": {}, "hyperliquid": {}}
        for v, m in (extra or {}).items(): out[v] = m
        return out
    return f


def test_selection_threshold_and_max_coins():
    conn = sqlite3.connect(":memory:")
    rates = {b: 0.0002 for b in BASES} | {"ADA": 0.00005, "AVAX": -0.0001}     # ADA below 0.01%, AVAX negative
    res = fd.run(conn, T0, fake(rates), BASES)
    held = {p["symbol"] for p in res["positions"]}
    assert len(held) == 6 and "ADA" not in held and "AVAX" not in held
    assert res["venue"] == "binance"
    n = res["positions"][0]["notional"]
    assert abs(n - 10_000 / 6 / 1.2) < 0.01
    assert abs(res["equity"] - (10_000 - 6 * n * fd.COST)) < 0.01                  # entry costs 0.15% of notional each


def test_hysteresis_keeps_then_exits():
    conn = sqlite3.connect(":memory:")
    fd.run(conn, T0, fake({"BTC": 0.0003}), ["BTC"])
    # average drops below the entry bar but stays > 0 → keep
    for i in range(1, 6):
        r = fd.run(conn, T0 + i * 1_800_000, fake({"BTC": 0.00002}), ["BTC"])
    assert [p["symbol"] for p in r["positions"]] == ["BTC"] and 0 < r["positions"][0]["avg7"] < fd.ENTRY_THR
    # a fresh coin at the same low level is NOT entered
    r2 = fd.run(sqlite3.connect(":memory:"), T0, fake({"ETH": 0.00002}), ["ETH"])
    assert r2["positions"] == []
    # average turns negative → exit, exit cost charged once
    eq = r["equity"]
    for i in range(6, 30):
        r = fd.run(conn, T0 + i * 1_800_000, fake({"BTC": -0.0005}), ["BTC"])
        if not r["positions"]: break
    assert r["positions"] == [] and any("Закрыли: BTC" in e["text"] for e in r["events"])
    n = 10_000 / 6 / 1.2
    assert r["equity"] < eq - n * fd.COST + 0.01 + n * 0.0003     # exit fee taken (funding credited in between is small)


def test_settlement_credited_once():
    conn = sqlite3.connect(":memory:")
    fd.run(conn, T0, fake({"BTC": 0.0003}), ["BTC"])
    eq0 = fd.run(conn, T0 + 3 * 3_600_000, fake({"BTC": 0.0003}), ["BTC"])["equity"]
    s = T0 - T0 % fd.H8 + fd.H8
    r1 = fd.run(conn, s + 600_000, fake({"BTC": 0.0005}), ["BTC"])            # after settlement: credit rate recorded BEFORE it
    n = r1["positions"][0]["notional"]
    assert abs(r1["equity"] - (eq0 + n * 0.0003)) < 0.01
    r2 = fd.run(conn, s + 1_800_000, fake({"BTC": 0.0005}), ["BTC"])          # same settlement window again → nothing new
    assert r2["equity"] == r1["equity"]
    assert abs(r2["positions"][0]["earned_usd"] - round(n * 0.0003, 2)) < 0.01
    assert sum("Начислен фандинг" in e["text"] for e in r2["events"]) == 1


def test_missing_rate_is_skipped_and_noted():
    conn = sqlite3.connect(":memory:")
    fd.run(conn, T0, fake({"BTC": 0.0003}), ["BTC"])
    eq = fd.run(conn, T0 + 60_000, fake({"BTC": 0.0003}), ["BTC"])["equity"]
    s = T0 - T0 % fd.H8 + fd.H8
    conn.execute("DELETE FROM funding_hist"); conn.commit()                  # no record before the settlement
    r = fd.run(conn, s + 600_000, fake({"BTC": 0.0003}), ["BTC"])
    assert r["equity"] == eq and any("пропущено" in e["text"] for e in r["events"])


def test_no_data_no_change_and_json_shape():
    conn = sqlite3.connect(":memory:")
    r = fd.run(conn, T0, lambda b: {}, ["BTC"])
    assert r["venue"] is None and r["equity"] == 10_000 and r["positions"] == []
    extra = {"bybit": {"BTC": 0.0001}, "hyperliquid": {"BTC": 0.0004, "ETH": 0.0001}}
    r = fd.run(conn, T0 + 1_800_000, fake({"BTC": 0.0002, "ETH": 0.0002}, extra), ["BTC", "ETH"])
    for k in ("asof", "venue", "equity", "start_equity", "pnl_usd", "pnl_pct", "positions", "history", "events",
              "next_settlement", "table", "note", "name", "about"):
        assert k in r, k
    assert set(r["positions"][0]) >= {"symbol", "notional", "entry_time", "earned_usd", "rate_now", "avg7"}
    btc = next(t for t in r["table"] if t["symbol"] == "BTC")
    assert btc["spread"]["short_on"] == "hyperliquid" and btc["spread"]["long_on"] == "bybit"
    assert abs(btc["spread"]["apr_pct"] - 0.0003 * 3 * 365 * 100) < 0.01
    assert r["next_settlement"] % fd.H8 == 0 and r["next_settlement"] > r["asof"]
    assert "расхождения не учтён" in r["note"] and "+5%" in r["about"]
    json.dumps(r, ensure_ascii=False)
    assert len(r["history"]) <= 500
