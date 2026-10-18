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
    assert c.step({"BTC": 120, "ETH": 10, "SOL": 1}, [], [], now + 86400000)["index"] == 112.0
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
    ms = {"PEPE": _meme_row("PEPE", "small")}                   # 17.10: and the meme check says «можно на небольшую сумму»
    st = port.step(rows, cards, True, memes, T0 + DAY, meme_status=ms)
    acts = {(a["symbol"], a["action"]) for a in st["actions"]}
    assert ("LAI", "TRIM") in acts and ("A0", "SELL") in acts and ("PEPE", "BUY") in acts
    by = {p["symbol"]: p for p in st["positions"]}
    assert by["LAI"]["fixes"] == 1 and by["LAI"]["reco"]["t"] == "Едет бесплатно" and "A0" not in by
    assert by["PEPE"]["role"] == "meme" and abs(by["PEPE"]["weight"] - 0.03) < 0.003
    _px(rows, "LAI", 0.6)                                     # 40 % below its peak → trailing exit of the rest
    _px(rows, "PEPE", 0.6)                                    # −40 % from entry → stop; still «in a dip» but not re-bought (30-day cooldown)
    st = port.step(rows, cards, True, memes, T0 + 2 * DAY, meme_status=ms)
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
    assert not st["actions"] and st["sleeves"]["core"]["now_pct"] > 60     # drift rebalance at most once a week after the last one
    st = port.step(rows, cards, True, memes, T0 + 8 * DAY)
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


def test_run_returns_six_portfolios(tmp_path):
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
        with open("web/ideas.json", "w", encoding="utf-8") as f:      # the previous run's table → «По рекомендациям» follows it
            json.dump(_ideas([_irow("BTC", "buy", 80000, "core"), _irow("ETH", "buy", 2400, "core"), _irow("GHOST", "buy", 3.0)], T0 - 1_800_000), f)
        pf.coingecko_markets = lambda pages=2: [dict(r) for r in mk]
        pf.hyperliquid_universe = lambda: {"perps": {r["symbol"]: {"vol24": 1e8} for r in mk}, "spot": {}, "available": True}
        res = pf.run(sqlite3.connect(":memory:"), btc_bull=True, llm=None, now=T0 + 60_000)
    finally:
        os.chdir(cwd)
        pf.coingecko_markets, pf.hyperliquid_universe = old
    assert res["portfolio_order"] == ["cons", "mod", "high", "meme", "bal", "ideas"] and set(res["portfolios"]) == set(res["portfolio_order"])
    for k, P in res["portfolios"].items():
        assert "error" not in P, (k, P.get("error"))
        for f in ("key", "name", "about", "positions", "cash_pct", "invested_pct", "index", "history", "actions", "recent_actions"):
            assert f in P, (k, f)
    assert {p["symbol"] for p in res["portfolios"]["bal"]["positions"]} >= {"BTC", "ETH", "SOL"}
    assert res["portfolios"]["meme"]["index"] == 104.0
    ip = res["portfolios"]["ideas"]
    assert {p["symbol"] for p in ip["positions"]} == {"BTC", "ETH"} and not ip["experiment"] and ip["ours"] and ip["name"] == "CRYPTO NOVA портфель"
    assert [a["symbol"] for a in ip["actions"] if a["action"] == "SKIP"] == ["GHOST"]          # no price source → skipped, said so


# ── 17.10: cons rebalances on MARKET weights, high takes memes only by the spot meme status, mod holds no memes, one-time reset ──
def _meme_row(sym, status, score=60, price=1.0, dd=-40.0, why=None):
    return {"symbol": sym, "status": status, "status_ru": {"small": "можно на небольшую сумму", "wait": "ждём", "avoid": "не брать"}[status],
            "why_ru": why or ("просадка −40% от максимума за 30 дней, проверки пройдены" if status == "small" else "ждём просадку"),
            "score": score, "price": price, "from_high30_pct": dd}


def test_cons_monthly_rebalance_uses_market_weights():
    from cnq.agents.spot_profiles import RiskPortfolio
    conn = sqlite3.connect(":memory:")
    d10 = int(datetime(2026, 9, 10, tzinfo=timezone.utc).timestamp() * 1000)
    c = RiskPortfolio(conn, "cons")
    c.step({"BTC": 100.0, "ETH": 10.0, "SOL": 1.0}, [], [], d10)
    px = {"BTC": 200.0, "ETH": 10.0, "SOL": 1.0}                                 # BTC doubles: cost weight still 60 %, market weight 75 %
    st = c.step(dict(px), [], [], d10 + DAY)
    assert not st["actions"] and st["index"] == 160.0                             # not the 1st–3rd → no rebalance
    oct1 = int(datetime(2026, 10, 1, 6, tzinfo=timezone.utc).timestamp() * 1000)
    st = c.step(dict(px), [], [], oct1)
    acts = {(a["symbol"], a["action"]) for a in st["actions"]}
    assert ("BTC", "TRIM") in acts and ("ETH", "ADD") in acts and ("SOL", "ADD") in acts and "рыночным долям" in st["actions"][0]["reason"]
    assert st["index"] == 160.0                                                   # rebalancing moves value, it does not create it
    V = c.value(px)
    mw = {p["symbol"]: c._mv(p, px) / V for p in c.positions()}
    assert abs(mw["BTC"] - 0.60) < 1e-6 and abs(mw["ETH"] - 0.20) < 1e-6 and abs(mw["SOL"] - 0.10) < 1e-6 and abs(c.cash() / V - 0.10) < 1e-6
    # inside the ±5 p.p. band → nothing next month
    px2 = {"BTC": 210.0, "ETH": 10.0, "SOL": 1.0}
    nov1 = int(datetime(2026, 11, 1, 6, tzinfo=timezone.utc).timestamp() * 1000)
    assert not c.step(dict(px2), [], [], nov1)["actions"]


