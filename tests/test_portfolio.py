"""Spot portfolio department: scoring, picks and the state machine on synthetic markets (no network)."""
import sqlite3

from cnq.agents import portfolio as pf


def fake_markets(n=40, seed=1):
    import random
    rnd = random.Random(seed)
    rows = [{"symbol": "BTC", "name": "Bitcoin", "id": "bitcoin", "price": 80000, "mcap": 1.6e12, "rank": 1, "volume": 3e10, "chg24": 1, "chg7": 3, "chg30": 8, "chg200": 25, "chg1y": 60, "ath_chg": -10, "ath_date": "2025-10-01T00:00:00Z", "atl_date": "2013-07-01T00:00:00Z"},
            {"symbol": "ETH", "name": "Ethereum", "id": "ethereum", "price": 2400, "mcap": 3e11, "rank": 2, "volume": 1.5e10, "chg24": 1, "chg7": 2, "chg30": 5, "chg200": 10, "chg1y": 20, "ath_chg": -40, "ath_date": "2021-11-01T00:00:00Z", "atl_date": "2015-10-01T00:00:00Z"},
            {"symbol": "USDT", "name": "Tether", "id": "tether", "price": 1, "mcap": 1e11, "rank": 3, "volume": 5e10, "chg24": 0, "chg7": 0, "chg30": 0, "chg200": 0, "chg1y": 0, "ath_chg": -1, "ath_date": "2018-01-01T00:00:00Z", "atl_date": "2015-01-01T00:00:00Z"}]
    for i in range(n):
        rows.append({"symbol": f"T{i:02d}", "name": f"Token{i}", "id": f"t{i}", "price": 1 + i, "mcap": 2e8 + i * 3e8, "rank": 4 + i, "volume": 2e7 + i * 1e6,
                     "chg24": rnd.uniform(-5, 5), "chg7": rnd.uniform(-20, 40), "chg30": rnd.uniform(-40, 80), "chg200": rnd.uniform(-60, 150), "chg1y": rnd.uniform(-80, 300),
                     "ath_chg": rnd.uniform(-95, -5), "ath_date": "2025-01-01T00:00:00Z", "atl_date": "2022-01-01T00:00:00Z"})
    return rows


def test_scoring_and_picks_exclude_stables_and_core():
    rows = pf.score_universe(fake_markets(), {"perps": {"T05": {"vol24": 1e8, "funding": 0.0001, "oi": 1e8}}, "spot": {"T07": {"vol24": 1e7, "price": 8, "chg24": 1}}}, True)
    syms = {r["symbol"] for r in rows}
    assert "USDT" not in syms and "BTC" in syms
    pk = pf.picks(rows)
    for h in pf.HORIZONS:
        assert pk[h] and all(x["symbol"] not in ("BTC", "ETH", "USDT") for x in pk[h])
        assert all(0 <= x["score"] <= 100 for x in pk[h])
    assert all(x["mcap"] >= 1e9 for x in pk["long"])
    t05 = next(r for r in rows if r["symbol"] == "T05")
    assert t05["hl_perp"] and not t05["hl_spot"]


def test_state_machine_buys_core_then_stops_and_trims():
    conn = sqlite3.connect(":memory:")
    rows = pf.score_universe(fake_markets(), {"perps": {}, "spot": {}}, True)
    pk = pf.picks(rows)
    port = pf.SpotPortfolio(conn)
    monday = 1_760_313_600_000  # 2025-10-13 Monday 00:00 UTC
    st = port.step(rows, pk, True, now=monday)
    syms = {p["symbol"] for p in st["positions"]}
    assert {"BTC", "ETH"} <= syms and st["cash_pct"] >= 10 and st["invested_pct"] <= 90
    assert any(a["action"] == "BUY" and a["symbol"] == "BTC" and abs(a["weight"] - 0.35) < 1e-9 for a in st["actions"])
    # crash a satellite by 20 % → SELL; pump another by 50 % → TRIM half
    sats = [p for p in st["positions"] if p["bucket"] != "core"]
    assert sats, "satellites expected on rebalance day"
    crash, pump = sats[0]["symbol"], sats[1]["symbol"] if len(sats) > 1 else None
    for r in rows:
        if r["symbol"] == crash:
            r["price"] *= 0.8
        if pump and r["symbol"] == pump:
            r["price"] *= 1.5
    st2 = port.step(rows, pk, True, now=monday + 3 * 86_400_000)
    acts = {(a["symbol"], a["action"]) for a in st2["actions"]}
    assert (crash, "SELL") in acts
    if pump:
        assert (pump, "TRIM") in acts
    assert crash not in {p["symbol"] for p in st2["positions"]}
    # bear regime: core shrinks on the next Monday, short ideas are not opened
    st3 = port.step(rows, pk, False, now=monday + 7 * 86_400_000)
    btc = next(p for p in st3["positions"] if p["symbol"] == "BTC")
    assert abs(btc["weight"] - 0.25) < 1e-9
    assert not [p for p in st3["positions"] if p["bucket"] == "short"]
    note = pf.market_note(False, rows[0], pk, st3)
    assert "медвежьем" in note and "Портфель" in note


def test_risk_profiles_cash_ledger():
    import sqlite3
    from cnq.agents.spot_profiles import RiskPortfolio
    conn = sqlite3.connect(":memory:"); now = 1_700_000_000_000
    c = RiskPortfolio(conn, "cons"); st = c.step({"BTC": 100, "ETH": 10, "SOL": 1}, [], [], now)
    assert st["index"] == 100.0 and st["cash_pct"] == 10 and len(st["positions"]) == 3
    assert c.step({"BTC": 120, "ETH": 10, "SOL": 1}, [], [], now + 86400000)["index"] == 110.0
    h = RiskPortfolio(conn, "high"); cards = [{"symbol": "WOW", "risk": "high", "rating": 70, "chg7": 30, "on_hl": True}]
    st = h.step({"WOW": 1.0}, cards, [{"symbol": "NEKO", "chg24": 12, "vol24": 9e6, "price": 5.0}], now)
    assert [p["symbol"] for p in st["positions"]] == ["WOW", "NEKO"] and st["cash_pct"] == 90
    st = h.step({"WOW": 2.1, "NEKO": 5.0}, cards, [], now + 2 * 86400000)
    assert st["index"] == 105.5 and st["positions"][0]["fixes"] == 1 and st["actions"][0]["action"] == "TRIM"
    st = h.step({"WOW": 3.2, "NEKO": 3.4}, cards, [], now + 3 * 86400000)
    assert st["index"] == 106.65 and [p["symbol"] for p in st["positions"]] == ["WOW"]
