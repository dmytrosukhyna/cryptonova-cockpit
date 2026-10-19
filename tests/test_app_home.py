import json
import os
import sqlite3

from cnq.agents import app_home


def _w(d, name, obj):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(obj, f)


def test_app_home_numbers(tmp_path):
    d = str(tmp_path)
    t0 = 1792300000000
    hist = lambda k: [{"time": t0, "equity": 10000.0}, {"time": t0 + 60000, "equity": 10000.0 + 100 * k}]
    prof = {p: {"equity": 10000.0, "open_pnl_usd": 100.0 * k, "equity_history": hist(k), "open": [], "closed": [], "stats": {}}
            for p, k in (("cons", 0.5), ("bal", 1.0), ("aggr", 1.5))}
    prof["bal"]["open"] = [{"symbol": "ARB", "side": "LONG", "entry": 0.2, "price": 0.22, "stop": 0.18, "tps": [0.3],
                            "open_pnl_usd": 100.0, "kind": "CLIMAX_LONG"}]
    _w(d, "paper.json", {"since": t0, "profiles": prof})
    _w(d, "app_portfolio.json", {"index": 99.0, "cash_pct": 15, "history": [{"t": t0, "v": 100.0}, {"t": t0 + 60000, "v": 99.0}],
                                 "positions": [{"symbol": "BTC", "weight": 0.5, "entry": 100, "price": 110, "kind_ru": "ядро"},
                                               {"symbol": "PENGU", "weight": 0.35, "entry": 1, "price": 0.9, "kind_ru": "мем"}],
                                 "recent_actions": [{"time": t0, "symbol": "SOL", "action": "TRIM", "why_ru": "лишнее продали"}]})
    _w(d, "markets.json", {"top": [{"symbol": "BTC", "price": 81000, "chg24": 1.234}], "fng": {"value": 59, "label": "жадность"},
                           "btc_bull": True})
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=t0 + 120000)
    assert doc["futures"]["usd"] == 100.0                      # the average of 50 / 100 / 150
    assert doc["spot"]["usd"] == -100.0                        # index 99 → −1 % of $10 000
    assert doc["total"]["usd"] == 0.0
    assert doc["futures"]["open"][0]["pct"] == 10.0 and doc["futures"]["open"][0]["target"] == 0.3
    assert doc["market"]["regime"] == "рост" and doc["market"]["btc"]["chg24"] == 1.23
    names = [g["name"] for g in doc["spot"]["alloc"]]
    assert names[0] == "Биткоин" and "мем-монеты" in names and names[-1] == "доллары"
    assert doc["spot"]["movers"][0] == {"sym": "BTC", "pct": 10.0}
    assert os.path.exists(os.path.join(d, "app_home.json"))