def test_high_takes_memes_only_by_status_with_caps():
    from cnq.agents.spot_profiles import RiskPortfolio
    conn = sqlite3.connect(":memory:")
    h = RiskPortfolio(conn, "high")
    memes = {"M1", "M2", "M3", "M4", "M5", "M6", "M7", "MW", "MA", "PUMP", "MEMECARD"}
    ms = {f"M{i}": _meme_row(f"M{i}", "small", score=70 - i) for i in range(1, 8)}
    ms["MW"] = _meme_row("MW", "wait", score=99)
    ms["MA"] = _meme_row("MA", "avoid", score=99, why="мало оборота")
    cards = [{"symbol": "ALT", "risk": "high", "rating": 70, "chg7": 20, "on_hl": True},
             {"symbol": "MEMECARD", "risk": "high", "rating": 90, "chg7": 50, "tags": ["мем"]},
             {"symbol": "USDX", "risk": "high", "rating": 90, "chg7": 1, "stable": True}]
    hl = [{"symbol": "UPUMP", "chg24": 9, "vol24": 9e6, "price": 0.004}, {"symbol": "USOL", "chg24": 2, "vol24": 9e6, "price": 100.0},
          {"symbol": "NEKO", "chg24": 12, "vol24": 9e6, "price": 5.0}]
    price = {"ALT": 2.0, "MEMECARD": 1.0, "USDX": 1.0, "SOL": 100.0}
    st = h.step(dict(price), cards, hl, T0, memes=memes, meme_status=ms)
    by = {p["symbol"]: p for p in st["positions"]}
    assert set(by) == {"ALT", "NEKO", "M1", "M2", "M3", "M4", "M5"}             # 5 memes × 5 % = the 25 % cap; best quality first
    assert all(by[s]["bucket"] == "meme" and by[s]["meme_status"] == "small" and by[s]["weight"] <= 0.06 for s in ("M1", "M2", "M3", "M4", "M5"))
    V = h.value({**price, **{s: 1.0 for s in ms}})
    assert sum(h._mv(p, {**price, **{s: 1.0 for s in ms}}) for p in h.positions() if p["symbol"] in memes) <= 0.25 * V + 1e-9
    assert st["cash_pct"] >= 50 and "MW" not in by and "MA" not in by and "MEMECARD" not in by and "UPUMP" not in by and "USOL" not in by and "USDX" not in by
    # next run: caps full → nothing new; M1 gets a red flag → sold at once; M2 −35 % → stop, and not re-bought while still «small»
    ms["M1"] = _meme_row("M1", "avoid", why="красный флаг: топ-10 кошельков держат 70%")
    px = {**price, "M2": 0.65}
    st = h.step(dict(px), cards, [], T0 + DAY, memes=memes, meme_status=ms)
    acts = {(a["symbol"], a["action"]) for a in st["actions"]}
    assert ("M1", "SELL") in acts and ("M2", "SELL") in acts and ("M2", "BUY") not in acts
    assert ("M6", "BUY") in acts and ("M7", "BUY") not in acts               # freed room refilled, but the cap is 25 % of the value NOW:
    held = {p["symbol"] for p in h.positions()}                                 # the M2 loss shrank the portfolio, 3 × 5 % + M6 already ≈ 20.3 %
    assert {"M3", "M4", "M5", "M6"} <= held and not {"M1", "M2", "M7"} & held
    pxm = {**price, **{s: 1.0 for s in ms}}
    assert sum(h._mv(p, pxm) for p in h.positions() if p["symbol"] in memes) <= 0.25 * h.value(pxm) + 1e-9
    # body at ×2 (the rule stays): sell half, the rest rides
    st = h.step({**price, "M3": 2.05}, cards, [], T0 + 2 * DAY, memes=memes, meme_status=ms)
    assert any(a["symbol"] == "M3" and a["action"] == "TRIM" for a in st["actions"])
    assert next(p for p in h.positions() if p["symbol"] == "M3")["fixes"] == 1
    # no status data (memes.json stale) → no meme is bought at all
    h2 = RiskPortfolio(sqlite3.connect(":memory:"), "high")
    assert not [p for p in h2.step(dict(price), cards, [], T0, memes=memes, meme_status={})["positions"] if p["symbol"] in memes]


