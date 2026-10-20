"""Strategy conveyor LAB → PAPER → READY → RETIRED (cnq/agents/conveyor.py). Synthetic databases and feeds, no network."""
import json
import math
import os
import sqlite3

from cnq.agents import conveyor as CV
from cnq.agents import listing_desk, own_desk, paper_desk

DAY = 86_400_000
NOW = 1_793_000_000_000                       # 2026-10-26 (a Monday) — fixed clock
FEE = paper_desk.FEE


def _dirs(tmp_path):
    out = tmp_path / "web"
    out.mkdir(exist_ok=True)
    return str(out), str(tmp_path / "data" / "conveyor_state.json")


def _paper(out, profiles):
    with open(os.path.join(out, "paper.json"), "w", encoding="utf-8") as f:
        json.dump({"profiles": profiles}, f)


def _profile(name, since, dd=None, closed=None, eqh=None):
    return {"name": name, "since": since, "closed": closed or [], "stats": {"max_dd_pct": dd or 0.0}, "equity_history": eqh or []}


def _paper_trades(conn, desk, pcts, t0, step=DAY, grade="E", notional=1000.0):
    """closed trades with a given net % of notional (after the desk's fee), one every `step`; equity rows follow the closed P&L"""
    d = paper_desk.PaperDesk(conn, desk)
    eq = 10_000.0
    conn.execute(f"INSERT OR REPLACE INTO {d.T_EQ}(time, equity) VALUES(?, ?)", (t0 - 1, eq))
    for i, x in enumerate(pcts):
        t = t0 + i * step
        net = x / 100 * notional
        pnl = net + notional * d.fee                       # the desk subtracts the entry fee from pnl_usd → net
        conn.execute(f"INSERT INTO {d.T_TR} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"{grade}-{desk}-{t}", "BTC", "LONG", grade, "X", 100.0, 90.0, 90.0, "[]", "[]", notional / 100.0, 0.0, 10.0,
                      t - 3_600_000, t + DAY, t, "TIME", pnl, 0.0, 0, "", t))
        eq += net
        conn.execute(f"INSERT OR REPLACE INTO {d.T_EQ}(time, equity) VALUES(?, ?)", (t, eq))
    conn.commit()


def _state(path):
    return json.load(open(path, encoding="utf-8"))


def _feed(out):
    return json.load(open(os.path.join(out, "conveyor.json"), encoding="utf-8"))


# ------------------------------------------------------------------------------------------------------------------- statistics
def test_t_distribution_matches_known_quantiles():
    assert abs(CV.t_cdf(-1.699127, 29) - 0.05) < 1e-4            # one-sided 5 % at df 29
    assert abs(CV.t_cdf(-2.462021, 29) - 0.01) < 1e-4            # one-sided 1 % at df 29
    assert abs(CV.t_cdf(0.0, 7) - 0.5) < 1e-12
    assert abs(CV.t_cdf(-1.0, 1) - 0.25) < 1e-9                   # Cauchy
    assert abs(CV.t_cdf(2.0, 10) - (1 - CV.t_cdf(-2.0, 10))) < 1e-12
    assert CV.t_cdf(float("-inf"), 5) == 0.0 and CV.t_cdf(float("inf"), 5) == 1.0


def test_t_test_edge_cases():
    assert CV.p_worse([]) is None and CV.p_worse([1.0]) is None
    assert CV.p_worse([-0.5, -0.5, -0.5]) == 0.0                  # no spread, below the reference → surely worse
    assert CV.p_worse([0.5, 0.5]) == 1.0 and CV.p_worse([0.0, 0.0]) == 1.0
    assert CV.p_worse([float("nan"), 1.0, float("inf")]) is None  # non-finite values are dropped first
    p = CV.p_worse([-1.0, 1.0] * 20)                               # mean 0 → p 0.5
    assert abs(p - 0.5) < 1e-9
    assert CV.p_worse([-1.0, -0.8, -1.2, -0.9, -1.1]) < 0.001


def test_losing_week_streak_counts_complete_weeks_only():
    mon = CV.MONDAY0 + CV.week_of(NOW) * CV.WEEK                   # this week's Monday
    tr = [{"closed": mon - (k + 1) * CV.WEEK + DAY, "net_usd": -5.0} for k in range(5)]
    tr.append({"closed": mon + DAY, "net_usd": -50.0})             # the running week is not counted
    assert CV.losing_week_streak(tr, mon - 10 * CV.WEEK, NOW) == 5
    tr[2]["net_usd"] = +20.0                                       # a winning week three weeks back breaks it
    assert CV.losing_week_streak(tr, mon - 10 * CV.WEEK, NOW) == 2
    gap = [t for i, t in enumerate(tr) if i != 0]                  # last complete week without trades → streak 0
    assert CV.losing_week_streak(gap, mon - 10 * CV.WEEK, NOW) == 0


