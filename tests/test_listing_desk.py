import json
import os
import sqlite3
import tempfile

from cnq.agents import listing_desk as L

H = 3_600_000
T0 = 1_792_000_000_000


def mk(**coins):
    return lambda: {s: {"launch": None, "price": p, "high24": p, "funding": f} for s, (p, f) in coins.items()}


def test_first_run_only_remembers_then_shorts_a_new_listing_after_4h():
    conn = sqlite3.connect(":memory:")
    a = L.step(conn, T0, mk(**{"BTC-USDT": (100_000.0, 0.0001)}))
    assert a["open"] == [] and a["equity"] == 10_000 and "Запомнили 1" in a["events"][0]
    b = L.step(conn, T0 + H, mk(**{"BTC-USDT": (100_000.0, 0.0001), "NEW-USDT": (1.0, -0.001)}))
    assert b["open"] == [] and "Новый фьючерс" in b["events"][0]
    c = L.step(conn, T0 + 3 * H, mk(**{"BTC-USDT": (100_000.0, 0.0001), "NEW-USDT": (1.2, -0.001)}))
    assert c["open"] == []                                             # only 2 h after the listing
    d = L.step(conn, T0 + 5 * H + 1, mk(**{"BTC-USDT": (100_000.0, 0.0001), "NEW-USDT": (1.0, -0.001)}))
    t = d["open"][0]
    assert t["symbol"] == "NEW" and t["side"] == "SHORT" and t["entry"] == 1.0 and abs(t["stop"] - 1.3) < 1e-9 and abs(t["qty"] - 500) < 1e-6
    # price falls 20 % and 14 days pass → closed with the fall, minus fees, minus the funding a short paid (rate −0.1 %)
    e = L.step(conn, T0 + 5 * H + 1 + 14 * 86_400_000, mk(**{"BTC-USDT": (100_000.0, 0.0001), "NEW-USDT": (0.8, -0.001)}))
    cl = e["closed"][0]
    # +$100 from the fall − $0.45 fees − $21 funding (500 × 0.1 % × 3 a day × 14 days)
    assert e["open"] == [] and cl["exit_kind"] == "14 дней" and abs(cl["net_usd"] - 78.55) < 0.01 and abs(cl["funding_usd"] + 21) < 0.01


def test_stop_at_30_percent_and_stale_listing_skipped():
    conn = sqlite3.connect(":memory:")
    L.step(conn, T0, mk(**{"BTC-USDT": (1.0, 0.0)}))
    L.step(conn, T0 + H, mk(**{"BTC-USDT": (1.0, 0.0), "PUMP-USDT": (2.0, 0.0)}))
    L.step(conn, T0 + 6 * H, mk(**{"BTC-USDT": (1.0, 0.0), "PUMP-USDT": (2.0, 0.0)}))
    s = L.step(conn, T0 + 7 * H, mk(**{"BTC-USDT": (1.0, 0.0), "PUMP-USDT": (2.8, 0.0)}))   # +40 % → stop, filled at the price
    cl = s["closed"][0]
    assert cl["exit_kind"] == "стоп" and abs(cl["net_usd"] - (-(0.8 * 250) - (2.0 + 2.8) * 250 * 0.0005)) < 0.01
    L.step(conn, T0 + 8 * H, mk(**{"BTC-USDT": (1.0, 0.0), "PUMP-USDT": (2.8, 0.0), "LATE-USDT": (5.0, 0.0)}))
    x = L.step(conn, T0 + 40 * H, mk(**{"BTC-USDT": (1.0, 0.0), "PUMP-USDT": (2.8, 0.0), "LATE-USDT": (5.0, 0.0)}))   # server was down
    assert x["open"] == [] and any("окно входа пропущено" in ev for ev in x["events"])


def test_run_puts_the_desk_into_paper_json():
    d = tempfile.mkdtemp()
    json.dump({"profiles": {"bal": {"equity": 1}}, "profile_list": [{"key": "bal"}]}, open(os.path.join(d, "paper.json"), "w"))
    L.run(sqlite3.connect(":memory:"), d, T0, mk(**{"BTC-USDT": (1.0, 0.0)}))
    p = json.load(open(os.path.join(d, "paper.json")))
    assert p["profiles"]["lst"]["name"] == "Шорт листингов" and [x["key"] for x in p["profile_list"]] == ["bal", "lst"]