def test_mod_holds_no_memes_and_counts_closed_pnl():
    conn = sqlite3.connect(":memory:")
    port = pf.SpotPortfolio(conn)
    port._act(T0, "X", "BUY", 0.08, 10.0, "t", "mid")
    port._act(T0, "PEPE", "BUY", 0.08, 1.0, "t", "mid")
    port._act(T0 + 1, "X", "SELL", 0, 8.0, "stop")                               # −20 % on 8 % → −1.6 % of the start
    assert abs(port.realized() + 0.016) < 1e-12
    port._act(T0 + 2, "X", "BUY", 0.08, 5.0, "re-buy", "mid")
    assert next(p for p in port.positions() if p["symbol"] == "X")["entry"] == 5.0   # a re-buy starts from its own price
    rows = [{"symbol": "X", "price": 5.0, "score": {"short": 0, "mid": 50, "long": 0}}, {"symbol": "PEPE", "price": 1.0, "score": {"short": 0, "mid": 99, "long": 0}}]
    st = port.step(rows, {"short": [], "mid": [{"symbol": "PEPE", "score": 99, "price": 1.0, "why": ""}], "long": []}, True, T0 + DAY, memes={"PEPE"})
    assert ("PEPE", "SELL") in {(a["symbol"], a["action"]) for a in st["actions"]} and "PEPE" not in {p["symbol"] for p in st["positions"]}
    assert st["index"] == round(100 * (1 - 0.016), 2)                             # the closed loss stays in the index


def test_spot_reset_archives_and_rebuilds_once(tmp_path):
    import json
    import os
    from cnq.agents.spot_profiles import RiskPortfolio
    conn = sqlite3.connect(":memory:")
    t_old = T0 - 2 * DAY
    RiskPortfolio(conn, "cons").step({"BTC": 64000.0, "ETH": 2400.0, "SOL": 100.0}, [], [], t_old)
    hp = RiskPortfolio(conn, "high"); hp._act(t_old, "T03", "BUY", 0.05, 4.0, "old", "high"); hp._set_cash(0.95)
    mp = pf.SpotPortfolio(conn); mp._act(t_old, "BTC", "BUY", 0.35, 80000.0, "old", "core"); mp._act(t_old, "PEPE", "BUY", 0.08, 1e-5, "old", "mid")
    conn.commit()
    mk = fake_markets()
    mk.append({"symbol": "SOL", "name": "Solana", "id": "solana", "price": 100, "mcap": 5e10, "rank": 6, "volume": 3e9, "chg24": 1, "chg7": 1,
               "chg30": 5, "chg200": 10, "chg1y": 20, "ath_chg": -60, "ath_date": "2025-01-01T00:00:00Z", "atl_date": "2020-05-01T00:00:00Z"})
    mk.append({"symbol": "PEPE", "name": "Pepe", "id": "pepe", "price": 1.1e-5, "mcap": 4e9, "rank": 30, "volume": 4e8, "chg24": 1, "chg7": 30,
               "chg30": 60, "chg200": 90, "chg1y": 50, "ath_chg": -40, "ath_date": "2025-01-01T00:00:00Z", "atl_date": "2023-05-01T00:00:00Z"})
    now = T0 + 60_000
    memes_json = _memes_json(now)
    memes_json["spot_memes"] = [_meme_row("DIPA", "small", 70, 0.5), _meme_row("DIPB", "small", 65, 2.0), _meme_row("WAITC", "wait", 90, 1.0)]
    cwd, old = os.getcwd(), (pf.coingecko_markets, pf.hyperliquid_universe)
    try:
        os.chdir(tmp_path); os.makedirs("web")
        json.dump(memes_json, open("web/memes.json", "w", encoding="utf-8"))
        pf.coingecko_markets = lambda pages=2: [dict(r) for r in mk]
        pf.hyperliquid_universe = lambda: {"perps": {r["symbol"]: {"vol24": 1e8} for r in mk}, "spot": {}, "available": True}
        res = pf.run(conn, btc_bull=True, llm=None, now=now)
        res2 = pf.run(conn, btc_bull=True, llm=None, now=now + 1_800_000)
    finally:
        os.chdir(cwd)
        pf.coingecko_markets, pf.hyperliquid_universe = old
    P = res["portfolios"]
    for k in ("cons", "mod", "high"):
        r = P[k]["reset"]
        assert r and r["time"] == now and "Портфель пересобран 17.10 по решению владельца" in r["note_ru"] and "Новый старт $10 000" in r["note_ru"], k
        assert P[k]["history"][0]["time"] == now and all(a["time"] == now for a in P[k]["recent_actions"]), k
        assert "error" not in P[k], (k, P[k].get("error"))
    assert P["cons"]["reset"]["archived_pnl_usd"] == 1500.0 and "итог +$1 500" in P["cons"]["reset"]["note_ru"]   # BTC 64k → 80k on 60 % (18.10)
    assert P["mod"]["reset"]["archived_pnl_usd"] == round(10_000 * 0.08 * (1.1e-5 / 1e-5 - 1), 2)   # BTC flat, PEPE +10 %
    assert "в прошлом составе мем — PEPE" in P["mod"]["reset"]["note_ru"]
    assert P["high"]["reset"]["archived_pnl_usd"] == round(10_000 * (0.95 + 0.05 * 4.0 / 4.0 - 1), 2)
    assert "PEPE" not in {p["symbol"] for p in P["mod"]["positions"]} and {"BTC", "ETH"} <= {p["symbol"] for p in P["mod"]["positions"]}
    assert {p["symbol"] for p in P["high"]["positions"] if p["bucket"] == "meme"} == {"DIPA", "DIPB"}
    assert {p["symbol"] for p in P["cons"]["positions"]} == {"BTC", "ETH", "SOL"} and P["cons"]["index"] == 100.0
    assert P["bal"].get("reset") is None
    assert res["portfolio"]["reset"] == P["mod"]["reset"]
    rows = conn.execute("SELECT id FROM spot_archive ORDER BY id").fetchall()
    assert [r[0] for r in rows] == [f"{pf.SPOT_RESET_ID}:{k}" for k in ("cons", "high", "mod")]
    arch = json.loads(conn.execute("SELECT json FROM spot_archive WHERE profile='high'").fetchone()[0])
    assert arch["positions"][0]["symbol"] == "T03" and arch["actions"][0]["reason"] == "old" and arch["meta"]["cash"] == 0.95
    # second run: no new archive, no rebuild, reset info unchanged
    P2 = res2["portfolios"]
    assert conn.execute("SELECT COUNT(*) FROM spot_archive").fetchone()[0] == 3 and pf.reset_spot(conn, now + 3_600_000, {}) == {}
    assert all(P2[k]["reset"] == P[k]["reset"] and not [a for a in P2[k]["actions"] if a["action"] == "BUY"] for k in ("cons", "mod", "high"))


