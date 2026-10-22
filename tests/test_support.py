import json
import os
import sqlite3

from cnq.agents import support as S

NOW = 1_792_600_000_000          # 2026-10-20
DAY = S.DAY


def _db(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE coin_cards(id TEXT PRIMARY KEY, symbol TEXT, updated INTEGER, data TEXT)")
    c.execute("INSERT INTO coin_cards VALUES('alpha','ALPHA',0,?)", (json.dumps({"categories": ["DeFi", "Paradigm Portfolio", "Andreessen Horowitz (a16z) Portfolio", "DWF Labs Portfolio"]}),))
    c.execute("INSERT INTO coin_cards VALUES('beta','BETA',0,?)", (json.dumps({"categories": ["Meme"]}),))
    c.execute("CREATE TABLE whale_snapshots(time INTEGER, addr TEXT, coin TEXT, side TEXT, notional REAL, entry REAL, lev REAL, PRIMARY KEY(time, addr, coin))")
    t0, t1 = NOW - 7 * DAY, NOW - H
    for a in ("0x1", "0x2", "0x3", "0x4"):
        c.execute("INSERT INTO whale_snapshots VALUES(?,?,?,?,?,?,?)", (t0, a, "BTC", "LONG", 1e6, 1, 1))
        c.execute("INSERT INTO whale_snapshots VALUES(?,?,?,?,?,?,?)", (t1, a, "BTC", "LONG", 1e6, 1, 1))
    for a in ("0x1", "0x2", "0x3"):      # three accounts open / grow ALPHA longs, one cuts
        c.execute("INSERT INTO whale_snapshots VALUES(?,?,?,?,?,?,?)", (t1, a, "ALPHA", "LONG", 600_000, 1, 1))
    c.execute("INSERT INTO whale_snapshots VALUES(?,?,?,?,?,?,?)", (t0, "0x4", "ALPHA", "LONG", 900_000, 1, 1))
    c.execute("INSERT INTO whale_snapshots VALUES(?,?,?,?,?,?,?)", (t1, "0x4", "ALPHA", "LONG", 100_000, 1, 1))
    c.execute("CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, title TEXT, summary TEXT, link TEXT, published INTEGER, received INTEGER, assets TEXT)")
    c.execute("INSERT INTO news VALUES('o','coindesk','old',NULL,'u',?,0,'[\"MARKET\"]')", (NOW - 60 * DAY,))
    c.execute("INSERT INTO news VALUES('p','theblock','Alpha partners with Visa for payments',NULL,'https://x/p',?,0,'[\"ALPHA\"]')", (NOW - 2 * DAY,))
    c.execute("INSERT INTO news VALUES('q','decrypt','Tether backs some startup',NULL,'https://x/q',?,0,'[\"BETA\"]')", (NOW - 2 * DAY,))
    c.commit()
    return c


H = S.H


def _fake_get(calls):
    def get(url, timeout=None):
        calls.append(url)
        if "public/instruments" in url:
            return {"data": [{"baseCcy": "ALPHA", "quoteCcy": "USDT", "state": "live", "listTime": str(NOW - 20 * DAY)},
                             {"baseCcy": "BTC", "quoteCcy": "USDT", "state": "live", "listTime": "1548133413000"}]}
        if "announcements" in url:
            return {"data": [{"details": [{"title": "OKX to list ALPHA (Alpha) for spot trading", "url": "https://okx/a", "pTime": str(NOW - 21 * DAY)}]}]}
        if "exchangeInfo" in url:
            return {"symbols": [{"baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING"},
                                {"baseAsset": "ALPHA", "quoteAsset": "USDT", "status": "TRADING"},
                                {"baseAsset": "BETA", "quoteAsset": "USDT", "status": "BREAK"}]}
        if "klines" in url:
            return [[NOW - 400 * DAY, "1", "1", "1", "1"]]
        if "coinbase" in url:
            return [{"base_currency": "BTC", "quote_currency": "USD", "status": "online"}]
        if "llama" in url:
            raise RuntimeError("HTTP Error 402: Payment Required")
        raise RuntimeError(url)
    return get


def _ideas():
    return {"asof": NOW, "rows": [{"symbol": "ALPHA", "status": "wait"}, {"symbol": "BETA", "status": "avoid"}],
            "statuses": {"ALPHA": {"status": "wait"}, "BETA": {"status": "avoid"}, "BTC": {"status": "buy"}, "NONE": {"status": "wait"}}}


def test_support_scores_evidence_and_no_status_change(tmp_path):
    web, data = tmp_path / "web", tmp_path / "data"
    os.makedirs(web); os.makedirs(data)
    (web / "research.json").write_text(json.dumps({"cards": [{"symbol": "ALPHA", "id": "alpha", "name": "Alpha"},
                                                             {"symbol": "BETA", "id": "beta", "name": "Beta"}]}), encoding="utf-8")
    (web / "coin_profiles.json").write_text(json.dumps({"coins": {"BTC": {
        "investors_ru": "Венчурных раундов не было. Крупнейшие держатели — спотовые ETF в США.", "partners_ru": "ETF BlackRock.",
        "sources": ["https://src/btc"]}}}, ensure_ascii=False), encoding="utf-8")
    _db(str(data / "agents.sqlite")).close()
    calls = []
    d = _ideas()
    before = json.dumps(d["statuses"], sort_keys=True)
    S.attach(d, str(web), get=_fake_get(calls), net=True)
    assert "support_error" not in d, d.get("support_error")
    assert json.dumps(d["statuses"], sort_keys=True) == before                     # information only: no status touched
    a = d["rows"][0]["support"]
    assert a["parts"]["funds"] == 30                                               # Paradigm, a16z, DWF → 3 tier-1
    txt = " | ".join(x["t"] for x in a["lines"])
    assert "фонды первого ряда" in txt and "Paradigm" in txt
    assert a["parts"]["whales"] == 15 and "нарастили 3, сократили 1" in txt         # 3 up − 1 down = 2 → 15
    assert "свежий листинг: OKX" in txt and "балл не даёт" in txt
    assert a["parts"]["partners"] == 24 and "партнёрство" in txt and "DWF Labs" in txt
    assert 0 <= a["score"] <= 100 and a["cov"] == 100
    b = d["rows"][1]["support"]
    assert b["parts"]["funds"] == 0 and b["parts"]["exch"] == 0                  # on no exchange list (Binance BREAK)
    assert "киты" in b["nodata"]                                                   # no tracked whale holds BETA → нет данных, not 0
    assert not any("startup" in x["t"] for x in b["lines"])                        # a headline that does not name the coin is ignored
    btc = d["support"]["BTC"]
    assert btc["score"] == 47 and btc["cov"] == 100 and any("ETF" in x["t"] for x in btc["lines"])   # 16 + 5 + 20 + 6
    assert d["support"]["NONE"]["score"] is None                                  # nothing known → no score, «мало данных»
    assert any("llama" in u for u in calls) and d["support_sources"]["llama_raises"]["ok"] is False
    # cached: a second run the same day makes no network calls
    n = len(calls)
    S.attach(_ideas(), str(web), get=_fake_get(calls), net=True, now=NOW + H)
    assert len(calls) == n
    c = sqlite3.connect(str(data / "agents.sqlite"))
    assert c.execute("SELECT COUNT(*) FROM support_hist").fetchone()[0] == 4


def test_support_never_breaks_and_offline(tmp_path):
    d = _ideas()
    S.attach(d, str(tmp_path / "web"))                                             # no db, no files → no network, no crash
    assert "support_error" not in d and d["rows"][0]["support"]["score"] is None
    d2 = {"rows": None}
    S.attach(d2, str(tmp_path / "web"))
    assert "support_error" in d2 or "support" in d2


def test_etf_sentence_rules():
    assert S._etf_trades("Спотовые ETF торгуются с 2024.")
    assert not S._etf_trades("Корпоративных партнёров и ETF нет.")
    assert not S._etf_trades("Grayscale подала заявку на ETF.")
