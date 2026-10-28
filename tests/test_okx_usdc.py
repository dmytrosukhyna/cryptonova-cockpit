"""«Автопилот OKX» через USDC (22.10.2026, owner: «если по USDT ничего нету, то надо через USDC конвертировать»): a coin with no
{COIN}-USDT pair goes USDT → USDC → COIN (and back) on spot and active spot, a coin with only a USDC-margined swap gets its margin as
USDC, free USDC goes back to USDT, USDC is cash in every share; and the planned restart onto new code keeps the lease. A fake OKX
(tests/okx_fake.py): every coin («UUU»), key and amount here is made-up test data; no real API is called."""
import json
import os
import tempfile

import okx_fake as F
from cnq.execution import okx_active as ac
from cnq.execution import okx_autopilot as ap
from cnq.execution import okx_futures as fu
from cnq.execution import okx_lease as L

ap.LOG = os.path.join(tempfile.mkdtemp(), "okx_autopilot.jsonl")
fu.LOG = os.path.join(tempfile.mkdtemp(), "okx_futures.jsonl")
ac.LOG = os.path.join(tempfile.mkdtemp(), "okx_active.jsonl")
NOW = F.NOW


def pf(*extra, base=True):
    p = F.portfolio()
    pos = p["portfolios"]["ideas"]["positions"]
    if not base:
        pos[:] = []
    for sym, w in extra:
        pos.append({"symbol": sym, "weight": w, "price": 4.0, "status": "OPEN", "name": sym, "bucket": "mid", "kind_ru": "крупный альт",
                    "role_ru": "из таблицы: можно брать", "table_why_ru": f"анкета {sym}", "levels": {"stop": 3.0, "take_half": None},
                    "target_weight": w})
    return p


def fake(**kw):
    kw.setdefault("demo_key", False)
    kw.setdefault("usdc_only", ("UUU",))
    kw.setdefault("strict", True)
    kw.setdefault("balances", {"USDT": 10000.0})
    return F.FakeOkx(**kw)


def live(tmp, **kw):
    return F.env(tmp, CNQ_LIVE_TRADING="yes", OKX_AUTOPILOT="on", **kw)


def coin_orders(fk, inst):
    return [o for o in fk.orders if o["instId"] == inst]


def events(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f]


def test_usdc_only_coin_is_bought_usdt_usdc_coin_with_the_route_in_the_plan_and_the_caps_on_the_coin_leg():
    tmp = tempfile.mkdtemp()
    fk = fake()
    e = live(tmp, OKX_MAX_ORDER_USD="200")
    lp = os.path.join(tmp, "log.jsonl")
    b = ap.build(ap.connect(e, fk, {}, F.nosleep), e, pf(("UUU", 0.075)))
    uuu = [o for o in b["buys"] if o["ccy"] == "UUU"]
    assert uuu and all(o["inst"] == "UUU-USDC" and o["via"] == "USDC" for o in uuu)
    assert abs(sum(o["usd"] for o in uuu) - 10000 * 0.65 * 0.075) < 5                 # the coin's share, as for any coin
    assert all(o["usd"] <= 200 for o in uuu) and len(uuu) >= 3                          # the per-order cap on the coin leg
    assert not any("UUU" in d for d in b["diffs"])                                       # the account CAN follow it now
    text = ap.plan_text(b, "РЕАЛЬНЫЕ ДЕНЬГИ")
    assert "через USDC: USDT → USDC → UUU" in text and "USDC · через USDC" in text
    r = ap.run_once(e, fk, pf(("UUU", 0.075)), NOW, F.nosleep, log_path=lp)
    assert r["ok"] and r["failed"] == 0 and not r["half"]
    seq = [o["instId"] for o in fk.orders if o["instId"] in ("USDC-USDT", "UUU-USDC")]
    assert seq[0] == "USDC-USDT" and seq[1] == "UUU-USDC"                               # USDC first, then the coin
    for o in coin_orders(fk, "UUU-USDC"):
        assert o["ordType"] == "ioc" and o["tdMode"] == "cash" and float(o["px"]) <= 4.0 * 1.001 * 1.004 + 0.001       # ask + OKX_SLIP, one tick up
        assert float(o["sz"]) * float(o["px"]) <= 200 * 1.01
    for o in coin_orders(fk, "USDC-USDT"):
        assert o["ordType"] == "ioc" and (float(o["px"]) <= 1.001 * 1.004 + 0.0001 if o["side"] == "buy" else float(o["px"]) >= 0.999 * 0.996 - 0.0001)
    got = fk.bal["UUU"] * 4.0
    assert abs(got - 10000 * 0.65 * 0.075) < 10
    legs = sum(float(o["sz"]) * float(o["px"]) for o in fk.orders if o["instId"] != "USDC-USDT")
    assert abs(ap.load_state(e)["day_usd"] - legs) < 1.0                                # the day's cap counts the coin legs only
    assert fk.bal.get("USDC", 0) < ap.SWEEP_MIN                                         # nothing left in USDC


