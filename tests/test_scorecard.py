"""Public prediction scorecard (cnq/agents/scorecard.py, scripts/verify_scorecard.py): hash chain and tamper detection, no look-ahead
in resolution, Brier / calibration maths on known examples, backfill marking, and missing or broken inputs never raise."""
import importlib.util
import json
import os
import pathlib
import sqlite3

from cnq.agents import scorecard as sc

M15, H = 900_000, 3_600_000
T0 = 1_792_000_800_000                    # a 15-minute boundary
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _verify_mod():
    spec = importlib.util.spec_from_file_location("verify_scorecard", ROOT / "scripts" / "verify_scorecard.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _store(path, series):
    """series = {sym: [(open_time, high, low, close), …]} → a candle store with the engine's schema"""
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS candles (symbol TEXT NOT NULL, interval TEXT NOT NULL, open_time INTEGER NOT NULL, open REAL, high REAL, "
              "low REAL, close REAL, volume REAL, close_time INTEGER, quote_volume REAL, trades INTEGER, taker_buy_volume REAL, taker_buy_quote REAL, "
              "PRIMARY KEY (symbol, interval, open_time))")
    for sym, bars in series.items():
        c.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,0,0,0,0,0,0)",
                      [(f"{sym}-USDT", "15m", t, cl, hi, lo, cl) for t, hi, lo, cl in bars])
    c.commit()
    c.close()
    return path


def _flat(start, n, px=100.0, spread=0.001):
    return [(start + i * M15, px * (1 + spread), px * (1 - spread), px) for i in range(n)]


def _ab(t, p=0.6, up=102.0, dn=98.0, sym="BTC", px=100.0):
    return {"id": f"ab:{sym}-USDT-{t // sc.BUCKET_MS}", "src": "ab", "sym": sym, "t": t, "hz": 24, "p": p, "ev": "up_first", "px": px, "up": up, "dn": dn}


# ---------------------------------------------------------------------------------------------------------------- chain
def test_chain_roundtrip_and_tamper_detection(tmp_path):
    d = str(tmp_path / "data")
    info = sc.read_chain(d, "ledger")
    assert info["ok"] and info["lines"] == 0 and info["head"] == sc.GENESIS
    rows = sc.append_chain(info, [_ab(T0), _ab(T0 + sc.BUCKET_MS, p=0.56), _ab(T0 + 2 * sc.BUCKET_MS)], T0 + 3 * sc.BUCKET_MS)
    assert [r["n"] for r in rows] == [1, 2, 3]
    again = sc.read_chain(d, "ledger")
    assert again["ok"] and again["lines"] == 3 and again["head"] == rows[-1]["h"]
    v = _verify_mod()
    assert v.main(["--data", d, "--feed", str(tmp_path / "none.json")]) == 0
    path = os.path.join(d, sc.LEDGER)
    good = open(path, encoding="utf-8").read()
    lines = good.splitlines()
    # 1) one number changed in line 2
    open(path, "w", encoding="utf-8").write(good.replace('"p":0.56', '"p":0.66'))
    bad = sc.read_chain(d, "ledger")
    assert not bad["ok"] and bad["bad_line"] == 2
    assert v.main(["--data", d, "--feed", str(tmp_path / "none.json")]) == 1
    # a broken chain is never appended to
    assert sc.append_chain(bad, [_ab(T0 + 3 * sc.BUCKET_MS)], T0) == []
    # 2) a line removed, 3) two lines swapped
    for text in ("\n".join([lines[0], lines[2]]) + "\n", "\n".join([lines[0], lines[2], lines[1]]) + "\n"):
        open(path, "w", encoding="utf-8").write(text)
        assert not sc.read_chain(d, "ledger")["ok"]
        assert v.main(["--data", d, "--feed", str(tmp_path / "none.json")]) == 1
    # 4) the whole chain re-hashed after an edit is internally consistent — but the head published earlier no longer matches
    open(path, "w", encoding="utf-8").write(good)
    feed = tmp_path / "scorecard.json"
    feed.write_text(json.dumps({"chain": {"ledger": {"lines": 3, "head": again["head"]}, "outcomes": {"lines": 0, "head": sc.GENESIS}}}))
    assert v.main(["--data", d, "--feed", str(feed)]) == 0
    forged, prev = [], sc.GENESIS
    for i, ln in enumerate(lines):
        p = json.loads(ln)
        if i == 1:
            p["p"] = 0.66
        line, prev = sc.make_line(prev, p)
        forged.append(line)
    open(path, "w", encoding="utf-8").write("\n".join(forged) + "\n")
    assert sc.read_chain(d, "ledger")["ok"]
    assert v.main(["--data", d, "--feed", str(feed)]) == 1


