"""«Автопилот OKX» (cnq/execution/okx_autopilot.py, okx_alloc.py) with a fake OKX (tests/okx_fake.py): the account is split 65 / 25 / 10,
the spot part mirrors «Наш портфель CRYPTO NOVA» (target = 0.65 × the portfolio's weight), a ticker is mapped only when the OKX
price matches (1000BBB → BBB; a wrong-price coin and a missing coin stay in USDT and are reported), sells go before buys as limit
IOC orders with a price cap, caps and the kill switch hold, a key with «Withdraw» or from the wrong world is refused, the window
rebalances only when the public portfolio changes. Also: the legacy OKX client finds the host, spot_mirror never runs the owner's
real OKX account, the card prices EEA pairs. Every coin, key and amount here is made-up test data; the real OKX API is never called."""
import io
import json
import os
import tempfile
import urllib.error

import okx_fake as F
from cnq.execution import myspot_watch as ms
from cnq.execution import okx_active as ac
from cnq.execution import okx_alloc as al
from cnq.execution import okx_autopilot as ap
from cnq.execution import okx_futures as fu

ap.LOG = os.path.join(tempfile.mkdtemp(), "okx_autopilot.jsonl")      # never write into the repo's data/
fu.LOG = os.path.join(tempfile.mkdtemp(), "okx_futures.jsonl")
ac.LOG = os.path.join(tempfile.mkdtemp(), "okx_active.jsonl")
PF = F.portfolio()
EEA, WWW, NOW = F.EEA, F.WWW, F.NOW


def live_fake(**kw):
    kw.setdefault("demo_key", False)
    kw.setdefault("balances", {"USDT": 9000.0, "DOGE": 5000.0})
    return F.FakeOkx(**kw)


def test_split_defaults_env_and_normalisation():
    # owner's decision of 28.10: spot portfolio 65 % + extra core 10 % + futures 15 % + «Импульс BTC» 10 %
    assert al.shares({}) == {"spot": 0.65, "core": 0.1, "futures": 0.15, "active": 0.1, "reserve": 0.0}
    w = al.shares({"OKX_SPOT_SHARE": "0.6", "OKX_CORE_SHARE": "0", "OKX_FUT_SHARE": "0.2", "OKX_ACT_SHARE": "0.1"})
    assert w["spot"] == 0.6 and round(w["reserve"], 6) == 0.1
    w = al.shares({"OKX_SPOT_SHARE": "0.8", "OKX_FUT_SHARE": "0.4", "OKX_ACT_SHARE": "0.0"})   # > 100 % → scaled to 100 %
    assert abs(w["spot"] + w["core"] + w["futures"] + w["active"] - 1) < 1e-6 and w["reserve"] == 0.0
    # the extra core in the core's plan proportions 30 / 10 / 5 — the same as the CRYPTO NOVA portfolio's core
    from cnq.agents.spot_profiles import IDEAS
    assert al.CORE_PLAN == IDEAS["core_w"]
    x = al.core_extra(al.shares({}))
    assert abs(sum(x.values()) - 0.10) < 1e-9 and abs(x["BTC"] - 0.10 * 30 / 45) < 1e-9 and abs(x["SOL"] - 0.10 * 5 / 45) < 1e-9
    assert al.shares({"OKX_SPOT_SHARE": "abc"})["spot"] == 0.65


