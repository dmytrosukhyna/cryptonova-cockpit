import json, math, os, random, sqlite3, tempfile

from cnq.agents import outlook as ol

DAY = 86_400_000


def _walk(n=1600, start=20000.0, seed=3, drift=0.0004, vol=0.03, day0=18600):
    rng = random.Random(seed); px = start; out = {}
    for i in range(n):
        px *= math.exp(rng.gauss(drift, vol)); out[day0 + i] = px
    return out


def _ctx(hist, now_ms):
    last = max(hist["BTC"]); price = hist["BTC"][last]
    btc = {"symbol": "BTC-USDT", "price": price, "chg24": 0.01, "chg7": -0.02,
           "regime": {"long": "BULL", "mid": "UP_NO_BREAKOUT", "mid_note": "тренд вверх, но без обновления максимума 20 дней — ждать",
                      "sma200": price * 0.9, "hh20": price * 1.05, "ll20": price * 0.95}}
    eth = {"symbol": "ETH-USDT", "price": hist["ETH"][max(hist["ETH"])], "chg24": 0.0, "chg7": 0.0, "regime": {}}
    return {"terminal": {"asof": now_ms, "coins": [btc, eth], "portfolio": {"btc_bull": True}},
            "calibration": {"stats": {"n": 243, "hit_rate": 0.51, "brier": 0.2614, "brier_coin_flip": 0.25, "per_coin": {"BTC": {"n": 14, "hit_rate": 0.64}}},
                            "open": {"BTC": {"p_up": 0.62, "up": price * 1.01, "dn": price * 0.99, "time": now_ms - 3600_000}}},
            "calendar": {"events": [{"time": now_ms + 3 * DAY, "name": "ФРС: решение по ставке", "importance": 3, "what": "Направление заранее не угадать."},
                                    {"time": now_ms + 20 * DAY, "name": "Инфляция в США (CPI)", "importance": 2, "what": "В первый час колебания сильнее."},
                                    {"time": now_ms + 21 * DAY, "name": "ВВП США", "importance": 1, "what": "Заметной реакции нет."}]},
            "macro": {"items": [{"key": "VIX", "name_ru": "VIX", "value": 20.0, "chg_1d": 21.4, "chg_unit": "%", "asof": "2026-10-16",
                                 "signal_ru": "Страх на рынке акций резко вырос."},
                                {"key": "DXY", "name_ru": "Доллар", "value": 101.0, "chg_1d": -0.2, "chg_unit": "%", "asof": "2026-10-16", "signal_ru": ""}]},
            "paper": {"scan": {"breadth": 0.17, "need_breadth": 0.4, "d_need_basis": -0.002, "rows": [{"coin": "BTC", "basis": -0.0025}]}},
            "markets": {"fng": {"value": 55, "label": "жадность", "week": 61}},
            "funding": {"table": [{"symbol": "BTC", "avg7": 0.0001}]}}


