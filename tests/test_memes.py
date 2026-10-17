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


def no_spot(conn, now, hl, deadline):
    return {"rows": [], "sources": {}, "note": "test"}


def no_holders(mint):
    return None


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
    assert any(f["level"] == "info" and "RugCheck не проверял" in f["text"] for f in mm.sol_flags(unk))
    assert not any("RugCheck не проверял" in f["text"] for f in mm.sol_flags(_sol_row(rug={"score": 1, "risks": []})))


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
                      hl_candles_fn=lambda c, s, e: candles.get(c, flat_1h(now)), sol_fn=no_sol, btc_fn=dict, spot_fn=no_spot, holders_fn=no_holders)
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


def _bars(h0, spec):
    """1h candles from h0: list of (high, low, close) per hour (open = previous close)."""
    out, prev = [], spec[0][2]
    for k, (hi, lo, cl) in enumerate(spec):
        out.append([h0 + k * H, prev, hi, lo, cl, 1e5]); prev = cl
    return out


class exit_as:
    """Temporarily set the live exit parameters."""
    def __init__(self, **kw):
        self.kw = kw

    def __enter__(self):
        self.old = dict(mm.EXIT); mm.EXIT.update(self.kw)

    def __exit__(self, *a):
        mm.EXIT.clear(); mm.EXIT.update(self.old)


def test_live_exit_is_body_first_with_trailing():
    X = mm.EXIT
    assert X["trail"] and X["body_at"] > 0 and abs(X["body_frac"] * (1 + X["body_at"]) - 1) < 1e-9     # the body sale returns the stake
    assert X["pre_h"] == 72 and X["max_h"] == 60 * 24 and "тело" in mm.EXIT_TEXT


def test_body_not_taken_in_3_days_exits_all():
    conn = sqlite3.connect(":memory:")
    base = with_dip(NOW, px=1.4, close=1.0)
    _run(conn, NOW, 1.0, {"WIF": base})
    r = _run(conn, NOW + 72 * H + 60_000, 1.2, {"WIF": base})
    cl = r["account"]["closed"][0]
    assert cl["why"] == "тело не взяли за 72 ч" and cl["exit_px"] == 1.2 and cl["pnl_usd"] > 0


def test_body_first_then_trailing_on_4h_closes():
    with exit_as(stop=-0.25, body_at=1.0, body_frac=0.5, trail=0.35, basis="close", pre_h=None, max_h=1440):
        _body_first_then_trailing()


def _body_first_then_trailing():
    conn = sqlite3.connect(":memory:")
    base = with_dip(NOW, px=1.4, close=1.0)
    _run(conn, NOW, 1.0, {"WIF": base}, funding_h=0.0001)
    h0 = NOW - NOW % H
    after = _bars(h0, [(1.0, 0.99, 1.0), (2.1, 0.98, 2.05), (3.02, 2.9, 3.0), (3.05, 2.95, 3.0),
                       (2.0, 1.88, 1.9), (1.95, 1.88, 1.9), (1.95, 1.88, 1.9), (1.95, 1.88, 1.9)])
    c = [x for x in base if x[0] < h0] + after
    r = _run(conn, h0 + 2 * H + 5 * 60_000, 2.05, {"WIF": [x for x in c if x[0] <= h0 + 2 * H]}, funding_h=0.0001)
    p = r["account"]["positions"][0]
    assert p["body_taken"] and p["tp_done"] and abs(p["qty"] - 500) < 1e-6 and p["remaining_pct"] == 50.0 and p["stop_px"] is None
    assert any(e["text"].startswith("Вернули вложенное по WIF: продали 50% по $2.0000") for e in r["account"]["events"])
    assert p["funding_paid"] > 0
    t3 = h0 + 8 * H + 10 * 60_000
    r = _run(conn, t3, 1.92, {"WIF": c}, funding_h=0.0001)
    acc = r["account"]
    assert acc["positions"] == [] and acc["closed"][0]["why"].startswith("трейлинг 35% от пика $3.0000")
    assert acc["closed"][0]["exit_px"] == 1.92 and any(e["text"].startswith("Трейлинг: продали остаток WIF по $1.9200") for e in acc["events"])
    gross = 500 * 2.0 * (1 - mm.COST / 2) + 500 * 1.92 * (1 - mm.COST / 2) - 1000 * (1 + mm.COST / 2)
    assert gross - 5 < acc["closed"][0]["pnl_usd"] < gross            # minus funding


