"""«Автопилот OKX · фьючерсы» (cnq/execution/okx_futures.py) and the active spot (okx_active.py) with a fake OKX (tests/okx_fake.py):
sizing from the desk's risk scaled to the budget (15 % of the account since the owner's decision of 28.10 — before the first live run;
on the real account 15 % of the starting capital + the futures' own result — split between the CRYPTO NOVA desks), the caps (leverage,
0.5 % risk, 5 positions, 3 % daily loss stop), a stop attached to every entry and re-placed when missing (or the position closed),
reduce-only exits, partial exits, moved stops, the kill switch, refusal without live mode / with a withdraw key / in the spot account
mode / on a copy-trader account, the lead-trader path. Every coin, key and amount here is made-up test data; no real API is called."""
import os
import tempfile

import okx_fake as F
from cnq.execution import okx_active as ac
from cnq.execution import okx_autopilot as ap
from cnq.execution import okx_futures as fu

fu.LOG = os.path.join(tempfile.mkdtemp(), "okx_futures.jsonl")
ac.LOG = os.path.join(tempfile.mkdtemp(), "okx_active.jsonl")
ap.LOG = os.path.join(tempfile.mkdtemp(), "okx_autopilot.jsonl")
NOW = F.NOW


def run(fk, e, d, now=NOW):
    cl = ap.connect(e, fk, {}, F.nosleep)
    return fu.step(e, desks=d, now=now, sleep=F.nosleep, cl=cl)


def swap_orders(fk):
    return [o for o in fk.orders if o["instId"].endswith("-SWAP")]


def test_desks_are_the_headers_crypto_nova_set():
    paper = {"profiles": {"bal": {"open": []}, "aggr": {"open": []}, "imp": {"open": []}, "own": {"open": [], "stats": {"trades": 12, "avg_trade_pct": 1.0}},
                          "imps": {"open": []}}}
    assert [k for k, _ in fu.pick_desks(paper)] == ["aggr", "imp"]          # 28.10: «Агрессивная»; «Сигналы» join only after their gate
    paper["profiles"]["own"]["stats"] = {"trades": 31, "avg_trade_pct": 0.4}
    assert [k for k, _ in fu.pick_desks(paper)] == ["aggr", "imp", "own"]
    assert [k for k, _ in fu.pick_desks(paper, ["bal"])] == ["bal"]


def test_main_desk_follows_desk_select_falls_back_to_aggr_and_env_overrides():
    """28.10: the main desk = web/desk_select.json «current» (panel_header.main_desk, the header's own rule); missing / broken /
    not a main-desk key → «Агрессивная»; OKX_FUT_PROFILES overrides everything; «Импульс BTC» is always the second half"""
    paper = {"profiles": {k: {"open": []} for k in ("cons", "bal", "aggr", "imp", "lab", "own")}}
    keys = lambda **kw: [k for k, _ in fu.pick_desks(paper, **kw)]                       # noqa: E731
    assert keys(sel={"current": "bal"}) == ["bal", "imp"] and keys(sel={"current": "cons"}) == ["cons", "imp"]
    assert keys(sel={"current": "own"}) == ["own", "imp"]
    for bad in (None, {}, {"current": "lab"}, {"current": "imp"}, {"current": 5}, ["bal"], "bal"):
        assert keys(sel=bad) == ["aggr", "imp"], bad
    assert keys(forced=["bal"], sel={"current": "cons"}) == ["bal"]
    paper["profiles"]["own"]["stats"] = {"trades": 31, "avg_trade_pct": 0.4}
    assert keys(sel={"current": "own"}) == ["own", "imp"] and keys(sel={"current": "bal"}) == ["bal", "imp", "own"]
    # an open position of the previous main desk is never moved: that desk stays the main one until the position is closed
    assert keys(sel={"current": "bal"}, prev="aggr", held={"aggr", "imp"}) == ["aggr", "imp", "own"]
    assert keys(sel={"current": "bal"}, prev="aggr", held={"imp"}) == ["bal", "imp", "own"]
    assert keys(sel={"current": "bal"}, prev="own", held={"own"}) == ["bal", "imp", "own"]       # own is in the set anyway


