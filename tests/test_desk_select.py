"""«Автовыбор основного стола» (cnq/agents/desk_select.py, 28.10.2026): slow and evidence-based — result ÷ drawdown, ≥ 20 trades,
drawdown within the history, ≥ 20 % better, no open position of the current desk on the real account, ≥ 7 days between switches;
28.10 (owner): plus READY by the conveyor (30 trades, 21 days, PF ≥ 1.2, mean ≥ +0.3 %), and cons / bal / aggr are ONE family
compared as one candidate whose size is the owner's choice («Агрессивная»). Every desk and number here is made-up test data."""
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
READY = {k: {"key": k, "stage": "ready", "trades": 35, "days": 25.0, "pf": 1.6, "mean_pct": 0.5} for k in ("cons", "bal", "aggr", "own")}


def strong(**kw):
    """a world where «Сигналы CRYPTO NOVA» clearly beat the autopilot family by result ÷ drawdown with 25 trades"""
    w = world(**kw)
    w["own"] = desk((10000, 10200, 10150, 10400), 25, "Сигналы CRYPTO NOVA")
    return w


def test_candidates_are_the_main_desk_keys_of_the_header_and_the_panel():
    assert set(ds.candidate_keys()) == set(ph.MAIN_KEYS) == {"cons", "bal", "aggr", "own"}
    s = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.html"), encoding="utf-8").read()
    assert "const NOVA_MAIN=['cons','bal','aggr','own'];" in s and ph.NOVA_BASE == ("aggr", "imp")
    assert "lab" not in ds.candidate_keys() and "brk" not in ds.candidate_keys() and "imp" not in ds.candidate_keys()
    assert cv.DESKS["lab"]["ev"] == "none" and cv.DESKS["brk"]["ev"] == "failed"


def test_today_the_owners_aggr_holds_and_waits_for_ready():
    prof = world(n=8)
    d = ds.decide(prof, None, REAL_FLAT, {}, ds.OWNER["since"] + 3_600_000)
    assert (d["current"], d["by"], d["since"], d["decision"]) == ("aggr", "owner", ds.OWNER["since"], "hold")
    assert d["short_ru"] == "ждёт READY конвейера" and "готовности по конвейеру" in d["why_ru"]
    assert d["family"] == "autopilot" and "Агрессивная" in d["family_ru"]
    keys = [c["key"] for c in d["candidates"]]
    assert keys == ["aggr", "own"]                                              # one autopilot family, its size = the owner's choice
    c = {x["key"]: x for x in d["candidates"]}
    assert set(c["own"]) >= {"key", "trades", "result_pct", "dd_pct", "ratio", "eligible", "why", "family", "members", "ready"}
    assert c["aggr"]["why"] == "текущий основной стол" and not c["aggr"]["eligible"] and c["aggr"]["members"] == ["cons", "bal", "aggr"]
    assert "нужна READY" in c["own"]["why"] and not c["own"]["ready"]


def test_switch_when_every_condition_holds_and_it_is_logged(tmp_path):
    prof = strong()
    d = ds.decide(prof, None, REAL_FLAT, READY, NOW)
    assert d["decision"] == "switch" and d["current"] == "own" and d["by"] == "auto" and d["since"] == NOW and d["from"] == "aggr"
    c = {x["key"]: x for x in d["candidates"]}
    assert c["own"]["eligible"] and c["own"]["ready"] and c["own"]["ratio"] >= 1.2 * c["own"]["cur_ratio"] > 0
    assert "Переключено с «Агрессивная» на «Сигналы CRYPTO NOVA» (READY по конвейеру)" in d["why_ru"]
    # the same through run(): web/desk_select.json + one log line; the next run keeps the new desk and its date
    out = tmp_path / "web"
    out.mkdir()
    (out / "paper.json").write_text(json.dumps({"profiles": prof}), encoding="utf-8")
    (out / "public_real.json").write_text(json.dumps(REAL_FLAT), encoding="utf-8")
    (out / "conveyor.json").write_text(json.dumps({"desks": list(READY.values())}), encoding="utf-8")
    log = tmp_path / "data" / "desk_select.jsonl"
    doc = ds.run(str(out), NOW, str(log))
    assert json.loads((out / "desk_select.json").read_text(encoding="utf-8")) == json.loads(json.dumps(doc, ensure_ascii=False))
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and (lines[0]["from"], lines[0]["to"], lines[0]["t"]) == ("aggr", "own", NOW)
    again = ds.run(str(out), NOW + H, str(log))
    assert (again["current"], again["since"], again["by"]) == ("own", NOW, "auto") and again["decision"] in ("hold", "wait")
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    assert ph.main_desk(ph.load_sel(str(out))) == "own"                      # the header and the real account read this file


