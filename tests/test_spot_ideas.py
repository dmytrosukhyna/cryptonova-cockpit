"""Spot ideas table (web/ideas.json): synthetic inputs only, no network."""
import json
import os

from cnq.agents import spot_ideas as si

NOW = 1_790_000_000_000


def card(sym, risk="mod", rating=45, chg7=-3.0, chg30=10.0, chg200=50.0, ath=-60.0, fdv=1.0, price=10.0, tags=None, cid=None):
    return {"symbol": sym, "name": sym.title(), "id": cid or sym.lower(), "price": price, "chg24": -1.0, "chg7": chg7, "chg30": chg30,
            "chg200": chg200, "ath_chg": ath, "risk": risk, "rating": rating, "fdv_mcap": fdv, "tags": tags or [], "stable": False}


def pick(sym, score, price=10.0):
    return {"symbol": sym, "name": sym.title(), "price": price, "score": score, "why": "test", "chg7": -3.0, "chg30": 10.0}


def pos(sym, score=None, pnl=1.0, reco=None, price=10.0, role="mid"):
    p = {"symbol": sym, "bucket": role, "role": role, "weight": 0.06, "entry": price, "price": price, "pnl_pct": pnl,
         "score": score or {"short": 50, "mid": 70, "long": 70}}
    if reco:
        p["reco"] = reco
    return p


def meme(sym, status, hi30, score=60, why=None):
    return {"symbol": sym, "name": sym.title(), "price": 0.5, "score": score, "status": status, "from_high30_pct": hi30,
            "why_ru": why or {"small": f"просадка {hi30:.0f}% от максимума за 30 дней, проверки пройдены",
                              "wait": f"{hi30:.0f}% от максимума за 30 дней — ждём просадку от −30%",
                              "avoid": "красный флаг: топ-10 кошельков держат 70%"}[status],
            "chg7d": -10.0, "chg30d": -40.0, "vol24": 3e7, "link": f"https://www.coingecko.com/en/coins/{sym.lower()}"}


def inputs(btc_bull=True, cards=(), picks=None, portfolios=None, memes=(), vols=None, decision=None):
    vols = vols or {}
    cards = list(cards)
    syms = {c["symbol"] for c in cards} | {m["symbol"] for m in memes}
    return {
        "research": {"asof": NOW, "cards": cards},
        "portfolio": {"asof": NOW, "btc_bull": btc_bull, "picks": picks or {"short": [], "mid": [], "long": []},
                      "portfolio_order": list((portfolios or {}).keys()),
                      "portfolios": {k: {"name": k.upper(), "positions": v} for k, v in (portfolios or {}).items()}},
        "memes": {"asof": NOW, "spot_memes": list(memes)},
        "markets": {"asof": NOW, "btc_bull": btc_bull, "top": [{"symbol": s, "volume": vols.get(s, 1e8)} for s in syms]},
        "decision": decision,
    }


def rows_by_sym(d):
    return {r["symbol"]: r for r in d["rows"]}


def base_cards():
    return [card("BTC", "cons", 55, price=80000), card("ETH", "cons", 52, price=2400),
            card("ALTA", "mod", 48), card("ALTB", "mod", 44),
            card("HIGHD", "high", 40, chg7=-20.0, chg30=-38.0),          # 30-day high ≥ price / 0.62 → ≈ −38 %
            card("HIGHN", "high", 40, chg7=-2.0, chg30=5.0),
            card("DEAD", "high", 45, ath=-96.0)]


def base_picks():
    return {"short": [], "mid": [pick("ALTA", 90), pick("ALTB", 70)], "long": [pick("ALTA", 80)]}