def test_fetch_reads_desk_select_next_to_paper_and_a_broken_file_means_aggr(monkeypatch):
    paper = {"profiles": {k: {"open": []} for k in ("cons", "bal", "aggr", "imp")}}
    files = {"paper.json": paper, "desk_select.json": {"current": "bal"}}
    seen = []

    def get(url):
        seen.append(url)
        v = files.get(url.rsplit("/", 1)[-1])
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(fu, "_get_json", get)
    assert [k for k, _ in fu.fetch_desks({})] == ["bal", "imp"]
    assert seen == [fu.SRC, fu.SEL_SRC]
    assert fu.sel_src({"OKX_FUT_SRC": "https://x.example/web/paper.json"}) == "https://x.example/web/desk_select.json"
    for broken in (OSError("404"), {"current": "brk"}, ["x"], None):
        files["desk_select.json"] = broken
        assert [k for k, _ in fu.fetch_desks({})] == ["aggr", "imp"], broken
    files["desk_select.json"] = {"current": "cons"}
    assert [k for k, _ in fu.fetch_desks({"OKX_FUT_PROFILES": "bal,imp"})] == ["bal", "imp"]


def test_a_switch_waits_until_the_old_desks_position_is_closed(monkeypatch):
    """the selector moved the main desk aggr → bal while this account held an aggr trade: the aggr trade is neither closed nor moved,
    no bal trade is opened; once aggr closes its trade on the desk (and here), bal becomes the main desk"""
    fk = F.FakeOkx()
    e = F.env()
    cl = ap.connect(e, fk, {}, F.nosleep)
    t1, t2 = F.trade("A1", "SOL"), F.trade("B1", "BTC", "SHORT", 100000.0, 102000.0, 90000.0)
    files = {"paper.json": {"profiles": {"aggr": {"equity": 10000.0, "open": [t1]}, "bal": {"equity": 10000.0, "open": [t2]},
                                         "imp": {"equity": 10000.0, "open": []}}},
             "desk_select.json": {"current": "aggr"}}
    monkeypatch.setattr(fu, "_get_json", lambda url: files[url.rsplit("/", 1)[-1]])
    step = lambda now: fu.step(e, now=now, sleep=F.nosleep, cl=cl)                      # noqa: E731
    assert step(NOW)["opened"] == 1 and list(fu.load_state(e)["trades"]) == ["aggr:A1"] and fu.load_state(e)["main"] == "aggr"
    files["desk_select.json"] = {"current": "bal"}
    r = step(NOW + 60_000)
    st = fu.load_state(e)
    assert r["opened"] == 0 and r["closed"] == 0 and list(st["trades"]) == ["aggr:A1"] and st["main"] == "aggr"
    assert "SOL-USDT-SWAP" in fk.pos and "BTC-USDT-SWAP" not in fk.pos
    files["paper.json"]["profiles"]["aggr"]["open"] = []                                  # aggr closed A1 → closed here too
    r = step(NOW + 120_000)
    assert r["closed"] == 1 and r["opened"] == 0 and "SOL-USDT-SWAP" not in fk.pos and not fu.load_state(e)["trades"]
    r = step(NOW + 180_000)                                                               # nothing held any more → bal
    st = fu.load_state(e)
    assert r["opened"] == 1 and st["main"] == "bal" and list(st["trades"]) == ["bal:B1"] and "BTC-USDT-SWAP" in fk.pos
    import json
    with open(fu.LOG, encoding="utf-8") as f:
        evs = [json.loads(x) for x in f if x.strip()]
    assert [x for x in evs if x.get("ev") == "desk_main"][-1]["to"] == "bal"