def test_day_cap_stops_before_any_usdc_is_bought():
    tmp = tempfile.mkdtemp()
    fk = fake()
    r = ap.run_once(live(tmp, OKX_MAX_DAY_USD="100"), fk, pf(("UUU", 0.5), base=False), NOW, F.nosleep)
    assert r["ok"] and r["limit"] and not fk.orders                                      # no USDC-USDT leg either


def test_usdc_only_coin_is_sold_coin_usdc_usdt():
    tmp = tempfile.mkdtemp()
    fk = fake(balances={"USDT": 9000.0, "UUU": 250.0})
    e = live(tmp)
    b = ap.build(ap.connect(e, fk, {}, F.nosleep), e, pf())
    s = [o for o in b["sells"] if o["ccy"] == "UUU"]
    assert s and s[0]["inst"] == "UUU-USDC" and "через USDC: UUU → USDC → USDT" in ap.plan_text(b, "x")
    r = ap.run_once(e, fk, pf(), NOW, F.nosleep, log_path=os.path.join(tmp, "l"))
    assert r["ok"] and not r["half"] and fk.bal["UUU"] == 0
    i = [o["instId"] for o in fk.orders].index("UUU-USDC")
    assert fk.orders[i]["side"] == "sell" and fk.orders[i + 1]["instId"] == "USDC-USDT" and fk.orders[i + 1]["side"] == "sell"
    assert fk.bal.get("USDC", 0) < 1.0                                                   # the USDC went straight back to USDT


def test_usdc_is_cash_in_every_share_and_free_usdc_goes_back_to_usdt():
    tmp = tempfile.mkdtemp()
    e = live(tmp)
    a = ap.build(ap.connect(e, fake(balances={"USDT": 9500.0}), {}, F.nosleep), e, pf())
    fk = fake(balances={"USDT": 9000.0, "USDC": 500.0})
    b = ap.build(ap.connect(e, fk, {}, F.nosleep), e, pf())
    assert a["total_usd"] == b["total_usd"] and a["targets"] == b["targets"] and a["same"] == b["same"] and a["diffs"] == b["diffs"]
    assert [(o["ccy"], o["usd"]) for o in a["buys"] + a["sells"]] == [(o["ccy"], o["usd"]) for o in b["buys"] + b["sells"]]
    assert any("USDC" in n and "вернёт в USDT" in n for n in b["notes"])
    lp = os.path.join(tmp, "l")
    r = ap.run_once(e, fk, pf(), NOW, F.nosleep, log_path=lp)
    assert r["ok"] and r["usdc_back"] >= 400 and fk.bal["USDC"] < ap.SWEEP_MIN
    last = fk.orders[-1]
    assert last["instId"] == "USDC-USDT" and last["side"] == "sell" and last["ordType"] == "ioc"
    assert any(x["ev"] == "usdc_back" for x in events(lp))
    fk2 = fake(balances={"USDT": 9000.0, "USDC": 500.0})                                # OKX_KEEP=USDC → never swept
    ap.run_once(live(tempfile.mkdtemp(), OKX_KEEP="USDC"), fk2, pf(), NOW, F.nosleep, log_path=lp)
    assert fk2.bal["USDC"] == 500.0 and not coin_orders(fk2, "USDC-USDT")


def test_one_leg_failing_never_leaves_a_half():
    tmp = tempfile.mkdtemp()
    lp = os.path.join(tmp, "l")
    model = pf(("UUU", 0.5), base=False)
    # 1. USDC bought, the coin leg does not fill → the USDC goes back to USDT at the end of the pass; the coin waits for the next cycle
    fk = fake(no_fill=("UUU-USDC",))
    r = ap.run_once(live(tmp), fk, model, NOW, F.nosleep, log_path=lp)
    assert r["ok"] and r["half"] and fk.bal.get("UUU", 0) == 0 and fk.bal["USDC"] < ap.SWEEP_MIN
    assert [o["instId"] + ":" + o["side"] for o in fk.orders][-1] == "USDC-USDT:sell"
    assert any(x["ev"] == "via_half" and "не куплен" in x["note"] or "не весь" in x.get("note", "") for x in events(lp))
    fk.no_fill.clear()                                                                   # the next cycle buys it
    r = ap.run_once(live(tmp), fk, model, NOW + 3_600_000, F.nosleep, log_path=lp)
    assert r["ok"] and fk.bal["UUU"] * 4.0 > 3000
    # 2. the USDC leg does not fill → the coin leg is never sent
    fk = fake(no_fill=("USDC-USDT",))
    r = ap.run_once(live(tempfile.mkdtemp()), fk, model, NOW, F.nosleep, log_path=lp)
    assert r["ok"] and r["half"] and not coin_orders(fk, "UUU-USDC") and "не докупился" in r["half"][0]
    # 3. a sale: the coin sold, the USDC leg fails → the plain note, and the next pass's sweep brings the USDC back
    tmp3 = tempfile.mkdtemp()
    fk = fake(balances={"USDT": 9000.0, "UUU": 250.0}, no_fill=("USDC-USDT",))
    r = ap.run_once(live(tmp3), fk, pf(), NOW, F.nosleep, log_path=lp)
    assert r["ok"] and fk.bal["UUU"] == 0 and fk.bal["USDC"] > 900 and "USDC в USDT не вернулся" in r["half"][0]
    fk.no_fill.clear()
    cl = ap.connect(live(tmp3), fk, {}, F.nosleep)
    assert ap.sweep(cl, live(tmp3), "cnqtest1", F.nosleep, lp)["filled"] > 900 and fk.bal["USDC"] < ap.SWEEP_MIN