# ------------------------------------------------------------------------------------------------------------------- gates
def test_minimum_sample_keeps_paper_even_when_awful(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    _paper_trades(conn, "bal", [-5.0] * 12, NOW - 20 * DAY)
    _paper(out, {"bal": _profile("Сбалансированная", NOW - 25 * DAY, dd=40.0)})
    doc = CV.run(conn, out, NOW, sp)
    st = _state(sp)["desks"]["bal"]
    assert st == {"stage": "paper", "paused": False, "reason_ru": "мало сделок: 12 из 30", "reason_en": "too few trades: 12 of 30"}
    assert doc["desks"][0]["gate"] == "trades" and doc["counts"]["retired"] == 0


def test_promotion_to_ready_and_back_to_paper(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    _paper_trades(conn, "bal", [2.5, -0.8, 1.6, 0.9, 1.2] * 7, NOW - 28 * DAY, step=DAY * 0.75)
    _paper(out, {"bal": _profile("Сбалансированная", NOW - 28 * DAY - DAY)})
    doc = CV.run(conn, out, NOW, sp)
    r = doc["desks"][0]
    assert r["stage"] == "ready" and r["trades"] == 35 and abs(r["mean_pct"] - 1.08) < 1e-6 and r["pf"] == round(6.2 / 0.8, 2)
    assert r["p_worse"] > 0.05 and r["ref_mean_pct"] == 1.07 and r["source"] == "db"
    assert "решение владельца" in r["reason_ru"] and _state(sp)["desks"]["bal"]["paused"] is False
    # a 20 % dip on the account curve (deeper than min(15 %, 1.5 × 18 %)) → back to PAPER, waiting on the drawdown gate
    conn.execute("INSERT INTO paper_bal_equity(time, equity) VALUES(?, ?)", (NOW - DAY // 2, 8_200.0))
    conn.execute("INSERT INTO paper_bal_equity(time, equity) VALUES(?, ?)", (NOW - DAY // 3, 10_300.0))
    conn.commit()
    doc = CV.run(conn, out, NOW + 2 * 3_600_000, sp)
    r = doc["desks"][0]
    assert r["stage"] == "paper" and r["gate"] == "dd" and "глубже предела 15.0%" in r["reason_ru"]
    assert [(x["from"], x["to"]) for x in doc["log"]][:2] == [("ready", "paper"), (None, "ready")]


def test_ready_needs_a_historical_reference(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    _paper_trades(conn, "lab", [1.5, -0.4, 0.9] * 12, NOW - 30 * DAY, step=DAY * 0.8, grade="W")    # W: LLM news, no history
    _paper(out, {"lab": _profile("Тест новых идей", NOW - 31 * DAY)})
    r = CV.run(conn, out, NOW, sp)["desks"][0]
    assert r["stage"] == "paper" and r["gate"] == "reference" and "W" in r["reason_ru"]


def test_retire_on_bad_last_trades_and_deep_drawdown_then_sticky(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    _paper_trades(conn, "bal", [-1.5] * 25, NOW - 26 * DAY, notional=12_000.0)   # −$180 a trade → equity −45 % (> 1.5 × 18 %)
    _paper(out, {"bal": _profile("Сбалансированная", NOW - 27 * DAY)})
    doc = CV.run(conn, out, NOW, sp)
    st = _state(sp)["desks"]["bal"]
    assert st["stage"] == "retired" and st["paused"] is True and "последние 20 сделок" in st["reason_ru"]
    assert doc["counts"]["retired"] == 1 and doc["log"][0]["to"] == "retired" and doc["log"][0]["by"] == "conveyor"
    _paper_trades(conn, "bal", [3.0] * 40, NOW + DAY, step=3_600_000)             # great trades later: still retired (owner only)
    doc = CV.run(conn, out, NOW + 3 * DAY, sp)
    assert doc["desks"][0]["stage"] == "retired" and _state(sp)["desks"]["bal"]["paused"] is True
    assert doc["desks"][0]["trades"] == 65                                         # history kept


def test_retire_on_four_losing_weeks(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    mon = CV.MONDAY0 + CV.week_of(NOW) * CV.WEEK
    _paper_trades(conn, "lab", [-0.2] * 24, mon - 4 * CV.WEEK + 3_600_000, step=CV.WEEK // 6, grade="W")
    _paper(out, {"lab": _profile("Тест новых идей", mon - 5 * CV.WEEK)})
    r = CV.run(conn, out, NOW, sp)["desks"][0]
    assert r["stage"] == "retired" and r["losing_weeks"] == 4 and "4 убыточных недели подряд" in r["reason_ru"]
    assert r["last_mean_pct"] > CV.RETIRE_LAST_MEAN_PCT                          # not the last-20 rule
    assert r["p_worse"] is None                                                   # W has no reference: not the t-test


def test_retire_when_live_is_significantly_worse_than_history(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    own_desk.ensure(conn)
    t0 = NOW - 24 * DAY
    for i in range(24):                                                           # +0.2 % ± 0.05 a trade vs +3.1 % promised
        x = 0.2 + (0.05 if i % 2 else -0.05)
        conn.execute("INSERT INTO own_trades(id, symbol, rule, side, opened, opened_day, entry, stop, qty, best, closed, exit, exit_kind, pnl_usd) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (f"P{i}", "ETH", "P", "LONG", t0 + i * DAY - 1, 0, 100.0, 90.0, 10.0, 100.0,
                                                           t0 + i * DAY, 100.0, "rule", x / 100 * 1000))
    conn.commit()
    _paper(out, {"own": _profile("Сигналы CRYPTO NOVA", t0 - DAY)})
    r = CV.run(conn, out, NOW, sp)["desks"][0]
    assert r["stage"] == "retired" and r["p_worse"] < 0.01 and "значимо хуже истории" in r["reason_ru"]
    assert r["losing_weeks"] == 0 and r["last_mean_pct"] > 0


def test_reinstate_by_owner_restarts_the_count_and_keeps_the_reason_private(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    _paper_trades(conn, "bal", [-1.5] * 25, NOW - 26 * DAY, notional=12_000.0)
    _paper(out, {"bal": _profile("Сбалансированная", NOW - 27 * DAY)})
    CV.run(conn, out, NOW, sp)
    for bad in (("bal", ""), ("cons", "x"), ("nope", "x")):
        try:
            CV.reinstate(conn, bad[0], bad[1], out, NOW + 60_000, sp)
            raise AssertionError(f"reinstate {bad} must refuse")
        except ValueError:
            pass
    doc = CV.reinstate(conn, "bal", "секретная причина владельца", out, NOW + 60_000, sp)
    st = _state(sp)["desks"]["bal"]
    assert st["stage"] == "paper" and st["paused"] is False and st["reason_ru"] == "мало сделок: 0 из 30"
    r = doc["desks"][0]
    assert r["trades"] == 0 and r["trades_total"] == 25 and r["window_start"] == NOW + 60_000
    assert doc["log"][0]["by"] == "owner" and (doc["log"][0]["from"], doc["log"][0]["to"]) == ("retired", "paper")
    assert "секретная" not in json.dumps(doc, ensure_ascii=False)                # the owner's text is not published
    assert conn.execute("SELECT note FROM conveyor_log WHERE by='owner'").fetchone()[0] == "секретная причина владельца"
    try:
        CV.reinstate(conn, "bal", "again", out, NOW + 120_000, sp)
        raise AssertionError("a desk that is not retired cannot be reinstated")
    except ValueError:
        pass


def test_mirror_follows_its_base_desk(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    listing_desk._db(conn)
    t0 = NOW - 22 * DAY
    eq = 10_000.0
    for i in range(22):
        conn.execute("INSERT INTO lst_trades(id, symbol, listed, opened, entry, stop, qty, expires, funding_usd, last_t, closed, exit, exit_kind, pnl_usd) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (f"L{i}", f"N{i}-USDT", t0, t0, 1.0, 1.3, 500.0, t0, 0.0, t0, t0 + i * DAY, 1.3, "STOP", -250.0))
        eq -= 250.0
        conn.execute("INSERT INTO lst_equity VALUES(?, ?)", (t0 + i * DAY, eq))
    conn.commit()
    _paper(out, {"lst": _profile("Шорт листингов", t0 - DAY), "lstx": _profile("Турбо · шорт листингов", t0 - DAY)})
    doc = CV.run(conn, out, NOW, sp)
    st = _state(sp)["desks"]
    assert st["lst"]["stage"] == "retired" and st["lstx"] == {**st["lstx"], "stage": "retired", "paused": True}
    assert st["lstx"]["reason_ru"].startswith("зеркало")
    try:
        CV.reinstate(conn, "lstx", "x", out, NOW + 60_000, sp)
        raise AssertionError("a mirror is reinstated through its base")
    except ValueError:
        pass
    CV.reinstate(conn, "lst", "проверили биржу", out, NOW + 60_000, sp)
    st = _state(sp)["desks"]
    assert st["lst"]["paused"] is False and st["lstx"]["paused"] is False and st["lstx"]["stage"] == "paper"
    assert {r["key"] for r in doc["desks"]} == {"lst", "lstx"}


def test_trades_of_rules_removed_from_the_desk_do_not_count(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    _paper_trades(conn, "lab", [-3.0] * 25, NOW - 20 * DAY, step=DAY // 2, grade="N")   # N was removed from the lab desk 17.10
    _paper(out, {"lab": _profile("Тест новых идей", NOW - 21 * DAY)})
    r = CV.run(conn, out, NOW, sp)["desks"][0]
    assert r["stage"] == "paper" and r["trades"] == 0 and r["trades_removed_rules"] == 25


# ------------------------------------------------------------------------------------------------------------------- LAB
def test_lab_candidates_recommendation_needs_every_gate():
    yrs = {str(y): 5.0 for y in range(2019, 2027)}                               # 8 walk-forward years
    al = {"tried": 800, "tested": 260, "rows": [
        {"market": "ETH", "class": "crypto", "family": "trend", "mode": "long", "sharpe": 1.8, "years": yrs, "n_years": 8, "to": "2026-10-19",
         "passes": True, "reliable": True, "hurdle": 1.18, "cagr": 20, "dd": -15},
        {"market": "BNB", "class": "crypto", "family": "trend", "mode": "long", "sharpe": 1.40, "years": dict(list(yrs.items())[:6]), "n_years": 6,
         "to": "2026-10-19", "passes": True, "reliable": True, "hurdle": 1.36, "cagr": 19.6, "dd": -10},
        {"market": "NDX", "class": "index", "family": "trend", "mode": "long", "sharpe": 1.5, "years": yrs, "n_years": 8, "to": "2026-10-19",
         "passes": True, "reliable": True, "hurdle": 0.5},
        {"market": "SOL", "class": "crypto", "family": "donch", "mode": "long", "sharpe": 1.15, "years": {"2023": 1}, "n_years": 4,
         "to": "2026-10-19", "passes": True, "reliable": False, "hurdle": 1.67},
        {"market": "XRP", "class": "crypto", "family": "rsi2", "mode": "long", "sharpe": float("nan"), "passes": True},   # broken row
        {"market": "BTC", "class": "crypto", "family": "hold", "mode": "long", "sharpe": 2.0, "passes": False},
    ]}
    lab = {c["id"]: c for c in CV.lab_registry(al, NOW)}
    eth = lab["autolab:ETH·trend·long"]
    assert eth["recommend_paper"] and eth["gate"] == "owner" and eth["hurdle_tried"] == round(math.sqrt(2 * math.log(800)) / math.sqrt(8), 2)
    assert lab["autolab:BNB·trend·long"]["gate"] == "deflated"                  # 1.40 < √(2 ln 800)/√6 ≈ 1.49
    assert lab["autolab:NDX·trend·long"]["gate"] == "venue"
    assert lab["autolab:SOL·donch·long"]["gate"] == "luck"
    assert "autolab:XRP·rsi2·long" not in lab and "autolab:BTC·hold·long" not in lab
    docs = {k: v for k, v in lab.items() if k.startswith("doc:")}
    assert docs["doc:fx_gap_A"]["gate"] == "stale" and docs["doc:unlocks_big_short"]["gate"] == "oos"
    assert docs["doc:b_extra_alts"]["gate"] == "refuted" and not any(v["recommend_paper"] for v in docs.values())
    assert all(c["stage"] == "lab" for c in lab.values())
    assert 0 <= eth["p_bonferroni"] <= 1


# ------------------------------------------------------------------------------------------------------------------- robustness and contract
def _contract_ok(st):
    assert set(st) == {"updated_at", "desks"} and isinstance(st["updated_at"], int)
    for k, v in st["desks"].items():
        assert isinstance(k, str) and set(v) == {"stage", "paused", "reason_ru", "reason_en"}
        assert v["stage"] in CV.STAGES and isinstance(v["paused"], bool) and isinstance(v["reason_ru"], str) and isinstance(v["reason_en"], str)
        assert v["paused"] == (v["stage"] == "retired")


def test_missing_or_broken_inputs_never_raise(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    doc = CV.run(conn, out, NOW, sp)                                              # nothing at all
    assert doc["status"]["ok"] is False and doc["desks"] == [] and len(doc["lab"]) == len(CV.DOC_CANDIDATES)
    _contract_ok(_state(sp))
    with open(os.path.join(out, "paper.json"), "w") as f:
        f.write("{broken")
    with open(os.path.join(out, "autolab.json"), "w") as f:
        f.write('{"rows": [{"passes": true, "sharpe": "x"}, 5, null], "tried": "lots"}')
    CV.run(conn, out, NOW + DAY, sp)
    _paper(out, {"bal": "not a dict", "own": {"since": "abc", "closed": [{"closed": "x", "net_usd": None}, 7]},
                 "lst": {"since": NOW - DAY, "closed": [{"id": 1, "closed": NOW - 3_600_000, "net_usd": float("nan")}],
                         "stats": {"max_dd_pct": float("inf")}, "equity_history": [{"time": NOW, "equity": None}, "x"]}})
    doc = CV.run(conn, out, NOW + 2 * DAY, sp)
    st = _state(sp)
    _contract_ok(st)
    assert set(st["desks"]) == {"bal", "own", "lst"} and not any(v["paused"] for v in st["desks"].values())
    assert any(e.startswith("own:") for e in doc["status"]["errors"])
    json.loads(open(os.path.join(out, "conveyor.json"), encoding="utf-8").read())  # strict JSON (no NaN)
    assert "NaN" not in open(os.path.join(out, "conveyor.json"), encoding="utf-8").read()


def test_a_retired_desk_stays_paused_when_its_inputs_break(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    _paper_trades(conn, "bal", [-1.5] * 25, NOW - 26 * DAY, notional=12_000.0)
    _paper(out, {"bal": _profile("Сбалансированная", NOW - 27 * DAY)})
    CV.run(conn, out, NOW, sp)
    os.remove(os.path.join(out, "paper.json"))
    doc = CV.run(conn, out, NOW + 2 * 3_600_000, sp)
    st = _state(sp)
    _contract_ok(st)
    assert st["desks"]["bal"]["stage"] == "retired" and st["desks"]["bal"]["paused"] is True and doc["status"]["ok"] is False


def test_hourly_at_most_unless_forced(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    _paper(out, {"exp": _profile("Свинг", NOW - DAY)})
    assert CV.run(conn, out, NOW, sp)["counts"]["paper"] == 1
    assert CV.run(conn, out, NOW + 10 * 60_000, sp) == {"skipped": True, "minutes_since_last": 10.0}
    assert "counts" in CV.run(conn, out, NOW + 10 * 60_000, sp, force=True)
    assert "counts" in CV.run(conn, out, NOW + 70 * 60_000, sp)


def test_public_feed_shape(tmp_path):
    out, sp = _dirs(tmp_path)
    conn = sqlite3.connect(":memory:")
    _paper_trades(conn, "cons", [1.0, -0.5] * 5, NOW - 10 * DAY)
    _paper(out, {"cons": _profile("Консервативная", NOW - 11 * DAY)})
    CV.run(conn, out, NOW, sp)
    d = _feed(out)
    assert {"asof", "counts", "gates", "desks", "lab", "recommend_paper", "log", "not_covered_ru", "not_covered_en", "status"} <= set(d)
    assert set(d["counts"]) == set(CV.STAGES) and d["counts"]["paper"] == 1 and d["counts"]["lab"] == len(d["lab"])
    r = d["desks"][0]
    for k in ("key", "name_ru", "name_en", "stage", "paused", "gate", "reason_ru", "reason_en", "trades", "days", "mean_pct", "pf",
              "winrate_pct", "max_dd_pct", "ref_mean_pct", "p_worse", "evidence_ru", "stage_since"):
        assert k in r, k
    assert d["gates"]["paper_to_ready"]["min_trades"] == 30 and d["gates"]["lab_to_paper"]["automatic"] is False
    assert d["log"][0]["desk"] == "cons" and d["log"][0]["from"] is None and d["log"][0]["to"] == "paper"


def test_engine_runs_the_conveyor_hourly_and_publishes_its_feed():
    from cnq import engine as E
    assert "conveyor" in E.HOURLY and "conveyor.json" in E.OWNED and "conveyor_state.json" in E.DB_OWNED
    assert E.STEP_TIMEOUT["conveyor"] <= 180