def test_size_from_desk_risk_scaled_to_budget_with_stop_attached_isolated_ioc():
    fk = F.FakeOkx()
    e = F.env()
    r = run(fk, e, F.desks(F.trade(risk=50.0)))                             # desk risk 0.5 % of its $10 000
    assert r["opened"] == 1 and not r["errors"]
    o = swap_orders(fk)[0]
    budget = 10000 * 0.15                                                    # owner 28.10: 15 % (no live start yet: of the account now)
    part = budget / 2                                                        # two desks (bal + imp) share the budget
    worst = float(o["px"])
    risk = float(o["sz"]) * 1 * (worst - 190.0)                              # SOL ctVal 1
    assert abs(risk - part * 0.005) <= 2.0 and risk <= budget * 0.01 + 1e-6   # the desk's own 0.5 % of this desk's part
    assert o["tdMode"] == "isolated" and o["ordType"] == "ioc" and o["side"] == "buy" and o["clOrdId"].startswith("cnqf")
    assert worst <= 200 * 1.001 * 1.0041                                     # never more than OKX_SLIP above the ask
    a = o["attachAlgoOrds"][0]
    assert a["slTriggerPx"] == "190.000" and a["slOrdPx"] == "-1" and a["slTriggerPxType"] == "last"
    assert a["tpTriggerPx"] == "230.000" and a["tpTriggerPxType"] == "last"
    assert fk.levers == [{"instId": "SOL-USDT-SWAP", "lever": "2", "mgnMode": "isolated"}]
    st = fu.load_state(e)
    assert list(st["trades"]) == ["bal:T1"] and st["trades"]["bal:T1"]["stop"] == 190.0


def test_risk_as_the_system_capped_at_one_percent_and_leverage_never_above_three():
    """Owner 22.10: «риск фьючерсов как у системы» — the desk's own risk share × this desk's part; 1 % of the budget is the ceiling."""
    fk = F.FakeOkx()
    e = F.env(OKX_FUT_MAX_LEV="10", OKX_FUT_RISK="0.05")                   # env can't loosen the hard caps
    assert fu.caps(e) == {"lev": 3, "risk": 0.01, "max_pos": 5, "day_loss": 0.03}
    run(fk, e, F.desks(F.trade(risk=500.0)))                                 # desk risked 5 % → capped at 1 % of the budget
    o = swap_orders(fk)[0]
    assert float(o["sz"]) * (float(o["px"]) - 190.0) <= 1500 * 0.01 + 1e-6
    fk2 = F.FakeOkx()
    run(fk2, F.env(), F.desks(F.trade(risk=150.0)))                          # desk risked 1.5 % → 1.5 % of the $750 part = $11.25
    o2 = swap_orders(fk2)[0]
    r2 = float(o2["sz"]) * (float(o2["px"]) - 190.0)
    assert 10.0 <= r2 <= 11.25 + 1e-6
    assert fk.levers[0]["lever"] == "3"
    fk = F.FakeOkx()                                                         # a 0.3 % stop: the leverage room is the limit
    run(fk, F.env(), F.desks(F.trade(entry=200.0, stop=199.0, tp=210.0, risk=500.0)))
    o = swap_orders(fk)[0]
    assert float(o["sz"]) * float(o["px"]) <= 2500 * 2 + 1e-6


def test_desk_close_is_a_reduce_only_market_exit_and_stops_are_cancelled():
    fk = F.FakeOkx()
    e = F.env()
    run(fk, e, F.desks(F.trade(), F.trade("T2", "BTC", "SHORT", 100000.0, 102000.0, 90000.0)))
    assert fk.pos == {"SOL-USDT-SWAP": 1.13, "BTC-USDT-SWAP": -0.49} or len(fk.pos) == 2
    r = run(fk, e, F.desks(F.trade()), NOW + 60_000)                         # the desk closed BTC
    assert r["closed"] == 1
    ex = swap_orders(fk)[-1]
    assert ex["instId"] == "BTC-USDT-SWAP" and ex["side"] == "buy" and ex["ordType"] == "market" and ex["reduceOnly"] is True
    assert "BTC-USDT-SWAP" not in fk.pos and not [a for a in fk.algos if a["instId"] == "BTC-USDT-SWAP"]
    assert list(fu.load_state(e)["trades"]) == ["bal:T1"]


