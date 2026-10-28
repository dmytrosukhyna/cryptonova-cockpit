import json
import os
import sqlite3
import tempfile

import pytest

from cnq.agents import capital as K, panel_header as ph

T0 = 1_792_000_000_000   # a Saturday


def world(d, spot=10_000.0, bal=10_000.0, imp=10_000.0, fund=10_000.0, hist=None, yld=10_000.0, susde=10_000.0, basis=10_000.0,
          risk_off=False, bull=True, rates=(4.0, 6.0, 6.0), with_basis=True, act=10_000.0, cons=100.0, main=None):
    """`bal` = the equity of the MAIN futures desk: `main` written to desk_select.json (28.10), or the default main desk when None"""
    key = main or ph.NOVA_BASE[0]
    if main:
        json.dump({"current": main}, open(os.path.join(d, "desk_select.json"), "w"))
    json.dump({"portfolios": {"ideas": {"equity_usd": spot, "history": hist or []}, "cons": {"equity_usd": None, "index": cons, "history": []}}},
              open(os.path.join(d, "portfolio.json"), "w"))
    json.dump({"risk_off": risk_off, "profiles": {key: {"equity": bal, "open": [], "equity_history": []}, "imp": {"equity": imp, "open": [], "equity_history": []},
                                                  "imps": {"equity": act, "open": [], "equity_history": []}}},
              open(os.path.join(d, "paper.json"), "w"))
    json.dump({"risk": {"btc_bull": bull}}, open(os.path.join(d, "decision.json"), "w"))
    json.dump({"start_equity": 10_000.0, "equity_mtm": fund, "history": []}, open(os.path.join(d, "funding.json"), "w"))
    json.dump({"week": {"virtual_pnl_usd": 24.0}}, open(os.path.join(d, "arb.json"), "w"))
    json.dump({"equity": yld, "apy_now": rates[0], "history": [], "accounts": {"susde": {"equity": susde, "apy_now": rates[1], "history": []}}},
              open(os.path.join(d, "yield.json"), "w"))
    if with_basis:
        json.dump({"equity": basis, "position": {"rate": rates[2]}, "rate_now": 2.0, "min_rate": 3.0, "history": []}, open(os.path.join(d, "basis.json"), "w"))
    elif os.path.exists(os.path.join(d, "basis.json")):
        os.remove(os.path.join(d, "basis.json"))


@pytest.mark.parametrize("main", [None, "bal", "cons"])
def test_allocator_splits_by_regime_and_rates_then_sleeves_follow_their_desks(main):
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    world(d, main=main)
    a = K.run(conn, d, T0)
    w = {s["key"]: s["weight"] for s in a["sleeves"]}
    # risk-on: 70 % directional (spot portfolio 42, active spot 10.5, futures 17.5); carry 30 %: lending ≥ 9 %, the rest ∝ rate −
    # risk discount (lending 4, sUSDe 6 − 1.5 = 4.5, fixed rate 6, funding unknown 2 − 1.5 = 0.5)
    assert w["spot"] == 0.42 and w["active"] == 0.105 and w["futures"] == 0.175 and abs(sum(w.values()) - 1) < 1e-3
    assert w["basis"] > w["susde"] > w["funding"] > 0 and w["yield"] >= 0.09 and abs(w["basis"] - 0.21 * 6 / 15) < 1e-3
    assert a["regime"]["key"] == "on" and any("Фикс-ставка" in x for x in a["why_ru"]) and a["value_usd"] == 10_000
    assert [x["key"] for x in a["watch_only"]] == ["arb", "tri"] and a["watch_only"][0]["week_virtual_usd"] == 24.0
    world(d, spot=11_000, bal=10_400, imp=10_000, fund=9_900, yld=10_050, basis=10_020, main=main)
    b = K.run(conn, d, T0 + 3_600_000)
    v = {s["key"]: s["value_usd"] for s in b["sleeves"]}
    assert abs(v["spot"] - 4620) < 0.1 and abs(v["futures"] - 1785) < 0.1 and abs(v["yield"] - 10_000 * w["yield"] * 1.005) < 0.1
    assert b["benchmark"]["value_usd"] == b["value_usd"] and b["events"] == []          # no re-split yet → equal to the benchmark
    assert b["index_keys"] == K.IDX_KEYS and b["index_history"][-1][1:3] == [110.0, 102.0]
    assert set(b["models"]) == {"calm", "balanced", "bold"} and all(abs(sum(m["w"].values()) - 1) < 1e-3 for m in b["models"].values())
    dirw = lambda m: sum(b["models"][m]["w"].get(k, 0) for k in ("spot", "active", "futures"))
    assert b["models"]["calm"]["w"]["futures"] == 0 and dirw("calm") < dirw("balanced") < dirw("bold") and b["models"]["bold"]["w"]["active"] > w["active"]
    assert b["month"]["profit_usd"] > 0 and abs(b["month"]["fee_usd"] - b["month"]["profit_usd"] * 0.10) < 0.01
    json.dumps(b)


