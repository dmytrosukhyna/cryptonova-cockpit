"""«Автовыбор основного стола» (cnq/agents/desk_select.py, 28.10.2026): slow and evidence-based — result ÷ drawdown, ≥ 20 trades,
drawdown within the history, ≥ 20 % better, no open position of the current desk on the real account, ≥ 7 days between switches.
Every desk and number here is made-up test data."""
import json
import os

from cnq.agents import conveyor as cv
from cnq.agents import desk_select as ds
from cnq.agents import panel_header as ph

H = 3_600_000
T0 = ph.FUT_D0 + H
NOW = ds.OWNER["since"] + 8 * ds.DAY                    # 8 days after the owner's 28.10 choice


def desk(curve, n=0, name=None, t0=T0, **kw):
    """a demo desk: equity every hour from t0 (the last point = now), n closed trades since 17.10"""
    return {"name": name, "start_equity": 10000.0, "equity": curve[-1], "open": [],
            "equity_history": [{"time": t0 + i * H, "equity": float(e)} for i, e in enumerate(curve)],
            "closed": [{"id": f"x{i}", "closed": t0 + i * 1000, "net_usd": 1.0} for i in range(n)], **kw}


def world(aggr=(10000, 10300, 9900, 10200), bal=(10000, 10200, 10050, 10300), cons=(10000, 10100, 9950, 10050), n=25):
    return {"cons": desk(cons, n, "Консервативная"), "bal": desk(bal, n, "Сбалансированная"), "aggr": desk(aggr, n, "Агрессивная"),
            "imp": desk((10000, 10000), 0, "Импульс BTC"), "imps": desk((10000, 10600), 40, "Активный спот"),
            "lab": desk((10000, 19000), 90, "Тест новых идей"), "brk": desk((10000, 18000), 90, "Пробой"),
            "own": desk((10000, 10010, 10000, 10020), 3, "Сигналы CRYPTO NOVA")}


REAL_FLAT = {"asof": NOW - 60_000, "futures": [{"inst": "SUI-USDT-SWAP", "sig": "imp:K1"}]}     # only the second half holds a position


def test_candidates_are_the_main_desk_keys_of_the_header_and_the_panel():
    assert set(ds.candidate_keys()) == set(ph.MAIN_KEYS) == {"cons", "bal", "aggr", "own"}
    s = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.html"), encoding="utf-8").read()
    assert "const NOVA_MAIN=['cons','bal','aggr','own'];" in s and ph.NOVA_BASE == ("aggr", "imp")
    assert "lab" not in ds.candidate_keys() and "brk" not in ds.candidate_keys() and "imp" not in ds.candidate_keys()
    assert cv.DESKS["lab"]["ev"] == "none" and cv.DESKS["brk"]["ev"] == "failed"


def test_today_the_owners_aggr_holds_and_waits_for_20_trades():
    prof = world(n=8)
    d = ds.decide(prof, None, REAL_FLAT, {}, ds.OWNER["since"] + 3_600_000)
    assert (d["current"], d["by"], d["since"], d["decision"]) == ("aggr", "owner", ds.OWNER["since"], "hold")
    assert d["short_ru"] == "ждёт 20 сделок" and "меньше 20 закрытых сделок" in d["why_ru"]
    keys = [c["key"] for c in d["candidates"]]
    assert set(keys) == {"cons", "bal", "aggr", "own"}
    c = {x["key"]: x for x in d["candidates"]}
    assert set(c["bal"]) >= {"key", "trades", "result_pct", "dd_pct", "ratio", "eligible", "why"}
    assert c["aggr"]["why"] == "текущий основной стол" and not c["aggr"]["eligible"] and "мало сделок: 8 из 20" in c["bal"]["why"]


def test_switch_when_every_condition_holds_and_it_is_logged(tmp_path):
    prof = world()
    d = ds.decide(prof, None, REAL_FLAT, {}, NOW)
    assert d["decision"] == "switch" and d["current"] == "bal" and d["by"] == "auto" and d["since"] == NOW and d["from"] == "aggr"
    c = {x["key"]: x for x in d["candidates"]}
    assert c["bal"]["eligible"] and c["bal"]["ratio"] >= 1.2 * c["bal"]["cur_ratio"] > 0
    assert "Переключено с «Агрессивная» на «Сбалансированная»" in d["why_ru"]
    # the same through run(): web/desk_select.json + one log line; the next run keeps the new desk and its date
    out = tmp_path / "web"
    out.mkdir()
    (out / "paper.json").write_text(json.dumps({"profiles": prof}), encoding="utf-8")
    (out / "public_real.json").write_text(json.dumps(REAL_FLAT), encoding="utf-8")
    log = tmp_path / "data" / "desk_select.jsonl"
    doc = ds.run(str(out), NOW, str(log))
    assert json.loads((out / "desk_select.json").read_text(encoding="utf-8")) == json.loads(json.dumps(doc, ensure_ascii=False))
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and (lines[0]["from"], lines[0]["to"], lines[0]["t"]) == ("aggr", "bal", NOW)
    again = ds.run(str(out), NOW + H, str(log))
    assert (again["current"], again["since"], again["by"]) == ("bal", NOW, "auto") and again["decision"] in ("hold", "wait")
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    assert ph.main_desk(ph.load_sel(str(out))) == "bal"                      # the header and the real account read this file