def test_bal_memes_need_status_small_and_red_flag_sells_next_run():
    rows, cards = bal_world()
    conn = sqlite3.connect(":memory:")
    port = pf.BalancedPortfolio(conn)
    memes = pf.meme_symbols(cards)
    port.step(rows, cards, True, memes, T0)                                     # Wednesday
    _px(rows, "PEPE", chg30=-40.0); _px(rows, "DOGE", chg30=-45.0)             # both ≥ 30 % below the 30-day high
    for ms in (None, {}, {"PEPE": _meme_row("PEPE", "wait"), "DOGE": _meme_row("DOGE", "avoid", why="низкий балл качества: 41 из 100")}):
        st = port.step(rows, cards, True, memes, T0 + 3_600_000, meme_status=ms)
        assert not [a for a in st["actions"] if a["symbol"] in ("PEPE", "DOGE")], ms   # a dip alone is not enough any more
    ms = {"PEPE": _meme_row("PEPE", "small"), "DOGE": _meme_row("DOGE", "small")}
    st = port.step(rows, cards, True, memes, T0 + DAY, meme_status=ms)
    acts = {(a["symbol"], a["action"]) for a in st["actions"]}
    assert ("PEPE", "BUY") in acts and ("DOGE", "BUY") in acts and "проверка мемов" in st["actions"][-1]["reason"]
    assert {c["symbol"]: c["status"] for c in st["candidates"]["memes"]}["PEPE"] == "small"
    # PEPE gets a red flag → sold on the next run (Friday) with an honest reason; DOGE «не брать» without a red flag waits for Monday
    ms = {"PEPE": _meme_row("PEPE", "avoid", why="красный флаг: топ-10 кошельков держат 60%"),
          "DOGE": _meme_row("DOGE", "avoid", why="мало оборота: $3M в сутки — цену легко сдвинуть")}
    st = port.step(rows, cards, True, memes, T0 + 2 * DAY, meme_status=ms)
    sells = {a["symbol"]: a["reason"] for a in st["actions"] if a["action"] == "SELL"}
    assert set(sells) == {"PEPE"} and sells["PEPE"] == "PEPE продан: проверка мемов — красный флаг (топ-10 кошельков держат 60%), итог 0% от входа."
    st = port.step(rows, cards, True, memes, T0 + 5 * DAY + 3_600_000, meme_status=ms)           # Monday 19.10: weekly meme check
    sells = {a["symbol"]: a["reason"] for a in st["actions"] if a["action"] == "SELL"}
    assert set(sells) == {"DOGE"} and sells["DOGE"].startswith("DOGE продан на недельной проверке мемов: статус «не брать» — мало оборота")
    assert not [p for p in st["positions"] if p["role"] == "meme"]
    st = port.step(rows, cards, True, memes, T0 + 5 * DAY + 7_200_000, meme_status=ms)           # same Monday: nothing twice
    assert not st["actions"]