def test_active_spot_buys_and_sells_a_usdc_only_coin_through_usdc():
    fk = fake(demo_key=True)
    e = F.env()
    cl = ap.connect(e, fk, {}, F.nosleep)
    t = {"id": "I1", "symbol": "UUU", "side": "LONG", "entry": 4.0, "stop": 3.2, "qty": 500.0, "qty_left": 500.0, "opened": NOW - 60_000}
    r = ac.step(e, desk={"equity": 10000.0, "open": [t]}, now=NOW, sleep=F.nosleep, cl=cl, total=10000.0)
    assert r["bought"] == 1 and [o["instId"] for o in fk.orders] == ["USDC-USDT", "UUU-USDC"]
    assert abs(float(fk.orders[-1]["sz"]) * 4.0 - 200.0) < 2.0 and ac.load_state(e)["trades"]["I1"]["via"] == "USDC"
    r = ac.step(e, desk={"equity": 10000.0, "open": []}, now=NOW + 60_000, sleep=F.nosleep, cl=cl)
    assert r["sold"] == 1 and [o["instId"] + ":" + o["side"] for o in fk.orders[2:]] == ["UUU-USDC:sell", "USDC-USDT:sell"]
    assert ac.held(e) == {} and fk.bal["USDC"] < 1.0


def test_futures_usdc_margined_swap_funds_margin_in_usdc_same_risk_and_converts_back():
    fk = F.FakeOkx(usdc_swaps=("UUU",), strict=True)
    e = F.env()
    cl = ap.connect(e, fk, {}, F.nosleep)
    t = F.trade("U1", "UUU", entry=4.0, stop=3.8, tp=4.6, risk=50.0)
    text = "\n".join(fu.plan_lines(cl, cl.config(), e, F.desks(t), NOW))
    assert "UUU-USDC-SWAP" in text and "через USDC: маржа USDT → USDC" in text
    r = fu.step(e, desks=F.desks(t), now=NOW, sleep=F.nosleep, cl=cl)
    assert r["opened"] == 1 and not r["errors"]
    ids = [o["instId"] for o in fk.orders]
    assert ids[:2] == ["USDC-USDT", "UUU-USDC-SWAP"]                                    # margin as USDC first, then the entry
    o = fk.orders[1]
    risk = float(o["sz"]) * 1 * (float(o["px"]) - 3.8)
    assert abs(risk - 10000 * 0.15 / 2 * 0.005) <= 1.0                                   # the same sizing as a USDT swap (15 %, 28.10)
    assert o["tdMode"] == "isolated" and o["ordType"] == "ioc" and o["attachAlgoOrds"][0]["slTriggerPx"] == "3.800"
    assert fk.levers[-1] == {"instId": "UUU-USDC-SWAP", "lever": "2", "mgnMode": "isolated"}
    assert fu.load_state(e)["trades"]["bal:U1"]["settle"] == "USDC" and fk.margin["UUU-USDC-SWAP"] > 0
    r = fu.step(e, desks=F.desks(), now=NOW + 60_000, sleep=F.nosleep, cl=cl)          # the desk closed it
    assert r["closed"] == 1 and "UUU-USDC-SWAP" not in fk.pos
    assert fk.orders[-1]["instId"] == "USDC-USDT" and fk.orders[-1]["side"] == "sell" and fk.bal["USDC"] < ap.SWEEP_MIN
    fk2 = F.FakeOkx(usdc_swaps=("UUU",))                                                 # OKX_FUT_USDC=off → skipped, plainly
    r = run_off = fu.step(F.env(OKX_FUT_USDC="off"), desks=F.desks(t), now=NOW, sleep=F.nosleep,
                          cl=ap.connect(F.env(), fk2, {}, F.nosleep))
    assert run_off["opened"] == 0 and "нет свопа UUU-USDT-SWAP" in r["skipped"][0]["why"] and not fk2.orders