def test_every_guard_alone_stops_the_switch():
    base = dict(prof=strong(), prev=None, real=REAL_FLAT, stages=READY, now=NOW)

    def dec(**kw):
        a = {**base, **kw}
        return ds.decide(a["prof"], a["prev"], a["real"], a["stages"], a["now"])

    def why(d, k="own"):
        return {x["key"]: x for x in d["candidates"]}[k]["why"]
    assert dec()["decision"] == "switch"
    # 28.10: not READY by the conveyor — no record, another stage, or one of the four numbers short
    for st in ({}, {"own": {**READY["own"], "stage": "paper"}}, {"own": {**READY["own"], "trades": 29}},
               {"own": {**READY["own"], "days": 20.9}}, {"own": {**READY["own"], "pf": 1.19}}, {"own": {**READY["own"], "mean_pct": 0.29}},
               {"own": {**READY["own"], "pf": None}}):
        d = dec(stages={**READY, **st} if st else {})
        assert d["decision"] == "hold" and d["current"] == "aggr" and not {x["key"]: x for x in d["candidates"]}["own"]["ready"], st
    # fewer than 20 closed trades since 17.10
    p = strong()
    p["own"]["closed"] = p["own"]["closed"][:19]
    assert dec(prof=p)["decision"] == "hold"
    # the drawdown deeper than the history promised (own: 23 %), even with a far better result
    p = strong()
    p["own"] = desk((10000, 13000, 9900, 14000), 25, "Сигналы CRYPTO NOVA")
    d = dec(prof=p)
    assert d["decision"] == "hold" and "глубже ожидаемой по истории 23%" in why(d)
    p = strong()
    p["own"]["stats"] = {"max_dd_pct": 23.5}                                 # the desk's own finer record is deeper than its curve
    assert dec(prof=p)["decision"] == "hold"
    # not 20 % better by result ÷ drawdown
    p = world(aggr=(10000, 10200, 10000, 10300))
    p["own"] = desk((10000, 10230, 10000, 10345), 25, "Сигналы CRYPTO NOVA")
    d = dec(prof=p)
    assert d["decision"] == "hold" and "нужно на 20% лучше" in why(d)
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
    d = dec(stages={**READY, "own": {**READY["own"], "stage": "retired", "paused": True}})
    assert d["decision"] == "hold" and "снят конвейером" in why(d)
    # a losing challenger never wins, even against a worse current desk
    p = world(aggr=(10000, 9500, 9000))
    p["own"] = desk((10000, 9900, 9800), 25, "Сигналы CRYPTO NOVA")
    d = dec(prof=p)
    assert d["decision"] == "hold" and "не в плюсе" in why(d)


def test_sizes_of_one_family_are_never_switched():
    """cons / bal / aggr are the same signals at three sizes: the bigger one earns more in a good stretch and loses more in a bad
    one. 28.10 (owner): they are ONE family — the selector never switches between them, whatever their numbers."""
    k = lambda f: tuple(10000 + (x - 10000) * f for x in (10000, 10400, 10100, 10600, 10300, 10800))   # noqa: E731
    d = ds.decide(world(cons=k(3.0), bal=k(1.0), aggr=k(0.5)), {"current": "bal", "since": 0, "by": "owner"}, REAL_FLAT, READY, NOW)
    keys = [x["key"] for x in d["candidates"]]
    assert d["decision"] == "hold" and d["current"] == "bal" and "cons" not in keys and "aggr" not in keys     # the current size represents it
    assert ds.family_reps(["cons", "bal", "aggr", "own"], "own") == ["aggr", "own"]                          # else the owner's size
    assert ds.family_of("cons") == ds.family_of("aggr") == "autopilot" and ds.family_of("own") == "own"


def test_back_to_the_family_takes_the_owners_size_not_the_best_size():
    """own is the main desk and the autopilot family beats it: the switch goes to «Агрессивная» (the owner's size), even when
    «Консервативная» has the better ratio."""
    p = world(cons=(10000, 10300, 10250, 10500), aggr=(10000, 10300, 10200, 10450))
    p["own"] = desk((10000, 10050, 9900, 10010), 25, "Сигналы CRYPTO NOVA")
    d = ds.decide(p, {"current": "own", "since": 0, "by": "auto"}, {"asof": NOW, "futures": [{"inst": "SUI-USDT-SWAP", "sig": "imp:K1"}]},
                  READY, NOW)
    c = {x["key"]: x for x in d["candidates"]}
    assert d["decision"] == "switch" and d["current"] == "aggr" and set(c) == {"aggr", "own"}
    assert {"lab", "brk", "imps", "imp"}.isdisjoint(c)


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
    assert c["own"]["cur_ratio"] == cur_same != ds.metrics(prof["aggr"], T0)["ratio"]


def test_whales_are_watched_but_never_a_candidate(tmp_path):
    """30.10 (owner: «Киты … как её быстро включать»): the whale-copy desk shows its distance to READY in a separate `watch` list;
    it is never in `candidates`, never eligible, and the decision does not read it"""
    import time
    wtop = {"desk": {"name_ru": "Киты · копирование топ-5", "start": 10000.0, "started": NOW - 12 * ds.DAY, "equity": 10800.0, "trades": 8,
                     "max_dd_pct": 3.5, "open": [], "history": [[NOW - 11 * ds.DAY, 10100.0], [NOW - 5 * ds.DAY, 9950.0]],
                     "closed": [{"whale": "okx:X", "coin": "BTC", "pnl": 200.0, "notional": 5000.0}, {"whale": "okx:X", "coin": "ETH", "pnl": -50.0, "notional": 5000.0}]}}
    w = ds.watch_whales(wtop, NOW)
    assert w["key"] == "whales" and w["trades"] == 8 and w["pf"] == 4.0 and w["mean_pct"] == 1.5 and not w["eligible"] and not w["ready"]
    assert "сделок 8 из 30" in w["ready_why"] and "дней 12,0 из 21" in w["ready_why"] and "владельца" in w["why"]
    assert ds.watch_whales(None, NOW) is None and ds.watch_whales({"desk": {}}, NOW) is None
    assert "whales" not in ds.candidate_keys()
    for name in ("paper.json", "conveyor.json", "whales_top.json"):
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", name)
        if os.path.exists(src):
            (tmp_path / name).write_text(open(src, encoding="utf-8").read(), encoding="utf-8")
    d = ds.run(str(tmp_path), NOW, str(tmp_path / "log.jsonl"))
    assert [c["key"] for c in d["candidates"]] and "whales" not in [c["key"] for c in d["candidates"]]
    assert isinstance(d["watch"], list) and all(x["key"] == "whales" and not x["eligible"] for x in d["watch"])