def test_targets_equal_spot_share_times_portfolio_weights_and_never_guess_a_ticker():
    e = F.env()
    fk = live_fake(demo_key=True)
    cl = ap.connect(e, fk, {}, F.nosleep)
    b = ap.build(cl, e, PF)
    total = b["total_usd"]
    pos = al.ideas(PF)
    got = {r["sym"]: r["target_pct"] / 100 for r in b["targets"]}
    extra = al.core_extra(al.shares(e))                                       # owner 28.10: + the extra core 30 / 10 / 5
    for sym in ("BTC", "ETH", "SOL", "AAA", "1000BBB"):
        assert abs(got[sym] - 0.65 * pos[sym]["w"] - extra.get(sym, 0.0)) <= 0.005, sym   # = spot share × the portfolio's weight (±0.5 %)
    assert abs(got["BTC"] - (0.65 * 0.30 + 0.10 * 30 / 45)) <= 0.005 and "доп. доля ядра" in next(o for o in b["buys"] if o["ccy"] == "BTC")["why"]
    assert {r["ccy"] for r in b["targets"]} == {"BTC", "ETH", "SOL", "AAA", "BBB"}  # 1000BBB → BBB-USDT (price × 1000 matches)
    assert "ZZZ" not in got and "CCC" not in got                               # missing on OKX / different price → never bought
    text = " ".join(b["diffs"])
    assert "ZZZ" in text and "нет спот-пары" in text and "CCC" in text and "25%" in text and not b["same"]
    bbb = next(o for o in b["buys"] if o["ccy"] == "BBB")
    assert abs(bbb["usd"] - 0.65 * 0.025 * total) <= 0.01 * total
    sells = {o["ccy"]: o for o in b["sells"]}
    assert sells["DOGE"]["act"] == "sell" and float(sells["DOGE"]["sz"]) == 5000 and "не брать" in sells["DOGE"]["why"]
    assert [o["ccy"] for o in b["buys"]][:3] == ["BTC", "ETH", "SOL"]          # the core first
    assert all(o["why"] for o in b["sells"] + b["buys"])                       # every order has its reason
    for o in b["buys"]:
        assert float(o["px"]) >= F.PX[o["ccy"]] * 1.001 and float(o["px"]) <= F.PX[o["ccy"]] * 1.001 * 1.0041 + 1e-7
    assert not fk.orders                                                       # building a plan never trades


def test_plan_prints_every_order_futures_sizes_and_the_match_and_places_nothing():
    e = F.env()
    fk = live_fake(demo_key=True)
    d = F.desks(F.trade(), F.trade("T2", "BTC", "SHORT", 100000.0, 102000.0, 90000.0), F.trade("T3", "APT", entry=0.95, stop=0.9, tp=1.3,
                                                                                                opened=NOW - 5 * 3600_000))
    text, go = ap.plan_all(e, fk, PF, d, {"equity": 10000.0, "open": []}, NOW)
    assert go and not fk.orders and not fk.levers
    for s in ("ДЕЛЕНИЕ СЧЁТА", "Спот · CRYPTO NOVA портфель: 65%", "Фьючерсы · CRYPTO NOVA (отобранные сигналы): 15%", "Импульс BTC: 10%",
              "Ядро BTC / ETH / SOL (доп., 30 / 10 / 5): 10%",
              "ПРОДАТЬ 5000 DOGE", "КУПИТЬ", "почему:", "ждёт сигнала", "ОРДЕР: купить", "SOL-USDT-SWAP", "стоп-лосс 190", "ОРДЕР: продать",
              "BTC-USDT-SWAP", "пропущено: сделка открыта раньше, цена ушла на 5,37%", "СОВПАДАЕТ С СИСТЕМОЙ CRYPTO NOVA: НЕТ", "ZZZ"):
        assert s in text, s
    text, go = ap.plan_all(F.env(), live_fake(), PF, d, {"open": []}, NOW)    # a real key, plan without --live (the usual way)
    assert go and "РЕАЛЬНЫЙ счёт" in text and "только после run --live" in text and "ОРДЕР: купить" in text and "НЕ БУДЕТ" not in text
    text, go = ap.plan_all(F.env(CNQ_LIVE_TRADING="yes"), F.FakeOkx(), PF, d, {"open": []}, NOW)   # a demo key in a real window
    assert not go and "ордера: НЕТ" in text and "ДЕМО" in text
    text, go = ap.plan_all(F.env(), live_fake(perm="read_only,trade,withdraw"), PF, d, {"open": []}, NOW)
    assert not go and "ВЫВОДА" in text