def test_regime_flip_moves_money_to_carry_but_not_more_than_once_a_day():
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    world(d)
    K.run(conn, d, T0)
    world(d, risk_off=True)                                # a short panic alone does not switch the regime (not supported by the test)
    assert K.run(conn, d, T0 + 1800_000)["regime"]["key"] == "on"
    world(d, bull=False)
    a = K.run(conn, d, T0 + 3_600_000)                     # within a day of the start split → wait
    assert a["events"] == [] and a["regime"]["key"] == "off"
    b = K.run(conn, d, T0 + 25 * 3_600_000)
    w = {s["key"]: s["weight"] for s in b["sleeves"]}
    assert abs(w["spot"] + w["active"] + w["futures"] - 0.20) < 1e-3 and "режим" in b["events"][0] and "комиссии" in b["events"][0]
    assert b["value_usd"] < 10_000 and b["benchmark"]["allocator_edge_usd"] < 0         # the move cost fees
    world(d)
    c = K.run(conn, d, T0 + 26 * 3_600_000)               # flips back within a day → no new move
    assert c["events"] == [] and abs(sum(s["weight"] for s in c["sleeves"] if s["key"] in ("spot", "active", "futures")) - 0.20) < 1e-3


def test_spot_cash_earns_the_lending_rate():
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    world(d)
    json.dump({"portfolios": {"ideas": {"equity_usd": 10_000.0, "cash_pct": 10, "history": []}}}, open(os.path.join(d, "portfolio.json"), "w"))
    a = K.run(conn, d, T0)
    b = K.run(conn, d, T0 + 36 * 3_600_000)               # 1.5 days later, same week, desk unchanged → only the cash interest
    sa = {s["key"]: s["value_usd"] for s in a["sleeves"]}["spot"]
    sb = {s["key"]: s["value_usd"] for s in b["sleeves"]}["spot"]
    assert b["events"] == [] and abs(sb - sa * (1 + 0.10 * 0.04 * 1.5 / 365)) < 0.01 and sb > sa


def test_new_source_gets_money_at_once_and_old_version_restarts_once():
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE capital_state(k TEXT PRIMARY KEY, v TEXT)")      # a v1 account (four sleeves, no version)
    conn.execute("INSERT INTO capital_state VALUES('sleeves', ?)", (json.dumps({"spot": 5004.0, "futures": 3000.0, "funding": 2000.0}),))
    conn.commit()
    world(d, with_basis=False)
    a = K.run(conn, d, T0)
    assert a["value_usd"] == 10_000 and a["previous"]["value"] == 10_004.0 and "Новая версия" in a["events"][0]
    assert "basis" not in {s["key"] for s in a["sleeves"]}
    world(d)
    b = K.run(conn, d, T0 + 3_600_000)
    assert "новый источник: фикс-ставка" in b["events"][0] and {s["key"]: s["weight"] for s in b["sleeves"]}["basis"] > 0
    c = K.run(conn, d, T0 + 2 * 3_600_000)
    assert c["events"] == [] and c["version"] == K.VERSION


def test_yield_desk_accrues_at_the_current_rate_and_keeps_the_last_rate_when_offline():
    from cnq.agents import yield_desk as Y
    conn = sqlite3.connect(":memory:")
    pools = [{"project": "aave-v3", "chain": "Ethereum", "symbol": "USDC", "apy": 5.5, "apyBase": 5.0, "apyReward": 0.5, "tvlUsd": 9e9},
             {"project": "aave-v3", "chain": "Ethereum", "symbol": "USDT", "apy": 3.0, "tvlUsd": 5e9},
             {"project": "aave-v3", "chain": "Ethereum", "symbol": "USDC", "apy": 9.0, "tvlUsd": 1e6},     # a small pool is ignored
             {"project": "aave-v3", "chain": "Ethereum", "symbol": "USDC", "apy": 7.0, "tvlUsd": 2e10, "poolMeta": "e-mode"},  # not the plain market
             {"project": "lido", "chain": "Ethereum", "symbol": "STETH", "apy": 2.8, "tvlUsd": 3e10}]
    a = Y.step(conn, T0, fetch=lambda: pools)              # base rates only (no reward tokens); Sky and sGHO missing → USDC alone
    assert a["equity"] == 10_000 and a["rates"] == {"USDC": 5.0} and a["missing"] == ["SUSDS", "SGHO"] and not a["stale"]
    assert a["apy_now"] == 5.0                              # USDT is no longer in the basket (19.10: replaced by sGHO)
    pools.append({"project": "sky-lending", "chain": "Ethereum", "symbol": "SUSDS", "apy": 4.0, "tvlUsd": 5e9})
    a = Y.step(conn, T0, fetch=lambda: pools)
    assert abs(a["apy_now"] - (4.0 * 0.4 + 5.0 * 0.3) / 0.7) < 0.01 and a["missing"] == ["SGHO"]
    pools.append({"project": "aave-v3", "chain": "Ethereum", "symbol": "SGHO", "apy": 4.5, "tvlUsd": 2e8})
    a = Y.step(conn, T0, fetch=lambda: pools)
    assert a["apy_now"] == 4.45 and a["missing"] == []     # 4×0.4 + 5×0.3 + 4.5×0.3
    assert any(t["name_ru"].startswith("Lido") for t in a["table"]) and any("sGHO" in t["name_ru"] for t in a["table"])
    b = Y.step(conn, T0 + 365 * 86_400_000, fetch=lambda: (_ for _ in ()).throw(OSError("down")))   # a year later, API down
    assert abs(b["equity"] - 10_445) < 0.5 and b["stale"] and b["error"]


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


