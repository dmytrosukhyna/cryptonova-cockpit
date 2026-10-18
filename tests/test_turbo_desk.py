"""«Турбо» demo accounts: the listing short ×4 and BTC ×2 while the regime says «рост». Synthetic files, no network."""
import json
import os
import sqlite3
import tempfile

from cnq.agents import turbo_desk as TD

T0 = 1_792_000_000_000
H = 3_600_000


def files(d, lst_eq=10_100.0, px=80_000.0, sma=70_000.0, r60=0.1):
    json.dump({"profiles": {"lst": {"equity": lst_eq, "start_equity": 10_000.0, "open": [{"symbol": "ABC", "qty": 10, "open_pnl_usd": 5}],
                                    "closed": [{"symbol": "XYZ", "net_usd": 20}], "stats": {"trades": 1, "winrate": 1.0, "avg_trade_pct": 4.0}}},
               "profile_list": []}, open(os.path.join(d, "paper.json"), "w"))
    json.dump({"coins": [{"symbol": "BTC-USDT", "price": px, "regime": {"sma200": sma, "r60": r60}}]}, open(os.path.join(d, "decision.json"), "w"))


def test_turbo_listing_mirror_is_four_times_the_base_and_btc_x2_follows_the_regime(tmp_path=None):
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    files(d)
    a = TD.run(conn, d, T0)
    assert a["lstx"]["equity_mtm"] == 10_400.0 and a["lstx"]["open"][0]["qty"] == 40 and a["lstx"]["closed"][0]["net_usd"] == 80
    assert a["btcx"]["in_market"] is True and "плечом 2" in a["btcx"]["events"][0] and a["btcx"]["target_month_pct"] == 70.0
    e0 = a["btcx"]["equity"]
    files(d, lst_eq=9_900.0, px=88_000.0)                          # BTC +10 % → the ×2 account about +20 %
    b = TD.run(conn, d, T0 + H)
    assert b["lstx"]["equity_mtm"] == 9_600.0 and abs(b["btcx"]["equity"] / e0 - 1.2) < 0.001
    files(d, px=88_000.0, sma=90_000.0)                             # regime off → out to dollars (fee on the switch)
    c = TD.run(conn, d, T0 + 2 * H)
    assert c["btcx"]["in_market"] is False and "доллары" in c["btcx"]["events"][0] and c["btcx"]["position_ru"] == "в долларах под 4%"
    files(d, px=60_000.0, sma=90_000.0)                             # out of the market: a BTC crash does not touch it
    e = TD.run(conn, d, T0 + 3 * H)["btcx"]["equity"]
    assert abs(e / c["btcx"]["equity"] - 1) < 1e-4
    p = json.load(open(os.path.join(d, "paper.json")))
    assert {"lstx", "btcx", "lst"} <= set(p["profiles"]) and {x["key"] for x in p["profile_list"]} == {"lstx", "btcx"}
