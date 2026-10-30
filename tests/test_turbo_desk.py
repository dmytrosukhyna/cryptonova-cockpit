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
    files(d, px=88_000.0, sma=90_000.0)                             # regime off intraday → no switch until the daily close
    assert TD.run(conn, d, T0 + 2 * H)["btcx"]["in_market"] is True
    c = TD.run(conn, d, T0 + 24 * H)                                 # next UTC day → out to dollars (fee on the switch)
    assert c["btcx"]["in_market"] is False and "доллары" in c["btcx"]["events"][0] and c["btcx"]["position_ru"] == "в долларах под 4%"
    files(d, px=60_000.0, sma=90_000.0)                             # out of the market: a BTC crash does not touch it
    e = TD.run(conn, d, T0 + 25 * H)["btcx"]["equity"]
    assert abs(e / c["btcx"]["equity"] - 1) < 1e-4
    p = json.load(open(os.path.join(d, "paper.json")))
    assert {"lstx", "btcx", "lst"} <= set(p["profiles"]) and {x["key"] for x in p["profile_list"]} == {"lstx", "btcx"}


def test_btc_x2_marks_to_the_fresher_engine_candle_not_the_stale_decision_price():
    d = tempfile.mkdtemp()
    files(d)                                                          # decision.json: BTC 80 000, no `time` → any candle is newer
    db = os.path.join(d, "cnq.sqlite")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE candles(symbol TEXT, interval TEXT, open_time INTEGER, open REAL, high REAL, low REAL, close REAL, "
              "volume REAL, close_time INTEGER)")
    c.execute("INSERT INTO candles VALUES('BTC-USDT','15m',?,0,0,0,84000,0,?)", (T0, T0 + 899_999))
    c.commit(); c.close()
    px, bull = TD.btc_state(d, db)
    assert px == 84_000.0 and bull is True
    json.dump({"time": T0 + H, "coins": [{"symbol": "BTC-USDT", "price": 80_000.0, "regime": {"sma200": 70_000.0, "r60": 0.1}}]},
              open(os.path.join(d, "decision.json"), "w"))
    assert TD.btc_state(d, db)[0] == 80_000.0                        # the candle is older than decision.json → keep decision
    assert TD.btc_state(d, os.path.join(d, "missing.sqlite"))[0] == 80_000.0


def test_btc_x2_decides_once_a_day_and_does_not_flip_on_the_threshold():
    """30.10: BTC sat on the 60-day threshold → the per-minute regime flipped the desk out and back in. Now: once per UTC day."""
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    day0 = (T0 // TD.DAY + 1) * TD.DAY                               # 00:00 UTC
    files(d, r60=0.001)
    assert TD.run(conn, d, day0 + 60_000)["btcx"]["in_market"] is True
    flips = 0
    for i in range(1, 600):                                          # ten hours of minutes, r60 swinging around zero
        files(d, r60=0.001 if i % 2 else -0.001)
        flips += bool(TD.run(conn, d, day0 + 60_000 * (i + 1))["btcx"]["events"])
    assert flips == 0
    files(d, r60=-0.01)                                              # next day's close below the threshold → one exit
    out = TD.run(conn, d, day0 + TD.DAY + 60_000)["btcx"]
    assert out["in_market"] is False and "доллары" in out["events"][0]
    files(d, r60=0.01)
    assert TD.run(conn, d, day0 + TD.DAY + 120_000)["btcx"]["in_market"] is False     # same day: stays in dollars


def test_btc_x2_old_state_keeps_its_position_until_the_next_close():
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    TD.run(conn, d, T0)                                              # no files → no price → nothing
    conn.execute("CREATE TABLE IF NOT EXISTS turbo_state(k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("INSERT OR REPLACE INTO turbo_state VALUES('btcx', ?)", (json.dumps({"equity": 9000.0, "px": 80_000.0, "on": True, "t": T0}),))
    files(d, sma=90_000.0)                                           # regime off, but the old state has no day yet
    assert TD.run(conn, d, T0 + 60_000)["btcx"]["in_market"] is True
    assert TD.run(conn, d, T0 + TD.DAY)["btcx"]["in_market"] is False
