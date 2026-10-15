import sqlite3
from cnq.agents import whales as W


def test_whales_aggregate_and_changes(monkeypatch=None):
    conn = sqlite3.connect(":memory:")
    lb = [{"addr": "0xa", "name": "A", "value": 2e6, "roi_m": 0.5, "pnl_m": 1e6, "pnl_all": 5e6}, {"addr": "0xb", "name": "", "value": 3e6, "roi_m": 0.4, "pnl_m": 1e6, "pnl_all": 2e6}]
    book = {"0xa": [{"coin": "ETH", "side": "LONG", "size": 100, "notional": 230000, "entry": 2300, "upnl": 0, "lev": 5, "liq": None}],
            "0xb": [{"coin": "ETH", "side": "LONG", "size": 50, "notional": 115000, "entry": 2310, "upnl": 0, "lev": 3, "liq": None},
                    {"coin": "BTC", "side": "SHORT", "size": 1, "notional": 80000, "entry": 81000, "upnl": 0, "lev": 2, "liq": None}]}
    W.leaders = lambda n=20: lb
    W.positions = lambda addr: book[addr]
    W.time.sleep = lambda s: None
    d = W.Whales(conn).run(1_700_000_000_000)
    eth = next(c for c in d["coins"] if c["coin"] == "ETH")
    assert eth["long_n"] == 2 and eth["lean"] == "LONG" and d["summary"]["bias"] == "LONG" and d["changes"] == []
    book["0xb"] = [{"coin": "BTC", "side": "LONG", "size": 1, "notional": 80000, "entry": 81000, "upnl": 0, "lev": 2, "liq": None}]
    d2 = W.Whales(conn).run(1_700_000_000_000 + 1800_000)
    kinds = {(c["coin"], c["kind"]) for c in d2["changes"]}
    assert ("BTC", "FLIP") in kinds and ("ETH", "CLOSE") in kinds and d2["whales"][1]["name"].startswith("лидер")
