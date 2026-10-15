import json, os, sqlite3, tempfile, datetime as dt
from cnq.agents import channel


def _web(tmp):
    json.dump({"coins": [{"symbol": "BTC-USDT", "price": 81000, "chg24": -1.2, "regime": {"long": "BULL", "mid": "SHORT", "short": "DOWN", "atr_pct": 2.5},
                           "levels": [{"lo": 78500, "hi": 79000, "center": 78750, "touches": 4, "status": "ACTIVE", "dist_atr": 0.8},
                                      {"lo": 83000, "hi": 83400, "center": 83200, "touches": 3, "status": "ACTIVE", "dist_atr": 1.1}]}]},
              open(os.path.join(tmp, "terminal.json"), "w"))
    json.dump({"global": {"total_mcap": 2.7e12, "mcap_chg24": -5.9, "btc_dom": 60.0}, "fng": {"value": 53, "label": "нейтрально", "yesterday": 62, "week": 59},
               "top": [{"symbol": "BTC", "price": 80984, "chg24": -2.9, "chg7": -2.5, "ath_chg": -36, "rank": 1, "volume": 4e10},
                       {"symbol": "USDGO", "price": 1, "chg24": 0.0, "rank": 50, "volume": 1e9}, {"symbol": "SOL", "price": 102, "chg24": -4.0, "rank": 5, "volume": 2e9}]},
              open(os.path.join(tmp, "markets.json"), "w"))
    json.dump({"open": {"BTC": {"p_up": 0.22, "up": 82331, "dn": 80141}}, "stats": {"n": 5, "hit_rate": 0.0, "brier": 0.61}}, open(os.path.join(tmp, "calibration.json"), "w"))
    json.dump({"news": {"impacts": [{"title": "Mock one", "asset": "BTC", "category": "hack", "direction": "bear", "magnitude": 4, "reasoning": "mock: keyword rule"},
                                    {"title": "ETF inflows hit record", "asset": "BTC", "category": "etf_flows", "direction": "bull", "magnitude": 4, "confidence": .7,
                                     "reasoning": "Фонды обязаны докупать под новые деньги.", "horizon": "days", "confirm": "закрепление выше 83 400"}]}},
              open(os.path.join(tmp, "decision.json"), "w"))
    json.dump({"available": True, "summary": {"n_whales": 20, "total_long_usd": 5.3e7, "total_short_usd": 2.7e7, "bias": "LONG"}, "coins": [], "changes": []}, open(os.path.join(tmp, "whales.json"), "w"))
    json.dump({"profiles": {"cons": {"pnl_pct": 0.0, "open": []}, "bal": {"pnl_pct": 1.25, "open": [1, 2]}}, "closed": []}, open(os.path.join(tmp, "paper.json"), "w"))


def test_channel_posts_and_once_per_slot():
    tmp = tempfile.mkdtemp(); _web(tmp)
    conn = sqlite3.connect(":memory:"); ch = channel.Channel(conn, tmp)
    m = ch.post("morning")
    assert "Сценарий A (78%): к поддержке 80 141" in m and "$2.70 трлн" in m and "<b>" in m and "http" not in m
    mid = ch.post("midday")
    assert "Mock one" not in mid and "ETF inflows" in mid and "Фонды обязаны" in mid
    ev = ch.post("evening")
    assert "USDGO" not in ev and "SOL -4.0%" in ev and "проверено 5" in ev
    sent = []
    now = dt.datetime(2026, 10, 15, 8, 30, tzinfo=channel.KYIV)
    r = ch.run(lambda t: sent.append(t) or True, now=now)
    assert r["sent"] and r["n"] == 2 and "ЗАПУСК" in sent[0]      # intro first, then the morning post
    r2 = ch.run(lambda t: sent.append(t) or True, now=now)
    assert not r2["sent"] and r2["why"] == "already posted"
    r3 = ch.run(lambda t: sent.append(t) or True, now=now.replace(hour=12))
    assert r3["slot"] is None
    # evening checks the morning scenario it stored
    ev2 = ch.run(lambda t: sent.append(t) or True, now=now.replace(hour=20))
    assert ev2["sent"] and "Утром ставили A (78%)" in sent[-1]


def test_polish_rejects_lost_numbers():
    conn = sqlite3.connect(":memory:"); ch = channel.Channel(conn, tempfile.mkdtemp())
    class L:
        def complete(self, s, u): return "Коротко: рынок падает."
    draft = "Биткоин <b>$80 984</b> упал на 2.9%."
    assert ch.polish(draft, L()) == draft
    class L2:
        def complete(self, s, u): return "Биткоин <b>$80 984</b> просел на 2.9% — ждём поддержку."
    assert "просел" in ch.polish(draft, L2())
