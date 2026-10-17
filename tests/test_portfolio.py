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


# ── 5th profile «Сбалансированный · максимум потенциала» ─────────────────────────────────────────────────────────────────
from datetime import datetime, timezone

DAY = 86_400_000
T0 = int(datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp() * 1000)     # a Wednesday, not the 1st–3rd → no monthly rebalance


def _r(sym, price, mid, mcap=5e9, vol=2e8, c30=10.0, c200=50.0, ath=-50.0, hl=True, rank=20, c24=0.0, c7=0.0):
    return {"symbol": sym, "name": sym, "price": price, "mcap": mcap, "volume": vol, "chg24": c24, "chg7": c7, "chg30": c30, "chg200": c200,
            "ath_chg": ath, "hl_perp": hl, "hl_spot": False, "rank": rank, "score": {"short": 50, "mid": mid, "long": 50}, "why": {"mid": f"{sym}: тренд"}}


def bal_world():
    rows = [_r("BTC", 80000, 50, mcap=1.6e12, rank=1), _r("ETH", 2400, 50, mcap=3e11, rank=2), _r("SOL", 100, 50, mcap=5e10, rank=6),
            _r("HYPE", 40, 70, mcap=1.5e10, rank=11)]
    cards = [{"symbol": s, "risk": "cons", "tags": ["L1"]} for s in ("BTC", "ETH", "SOL")]
    cards.append({"symbol": "HYPE", "risk": "mod", "tags": ["DeFi", "биржевой токен", "Hyperliquid"]})
    for i, sc in enumerate([90, 85, 80, 75, 70, 65, 60]):                     # large established alts A0…A6
        rows.append(_r(f"A{i}", 10 + i, sc)); cards.append({"symbol": f"A{i}", "risk": "mod", "tags": ["L1"]})
    traps = [_r("XCEX", 5, 99), _r("XDOWN", 5, 98, c200=-5), _r("XNOHL", 5, 97, hl=False), _r("XTHIN", 5, 96, vol=1e7), _r("XDEAD", 5, 95, ath=-95)]
    rows += traps
    cards += [{"symbol": "XCEX", "risk": "mod", "tags": ["биржевой токен"]}] + [{"symbol": x["symbol"], "risk": "mod", "tags": ["L1"]} for x in traps[1:]]
    rows += [_r("LAI", 2, 88, mcap=8e8, vol=5e7), _r("LAI2", 2, 87, mcap=8e8, vol=5e7), _r("LL2", 2, 85, mcap=8e8, vol=5e7, c30=-5),
             _r("LDEFI", 2, 80, mcap=8e8, vol=5e7), _r("LTINY", 2, 79, mcap=1e8, vol=5e7)]
    cards += [{"symbol": "LAI", "risk": "high", "tags": ["ИИ"]}, {"symbol": "LAI2", "risk": "high", "tags": ["ИИ", "L1"]},
              {"symbol": "LL2", "risk": "high", "tags": ["L2"]}, {"symbol": "LDEFI", "risk": "high", "tags": ["DeFi"]}, {"symbol": "LTINY", "risk": "high", "tags": ["игры"]}]
    rows += [_r("DOGE", 0.2, 40, mcap=1e10, vol=5e8), _r("PEPE", 1e-5, 45, mcap=2e9, vol=1e8), _r("MINI", 0.1, 99, mcap=5e8, vol=9e7)]
    cards += [{"symbol": "MINI", "risk": "high", "tags": ["мем"]}]
    return rows, cards


def _px(rows, sym, mult=None, **kw):
    for r in rows:
        if r["symbol"] == sym:
            if mult:
                r["price"] *= mult
            r.update(kw)


def test_bal_targets_sum_to_one_and_sleeves_in_ranges():
    b, w = pf.bal_targets("bull"), pf.bal_targets("bear")
    assert abs(sum(b.values()) - 1) < 1e-9 and abs(sum(w.values()) - 1) < 1e-9
    assert 0.40 <= b["core"] <= 0.50 and 0.30 <= b["mid"] <= 0.35 and 0.15 <= b["high"] <= 0.25 and abs(b["cash"] - 0.05) < 1e-9
    assert abs(sum(pf.BAL["core"].values()) - b["core"]) < 1e-9
    assert abs(pf.BAL["anchor_w"] + pf.BAL["lead_n"] * pf.BAL["lead_w"] + pf.BAL["meme_n"] * pf.BAL["meme_w"] - b["high"]) < 1e-9
    assert w["high"] == 0 and abs(w["mid"] - b["mid"] / 2) < 1e-9 and w["cash"] > 0.5        # protection mode


