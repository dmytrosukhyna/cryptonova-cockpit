"""«Ведущий трейдер» на споте OKX (cnq/execution/okx_lead.py, 22.10.2026): the autopilot's spot trades of «CRYPTO NOVA портфель» go
as lead orders (tdMode spot_isolated) on the copyable coins, the lead instruments are set to the portfolio's coins, the rest stays
ordinary, lead positions are counted exactly like ordinary coins, OKX_SPOT_LEAD=off turns it off. A fake OKX (tests/okx_fake.py):
every coin, key, code and amount is made-up test data; no real API is called."""
import json
import os
import tempfile

import okx_fake as F
from cnq.execution import myspot_sub as sub
from cnq.execution import okx_active as ac
from cnq.execution import okx_autopilot as ap
from cnq.execution import okx_futures as fu
from cnq.execution import okx_lead as ld
from cnq.execution import public_real as PR

ap.LOG = os.path.join(tempfile.mkdtemp(), "okx_autopilot.jsonl")
fu.LOG = os.path.join(tempfile.mkdtemp(), "okx_futures.jsonl")
ac.LOG = os.path.join(tempfile.mkdtemp(), "okx_active.jsonl")
NOW = F.NOW


def fake(**kw):
    kw.setdefault("demo_key", False)
    kw.setdefault("strict", True)
    return F.FakeOkx(**kw)


def live(tmp, **kw):
    return F.env(tmp, CNQ_LIVE_TRADING="yes", OKX_AUTOPILOT="on", **kw)


def spot_orders(fk):
    return [o for o in fk.orders if not o["instId"].endswith("-SWAP")]


def test_lead_role_buys_copyable_coins_as_lead_sets_the_instruments_and_leaves_the_rest_ordinary():
    tmp = tempfile.mkdtemp()
    fk = fake(spot_role="1", copiers=3, spot_supported={f"{c}-USDT" for c in ("BTC", "ETH", "AAA", "BBB", "DOGE")})
    e = live(tmp)
    r = ap.run_once(e, fk, F.portfolio(), NOW, F.nosleep, log_path=os.path.join(tmp, "l.jsonl"))
    assert r["ok"]
    # the lead instruments = the portfolio's coins OKX supports for spot copy (SOL is not one here, ZZZ has no pair), instType SPOT
    assert fk.set_calls and fk.set_calls[0]["instType"] == "SPOT"
    assert set(fk.set_calls[0]["instId"].split(",")) == {"BTC-USDT", "ETH-USDT", "AAA-USDT", "BBB-USDT", "DOGE-USDT"}   # every supported
    by = {o["instId"]: o["tdMode"] for o in spot_orders(fk)}
    assert by["BTC-USDT"] == by["ETH-USDT"] == by["AAA-USDT"] == by["BBB-USDT"] == "spot_isolated"
    assert by["SOL-USDT"] == "cash"                                       # not supported for copy → an ordinary trade
    assert {o["instId"] for o in fk.lead_subs} == {"BTC-USDT", "ETH-USDT", "AAA-USDT", "BBB-USDT"} and fk.bal["SOL"] > 0
    txt = ap.plan_text(r["plan"], r["mode"])
    assert "ВЕДУЩИЙ ТРЕЙДЕР: спот — да (4 монеты копируются) · фьючерсы — нет · копируют: 3" in txt
    assert "SOL: не добавлено в копируемый портфель: OKX не даёт копировать эту монету" in txt
    assert "КУПИТЬ" in txt and "· копируется ·" in txt and "· не копируется ·" in txt
    assert "профиль ведущего трейдера: https://www.okx.com/copy-trading/account/TESTLEAD01" in txt
    # the second pass: the account matches, the instruments are fresh → no new set call, no orders
    n_set, n_ord = len(fk.set_calls), len(fk.orders)
    st = ap.load_state(e)
    b = ap.build(ap.connect(e, fk, st, F.nosleep), e, F.portfolio())
    assert not b["sells"] and not b["buys"] and len(fk.set_calls) == n_set and len(fk.orders) == n_ord


def test_no_lead_role_means_cash_orders_and_no_copytrading_writes():
    tmp = tempfile.mkdtemp()
    fk = fake()
    r = ap.run_once(live(tmp), fk, F.portfolio(), NOW, F.nosleep)
    assert r["ok"] and spot_orders(fk) and all(o["tdMode"] == "cash" for o in spot_orders(fk))
    assert not fk.set_calls and not [c for c in fk.calls if c[0] == "POST" and "/copytrading/" in c[2]]
    assert not [c for c in fk.calls if c[2] == ld.SUBS]                  # a normal account never reads lead positions
    assert "ВЕДУЩИЙ ТРЕЙДЕР: спот — нет · фьючерсы — нет" in ap.plan_text(r["plan"], r["mode"])