def test_moved_stop_is_amended_partial_exit_is_reduce_only_and_exchange_close_is_forgotten():
    fk = F.FakeOkx()
    e = F.env()
    t = F.trade(qty=10.0, risk=100.0)
    run(fk, e, F.desks(t))
    c0 = fk.pos["SOL-USDT-SWAP"]
    t2 = dict(t, stop_now=195.0)
    r = run(fk, e, F.desks(t2), NOW + 60_000)
    assert r["stops"] == 1 and fk.amends[-1]["newSlTriggerPx"] == "195.000" and fk.amends[-1]["newSlTriggerPxType"] == "last"
    t3 = dict(t2, qty_left=5.0)                                              # the desk took half off
    r = run(fk, e, F.desks(t3), NOW + 120_000)
    assert r["reduced"] == 1
    cut = swap_orders(fk)[-1]
    assert cut["reduceOnly"] is True and cut["side"] == "sell" and abs(float(cut["sz"]) - (c0 - round(c0 * 0.5 - 0.005, 2))) < 0.02
    assert any(a.get("newSz") for a in fk.amends)                            # the stop now covers the smaller position exactly
    fk.pos.clear()                                                           # OKX hit the stop
    r = run(fk, e, F.desks(t3), NOW + 180_000)
    assert r["gone"] == 1 and not fu.load_state(e)["trades"] and not fk.pos   # and never re-opened while the desk holds it


def test_missing_stop_is_placed_for_the_whole_position_or_the_position_is_closed():
    fk = F.FakeOkx(attach=False)                                             # OKX did not keep the attached stop
    e = F.env()
    r = run(fk, e, F.desks(F.trade()))
    assert r["opened"] == 1 and fk.algo_posts
    p = fk.algo_posts[0]
    assert p["closeFraction"] == "1" and p["reduceOnly"] is True and p["slTriggerPx"] == "190.000" and p["slTriggerPxType"] == "last"
    fk = F.FakeOkx(attach=False, algo_ok=False)                             # and OKX refuses a new stop too → closed at once
    e = F.env()
    r = run(fk, e, F.desks(F.trade()))
    assert r["opened"] == 0 and r["errors"] == 1 and not fk.pos and swap_orders(fk)[-1]["reduceOnly"] is True
    assert not fu.load_state(e)["trades"]


def test_max_five_positions_one_per_instrument_and_old_signals_not_chased():
    fk = F.FakeOkx(balances={"USDT": 100000.0})
    e = F.env()
    coins = ["SOL", "APT", "XRP", "LINK", "AVAX", "ADA", "DOT"]
    ts = [F.trade(f"T{i}", c, entry=F.PX[c], stop=F.PX[c] * 0.95, tp=F.PX[c] * 1.2, risk=50.0) for i, c in enumerate(coins)]
    ts.append(F.trade("TOLD", "BTC", entry=95000.0, stop=90000.0, tp=120000.0, opened=NOW - 3 * 3600_000))   # price 5 % above
    r = run(fk, e, F.desks(*ts, imp=[F.trade("I1", "SOL", entry=200.0, stop=190.0, opened=NOW - 180_000)]))
    assert r["opened"] == 5 and len(fk.pos) == 5 and "BTC-USDT-SWAP" not in fk.pos
    why = " ".join(x["why"] for x in r["skipped"])
    assert "предел 5" in why and "уже есть позиция" in why
    assert "пропущено: сделка открыта раньше, цена ушла на 5,37% от входа системы" in why