def test_mod_does_not_buy_overheated_coins():
    assert pf.overheated(40.0, None) and pf.overheated(None, 120.0) and pf.overheated(278.7, 10.0)
    assert not pf.overheated(39.9, 119.9) and not pf.overheated(None, None)
    conn = sqlite3.connect(":memory:")
    rows = [{"symbol": "BTC", "price": 80000.0, "score": {"short": 50, "mid": 50, "long": 50}},
            {"symbol": "ETH", "price": 2400.0, "score": {"short": 50, "mid": 50, "long": 50}}]
    mid = [{"symbol": "Q", "score": 95, "price": 0.12, "why": "", "chg7": 278.7, "chg30": 300.0},
           {"symbol": "QNT", "score": 90, "price": 80.0, "why": "", "chg7": 12.0, "chg30": 130.0},
           {"symbol": "NEAR", "score": 85, "price": 4.8, "why": "", "chg7": 5.0, "chg30": 70.0},
           {"symbol": "INJ", "score": 80, "price": 8.4, "why": "", "chg7": 15.9, "chg30": 50.0}]
    st = pf.SpotPortfolio(conn).step(rows, {"short": [], "mid": mid, "long": []}, True, T0)
    held = {p["symbol"] for p in st["positions"]}
    assert held == {"BTC", "ETH", "NEAR", "INJ"}                                 # the next candidates take the place of Q / QNT
    # through run(): a coin with the best trend after a +500 % month is still shown in the picks but not bought by «Умеренный»
    import os
    cwd, old = os.getcwd(), (pf.coingecko_markets, pf.hyperliquid_universe)
    mk = fake_markets()
    for r in mk:
        if r["symbol"] == "T05":
            r.update(chg30=500.0, chg200=900.0, chg7=60.0)
    tmp = __import__("tempfile").mkdtemp()
    try:
        os.chdir(tmp); os.makedirs("web")
        pf.coingecko_markets = lambda pages=2: [dict(r) for r in mk]
        pf.hyperliquid_universe = lambda: {"perps": {r["symbol"]: {"vol24": 1e8} for r in mk}, "spot": {}, "available": True}
        res = pf.run(sqlite3.connect(":memory:"), btc_bull=True, llm=None, now=T0 + 60_000)
    finally:
        os.chdir(cwd)
        pf.coingecko_markets, pf.hyperliquid_universe = old
    assert "T05" in {x["symbol"] for x in res["picks"]["mid"]} and "T05" not in {p["symbol"] for p in res["portfolios"]["mod"]["positions"]}
    assert len([p for p in res["portfolios"]["mod"]["positions"] if p["bucket"] == "mid"]) >= 3


# ── 6th profile «По рекомендациям»: buys what web/ideas.json says ──────────────────────────────────────────────────────────
import copy  # noqa: E402

from cnq.agents import spot_profiles as sp  # noqa: E402

_IRU = {"buy": "можно брать", "add": "можно добрать", "small": "можно на небольшую сумму", "wait": "ждём", "avoid": "не брать"}


def _irow(sym, status, price, kind="mid", why=None):
    return {"symbol": sym, "name": sym.title(), "kind": kind, "status": status, "status_ru": _IRU[status],
            "why_ru": why or f"{sym}: {_IRU[status]}", "price": price}


def _ideas(rows, asof, statuses=None):
    d = {"asof": asof, "available": True, "summary_ru": "тест", "rows": rows}
    if statuses is not None:
        d["statuses"] = statuses
    return d


def ideas_world():
    rows = [_irow("BTC", "buy", 80000, "core"), _irow("ETH", "buy", 2400, "core"), _irow("NAMESAKE", "buy", 1.0), _irow("NOPX", "buy", 3.0)]
    rows += [_irow(f"A{i}", "buy", 10.0 + i) for i in range(1, 7)]                 # A1…A6
    rows += [_irow("ADD1", "add", 5.0)]
    rows += [_irow(f"M{i}", "small", 0.01 * i, "meme") for i in range(1, 6)]         # M1…M5
    rows += [_irow("W1", "wait", 7.0), _irow("X1", "avoid", 9.0)]
    cg = {r["symbol"]: r["price"] for r in rows if r["kind"] != "meme" and r["symbol"] not in ("NAMESAKE", "NOPX")}
    cg["NAMESAKE"] = 50.0                                                          # another token with the same ticker
    meme = {r["symbol"]: r["price"] for r in rows if r["kind"] == "meme"}
    return rows, {"cg": cg, "meme": meme, "hl": {}}


def _set(rows, sym, **kw):
    for r in rows:
        if r["symbol"] == sym:
            r.update(kw)