def test_disabled_by_env_keeps_every_order_cash_and_sets_nothing():
    tmp = tempfile.mkdtemp()
    fk = fake(spot_role="1")
    e = live(tmp, OKX_SPOT_LEAD="off")
    r = ap.run_once(e, fk, F.portfolio(), NOW, F.nosleep)
    assert r["ok"] and spot_orders(fk) and all(o["tdMode"] == "cash" for o in spot_orders(fk)) and not fk.set_calls
    assert "OKX_SPOT_LEAD=off" in ap.plan_text(r["plan"], r["mode"])


def test_accounting_counts_lead_positions_exactly_like_ordinary_coins_whatever_the_balance_shows():
    """The same coins held ordinary vs. as lead positions (eq with / without them, spotIsoBal or not, absent from the balance):
    the same total, the same shares, the same «совпадает», the same orders (only their tdMode differs)."""
    held = {"BTC": 0.0195, "ETH": 0.65, "AAA": 48.75}
    plain = fake(balances={"USDT": 2000.0, **held})
    base = ap.build(ap.connect(F.env(), plain, {}, F.nosleep), F.env(), F.portfolio())
    subs = [{"subPosId": f"s{i}", "instId": f"{c}-USDT", "subPos": q, "openAvgPx": F.PX[c]} for i, (c, q) in enumerate(held.items())]
    for kw in ({"iso_in_eq": False, "iso_field": True}, {"iso_in_eq": True, "iso_field": True}, {"iso_in_eq": False, "iso_field": False},
               {"iso_in_eq": True, "iso_field": False}):
        fk = fake(balances={"USDT": 2000.0}, spot_role="1", spot_enabled={f"{c}-USDT" for c in held}, lead_subs=subs, **kw)
        b = ap.build(ap.connect(F.env(), fk, {}, F.nosleep), F.env(), F.portfolio())
        assert b["total_usd"] == base["total_usd"], kw
        assert b["targets"] == base["targets"] and b["same"] == base["same"] and b["spot_now_usd"] == base["spot_now_usd"], kw
        key = lambda os_: sorted((o["ccy"], o["side"], o["sz"]) for o in os_)  # noqa: E731
        assert key(b["sells"]) == key(base["sells"]) and key(b["buys"]) == key(base["buys"]), kw
        assert b["lead"]["lead_qty"] == {c: q for c, q in held.items()} and not b["lead"]["legacy"]
    # the futures budget and the card read the same total (read_raw merges the lead coins for every reader)
    e = F.env()
    fk = fake(balances={"USDT": 2000.0}, spot_role="1", spot_enabled={f"{c}-USDT" for c in held}, lead_subs=subs)
    assert fu.budget(ap.connect(e, fk, {}, F.nosleep), {}, e, fresh=True)["total"] == base["total_usd"]


def test_a_sale_takes_the_lead_positions_first_then_the_ordinary_coins_and_never_sells_lead_coins_as_cash():
    tmp = tempfile.mkdtemp()
    pf = F.portfolio()
    pf["portfolios"]["ideas"]["positions"] = [p for p in pf["portfolios"]["ideas"]["positions"] if p["symbol"] != "AAA"]
    fk = fake(balances={"USDT": 8000.0, "AAA": 30.0}, spot_role="1", spot_enabled={"AAA-USDT"},
              lead_subs=[{"subPosId": "s1", "instId": "AAA-USDT", "subPos": 20.0, "openAvgPx": 10.0}])
    r = ap.run_once(live(tmp), fk, pf, NOW, F.nosleep)
    aaa = [o for o in spot_orders(fk) if o["instId"] == "AAA-USDT"]
    assert [(o["tdMode"], float(o["sz"])) for o in aaa] == [("spot_isolated", 20.0), ("cash", 30.0)]
    assert r["ok"] and not [x for x in fk.lead_subs if x["instId"] == "AAA-USDT"] and fk.bal["AAA"] == 0
    # the AAA lead instrument stays set while it had a lead position (set before the sale), then follows the portfolio
    assert "AAA-USDT" in fk.set_calls[0]["instId"].split(",")


