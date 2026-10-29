"""22.10: «Если покупать сегодня» for the app — the panel's buyNow features on the server (cnq/agents/buy_now.py)"""
import json
import os
import sqlite3

from cnq.agents import app_home, buy_now

H4, D1 = 4 * 3_600_000, 86_400_000


def _bars(closes, start, step):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append([start + i * step, prev, max(prev, c) * 1.002, min(prev, c) * 0.998, c, 1000.0])
        prev = c
    return out


def _support_series(n=200, floor=100.0):
    """4-hour closes that bounce off the same floor 4 times, then sit just above it after a slide (RSI low)"""
    cs = []
    for k in range(4):
        cs += [floor + 12 - i * 3 for i in range(5)] + [floor - 0.4] + [floor + 3 + i * 2 for i in range(6)]
    cs += [floor + 14] * (n - len(cs) - 12) + [floor + 14 - i * 1.1 for i in range(12)]
    return cs


def test_buy_now_scores_like_the_panel(tmp_path):
    d = str(tmp_path)
    with open(os.path.join(d, "research.json"), "w", encoding="utf-8") as f:
        json.dump({"cards": [{"symbol": "LINK", "rating": 72}]}, f)
    with open(os.path.join(d, "whales.json"), "w", encoding="utf-8") as f:
        json.dump({"coins": [{"coin": "LINK", "lean": "LONG"}]}, f)
    up = [50 * 1.004 ** i for i in range(220)]                 # daily: well above its 200-day average
    flat = [100.0] * 220

    def candles(sym, interval, limit):
        if interval == "1d":
            return _bars(up if sym == "LINK" else flat, 0, D1)
        return _bars(_support_series() if sym == "LINK" else [100.0 + (i % 3) for i in range(200)], 0, H4)

    doc = buy_now.build(d, now=1_800_000_000_000, candles=candles, coins=["LINK", "XRP", "ADA"])
    assert doc["available"] and doc["checked"] == 3
    assert [x["sym"] for x in doc["items"]] == ["LINK"]
    it = doc["items"][0]
    keys = [w["k"] for w in it["why"]]
    assert keys[0] == "sma200" and "quality" in keys and "whales" in keys
    assert it["score"] == 20 + 18 + 10 + (30 if "support" in keys else 0) + (15 if "rsi" in keys else 0)
    assert it["grade"] in ("strong", "good", "possible")


def test_buy_now_needs_candles_and_never_invents():
    assert buy_now.score("X", [[0, 1, 1, 1, 1, 1]] * 10, [], None, None) is None    # < 60 four-hour bars
    flat = [[i, 100.0, 100.5, 99.5, 100.0, 1.0] for i in range(120)]
    assert buy_now.score("X", flat, [], None, None) is None                           # nothing true → no points


def test_app_home_carries_buy_now_in_every_language(tmp_path):
    d = str(tmp_path)
    now = 1_800_000_000_000
    with open(os.path.join(d, "buy_now.json"), "w", encoding="utf-8") as f:
        json.dump({"asof": now - 60_000, "available": True, "checked": 40, "items": [
            {"sym": "LINK", "price": 12.7, "chg24": 3.3, "score": 73, "grade": "good",
             "why": [{"k": "sma200"}, {"k": "support", "p": 12.31, "n": 4}, {"k": "rsi", "v": 38}, {"k": "bogus"}]}]}, f)
    with open(os.path.join(d, "paper.json"), "w", encoding="utf-8") as f:
        json.dump({"profiles": {}}, f)
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=now)
    b = doc["buy_now"]
    assert b["checked"] == 40 and b["items"][0]["grade_text"] == "хороший"
    assert b["items"][0]["why"] == ["Тренд вверх: цена выше своей средней за 200 дней — долгий рост не сломан.",
                                    "Цена у уровня поддержки 12,31: 4 раза от него уже отскакивала — здесь покупатели обычно активны.",
                                    "Без перегрева: индикатор RSI 38 — после снижения монета не перекуплена."]
    with open(os.path.join(d, "app_home.en.json"), encoding="utf-8") as f:
        en = json.load(f)
    assert en["buy_now"]["items"][0]["why"][:2] == ["Uptrend: the price is above its 200-day average — the long rise is intact.",
                                                   "The price is at the support level 12.31: it has bounced off it 4 times — buyers are usually active here."]
    # stale (> 3 h) → hidden
    assert app_home.run(sqlite3.connect(":memory:"), d, now=now + 4 * 3_600_000)["buy_now"] is None