def test_bal_start_composition_is_rule_based():
    rows, cards = bal_world()
    conn = sqlite3.connect(":memory:")
    st = pf.BalancedPortfolio(conn).step(rows, cards, True, pf.meme_symbols(cards), T0)
    by = {p["symbol"]: p for p in st["positions"]}
    roles = {s: p["role"] for s, p in by.items()}
    assert {s for s, r in roles.items() if r == "core"} == {"BTC", "ETH", "SOL"}
    assert {s for s, r in roles.items() if r == "mid"} == {"A0", "A1", "A2", "A3", "A4"}           # traps excluded, best five by score
    assert roles["HYPE"] == "anchor" and {s for s, r in roles.items() if r == "lead"} == {"LAI", "LDEFI"}   # one per sector, 30d > 0
    assert not {s for s, r in roles.items() if r == "meme"}                                        # no dip yet → meme share waits in cash
    for s, w in pf.BAL["core"].items():
        assert abs(by[s]["weight"] - w) < 0.002
    assert all(abs(by[s]["weight"] - 0.06) < 0.002 for s in ("A0", "A1", "A2", "A3", "A4", "HYPE"))
    assert abs(sum(p["weight"] for p in st["positions"]) + st["cash_pct"] / 100 - 1) < 0.01
    sl = st["sleeves"]
    assert 40 <= sl["core"]["now_pct"] <= 50 and 29 <= sl["mid"]["now_pct"] <= 31 and 13 <= sl["high"]["now_pct"] <= 15 and 10 <= sl["cash"]["now_pct"] <= 12
    assert st["index"] < 100 and st["index"] > 99.8                                               # 0.1 % fee on the start purchases
    for k in ("key", "name", "about", "positions", "cash_pct", "invested_pct", "index", "history", "actions", "recent_actions"):
        assert k in st, k
    assert st["experiment"] and "Эксперимент" in st["about"] and "если рынок развернётся вниз" in st["about"]
    assert all(p["reco"]["k"] in ("hold", "add", "trim", "sell") and p["reco"]["t"] for p in st["positions"])
    assert st["candidates"]["memes"] and st["candidates"]["memes"][0]["symbol"] == "DOGE" and "MINI" not in [c["symbol"] for c in st["candidates"]["memes"]]


def test_bal_take_profit_trailing_stops_and_meme_dip():
    rows, cards = bal_world()
    conn = sqlite3.connect(":memory:")
    port = pf.BalancedPortfolio(conn)
    memes = pf.meme_symbols(cards)
    port.step(rows, cards, True, memes, T0)
    _px(rows, "LAI", 2.1)                                     # ×2 → body back
    _px(rows, "A0", 0.65)                                     # −35 % → mid stop
    _px(rows, "PEPE", chg30=-40.0)                              # 40 % below the price 30 days ago → dip → meme slot
    st = port.step(rows, cards, True, memes, T0 + DAY)
    acts = {(a["symbol"], a["action"]) for a in st["actions"]}
    assert ("LAI", "TRIM") in acts and ("A0", "SELL") in acts and ("PEPE", "BUY") in acts
    by = {p["symbol"]: p for p in st["positions"]}
    assert by["LAI"]["fixes"] == 1 and by["LAI"]["reco"]["t"] == "Едет бесплатно" and "A0" not in by
    assert by["PEPE"]["role"] == "meme" and abs(by["PEPE"]["weight"] - 0.03) < 0.003
    _px(rows, "LAI", 0.6)                                     # 40 % below its peak → trailing exit of the rest
    _px(rows, "PEPE", 0.6)                                    # −40 % from entry → stop; still «in a dip» but not re-bought (30-day cooldown)
    st = port.step(rows, cards, True, memes, T0 + 2 * DAY)
    acts = {(a["symbol"], a["action"]) for a in st["actions"]}
    assert ("LAI", "SELL") in acts and ("PEPE", "SELL") in acts and ("PEPE", "BUY") not in acts
    assert any(a["reason"].startswith("Трейлинг") for a in st["actions"]) and "PEPE" in port.stopped_recently(T0 + 2 * DAY)
    assert st["index"] > 0 and st["cash_pct"] > 11