def test_line_format_is_language_neutral():
    line, h = sc.make_line(sc.GENESIS, {"id": "x", "n": 1, "p": 0.5, "tt": "Рост «ETF»"})
    body, h2 = sc.split_line(line)
    assert h == h2 and json.loads(line)["h"] == h and json.loads(body) == {"id": "x", "n": 1, "p": 0.5, "tt": "Рост «ETF»"}
    import hashlib
    assert hashlib.sha256((sc.GENESIS + body).encode("utf-8")).hexdigest() == h


def test_segments_rotate_and_still_verify(tmp_path):
    d = str(tmp_path / "data")
    old = sc.SEG_BYTES
    sc.SEG_BYTES = 1                                   # every append closes a segment
    try:
        info = sc.read_chain(d, "ledger")
        sc.append_chain(info, [_ab(T0), _ab(T0 + sc.BUCKET_MS)], T0 + 13 * sc.BUCKET_MS)
        for i in range(2, 12):
            sc.append_chain(info, [_ab(T0 + i * sc.BUCKET_MS)], T0 + 13 * sc.BUCKET_MS)
        segs = sorted(os.listdir(os.path.join(d, sc.SEG_DIR)))
        assert len(segs) == 11 and segs[0] == "ledger-0000001.jsonl" and segs[1] == "ledger-0000003.jsonl"
        r = sc.read_chain(d, "ledger")
        assert r["ok"] and r["lines"] == 12 and r["segments"] == 11
        assert _verify_mod().main(["--data", d, "--feed", str(tmp_path / "none.json")]) == 0
        # a rotation that stopped after writing the segment (the active file still holds the same lines) is recognised …
        last = os.path.join(d, sc.SEG_DIR, segs[-1])
        act = os.path.join(d, sc.LEDGER)
        assert open(act, encoding="utf-8").read() == ""
        open(act, "w", encoding="utf-8").write(open(last, encoding="utf-8").read())
        assert _verify_mod().main(["--data", d, "--feed", str(tmp_path / "none.json")]) == 0
        r2 = sc.read_chain(d, "ledger")
        assert r2["ok"] and r2["lines"] == r["lines"] and r2["half_rotated"]
        # … and the next append continues the chain cleanly
        sc.append_chain(r2, [_ab(T0 + 50 * sc.BUCKET_MS)], T0 + 60 * sc.BUCKET_MS)
        r3 = sc.read_chain(d, "ledger")
        assert r3["ok"] and r3["lines"] == r["lines"] + 1 and not r3["half_rotated"]
    finally:
        sc.SEG_BYTES = old


# ---------------------------------------------------------------------------------------------------------------- no look-ahead
def test_ab_resolution_never_uses_candles_from_before_publication(tmp_path):
    t = T0 + 10 * M15 + 60_000                         # published 1 minute into a candle
    bars = _flat(T0, 10)
    bars.append((T0 + 10 * M15, 103.0, 99.5, 100.0))   # this candle opened BEFORE t and touched the up target → must be ignored
    bars += [(T0 + (11 + i) * M15, 100.2, 99.8, 100.0) for i in range(30)]
    bars.append((T0 + 41 * M15, 100.0, 97.5, 98.0))    # after t: the down target is touched first
    bars += [(T0 + (42 + i) * M15, 104.0, 99.0, 103.0) for i in range(80)]   # later the up target too
    prices = sc.Prices(_store(str(tmp_path / "c.sqlite"), {"BTC": bars}))
    p = _ab(t, p=0.7)
    assert sc.resolve_prob(p, prices, t + 23 * H) is None            # the horizon has not passed: nothing is decided early
    o = sc.resolve_prob(p, prices, t + 25 * H)
    assert o["y"] == 0 and o["t"] == T0 + 41 * M15 and o["px1"] == 98.0
    # neither target touched → the close decides, only with the whole window covered by candles
    flat = sc.Prices(_store(str(tmp_path / "f.sqlite"), {"BTC": _flat(T0, 10) + [(T0 + (10 + i) * M15, 100.6, 100.4, 100.5) for i in range(100)]}))
    q = _ab(T0 + 10 * M15, p=0.4)
    assert sc.resolve_prob(q, flat, T0 + 10 * M15 + 25 * H)["y"] == 1
    holed = sc.Prices(_store(str(tmp_path / "h.sqlite"), {"BTC": _flat(T0, 10) + [(T0 + (10 + i) * M15, 100.6, 100.4, 100.5) for i in range(0, 100, 3)]}))
    assert sc.resolve_prob(q, holed, T0 + 10 * M15 + 25 * H) is None