def test_every_guard_alone_stops_the_switch():
    base = dict(prof=world(), prev=None, real=REAL_FLAT, stages={}, now=NOW)

    def dec(**kw):
        a = {**base, **kw}
        return ds.decide(a["prof"], a["prev"], a["real"], a["stages"], a["now"])
    assert dec()["decision"] == "switch"
    # fewer than 20 closed trades since 17.10
    p = world()
    p["bal"]["closed"] = p["bal"]["closed"][:19]
    d = dec(prof=p)
    assert d["decision"] == "hold" and d["current"] == "aggr"
    # the drawdown deeper than the history promised (bal: 18 %), even with a far better result
    d = dec(prof=world(bal=(10000, 12000, 9700, 13000)))
    assert d["decision"] == "hold" and "глубже ожидаемой по истории 18%" in {x["key"]: x for x in d["candidates"]}["bal"]["why"]
    p = world()
    p["bal"]["stats"] = {"max_dd_pct": 18.5}                                 # the desk's own finer record is deeper than its curve
    assert dec(prof=p)["decision"] == "hold"
    # not 20 % better by result ÷ drawdown (here ≈ 15 %)
    d = dec(prof=world(aggr=(10000, 10200, 10000, 10300), bal=(10000, 10230, 10000, 10345)))
    assert d["decision"] == "hold" and "нужно на 20% лучше" in {x["key"]: x for x in d["candidates"]}["bal"]["why"]
    # less than 7 days since the last switch
    d = dec(now=ds.OWNER["since"] + 6 * ds.DAY)
    assert d["decision"] == "wait" and d["current"] == "aggr" and "из 7" in d["why_ru"] and "04.11" in d["short_ru"]
    # the current desk holds a position on the real account — or a position nobody can attribute — or no fresh real data
    for real in ({"asof": NOW, "futures": [{"inst": "SOL-USDT-SWAP", "sig": "aggr:T9"}]},
                 {"asof": NOW, "futures": [{"inst": "SOL-USDT-SWAP", "sig": None}]},
                 {"asof": NOW - 3 * H, "futures": []}, None, {"asof": NOW}):
        d = dec(real=real)
        assert d["decision"] == "wait" and d["current"] == "aggr" and "не переносим" in d["why_ru"], real
    # retired by the conveyor
    d = dec(stages={"bal": {"key": "bal", "stage": "retired", "paused": True}})
    assert d["decision"] == "hold" and "снят конвейером" in {x["key"]: x for x in d["candidates"]}["bal"]["why"]
    # a losing challenger never wins, even against a worse current desk
    d = dec(prof=world(aggr=(10000, 9500, 9000), bal=(10000, 9900, 9800), cons=(10000, 9950, 9900)))
    assert d["decision"] == "hold" and "не в плюсе" in {x["key"]: x for x in d["candidates"]}["bal"]["why"]


def test_more_money_after_a_winning_stretch_is_not_a_reason():
    """cons / bal / aggr are the same signals at three sizes: the bigger one earns more in a good stretch and loses more in a bad
    one. Ranked by profit the biggest would always win; by result ÷ drawdown they are equal — so nothing switches."""
    k = lambda f: tuple(10000 + (x - 10000) * f for x in (10000, 10400, 10100, 10600, 10300, 10800))   # noqa: E731
    d = ds.decide(world(cons=k(0.5), bal=k(1.0), aggr=k(1.5)), {"current": "bal", "since": 0, "by": "owner"}, REAL_FLAT, {}, NOW)
    c = {x["key"]: x for x in d["candidates"]}
    assert c["aggr"]["result_pct"] > c["bal"]["result_pct"] > c["cons"]["result_pct"]
    assert d["decision"] == "hold" and d["current"] == "bal"
    assert all(not c[x]["eligible"] for x in ("cons", "aggr"))


def test_the_best_ratio_wins_among_several_eligible_and_spot_lab_brk_never_count():
    d = ds.decide(world(cons=(10000, 10300, 10250, 10500)), None, REAL_FLAT, {}, NOW)
    c = {x["key"]: x for x in d["candidates"]}
    assert c["cons"]["eligible"] and c["bal"]["eligible"] and c["cons"]["ratio"] > c["bal"]["ratio"]
    assert d["current"] == "cons" and {"lab", "brk", "imps", "imp"}.isdisjoint(c)


def test_a_broken_state_file_starts_from_the_owners_choice_and_the_window_is_shared():
    prof = world(n=5)
    for prev in (None, {}, {"current": "lab", "since": 1, "by": "auto"}, {"current": "bal"}, ["bal"], {"current": "bal", "since": "x", "by": "auto"}):
        d = ds.decide(prof, prev, REAL_FLAT, {}, NOW)
        assert (d["current"], d["since"], d["by"]) == ("aggr", ds.OWNER["since"], "owner"), prev
    # a desk that started later is compared with the current one from ITS start: both over the same hours
    prof = world()
    prof["own"] = desk((10000, 10500, 10400, 10600), 25, "Сигналы CRYPTO NOVA", t0=T0 + 2 * H)
    c = {x["key"]: x for x in ds.decide(prof, None, REAL_FLAT, {}, NOW)["candidates"]}
    cur_same = ds.metrics(prof["aggr"], T0 + 2 * H)["ratio"]
    assert c["own"]["cur_ratio"] == cur_same != c["bal"]["cur_ratio"]