def test_bal_bear_protection_with_confirmation_and_return():
    rows, cards = bal_world()
    conn = sqlite3.connect(":memory:")
    port = pf.BalancedPortfolio(conn)
    memes = pf.meme_symbols(cards)
    port.step(rows, cards, True, memes, T0)
    st = port.step(rows, cards, False, memes, T0 + DAY)               # bear seen, not confirmed yet
    assert st["mode"] == "bull" and st["mode_pending"]["to"] == "bear" and not st["actions"]
    st = port.step(rows, cards, False, memes, T0 + 2 * DAY)
    assert st["mode"] == "bull" and not st["actions"]                 # 24 h is not enough — the rule wants 3 days
    st = port.step(rows, cards, False, memes, T0 + 4 * DAY + 3_600_000)
    assert st["mode"] == "bear"
    by = {p["symbol"]: p for p in st["positions"]}
    assert not [p for p in st["positions"] if p["bucket"] == "high"]
    assert all(abs(by[s]["weight"] - 0.03) < 0.003 for s in ("A0", "A1", "A2", "A3", "A4"))
    assert abs(by["BTC"]["weight"] - 0.22 * 2 / 3) < 0.003 and st["cash_pct"] >= 54
    st = port.step(rows, cards, True, memes, T0 + 5 * DAY)
    assert st["mode"] == "bear"                                       # bull seen again, waits 3 days
    st = port.step(rows, cards, True, memes, T0 + 8 * DAY + 3_600_000)
    assert st["mode"] == "bull" and abs(st["sleeves"]["mid"]["now_pct"] - 30) < 1 and abs(st["sleeves"]["core"]["now_pct"] - 45) < 1
    assert "HYPE" in {p["symbol"] for p in st["positions"]}


def test_bal_drift_rebalance_and_add_keeps_coin_amount():
    rows, cards = bal_world()
    conn = sqlite3.connect(":memory:")
    port = pf.BalancedPortfolio(conn)
    memes = pf.meme_symbols(cards)
    port.step(rows, cards, True, memes, T0)
    st = port.step(rows, cards, True, memes, T0 + DAY)
    assert not st["actions"]                                          # nothing moved → no trades
    for s in ("BTC", "ETH", "SOL"):
        _px(rows, s, 3.0)                                             # core triples → core share far above 60 %
    st = port.step(rows, cards, True, memes, T0 + 2 * DAY)
    assert any(a["symbol"] == "BTC" and a["action"] == "TRIM" for a in st["actions"])
    assert abs(st["sleeves"]["core"]["now_pct"] - 45) < 1 and abs(st["sleeves"]["mid"]["now_pct"] - 30) < 1
    # the ledger's ADD keeps the coin amount exact (harmonic entry): buy 0.5 @100, add 0.5 @50 → worth 0.75 at 50
    from cnq.agents.spot_profiles import RiskPortfolio
    c = sqlite3.connect(":memory:"); rp = RiskPortfolio(c, "cons")
    rp._act(T0, "X", "BUY", 0.5, 100.0, "t", "core"); rp._act(T0, "X", "ADD", 1.0, 50.0, "t")
    p = rp.positions()[0]
    assert abs(p["weight"] * 50 / p["entry"] - 0.75) < 1e-9 and abs(rp.cash()) < 1e-9


# ── 4th profile: the meme desk's paper account in the same schema ───────────────────────────────────────────────────────
def _memes_json(now):
    acc = {"name": "Мем-портфель", "start_equity": 10_000.0, "equity": 10_400.0, "cash": 9_300.0, "pnl_usd": 400.0, "pnl_pct": 4.0,
           "positions": [{"coin": "kPEPE", "entry_px": 0.008, "px": 0.0088, "qty": 125000.0, "value": 1100.0, "invested": 1000.75, "pnl_usd": 98.4,
                          "pnl_pct": 9.83, "entry_time": now - 10 * 3_600_000, "hours_held": 10, "stop_px": 0.006, "tp_px": 0.012, "tp_done": False,
                          "body_px": 0.012, "body_taken": False, "remaining_pct": 100.0, "peak_px": 0.009, "trail_px": None,
                          "exit_by": now + 50 * DAY, "funding_paid": 0.5, "why": "цена упала на 27% от максимума за сутки", "context": [], "price_live": True}],
           "closed": [{"coin": "WIF", "entry_time": now - 5 * DAY, "exit_time": now - 3 * DAY, "entry_px": 1.0, "exit_px": 1.3, "pnl_usd": 302.0,
                       "pnl_pct": 30.2, "why": "трейлинг 35% от пика $2.00 (закрытие 4ч)", "context": []}],
           "stats": {"trades": 1, "wins": 1, "sum_pnl": 302.0},
           "events": [{"time": now - 4 * DAY, "text": "Вернули вложенное по WIF: продали 67% по $1.5000 (+50%), получили $1,000. Остаток держим"}],
           "history": [[now - 2 * DAY + i * 1_800_000, 10_000 + i * 5] for i in range(80)], "rules": "правила", "rule": "dip",
           "rule_status": "эксперимент", "limits": {"size_pct": 10.0, "stop_pct": -25.0, "take_profit_pct": 50.0, "pre_body_limit_h": 72}}
    return {"asof": now, "account": acc}


