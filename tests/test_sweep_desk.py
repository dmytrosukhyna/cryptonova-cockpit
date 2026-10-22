"""«Ложный пробой · лонг» (paper-only test desk, docs/research/sweep.md) and the «Что будет делать система» block.
Synthetic candle store and files, no network."""
import ast
import glob
import json
import os
import sqlite3
import tempfile

from cnq.agents import conveyor as CV
from cnq.agents import outlook as OL
from cnq.agents import sweep_desk as SW

DAY = 86_400_000
M15 = 900_000
D0 = 1_700_006_400_000 - 1_700_006_400_000 % DAY        # a UTC midnight in 2023: the risk gate treats it as a replay (no live limits)


def day_bars(t0, plan):
    """plan: [(o, h, l, c)] per day → 96 identical 15m bars a day (the day aggregates back to the same o/h/l/c)"""
    out = []
    for i, (o, h, l, c) in enumerate(plan):
        for k in range(96):
            out.append((t0 + i * DAY + k * M15, o if k == 0 else c, h, l, c))
    return out


def store(path, coins):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS candles(symbol TEXT, interval TEXT, open_time INTEGER, open REAL, high REAL, low REAL, close REAL, "
              "volume REAL, close_time INTEGER)")
    c.execute("DELETE FROM candles")
    for sym, rows in coins.items():
        c.executemany("INSERT INTO candles VALUES(?, '15m', ?, ?, ?, ?, ?, 0, ?)", [(f"{sym}-USDT", t, o, h, l, cl, t + M15 - 1) for t, o, h, l, cl in rows])
    c.commit()
    c.close()


def files(d, bull=True):
    json.dump({"coins": [{"symbol": "BTC-USDT", "price": 100.0, "regime": {"sma200": 80.0 if bull else 120.0, "r60": 0.1}}]},
              open(os.path.join(d, "decision.json"), "w"))
    json.dump({"profiles": {"bal": {"equity": 10_000.0}}, "profile_list": [{"key": "bal"}]}, open(os.path.join(d, "paper.json"), "w"))


FLAT = [(100, 101, 99, 100)] * 30
SWEEP = FLAT + [(100, 100, 98, 98.8), (98.8, 99.8, 98.6, 99.6)]          # day 30 pierces 99 by 1 %, day 31 closes back above → confirmed


def test_episodes_follow_the_study_definitions():
    b = [[D0 + i * DAY, *x] for i, x in enumerate(SWEEP)]
    e = SW.episodes(b)[-1]
    assert e["kind"] == "sweep" and e["j"] == 31 and e["level"] == 99 and e["low"] == 98
    shallow = FLAT + [(100, 100, 98.7, 98.8), (98.8, 99.8, 98.6, 99.6)]   # only 0.3 % under the level → neither
    assert SW.episodes([[D0 + i * DAY, *x] for i, x in enumerate(shallow)])[-1]["kind"] == "none"
    down = FLAT + [(100, 100, 97, 97.5), (97.5, 98, 96, 96.5), (96.5, 97, 95, 95.5)]
    assert SW.episodes([[D0 + i * DAY, *x] for i, x in enumerate(down)])[-1]["kind"] == "breakdown"
    pend = FLAT + [(100, 100, 98, 98.5)]                                    # broken today, no reclaim yet → «open» episode
    e = SW.episodes([[D0 + i * DAY, *x] for i, x in enumerate(pend)])[-1]
    assert e["kind"] == "open" and e["level"] == 99