def test_autopilot_finds_eea_host_and_trades_in_usdc():
    tmp = tempfile.mkdtemp()
    fk = live_fake(host=EEA, quote="USDC", balances={"USDC": 9000.0, "DOGE": 5000.0})
    e = F.env(tmp, CNQ_LIVE_TRADING="yes")
    cl = ap.connect(e, fk, {}, F.nosleep)
    assert cl.host == EEA and not cl.demo and any(c[1] == WWW for c in fk.calls)   # www said 60032 → eea.okx.com
    assert ap.pick_quote(cl, e) == "USDC" and ap.taker_fee(cl, e) == 0.002
    b = ap.build(cl, e, PF)
    assert b["buys"] and all(o["inst"].endswith("-USDC") for o in b["sells"] + b["buys"])


def test_autopilot_run_live_ioc_orders_sells_before_buys_split_and_armed():
    tmp = tempfile.mkdtemp()
    fk = live_fake()
    e = F.env(tmp, CNQ_LIVE_TRADING="yes", OKX_AUTOPILOT="on", OKX_MAX_ORDER_USD="1000")
    r = ap.run_once(e, fk, PF, NOW, F.nosleep, log_path=os.path.join(tmp, "log.jsonl"))
    assert r["ok"] and r["orders"] == len(fk.orders) > 0 and r["failed"] == 0
    sides = [o["side"] for o in fk.orders]
    assert sides == sorted(sides, key=lambda s: s != "sell")               # every sell before every buy
    for o in fk.orders:
        assert o["ordType"] == "ioc" and o["tdMode"] == "cash" and o["clOrdId"].startswith("cnqap") and o["instId"].endswith("-USDT")
        assert float(o["sz"]) * float(o["px"]) <= 1000 * 1.01                 # one order ≤ the cap (bigger ones were split)
    assert sum(1 for o in fk.orders if o["instId"] == "BTC-USDT") >= 2      # BTC $1 950 → split in two
    paths = {c[2] for c in fk.calls}
    assert not any(("withdraw" in p or "transfer" in p) for p in paths)
    st = ap.load_state(e)
    assert st["armed"] and st["last_sig"] == ap.model_sig(PF, e) and st["day_usd"] > 0 and st["split"]["spot"] == 0.65
    assert ap.status(e)["armed"] and "APKEY" not in json.dumps(ap.status(e))


def test_autopilot_refuses_withdraw_wrong_world_and_kill_switch():
    tmp = tempfile.mkdtemp()
    r = ap.run_once(F.env(tmp, CNQ_LIVE_TRADING="yes"), live_fake(perm="read_only,trade,withdraw"), PF, NOW, F.nosleep)
    assert not r["ok"] and "ВЫВОД" in r["why"]
    fk = live_fake()                                                       # a real-account key, the window in demo mode
    r = ap.run_once(F.env(tmp), fk, PF, NOW, F.nosleep)
    assert not r["ok"] and "РЕАЛЬНОГО" in r["why"] and not fk.orders
    fk = live_fake(demo_key=True)                                          # a demo key, the window in real-money mode
    r = ap.run_once(F.env(tmp, CNQ_LIVE_TRADING="yes"), fk, PF, NOW, F.nosleep)
    assert not r["ok"] and "ДЕМО" in r["why"] and not fk.orders
    fk = live_fake()
    fk.pend = [{"instId": "BTC-USDT", "ordId": "p1", "clOrdId": "cnqap1"}, {"instId": "ETH-USDT", "ordId": "p2", "clOrdId": "mine"}]
    e = F.env(tmp, CNQ_LIVE_TRADING="yes", OKX_AUTOPILOT="on")
    msg = ap.stop(e, fk)
    assert ap.stopped(e) and "остановлен" in msg and [c["ordId"] for c in fk.cancels] == ["p1"]   # only the autopilot's own order
    r = ap.run_once(e, fk, PF, NOW, F.nosleep)
    assert not r["ok"] and "стоп" in r["why"] and not fk.orders
    ap.resume(e)
    assert not ap.stopped(e)


