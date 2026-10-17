"""Meme desk (HL meme perps scanner + paper «Мем-портфель» + Solana watchlist): offline, fake fetchers."""
import json
import sqlite3

from cnq.agents import memes as mm

H, DAY = mm.H, mm.DAY
NOW = 1_790_000_000_000 - 1_790_000_000_000 % (4 * H) + 20 * 60_000      # 20 min after a 4h boundary


def flat_1h(now, px=1.0, vol=1e5, days=22):
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
            x[2] = px * 1.12; x[4] = px * 1.10; x[5] = 1e5 * k
    return c


def with_dip(now, px=1.0, close=0.72, days=22):
    """Quiet market, then the last CLOSED 4h bar falls to `close` (24 h high = px × 1.01) — a fresh dip signal."""
    c = flat_1h(now, px, days=days)
    b0 = now - now % (4 * H) - 4 * H
    for x in c:
        if b0 <= x[0] < b0 + 4 * H:
            x[1], x[2], x[3], x[4] = px, px, close * 0.98, close
    return c


def ctx_of(px, vol=5e7, funding_h=0.0, prev=None):
    return {"px": px, "prev": prev or px, "vol24": vol, "oi_usd": 1e7, "funding_h": funding_h}


def no_sol():
    return {"rows": [], "sources": {}}


def test_to_4h_and_metrics():
    c = with_breakout(NOW)
    b4 = mm.to_4h(c, NOW)
    assert all(b[0] % (4 * H) == 0 and b[0] + 4 * H <= NOW for b in b4)
    r = mm.metrics("WIF", ctx_of(1.10, prev=1.0), c, NOW)
    assert r["breakout"] is True and r["break_vol_x"] >= 3 and r["signal_bar"] == NOW - NOW % (4 * H) - 4 * H
    assert abs(r["chg24h"] - 10) < 1e-6 and r["above_ma20"] is True and r["dip_signal"] is False
    q = mm.metrics("WIF", ctx_of(1.0), flat_1h(NOW), NOW)
    assert q["breakout"] is False and abs(q["chg5d"]) < 1e-9 and q["dip_signal"] is False and q["liq_day"] >= 1e6
    d = mm.metrics("WIF", ctx_of(0.72), with_dip(NOW), NOW)
    assert d["dip_signal"] is True and abs(d["dip_dd_pct"] - (0.72 / 1.01 - 1) * 100) < 0.01
    shallow = mm.metrics("WIF", ctx_of(0.80), with_dip(NOW, close=0.80), NOW)        # −21% → no signal
    assert shallow["dip_signal"] is False
    young = mm.metrics("WIF", ctx_of(0.72), with_dip(NOW, days=12), NOW)               # < 20 days of history
    assert young["dip_signal"] is False and young["new_listing"] is True
    thin = mm.metrics("WIF", ctx_of(0.72), [x[:5] + [x[5] / 100] for x in with_dip(NOW)], NOW)   # $24k a day
    assert thin["dip_signal"] is False


def test_score_hl_and_signal_text():
    rows = [mm.metrics("A", ctx_of(0.72, vol=5e7), with_dip(NOW), NOW),
            mm.metrics("B", ctx_of(0.85, vol=5e7), with_dip(NOW, close=0.85), NOW),
            mm.metrics("C", ctx_of(1.4, vol=5e7, prev=1.0), flat_1h(NOW), NOW),
            mm.metrics("D", ctx_of(0.72, vol=1e5), with_dip(NOW), NOW)]
    mm.score_hl(rows)
    sc = {r["coin"]: r["score"] for r in rows}
    assert sc["D"] is None and sc["A"] > sc["B"] > sc["C"]
    assert set(rows[0]["score_parts"]) == {"близость к сигналу", "объём 24ч", "открытый интерес", "погоня"}
    assert rows[2]["score_parts"]["погоня"] == -20
    assert mm.signal_text(rows[0]).startswith("сигнал: обвал")
    assert mm.signal_text(rows[1]).startswith("близко к сигналу")
    assert mm.signal_text(rows[2]) == "сильный рост за сутки — не догоняем"
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


def _run(conn, now, px, candles, rule="dip", funding_h=0.0):
    old = mm.RULE
    mm.RULE = rule
    try:
        return mm.run(conn, now, hl_ctx_fn=lambda: {"WIF": ctx_of(px, funding_h=funding_h), "POPCAT": ctx_of(1.0)},
                      hl_candles_fn=lambda c, s, e: candles.get(c, flat_1h(now)), sol_fn=no_sol, spot_fn=list, btc_fn=dict)
    finally:
        mm.RULE = old