def test_long_after_a_sweep_enters_at_the_next_open_sizes_by_risk_and_exits_after_7_days():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "cnq.sqlite")
    files(d)
    entry_day = D0 + 32 * DAY
    rows = day_bars(D0, SWEEP) + [(entry_day, 99.7, 99.9, 99.6, 99.8)]       # the entry day's first 15m bar
    store(db, {"BTC": rows})
    conn = sqlite3.connect(":memory:")
    doc = SW.run(conn, d, entry_day + 5 * 60_000, db)
    assert len(doc["open"]) == 1
    o = doc["open"][0]
    assert o["symbol"] == "BTC" and o["entry"] == 99.7 and abs(o["stop"] - 98 * 0.998) < 1e-9 and o["expires"] == entry_day + 7 * DAY
    assert o["qty"] * o["entry"] <= 10_000 / 3 + 1e-6 and o["risk_usd"] <= 100 + 1e-6          # 1 % risk, never above 1/3 of the account
    assert "в работе" in doc["status_ru"] and doc["paper_only"] is True
    p = json.load(open(os.path.join(d, "paper.json")))
    assert p["profiles"]["swp"]["name"] == "Ложный пробой · лонг" and {x["key"] for x in p["profile_list"]} == {"bal", "swp"}
    # the same signal is never opened twice
    assert len(SW.run(conn, d, entry_day + 10 * 60_000, db)["open"]) == 1
    # 7 days at 101 → time exit at the daily close 7 days after the entry day's open
    rows += day_bars(entry_day, [(99.7, 101.5, 99.6, 101)] * 8)
    store(db, {"BTC": sorted(set(rows))})
    doc = SW.run(conn, d, entry_day + 7 * DAY + 3_600_000, db)
    assert not doc["open"] and doc["closed"][0]["exit_kind"] == "time" and doc["closed"][0]["exit"] == 101
    q = doc["closed"][0]["qty"]
    assert abs(doc["closed"][0]["net_usd"] - round((101 - 99.7) * q - 0.001 * (99.7 + 101) * q, 2)) < 0.011
    assert doc["equity"] > 10_000 and doc["stats"]["trades"] == 1
    # the conveyor reads the same record (net of fees) and has a reference mean for the rule
    tr = CV.db_trades(conn, "swp")
    assert len(tr) == 1 and tr[0]["rule"] == "SWP" and CV.rule_ref("swp", "SWP")[0] == 1.21 and "swp" in CV.DESKS


def test_stop_first_and_a_gap_fills_at_the_open():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "cnq.sqlite")
    files(d)
    entry_day = D0 + 32 * DAY
    rows = day_bars(D0, SWEEP) + [(entry_day, 99.7, 99.9, 99.6, 99.8)]
    store(db, {"ETH": rows, "BTC": day_bars(D0, FLAT + [(100, 100.5, 99.5, 100)] * 3)})
    conn = sqlite3.connect(":memory:")
    assert [o["symbol"] for o in SW.run(conn, d, entry_day + 60_000, db)["open"]] == ["ETH"]
    rows += [(entry_day + M15, 99.8, 99.9, 99.0, 99.2), (entry_day + 2 * M15, 97.0, 97.5, 96.0, 96.5)]   # gaps under the stop
    store(db, {"ETH": rows, "BTC": day_bars(D0, FLAT + [(100, 100.5, 99.5, 100)] * 3)})
    doc = SW.run(conn, d, entry_day + 4 * M15, db)
    c = doc["closed"][0]
    assert c["exit_kind"] == "stop" and c["exit"] == 97.0 and c["net_usd"] < 0 and abs(c["pnl_r"] + 1.4) < 0.3


def test_no_entry_outside_rost_and_the_status_says_why():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "cnq.sqlite")
    files(d, bull=False)
    entry_day = D0 + 32 * DAY
    store(db, {"BTC": day_bars(D0, SWEEP) + [(entry_day, 99.7, 99.9, 99.6, 99.8)]})
    doc = SW.run(sqlite3.connect(":memory:"), d, entry_day + 60_000, db)
    assert not doc["open"] and doc["watch"]["skipped"] == {"regime": 1}
    assert doc["status_ru"].startswith("режим не «рост» — новых входов нет") and "пропущен — режим не «рост»" in doc["status_ru"]