def test_ordering_and_shape(tmp_path):
    inp = inputs(cards=base_cards(), picks=base_picks(),
                 memes=[meme("MDIP", "small", -45.0), meme("MWAIT", "wait", -12.0), meme("MBAD", "avoid", -20.0)])
    d = si.build(inp, NOW)
    order = [si.STATUS_ORDER.index(r["status"]) for r in d["rows"]]
    assert order == sorted(order)
    for st in si.STATUS_ORDER:
        sc = [r["score"] for r in d["rows"] if r["status"] == st]
        assert sc == sorted(sc, reverse=True)
    R = rows_by_sym(d)
    assert R["BTC"]["status"] == "buy" and R["ALTA"]["status"] == "buy" and R["ALTB"]["status"] == "buy"
    assert R["MDIP"]["status"] == "small" and R["MWAIT"]["status"] == "wait" and R["MBAD"]["status"] == "avoid"
    assert R["DEAD"]["status"] == "avoid"
    assert d["summary_ru"].startswith("Сейчас можно брать: ") and "на небольшую сумму: " in d["summary_ru"] and "остальные ждём" in d["summary_ru"]
    assert d["counts"]["buy"] == sum(1 for r in d["rows"] if r["status"] == "buy")
    # file shape
    os.makedirs(tmp_path / "web", exist_ok=True)
    for k, fn in (("research", "research.json"), ("portfolio", "portfolio.json"), ("memes", "memes.json"), ("markets", "markets.json")):
        (tmp_path / "web" / fn).write_text(json.dumps(inp[k], ensure_ascii=False), encoding="utf-8")
    out = si.write(str(tmp_path / "web"))
    disk = json.loads((tmp_path / "web" / "ideas.json").read_text(encoding="utf-8"))
    assert disk["summary_ru"] == out["summary_ru"]
    for k in ("asof", "btc_bull", "summary_ru", "rules_ru", "counts", "rows"):
        assert k in disk
    assert isinstance(disk["rules_ru"], list) and len(disk["rules_ru"]) >= 4
    for r in disk["rows"]:
        for k in ("symbol", "name", "kind_ru", "status", "status_ru", "score", "why_ru", "info_ru", "in_portfolios", "price", "chg7",
                  "from_high30_pct"):
            assert k in r, k
        assert r["status_ru"] == si.STATUS_RU[r["status"]]
        assert r["kind_ru"] in ("ядро", "крупный альт", "высокий риск", "мем")
        assert 0 <= r["score"] <= 100
        assert si._words(r["why_ru"]) <= 14, r["why_ru"]
        n_sent = r["info_ru"].count(". ") + 1
        assert 2 <= n_sent <= 5, r["info_ru"]
        assert "не прогноз цены" in r["info_ru"]
    assert disk["rows"][0]["links"]["coingecko"].startswith("https://www.coingecko.com/")


def test_bear_regime_only_core_can_be_buy():
    d = si.build(inputs(btc_bull=False, cards=base_cards(), picks=base_picks(), memes=[meme("MDIP", "small", -45.0)]), NOW)
    R = rows_by_sym(d)
    buys = [r["symbol"] for r in d["rows"] if r["status"] == "buy"]
    assert set(buys) == {"BTC", "ETH"}
    assert R["ALTA"]["status"] == "wait" and "медвежьем" in R["ALTA"]["why_ru"]
    assert R["HIGHD"]["status"] == "wait"                    # high-risk dip needs a bull regime
    assert R["MDIP"]["status"] == "small"                    # meme desk dip rule is regime-independent (research: flush buying)
    assert d["btc_bull"] is False
    # unknown regime is treated as bear
    d2 = si.build(inputs(btc_bull=None, cards=base_cards(), picks=base_picks()), NOW)
    assert {r["symbol"] for r in d2["rows"] if r["status"] == "buy"} == {"BTC", "ETH"}