def test_a_full_exit_refused_as_a_lead_order_closes_the_lead_positions():
    tmp = tempfile.mkdtemp()
    pf = F.portfolio()
    pf["portfolios"]["ideas"]["positions"] = [p for p in pf["portfolios"]["ideas"]["positions"] if p["symbol"] != "AAA"]
    fk = fake(spot_role="1", spot_enabled={"AAA-USDT"}, lead_sell_reject=True,
              lead_subs=[{"subPosId": "s1", "instId": "AAA-USDT", "subPos": 20.0, "openAvgPx": 10.0}])
    r = ap.run_once(live(tmp), fk, pf, NOW, F.nosleep)
    closes = [b for k, b in fk.lead_calls if k == "close"]
    assert r["ok"] and r["failed"] == 0 and closes and closes[0]["instType"] == "SPOT" and closes[0]["ordType"] == "limit"
    assert float(closes[0]["px"]) <= 10.0 and not [x for x in fk.lead_subs if x["instId"] == "AAA-USDT"]


def test_a_coin_okx_refuses_as_a_lead_instrument_is_added_one_by_one_and_bought_ordinary():
    tmp = tempfile.mkdtemp()
    fk = fake(spot_role="1", set_reject={"SOL-USDT"})
    r = ap.run_once(live(tmp), fk, F.portfolio(), NOW, F.nosleep)
    assert r["ok"] and fk.spot_enabled == {"BTC-USDT", "ETH-USDT", "AAA-USDT", "BBB-USDT"}
    by = {o["instId"]: o["tdMode"] for o in spot_orders(fk)}
    assert by["SOL-USDT"] == "cash" and by["BTC-USDT"] == "spot_isolated"
    assert "SOL: не добавлено в копируемый портфель: OKX не принял её в список ведущего трейдера" in ap.plan_text(r["plan"], r["mode"])
    assert ap.load_state(live(tmp))["lead_fail"].keys() == {"SOL-USDT"}


def test_usdc_route_coin_is_not_copied():
    tmp = tempfile.mkdtemp()
    pf = F.portfolio()
    pf["portfolios"]["ideas"]["positions"].append({"symbol": "UUU", "weight": 0.05, "price": 4.0, "status": "OPEN", "name": "UUU",
                                                   "bucket": "mid", "kind_ru": "крупный альт", "role_ru": "из таблицы: можно брать",
                                                   "table_why_ru": "анкета UUU", "levels": {"stop": 3.0, "take_half": None},
                                                   "target_weight": 0.05})
    fk = fake(spot_role="1", usdc_only=("UUU",))
    r = ap.run_once(live(tmp), fk, pf, NOW, F.nosleep)
    assert r["ok"] and all(o["tdMode"] == "cash" for o in spot_orders(fk) if o["instId"] in ("UUU-USDC", "USDC-USDT"))
    assert ("UUU: не добавлено в копируемый портфель: OKX копирует только пары к USDT, а у этой монеты есть только пара к USDC"
            in ap.plan_text(r["plan"], r["mode"]))


def test_coins_bought_before_the_switch_stay_and_the_plan_names_them_and_migrate_only_plans():
    tmp = tempfile.mkdtemp()
    e = live(tmp)
    held = {"USDT": 2000.0, "BTC": 0.0195, "ETH": 0.65, "SOL": 3.25, "AAA": 48.75, "CCC": 32.5}
    fk = fake(balances=dict(held), spot_role="1")
    b = ap.build(ap.connect(e, fk, {}, F.nosleep), e, F.portfolio())
    assert set(b["lead"]["legacy"]) == {"BTC", "ETH", "SOL", "AAA"} and "CCC" not in b["lead"]["legacy"]   # no BBB held
    assert "не лидерские (куплены до включения" in ap.plan_text(b, "x")
    n = len(fk.orders)
    txt = ld.migrate(e, fk, F.portfolio(), now=NOW, sleep=F.nosleep)
    assert "ПЕРЕВОД В ЛИДЕРСКИЕ ПОЗИЦИИ" in txt and "только план" in txt and len(fk.orders) == n and not fk.set_calls
    assert "комиссия ≈ $" in txt
    # with ДА: ordinary sells, then the same coins bought back as lead positions; the account value stays (minus the costs)
    txt = ld.migrate(e, fk, F.portfolio(), confirm=lambda _t: True, now=NOW, sleep=F.nosleep)
    sells = [o for o in fk.orders[n:] if o["side"] == "sell"]
    buys = [o for o in fk.orders[n:] if o["side"] == "buy"]
    assert sells and all(o["tdMode"] == "cash" for o in sells) and buys and all(o["tdMode"] == "spot_isolated" for o in buys)
    assert {o["instId"] for o in fk.lead_subs} == {"BTC-USDT", "ETH-USDT", "SOL-USDT", "AAA-USDT"} and "Готово" in txt
    # cancelled at the prompt → nothing
    fk2 = fake(balances=dict(held), spot_role="1")
    assert ld.migrate(e, fk2, F.portfolio(), confirm=lambda _t: False, now=NOW, sleep=F.nosleep) == "Отменено — ордеров нет."
    assert not fk2.orders