def test_trailing_on_highs_and_time_limit():
    old = dict(mm.EXIT)
    mm.EXIT.update({"stop": -0.25, "body_at": 1.0, "body_frac": 0.5, "trail": 0.35, "basis": "high", "pre_h": None, "max_h": 10})
    try:
        conn = sqlite3.connect(":memory:")
        base = with_dip(NOW, px=1.4, close=1.0)
        _run(conn, NOW, 1.0, {"WIF": base})
        h0 = NOW - NOW % H
        after = _bars(h0, [(1.0, 0.99, 1.0), (2.1, 0.98, 2.05), (4.0, 2.9, 3.9), (3.0, 2.5, 2.7)])
        c = [x for x in base if x[0] < h0] + after
        r = _run(conn, h0 + 4 * H + 5 * 60_000, 2.7, {"WIF": c})
        cl = r["account"]["closed"][0]
        assert cl["why"].startswith("трейлинг 35% от пика $4.0000") and abs(cl["exit_px"] - 4.0 * 0.65) < 1e-9
        conn2 = sqlite3.connect(":memory:")
        _run(conn2, NOW, 1.0, {"WIF": base})
        r = _run(conn2, NOW + 11 * H, 1.1, {"WIF": base})
        assert r["account"]["closed"][0]["why"] == "время вышло (0 дн.)"
    finally:
        mm.EXIT.clear(); mm.EXIT.update(old)