def test_late_join_guard_old_trade_only_near_the_desk_entry_and_risk_from_the_worst_fill():
    """19.10 AVAX trade at 9.772 while the price is 9.86 now (+0.9 %): skipped; at 9.79 (+0.2 % with the ask) it is joined."""
    F.PX["AVAX"] = 9.86
    try:
        fk = F.FakeOkx()
        e = F.env(CNQ_LIVE_TRADING="yes")
        fk.demo_key = False
        old = F.trade("AV", "AVAX", entry=9.772, stop=9.2, tp=11.0, risk=100.0, opened=NOW - 3 * 86_400_000)
        cl = ap.connect(e, fk, {}, F.nosleep)
        text = "\n".join(fu.plan_lines(cl, cl.config(), e, F.desks(old), NOW))
        assert "пропущено: сделка открыта раньше, цена ушла на 1,00% от входа системы (порог 0,3%)" in text and not fk.orders
        r = fu.step(e, desks=F.desks(old), now=NOW, sleep=F.nosleep, cl=cl)
        assert r["opened"] == 0 and "цена ушла" in r["skipped"][0]["why"] and fu.load_state(e)["first_live"] == NOW
        F.PX["AVAX"] = 9.775                                                     # back to the desk's entry (ask +0.13 %)
        r = fu.step(e, desks=F.desks(old), now=NOW + 60_000, sleep=F.nosleep, cl=cl)
        assert r["opened"] == 1
        o = swap_orders(fk)[0]
        assert float(o["sz"]) * (float(o["px"]) - 9.2) <= 2500 * 0.005 + 1e-6   # risk from the worst possible fill vs the desk stop
        new = F.trade("NW", "SOL", entry=195.0, stop=185.0, tp=230.0, opened=NOW + 90_000)   # opened after the first live start
        r = fu.step(e, desks=F.desks(old, new), now=NOW + 120_000, sleep=F.nosleep, cl=cl)
        assert r["opened"] == 1                                                  # new signal: joined though the price is 2.7 % away
        assert fu.join_gap({"OKX_FUT_JOIN_GAP": "0.01"}) == 0.01 and fu.join_gap({"OKX_FUT_JOIN_GAP": "0.5"}) == fu.JOIN_GAP
    finally:
        F.PX["AVAX"] = 30.0


def test_daily_loss_stop_blocks_new_entries_for_the_day():
    budget = 10000 * 0.25
    fk = F.FakeOkx(hist=[{"instId": "SOL-USDT-SWAP", "realizedPnl": str(-(budget * 0.03 + 1)), "uTime": str(NOW - 3600_000)}])
    e = F.env()
    r = run(fk, e, F.desks(F.trade()))
    assert r["opened"] == 0 and not swap_orders(fk) and "дневной стоп" in r["skipped"][0]["why"]
    st = fu.load_state(e)
    assert st["day_stop"] == "2027-01-15" and fu.status(e)["day_stop"] is False or st["day_stop"] == "2027-01-15"
    fk2 = F.FakeOkx(hist=[{"instId": "SOL-USDT-SWAP", "realizedPnl": "-10", "uTime": str(NOW - 86_400_000 * 2)}])   # yesterday's loss
    assert run(fk2, F.env(), F.desks(F.trade()))["opened"] == 1


def test_kill_switch_and_refusals_place_nothing():
    tmp = tempfile.mkdtemp()
    e = F.env(tmp)
    open(e["OKX_STOP_FILE"], "w").close()
    fk = F.FakeOkx()
    r = fu.step(e, desks=F.desks(F.trade()), now=NOW, sleep=F.nosleep, cl=ap.connect(e, fk, {}, F.nosleep))
    assert r["opened"] == 0 and "стоп" in r["why"] and not swap_orders(fk)
    os.remove(e["OKX_STOP_FILE"])
    cases = [(F.FakeOkx(demo_key=False), F.env(), "РЕАЛЬНОГО"),                        # a real key without CNQ_LIVE_TRADING=yes
             (F.FakeOkx(perm="read_only,trade,withdraw"), F.env(), "ВЫВОДА"),
             (F.FakeOkx(acct_lv="1"), F.env(), "«Спот»"),
             (F.FakeOkx(role="2"), F.env(), "копирование")]
    for fk, e, word in cases:
        r = run(fk, e, F.desks(F.trade()))
        assert r["opened"] == 0 and word in r["why"] and not swap_orders(fk) and not fk.levers, word
    assert fu.step(F.env(OKX_FUTURES="off"), desks=F.desks(F.trade()))["why"].startswith("фьючерсы выключены")