def test_lead_instruments_are_refreshed_daily_by_the_window_without_a_rebalance():
    tmp = tempfile.mkdtemp()
    e = live(tmp)
    fk = fake(spot_role="1")
    assert ap.run_once(e, fk, F.portfolio(), NOW, F.nosleep)["ok"]
    n = len(fk.set_calls)
    st = ap.load_state(e)
    st["drift_t"] = 0
    ap.save_state(st, e)
    ap.tick(e, fk, F.portfolio(), NOW + 2 * 3_600_000, F.nosleep)          # hourly check, instruments fresh → nothing set
    assert len(fk.set_calls) == n
    st = ap.load_state(e)
    st["drift_t"] = 0
    ap.save_state(st, e)
    ap.tick(e, fk, F.portfolio(), NOW + ld.DAY_MS + 60_000, F.nosleep)       # a day later, nothing missing → no call
    assert len(fk.set_calls) == n
    fk.spot_enabled.discard("ETH-USDT")                                    # OKX dropped a portfolio coin → added back, rest kept
    st = ap.load_state(e)
    st["drift_t"] = 0
    ap.save_state(st, e)
    ap.tick(e, fk, F.portfolio(), NOW + 2 * ld.DAY_MS + 120_000, F.nosleep)
    assert len(fk.set_calls) == n + 1 and "ETH-USDT" in fk.spot_enabled


def test_spot_lead_instruments_are_never_shrunk_only_missing_portfolio_coins_are_added():
    tmp = tempfile.mkdtemp()
    many = {f"{c}-USDT" for c in ("BTC", "ETH", "AAA", "DOGE", "XRP", "LINK", "AVAX", "ADA", "DOT", "APT")}   # OKX: all enabled
    fk = fake(spot_role="1", spot_enabled=set(many))
    assert ap.run_once(live(tmp), fk, F.portfolio(), NOW, F.nosleep)["ok"]
    every = fk.spot_supported
    assert len(fk.set_calls) == 1 and set(fk.set_calls[0]["instId"].split(",")) == every and fk.spot_enabled == every
    assert not set(fk.set_calls[0]["instId"].split(",")) < many
    fk2 = fake(spot_role="1", spot_enabled=set(F.FakeOkx().spot_supported))
    assert ap.run_once(live(tempfile.mkdtemp()), fk2, F.portfolio(), NOW, F.nosleep)["ok"] and not fk2.set_calls


def test_unreadable_lead_positions_mean_no_spot_orders():
    tmp = tempfile.mkdtemp()
    fk = fake(spot_role="1")
    real = fk.__call__

    def opener(method, url, headers, body):
        if ld.SUBS in url:
            return {"code": "50013", "msg": "made-up: system busy", "data": []}
        return real(method, url, headers, body)
    r = ap.run_once(live(tmp), opener, F.portfolio(), NOW, F.nosleep)
    assert not r["ok"] and "лидерские спот-позиции не прочитались" in r["why"] and not fk.orders


