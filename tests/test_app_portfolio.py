import json
import sqlite3

from cnq.agents import app_portfolio as A


def test_app_portfolio_file(tmp_path):
    pf = {"portfolios": {"ideas": {"name": "CRYPTO NOVA портфель", "index": 101.5, "cash_pct": 15, "started": 1, "about": "x",
                                   "history": [{"time": 10, "v": 100.0}, {"time": 20, "v": 100.7}],
                                   "positions": [{"symbol": "ETH", "weight": 0.1, "entry": 2000, "status": "OPEN", "name": "Ethereum"},
                                                 {"symbol": "BTC", "weight": 0.4, "entry": 80000, "status": "OPEN"},
                                                 {"symbol": "DOGE", "weight": 0.05, "status": "CLOSED"}],
                                   "recent_actions": [{"time": 5, "action": "BUY", "symbol": "BTC", "price": 80000, "reason": "план"}]}}}
    (tmp_path / "portfolio.json").write_text(json.dumps(pf))
    (tmp_path / "markets.json").write_text(json.dumps({"top": [{"symbol": "BTC", "name": "Bitcoin", "price": 81000, "image": "img"}]}))
    conn = sqlite3.connect(":memory:")
    d = A.run(conn, str(tmp_path), now=30)
    assert [p["symbol"] for p in d["positions"]] == ["BTC", "ETH"]            # open only, biggest share first
    assert d["positions"][0]["price"] == 81000 and d["positions"][0]["name"] == "Bitcoin"
    assert [h["t"] for h in d["history"]] == [10, 20, 30] and d["history"][-1]["v"] == 101.5
    d2 = A.run(conn, str(tmp_path), now=40)                                   # the curve keeps growing between runs
    assert [h["t"] for h in d2["history"]] == [10, 20, 30, 40]
    assert json.loads((tmp_path / "app_portfolio.json").read_text())["available"] is True