def test_meme_and_high_risk_dip_rule():
    d = si.build(inputs(cards=base_cards(), picks=base_picks(),
                        memes=[meme("MDIP", "small", -45.0), meme("MWAIT", "wait", -12.0)]), NOW)
    R = rows_by_sym(d)
    assert R["MDIP"]["status"] == "small" and R["MDIP"]["kind_ru"] == "мем" and R["MDIP"]["from_high30_pct"] == -45.0
    assert "docs/research/memes.md" in R["MDIP"]["info_ru"] and "не жалко" in R["MDIP"]["info_ru"]
    assert R["MWAIT"]["status"] == "wait"
    assert R["HIGHD"]["status"] == "small" and R["HIGHD"]["from_high30_pct"] <= -30
    assert R["HIGHN"]["status"] == "wait" and "просадки" in R["HIGHN"]["why_ru"]
    # high risk and memes are never «buy», even with great scores
    d2 = si.build(inputs(cards=[card("HOT", "high", 55, chg7=-1.0, chg30=2.0)], picks={"mid": [pick("HOT", 99)], "long": []}), NOW)
    assert rows_by_sym(d2)["HOT"]["status"] != "buy"
    # overheated large alt → wait, not buy
    d3 = si.build(inputs(cards=[card("PUMPY", "mod", 50, chg7=60.0, chg30=150.0)], picks={"mid": [pick("PUMPY", 95)]}), NOW)
    r = rows_by_sym(d3)["PUMPY"]
    assert r["status"] == "wait" and "+60%" in r["why_ru"]


def test_dedup_and_portfolio_state():
    cards = [card("BTC", "cons", 55, price=80000), card("ETH", "cons", 52, price=2400), card("ALTA", "mod", 48), card("ALTS", "mod", 48)]
    pf = {"mod": [pos("ALTA"), pos("ALTS", pnl=-13.0)],
          "bal": [pos("ALTA", reco={"k": "hold", "t": "Держать", "why": "x"}), pos("ETH", role="core", price=2400)]}
    dec = {"asof": NOW, "coins": [{"symbol": "ETH-USDT", "price": 2400, "regime": {"hh20": 2500},
                                   "levels": [{"kind": "support", "status": "ACTIVE", "state": "IN_ZONE", "lo": 2390, "hi": 2410,
                                               "dist_pct": 0.0, "score": 90}]}]}
    inp = inputs(cards=cards + [card("ALTA", "mod", 48)], picks={"mid": [pick("ALTA", 90), pick("ALTA", 85)], "long": [pick("ALTA", 80)]},
                 portfolios=pf, decision=dec)
    d = si.build(inp, NOW)
    syms = [r["symbol"] for r in d["rows"]]
    assert len(syms) == len(set(syms))
    R = rows_by_sym(d)
    assert R["ALTA"]["in_portfolios"] == ["mod", "bal"]
    assert R["ALTA"]["consultant"]["mid"] == 90
    assert R["ETH"]["status"] == "add" and "поддержки" in R["ETH"]["why_ru"]          # held + price in a support zone
    assert R["ALTS"]["status"] == "avoid" and "стопа" in R["ALTS"]["why_ru"]          # «Умеренный» −13 % ≤ −12 % → at stop
    assert R["ETH"]["from_high30_pct"] <= -4.0                                         # 20-day high 2500 used as a lower bound
    # a symbol that is both a research card and a meme-desk row is one row, kind «мем»
    d2 = si.build(inputs(cards=[card("DOGE", "high", 35, tags=["мем"])], memes=[meme("DOGE", "wait", -20.0)]), NOW)
    assert [r["symbol"] for r in d2["rows"]] == ["DOGE"] and d2["rows"][0]["kind_ru"] == "мем"