def test_susde_is_a_separate_account_at_its_own_rate():
    from cnq.agents import yield_desk as Y
    conn = sqlite3.connect(":memory:")
    pools = [{"project": "aave-v3", "chain": "Ethereum", "symbol": "USDC", "apy": 4.0, "tvlUsd": 9e9},
             {"project": "ethena-usde", "chain": "Ethereum", "symbol": "SUSDE", "apy": 6.0, "apyBase": 5.0, "tvlUsd": 4e9}]
    a = Y.step(conn, T0, fetch=lambda: pools)
    assert a["accounts"]["susde"]["equity"] == 10_000 and a["accounts"]["susde"]["apy_now"] == 5.0
    b = Y.step(conn, T0 + 365 * 86_400_000, fetch=lambda: pools)
    assert abs(b["accounts"]["susde"]["equity"] - 10_500) < 0.5 and abs(b["equity"] - 10_400) < 0.5


def test_basis_desk_locks_the_rate_waits_when_low_and_rolls_at_expiry():
    from cnq.agents import basis_desk as B
    from datetime import datetime, timezone
    day = 86_400_000
    now = int(datetime(2026, 10, 18, 8, tzinfo=timezone.utc).timestamp() * 1000)
    low = [{"instrument_name": "BTC-25DEC26", "mark_price": 100_300, "estimated_delivery_price": 100_000}]      # ≈1.6 % − fees
    high = [{"instrument_name": "BTC-25DEC26", "mark_price": 101_500, "estimated_delivery_price": 100_000}]     # ≈8.2 % − 1.6 % fees
    conn = sqlite3.connect(":memory:")
    d = B.step(conn, now, fetch=lambda c: low if c == "BTC" else [])
    assert d["position"] is None and d["equity"] == 10_000 and "Ждём" in d["events"][-1]["ru"]
    d = B.step(conn, now + day, fetch=lambda c: high if c == "BTC" else [])
    pos = d["position"]
    assert pos and pos["cur"] == "BTC" and 6 < pos["rate"] < 7 and d["equity"] == 10_000
    d = B.step(conn, now + 30 * day, fetch=lambda c: low if c == "BTC" else [])        # the rate fell — ours stays locked
    assert d["position"]["rate"] == pos["rate"] and abs(d["equity"] - 10_000 * (1 + pos["rate"] / 100 * 29 / 365)) < 0.05
    d = B.step(conn, pos["expiry"] + day, fetch=lambda c: [])                         # expiry: earned in full, flat again
    held = (pos["expiry"] - pos["entered"]) / (365 * day)
    assert d["position"] is None and abs(d["equity"] - 10_000 * (1 + pos["rate"] / 100 * held)) < 0.05


def test_tilt_halves_a_losing_directional_desk_and_the_difference_goes_to_carry():
    avail = set(K.KEYS)
    w, why = K.allocate(K.PROFILES["balanced"], "on", {"spot": -0.08, "futures": 0.07}, {"yield": 4.0, "basis": 5.0, "susde": 5.0}, avail)
    assert abs(w["spot"] - 0.21) < 1e-3 and abs(w["futures"] - 0.21875) < 1e-3 and abs(w["active"] - 0.105) < 1e-3
    assert abs(sum(w.values()) - 1) < 1e-3 and any("вдвое" in x for x in why) and w["susde"] <= 0.15
    w, _ = K.allocate(K.PROFILES["bold"], "on", {"spot": 0.2, "futures": 0.2}, {}, avail)     # a great month adds ≤ 10 points
    assert abs(w["spot"] + w["active"] + w["futures"] - 0.95) < 1e-3
    w, _ = K.allocate(K.PROFILES["balanced"], "on", {}, {"yield": 4.0, "basis": 0.0, "susde": 1.0, "funding": -3.0}, avail)
    assert w["basis"] == 0 and w["funding"] == 0 and w["susde"] == 0 and abs(w["yield"] - 0.3) < 1e-3   # nothing else pays → reserve
    w, _ = K.allocate(K.PROFILES["balanced"], "on", {}, {}, avail - {"active"})                 # active desk not running yet → spot
    assert w.get("active", 0) == 0 and abs(w["spot"] - 0.525) < 1e-3


