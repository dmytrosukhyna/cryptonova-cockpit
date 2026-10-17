import datetime as dt, json, math, os, random, sqlite3, tempfile

from cnq.agents import broadcast as bc
from cnq.agents import outlook as ol

DAY = 86_400_000
UTC = dt.timezone.utc


def _ms(*a):
    return int(dt.datetime(*a, tzinfo=UTC).timestamp() * 1000)


NOW = _ms(2026, 10, 16, 15, 0)          # Friday 18:00 Kyiv


def _coin(sym, price, seed, now=NOW):
    rng = random.Random(seed)
    n = 700; t0 = (now // 900_000) * 900_000 - (n - 1) * 900_000
    px = price; cs = []
    for i in range(n):
        o = px; px = px * math.exp(rng.gauss(0, 0.004)); cs.append([t0 + i * 900_000, o, max(o, px) * 1.001, min(o, px) * 0.999, px, 10.0])
    d0 = (now // DAY) - 39
    c1d = [[(d0 + i) * DAY, 1, 1, 1, price * (1 + 0.01 * math.sin(i + seed)), 1] for i in range(40)]
    p = cs[-1][4]
    return {"symbol": f"{sym}-USDT", "price": p, "chg24": p / cs[-97][4] - 1, "chg7": p / cs[-96 * 7 - 1][4] - 1, "candles": cs, "candles_1d": c1d,
            "levels": [{"kind": "support", "lo": p * 0.97, "hi": p * 0.98, "center": p * 0.975, "touches": 3, "status": "ACTIVE", "dist_pct": -2.5},
                       {"kind": "resistance", "lo": p * 1.03, "hi": p * 1.04, "center": p * 1.035, "touches": 2, "status": "ACTIVE", "dist_pct": 3.5}],
            "regime": {"long": "BULL", "mid": "UP_NO_BREAKOUT", "mid_note": "тренд вверх, но без обновления максимума 20 дней — ждать",
                       "sma200": p * 0.9, "hh20": p * 1.06, "ll20": p * 0.94}}


def _terminal():
    coins = [_coin(s, p, i) for i, (s, p) in enumerate((("BTC", 81000.0), ("ETH", 2400.0), ("SOL", 104.0), ("ADA", 0.6), ("XRP", 1.3), ("DOGE", 0.08)))]
    return {"asof": NOW - 900_000, "coins": coins, "portfolio": {"btc_bull": True}}


def _full_ctx():
    term = _terminal()
    rng = random.Random(5); px = 20000.0; hist = {}
    for i in range(1500):
        px *= math.exp(rng.gauss(0.0004, 0.03)); hist[18600 + i] = px
    outlook = ol.build({"terminal": term}, {"BTC": hist}, NOW)
    news = [{"news_id": "a", "asset": "MARKET", "category": "macro", "direction": "bear", "horizon": "hours", "magnitude": 4, "confidence": 0.7,
             "created": NOW - 2 * DAY, "title": "Рост нефти толкает крипту вниз", "reasoning": "x"},
            {"news_id": "b", "asset": "BTC", "category": "etf_flows", "direction": "bull", "horizon": "days", "magnitude": 5, "confidence": 0.8,
             "created": NOW - 3 * DAY, "title": "Big inflows into spot bitcoin ETFs continue for a third week", "reasoning": "x"},
            {"news_id": "c", "asset": "ETH", "category": "tech", "direction": "bull", "horizon": "days", "magnitude": 3, "confidence": 0.6,
             "created": NOW - 1 * DAY, "title": "Обновление сети Ethereum прошло успешно", "reasoning": "x"},
            {"news_id": "d", "asset": "MARKET", "category": "other", "direction": "bull", "horizon": "days", "magnitude": 5, "confidence": 0.2,
             "created": NOW - 1 * DAY, "title": "Слух: крупный фонд покупает", "reasoning": "x"}]
    prof = lambda name, eq, trades, open_=(): {"name": name, "equity": eq, "open_pnl_usd": 0, "start_equity": 10000, "open": list(open_),  # noqa: E731
                                                "stats": {"trades": trades}, "equity_history": [{"time": NOW - DAY, "equity": 10000}]}
    paper = {"reset": {"time": NOW - DAY, "archived_pnl_usd": -298.84, "archived_trades": 3, "note_ru": "Счёт обнулён 17.10"},
             "profiles": {"cons": prof("Автопилот · осторожно", 10000, 0), "bal": prof("Автопилот · норма", 10000, 0), "aggr": prof("Автопилот · агрессивно", 10000, 0),
                          "lab": prof("Тест новых идей", 9886.93, 4, [{"symbol": "SAND", "side": "LONG", "grade": "X", "kind": "ALT_PANIC", "entry": 0.059,
                                                                         "price": 0.06, "open_pnl_usd": 10.4}])},
             "scan": {"breadth": 0.17, "need_breadth": 0.4, "d_need_basis": -0.002, "b_need_sigma": -3.5, "f_need_oi": -0.12, "f_need_px": -0.05,
                      "rows": [{"coin": "BTC", "b_only": False, "b_sigma": -0.2, "px4": 0.001, "oi4": 0.01, "ret_last": -0.0004, "basis": -0.0004},
                               {"coin": "SAND", "b_only": True, "b_sigma": 0.1, "px4": 0.01, "oi4": 0.02, "ret_last": 0.001, "basis": -0.0024}]}}
    return {"terminal": term, "outlook": outlook,
            "markets": {"global": {"total_mcap": 2.75e12, "mcap_chg24": -2.26, "btc_dom": 59.4}, "fng": {"value": 55, "label": "жадность", "week": 61}},
            "decision": {"news": {"impacts": news}},
            "macro": {"items": [{"key": k, "name_ru": k, "value": v, "chg_1d": 1.0, "chg_5d": c, "chg_unit": u, "asof": "2026-10-16", "signal_ru": ""}
                                for k, v, c, u in (("NDX_FUT", 29391.5, -5.52, "%"), ("SPX_FUT", 7704.25, -1.98, "%"), ("VIX", 19.99, 34.7, "%"),
                                                   ("DXY", 101.78, -0.42, "%"), ("US10Y", 5.098, -14.6, "бп"), ("WTI", 99.02, 7.81, "%"))],
                      "notes": ["Акции США и биткоин в один и тот же день чаще всего движутся вместе. Второе.", "Б.", "Доллар и нефть: связь слабая. Ещё."]},
            "calendar": {"events": [{"time": NOW + 2 * DAY, "name": "Инфляция в США (CPI)", "importance": 2, "what": "В первый час колебания сильнее. Ещё."},
                                    {"time": NOW + 12 * DAY, "name": "ФРС: решение по ставке", "importance": 3, "what": "Направление не угадать."}],
                         "verdict": "Проверено на 2023–2026: направления цены ни одно плановое событие не предсказывает. Ещё."},
            "paper": paper,
            "portfolio": {"portfolios": {"cons": {"name": "Консервативный", "index": 100.11, "positions": [{"status": "OPEN"}], "history": [{"time": NOW - DAY, "v": 100}],
                                                  "reset": {"archived_pnl_usd": 86.24}},
                                         "ideas": {"name": "AI-портфель · по рекомендациям", "experiment": True, "equity_usd": 10003.61, "index": 100.04, "positions": []},
                                         "meme": {"name": "Мем-портфель", "index": 100}}},
            "ideas": {"available": True, "btc_bull": True, "regime_ru": "BTC в бычьем режиме", "counts": {"buy": 2, "add": 0, "small": 1, "wait": 3, "avoid": 1},
                      "disclaimer_ru": "Правила отбора по данным панели, а не прогноз цены.",
                      "rows": [{"symbol": "BTC", "status": "buy", "why_ru": "ядро, анкета 60"}, {"symbol": "ETH", "status": "buy", "why_ru": "ядро"},
                               {"symbol": "PENGU", "status": "small"}, {"symbol": "FLOKI", "status": "avoid"}]},
            "memes": {"account": {"equity": 10000.0, "pnl_usd": 0.0, "positions": [], "stats": {"trades": 0}, "rule_status": "эксперимент"},
                      "hl_memes": [{"coin": "PUMP", "signal": "ждём", "dip_dd_pct": -7.27, "score": 62}], "spot_status_counts": {"small": 4, "wait": 7, "avoid": 14},
                      "research_summary": {"verdict": "надёжного правила нет"}},
            "arb": {"since": NOW - DAY, "week": {"windows": 9, "virtual_pnl_usd": 23.78, "observations": 281, "stuck_observations": 272}},
            "tri": {"week": {"windows": 5, "virtual_pnl_usd": 3.47, "samples": 28}, "scanned_total": 1407},
            "funding": {"equity": 9989.85, "start_equity": 10000, "pnl_usd": -10.15, "positions": [{}], "history": [[NOW - DAY, 10000]],
                        "breakdown": {"funding_usd": 2.35, "fees_usd": 12.5, "breakeven_days": 11.2}},
            "conn": None}


KEYS = {"id", "title", "show", "bullets", "say_ru", "facts", "tip_ru"}


def test_broadcast_slides_schema_and_show_ids():
    ctx = _full_ctx()
    slides = bc.build_slides(ctx, NOW)
    assert not ctx.get("_errors"), ctx.get("_errors")
    assert 12 <= len(slides) <= 14
    assert len({s["id"] for s in slides}) == len(slides)
    for s in slides:
        assert set(s) == KEYS and s["show"] in bc.SHOW_IDS and s["title"] and s["tip_ru"]
        assert 1 <= len(s["bullets"]) <= 5 and all(isinstance(b, str) and b for b in s["bullets"])
        assert s["say_ru"] and "None" not in s["say_ru"] and "nan" not in s["say_ru"] and isinstance(s["facts"], dict)
    ids = [s["id"] for s in slides]
    assert ids[0] == "week" and ids[-1] == "plan" and "forecast" in ids
    news = next(s for s in slides if s["id"] == "news")
    titles = [n["title"] for n in news["facts"]["news"]]
    assert "Рост нефти толкает крипту вниз" in titles and not any("ETF" in t for t in titles) and not any("Слух" in t for t in titles)
    strat = next(s for s in slides if s["id"] == "strategies")
    assert "минус 299 долларов" in strat["say_ru"] and any("Тест новых идей −$113" in b for b in strat["bullets"])
    now = next(s for s in slides if s["id"] == "now")
    assert now["show"] == "apRadar" and now["facts"]["radar_top"][0]["coin"] == "SAND"   # basis −0.24 % of 0.25 % needed
    cal = next(s for s in slides if s["id"] == "calendar")
    assert [e["name"] for e in cal["facts"]["events"]] == ["Инфляция в США (CPI)"]          # only the next 7 days
    plan = next(s for s in slides if s["id"] == "plan")
    assert "Не финансовая рекомендация" in " ".join(plan["bullets"]) and "демо" in plan["say_ru"]
    assert 8 <= bc.duration_min(slides) <= 16


def test_broadcast_skips_slides_without_data():
    assert bc.build_slides({}, NOW) == []
    only_term = bc.build_slides({"terminal": _terminal()}, NOW)
    ids = {s["id"] for s in only_term}
    assert ids <= {"week", "breadth", "rsi", "btc", "plan"} and {"week", "btc"} <= ids
    for missing in ("news", "macro", "calendar", "forecast", "strategies", "now", "ideas", "memes", "earn"):
        assert missing not in ids
    ctx = _full_ctx(); ctx["ideas"] = {"available": False}; ctx["outlook"] = None; ctx["memes"] = None
    ids2 = {s["id"] for s in bc.build_slides(ctx, NOW)}
    assert "ideas" not in ids2 and "forecast" not in ids2 and "memes" not in ids2 and "strategies" in ids2


def test_broadcast_friday_regeneration_in_kyiv_time():
    d = tempfile.mkdtemp(); p = os.path.join(d, "broadcast.json")
    assert bc.should_regenerate(p, NOW) == "missing"
    json.dump({"generated": _ms(2026, 10, 12, 10, 0), "slides": [{"id": "week"}]}, open(p, "w"))
    assert bc.should_regenerate(p, _ms(2026, 10, 15, 20, 30)) is None          # Thu 23:30 Kyiv (UTC+3)
    assert bc.should_regenerate(p, _ms(2026, 10, 15, 21, 30)) == "friday"      # Fri 00:30 Kyiv, still Thursday in UTC
    assert bc.should_regenerate(p, _ms(2026, 10, 16, 20, 59)) == "friday"      # Fri 23:59 Kyiv
    assert bc.should_regenerate(p, _ms(2026, 10, 16, 21, 30)) is None          # Sat 00:30 Kyiv, Friday in UTC
    assert bc.should_regenerate(p, _ms(2026, 10, 20, 10, 0)) == "stale"        # 8 days old
    json.dump({"generated": _ms(2026, 10, 12, 10, 0), "slides": []}, open(p, "w"))
    assert bc.should_regenerate(p, _ms(2026, 10, 13, 10, 0)) == "missing"      # empty deck counts as missing
    if hasattr(bc.KYIV, "key"):                                                # real tz database: winter time is UTC+2
        json.dump({"generated": _ms(2026, 11, 16, 10, 0), "slides": [{"id": "week"}]}, open(p, "w"))
        assert bc.should_regenerate(p, _ms(2026, 11, 19, 21, 30)) is None      # Thu 23:30 Kyiv
        assert bc.should_regenerate(p, _ms(2026, 11, 19, 22, 30)) == "friday"  # Fri 00:30 Kyiv


def test_broadcast_run_rebuilds_on_friday_and_only_refreshes_live_otherwise():
    d = tempfile.mkdtemp()
    json.dump(_terminal(), open(os.path.join(d, "terminal.json"), "w"))
    json.dump({"fng": {"value": 55, "label": "жадность"}}, open(os.path.join(d, "markets.json"), "w"))
    tue = _ms(2026, 10, 13, 9, 0)
    doc, why = bc.run(d, None, now_ms=tue)
    assert why == "missing" and doc["generated"] == tue and doc["week_of"] == "2026-10-12" and doc["slides"]
    assert doc["live"]["btc_price"] and doc["live"]["deck_age_h"] == 0
    doc2, why2 = bc.run(d, None, now_ms=tue + 3600_000)
    assert why2 is None and doc2["generated"] == tue and doc2["slides"] == doc["slides"] and doc2["live"]["asof"] == tue + 3600_000
    assert doc2["live"]["deck_age_h"] == 1.0 and "Слайды собраны" in doc2["live"]["note_ru"]
    fri = _ms(2026, 10, 16, 9, 0)
    doc3, why3 = bc.run(d, None, now_ms=fri)
    assert why3 == "friday" and doc3["generated"] == fri and doc3["generated_reason"] == "friday"
    doc4, why4 = bc.run(d, None, now_ms=tue + 7200_000, force=True)
    assert why4 == "forced"
    saved = json.load(open(os.path.join(d, "broadcast.json"), encoding="utf-8"))
    assert saved["generated_reason"] == "forced" and saved["live"]["asof"] == tue + 7200_000


def test_broadcast_indicator_ports_match_the_panel():
    assert bc.rsi([float(i) for i in range(1, 41)])[-1] == 100.0
    xs = [100 + (1 if i % 2 else -1) for i in range(60)]
    assert 40 < bc.rsi(xs)[-1] < 60
    rows = bc.radar_rows({"rows": [{"coin": "A", "b_only": False, "basis": -0.002}, {"coin": "B", "oi4": -0.06, "px4": -0.05},
                                   {"coin": "C", "b_sigma": -1.0, "ret_last": -0.01}], "d_need_basis": -0.002})
    by = {r["coin"]: r for r in rows}
    assert by["A"]["score"] == 1.0 and by["A"]["condition_ru"] == "дисконт фьючерса"
    assert by["B"]["score"] == 0.5 and by["C"]["score"] == round(0.01 / 0.035, 3)
    assert bc.resample_closes([[0, 1, 1, 1, 5, 1], [900_000, 1, 1, 1, 6, 1], [14_400_000, 1, 1, 1, 7, 1]], 14_400_000) == [6, 7]


def test_broadcast_week_news_from_sqlite():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE news_impact (news_id TEXT, asset TEXT, category TEXT, direction TEXT, horizon TEXT, magnitude INTEGER, confidence REAL, "
                 "reasoning TEXT, confirm TEXT, created INTEGER, price_at REAL, realized_ret REAL, scored INTEGER DEFAULT 0, title_ru TEXT)")
    rows = [("n1", "MARKET", "macro", "bear", "hours", 4, 0.7, "r", "", NOW - DAY, 80000, 0.005, 1, "Нефть давит на крипту"),
            ("n1", "BTC", "macro", "bear", "hours", 4, 0.6, "r", "", NOW - DAY, 80000, 0.005, 1, "Нефть давит на крипту"),
            ("n2", "ETH", "tech", "bull", "days", 3, 0.5, "r", "", NOW - 2 * DAY, 2400, None, 0, "Обновление сети прошло успешно"),
            ("n3", "MARKET", "macro", "bull", "days", 5, 0.9, "r", "", NOW - 9 * DAY, 80000, 0.01, 1, "Старая новость"),
            ("n4", "MARKET", "regulation", "bull", "days", 5, 0.9, "r", "", NOW - DAY, 80000, None, 0, None)]
    rows += [(f"s{i}", "BTC", "macro", "bull", "hours", 2, 0.5, "r", "", NOW - 20 * DAY, 1, 0.01 if i % 4 else -0.01, 1, "т") for i in range(20)]
    conn.executemany("INSERT INTO news_impact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    items, hit = bc.week_news(conn, None, NOW)
    assert [i["title"] for i in items] == ["Нефть давит на крипту", "Обновление сети прошло успешно"]
    assert items[0]["asset"] == "MARKET" and items[0]["realized_ret"] == 0.005
    assert hit["n"] == 23 and 0 <= hit["hit_rate"] <= 1