def test_check_card_and_public_line_show_the_role_the_copiers_and_the_profile_link():
    tmp = tempfile.mkdtemp()
    e = live(tmp)
    fk = fake(spot_role="1", copiers=7, spot_enabled={"BTC-USDT", "ETH-USDT"},
              lead_subs=[{"subPosId": "s1", "instId": "BTC-USDT", "subPos": 0.01, "openAvgPx": 100000.0}])
    lines = "\n".join(ap.check(e, fk))
    assert "ведущий трейдер: спот — да (2 монеты копируются) · фьючерсы — нет · копируют: 7" in lines
    assert "https://www.okx.com/copy-trading/account/TESTLEAD01" in lines

    def fetch(host, path, params=None, signed=True):
        import urllib.parse
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        assert path in sub.READS, path
        return fk("GET", host + path + q, {"OK-ACCESS-KEY": "x"} if signed else {}, None)
    s = sub.read(e, fetch, NOW)
    assert s["ok"] and s["lead"]["copiers"] == 7 and s["lead"]["url"].endswith("/TESTLEAD01")
    assert s["total_usd"] == round(10000.0 + 0.01 * 100000.0, 2)               # the lead BTC is in the total
    assert any(r["ccy"] == "BTC" and r["usd"] == 1000.0 for r in s["spot"]["rows"])
    doc = PR.clean(PR.build(s, {}, NOW, 0))
    assert doc["copiers"] == 7 and doc["copy_url"] == "https://www.okx.com/copy-trading/account/TESTLEAD01"
    assert PR.clean({**doc, "copy_url": "https://evil.example/x"})["copy_url"] is None
    # the app's «Копировать CRYPTO NOVA» lights up from public_real.json when app_config has no okx_copy_url
    from cnq.agents import app_home
    out = tempfile.mkdtemp()
    with open(os.path.join(out, "public_real.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    assert app_home._config(out)["okx_copy_url"] == "https://www.okx.com/copy-trading/account/TESTLEAD01"
    with open(os.path.join(out, "app_config.json"), "w", encoding="utf-8") as f:
        json.dump({"okx_copy_url": "https://www.okx.com/copy-trading/account/OWNERSET"}, f)
    assert app_home._config(out)["okx_copy_url"].endswith("/OWNERSET")


def test_selfcheck_and_cash_is_the_default_order_mode():
    assert ap.selfcheck() == []
    fk = fake()
    cl = ap.connect(F.env(), fk, {}, F.nosleep)
    cl.order("BTC-USDT", "buy", "100100", "0.001", "cnqtest1")
    assert fk.orders[-1]["tdMode"] == "cash"
    try:
        cl.order("BTC-USDT", "buy", "100100", "0.001", "cnqtest2", td="cross")
        raise AssertionError("cross must be refused")
    except ValueError:
        pass


def imp(tid="I1", sym="XRP", entry=2.0):
    return {"id": tid, "symbol": sym, "side": "LONG", "entry": entry, "stop": entry * 0.8, "qty": 1000.0, "qty_left": 1000.0,
            "opened": NOW - 60_000}


def test_impulse_active_spot_buys_as_lead_and_its_exit_sells_the_lead_positions():
    fk = fake(spot_role="1", spot_enabled={"BTC-USDT"})                    # XRP supported but not set yet → added first
    e = F.env(CNQ_LIVE_TRADING="yes")
    cl = ap.connect(e, fk, {}, F.nosleep)
    r = ac.step(e, desk={"equity": 10000.0, "open": [imp()]}, now=NOW, sleep=F.nosleep, cl=cl, total=10000.0)
    o = fk.orders[-1]
    assert r["bought"] == 1 and o["instId"] == "XRP-USDT" and o["tdMode"] == "spot_isolated" and "XRP-USDT" in fk.spot_enabled
    assert "BTC-USDT" in fk.spot_enabled                                    # the full list was sent: nothing removed
    m = ac.load_state(e)["trades"]["I1"]
    assert m["lead"] is True and m["ords"] == ["o1"] and ac.held(e, True) == {"XRP": float(o["sz"])}
    # the spot rebalance counts the sleeve's lead XRP and never sells it
    b = ap.build(cl, e, F.portfolio())
    assert not [x for x in b["sells"] if x["ccy"] == "XRP"] and b["lead"]["lead_qty"]["XRP"] == float(o["sz"])
    # the 12-hour exit (the desk closed it): a lead sell of the sleeve's coins
    r = ac.step(e, desk={"equity": 10000.0, "open": []}, now=NOW + 12 * 3_600_000, sleep=F.nosleep, cl=cl)
    s = fk.orders[-1]
    assert r["sold"] == 1 and s["side"] == "sell" and s["tdMode"] == "spot_isolated" and not fk.lead_subs and ac.held(e) == {}


def test_impulse_exit_refused_as_a_lead_order_closes_only_the_sleeves_own_lead_positions():
    fk = fake(spot_role="1", spot_enabled={"XRP-USDT"}, lead_sell_reject=True,
              lead_subs=[{"subPosId": "pf1", "instId": "XRP-USDT", "subPos": 50.0, "openAvgPx": 2.0, "openOrdId": "old"}])
    e = F.env(CNQ_LIVE_TRADING="yes")
    cl = ap.connect(e, fk, {}, F.nosleep)
    ac.step(e, desk={"equity": 10000.0, "open": [imp()]}, now=NOW, sleep=F.nosleep, cl=cl, total=10000.0)
    r = ac.step(e, desk={"equity": 10000.0, "open": []}, now=NOW + 12 * 3_600_000, sleep=F.nosleep, cl=cl)
    closes = [b for k, b in fk.lead_calls if k == "close"]
    assert r["sold"] == 1 and len(closes) == 1 and closes[0]["subPosId"] != "pf1" and closes[0]["instType"] == "SPOT"
    assert [x["subPosId"] for x in fk.lead_subs] == ["pf1"] and ac.held(e) == {}


def test_impulse_on_a_coin_okx_does_not_copy_or_without_the_lead_role_stays_ordinary():
    for kw in ({"spot_role": "1", "spot_supported": {"BTC-USDT"}}, {"spot_role": "0"}, {"spot_role": "1", "env": {"OKX_SPOT_LEAD": "off"}}):
        extra = kw.pop("env", {})
        fk = fake(**kw)
        e = F.env(CNQ_LIVE_TRADING="yes", **extra)
        cl = ap.connect(e, fk, {}, F.nosleep)
        assert ac.step(e, desk={"equity": 10000.0, "open": [imp()]}, now=NOW, sleep=F.nosleep, cl=cl, total=10000.0)["bought"] == 1
        assert fk.orders[-1]["tdMode"] == "cash" and not fk.set_calls and "lead" not in ac.load_state(e)["trades"]["I1"]
        ac.step(e, desk={"equity": 10000.0, "open": []}, now=NOW + 60_000, sleep=F.nosleep, cl=cl)
        assert fk.orders[-1]["side"] == "sell" and fk.orders[-1]["tdMode"] == "cash" and ac.held(e) == {}


def test_public_real_marks_each_coin_and_position_copied_or_why_not_and_splits_the_result():
    e = live(tempfile.mkdtemp())
    fk = fake(spot_role="1", role="1", copiers=2, usdc_only=("UUU",), spot_supported={"BTC-USDT", "ETH-USDT"},
              spot_enabled={"BTC-USDT", "ETH-USDT"}, lead_insts=["SOL-USDT-SWAP"],
              balances={"USDT": 5000.0, "ETH": 0.5, "XRP": 100.0, "UUU": 25.0},
              lead_subs=[{"subPosId": "s1", "instId": "BTC-USDT", "subPos": 0.01, "openAvgPx": 100000.0}],
              hist=[{"instId": "APT-USDT-SWAP", "uTime": str(NOW + 1000), "realizedPnl": "-4.5"}])
    fu.step(e, desks=F.desks(F.trade(), F.trade("T2", "APT", entry=1.0, stop=0.95, tp=1.2)), now=NOW, sleep=F.nosleep,
            cl=ap.connect(e, fk, {}, F.nosleep))
    fk.swap_lead.discard("APT-USDT-SWAP")                                   # APT: an ordinary position (e.g. refused as lead)

    def fetch(host, path, params=None, signed=True):
        import urllib.parse
        assert path in sub.READS + (PR.HISTORY,), path
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return fk("GET", host + path + q, {"OK-ACCESS-KEY": "x"} if signed else {}, None)
    s = sub.read(e, fetch, NOW + 2000)
    doc = PR.clean(PR.build(s, {}, NOW + 2000, *PR.closed_stats(fetch, "https://" + s["host"], NOW)))
    spot = {x["sym"]: x for x in doc["spot"]}
    assert spot["BTC"]["copy"] is True and spot["BTC"]["copy_reason"] is None
    assert spot["ETH"]["copy"] is False and spot["ETH"]["copy_reason"] == "legacy"
    assert spot["XRP"]["copy_reason"] == "not_supported" and spot["UUU"]["copy_reason"] == "usdc_only"
    fut = {x["inst"]: x for x in doc["futures"]}
    assert fut["SOL-USDT-SWAP"]["copy"] is True and fut["APT-USDT-SWAP"] == {**fut["APT-USDT-SWAP"], "copy": False, "copy_reason": "ordinary"}
    upl = sum(p["upl"] or 0.0 for p in s["futures"]["positions"])
    assert doc["fut_pnl_usd"] == round(-4.5 + upl, 2) and abs(doc["spot_pnl_usd"] + doc["fut_pnl_usd"] - doc["pnl_usd"]) < 0.02
    assert ld.coin_copy("ZZZ", {"role": {"spot": False}, "off": False, "supported": set(), "held": {}}, 1.0, set())["copy_reason"] == "ordinary"