def test_old_table_is_migrated():
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE meme_pos(coin TEXT PRIMARY KEY, entry_px REAL, qty REAL, qty0 REAL, cost_in REAL, cash_out REAL,
                    entry_time INTEGER, tp_done INTEGER, funding REAL, last_bar INTEGER, why TEXT, tags TEXT)""")
    conn.execute("INSERT INTO meme_pos VALUES('WIF', 1.0, 1000, 1000, 1000.75, 0, ?, 0, 0, ?, 'x', '')", (NOW - H, NOW - 2 * H))
    r = _run(conn, NOW, 1.1, {"WIF": flat_1h(NOW, 1.1)})
    p = r["account"]["positions"][0]
    assert p["coin"] == "WIF" and p["peak_px"] >= 1.0 and p["trail_px"] is None

def test_btc_context():
    daily = [[NOW - (60 - i) * DAY, 1, 1, 1, 100.0 + i, 1] for i in range(61)]
    b = mm.btc_context(lambda c, s, e, iv: daily, NOW)
    assert b["btc_above_ma50"] is True and abs(b["btc_ma50"] - sum(100.0 + i for i in range(10, 60)) / 50) < 1e-9


def test_pending_rule_no_trades_and_no_hl():
    conn = sqlite3.connect(":memory:")
    r = _run(conn, NOW, 0.72, {"WIF": with_dip(NOW)}, rule="pending")
    assert r["account"]["positions"] == [] and r["account"]["equity"] == 10_000
    r = mm.run(conn, NOW + H, hl_ctx_fn=lambda: {}, hl_candles_fn=lambda *a: [], sol_fn=no_sol, btc_fn=dict, spot_fn=no_spot, holders_fn=no_holders)
    assert r["hl_memes"] == [] and r["account"]["equity"] == 10_000 and r["warnings"][0].startswith("Hyperliquid не ответил")
    assert r["sources"]["hyperliquid"] is False


def test_json_shape_and_max_positions():
    conn = sqlite3.connect(":memory:")
    coins = [f"C{i}" for i in range(9)]
    old = mm.RULE, mm.MEME_COINS
    mm.RULE = "dip"
    try:
        r = mm.run(conn, NOW, hl_ctx_fn=lambda: {c: ctx_of(0.74 - 0.01 * int(c[1:])) for c in coins},
                   hl_candles_fn=lambda c, s, e: with_dip(NOW, close=0.74 - 0.01 * int(c[1:])), sol_fn=no_sol, btc_fn=dict, spot_fn=no_spot, holders_fn=no_holders)
    finally:
        mm.RULE = old[0]
    acc = r["account"]
    assert len(acc["positions"]) == mm.MAX_POS
    assert {p["coin"] for p in acc["positions"]} == {f"C{i}" for i in range(3, 9)}      # deepest dips first
    for k in ("asof", "account", "hl_memes", "solana", "warnings", "research_summary", "sources", "score_formula", "solana_note", "market"):
        assert k in r, k
    for k in ("equity", "pnl_usd", "pnl_pct", "positions", "closed", "events", "rules", "rule_status", "history", "limits"):
        assert k in acc, k
    assert any("100%" in w for w in r["warnings"])
    json.dumps(r, ensure_ascii=False)


# ── spot memes (cnq/agents/meme_spot.py) ──────────────────────────────────────────────────────────────────────────────

from cnq.agents import meme_spot as ms


def fake_rpc(largest, owners, owner_programs, supply="1000"):
    def post(url, body):
        m, prm = body["method"], body["params"]
        if m == "getTokenLargestAccounts":
            return {"result": {"value": [{"address": a, "uiAmountString": str(v)} for a, v in largest]}}
        if m == "getTokenSupply":
            return {"result": {"value": {"uiAmountString": supply}}}
        if m == "getMultipleAccounts" and prm[1]["encoding"] == "jsonParsed":
            return {"result": {"value": [{"data": {"parsed": {"info": {"owner": owners[a]}}}} for a in prm[0]]}}
        if m == "getMultipleAccounts":
            return {"result": {"value": [({"owner": owner_programs[o]} if owner_programs.get(o) else None) for o in prm[0]]}}
        raise OSError(m)
    return post


def test_holders_excludes_pools_burn_and_locks():
    largest = [("a1", 300), ("a2", 100), ("a3", 50), ("a4", 40), ("a5", 30)] + [(f"x{i}", 10) for i in range(10)]
    owners = {"a1": "POOLSTATE", "a2": "1nc1nerator11111111111111111111111111111111", "a3": "LOCKER", "a4": "w4", "a5": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"}
    owners.update({f"x{i}": f"w{i}x" for i in range(10)})
    progs = {"POOLSTATE": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "LOCKER": "strmRqUCoQUgGUan5YhzUZa6KqdzwX5L6FpUxfmKg5m",
             "w4": "11111111111111111111111111111111"}
    h = ms.holders("MINT", fake_rpc(largest, owners, progs))
    # kept: w4 40 + ten wallets × 10 → top-10 = 40 + 9 × 10 = 130 of (1000 − 100 burned)
    assert abs(h["top10_pct"] - 130 / 900 * 100) < 0.1 and abs(h["top1_pct"] - 40 / 900 * 100) < 0.1
    whys = {e["why"] for e in h["excluded"]}
    assert whys == {"пул PumpSwap", "сожжено", "заблокировано (Streamflow)", "пул Raydium"}


def _cg_row(i, **kw):
    r = {"id": f"c{i}", "symbol": f"m{i}", "name": f"Meme {i}", "current_price": 1.0, "market_cap": 5e8, "fully_diluted_valuation": 5e8,
         "total_volume": 5e7, "circulating_supply": 1, "total_supply": 1, "max_supply": None, "price_change_percentage_24h": 1.0,
         "price_change_percentage_7d_in_currency": 2.0, "price_change_percentage_30d_in_currency": 3.0, "ath_change_percentage": -80.0}
    r.update(kw)
    return r


def test_spot_run_score_reasons_and_cache():
    calls = {"n": 0}
    markets = [_cg_row(1), _cg_row(2, fully_diluted_valuation=2e9, symbol="wif"), _cg_row(3, market_cap=1e7), _cg_row(4, symbol="usdt")]
    det = {"c1": {"id": "c1", "categories": ["Meme", "DWF Labs Portfolio"], "platforms": {"ethereum": "0xabc"}, "genesis_date": "2023-04-14", "links": {"homepage": ["https://x"]}},
           "c2": {"id": "c2", "categories": ["Meme", "Solana Meme"], "platforms": {"solana": "MINT2"}, "genesis_date": None, "links": {}}}
    raises = {"raises": [{"name": "Meme 1", "date": 1, "round": "Seed", "amount": 2, "leadInvestors": ["Polychain Capital"], "otherInvestors": ["Some Fund"]}]}

    def get(url):
        calls["n"] += 1
        if "coins/markets" in url:
            return markets
        if "/coins/" in url:
            return det[url.split("/coins/")[1].split("?")[0]]
        if "llama" in url:
            return raises
        if "okx" in url:
            return {"data": [{"baseCcy": "M1", "quoteCcy": "USDT"}]}
        if "dexscreener" in url:
            return [{"baseToken": {"address": "MINT2"}, "liquidity": {"usd": 3e6}, "url": "https://dexscreener.com/solana/p2", "pairCreatedAt": NOW - 400 * DAY}]
        if "rugcheck" in url and url.endswith("/report"):
            raise OSError("report unavailable → RPC fallback")
        if "rugcheck" in url:
            return {"score": 1, "lpLockedPct": 100, "risks": []}
        raise OSError(url)
    largest = [(f"a{i}", 10) for i in range(15)]
    post = fake_rpc(largest, {f"a{i}": f"w{i}" for i in range(15)}, {})
    conn = sqlite3.connect(":memory:"); ms.ensure(conn)
    res = ms.run(conn, NOW, get, post, {"kM1"}, deadline=10 ** 12)
    rows = {r["symbol"]: r for r in res["rows"]}
    assert set(rows) == {"M1", "WIF"}                                                  # small cap and stablecoin dropped
    m1, wif = rows["M1"], rows["WIF"]
    assert m1["backers"]["tier1"] == ["DWF", "Polychain"] and m1["score_parts"]["бонус инвесторы"] == 10
    assert m1["backers"]["source"] == "CoinGecko портфели, DefiLlama" and "DWF Labs" in m1["backers"]["investors"]
    assert m1["listed"] == {"okx_spot": True, "hl_perp": True, "solana_liq": None} and m1["holders"] is None
    assert any("держатели: нет данных (не Solana" in x for x in m1["reasons"]) and m1["age_source"] == "CoinGecko genesis"
    assert wif["circ_ratio"] == 0.25 and any(f["level"] == "red" and "в обращении только 25%" in f["text"] for f in wif["flags"])
    assert abs(wif["holders"]["top10_pct"] - 10.0) < 0.01                             # 10 wallets × 10 of supply 1000
    assert wif["age_days"] == 400 and wif["lp_locked_pct"] == 100 and wif["backers"]["checked"] and wif["backers"]["raises"] == []
    assert any(x.startswith("инвесторы: нет данных (в CoinGecko нет портфелей") for x in wif["reasons"]) and wif["score_parts"]["бонус инвесторы"] == 0
    assert 0 < wif["coverage"] <= 1 and m1["coverage"] < 1 and 0 <= wif["score"] <= 100
    assert res["sources"]["coingecko"] and res["sources"]["defillama"] and res["sources"]["okx"]
    n1 = calls["n"]
    ms.run(conn, NOW + 60_000, get, post, set(), deadline=10 ** 12)                  # everything cached → no new calls except DexScreener
    assert calls["n"] - n1 <= 1
    dead = ms.run(sqlite3.connect(":memory:"), NOW, lambda u: (_ for _ in ()).throw(OSError()), post, set(), deadline=10 ** 12)
    assert dead["rows"] == [] and "CoinGecko не ответил" in dead["note"]


def test_solana_holder_flags():
    r = _sol_row(holders={"top10_pct": 72.0}, ds_mcap=1e6, ds_fdv=4e6)
    texts = [(f["level"], f["text"]) for f in mm.sol_flags(r)]
    assert ("red", "топ-10 кошельков (без пулов) держат 72%") in texts
    assert any(lv == "yellow" and "в обращении 25%" in t for lv, t in texts)


def test_holders_from_rugcheck_report():
    rep = {"totalHolders": 813444,
           "topHolders": [{"address": "t1", "owner": "POOLOWNER", "pct": 20.0}, {"address": "t2", "owner": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM", "pct": 9.7},
                          {"address": "t3", "owner": "w3", "pct": 5.0, "insider": True}, {"address": "t4", "owner": "w4", "pct": 3.0},
                          {"address": "t5", "owner": "1nc1nerator11111111111111111111111111111111", "pct": 4.0}]
                         + [{"address": f"x{i}", "owner": f"o{i}", "pct": 1.0} for i in range(12)],
           "knownAccounts": {"POOLOWNER": {"name": "Raydium AMM", "type": "AMM"},
                             "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": {"name": "Binance 2", "type": "CEX"}}}
    h = ms.holders_rugcheck(rep)
    assert h["top10_pct"] == 5.0 + 3.0 + 8 * 1.0 and h["top1_pct"] == 5.0 and h["insiders_pct"] == 5.0 and h["holders_total"] == 813444
    assert {e["why"] for e in h["excluded"]} == {"пул Raydium AMM", "биржа Binance 2", "сожжено"} and h["source"] == "RugCheck"
    got = ms.holders_any("M", lambda url: rep, lambda u, b: (_ for _ in ()).throw(OSError()))
    assert got["source"] == "RugCheck"
    assert ms.holders_any("M", lambda url: {}, lambda u, b: (_ for _ in ()).throw(OSError())) is None
