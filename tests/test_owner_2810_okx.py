"""Owner's decisions of 28.10.2026 on the real account (answered explicitly), with a fake OKX (tests/okx_fake.py):
1. leverage stays 2x (max 3x); 2. futures 25 % → 15 %, the freed 10 % → the core BTC / ETH / SOL 30 / 10 / 5;
3. «Импульс BTC» waits in BTC between its trades, its BTC sold on the desk's exit, USDT until the next entry or 3 days, the BTC
   pays for an entry, its BTC counted apart from the portfolio's; 4. the futures budget = 15 % of the starting capital + its own
result. Every coin, key and amount here is made-up test data; no real API is called."""
import json
import os
import tempfile

import okx_fake as F
from cnq.execution import okx_active as ac
from cnq.execution import okx_alloc as al
from cnq.execution import okx_autopilot as ap
from cnq.execution import okx_futures as fu
from cnq.execution import public_real as pr

ap.LOG = os.path.join(tempfile.mkdtemp(), "okx_autopilot.jsonl")
fu.LOG = os.path.join(tempfile.mkdtemp(), "okx_futures.jsonl")
ac.LOG = os.path.join(tempfile.mkdtemp(), "okx_active.jsonl")
NOW = F.NOW
DAY = 86_400_000


def _events(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def test_leverage_stays_two_max_three():
    assert fu.DEF_LEV == 2 and fu.MAX_LEV == 3 and fu.caps({})["lev"] == 2 and fu.caps({"OKX_FUT_MAX_LEV": "20"})["lev"] == 3


def test_first_run_moves_the_futures_ten_percent_to_the_core_and_sells_nothing_of_it():
    fk = F.FakeOkx(balances={"USDT": 10000.0})
    e = F.env()
    cl = ap.connect(e, fk, {}, F.nosleep)
    b = ap.build(cl, e, F.portfolio())
    buys = {o["ccy"]: o["usd"] for o in b["buys"]}
    for c, plan in (("BTC", 0.30), ("ETH", 0.10), ("SOL", 0.05)):          # the portfolio's core + the extra core 30 / 10 / 5 of 10 %
        want = (0.65 * plan + 0.10 * plan / 0.45) * 10000
        assert abs(buys[c] - want) <= 0.01 * want + 10, c
    assert not b["sells"] and [o["ccy"] for o in b["buys"]][:3] == ["BTC", "ETH", "SOL"] and not fk.orders
    assert "Ядро BTC / ETH / SOL (доп., 30 / 10 / 5): 10%" in ap.plan_text(b, "демо")
    # the signature changed with the split → an armed window rebalances on its next check
    assert ap.model_sig(F.portfolio(), e) != al.signature(F.portfolio(), {"spot": 0.65, "futures": 0.25, "active": 0.1, "reserve": 0.0})


def test_futures_budget_is_its_share_of_the_start_plus_its_own_result_never_below_the_open_margin(monkeypatch):
    monkeypatch.setattr(ap, "LOG", os.path.join(tempfile.mkdtemp(), "okx_autopilot.jsonl"))   # no live start from another test's log
    hist = [{"instId": "SOL-USDT-SWAP", "uTime": str(NOW - 3_600_000), "realizedPnl": "-30"},
            {"instId": "APT-USDT-SWAP", "uTime": str(NOW - 40 * DAY), "realizedPnl": "500"}]            # before the start: not counted
    fk = F.FakeOkx(hist=hist, balances={"USDT": 7000.0})                     # the account fell (spot losses) — the budget does not
    e = F.env(OKX_START_USD="10000", OKX_START_AT=str(NOW - 5 * DAY))
    cl = ap.connect(e, fk, {}, F.nosleep)
    b = fu.budget(cl, {}, e, NOW, fresh=True)
    assert b["how"] == "own" and b["capital"] == 10000.0 and b["realized"] == -30.0 and b["usd"] == 1470.0 and b["share"] == 0.15
    assert "15% стартового капитала $10 000.00 + результат фьючерсов −$30.00" in fu.budget_text(b)
    assert al.fut_budget(0.15, 10000.0, -1400.0, -90.0, 120.0) == 120.0          # never below the margin of the open positions
    # no known start (a demo key before its first live run): the old rule, the share of the account now
    b0 = fu.budget(ap.connect(F.env(), F.FakeOkx(), {}, F.nosleep), {}, F.env(), NOW, fresh=True)
    assert b0["how"] == "account" and b0["usd"] == 1500.0


def test_the_app_line_moved_disappears_when_the_futures_part_is_its_own_money():
    from cnq.agents import app_home
    since, now = NOW - 5 * DAY, NOW
    s0, fpnl = 10024.4, 0.27
    fut = round(0.15 * s0 + fpnl, 2)                                         # the budget = what the futures part holds
    eq = 9925.07
    real = {"exchange": "OKX", "since": since, "start_usd": s0, "equity_usd": eq, "pnl_usd": eq - s0, "pnl_pct": -0.99,
            "today_usd": 0.0, "today_pct": 0.0, "spot": [], "usdt_pct": 50.0, "usdt_usd": 5000.0, "futures": [], "closed_n": 1,
            "alloc": {"spot_coins_usd": 4000.0, "spot_cash_usd": 1600.0, "fut_budget_usd": fut, "fut_margin_usd": 0.0, "fut_reserve_usd": fut,
                      "active_coins_usd": 990.0, "active_wait_usd": 0.0, "other_usd": 0.0, "spot_pct": 65.0, "fut_pct": 15.0, "active_pct": 10.0,
                      "core_pct": 10.0},
            "curve": [[since, 0.0], [now - 60_000, eq - s0]], "asof": now - 60_000, "stale": False, "spot_pnl_usd": eq - s0 - fpnl,
            "fut_pnl_usd": fpnl, "closed": [], "fut_curve": [], "actions": [], "flows": {}}
    d = tempfile.mkdtemp()
    from test_public_real import _pp_files
    _pp_files(d, real)
    pp = app_home.run(None, d, now=now)["public_portfolio"]
    assert pp["moved"] is None and pp["futures"]["moved_note"] is None and pp["spot"]["moved_note"] is None
    assert pp["futures"]["start_usd"] == round(0.15 * s0, 2) and pp["spot"]["start_usd"] == round(0.85 * s0, 2)


def test_impulse_sleeve_waits_in_btc_sells_it_on_the_exit_pauses_three_days_and_pays_entries_with_it():
    fk = F.FakeOkx(balances={"USDT": 10000.0})
    e = F.env()
    cl = ap.connect(e, fk, {}, F.nosleep)
    desk = {"equity": 10000.0, "open": [], "closed": [{"id": "OLD", "closed": NOW - 9 * DAY}]}       # an old exit never triggers
    r = ac.step(e, desk=desk, now=NOW, sleep=F.nosleep, cl=cl, total=10000.0)
    o = fk.orders[-1]
    assert r["bought"] == 1 and o["instId"] == "BTC-USDT" and o["side"] == "buy" and o["ordType"] == "ioc"
    q = float(o["sz"])
    assert 950 <= q * 100000 <= 1001 and ac.held(e) == {"BTC": q}            # ≈ 10 % of the $10 000 account, in BTC
    ev = [x for x in _events(ac.LOG) if x.get("ev") == "park"][-1]
    assert "сигнала нет" in ev["text"] and "держим в BTC" in ev["text"]
    # the spot rebalance and the extra core never count or sell the sleeve's BTC
    b = ap.build(cl, e, F.portfolio())
    assert b["active_held"] == {"BTC": q} and not [x for x in b["sells"] if x["ccy"] == "BTC"]
    n = len(fk.orders)
    ac.step(e, desk=desk, now=NOW + 60_000, sleep=F.nosleep, cl=cl, total=10000.0)
    assert len(fk.orders) == n                                               # parked: nothing more to do
    # an entry signal: the BTC already held pays for the buy (sold first, as much as needed)
    t = {"id": "I1", "symbol": "XRP", "side": "LONG", "entry": 2.0, "stop": 1.6, "qty": 1000.0, "qty_left": 1000.0, "opened": NOW + 2 * 60_000}
    r = ac.step(e, desk={**desk, "open": [t]}, now=NOW + 3 * 60_000, sleep=F.nosleep, cl=cl, total=10000.0)
    a, b_ = fk.orders[-2], fk.orders[-1]
    assert a["instId"] == "BTC-USDT" and a["side"] == "sell" and 190 <= float(a["sz"]) * 100000 <= 205
    assert b_["instId"] == "XRP-USDT" and b_["side"] == "buy" and r["bought"] == 1
    held = ac.held(e)
    assert set(held) == {"BTC", "XRP"} and abs(held["BTC"] - (q - float(a["sz"]))) < 1e-9
    # the desk's exit: the alt is sold, the sleeve's BTC too; USDT until the next entry or 3 days
    t_exit = NOW + 12 * 3_600_000
    r = ac.step(e, desk={"equity": 10000.0, "open": [], "closed": [{"id": "I1", "closed": t_exit - 60_000}]}, now=t_exit,
                sleep=F.nosleep, cl=cl, total=10000.0)
    st = ac.load_state(e)
    assert ac.held(e) == {} and [x["instId"] + ":" + x["side"] for x in fk.orders[-2:]] == ["XRP-USDT:sell", "BTC-USDT:sell"]
    assert st["pause_until"] == t_exit - 60_000 + 3 * DAY
    texts = [x.get("text") or "" for x in _events(ac.LOG)]
    assert any("закрыл сделку — BTC этой части продан в USDT" in x for x in texts) and any("в USDT до следующего сигнала" in x for x in texts)
    n = len(fk.orders)
    ac.step(e, desk={"equity": 10000.0, "open": [], "closed": [{"id": "I1", "closed": t_exit - 60_000}]}, now=t_exit + DAY,
            sleep=F.nosleep, cl=cl, total=10000.0)
    assert len(fk.orders) == n and ac.held(e) == {}                          # the pause: still USDT a day later
    r = ac.step(e, desk={"equity": 10000.0, "open": [], "closed": [{"id": "I1", "closed": t_exit - 60_000}]}, now=t_exit + 3 * DAY,
                sleep=F.nosleep, cl=cl, total=10000.0)
    assert fk.orders[-1]["instId"] == "BTC-USDT" and fk.orders[-1]["side"] == "buy" and set(ac.held(e)) == {"BTC"}
    assert "снова в BTC" in " ".join(x.get("text") or "" for x in _events(ac.LOG)[-3:])
    # the account's public actions name both moves in plain words (no order id)
    A = pr.actions(0, {"spot": ap.LOG, "active": ac.LOG, "fut": fu.LOG})              # the log stamps the wall clock
    kinds = [x["kind"] for x in A]
    assert "active_park" in kinds and "active_unpark" in kinds and all("ordId" not in json.dumps(x) for x in A)
    assert pr.clean({**{k: None for k in pr.KEYS}, "closed": [], "fut_curve": [], "flows": {}, "actions": A, "alloc": {}})["actions"]


def test_impulse_parking_switched_off_sells_the_btc_and_waits_in_usdt():
    fk = F.FakeOkx(balances={"USDT": 10000.0})
    e = F.env()
    cl = ap.connect(e, fk, {}, F.nosleep)
    ac.step(e, desk={"equity": 10000.0, "open": []}, now=NOW, sleep=F.nosleep, cl=cl, total=10000.0)
    assert set(ac.held(e)) == {"BTC"}
    r = ac.step({**e, "OKX_ACT_PARK": "off"}, desk={"equity": 10000.0, "open": []}, now=NOW + 60_000, sleep=F.nosleep, cl=cl, total=10000.0)
    assert r["sold"] == 1 and fk.orders[-1]["side"] == "sell" and ac.held(e) == {}
    assert "в USDT" in "\n".join(ac.plan_lines({**e, "OKX_ACT_PARK": "off"}, {"open": []}, NOW, 10000.0))