def test_ideas_first_run_weights_cash_and_unpriceable_skips():
    rows, src = ideas_world()
    conn = sqlite3.connect(":memory:")
    port = sp.IdeasPortfolio(conn)
    st = port.step(src, _ideas(rows, T0 - 1_800_000), T0)
    by = {p["symbol"]: p for p in st["positions"]}
    big = {s for s, p in by.items() if p["role"] in ("buy", "add")}
    assert big == {"BTC", "ETH", "A1", "A2", "A3", "A4", "A5", "A6"}                 # at most 8; ADD1 has no place left
    # 18.10 evening: core BTC 30 % / ETH 10 % (SOL 5 %), other «можно брать» 7.5 %, small 2.5 % — here 40 + 6 × 7.5 = 85 % leaves
    # room for two small ones before the 10 % cash floor (index ≈ 100 → market weight ≈ cost weight)
    assert {s for s, p in by.items() if p["role"] == "small"} == {"M1", "M2"}
    assert all(abs(by[s]["weight"] - {"BTC": 0.30, "ETH": 0.10}.get(s, 0.075)) < 0.001 for s in big)
    assert all(abs(by[f"M{i}"]["weight"] - 0.025) < 0.001 for i in range(1, 3))
    assert st["cash_pct"] >= 10 and port.rp.cash() >= 0.10 * st["index"] / 100 - 1e-9 and 99.85 < st["index"] < 100   # fees 0.1 %
    skips = {a["symbol"]: a["reason"] for a in st["actions"] if a["action"] == "SKIP"}
    assert set(skips) == {"NAMESAKE", "NOPX"} and "другой токен" in skips["NAMESAKE"] and "нет живой цены" in skips["NOPX"]
    nb = {x["symbol"]: x["why_ru"] for x in st["not_bought"]}
    assert "8 мест" in nb["ADD1"] and all("кэш" in nb[m] for m in ("M3", "M4", "M5")) and "W1" not in nb and "X1" not in nb
    for k in ("key", "name", "about", "positions", "cash_pct", "invested_pct", "index", "history", "actions", "recent_actions"):
        assert k in st, k
    assert st["key"] == "ideas" and st["name"] == "CRYPTO NOVA портфель" and st["ours"] and not st["experiment"] and "не обещаем" in st["about"]
    assert by["A1"]["bucket"] == "mid" and by["BTC"]["bucket"] == "core" and by["M1"]["bucket"] == "meme"
    assert by["A1"]["role_ru"] == "из таблицы: можно брать" and by["M1"]["role_ru"] == "на небольшую сумму"
    assert by["M1"]["price_src"] == "meme" and by["A1"]["price_src"] == "cg" and by["A1"]["price_live"]
    for p in st["positions"]:
        assert p["reco"]["k"] in ("hold", "add", "trim", "sell") and p["reco"]["t"] and p["reco"]["why"]
        assert p["status"] == "OPEN" and "pnl_usd" in p and abs(p["value_usd"] - 10_000 * p["weight"] * st["index"] / 100) < 1
    assert abs(st["sleeves"]["buy"]["now_pct"] - 85) < 0.5 and abs(st["sleeves"]["small"]["now_pct"] - 5) < 0.5
    st2 = port.step(src, _ideas(rows, T0), T0 + 1_800_000)          # nothing changed → no trades (places full → no SKIP either)
    assert not st2["actions"] and all(a["action"] != "SKIP" for a in st2["recent_actions"]) and len(st2["recent_actions"]) == 10
    src3 = copy.deepcopy(src)
    src3["cg"]["A1"] *= 1.1
    st3 = port.step(src3, _ideas(rows, T0), T0 + 3_600_000)                          # the index moves with the live price
    assert st3["index"] > st2["index"] and {p["symbol"]: p for p in st3["positions"]}["A1"]["pnl_pct"] == 10.0
    # 18.10: every holding has a horizon — core long (no stop), «можно брать» mid (stop −20 %), small short (stop −35 %, ×2 half)
    b3 = {p["symbol"]: p for p in st3["positions"]}
    assert b3["BTC"]["horizon"] == "long" and b3["BTC"]["levels"]["stop"] is None and b3["BTC"]["held_days"] == 0.0
    assert b3["A1"]["horizon"] == "mid" and abs(b3["A1"]["levels"]["stop"] - 11.0 * 0.8) < 1e-6 and b3["A1"]["levels"]["take_half"] is None
    assert b3["M1"]["horizon"] == "short" and abs(b3["M1"]["levels"]["take_half"] - 0.02) < 1e-9 and "2–4 недели" in b3["M1"]["horizon_ru"]
    hz = st3["horizons"]
    assert [hz[h]["n"] for h in ("long", "mid", "short")] == [2, 6, 2] and abs(sum(hz[h]["share_pct"] for h in hz) - st3["invested_pct"]) < 1.5
    assert hz["mid"]["base"]["days"] == 60 and "быть лучше" in hz["mid"]["base"]["ru"] and hz["long"]["plan_ru"]


