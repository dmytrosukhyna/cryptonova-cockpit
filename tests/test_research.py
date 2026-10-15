import sqlite3
from cnq.agents import research as R


def test_card_rating_targets_and_scenarios():
    hl = {"spot": {"HYPE": {}}, "perps": ["BTC", "HYPE"]}
    btc = {"symbol": "BTC", "name": "Bitcoin", "id": "bitcoin", "rank": 1, "price": 80000.0, "mcap": 1.6e12, "fdv": 1.7e12, "volume": 4e10,
           "chg30": 5, "chg200": 30, "ath_chg": -20, "ath": 100000.0, "circ": 19.9e6, "total": 21e6}
    c = R.build_card(btc, {"updated": 1, "categories": ["Layer 1"], "age_days": 5000, "community": {"twitter": 7e6, "reddit": 6e6}, "developer": {"commits_4w": 400, "stars": 80000}}, hl, True)
    assert c["risk"] == "cons" and 60 <= c["rating"] <= 100 and c["stop_pct"] is None and c["scenarios"]["years"][10]["base"] > 80000
    meme = {"symbol": "WOW", "name": "Wow", "id": "wow", "rank": 150, "price": 0.5, "mcap": 2e8, "fdv": 4e8, "volume": 5e7, "chg30": 300, "chg200": None, "ath_chg": -40, "ath": 0.8, "circ": 4e8, "total": 8e8}
    m = R.build_card(meme, {"updated": 1, "categories": ["Meme"], "age_days": 90, "community": {}, "developer": {}}, hl, True)
    assert m["risk"] == "high" and m["stop_pct"] == -0.3 and m["targets"][0]["price"] == 1.0 and m["scenarios"] is None and m["dilution"] == 50
    usdt = R.build_card({"symbol": "USDT", "name": "Tether", "id": "tether", "rank": 3, "price": 1, "mcap": 1e11, "fdv": 1e11, "volume": 1e11, "stable": True}, None, hl, True)
    assert usdt["rating"] == 0


def test_research_store_roundtrip():
    conn = sqlite3.connect(":memory:")
    r = R.Research(conn)
    assert r.details("x") is None
    conn.execute("INSERT INTO coin_cards VALUES(?,?,?,?)", ("x", "X", 1, '{"updated":1,"categories":[]}'))
    assert r.details("x")["updated"] == 1