def test_info_contains_input_numbers_and_stables_excluded():
    cards = [card("ALTA", "mod", 47, chg7=-9.1, chg30=12.3, chg200=88.0, ath=-71.0),
             card("USDX", "mod", 39, chg7=0.1, chg30=0.2, ath=-1.0),                  # peg-like → not in the table
             card("HIGHD", "high", 41, chg7=-20.0, chg30=-38.0)]
    d = si.build(inputs(cards=cards, picks={"mid": [pick("ALTA", 93)], "long": [pick("ALTA", 81)]},
                        memes=[meme("MDIP", "small", -49.0, score=56)]), NOW)
    R = rows_by_sym(d)
    assert "USDX" not in R
    a = R["ALTA"]["info_ru"]
    for s in ("47/100", "93/100", "81/100", "−9.1%", "+12.3%", "+88%"):
        assert s in a, (s, a)
    assert "−49%" in R["MDIP"]["info_ru"] and "56/100" in R["MDIP"]["info_ru"]
    assert "41/100" in R["HIGHD"]["info_ru"] and "−38%" in R["HIGHD"]["info_ru"]
    assert R["ALTA"]["score"] == round((47 * 0.5 + 93 * 0.3) / 0.8)


def test_top30_keeps_room_for_avoid():
    cards = [card(f"W{i:02d}", "mod", 45, chg200=-5.0) for i in range(40)]          # 40 × «ждём» (below their 200-day level)
    cards += [card(f"D{i}", "high", 30, ath=-97.0) for i in range(10)]               # 10 × «не брать»
    d = si.build(inputs(cards=cards, picks={"mid": [pick("D1", 70)], "long": []}), NOW)
    assert len(d["rows"]) == si.MAX_ROWS
    av = [r["symbol"] for r in d["rows"] if r["status"] == "avoid"]
    assert len(av) == si.AVOID_SLOTS and "D1" in av                                   # picked coin goes first among «не брать»
    assert d["counts_all"]["avoid"] == 10 and d["evaluated"] == 50


def test_follower_portfolio_does_not_feed_back_and_statuses_cover_every_coin():
    """«По рекомендациям» (portfolio key `ideas`) buys FROM the table: its holdings — even at a stop with reco «sell» — must not turn a
    coin into «не брать» / «добрать» or count as «held»; a coin only it holds stays evaluated; `statuses` covers every evaluated coin."""
    cards = base_cards()                                     # ONLYF has no card: only the follower holds it
    fol = [pos("ALTA", pnl=-30.0, reco={"k": "sell", "t": "У стопа −20%", "why": "x"}), pos("ALTB", reco={"k": "add", "t": "x", "why": "x"}),
           pos("ONLYF")]
    base = si.build(inputs(cards=cards, picks=base_picks()), NOW)
    d = si.build(inputs(cards=cards, picks=base_picks(), portfolios={"ideas": fol}), NOW)
    R0, R = rows_by_sym(base), rows_by_sym(d)
    for s in ("ALTA", "ALTB", "BTC"):
        assert R[s]["status"] == R0[s]["status"] and R[s]["in_portfolios"] == [] and R[s]["why_ru"] == R0[s]["why_ru"]
    assert R["ALTA"]["status"] == "buy"
    assert d["evaluated"] == len(d["statuses"]) and set(d["statuses"]) >= {r["symbol"] for r in d["rows"]} | {"ONLYF"}
    st = d["statuses"]["ALTA"]
    assert st["status"] == "buy" and st["status_ru"] == "можно брать" and st["kind"] == "mid" and st["price"] == 10.0 and st["why_ru"]
    assert d["statuses"]["ONLYF"]["status"] == "wait" and "ONLYF" not in base["statuses"]


def test_holders_revenue_is_a_fact_and_a_small_score_bonus_never_a_gate():
    """18.10: tokens whose protocol pays holders (DefiLlama holders revenue) rank higher; no data → no bonus, status unchanged."""
    cs = [card("ALTA", "mod", 48), card("ALTB", "mod", 48)]
    for c in cs:
        c["mcap"] = 1e9
    inp = inputs(cards=cs, picks={"short": [], "mid": [pick("ALTA", 70), pick("ALTB", 70)], "long": []})
    d0 = rows_by_sym(si.build(inp, NOW))
    inp["tokenomics"] = {"tokens": {"ALTA": {"name": "Alta", "holders_rev_30d_usd": 5_000_000, "holders_rev_1y_usd": 60_000_000}}}
    d1 = rows_by_sym(si.build(inp, NOW))
    assert d1["ALTA"]["score"] == min(100, d0["ALTA"]["score"] + 10) and d1["ALTB"]["score"] == d0["ALTB"]["score"]   # 6 %/yr → capped +10
    assert d1["ALTA"]["status"] == d0["ALTA"]["status"] and d1["ALTB"]["status"] == d0["ALTB"]["status"]
    assert any(c["key"] == "holders" and "$5" in c["t"] for c in d1["ALTA"]["checks"])
    assert any(c["key"] == "holders" and "не получают" in c["t"] for c in d1["ALTB"]["checks"])