def test_band_and_news_resolve_on_the_close_after_the_horizon_only(tmp_path):
    t = T0 + 8 * M15
    before = [(T0 + i * M15, 130.0, 70.0, 100.0) for i in range(8)]               # wild candles before publication
    after = [(t + i * M15, 101.0, 99.0, 100.0 + (5.0 if i == 95 else 0.0)) for i in range(100)]
    prices = sc.Prices(_store(str(tmp_path / "c.sqlite"), {"ETH": before + after}))
    band = {"id": "band:ETH:day:1", "src": "band", "sym": "ETH", "t": t, "hz": 24, "p": sc.BAND_P, "ev": "inside", "px": 100.0, "lo": 98.0, "hi": 102.0}
    assert sc.resolve_prob(band, prices, t + 24 * H) is None                        # its last candle is not closed yet
    o = sc.resolve_prob(band, prices, t + 24 * H + M15)
    assert o["y"] == 0 and o["px1"] == 105.0 and o["t"] == t + 24 * H               # the close at the end of the window: outside
    news = {"id": "news:a:ETH", "src": "news", "sym": "ETH", "t": t, "hz": 24, "p": 0.8, "ev": "dir", "dir": "bear", "px": 50.0}
    o = sc.resolve_prob(news, prices, t + 25 * H)
    assert o["px0"] == 100.0 and o["y"] == 0                                         # engine's own price at t, then +5 % → bear wrong
    # no candles after publication → nothing is decided (the candles before it are never used for the outcome)
    only_before = sc.Prices(_store(str(tmp_path / "b.sqlite"), {"ETH": before}))
    assert sc.resolve_prob(band, only_before, t + 30 * H) is None


# ---------------------------------------------------------------------------------------------------------------- maths
def test_brier_climatology_and_calibration_on_known_examples():
    pairs = [(0.8, 1), (0.8, 1), (0.8, 0), (0.3, 0), (0.3, 1)]
    st = sc.prob_stats(pairs)
    # (0.04 + 0.04 + 0.64 + 0.09 + 0.49) / 5 = 0.26
    assert st["brier"] == 0.26 and st["n"] == 5 and st["base_rate"] == 0.6 and st["brier_clim"] == 0.24
    assert st["hits"] == 3 and st["hit_rate"] == 0.6 and st["skill_vs_coin"] == round(1 - 0.26 / 0.25, 3)
    tab = sc.calibration_table(pairs)
    assert tab == [{"range": "30–40%", "n": 2, "p_mean": 0.3, "freq": 0.5, "gap": 0.2},
                   {"range": "80–90%", "n": 3, "p_mean": 0.8, "freq": 0.667, "gap": -0.133}]
    assert sc.calibration_table([(1.0, 1), (0.0, 0)])[-1]["range"] == "90–100%"
    assert sc.prob_stats([]) == {"n": 0}
    many = pairs * 6                                                                  # n = 30 → a verdict is allowed
    code, ru, en = sc.verdict("ab", sc.prob_stats(many))
    assert code == "worse_than_coin" and "хуже подбрасывания монеты" in ru and "coin flip" in en
    assert sc.verdict("ab", sc.prob_stats(pairs))[0] == "few"
    good = sc.prob_stats([(0.9, 1)] * 20 + [(0.1, 0)] * 20)
    assert sc.verdict("news", good)[0] == "skill"
    coin = sc.prob_stats([(0.5, 1)] * 15 + [(0.5, 0)] * 15)
    assert coin["brier"] == 0.25 and sc.verdict("base", coin)[0] == "no_skill"
    assert sc.verdict("band", sc.prob_stats([(sc.BAND_P, 1)] * 10 + [(sc.BAND_P, 0)] * 20))[0] == "narrow"
    assert sc.verdict("band", sc.prob_stats([(sc.BAND_P, 1)] * 20 + [(sc.BAND_P, 0)] * 10))[0] == "as_claimed"


