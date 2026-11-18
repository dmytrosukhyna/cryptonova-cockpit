"""09.11 (owner 21:40): «Под наблюдением владельца» — ADA, AVAX, LIT: levels, walls with their age, funding / OI, traders, smart
wallets, unlocks, sourced news; alerts for $1M bid walls, average crossings, new longs and smart buys. Information only."""
import json
import os
import shutil
import tempfile

from cnq.agents import owner_watch as W

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = 1794250000000
H = 3600_000


def _daily(px_last):
    rows = [[NOW - (220 - i) * 24 * H, 1.0] for i in range(219)] + [[NOW - 24 * H, 0.98], [NOW, px_last]]
    return rows


def _raw(px=1.05, wall_usd=1_500_000, wall_px=1.0):
    q = wall_usd / wall_px
    book = {"venue": "OKX спот", "bids": [(px * 0.999, 1000.0), (wall_px, q), (px * 0.9, 9e9)],   # −14 %: outside ±5 %
            "asks": [(px * 1.001, 1000.0), (px * 1.03, 400_000.0)]}
    return {"errors": [], "daily": _daily(px), "books": [book], "funding_okx": 0.0001,
            "oi": [[NOW - 25 * H, 100e6], [NOW - 24 * H, 100e6], [NOW, 110e6]]}


def _web():
    d = tempfile.mkdtemp()
    json.dump({"whales": [{"name": "Trader A", "positions": [{"coin": "AVAX", "side": "LONG", "notional": 2_500_000, "entry": 8.4}]}]},
              open(os.path.join(d, "whale_watch.json"), "w"))
    json.dump({"watch": {"tokens": [{"symbol": "ADA", "status": "накопление", "chain": "eth", "price": 1.0,
                                     "events": [{"t": NOW - 2 * H, "kind": "buyer", "text": "покупает: Jump Trading"}]}]}},
              open(os.path.join(d, "smart_money.json"), "w"))
    json.dump({"news": {"impacts": [{"title": "NYSE выбрала Avalanche", "source": "coindesk", "link": "https://c/1", "asset": "AVAX", "created": NOW - H},
                                    {"title": "Avalanche без источника", "source": None, "link": None, "asset": "AVAX", "created": NOW}]}},
              open(os.path.join(d, "decision.json"), "w"))
    return d


def test_card_levels_walls_sources_and_alerts():
    d = _web()
    raws = {c: _raw() for c in W.COINS}
    doc = W.build(d, raws, NOW)
    json.dump(doc, open(os.path.join(d, "owner_watch.json"), "w"))
    ada = next(c for c in doc["coins"] if c["coin"] == "ADA")
    assert round(ada["chg24"], 1) == 7.1 and round(ada["dist200"], 1) == 5.0 and ada["sma20"] is not None
    bw = ada["bid_walls"][0]
    assert bw["usd"] == 1_500_000 and bw["since"] == NOW and -5 < bw["dist"] < 0 and all(w["dist"] > -5 for w in ada["bid_walls"])
    assert ada["ask_walls"] and ada["funding"] == {"okx": 0.01} and ada["oi_chg24"] == 10.0
    avax = next(c for c in doc["coins"] if c["coin"] == "AVAX")
    assert [n["source"] for n in avax["news"]] == ["CoinDesk"] and avax["traders"][0]["usd"] == 2_500_000
    lit = next(c for c in doc["coins"] if c["coin"] == "LIT")
    assert lit["unlocks"][0]["date"] == "2026-12-27" and lit["unlocks"][0]["link"].startswith("https://tokenomist.ai/")
    assert doc["alerts"] == []                                   # the first run sets the baseline: nothing «appeared» yet
    # next run: ADA wall gone, a new $2M wall on AVAX, ADA price under its 20-day average, a new smart buy, a new long on LIT
    raws2 = {"ADA": _raw(px=0.97, wall_usd=200_000, wall_px=0.95), "AVAX": _raw(wall_usd=2_000_000, wall_px=1.02), "LIT": _raw()}
    json.dump({"whales": [{"name": "Trader A", "positions": [{"coin": "AVAX", "side": "LONG", "notional": 2_500_000},
                                                             {"coin": "LIT", "side": "LONG", "notional": 300_000}]}]},
              open(os.path.join(d, "whale_watch.json"), "w"))
    json.dump({"watch": {"tokens": [{"symbol": "ADA", "status": "накопление",
                                     "events": [{"t": NOW + H, "kind": "buyer", "text": "снова покупает: Jump Trading"}]}]}},
              open(os.path.join(d, "smart_money.json"), "w"))
    doc2 = W.build(d, raws2, NOW + 2 * H)
    kinds = {(a["coin"], a["kind"]) for a in doc2["alerts"]}
    assert {("ADA", "wall_gone"), ("AVAX", "wall_new"), ("ADA", "level"), ("ADA", "smart"), ("LIT", "whale")} <= kinds
    assert not any(k == ("AVAX", "whale") for k in kinds)        # an old position is not news
    txt = " ".join(a["text"] for a in doc2["alerts"])
    assert "купить" not in txt.lower() and "продать" not in txt.lower()
    json.dump(doc2, open(os.path.join(d, "owner_watch.json"), "w"))
    line = W.owner_line(d, NOW + 3 * H)
    assert line.startswith("Под наблюдением: ") and len(line) < 400
    shutil.rmtree(d)


def test_fetch_survives_a_failing_exchange():
    def http(url):
        raise OSError("blocked")
    raw = W.fetch("ADA", http=http)
    assert raw["books"] == [] and len(raw["errors"]) >= 3
    doc = W.build(tempfile.mkdtemp(), {"ADA": raw}, NOW)
    assert next(c for c in doc["coins"] if c["coin"] == "ADA")["errors"]


def test_owner_only_cards_on_the_panel():
    spot = open(os.path.join(ROOT, "web", "vip", "spot.js"), encoding="utf-8").read()
    ov = open(os.path.join(ROOT, "web", "vip", "overview.js"), encoding="utf-8").read()
    html = open(os.path.join(ROOT, "web", "vip.html"), encoding="utf-8").read()
    assert "V.ownerWatchCard && V.auth.owner() ? await V.ownerWatchCard()" in spot and "${OW}\n        ${summary(M, X)}" in spot
    assert "V.ownerHealthCard && V.auth.owner() ? await V.ownerHealthCard()" in ov and "return `${HL}<div class=\"ph\">" in ov
    assert '<script src="vip/watch.js"></script>' in html and '<script src="vip/health.js"></script>' in html
    yml = open(os.path.join(ROOT, ".github", "workflows", "site.yml"), encoding="utf-8").read()
    assert "python -m cnq.agents.owner_watch" in yml and "python -m cnq.agents.owner_health" in yml and "web/owner_watch.json" in yml
