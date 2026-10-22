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
    assert set(fk.set_calls[0]["instId"].split(",")) == {"BTC-USDT", "ETH-USDT", "AAA-USDT", "BBB-USDT"}
    by = {o["instId"]: o["tdMode"] for o in spot_orders(fk)}
    assert by["BTC-USDT"] == by["ETH-USDT"] == by["AAA-USDT"] == by["BBB-USDT"] == "spot_isolated"
    assert by["SOL-USDT"] == "cash"                                       # not supported for copy → an ordinary trade
    assert {o["instId"] for o in fk.lead_subs} == {"BTC-USDT", "ETH-USDT", "AAA-USDT", "BBB-USDT"} and fk.bal["SOL"] > 0
    txt = ap.plan_text(r["plan"], r["mode"])
    assert "ВЕДУЩИЙ ТРЕЙДЕР: спот — да (4 монеты копируются) · фьючерсы — нет · копируют: 3" in txt
    assert "не копируется (обычные сделки): SOL — OKX не даёт копировать эту монету" in txt
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
    assert "SOL — OKX не принял её в лидерские инструменты" in ap.plan_text(r["plan"], r["mode"])
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
    assert "UUU — через USDC" in ap.plan_text(r["plan"], r["mode"])


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
    ap.tick(e, fk, F.portfolio(), NOW + ld.DAY_MS + 60_000, F.nosleep)       # a day later → set again (OKX's list refreshed)
    assert len(fk.set_calls) == n + 1


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