def test_at_most_three_positions_in_coin_order():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "cnq.sqlite")
    files(d)
    entry_day = D0 + 32 * DAY
    rows = day_bars(D0, SWEEP) + [(entry_day, 99.7, 99.9, 99.6, 99.8)]
    store(db, {c: rows for c in ("BTC", "ETH", "SOL", "XRP", "BNB")})
    doc = SW.run(sqlite3.connect(":memory:"), d, entry_day + 60_000, db)
    assert [o["symbol"] for o in doc["open"]] == ["BTC", "ETH", "SOL"] and doc["watch"]["skipped"] == {"slots": 2}


def test_paper_only_nothing_real_reads_or_imports_it():
    tree = ast.parse(open(SW.__file__, encoding="utf-8").read())
    mods = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + \
           [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any("execution" in m for m in mods)
    for f in glob.glob(os.path.join(os.path.dirname(SW.__file__), "..", "execution", "*.py")) + \
            [os.path.join(os.path.dirname(SW.__file__), n) for n in ("okx_signal.py", "spot_profiles.py")]:
        assert "swp" not in open(f, encoding="utf-8").read(), f


def test_outlook_system_plan_levels_reactions_and_desk_status():
    asof = D0 + 40 * DAY + 3_600_000
    c1d = [[D0 + i * DAY, 100, 110 + i % 3, 90 - i % 5, 100, 1] for i in range(40)] + [[D0 + 40 * DAY, 100, 200, 10, 100, 1]]   # forming bar ignored
    term = {"asof": asof, "coins": [{"symbol": "BTC-USDT", "price": 100.0, "candles_1d": c1d}]}
    paper = {"profiles": {"swp": {"status_ru": "ждёт ложный пробой минимума 20 дней"}}}
    reg = {"btc_bull": True, "sma200_panel": 95.0, "sma200_hist": 90.0, "price": 100.0}
    S = OL.system_plan({"terminal": term, "paper": paper}, reg, 100.0)
    lv = {x["key"]: x for x in S["levels"]}
    assert lv["low20"]["price"] == 86 and lv["high20"]["price"] == 112 and lv["sma200"]["price"] == 95.0
    assert lv["sma200"]["note_ru"].startswith("по полной истории") and lv["low20"]["dist_pct"] == -14.0
    lines = [l for x in S["levels"] for l in x["lines"]] + S["always"]
    assert all(l["src"] and l["text_ru"] for l in lines)
    txt = " ".join(l["text_ru"] for l in lines)
    for need in ("−20%", "−10%", "−3%", "−15%", "−3.76%", "«осторожно»", "со стопом на бирже", "Ложный пробой · лонг"):
        assert need in txt, need
    assert S["desk"]["status_ru"] == "ждёт ложный пробой минимума 20 дней" and S["regime_ru"] == "рост"
    doc = OL.build({"terminal": term, "paper": paper}, {})
    assert doc["system_plan"] is None or doc["system_plan"]["levels"]


def test_a_window_that_closed_before_the_desk_started_is_not_counted_but_a_later_one_is():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "cnq.sqlite")
    files(d)
    store(db, {"BTC": day_bars(D0, SWEEP + [(99.7, 100, 99.5, 99.8)] * 2)})      # entry day (32) already over at the first run
    conn = sqlite3.connect(":memory:")
    doc = SW.run(conn, d, D0 + 34 * DAY + 60_000, db)
    assert not doc["open"] and doc["watch"]["signals"] == 0 and doc["watch"]["last"] is None
    conn2 = sqlite3.connect(":memory:")
    SW.ensure(conn2)
    SW._set_meta(conn2, "since", D0)                                               # the desk ran, but not during the entry day
    doc = SW.run(conn2, d, D0 + 34 * DAY + 60_000, db)
    assert doc["watch"]["skipped"] == {"missed": 1} and "окно входа прошло" in doc["status_ru"]