def test_calm_holds_the_core_portfolio_and_does_not_trade_actively():
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    world(d)
    a = K.run(conn, d, T0)
    cw = a["models"]["calm"]["w"]
    assert cw["futures"] == 0 and cw["active"] == 0 and cw["spot"] == 0.35
    world(d, cons=110.0)                                    # only the conservative core portfolio moved (+10 %)
    b = K.run(conn, d, T0 + 3_600_000)
    assert abs(b["models"]["calm"]["value_usd"] - 10_350) < 0.5 and b["value_usd"] == 10_000      # balanced holds the CRYPTO NOVA портфель


def test_models_are_ranked_by_result_per_drawdown_only_after_four_weeks():
    m = {"calm": {"hist": [[0, 10_000], [1, 10_200]]}, "balanced": {"hist": [[0, 10_000], [1, 10_800], [2, 10_300], [3, 10_600]]},
         "bold": {"hist": [[0, 10_000], [1, 11_500], [2, 9_800], [3, 10_700]]}}
    r = K.rank_models(m, 10 * 86_400_000, 0)
    assert not r["ready"] and r["leader"] is None and "28 дней" in r["ru"]
    r = K.rank_models(m, 30 * 86_400_000, 0)
    # calm +2 % / dd 0 → 2/2 = 1.0; balanced +6 % / −4.6 % → 1.30; bold +7 % / −14.8 % → 0.47
    assert r["ready"] and r["leader"] == "balanced" and [x["key"] for x in r["rows"]] == ["balanced", "calm", "bold"]



def test_regime_uses_btc_200d_average_and_60d_return_when_the_terminal_has_them():
    d = tempfile.mkdtemp()
    def dec(price, sma, r60, bull=True):
        json.dump({"risk": {"btc_bull": bull}, "coins": [{"symbol": "BTC-USDT", "price": price, "regime": {"sma200": sma, "r60": r60}}]},
                  open(os.path.join(d, "decision.json"), "w"))
    dec(80_000, 74_000, 0.12)
    assert K.regime(d)[0] == "on" and "60 дней" in K.regime(d)[1]
    dec(80_000, 74_000, -0.03)
    assert K.regime(d) == ("off", "BTC за 60 дней в минусе (-3%)")
    dec(70_000, 74_000, 0.05, bull=True)
    assert K.regime(d)[0] == "off"
    json.dump({"risk": {"btc_bull": False}}, open(os.path.join(d, "decision.json"), "w"))   # no terminal figures → the system flag
    assert K.regime(d)[0] == "off"


def test_listing_short_gets_dollars_only_after_its_live_test_passes():
    """19.10: the listing short is tracked from the start but is not a source of its own until ≥ 20 closed demo trades average
    ≥ +1 %; then balanced 8 %, bold up to 12 % (never more than half the dollar part), calm 0 — taken from the dollars."""
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    world(d)

    def lst(trades, avg, eq=10_000.0):
        p = json.load(open(os.path.join(d, "paper.json")))
        p["profiles"]["lst"] = {"equity": eq, "open": [], "equity_history": [], "stats": {"trades": trades, "avg_trade_pct": avg}}
        json.dump(p, open(os.path.join(d, "paper.json"), "w"))
    lst(12, 3.0)
    a = K.run(conn, d, T0)
    assert "listings" not in {s["key"] for s in a["sleeves"]} and "listings" in a["index_keys"]
    assert a["index_history"][-1][-1] == 100.0                     # tracked as an index all the same
    lst(25, 0.4)                                                    # enough trades, not enough profit → still nothing
    b = K.run(conn, d, T0 + 3_600_000)
    assert "listings" not in {s["key"] for s in b["sleeves"]}
    lst(25, 2.1)
    c = K.run(conn, d, T0 + 2 * 3_600_000)
    w = {s["key"]: s["weight"] for s in c["sleeves"]}
    assert "новый источник: шорт листингов" in c["events"][0] and w["listings"] == 0.08 and abs(sum(w.values()) - 1) < 1e-3
    assert c["models"]["calm"]["w"].get("listings", 0) == 0 and abs(c["models"]["bold"]["w"]["listings"] - 0.075) < 1e-3
    assert any("Шорт листингов прошёл живую проверку (25 сделок" in x for x in c["why_ru"])
    assert next(s for s in c["sleeves"] if s["key"] == "listings")["kind"] == "short"
