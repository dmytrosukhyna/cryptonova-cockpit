import json
import os
import sqlite3
import tempfile

from cnq.agents import capital as K

T0 = 1_792_000_000_000   # a Saturday


def world(d, spot=10_000.0, bal=10_000.0, imp=10_000.0, fund=10_000.0, hist=None, yld=10_000.0):
    json.dump({"portfolios": {"ideas": {"equity_usd": spot, "history": hist or []}}}, open(os.path.join(d, "portfolio.json"), "w"))
    json.dump({"profiles": {"bal": {"equity": bal, "open": [], "equity_history": []}, "imp": {"equity": imp, "open": [], "equity_history": []}}},
              open(os.path.join(d, "paper.json"), "w"))
    json.dump({"start_equity": 10_000.0, "equity_mtm": fund, "history": []}, open(os.path.join(d, "funding.json"), "w"))
    json.dump({"week": {"virtual_pnl_usd": 24.0}}, open(os.path.join(d, "arb.json"), "w"))
    json.dump({"equity": yld, "history": []}, open(os.path.join(d, "yield.json"), "w"))


def test_split_follow_desks_and_monthly_fee():
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    world(d)
    a = K.run(conn, d, T0)
    w = {s["key"]: s["weight"] for s in a["sleeves"]}
    assert w == {"spot": 0.4, "futures": 0.25, "funding": 0.15, "yield": 0.2} and a["value_usd"] == 10000.0
    assert [x["key"] for x in a["watch_only"]] == ["arb", "tri"] and a["watch_only"][0]["week_virtual_usd"] == 24.0
    # spot +10 %, futures: bal +4 % and imp 0 % → +2 %, funding −1 %, lending +0.5 %
    world(d, spot=11_000, bal=10_400, imp=10_000, fund=9_900, yld=10_050)
    b = K.run(conn, d, T0 + 3_600_000)
    v = {s["key"]: s["value_usd"] for s in b["sleeves"]}
    assert abs(v["spot"] - 4400) < 0.1 and abs(v["futures"] - 2550) < 0.1 and abs(v["funding"] - 1485) < 0.1 and abs(v["yield"] - 2010) < 0.1
    assert abs(b["value_usd"] - 10445) < 0.1
    # each desk as an index from the start → any user model can be valued from these
    assert b["index_keys"] == ["spot", "futures", "funding", "yield"] and b["index_history"][-1][1:] == [110.0, 102.0, 99.0, 100.5]
    assert set(b["models"]) == {"calm", "balanced", "bold"} and all(abs(sum(m["w"].values()) - 1) < 1e-6 for m in b["models"].values()) and b["month"]["fee_usd"] == round(b["month"]["profit_usd"] * 0.10, 2) > 0
    json.dumps(b)


def test_weekly_tilt_halves_a_losing_desk_within_bounds():
    w = K.target_weights({"spot": 0.08, "futures": -0.10, "funding": 0.0}, {"spot", "futures", "funding", "yield"})
    assert abs(sum(w.values()) - 1) < 1e-3 and w["spot"] <= 0.55 and w["futures"] >= 0.10 and w["futures"] < 0.25


def test_yield_desk_accrues_at_the_current_rate_and_keeps_the_last_rate_when_offline():
    from cnq.agents import yield_desk as Y
    conn = sqlite3.connect(":memory:")
    pools = [{"project": "aave-v3", "chain": "Ethereum", "symbol": "USDC", "apy": 5.5, "apyBase": 5.0, "apyReward": 0.5, "tvlUsd": 9e9},
             {"project": "aave-v3", "chain": "Ethereum", "symbol": "USDT", "apy": 3.0, "tvlUsd": 5e9},
             {"project": "aave-v3", "chain": "Ethereum", "symbol": "USDC", "apy": 9.0, "tvlUsd": 1e6},     # a small pool is ignored
             {"project": "aave-v3", "chain": "Ethereum", "symbol": "USDC", "apy": 7.0, "tvlUsd": 2e10, "poolMeta": "e-mode"},  # not the plain market
             {"project": "lido", "chain": "Ethereum", "symbol": "STETH", "apy": 2.8, "tvlUsd": 3e10}]
    a = Y.step(conn, T0, fetch=lambda: pools)              # base rates only (no reward tokens); Sky missing → average of the rest
    assert a["equity"] == 10_000 and a["rates"] == {"USDC": 5.0, "USDT": 3.0} and a["missing"] == ["SUSDS"] and not a["stale"]
    assert abs(a["apy_now"] - (5.0 * 0.4 + 3.0 * 0.3) / 0.7) < 0.01
    pools.append({"project": "sky-lending", "chain": "Ethereum", "symbol": "SUSDS", "apy": 4.0, "tvlUsd": 5e9})
    a = Y.step(conn, T0, fetch=lambda: pools)
    assert a["apy_now"] == 4.1 and a["missing"] == []      # 5×0.4 + 3×0.3 + 4×0.3
    assert any(t["name_ru"].startswith("Lido") for t in a["table"])
    b = Y.step(conn, T0 + 365 * 86_400_000, fetch=lambda: (_ for _ in ()).throw(OSError("down")))   # a year later, API down
    assert abs(b["equity"] - 10_410) < 0.5 and b["stale"] and b["error"]


def test_basis_row_takes_the_furthest_dated_futures_and_annualises_net_of_fees():
    from cnq.agents import yield_desk as Y
    from datetime import datetime, timezone
    now = int(datetime(2026, 10, 18, 8, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [{"instrument_name": "BTC-PERPETUAL", "mark_price": 100_500, "estimated_delivery_price": 100_000},
            {"instrument_name": "BTC-30OCT26", "mark_price": 100_200, "estimated_delivery_price": 100_000},      # 12 days: too short
            {"instrument_name": "BTC-27NOV26", "mark_price": 100_600, "estimated_delivery_price": 100_000},
            {"instrument_name": "BTC-25DEC26", "mark_price": 101_000, "estimated_delivery_price": 100_000},      # 68 days → used
            {"instrument_name": "BTC-26JUN27", "mark_price": 104_000, "estimated_delivery_price": 100_000}]     # 251 days: too far
    r = Y.basis_row(rows, "BTC", now)
    assert r["days"] == 68 and abs(r["apy_base"] - 1 * 365 / 68) < 0.01 and abs(r["apy"] - (1 - 0.3) * 365 / 68) < 0.01
    assert "25.12.2026" in r["name_ru"] and Y.basis_row(rows[:2], "BTC", now) is None
    conn = sqlite3.connect(":memory:")
    a = Y.step(conn, now, fetch=lambda: [], fetch_fut=lambda c: rows if c == "BTC" else (_ for _ in ()).throw(OSError("down")))
    assert [t["symbol"] for t in a["table"]] == ["BTC"]