def test_meme_profile_has_the_spot_schema():
    now = T0
    mp = pf.meme_profile(_memes_json(now), now + 60_000)
    for k in ("key", "name", "about", "positions", "cash_pct", "invested_pct", "index", "history", "actions", "recent_actions"):
        assert k in mp, k
    assert mp["key"] == "meme" and mp["name"] == "Мем-портфель" and mp["index"] == 104.0 and mp["cash_pct"] == 89 and mp["invested_pct"] == 11
    p = mp["positions"][0]
    assert p["symbol"] == "PEPE" and p["coin"] == "kPEPE" and abs(p["entry"] - 8e-6) < 1e-15 and abs(p["price"] - 8.8e-6) < 1e-15
    assert p["status"] == "OPEN" and abs(p["weight"] - 1100 / 10_400) < 1e-3 and p["pnl_pct"] == 9.83 and p["reco"]["k"] == "hold"
    assert len(mp["history"]) == 60 and mp["history"][-1]["v"] == round(100 * (10_000 + 79 * 5) / 10_000, 2)
    kinds = [(a["symbol"], a["action"]) for a in mp["recent_actions"]]
    assert ("WIF", "SELL") in kinds and ("WIF", "TRIM") in kinds and ("WIF", "BUY") in kinds and ("PEPE", "BUY") in kinds
    assert mp["recent_actions"] == sorted(mp["recent_actions"], key=lambda a: -a["time"])
    trim = next(a for a in mp["recent_actions"] if a["action"] == "TRIM")
    assert trim["price"] == 1.5
    empty = pf.meme_profile(None, now)
    assert empty["positions"] == [] and empty["index"] == 100.0 and "error" in empty
    conn = sqlite3.connect(":memory:")                     # no memes.json → the account from the DB tables (empty start)
    m = pf.load_meme_account(conn, now, path="/nonexistent/memes.json")
    assert pf.meme_profile(m, now)["index"] == 100.0


def test_run_returns_five_portfolios(tmp_path):
    import json
    import os
    cwd = os.getcwd()
    old = pf.coingecko_markets, pf.hyperliquid_universe
    mk = fake_markets()
    mk.append({"symbol": "SOL", "name": "Solana", "id": "solana", "price": 100, "mcap": 5e10, "rank": 6, "volume": 3e9, "chg24": 1, "chg7": 1,
               "chg30": 5, "chg200": 10, "chg1y": 20, "ath_chg": -60, "ath_date": "2025-01-01T00:00:00Z", "atl_date": "2020-05-01T00:00:00Z"})
    try:
        os.chdir(tmp_path)
        os.makedirs("web")
        with open("web/memes.json", "w", encoding="utf-8") as f:
            json.dump(_memes_json(T0), f)
        pf.coingecko_markets = lambda pages=2: [dict(r) for r in mk]
        pf.hyperliquid_universe = lambda: {"perps": {r["symbol"]: {"vol24": 1e8} for r in mk}, "spot": {}, "available": True}
        res = pf.run(sqlite3.connect(":memory:"), btc_bull=True, llm=None, now=T0 + 60_000)
    finally:
        os.chdir(cwd)
        pf.coingecko_markets, pf.hyperliquid_universe = old
    assert res["portfolio_order"] == ["cons", "mod", "high", "meme", "bal"] and set(res["portfolios"]) == set(res["portfolio_order"])
    for k, P in res["portfolios"].items():
        assert "error" not in P, (k, P.get("error"))
        for f in ("key", "name", "about", "positions", "cash_pct", "invested_pct", "index", "history", "actions", "recent_actions"):
            assert f in P, (k, f)
    assert {p["symbol"] for p in res["portfolios"]["bal"]["positions"]} >= {"BTC", "ETH", "SOL"}
    assert res["portfolios"]["meme"]["index"] == 104.0