def test_ideas_avoid_stops_body_trailing_and_cooldown():
    rows, src = ideas_world()
    conn = sqlite3.connect(":memory:")
    port = sp.IdeasPortfolio(conn)
    port.step(src, _ideas(rows, T0), T0)
    rows1, src1 = copy.deepcopy(rows), copy.deepcopy(src)
    _set(rows1, "A1", status="avoid", status_ru="не брать", why_ru="Оборот $3M в сутки — цену легко сдвинуть")
    src1["cg"]["A2"] *= 0.79                                                          # −21 % → stop −20 %
    src1["cg"]["A3"] *= 0.85                                                          # −15 % → no stop yet
    src1["cg"]["BTC"] *= 0.60                                                         # core: no stop
    src1["meme"]["M1"] *= 0.64                                                        # −36 % → stop −35 %
    src1["meme"]["M3"] *= 0.70                                                        # −30 % → kept (small stop is −35 %)
    src1["meme"]["M2"] *= 2.0                                                         # ×2 → sell half
    st = port.step(src1, _ideas(rows1, T0 + DAY), T0 + DAY)
    acts = {(a["symbol"], a["action"]): a for a in st["actions"]}
    assert ("A1", "SELL") in acts and "Оборот $3M в сутки" in acts[("A1", "SELL")]["reason"]
    assert ("A2", "SELL") in acts and acts[("A2", "SELL")]["reason"].startswith("Стоп −20%")
    assert ("M1", "SELL") in acts and acts[("M1", "SELL")]["reason"].startswith("Стоп −35%") and ("M2", "TRIM") in acts
    assert not {("A3", "SELL"), ("BTC", "SELL"), ("M3", "SELL")} & set(acts)
    by = {p["symbol"]: p for p in st["positions"]}
    assert by["M2"]["fixes"] == 1 and by["M2"]["reco"]["t"] == "Едет бесплатно" and abs(by["M2"]["cost_weight"] - 0.025 / 1.001 / 2) < 1e-6
    # 18.10: the core has no stop and is topped back up to its plan (BTC 30 %) when it falls under ¾ of it (cash permitting)
    assert ("BTC", "ADD") in acts and "доливаем" in acts[("BTC", "ADD")]["reason"]
    assert -40.0 < by["BTC"]["pnl_pct"] < 0 and by["BTC"]["reco"]["k"] == "hold"
    assert by["A3"]["reco"]["k"] == "sell" and by["A3"]["reco"]["t"] == "У стопа −20%"
    # freed places go to the next rows of the table; sold coins are not bought back (14 days)
    assert ("ADD1", "BUY") in acts and ("M4", "BUY") in acts and ("A2", "BUY") not in acts and ("M1", "BUY") not in acts
    assert "снова не раньше" in {x["symbol"]: x["why_ru"] for x in st["not_bought"]}["A2"]
    # the rest of a ×2 position trails 35 % from its peak
    src2 = copy.deepcopy(src1)
    src2["meme"]["M2"] = src["meme"]["M2"] * 2.5
    st = port.step(src2, _ideas(rows1, T0 + DAY + 3_600_000), T0 + DAY + 3_600_000)
    assert not [a for a in st["actions"] if a["symbol"] == "M2"]
    src2["meme"]["M2"] = src["meme"]["M2"] * 2.5 * 0.6
    st = port.step(src2, _ideas(rows1, T0 + DAY + 7_200_000), T0 + DAY + 7_200_000)
    m2 = [a for a in st["actions"] if a["symbol"] == "M2"]
    assert len(m2) == 1 and m2[0]["action"] == "SELL" and m2[0]["reason"].startswith("Трейлинг")
    # cooldown: A2 (sold on T0+1d, still «можно брать») is not bought on day 13, bought again after 14 days
    _set(rows1, "A2", price=src1["cg"]["A2"])
    st = port.step(src1, _ideas(rows1, T0 + 14 * DAY), T0 + 14 * DAY)
    assert ("A2", "BUY") not in {(a["symbol"], a["action"]) for a in st["actions"]}
    st = port.step(src1, _ideas(rows1, T0 + 15 * DAY + 3_600_000), T0 + 15 * DAY + 3_600_000)
    why = {x["symbol"]: x["why_ru"] for x in st["not_bought"]}.get("A2", "")
    # after 14 days the cooldown is over; here the BTC top-up used the free cash, so A2 now waits for cash, not for the cooldown
    assert ("A2", "BUY") in {(a["symbol"], a["action"]) for a in st["actions"]} or ("снова не раньше" not in why and "кэш" in why)


