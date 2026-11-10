"""10.11 (owner, real money): a true 200-day average for the long regime (older daily closes behind the ~106 days of 15m candles)
and the «no chase» entry rule of the CRYPTO NOVA AI spot portfolio (option A)."""
import sqlite3

from cnq.agents import entry_support as es
from cnq.agents import spot_profiles as sp
from cnq.bingx.rest import Candle
from cnq.terminal import daily
from cnq.terminal.report import long_avg_ru, regime

DAY = 86_400_000
T0 = 1_794_000_000_000 // DAY * DAY            # a UTC midnight


def _c15(day_px, t0: int):
    out = []
    for d, p in enumerate(day_px):
        for j in range(96):
            t = t0 + (d * 96 + j) * 900_000
            out.append(Candle(t, p, p * 1.001, p * 0.999, p, 1.0, t + 899_999, p, 1, 0.5, 0.5 * p))
    return out


def test_regime_uses_older_daily_closes_for_a_true_200_day_average():
    t15 = T0 - 107 * DAY
    px = [85.0] * 17 + [110.0] * 83 + [92.0] * 7   # 107 days of 15m: alone, 92 is under their own average (~105) → BEAR
    cs = _c15(px, t15)
    alone = regime(cs)
    assert alone["long"] == "BEAR" and alone["sma_days"] == 107
    older = {t15 // DAY - k: 60.0 for k in range(1, 151)}   # older daily closes at 60 → the true 200-day average is ~84
    r = regime(cs, older)
    assert r["sma_days"] == 200 and 80 < r["sma200"] < 90 and r["long"] == "BULL"
    assert long_avg_ru(r).startswith("200-дневной средней")
    assert abs(r["r90"] - (92.0 / 85.0 - 1)) < 1e-9                 # the 90-day change from the same daily series


def test_daily_history_merges_seed_and_store_and_refresh_keeps_250_days(tmp_path):
    class Store:
        def __init__(self):
            self.rows = {}

        def candle_bounds(self, s, i):
            r = self.rows.get(s) or []
            return (r[0].open_time, r[-1].open_time, len(r)) if r else (None, None, 0)

        def upsert_candles(self, s, i, cs):
            m = {c.open_time: c for c in self.rows.get(s) or []}
            m.update({c.open_time: c for c in cs})
            self.rows[s] = [m[k] for k in sorted(m)]
            return len(cs)

        def candles(self, s, i, start=None):
            return [c for c in self.rows.get(s) or [] if start is None or c.open_time >= start]

    calls = []

    class Rest:
        def klines_range(self, s, i, a, b):
            calls.append((s, a, b))
            return [Candle(t, 1, 1, 1, 50.0, 1, t + DAY - 1, 1, 1, 1, 1) for t in range(a // DAY * DAY, b, DAY)]

    st, now = Store(), T0 + 3600_000
    assert daily.refresh(st, Rest(), ["ZEC-USDT"], now) >= 400        # empty → the whole 400-day window
    assert daily.refresh(st, Rest(), ["ZEC-USDT"], now + 3600_000) == 0 and len(calls) == 1   # fresh → nothing
    seed = tmp_path / "seed.json"
    seed.write_text('{"symbols": {"ZEC": {"start_day": %d, "closes": [10.0, 11.0]}}}' % (T0 // DAY - 420))
    h = daily.history(st, ["ZEC-USDT"], now, seed_path=str(seed), local_path=str(tmp_path / "none.json"))["ZEC-USDT"]
    assert T0 // DAY not in h and T0 // DAY - 1 in h                  # today's forming bar left out
    assert len(h) >= 250 and h[T0 // DAY - 420] == 10.0               # store days + the older seed days
    m = daily.sma_map({"ZEC-USDT": h})
    assert m["ZEC"]["days"] == 200 and abs(m["ZEC"]["sma200"] - 50.0) < 1e-9


def _sup(**kw):
    s = {"sma200": {"AVAX": 10.0}, "walls": {}, "btc": {"price": 75000, "ll20": 70000, "mid": "UP_NO_BREAKOUT"}}
    s.update(kw)
    return s


def test_no_chase_rule():
    ok, why = es.check("AVAX", 10.2, _sup())
    assert ok and "200-дневной" in why and "+2.0%" not in why and "2.0%" in why
    ok, why = es.check("AVAX", 10.5, _sup())                         # +5 % above the average → not chased
    assert not ok and "не догоняем" in why and "$10.30" in why
    ok, why = es.check("AVAX", 9.5, _sup())                          # below the average, no wall → the support is broken
    assert not ok and "пробита" in why
    walls = {"AVAX": [{"price": 9.4, "usd": 2e6, "venue": "OKX спот", "since": 1}]}
    ok, why = es.check("AVAX", 9.5, _sup(walls=walls))               # a persistent wall 1 % below → a support
    assert ok and "стены покупок" in why
    ok, why = es.check("NEW", 1.0, _sup())
    assert not ok and "нет данных о поддержке" in why
    ok, why = es.check("AVAX", 10.1, _sup(btc={"price": 69000, "ll20": 70000}))
    assert not ok and "минимум 20 дней" in why
    assert es.check("AVAX", 50.0, None) == (True, "")                # no support data passed → the old behaviour


def test_load_reads_terminal_owner_watch_and_keeps_only_persistent_walls(tmp_path):
    import json
    (tmp_path / "terminal.json").write_text(json.dumps({"daily_sma200": {"ZEC": {"sma200": 900.0, "days": 200},
                                                                         "XX": {"sma200": 5.0, "days": 120}}}))
    t = T0
    (tmp_path / "owner_watch.json").write_text(json.dumps({"asof": t, "coins": [{"coin": "ADA", "asof": t, "sma200": 0.2, "bid_walls": [
        {"price": 0.17, "usd": 9e6, "venue": "BingX", "since": t - 2 * 3600_000},
        {"price": 0.175, "usd": 5e6, "venue": "BingX", "since": t - 600_000}]}]}))
    (tmp_path / "decision.json").write_text(json.dumps({"coins": [{"symbol": "BTC-USDT", "price": 75000,
                                                                   "regime": {"ll20": 70000, "mid": "LONG"}}]}))
    s = es.load(str(tmp_path))
    assert s["sma200"] == {"ZEC": 900.0, "ADA": 0.2}                 # a 120-day figure is not a 200-day average
    assert [w["price"] for w in s["walls"]["ADA"]] == [0.17]         # the 10-minute-old wall is not persistent yet
    assert s["btc"] == {"price": 75000, "ll20": 70000, "mid": "LONG"}


def test_ideas_portfolio_buys_alts_only_near_support_and_logs_why():
    rows = [{"symbol": "BTC", "name": "Bitcoin", "kind": "core", "status": "buy", "status_ru": "можно брать", "why_ru": "ядро", "price": 80000},
            {"symbol": "NEAR1", "name": "Near1", "kind": "mid", "status": "buy", "status_ru": "можно брать", "why_ru": "ок", "price": 10.2},
            {"symbol": "FAR1", "name": "Far1", "kind": "mid", "status": "buy", "status_ru": "можно брать", "why_ru": "ок", "price": 12.0}]
    src = {"cg": {"BTC": 80000, "NEAR1": 10.2, "FAR1": 12.0}, "meme": {}, "hl": {}}
    sup = {"sma200": {"NEAR1": 10.0, "FAR1": 10.0}, "walls": {}, "btc": {"price": 80000, "ll20": 70000, "mid": "LONG"}}
    st = sp.IdeasPortfolio(sqlite3.connect(":memory:")).step(src, {"asof": T0, "available": True, "rows": rows}, T0, support=sup)
    held = {p["symbol"] for p in st["positions"]}
    assert "BTC" in held and "NEAR1" in held and "FAR1" not in held
    buy = next(a for a in st["actions"] if a["symbol"] == "NEAR1" and a["action"] == "BUY")
    assert "Вход у поддержки" in buy["reason"] and "200-дневной средней" in buy["reason"]
    skip = next(a for a in st["actions"] if a["symbol"] == "FAR1")
    assert skip["action"] == "SKIP" and "не догоняем" in skip["reason"]
    assert any(n["symbol"] == "FAR1" and "не догоняем" in n["why_ru"] for n in st["not_bought"])