def test_live_mode_trades_with_a_real_key_only_inside_the_window():
    fk = F.FakeOkx(demo_key=False)
    r = run(fk, F.env(CNQ_LIVE_TRADING="yes"), F.desks(F.trade()))
    assert r["opened"] == 1 and all("x-simulated-trading" not in c[5] for c in fk.calls)
    fk = F.FakeOkx()
    run(fk, F.env(), F.desks(F.trade()))
    assert all(c[5].get("x-simulated-trading") == "1" for c in fk.calls)   # demo by default


def test_lead_trader_stops_and_exits_go_through_the_lead_positions():
    fk = F.FakeOkx(role="1", lead_insts=["SOL-USDT-SWAP"])
    e = F.env()
    r = run(fk, e, F.desks(F.trade(), F.trade("T2", "APT", entry=1.0, stop=0.95, tp=1.2)))
    # 22.10: APT was not a lead instrument → added first (SWAP, the full list), entered in the same pass, copied
    assert r["opened"] == 2 and not r["skipped"]
    assert fk.set_calls == [{"instType": "SWAP", "instId": "APT-USDT-SWAP,SOL-USDT-SWAP"}] and fk.swap_lead == {"SOL-USDT-SWAP", "APT-USDT-SWAP"}
    assert fu.load_state(e)["trades"]["bal:T2"]["copy"] is True
    assert ("stop", {"instType": "SWAP", "subPosId": "s-SOL-USDT-SWAP", "slTriggerPx": "190.000", "slTriggerPxType": "last",
                     "tpTriggerPx": "230.000", "tpTriggerPxType": "last"}) in fk.lead_calls
    r = run(fk, e, F.desks(), NOW + 60_000)
    assert r["closed"] == 2 and ("close", {"instType": "SWAP", "subPosId": "s-SOL-USDT-SWAP", "ordType": "market"}) in fk.lead_calls


def test_lead_swap_refused_by_okx_is_entered_as_an_ordinary_position_and_managed_ordinarily():
    fk = F.FakeOkx(role="1", lead_insts=["SOL-USDT-SWAP"], swap_set_reject={"APT-USDT-SWAP"})
    e = F.env()
    t = F.trade("T2", "APT", entry=1.0, stop=0.95, tp=1.2, qty=2000.0, risk=100.0)
    r = run(fk, e, F.desks(t))
    m = fu.load_state(e)["trades"]["bal:T2"]
    assert r["opened"] == 1 and m["copy"] is False and "APT-USDT-SWAP" in fk.pos and "APT-USDT-SWAP" not in fk.swap_lead
    assert fu.load_state(e)["lead_refused"].keys() == {"APT-USDT-SWAP"}
    n = len(fk.set_calls)
    r = run(fk, e, F.desks(dict(t, qty_left=1000.0)), NOW + 60_000)        # partial exit: an ordinary reduce-only order
    assert r["reduced"] == 1 and swap_orders(fk)[-1]["reduceOnly"] is True and len(fk.set_calls) == n
    r = run(fk, e, F.desks(), NOW + 120_000)                                  # the desk closed it: reduce-only market, no lead call
    assert r["closed"] == 1 and swap_orders(fk)[-1]["reduceOnly"] is True and "APT-USDT-SWAP" not in fk.pos
    assert not [c for c in fk.lead_calls if c[0] == "close"]