def test_autopilot_day_limit_and_partial_fills():
    tmp = tempfile.mkdtemp()
    fk = live_fake(fill=0.5)
    e = F.env(tmp, CNQ_LIVE_TRADING="yes", OKX_MAX_DAY_USD="1500")
    r = ap.run_once(e, fk, PF, NOW, F.nosleep, log_path=os.path.join(tmp, "log.jsonl"))
    assert r["ok"] and r["limit"] and r["skipped"] >= 1 and r["partial"] >= 1
    assert ap.load_state(e)["day_usd"] <= 1500


def test_window_rebalances_only_when_the_public_portfolio_changes_or_the_account_drifted():
    tmp = tempfile.mkdtemp()
    e = F.env(tmp, CNQ_LIVE_TRADING="yes", OKX_AUTOPILOT="on")
    assert ap.tick(e, live_fake(), PF, NOW, F.nosleep) is None              # never armed → nothing
    fk = live_fake()
    ap.run_once(e, fk, PF, NOW, F.nosleep, log_path=os.path.join(tmp, "l"))
    st = ap.load_state(e)
    assert ap.due(st, PF, NOW + 300_000, e) is None                         # same portfolio five minutes later
    changed = json.loads(json.dumps(PF))
    changed["portfolios"]["ideas"]["recent_actions"].append({"time": NOW + 200_000, "symbol": "AAA", "action": "SELL", "reason": "стоп −20%"})
    changed["portfolios"]["ideas"]["positions"] = [p for p in changed["portfolios"]["ideas"]["positions"] if p["symbol"] != "AAA"]
    assert ap.due(st, changed, NOW + 300_000, e) == "портфель CRYPTO NOVA изменился"
    assert ap.due(st, changed, NOW + 300_000, {**e, "OKX_AUTOPILOT": "off"}) is None
    n = len(fk.orders)
    msg = ap.tick(e, fk, changed, NOW + 300_000, F.nosleep)
    assert msg and msg.startswith("Автопилот OKX (портфель CRYPTO NOVA изменился)")
    sold = [o for o in fk.orders[n:] if o["side"] == "sell"]
    assert sold and sold[0]["instId"] == "AAA-USDT"                         # the public portfolio's stop reached the account
    assert ap.tick(e, fk, changed, NOW + 600_000, F.nosleep) is None        # nothing new, no drift check yet


def test_active_spot_coins_are_never_sold_by_the_spot_rebalance():
    tmp = tempfile.mkdtemp()
    e = F.env(tmp)
    ac.save_state({"started": NOW, "trades": {"x": {"ccy": "XRP", "q0": 100.0, "qty": 100.0, "opened": NOW}}}, e)
    fk = live_fake(demo_key=True, balances={"USDT": 9000.0, "XRP": 100.0})
    b = ap.build(ap.connect(e, fk, {}, F.nosleep), e, PF)
    assert "XRP" not in {o["ccy"] for o in b["sells"]} and b["active_held"] == {"XRP": 100.0}