def test_ideas_weekly_review_on_monday_once():
    rows, src = ideas_world()
    conn = sqlite3.connect(":memory:")
    port = sp.IdeasPortfolio(conn)
    port.step(src, _ideas(rows, T0), T0)                                              # Wednesday 14.10
    rows1, src1 = copy.deepcopy(rows), copy.deepcopy(src)
    for s in ("A3", "A4"):
        _set(rows1, s, status="wait", status_ru="ждём", why_ru="Консультант 55/100 ниже порога 60 — ждём")
    src1["cg"]["A3"] *= 0.88                                                          # «ждём» and −12 % → sold on Monday
    src1["cg"]["A4"] *= 0.95                                                          # «ждём» and −5 % → kept
    src1["cg"]["A5"] *= 0.88                                                          # −12 % but still «можно брать» → kept
    sun = T0 + 4 * DAY
    st = port.step(src1, _ideas(rows1, sun), sun)
    assert not [a for a in st["actions"] if a["action"] == "SELL"]
    assert {p["symbol"]: p for p in st["positions"]}["A3"]["reco"]["t"] == "Продать в понедельник"
    mon = T0 + 5 * DAY + 3_600_000                                                    # Monday 19.10
    st = port.step(src1, _ideas(rows1, mon), mon)
    sells = {a["symbol"]: a["reason"] for a in st["actions"] if a["action"] == "SELL"}
    assert set(sells) == {"A3"} and sells["A3"].startswith("Недельный пересмотр") and "−12%" in sells["A3"]
    src1["cg"]["A4"] = src["cg"]["A4"] * 0.85                                         # later the same Monday: −15 %, review already done
    st = port.step(src1, _ideas(rows1, mon + 3_600_000), mon + 3_600_000)
    assert not [a for a in st["actions"] if a["action"] == "SELL"] and st["last_weekly"] == mon
    nxt = T0 + 12 * DAY + 3_600_000                                                   # next Monday → A4 goes
    st = port.step(src1, _ideas(rows1, nxt), nxt)
    assert {a["symbol"] for a in st["actions"] if a["action"] == "SELL"} == {"A4"}


def test_ideas_stale_or_missing_table_no_buys_but_holdings_priced():
    rows, src = ideas_world()
    conn = sqlite3.connect(":memory:")
    port = sp.IdeasPortfolio(conn)
    st = port.step(src, _ideas(rows, T0 - 4 * 3_600_000), T0)                          # 4 h old
    assert not st["positions"] and not st["actions"] and st["index"] == 100.0 and st["cash_pct"] == 100
    assert not st["ideas"]["fresh"] and "не обновлялась" in st["ideas"]["note_ru"]
    st = port.step(src, None, T0 + 60_000)                                             # no file at all
    assert not st["positions"] and "нет" in st["ideas"]["note_ru"]
    port.step(src, _ideas(rows, T0), T0 + 120_000)
    n = len(port.rp.positions())
    rows1, src1 = copy.deepcopy(rows), copy.deepcopy(src)
    _set(rows1, "A1", status="avoid", status_ru="не брать", why_ru="красный флаг")
    for s in src1["cg"]:
        src1["cg"][s] *= 1.05
    src1["meme"]["M1"] *= 0.6                                                          # price rules still work on a stale table
    t = T0 + 5 * 3_600_000
    st = port.step(src1, _ideas(rows1, T0 + 1_000), t)                                 # the table is ~5 h old
    assert [(a["symbol"], a["action"]) for a in st["actions"]] == [("M1", "SELL")]     # «не брать» ignored, nothing bought
    assert len(st["positions"]) == n - 1 and st["index"] > 100 and all(p["price_live"] for p in st["positions"])
    assert all("таблица устарела" in p["reco"]["why"] for p in st["positions"] if p["reco"]["k"] == "hold")
    # statuses beyond the 30 shown rows: A6 left the rows but `statuses` says «не брать» → sold
    rows2 = [r for r in rows1 if r["symbol"] != "A6"]
    st = port.step(src1, _ideas(rows2, t, statuses={"A6": {"status": "avoid", "status_ru": "не брать", "why_ru": "мёртвая монета",
                                                           "kind": "mid", "price": 16.0}}), t + 60_000)
    assert {(a["symbol"], a["action"]) for a in st["actions"]} >= {("A6", "SELL"), ("A1", "SELL")}


def test_ideas_core_is_btc_heavy_and_trimmed_back_on_monday():
    """18.10 evening: core BTC 30 % / ETH 10 % / SOL 5 %; a core coin that grew past 1.5× its plan is trimmed back at the Monday review."""
    rows, src = ideas_world()
    conn = sqlite3.connect(":memory:")
    port = sp.IdeasPortfolio(conn)
    port.step(src, _ideas(rows, T0), T0)                                              # Wednesday
    src1 = copy.deepcopy(src)
    src1["cg"]["BTC"] *= 2.5
    rows1 = copy.deepcopy(rows)
    _set(rows1, "BTC", price=src1["cg"]["BTC"])
    tue = T0 + DAY
    st = port.step(src1, _ideas(rows1, tue), tue)
    assert not [a for a in st["actions"] if a["symbol"] == "BTC"]                    # not Monday → no trim
    mon = T0 + 5 * DAY + 3_600_000
    st = port.step(src1, _ideas(rows1, mon), mon)
    a = next(x for x in st["actions"] if x["symbol"] == "BTC")
    by = {p["symbol"]: p for p in st["positions"]}
    assert a["action"] == "TRIM" and "больше плана 30%" in a["reason"] and abs(by["BTC"]["weight"] - 0.30) < 0.002
    assert sp.core_target("BTC") == 0.30 and sp.core_target("SOL") == 0.05 and sp.core_target("XYZ") == 0.10
