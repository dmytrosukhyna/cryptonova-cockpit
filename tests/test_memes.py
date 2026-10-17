"""Meme desk (HL meme perps scanner + paper «Мем-портфель» + Solana watchlist): offline, fake fetchers."""
import json
import sqlite3

from cnq.agents import memes as mm

H, DAY = mm.H, mm.DAY
NOW = 1_790_000_000_000 - 1_790_000_000_000 % (4 * H) + 20 * 60_000      # 20 min after a 4h boundary


def flat_1h(now, px=1.0, vol=1000.0, days=22):
    """22 days of quiet 1h candles ending with the bar that closes at the last 4h boundary (+ the forming bar)."""
    out = []
    t = now - now % H - days * DAY
    while t <= now - now % H:
        out.append([t, px, px * 1.01, px * 0.99, px, vol])
        t += H
    return out


def with_breakout(now, px=1.0, k=6.0):
    c = flat_1h(now, px)
    b0 = now - now % (4 * H) - 4 * H                      # last CLOSED 4h bar
    for x in c:
        if b0 <= x[0] < b0 + 4 * H:
            x[2] = px * 1.12; x[4] = px * 1.10; x[5] = 1000.0 * k
    return c


def ctx_of(px, vol=5e7, funding_h=0.0, prev=None):
    return {"px": px, "prev": prev or px, "vol24": vol, "oi_usd": 1e7, "funding_h": funding_h}


def no_sol():
    return {"rows": [], "sources": {}}


def test_to_4h_and_breakout_metrics():
    c = with_breakout(NOW)
    b4 = mm.to_4h(c, NOW)
    assert all(b[0] % (4 * H) == 0 and b[0] + 4 * H <= NOW for b in b4)
    r = mm.metrics("WIF", ctx_of(1.10, prev=1.0), c, NOW)
    assert r["breakout"] is True and r["break_vol_x"] >= 3 and r["break_bar"] == NOW - NOW % (4 * H) - 4 * H
    assert abs(r["chg24h"] - 10) < 1e-6 and r["above_ma20"] is True
    q = mm.metrics("WIF", ctx_of(1.0), flat_1h(NOW), NOW)
    assert q["breakout"] is False and abs(q["chg5d"]) < 1e-9


def test_score_hl_and_signal_text():
    rows = [mm.metrics(c, ctx_of(p, vol=v, prev=1.0), flat_1h(NOW, 1.0), NOW) for c, p, v in
            (("A", 1.3, 5e7), ("B", 1.0, 5e7), ("C", 0.9, 5e7), ("D", 1.5, 1e5))]
    mm.score_hl(rows)
    sc = {r["coin"]: r["score"] for r in rows}
    assert sc["D"] is None and sc["A"] > sc["B"] > sc["C"]
    assert set(rows[0]["score_parts"]) == {"рост 5д", "рост 24ч", "объём к 7д", "выше 20-дн. средней", "открытый интерес"}
    assert mm.signal_text(rows[3]) == "мало объёма — не торгуем"


def _sol_row(**kw):
    r = {"symbol": "CAT", "name": "Cat", "mint": "M1", "pool": "P1", "dex": "raydium", "price": 0.01, "chg1h": 2.0, "chg6h": 30.0,
         "chg24h": 80.0, "vol1h": 1e5, "vol6h": 6e5, "vol24": 2e6, "liquidity": 8e5, "fdv": 1e7, "mcap": 1e7, "age_h": 200.0,
         "buys24": 900, "sells24": 800, "buyers24": 400, "sellers24": 300, "source": "trending_pools", "link": "https://dexscreener.com/solana/P1",
         "auth": {"mint": None, "freeze": None, "ext": [], "fee": None}, "socials": True}
    r.update(kw)
    return r


def test_sol_flags_and_score():
    good = _sol_row()
    good["flags"] = mm.sol_flags(good); mm.score_sol(good)
    assert good["passed"] and good["red_flags"] == 0 and 0 < good["score"] <= 100
    bad = _sol_row(age_h=5.0, auth={"mint": "X", "freeze": "Y", "ext": ["permanentDelegate"], "fee": None}, sells24=0, buys24=300,
                   rug={"risks": [{"name": "Mutable metadata", "level": "warn"}, {"name": "Creator history of rugged tokens", "level": "danger"}]})
    bad["flags"] = mm.sol_flags(bad); mm.score_sol(bad)
    texts = " | ".join(f["text"] for f in bad["flags"])
    for s in ("новый — высокий риск", "допечатать", "заморозить", "постоянный делегат", "ханипот", "метаданные можно менять", "«раги»"):
        assert s in texts, s
    assert not bad["passed"] and bad["score"] < good["score"]
    unk = _sol_row(); unk.pop("auth")
    assert any(f["level"] == "info" and "не проверены" in f["text"] for f in mm.sol_flags(unk))