def test_autopilot_check_lines_name_each_key_and_the_futures_readiness():
    tmp = tempfile.mkdtemp()
    lines = ap.check(F.env(tmp), live_fake(perm="read_only,trade,withdraw", acct_lv="1"))
    text = "\n".join(lines)
    assert "www.okx.com" in text and "РЕАЛЬНОГО счёта" in text and "ВЫВОДА" in text and "203.0.113.7" in text
    assert "режим счёта: «Спот» (acctLv 1)" in text and "Нужен режим «Фьючерсы»" in text and "бюджет фьючерсов" in text
    assert "Мой портфель OKX" in text
    text = "\n".join(ap.check(F.env(tmp), F.FakeOkx()))
    assert "«Фьючерсы» (acctLv 2" in text and "Нужен режим" not in text and "плечо не выше 2" in text
    assert ap.check({"OKX_AUTOPILOT_STATE": os.path.join(tmp, "s")}, None)[0].startswith("Автопилот OKX: ключа нет")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_legacy_okx_client_finds_the_eea_host_and_never_retries_an_order():
    from cnq.execution.okx_spot import OkxSpot
    seen = []

    def opener(req):
        seen.append((req.get_method(), req.full_url))
        if req.full_url.startswith("https://www.okx.com") and req.has_header("Ok-access-key"):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"code":"60032","msg":"wrong domain","data":[]}'))
        if "/account/balance" in req.full_url:
            return _Resp(json.dumps({"code": "0", "data": [{"details": [{"ccy": "USDC", "cashBal": "100", "eqUsd": "100"}]}]}).encode())
        return _Resp(json.dumps({"code": "0", "data": [{"ordId": "1", "sCode": "0"}]}).encode())

    a = OkxSpot("k", "s", "p", live=True, opener=opener)
    a.holdings()
    assert a.base == EEA and [u for m, u in seen][-1].startswith(EEA)
    seen.clear()
    a.market("BTC", "Buy", 10, True)
    assert len(seen) == 1 and seen[0][1].startswith(EEA)                   # the remembered host; an order is sent once


def test_spot_mirror_never_runs_the_real_okx_account():
    from cnq.execution import spot_mirror as SM
    old = os.environ.get("CNQ_LIVE_TRADING")
    os.environ["CNQ_LIVE_TRADING"] = "yes"
    try:
        assert SM.main(["--exchange", "okx", "--run"]) == 2
    finally:
        if old is None:
            os.environ.pop("CNQ_LIVE_TRADING", None)
        else:
            os.environ["CNQ_LIVE_TRADING"] = old


def test_card_prices_eea_pairs_and_shows_the_autopilot_state():
    p = ms.prices([{"instId": "BTC-USDC", "last": "100000", "open24h": "98000"}, {"instId": "USDC-EUR", "last": "0.9", "open24h": "0.9"},
                   {"instId": "XYZ-EUR", "last": "9", "open24h": "9"}])
    assert p["BTC"] == (100000.0, 98000.0) and round(p["XYZ"][0], 6) == 10.0 and round(p["EUR"][0], 6) == round(1 / 0.9, 6)
    tmp = tempfile.mkdtemp()
    st = ap.status(F.env(tmp, OKX_AUTOPILOT="on"))
    assert st["configured"] and not st["armed"] and st["mode"] == "демо-торговля OKX" and st["split"]["futures"] == 0.15
    assert "futures" in st and st["futures"]["lev"] == 2 and st["active"]["enabled"]
    assert ap.status({"OKX_AUTOPILOT_STATE": os.path.join(tmp, "x")}) is None


def test_autopilot_global_account_uses_www_usdt_and_global_fee():
    """The owner's real case (22.10): the Ukrainian / global OKX account — www.okx.com, USDT pairs, 0.10 % taker."""
    tmp = tempfile.mkdtemp()
    fk = live_fake()
    e = F.env(tmp, CNQ_LIVE_TRADING="yes")
    cl = ap.connect(e, fk, {}, F.nosleep)
    assert cl.host == WWW and not ap.is_eea(cl) and {c[1] for c in fk.calls} == {WWW}
    assert ap.pick_quote(cl, e) == "USDT" and ap.taker_fee(cl, e) == 0.001
    text = "\n".join(ap.check(e, live_fake()))
    assert "www.okx.com · глобальный счёт OKX" in text and "валюта торговли: USDT" in text and "комиссия 0,1%" in text
    assert "OKX Europe" not in text