def test_futures_usdc_margin_short_waits_for_the_next_cycle():
    fk = F.FakeOkx(usdc_swaps=("UUU",), strict=True, no_fill=("USDC-USDT",))
    e = F.env()
    r = fu.step(e, desks=F.desks(F.trade("U1", "UUU", entry=4.0, stop=3.8, tp=4.6, risk=50.0)), now=NOW, sleep=F.nosleep,
                cl=ap.connect(e, fk, {}, F.nosleep))
    assert r["opened"] == 0 and "USDC под маржу не докупился" in r["skipped"][0]["why"]
    assert not [o for o in fk.orders if o["instId"].endswith("-SWAP")] and "bal:U1" not in fu.load_state(e).get("skip", {})


# ── the planned restart onto new code ────────────────────────────────────────────────────────────────────────────────
def _repo(tmp, head="a" * 40, code="x = 1\n", packed=False):
    os.makedirs(os.path.join(tmp, ".git", "refs", "heads"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "cnq"), exist_ok=True)
    with open(os.path.join(tmp, ".git", "HEAD"), "w") as f:
        f.write("ref: refs/heads/main\n")
    ref = os.path.join(tmp, ".git", "refs", "heads", "main")
    if packed:
        if os.path.exists(ref):
            os.remove(ref)
        with open(os.path.join(tmp, ".git", "packed-refs"), "w") as f:
            f.write(f"# pack-refs\n{head} refs/heads/main\n")
    else:
        with open(ref, "w") as f:
            f.write(head + "\n")
    with open(os.path.join(tmp, "cnq", "m.py"), "w") as f:
        f.write(code)


def test_updater_restarts_only_on_new_code_that_passes_the_selfcheck():
    tmp = tempfile.mkdtemp()
    _repo(tmp)
    calls = []
    verdict = {"ok": True}

    def run(root):
        calls.append(root)
        return verdict["ok"], "САМОПРОВЕРКА: OK" if verdict["ok"] else "SyntaxError"
    u = ap.Updater(tmp, run=run, env={})
    assert u.check() == (False, None)
    _repo(tmp, head="b" * 40)                                                            # a data-only commit
    assert u.check() == (False, None) and not calls
    verdict["ok"] = False
    _repo(tmp, head="c" * 40, code="x = (\n")                                             # broken new code
    restart, msg = u.check()
    assert not restart and "не прошёл самопроверку" in msg and len(calls) == 1
    _repo(tmp, head="d" * 40, code="x = (\n", packed=True)                               # the same broken code: not tried again
    assert u.check() == (False, None) and len(calls) == 1 and ap.repo_head(tmp) == "d" * 40
    verdict["ok"] = True
    _repo(tmp, head="e" * 40, code="x = 2\n")
    restart, msg = u.check()
    assert restart and "замок сохраняется" in msg and len(calls) == 2
    assert ap.Updater(tmp, run=run, env={"OKX_SELF_UPDATE": "off"}).check() == (False, None)


def test_the_real_selfcheck_passes_in_a_fresh_process():
    ok, out = ap._selfcheck_proc(ap.ROOT)
    assert ok, out
    assert ap.selfcheck() == []


def test_planned_restart_keeps_the_lease_a_clean_exit_releases_it(monkeypatch):
    tmp = tempfile.mkdtemp()
    e = F.env(tmp, OKX_LEASE_ID="mac-test", OKX_IPV6="on")
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    for k in ("OKX_AUTOPILOT", "OKX_NODE", "CNQ_GH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setitem(ap._PLANNED, "restart", False)
    monkeypatch.setattr(L, "_CACHE", {})

    def holder():
        with open(e["OKX_LEASE_FILE"], encoding="utf-8") as f:
            return (json.load(f).get("doc") or {}).get("holder")

    def no_sleep(_s):
        raise AssertionError("the loop must exit at the cycle boundary")

    class Restart:
        def check(self):
            return True, "новый код прошёл самопроверку"
    assert L.gate()["ok"] and holder() == "mac-test"
    monkeypatch.setattr(ap, "Updater", Restart)
    monkeypatch.setattr(ap.time, "sleep", no_sleep)
    assert ap.main(["--loop"]) == 0 and ap._PLANNED["restart"]
    assert holder() == "mac-test"                                                        # kept: the restarted window resumes at once
    ap._PLANNED["restart"] = False

    class Interrupt:
        def check(self):
            raise KeyboardInterrupt
    monkeypatch.setattr(ap, "Updater", Interrupt)
    assert ap.main(["--loop"]) == 130 and holder() is None                               # Ctrl+C still gives the lease back