def test_signal_scores_after_fees_against_buying_the_same_coin(tmp_path):
    t0, t1 = T0 + 8 * M15, T0 + 8 * M15 + 24 * H
    bars = [(T0 + i * M15, 10.1, 9.9, 10.0) for i in range(8)] + [(t0 + i * M15, 11.1, 9.0, 11.0 if i >= 95 else 10.0) for i in range(100)]
    prices = sc.Prices(_store(str(tmp_path / "c.sqlite"), {"ARB": bars}))
    pred = {"id": "feed:E-ARB-1", "src": "feed", "sym": "ARB", "t": t0, "side": "LONG", "entry": 10.0, "stop": 9.0}
    o = sc.resolve_feed(pred, {"id": "E-ARB-1", "closed": t1, "pnl_pct": 5.0, "exit_kind": "TIME"}, prices)
    assert o["pct"] == 5.0 and o["r"] == 0.5 and o["base"] == round(10.0 - sc.FEE_RT_PCT, 3) and o["by"] == "desk"
    assert sc.resolve_feed(pred, {"id": "E-ARB-1", "closed": None, "pnl_pct": None}, prices) is None   # still open
    st = sc.signal_stats([o, {"pct": -2.0, "r": -0.2, "base": 1.0}])
    assert st["n"] == 2 and st["wins"] == 1 and st["avg_pct"] == 1.5 and st["avg_r"] == 0.15 and st["edge_pct"] == round(1.5 - (9.86 + 1.0) / 2, 3)
    assert sc.verdict("feed", st)[0] == "few"
    assert sc.verdict("feed", sc.signal_stats([{"pct": -0.5, "r": -0.1}] * 20))[0] == "loss"
    assert sc.verdict("feed", sc.signal_stats([{"pct": 1.0, "base": 2.0}] * 20))[0] == "no_edge"
    assert sc.resolve_app({"status": "closed", "policies": {"ladder_be": {"pct": -0.0029, "r": -0.0028}}})["pct"] == -0.0029
    assert sc.resolve_app({"status": "active"}) is None


def test_outlook_windows_never_overlap():
    def ol(asof):
        h = {"range": {"lo": 90.0, "hi": 110.0}, "base_rate_up": {"p": 0.52}}
        return {"asof": asof, "symbol": "BTC", "price": 100.0, "horizons": {"day": h, "week": h, "month": h},
                "others": {"ETH": {"price": 10.0, "horizons": {"day": h}}}}
    last = {}
    a = sc.collect_outlook(ol(T0), last, T0)
    assert len(a) == 8 and {x["src"] for x in a} == {"band", "base"}
    assert sc.collect_outlook(ol(T0 + H), last, T0 + H) == []                          # the day window has not ended
    b = sc.collect_outlook(ol(T0 + 24 * H), last, T0 + 24 * H)
    assert sorted((x["src"], x["sym"], x["hz"]) for x in b) == [("band", "BTC", 24), ("band", "ETH", 24), ("base", "BTC", 24), ("base", "ETH", 24)]
    assert sc.collect_outlook(ol(T0), {}, T0 + 10 * H) == []                           # a stale outlook is not «published now»