def test_okx_autopilot_talks_ipv4_only_so_the_bound_ip_matches():
    """22.10: a home Mac's temporary IPv6 changes daily — the key is bound to the IPv4 address, so the process resolves IPv4 only."""
    import socket
    from cnq.execution import okx_autopilot as ap
    orig = socket.getaddrinfo
    seen = []

    def fake(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        seen.append(family)
        if host == "v6only.example" and family == socket.AF_INET:
            raise socket.gaierror("no A record")
        return [(family or socket.AF_INET6, type, proto, "", (host, port))]
    try:
        socket.getaddrinfo = fake
        assert ap.ipv4_only({"OKX_IPV6": "on"}) is False and socket.getaddrinfo is fake
        assert ap.ipv4_only({}) is True and ap.ipv4_only({}) is True          # installed once
        assert socket.getaddrinfo("www.okx.com", 443)[0][0] == socket.AF_INET and seen[-1] == socket.AF_INET
        socket.getaddrinfo("v6only.example", 443)                              # no IPv4 address → resolves as before
        assert seen[-2:] == [socket.AF_INET, 0]
        assert socket.getaddrinfo("x", 1, socket.AF_INET6)[0][0] == socket.AF_INET6   # an explicit family is left alone
    finally:
        socket.getaddrinfo = orig


def test_plan_reason_of_a_held_coin_is_the_portfolios_own_never_the_screeners_opposite_advice():
    """22.10: «КУПИТЬ … PENGU … почему: … ждём просадку от −30%» — the screener's line of today said the opposite of the portfolio
    that holds the coin. A held coin's reason is the portfolio's own (its buy / top-up reason, else the position's reason) + its stop."""
    pf = json.loads(json.dumps(PF))
    pos = pf["portfolios"]["ideas"]["positions"]
    bbb = next(p for p in pos if p["symbol"] == "1000BBB")
    bbb.update({"table_why_ru": "−26% от максимума за 30 дней — ждём просадку от −30%", "entry_time": NOW - 5 * 86_400_000,
                "reason": "Из таблицы «можно на небольшую сумму»: Просадка −32% от максимума за 30 дней, проверки пройдены. 2.5% счёта, "
                          "стоп −35%, на ×2 продаём половину; продаём и при статусе «не брать».", "levels": {"stop": 1.3, "take_half": 4.0}})
    aaa = next(p for p in pos if p["symbol"] == "AAA")
    aaa.update({"table_why_ru": "ждём отката", "entry_time": NOW - 5 * 86_400_000, "reason": "План: AAA 9.7% счёта — лишнее продаём."})
    pf["portfolios"]["ideas"]["recent_actions"] += [
        {"time": NOW - 5 * 86_400_000, "symbol": "AAA", "action": "BUY", "reason": "Из таблицы «можно брать»: Анкета 50. 10% счёта, стоп −20% от входа."},
        {"time": NOW - 86_400_000, "symbol": "AAA", "action": "TRIM", "reason": "План: AAA 9.7% счёта — лишнее продаём."},
        {"time": NOW - 30 * 86_400_000, "symbol": "BBB", "action": "BUY", "reason": "старая покупка до продажи"}]
    by = al.ideas(pf)
    why = al.reason(by["1000BBB"])
    assert "ждём" not in why and "Просадка −32%" in why and "в портфеле CRYPTO NOVA: из таблицы «можно на небольшую сумму»" in why
    assert "стоп портфеля 1.3, на ×2 продаёт половину (4)" in why and "старая покупка" not in why
    why = al.reason(by["AAA"])
    assert "ждём" not in why and "Анкета 50" in why and "лишнее продаём" not in why     # the buy reason, not the later trim
    assert "без стопа" in al.reason(by["BTC"]) and al.reason(by["BTC"]).count("без стопа") == 1
    e = F.env()
    b = ap.build(ap.connect(e, live_fake(demo_key=True), {}, F.nosleep), e, pf)
    text = ap.plan_text(b, "test")
    line = next(x for x in text.split("\n") if "КУПИТЬ" in x and " BBB " in x)
    assert "ждём" not in line and "Просадка −32%" in line and "стоп портфеля 1.3" in line