def test_dip_entry_then_stop():
    conn = sqlite3.connect(":memory:")
    r = _run(conn, NOW, 0.72, {"WIF": with_dip(NOW)})
    pos = r["account"]["positions"]
    assert [p["coin"] for p in pos] == ["WIF"] and any(e["text"].startswith("Купили WIF по $0.72") for e in r["account"]["events"])
    assert abs(pos[0]["invested"] - 1000 * (1 + mm.COST / 2)) < 0.01 and abs(pos[0]["stop_px"] - 0.72 * 0.75) < 1e-12
    # same 4h bar again → no second entry; price falls through the stop inside a closed 1h bar
    r = _run(conn, NOW + 30 * 60_000, 0.70, {"WIF": with_dip(NOW)})
    assert len(r["account"]["positions"]) == 1
    t2 = NOW + 2 * H
    c = with_dip(NOW) + [[NOW - NOW % H + H, 0.70, 0.71, 0.45, 0.50, 1e5]]
    r = _run(conn, t2, 0.50, {"WIF": c})
    acc = r["account"]
    assert acc["positions"] == [] and acc["closed"][0]["why"].startswith("стоп")
    assert abs(acc["closed"][0]["exit_px"] - 0.72 * 0.75) < 1e-9                  # filled at the stop, not at the bar low
    assert acc["closed"][0]["pnl_pct"] < -25 and any(e["text"].startswith("Стоп: продали WIF") for e in acc["events"])
    assert abs(acc["equity"] - (10_000 + acc["closed"][0]["pnl_usd"])) < 0.02
    assert acc["closed"][0]["context"] == ["мало ликвидных мемов для оценки", "BTC: нет данных"]
    assert acc["stats"]["by_context"]["BTC: нет данных"]["trades"] == 1 and acc["stats"]["trades"] == 1 and acc["stats"]["wins"] == 0
    r = _run(conn, t2 + 30 * 60_000, 0.50, {"WIF": with_dip(NOW)})       # the old signal bar is not re-used
    assert r["account"]["positions"] == []


def test_entry_context_tags():
    liq = [{"liq_day": 5e6, "dip_dd_pct": d} for d in (-28, -18, -3, -2)]
    assert mm.entry_context(liq, {"btc_above_ma50": False}) == ["массовый обвал", "BTC ниже 50-дн. средней"]
    assert mm.entry_context(liq[:1] + [{"liq_day": 5e6, "dip_dd_pct": -1}] * 3, {"btc_above_ma50": True}) == ["обвал одной монеты", "BTC выше 50-дн. средней"]


def test_stale_signal_not_entered():
    conn = sqlite3.connect(":memory:")
    r = _run(conn, NOW + 5 * H, 0.72, {"WIF": with_dip(NOW)[:-5]})       # last closed bar is the dip, but runs were missed for 5 h
    assert r["account"]["positions"] == []


def test_take_profit_half_then_time_exit_and_funding():
    conn = sqlite3.connect(":memory:")
    _run(conn, NOW, 1.0, {"WIF": with_dip(NOW, px=1.4, close=1.0)}, funding_h=0.0001)
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


def test_spot_and_btc_context():
    meta = {"tokens": [{"index": 0, "name": "USDC"}, {"index": 1, "name": "WOW"}, {"index": 2, "name": "UBTC"}, {"index": 3, "name": "NEKO"}],
            "universe": [{"tokens": [1, 0], "name": "@1"}, {"tokens": [2, 0], "name": "@2"}, {"tokens": [3, 0], "name": "@3"}]}
    ctxs = [{"markPx": "2", "prevDayPx": "1", "dayNtlVlm": "4e7"}, {"markPx": "80000", "prevDayPx": "80000", "dayNtlVlm": "9e8"},
            {"markPx": "0.5", "prevDayPx": "0.5", "dayNtlVlm": "5e4"}]
    old = mm._req
    mm._req = lambda url, body=None, timeout=10, headers=None: [meta, ctxs]
    try:
        sp = mm.hl_spot()
    finally:
        mm._req = old
    assert [x["token"] for x in sp] == ["WOW"] and sp[0]["chg24h"] == 100.0          # wrapped BTC and thin NEKO dropped
    daily = [[NOW - (60 - i) * DAY, 1, 1, 1, 100.0 + i, 1] for i in range(61)]
    b = mm.btc_context(lambda c, s, e, iv: daily, NOW)
    assert b["btc_above_ma50"] is True and abs(b["btc_ma50"] - sum(100.0 + i for i in range(10, 60)) / 50) < 1e-9


def test_pending_rule_no_trades_and_no_hl():
    conn = sqlite3.connect(":memory:")
    r = _run(conn, NOW, 0.72, {"WIF": with_dip(NOW)}, rule="pending")
    assert r["account"]["positions"] == [] and r["account"]["equity"] == 10_000
    r = mm.run(conn, NOW + H, hl_ctx_fn=lambda: {}, hl_candles_fn=lambda *a: [], sol_fn=no_sol, spot_fn=list, btc_fn=dict)
    assert r["hl_memes"] == [] and r["account"]["equity"] == 10_000 and r["warnings"][0].startswith("Hyperliquid не ответил")
    assert r["sources"]["hyperliquid"] is False


def test_json_shape_and_max_positions():
    conn = sqlite3.connect(":memory:")
    coins = [f"C{i}" for i in range(9)]
    old = mm.RULE, mm.MEME_COINS
    mm.RULE = "dip"
    try:
        r = mm.run(conn, NOW, hl_ctx_fn=lambda: {c: ctx_of(0.74 - 0.01 * int(c[1:])) for c in coins},
                   hl_candles_fn=lambda c, s, e: with_dip(NOW, close=0.74 - 0.01 * int(c[1:])), sol_fn=no_sol, spot_fn=list, btc_fn=dict)
    finally:
        mm.RULE = old[0]
    acc = r["account"]
    assert len(acc["positions"]) == mm.MAX_POS
    assert {p["coin"] for p in acc["positions"]} == {f"C{i}" for i in range(3, 9)}      # deepest dips first
    for k in ("asof", "account", "hl_memes", "solana", "warnings", "research_summary", "sources", "score_formula", "solana_note",
              "market", "hl_spot", "hl_spot_note"):
        assert k in r, k
    for k in ("equity", "pnl_usd", "pnl_pct", "positions", "closed", "events", "rules", "rule_status", "history", "limits"):
        assert k in acc, k
    assert any("100%" in w for w in r["warnings"])
    json.dumps(r, ensure_ascii=False)