def test_tokenomics_joins_holders_revenue_to_symbols():
    from cnq.agents import tokenomics as T
    ov = {"protocols": [{"name": "Hyperliquid", "slug": "hyperliquid", "total30d": 7e7, "total1y": 8e8},
                        {"name": "Ondo", "slug": "ondo-finance", "total30d": 0, "total1y": 0},
                        {"name": "Uniswap V3", "slug": "uniswap-v3", "parentProtocol": "parent#uniswap", "total30d": 2e6, "total1y": 2e7}]}
    pr = [{"name": "Hyperliquid", "slug": "hyperliquid", "symbol": "HYPE"}, {"name": "Ondo Finance", "slug": "ondo-finance", "symbol": "ONDO"},
          {"name": "Uniswap", "slug": "uniswap", "symbol": "UNI", "parentProtocol": "parent#uniswap"}]
    t = T.build(ov, pr)
    assert t["HYPE"]["holders_rev_30d_usd"] == 70_000_000 and "ONDO" not in t and t["UNI"]["holders_rev_1y_usd"] == 20_000_000


def test_realised_dilution_is_measured_from_our_own_snapshots_and_lowers_the_score():
    import sqlite3
    from cnq.agents import tokenomics as T
    conn = sqlite3.connect(":memory:")
    day = 86_400_000
    t0 = 1_790_000_000_000
    T.supply_growth(conn, {"top": [{"symbol": "UNLK", "mcap": 1e9, "price": 1.0}, {"symbol": "FLAT", "mcap": 1e9, "price": 1.0}]}, t0)
    assert T.supply_growth(conn, {"top": [{"symbol": "UNLK", "mcap": 1e9, "price": 1.0}]}, t0 + 10 * day) == {}      # too early
    g = T.supply_growth(conn, {"top": [{"symbol": "UNLK", "mcap": 1.1e9, "price": 1.0}, {"symbol": "FLAT", "mcap": 1.2e9, "price": 1.2}]}, t0 + 30 * day)
    assert g == {"UNLK": 10.0, "FLAT": 0.0}
    cs = [card("UNLK", "mod", 48), card("FLAT", "mod", 48)]
    inp = inputs(cards=cs, picks={"short": [], "mid": [pick("UNLK", 70), pick("FLAT", 70)], "long": []})
    d0 = rows_by_sym(si.build(inp, NOW))
    inp["tokenomics"] = {"tokens": {"UNLK": {"supply_30d_pct": 10.0}, "FLAT": {"supply_30d_pct": 0.0}}}
    d1 = rows_by_sym(si.build(inp, NOW))
    assert d1["UNLK"]["score"] == max(0, d0["UNLK"]["score"] - 10) and d1["FLAT"]["score"] == d0["FLAT"]["score"]
    assert any(c["key"] == "supply" and "10.0%" in c["t"] for c in d1["UNLK"]["checks"]) and d1["UNLK"]["status"] == d0["UNLK"]["status"]


