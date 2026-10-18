import json
import os
import sqlite3
import tempfile

from cnq.agents import capital as K

T0 = 1_792_000_000_000   # a Saturday


def world(d, spot=10_000.0, bal=10_000.0, imp=10_000.0, fund=10_000.0, hist=None):
    json.dump({"portfolios": {"ideas": {"equity_usd": spot, "history": hist or []}}}, open(os.path.join(d, "portfolio.json"), "w"))
    json.dump({"profiles": {"bal": {"equity": bal, "open": [], "equity_history": []}, "imp": {"equity": imp, "open": [], "equity_history": []}}},
              open(os.path.join(d, "paper.json"), "w"))
    json.dump({"start_equity": 10_000.0, "equity_mtm": fund, "history": []}, open(os.path.join(d, "funding.json"), "w"))
    json.dump({"week": {"virtual_pnl_usd": 24.0}}, open(os.path.join(d, "arb.json"), "w"))


def test_split_follow_desks_and_monthly_fee():
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    world(d)
    a = K.run(conn, d, T0)
    w = {s["key"]: s["weight"] for s in a["sleeves"]}
    assert w == {"spot": 0.5, "futures": 0.3, "funding": 0.2} and a["value_usd"] == 1000.0
    assert [x["key"] for x in a["watch_only"]] == ["arb", "tri"] and a["watch_only"][0]["week_virtual_usd"] == 24.0
    # spot +10 %, futures: bal +4 % and imp 0 % → +2 %, funding −1 %
    world(d, spot=11_000, bal=10_400, imp=10_000, fund=9_900)
    b = K.run(conn, d, T0 + 3_600_000)
    v = {s["key"]: s["value_usd"] for s in b["sleeves"]}
    assert abs(v["spot"] - 550) < 0.01 and abs(v["futures"] - 306) < 0.01 and abs(v["funding"] - 198) < 0.01
    assert abs(b["value_usd"] - 1054) < 0.01 and b["month"]["fee_usd"] == round(b["month"]["profit_usd"] * 0.10, 2) > 0
    json.dumps(b)


def test_weekly_tilt_halves_a_losing_desk_within_bounds():
    w = K.target_weights({"spot": 0.08, "futures": -0.10, "funding": 0.0}, {"spot", "futures", "funding"})
    assert abs(sum(w.values()) - 1) < 1e-6 and w["spot"] <= 0.60 and w["futures"] >= 0.15 and w["futures"] < 0.30