# ---------------------------------------------------------------------------------------------------------------- backfill + engine step
def _agents_db(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE forecasts(id TEXT PRIMARY KEY, time INTEGER, symbol TEXT, p_up REAL, price REAL, up_target REAL, dn_target REAL, "
              "horizon INTEGER, outcome TEXT, resolved INTEGER, hit_price REAL, components TEXT)")
    c.execute("INSERT INTO forecasts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (f"BTC-USDT-{T0 // sc.BUCKET_MS}", T0, "BTC-USDT", 0.62, 100.0, 102.0, 98.0, 24 * H, "UP", 1, 102.0, "{}"))
    c.execute("CREATE TABLE news_impact (news_id TEXT, asset TEXT, category TEXT, direction TEXT, horizon TEXT, magnitude INTEGER, confidence REAL, "
              "reasoning TEXT, confirm TEXT, created INTEGER, price_at REAL, realized_ret REAL, scored INTEGER DEFAULT 0, title_ru TEXT, PRIMARY KEY (news_id, asset))")
    for asset in ("MARKET", "BTC"):                    # the same story for MARKET and BTC is ONE call on BTC
        c.execute("INSERT INTO news_impact VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("n1", asset, "macro", "bear", "hours", 3, 0.7, "", "", T0 + 5, 100.0, None, 0, "ФРС"))
    c.execute("CREATE TABLE app_signals (signal_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, entry_lo REAL, entry_hi REAL, sl REAL, leverage REAL, "
              "t_signal INTEGER, targets TEXT, sim TEXT)")
    sim = {"state": "closed", "ended": "sl", "policies": {"ladder_be": {"closed": True, "net": -0.5, "r": -0.4, "t": T0 + 9 * H}}}
    c.execute("INSERT INTO app_signals VALUES(?,?,?,?,?,?,?,?,?,?)", ("77", "DOGE-USDT", "LONG", 0.1, 0.1, 0.09, 10, T0 + 7, json.dumps([{"price": 0.11}]), json.dumps(sim)))
    c.commit()
    c.close()
    return path


def _feeds(web, now, feed_items=None):
    os.makedirs(web, exist_ok=True)
    json.dump({"open": {"ETH": {"p_up": 0.4, "up": 11.0, "dn": 9.0, "time": now - 10 * 60_000, "price": 10.0}}, "stats": {"n": 570, "brier": 0.2603}},
              open(os.path.join(web, "calibration.json"), "w"))
    json.dump({"items": feed_items or [{"id": "E-SOL-1", "symbol": "SOL", "side": "LONG", "grade": "E", "entry": 100.0, "stop": 90.0,
                                        "opened": T0 + 9, "seeded": 1, "closed": None, "pnl_pct": None,
                                        "open_text": "x\nTake-Profit Targets:\n1) 110\n2) 120\nStop Targets:\n1) 90"}]},
              open(os.path.join(web, "signals_feed.json"), "w"))


def test_backfill_marks_every_line_and_is_idempotent(tmp_path):
    data, web = str(tmp_path / "data"), str(tmp_path / "web")
    now = T0 + 10 * H
    _feeds(web, now)
    db = _agents_db(str(tmp_path / "agents.sqlite"))
    r = sc.backfill(web, data, db, git_dir=None, now=now)
    assert r["added"] == 5 and r["by_source"] == {"ab": 2, "news": 1, "feed": 1, "app": 1} and r["outcomes"] == 1
    led = sc.read_chain(data, "ledger")
    assert led["ok"] and all(x.get("bf") == 1 for x in led["rows"])
    news = [x for x in led["rows"] if x["src"] == "news"]
    assert len(news) == 1 and news[0]["sym"] == "BTC" and news[0]["conf"] == 0.7 and "p" not in news[0] and news[0]["hz"] == 6
    assert sc.prob(news[0]) == 0.85 and sc.prob({**news[0], "ev": "neutral"}) is None
    feed = next(x for x in led["rows"] if x["src"] == "feed")
    assert feed["tps"] == [110.0, 120.0] and feed["pub"] == 0
    oc = sc.read_chain(data, "outcomes")
    assert oc["rows"][0]["id"] == "app:77" and oc["rows"][0]["bf"] == 1 and oc["rows"][0]["ph"] == next(x["h"] for x in led["rows"] if x["id"] == "app:77")
    assert sc.backfill(web, data, db, git_dir=None, now=now)["added"] == 0
    # the engine step afterwards records only what is new, without the backfill mark
    _feeds(web, now + 4 * H, feed_items=[{"id": "E-SOL-2", "symbol": "SOL", "side": "SHORT", "entry": 100.0, "stop": 110.0, "opened": now + 3 * H,
                                          "seeded": 0, "open_text": ""}])
    d = sc.run(web, data, None, now=now + 4 * H)
    assert d["this_run"]["recorded"] == 2
    led = sc.read_chain(data, "ledger")
    fresh = [x for x in led["rows"] if not x.get("bf")]
    assert {x["id"] for x in fresh} == {"feed:E-SOL-2", f"ab:ETH-USDT-{(now + 4 * H - 10 * 60_000) // sc.BUCKET_MS}"}
    assert all(x["rec"] == now + 4 * H for x in fresh) and next(x for x in fresh if x["src"] == "feed")["pub"] == 1


def test_missing_or_broken_inputs_never_raise(tmp_path):
    # nothing at all
    d = sc.run(str(tmp_path / "w"), str(tmp_path / "d"), str(tmp_path / "none.sqlite"), now=T0)
    assert d["headline"]["recorded"] == 0 and any("candles" in e for e in d["errors"]) and os.path.exists(tmp_path / "w" / "scorecard.json")
    # garbage feeds
    web, data = tmp_path / "web", tmp_path / "data"
    web.mkdir()
    for f in ("calibration.json", "outlook.json", "decision.json", "signals_feed.json", "app_signals.json"):
        (web / f).write_text("{not json")
    (web / "app_signals.json").write_text("[1, 2]")
    d = sc.run(str(web), str(data), None, now=T0)
    assert d["headline"]["recorded"] == 0 and len(d["errors"]) >= 5
    # odd rows inside valid JSON are skipped, not fatal
    json.dump({"open": {"BTC": {"p_up": "x"}, "ETH": None, "SOL": {"p_up": 0.5, "up": 1, "dn": 2, "time": T0}}}, open(web / "calibration.json", "w"))
    json.dump({"news": {"impacts": [None, {"news_id": "a"}, {"news_id": "b", "created": T0, "asset": "BTC", "direction": "bull", "confidence": "nan"}]}},
              open(web / "decision.json", "w"))
    json.dump({"items": [{"id": "1"}, {"id": "2", "opened": T0, "side": "UP", "entry": 1, "stop": 1}]}, open(web / "signals_feed.json", "w"))
    d = sc.run(str(web), str(data), None, now=T0 + H)
    assert d["headline"]["recorded"] == 1 and sc.read_chain(str(data), "ledger")["rows"][0]["ev"] == "neutral"
    # a broken ledger: reported, never appended to, never raises
    path = data / sc.LEDGER
    path.write_text(path.read_text().replace('"neutral"', '"dir"'))
    before = path.read_text()
    json.dump({"open": {"ETH": {"p_up": 0.4, "up": 11.0, "dn": 9.0, "time": T0 + 2 * H}}}, open(web / "calibration.json", "w"))
    d = sc.run(str(web), str(data), None, now=T0 + 2 * H)
    assert any("BROKEN" in e for e in d["errors"]) and not d["chain"]["ledger"]["ok"] and path.read_text() == before
    # without a candle store nothing is voided, however old
    data2 = tmp_path / "d2"
    info = sc.read_chain(str(data2), "ledger")
    sc.append_chain(info, [_ab(T0)], T0)
    d = sc.run(str(tmp_path / "w2"), str(data2), None, now=T0 + 1000 * H)
    assert d["headline"]["void"] == 0 and d["headline"]["pending"] == 1


def test_end_to_end_feed_lists_misses_and_publishes_the_head(tmp_path):
    data, web = str(tmp_path / "data"), str(tmp_path / "web")
    t = T0 + 8 * M15
    bars = _flat(T0, 8) + [(t + i * M15, 100.3, 97.0 if i == 4 else 99.7, 100.0) for i in range(120)]
    db = _store(str(tmp_path / "c.sqlite"), {"BTC": bars})
    info = sc.read_chain(data, "ledger")
    sc.append_chain(info, [_ab(t, p=0.75), {**_ab(t + 30 * 60_000, sym="XYZ"), "id": "ab:XYZ"}], t)
    d = sc.run(web, data, db, now=t + 26 * H)
    assert d["this_run"]["resolved"] == 1                                            # XYZ has no candles, but its window is too recent to void
    src = {s["key"]: s for s in d["sources"]}
    assert len(src) == 6 and src["ab"]["checked"] == 1 and src["ab"]["pending"] == 1 and src["ab"]["stats"]["brier"] == round(0.75 ** 2, 4)
    assert d["misses"] and d["misses"][0]["id"] == f"ab:BTC-USDT-{t // sc.BUCKET_MS}" and "вниз" in d["misses"][0]["ru"]
    assert d["recent"][0]["ok"] is False and len(d["recent"]) <= sc.RECENT_N
    pub = json.load(open(os.path.join(web, "scorecard.json"), encoding="utf-8"))
    led = sc.read_chain(data, "ledger")
    assert pub["chain"]["ledger"]["head"] == led["head"] and pub["chain"]["ledger"]["lines"] == 2
    assert pub["sources"][0]["verdict_ru"] and pub["sources"][0]["verdict_en"] and pub["method_en"] and pub["not_covered_ru"]
    assert _verify_mod().main(["--data", data, "--feed", os.path.join(web, "scorecard.json")]) == 0
    # 72 h after its horizon a coin the (healthy) store never had is listed as «could not be checked», not dropped
    late = bars + [(t + i * M15, 100.3, 99.7, 100.0) for i in range(120, 120 + 4 * 24 * 5)]
    db2 = _store(str(tmp_path / "c2.sqlite"), {"BTC": late})
    d = sc.run(web, data, db2, now=t + 24 * H + (sc.VOID_AFTER_H + 2) * H)
    assert d["headline"]["void"] == 1 and any("не удалось проверить" in r["ru"] for r in d["recent"])
    # nothing changed → the feed is not rewritten (the engine commits every changed file)
    assert sc.run(web, data, db2, now=t + 24 * H + (sc.VOID_AFTER_H + 2) * H + 60_000)["written"] is False


def test_engine_runs_the_step_and_owns_its_files(tmp_path):
    from cnq import engine as E
    assert E.FAST[-1] == "scorecard" and "scorecard.json" in E.OWNED and "scorecard" in E.STEP_TIMEOUT
    assert {"scorecard_ledger.jsonl", "scorecard_outcomes.jsonl", "scorecard"} <= set(E.DATA_OWNED)
    repo, run = tmp_path / "repo", tmp_path / "run"
    for d in ("cnq", "scripts", "config", "data", "web", "data/scorecard"):
        (repo / d).mkdir(parents=True)
    (repo / "data" / "scorecard_ledger.jsonl").write_text("repo\n")
    (repo / "data" / "scorecard" / "ledger-0000001.jsonl").write_text("seg\n")
    old = (E.REPO, E.RUN)
    E.REPO, E.RUN = str(repo), str(run)
    try:
        E.prepare()                                                                  # first start: the repo's ledger and segments
        assert (run / "data" / "scorecard_ledger.jsonl").read_text() == "repo\n"
        assert (run / "data" / "scorecard" / "ledger-0000001.jsonl").read_text() == "seg\n"
        (run / "data" / "scorecard_ledger.jsonl").write_text("engine\n")
        (repo / "data" / "scorecard_ledger.jsonl").write_text("repo, newer\n")
        E.pull_inputs()                                                              # a pull never overwrites the engine's ledger
        assert (run / "data" / "scorecard_ledger.jsonl").read_text() == "engine\n"
        (run / "data" / "scorecard" / "ledger-0000002.jsonl").write_text("seg2\n")
        paths = E.copy_out_data()
        assert set(paths) == {"data/scorecard_ledger.jsonl", "data/scorecard"}
        assert (repo / "data" / "scorecard_ledger.jsonl").read_text() == "engine\n"
        assert (repo / "data" / "scorecard" / "ledger-0000002.jsonl").read_text() == "seg2\n"
    finally:
        E.REPO, E.RUN = old
    gi = (ROOT / ".gitignore").read_text()
    assert "!data/scorecard_ledger.jsonl" in gi and "!data/scorecard/" in gi
    wf = (ROOT / ".github" / "workflows" / "site.yml").read_text()
    head = wf[:wf.index("schedule:")]
    assert '"web/scorecard.json"' in head and '"data/scorecard/**"' in head       # engine pushes never restart the whole pipeline
