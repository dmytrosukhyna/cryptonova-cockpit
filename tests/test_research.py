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


def test_missing_team_and_community_data_is_no_data_not_zero():
    """11.11: CoinGecko returns null developer / community numbers → those factors are left out of the rating (re-normalised), not 0"""
    from cnq.agents import research as R
    m = {"symbol": "MORPHO", "name": "Morpho", "id": "morpho", "rank": 60, "price": 2.0, "mcap": 1.4e9, "fdv": 2.0e9, "volume": 5e7,
         "chg30": -18, "chg200": 9, "ath_chg": -51, "circ": 1, "total": 1.4}
    nul = {"developer": {"stars": None, "forks": None, "commits_4w": None, "prs": None},
           "community": {"twitter": None, "reddit": None, "telegram": None}, "age_days": None, "categories": ["Decentralized Finance (DeFi)"]}
    c = R.build_card(m, nul, {"spot": {}, "perps": ["MORPHO"]}, True)
    assert set(c["factors"]) == {"economics", "market", "narrative"} and c["factors_nodata"] == ["team", "community"]
    W = (0.25, 0.20, 0.15)                                       # mod weights of economics, market, narrative
    f = c["factors"]
    assert abs(c["rating"] - (W[0] * f["economics"] + W[1] * f["market"] + W[2] * f["narrative"]) / sum(W)) <= 1
    full = R.build_card(m, {**nul, "developer": {"commits_4w": 100, "stars": 1000}, "community": {"twitter": 1e6, "reddit": None},
                            "age_days": 1000}, {"spot": {}, "perps": []}, True)
    assert full["factors_nodata"] == [] and full["factors"]["team"] > 0 and full["factors"]["community"] > 0