def test_outlook_band_and_base_rate_on_synthetic_history():
    closes = _walk()
    labels = ol.regime_labels(closes)
    assert labels and set(labels.values()) <= {"BULL", "BEAR", "MIXED"}
    sig = ol.sigmas(closes)
    price = closes[max(closes)]
    widths = []
    for N in (1, 7, 30):
        fit = ol.band_fit(closes, N, sig)
        assert fit and fit["k"] > 0 and fit["check"] and 0 <= fit["check"]["coverage"] <= 1
        # in sample the band holds about 2 outcomes of 3 by construction
        inside = sum(1 for d in sig if d + N in closes and abs(math.log(closes[d + N] / closes[d])) <= fit["k"] * sig[d] * math.sqrt(N))
        total = sum(1 for d in sig if d + N in closes)
        assert abs(inside / total - 2 / 3) < 0.02
        r = ol.price_range(price, sig[max(sig)], N, fit)
        assert r["lo"] < price < r["hi"] and r["pm_pct"] > 0
        widths.append(r["pm_pct"])
        for bull in (True, False, None):
            br = ol.base_rate(closes, labels, N, bull)
            assert br and 0 <= br["p"] <= 1 and br["n"] > 0 and br["n_independent"] == max(1, br["n"] // N)
            assert 0 <= br["ci95"][0] <= br["p"] <= br["ci95"][1] <= 1
            assert br["verdict"] in ("coin", "up", "down") and br["period_ru"].count("–") == 1
    assert widths[0] < widths[1] < widths[2]


def test_outlook_regime_labels_follow_the_panel_rule():
    up = {18000 + i: 100 * 1.002 ** i for i in range(400)}
    dn = {18000 + i: 100 * 0.998 ** i for i in range(400)}
    assert ol.regime_labels(up)[18399] == "BULL" and ol.regime_labels(dn)[18399] == "BEAR"
    assert 18198 not in ol.regime_labels(up)          # 200 closes needed


def test_outlook_build_horizons_drivers_and_texts():
    hist = {"BTC": _walk(seed=3), "ETH": _walk(seed=4, start=1500.0)}
    now = (max(hist["BTC"]) + 1) * DAY + 15 * 3600_000
    doc = ol.build(_ctx(hist, now), hist, now)
    h = doc["horizons"]
    assert set(h) == {"day", "week", "month"} and "ETH" in doc["others"] and doc["regime"]["btc_bull"] is True
    for k, x in h.items():
        r, b = x["range"], x["base_rate_up"]
        assert r["lo"] < doc["price"] < r["hi"] and b["regime"] == "BULL" and 0 <= b["p"] <= 1 and b["n"] > 0
        assert 1 <= len(x["drivers"]) <= 5 and all(d["text_ru"] for d in x["drivers"])
        n_sent = len([s for s in x["text_ru"].split(". ") if s.strip()])
        assert 2 <= n_sent <= 4 and any(ch.isdigit() for ch in x["text_ru"])
    ab = h["day"]["forecast_ab"]
    assert ab["n"] == 243 and ab["side"] == "up" and "51%" in h["day"]["text_ru"] and "монетка" in h["day"]["text_ru"]
    kinds = lambda k: [d["kind"] for d in h[k]["drivers"]]  # noqa: E731
    names = lambda k: [d.get("name") for d in h[k]["drivers"] if d["kind"] == "calendar"]  # noqa: E731
    assert names("day") == [None] and "ФРС: решение по ставке" in names("week")          # 3 days away: week yes, day no
    assert "Инфляция в США (CPI)" in names("month") and "ВВП США" not in names("month")   # month: importance ≥ 2 only
    assert "macro" in kinds("day") and "macro" not in kinds("month") and "basis" in kinds("day")
    assert "funding" not in kinds("day")                                                 # 0.01 % / 8 h is ordinary
    assert doc["params_ru"].startswith("Что используем") and "90 дней" in doc["params_ru"]
    assert doc["summary_ru"].startswith("BTC ")
    # no calibration / calendar → no A/B, no calendar line, still a valid outlook
    doc2 = ol.build({"terminal": _ctx(hist, now)["terminal"]}, hist, now)
    assert doc2["horizons"]["day"]["forecast_ab"] is None and all(d["kind"] != "calendar" for d in doc2["horizons"]["week"]["drivers"])


def test_outlook_write_merges_seed_and_terminal_and_persists():
    d = tempfile.mkdtemp()
    hist = _walk(n=700, day0=20000)
    seed_days = sorted(hist)[:650]
    json.dump({"symbols": {"BTC": {"start_day": seed_days[0], "closes": [hist[x] for x in seed_days]}}}, open(os.path.join(d, "seed.json"), "w"))
    last = max(hist)
    asof = (last + 1) * DAY + 5 * 3600_000                       # 05:00 UTC of the next day: day `last` is complete
    c1d = [[(20649 * DAY) + 14 * 3600_000, 1, 1, 1, 999.0, 1]]   # partial first bar (starts at 14:00) — must be skipped
    c1d += [[x * DAY, hist[x], hist[x], hist[x], hist[x], 1] for x in sorted(hist)[650:]]
    c1d += [[(last + 1) * DAY, 1, 1, 1, 12345.0, 1]]              # today's forming bar — must be skipped
    json.dump({"asof": asof, "coins": [{"symbol": "BTC-USDT", "price": hist[last], "candles_1d": c1d, "regime": {"long": "BULL"}}],
               "portfolio": {"btc_bull": True}}, open(os.path.join(d, "terminal.json"), "w"))
    conn = sqlite3.connect(":memory:")
    doc = ol.write(d, conn, now_ms=asof, seed_path=os.path.join(d, "seed.json"), local_path=os.path.join(d, "nope.json"))
    assert os.path.exists(os.path.join(d, "outlook.json"))
    rows = dict(conn.execute("SELECT day, close FROM outlook_daily WHERE symbol='BTC'").fetchall())
    assert len(rows) == 50 and last in rows and (last + 1) not in rows and 20649 not in rows
    assert doc["history"]["days"] == 700 and doc["history"]["to"] == ol._day_iso(last)
    assert doc["horizons"]["week"]["range"]["lo"] < hist[last] < doc["horizons"]["week"]["range"]["hi"]