def test_sol_fetch_parsing():
    gt = {"data": [{"id": "solana_P1", "type": "pool", "attributes": {
        "name": "CAT / SOL", "address": "P1", "base_token_price_usd": "0.01", "fdv_usd": "10000000", "market_cap_usd": None,
        "reserve_in_usd": "800000", "pool_created_at": "2020-01-01T00:00:00Z",
        "price_change_percentage": {"h1": "2", "h6": "30", "h24": "80"}, "volume_usd": {"h1": "100000", "h6": "600000", "h24": "2000000"},
        "transactions": {"h24": {"buys": 900, "sells": 800, "buyers": 400, "sellers": 300}}},
        "relationships": {"base_token": {"data": {"id": "solana_M1"}}, "dex": {"data": {"id": "raydium"}}}},
        {"id": "solana_P2", "type": "pool", "attributes": {"name": "SOL / USDC", "address": "P2", "reserve_in_usd": "9e7"},
         "relationships": {"base_token": {"data": {"id": "solana_So111"}}}}],
        "included": [{"id": "solana_M1", "type": "token", "attributes": {"symbol": "CAT", "name": "Cat"}},
                     {"id": "solana_So111", "type": "token", "attributes": {"symbol": "SOL", "name": "Wrapped SOL"}}]}
    ds = [{"baseToken": {"address": "M1"}, "url": "https://dexscreener.com/solana/p1", "priceUsd": "0.0101", "liquidity": {"usd": 790000},
           "boosts": {"active": 2}, "info": {"websites": [{"url": "x"}]}}]

    def get(url, headers=None):
        if "geckoterminal" in url:
            return gt if "page=1" in url else {"data": []}
        if "dexscreener" in url:
            return ds
        if "rugcheck" in url:
            return {"score_normalised": 12, "risks": [{"name": "Low Liquidity", "level": "warn"}]}
        raise OSError("unreachable")

    def post(url, body):
        return {"result": {"value": [{"data": {"parsed": {"info": {"mintAuthority": None, "freezeAuthority": None}}}}]}}
    s = mm.sol_fetch(get, post)
    assert [r["symbol"] for r in s["rows"]] == ["CAT"]                        # SOL/USDC pool dropped
    r = s["rows"][0]
    assert r["liquidity"] == 800000 and r["boosted"] and r["link"] == "https://dexscreener.com/solana/p1" and r["age_h"] > 24
    assert r["auth"]["mint"] is None and r["rug"]["score"] == 12
    assert s["sources"] == {"geckoterminal": True, "dexscreener": True, "solana_rpc": True, "rugcheck": True}
    fl = " | ".join(f["text"] for f in mm.sol_flags(r))
    assert "платная реклама" in fl and "мало ликвидности" in fl
    dead = mm.sol_fetch(lambda url, headers=None: (_ for _ in ()).throw(OSError()), lambda u, b: None)
    assert dead["rows"] == [] and not any(dead["sources"].values())


def _run(conn, now, px, candles, rule="breakout", funding_h=0.0):
    old = mm.RULE
    mm.RULE = rule
    try:
        return mm.run(conn, now, hl_ctx_fn=lambda: {"WIF": ctx_of(px, funding_h=funding_h), "POPCAT": ctx_of(1.0)},
                      hl_candles_fn=lambda c, s, e: candles.get(c, flat_1h(now)), sol_fn=no_sol)
    finally:
        mm.RULE = old