def test_big_unlock_ahead_means_wait_small_unlock_is_only_a_fact():
    """18.10 (scripts/unlocks_study.py): an unlock of ≥ 20 % of the circulating supply within 30 days → «ждём» for a non-core
    coin; 5–20 % → a fact line, status unchanged; the core is never gated."""
    cs = [card("ALTA", "mod", 48), card("ALTB", "mod", 48), card("BTC", "cons", 55, price=80000)]
    inp = inputs(cards=cs, picks={"short": [], "mid": [pick("ALTA", 70), pick("ALTB", 70)], "long": []})
    d0 = rows_by_sym(si.build(inp, NOW))
    assert d0["ALTA"]["status"] == "buy" and d0["ALTB"]["status"] == "buy"
    inp["tokenomics"] = {"tokens": {}, "unlocks_next": {
        "ALTA": {"date": "2026-11-01", "days": 12, "pct_circ": 25.0, "event_class": "one_off_cliff"},
        "ALTB": {"date": "2026-11-01", "days": 12, "pct_circ": 8.0},
        "BTC": {"date": "2026-11-01", "days": 12, "pct_circ": 50.0}}}
    d1 = rows_by_sym(si.build(inp, NOW))
    a, b = d1["ALTA"], d1["ALTB"]
    assert a["status"] == "wait" and "разлок 25%" in a["why_ru"] and any(c["key"] == "unlock" and c["ok"] is False for c in a["checks"])
    assert b["status"] == "buy" and any(c["key"] == "unlock" and c["ok"] is None and "8%" in c["t"] for c in b["checks"])
    assert d1["BTC"]["status"] == d0["BTC"]["status"] and not any(c["key"] == "unlock" for c in d1["BTC"]["checks"])


def test_unlock_calendar_window_and_forward_watch(tmp_path):
    import sqlite3
    from cnq.agents import tokenomics as T
    day = 86_400_000
    t0 = T._day_ms("2026-10-18") + 3_600_000
    cal = {"built": "2026-10-18", "events": [
        {"symbol": "monx", "date": "2026-11-10", "pct_circ": 40.0, "event_class": "one_off_cliff", "source": "https://x"},
        {"symbol": "MONX", "date": "2026-10-25", "pct_circ": 2.0},
        {"symbol": "FAR", "date": "2026-12-31", "pct_circ": 90.0},
        {"symbol": "PAST", "date": "2026-10-10", "pct_circ": 30.0},
        {"symbol": "BAD", "date": "soon", "pct_circ": 30.0}]}
    nx = T.upcoming(cal, t0)
    assert set(nx) == {"MONX"} and nx["MONX"]["pct_circ"] == 40.0 and nx["MONX"]["days"] == 23
    conn = sqlite3.connect(":memory:")
    mk = lambda p, b: {"top": [{"symbol": "MONX", "price": p}, {"symbol": "BTC", "price": b}]}   # noqa: E731
    w = T.unlock_watch(conn, mk(1.0, 100.0), cal, t0)
    assert w["resolved"] == 0 and w["open"][0]["symbol"] == "MONX" and w["open"][0]["days_before_seen"] == 23
    T.unlock_watch(conn, mk(2.0, 100.0), cal, t0 + 5 * day)               # first sight is kept
    w = T.unlock_watch(conn, mk(0.8, 110.0), cal, t0 + 23 * day)          # the unlock day: coin −20 %, BTC +10 %
    assert w["resolved"] == 1 and w["last"][0]["vs_btc_pct"] == -30.0 and w["lagged_share_pct"] == 100
    p = tmp_path / "cal.json"
    p.write_text(json.dumps(cal))
    (tmp_path / "markets.json").write_text(json.dumps(mk(1.0, 100.0)))
    d = T.run(str(tmp_path), fetch=lambda u: (_ for _ in ()).throw(OSError("offline")), conn=sqlite3.connect(":memory:"),
              now=t0, calendar=str(p))
    assert d["unlocks_next"]["MONX"]["days"] == 23 and d["calendar_built"] == "2026-10-18" and d["unlock_watch"]["open"]
    assert json.load(open(tmp_path / "tokenomics.json"))["unlocks_next"]["MONX"]["pct_circ"] == 40.0