def test_a_position_opened_before_the_lead_role_keeps_its_ordinary_stop_and_reduce_only_close():
    fk = F.FakeOkx(lead_insts=["SOL-USDT-SWAP"])
    e = F.env()
    t = F.trade("T2", "APT", entry=1.0, stop=0.95, tp=1.2)
    assert run(fk, e, F.desks(t))["opened"] == 1 and fk.algos                  # an ordinary APT position, its stop attached
    fk.role = "1"                                                             # the account became a futures lead trader
    r = run(fk, e, F.desks(dict(t, stop_now=0.97)), NOW + 60_000)
    assert r["stops"] == 1 and fk.amends[-1]["newSlTriggerPx"] == "0.970" and not fk.lead_calls and not fk.set_calls
    r = run(fk, e, F.desks(), NOW + 120_000)
    assert r["closed"] == 1 and swap_orders(fk)[-1]["reduceOnly"] is True and not fk.pos and not fk.lead_calls


def test_hedge_mode_exits_close_the_same_side_never_open_the_other():
    fk = F.FakeOkx(pos_mode="long_short_mode")
    e = F.env()
    run(fk, e, F.desks(F.trade()))
    o = swap_orders(fk)[0]
    assert o["posSide"] == "long" and fk.levers[0]["posSide"] == "long"
    run(fk, e, F.desks(), NOW + 60_000)
    ex = swap_orders(fk)[-1]
    assert ex["side"] == "sell" and ex["posSide"] == "long" and "reduceOnly" not in ex and not fk.pos


def test_fut_close_closes_everything_even_with_the_kill_switch():
    fk = F.FakeOkx()
    e = F.env()
    run(fk, e, F.desks(F.trade(), F.trade("T2", "BTC", "SHORT", 100000.0, 102000.0, 90000.0)))
    open(e["OKX_STOP_FILE"], "w").close()
    msg = fu.close_all(e, now=NOW + 60_000, sleep=F.nosleep, cl=ap.connect(e, fk, {}, F.nosleep))
    assert "закрыто позиций 2" in msg and not fk.pos and all(o["reduceOnly"] for o in swap_orders(fk)[2:])
    os.remove(e["OKX_STOP_FILE"])
    r = run(fk, e, F.desks(F.trade(), F.trade("T2", "BTC", "SHORT", 100000.0, 102000.0, 90000.0)), NOW + 120_000)
    assert r["opened"] == 0 and not fk.pos                                   # the desk still holds them: not opened again


def test_plan_shows_the_same_order_the_step_places():
    fk = F.FakeOkx()
    e = F.env()
    cl = ap.connect(e, fk, {}, F.nosleep)
    text = "\n".join(fu.plan_lines(cl, cl.config(), e, F.desks(F.trade()), NOW))
    assert not fk.orders
    run(fk, e, F.desks(F.trade()))
    o = swap_orders(fk)[0]
    assert f"купить {o['sz']} контр. SOL-USDT-SWAP" in text and f"не хуже {float(o['px']):.6g}" in text and "стоп-лосс 190" in text


def test_active_spot_follows_the_impulse_desk():
    fk = F.FakeOkx(balances={"USDT": 10000.0})
    e = F.env()
    cl = ap.connect(e, fk, {}, F.nosleep)
    t = {"id": "I1", "symbol": "XRP", "side": "LONG", "entry": 2.0, "stop": 1.6, "qty": 1000.0, "qty_left": 1000.0, "opened": NOW - 60_000}
    r = ac.step(e, desk={"equity": 10000.0, "open": [t]}, now=NOW, sleep=F.nosleep, cl=cl, total=10000.0)
    assert r["bought"] == 1
    o = fk.orders[-1]
    assert o["instId"] == "XRP-USDT" and o["side"] == "buy" and o["ordType"] == "ioc" and abs(float(o["sz"]) * 2.0 - 200.0) < 2.0   # 20 % of $1 000
    assert ac.held(e) == {"XRP": float(o["sz"])}
    r = ac.step(e, desk={"equity": 10000.0, "open": []}, now=NOW + 60_000, sleep=F.nosleep, cl=cl)
    assert r["sold"] == 1 and fk.orders[-1]["side"] == "sell" and ac.held(e) == {}
    assert "ждёт сигнала" in "\n".join(ac.plan_lines(e, {"open": []}, NOW, 10000.0))