def test_breakout_entry_then_stop():
    conn = sqlite3.connect(":memory:")
    r = _run(conn, NOW, 1.10, {"WIF": with_breakout(NOW)})
    pos = r["account"]["positions"]
    assert [p["coin"] for p in pos] == ["WIF"] and any(e["text"].startswith("Купили WIF по $1.1000") for e in r["account"]["events"])
    assert abs(pos[0]["invested"] - 1000 * (1 + mm.COST / 2)) < 0.01 and pos[0]["stop_px"] == 1.10 * 0.75
    # same 4h bar again → no second entry; price falls through the stop inside a closed 1h bar
    r = _run(conn, NOW + 30 * 60_000, 1.05, {"WIF": with_breakout(NOW)})
    assert len(r["account"]["positions"]) == 1
    t2 = NOW + 2 * H
    c = with_breakout(NOW) + [[NOW - NOW % H + H, 1.05, 1.06, 0.70, 0.80, 1000.0]]
    r = _run(conn, t2, 0.80, {"WIF": c})
    acc = r["account"]
    assert acc["positions"] == [] and acc["closed"][0]["why"].startswith("стоп")
    assert abs(acc["closed"][0]["exit_px"] - 1.10 * 0.75) < 1e-9                  # filled at the stop, not at the bar low
    assert acc["closed"][0]["pnl_pct"] < -25 and any(e["text"].startswith("Стоп: продали WIF") for e in acc["events"])
    assert abs(acc["equity"] - (10_000 + acc["closed"][0]["pnl_usd"])) < 0.02
    r = _run(conn, t2 + 30 * 60_000, 0.80, {"WIF": with_breakout(NOW)})   # the old breakout bar is not re-used
    assert r["account"]["positions"] == []


def test_take_profit_half_then_time_exit_and_funding():
    conn = sqlite3.connect(":memory:")
    _run(conn, NOW, 1.0, {"WIF": with_breakout(NOW, px=1.0 / 1.10)}, funding_h=0.0001)
    t1 = NOW + 3 * H
    c = flat_1h(NOW, 1.0) + [[NOW - NOW % H + H, 1.0, 1.60, 0.98, 1.40, 1000.0]]
    r = _run(conn, t1, 1.40, {"WIF": c}, funding_h=0.0001)
    p = r["account"]["positions"][0]
    assert p["tp_done"] and abs(p["qty"] - 500) < 1e-6 and p["funding_paid"] > 0
    assert any("Зафиксировали половину WIF по $1.5000" in e["text"] for e in r["account"]["events"])
    r = _run(conn, NOW + (mm.HOLD_H + 1) * H, 1.20, {"WIF": flat_1h(NOW, 1.0)}, funding_h=0.0001)
    acc = r["account"]
    assert acc["positions"] == [] and "время вышло" in acc["closed"][0]["why"]
    # 500 sold at 1.5, 500 at 1.2, minus costs and funding
    gross = 500 * 1.5 * (1 - mm.COST / 2) + 500 * 1.2 * (1 - mm.COST / 2)
    assert acc["closed"][0]["pnl_usd"] < gross - 1000 and acc["closed"][0]["pnl_usd"] > gross - 1000 - 10


def test_pending_rule_no_trades_and_no_hl():
    conn = sqlite3.connect(":memory:")
    r = _run(conn, NOW, 1.10, {"WIF": with_breakout(NOW)}, rule="pending")
    assert r["account"]["positions"] == [] and r["account"]["equity"] == 10_000
    r = mm.run(conn, NOW + H, hl_ctx_fn=lambda: {}, hl_candles_fn=lambda *a: [], sol_fn=no_sol)
    assert r["hl_memes"] == [] and r["account"]["equity"] == 10_000 and r["warnings"][0].startswith("Hyperliquid не ответил")
    assert r["sources"]["hyperliquid"] is False


def test_json_shape_and_max_positions():
    conn = sqlite3.connect(":memory:")
    coins = [f"C{i}" for i in range(9)]
    old = mm.RULE, mm.MEME_COINS
    mm.RULE = "breakout"
    try:
        r = mm.run(conn, NOW, hl_ctx_fn=lambda: {c: ctx_of(1.10) for c in coins},
                   hl_candles_fn=lambda c, s, e: with_breakout(NOW, k=5 + int(c[1:])), sol_fn=no_sol)
    finally:
        mm.RULE = old[0]
    acc = r["account"]
    assert len(acc["positions"]) == mm.MAX_POS
    assert {p["coin"] for p in acc["positions"]} == {f"C{i}" for i in range(3, 9)}      # strongest volume spikes first
    for k in ("asof", "account", "hl_memes", "solana", "warnings", "research_summary", "sources", "score_formula", "solana_note"):
        assert k in r, k
    for k in ("equity", "pnl_usd", "pnl_pct", "positions", "closed", "events", "rules", "rule_status", "history", "limits"):
        assert k in acc, k
    assert any("100%" in w for w in r["warnings"])
    json.dumps(r, ensure_ascii=False)
