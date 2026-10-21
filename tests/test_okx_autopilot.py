"""«Автопилот OKX» (cnq/execution/okx_autopilot.py) with a fake OKX: the host is found (an OKX Europe key fails on www with 60032),
an EEA account trades in USDC (never -USDT), sells go before buys as limit IOC orders with a price cap, caps and the kill switch
hold, a key with «Withdraw» or from the wrong world is refused, the hourly check trades only when the model asks.
Also: the legacy OKX client finds the host too, spot_mirror never runs the owner's real OKX account, the card prices EEA pairs.
Every coin, key and amount here is made-up test data; the real OKX API is never called."""
import io
import json
import os
import tempfile
import urllib.error
import urllib.parse

from cnq.execution import myspot_watch as ms
from cnq.execution import okx_autopilot as ap

ap.LOG = os.path.join(tempfile.mkdtemp(), "okx_autopilot.jsonl")      # never write into the repo's data/

EEA = "https://eea.okx.com"
NOW = 1_800_000_000_000          # Friday 2027-01-15 08:00 UTC
MONDAY = 1_800_259_200_000        # NOW + 3 days → Monday 2027-01-18 (UTC)
MODEL = ({"asof": NOW, "gauge": {"on": True, "bull": True, "breadth_pct": 66},
          "targets_pct": {"BTC": 40.0, "Фьючерсы (сигналы)": 15.0, "USDT (Earn)": 10.0, "ETH": 10.0, "SOL": 5.0, "AAA": 10.0, "BBB": 10.0},
          "basket": ["AAA", "BBB"]},
         {"asof": NOW, "universe": 40, "basket": ["AAA"],
          "rows": [{"symbol": "AAA", "rank": 2, "score": 1.2, "age": 900, "m3": -0.1, "vt": 0.8, "rev": True, "rev30": 40_000},
                   {"symbol": "BBB", "rank": 9, "score": 0.9, "age": 500, "m3": 0.35, "vt": 1.6, "rev": False},
                   {"symbol": "DOGE", "rank": 30, "score": 0.2, "age": 3000, "m3": -0.1, "vt": 0.9, "rev": False}]},
         {"coins": {}})
PX = {"BTC": 100000.0, "ETH": 3000.0, "SOL": 200.0, "AAA": 10.0, "BBB": 2.0, "DOGE": 0.2}


class FakeOkx:
    """One OKX Europe account: the key works on eea.okx.com only (www answers 60032 like a real EEA key there); spot pairs are
    -USDC and -EUR, no -USDT. Orders fill fully at the limit price. Records every call."""

    def __init__(self, perm="read_only,trade", demo_key=False, balances=None, funding=None, fill=1.0, host=EEA, quote="USDC"):
        self.perm, self.demo_key, self.fill, self.calls, self.orders, self.cancels = perm, demo_key, fill, [], [], []
        self.host, self.quote = host, quote
        self.bal = balances or {"BTC": 0.01, "ETH": 1.0, "DOGE": 1000.0, "USDC": 5800.0}
        self.funding = funding or {"SOL": 2.0}
        self.pend = []

    def __call__(self, method, url, headers, body):
        u = urllib.parse.urlparse(url)
        host, path, q = f"{u.scheme}://{u.netloc}", u.path, dict(urllib.parse.parse_qsl(u.query))
        self.calls.append((method, host, path, q, body, dict(headers)))
        signed = "OK-ACCESS-KEY" in headers
        if signed and host != self.host:
            return {"code": "60032", "msg": "API key is not valid for this domain", "data": []}
        if signed and (headers.get("x-simulated-trading") == "1") != self.demo_key:
            return {"code": "50101", "msg": "APIKey does not match current environment", "data": []}
        if path == "/api/v5/account/balance":
            det = [{"ccy": c, "eq": str(v), "availBal": str(v), "cashBal": str(v),
                    "eqUsd": str(v * (PX.get(c) or 1.0))} for c, v in self.bal.items() if not q.get("ccy") or q["ccy"] == c]
            return {"code": "0", "data": [{"details": det}]}
        if path == "/api/v5/asset/balances":
            return {"code": "0", "data": [{"ccy": c, "bal": str(v)} for c, v in self.funding.items()]}
        if path == "/api/v5/finance/savings/balance":
            return {"code": "0", "data": []}
        if path == "/api/v5/account/config":
            return {"code": "0", "data": [{"perm": self.perm, "ip": "203.0.113.7"}]}
        if path == "/api/v5/market/tickers":
            return {"code": "0", "data": [{"instId": f"{c}-{self.quote}", "last": str(p), "open24h": str(p)} for c, p in PX.items()]
                    + [{"instId": "USDC-EUR", "last": "0.9", "open24h": "0.9"}]}
        if path == "/api/v5/public/instruments":
            inst = q.get("instId", "")
            base, quote = inst.split("-")
            if quote != self.quote or base not in PX:
                return {"code": "0", "data": []}
            tick = "0.01" if PX[base] >= 1000 else "0.001" if PX[base] >= 1 else "0.00001"
            return {"code": "0", "data": [{"instId": inst, "state": "live", "lotSz": "0.0001", "minSz": "0.0001", "tickSz": tick}]}
        if path == "/api/v5/market/ticker":
            base = q["instId"].split("-")[0]
            p = PX[base]
            return {"code": "0", "data": [{"instId": q["instId"], "last": str(p), "bidPx": str(p * 0.999), "askPx": str(p * 1.001)}]}
        if path == "/api/v5/trade/order" and method == "POST":
            b = json.loads(body)
            self.orders.append(b)
            return {"code": "0", "data": [{"ordId": f"o{len(self.orders)}", "clOrdId": b["clOrdId"], "sCode": "0", "sMsg": ""}]}
        if path == "/api/v5/trade/order" and method == "GET":
            b = self.orders[int(q["ordId"][1:]) - 1]
            sz = float(b["sz"]) * self.fill
            return {"code": "0", "data": [{"ordId": q["ordId"], "state": "filled" if self.fill >= 1 else "canceled",
                                           "accFillSz": str(sz), "avgPx": b["px"], "fee": "-0.1"}]}
        if path == "/api/v5/trade/orders-pending":
            return {"code": "0", "data": self.pend}
        if path == "/api/v5/trade/cancel-order":
            self.cancels.append(json.loads(body))
            return {"code": "0", "data": [{"sCode": "0"}]}
        return {"code": "51000", "msg": f"unexpected {path}", "data": []}


def env(tmp, **kw):
    e = {"OKX_API_KEY": "APKEY-test", "OKX_API_SECRET": "APSECRET-test", "OKX_API_PASSPHRASE": "APPHRASE-test",
         "OKX_AUTOPILOT_STATE": os.path.join(tmp, "state.json"), "OKX_STOP_FILE": os.path.join(tmp, "stop")}
    e.update(kw)
    return e


def nosleep(_s):
    return None


def test_autopilot_finds_eea_host_and_plans_in_usdc_sells_first():
    tmp = tempfile.mkdtemp()
    fk = FakeOkx()
    e = env(tmp, CNQ_LIVE_TRADING="yes")
    cl = ap.connect(e, fk, {}, nosleep)
    assert cl.host == EEA and not cl.demo                                  # www said 60032 → eea.okx.com
    assert any(c[1] == "https://www.okx.com" for c in fk.calls)
    assert ap.pick_quote(cl, e) == "USDC" and ap.taker_fee(cl, e) == 0.002  # OKX Europe: USDC, 0.20 % taker
    b = ap.build(cl, e, MODEL)
    insts = [o["inst"] for o in b["sells"] + b["buys"]]
    assert insts and all(i.endswith("-USDC") for i in insts)               # never a -USDT pair on an EEA account
    assert not any("-USDT" in (c[3].get("instId") or "") for c in fk.calls if c[2] == "/api/v5/trade/order")
    sells = {o["ccy"]: o for o in b["sells"]}
    assert sells["DOGE"]["act"] == "sell" and float(sells["DOGE"]["sz"]) == 1000.0   # not in the model → all of it
    assert sells["ETH"]["act"] == "trim"
    assert "SOL" not in sells and any("SOL" in n for n in b["notes"])      # SOL sits in Funding: counted, never moved
    buys = [o["ccy"] for o in b["buys"]]
    assert buys[0] == "AAA" and "BTC" in buys                              # undervalued alt first
    for o in b["sells"]:
        assert float(o["px"]) <= PX[o["ccy"]] * 0.999 and float(o["px"]) >= PX[o["ccy"]] * 0.999 * (1 - 0.0045)
    for o in b["buys"]:
        assert float(o["px"]) >= PX[o["ccy"]] * 1.001 and float(o["px"]) <= PX[o["ccy"]] * 1.001 * 1.0041 + 0.01
    assert b["fee_usd"] > 0 and "комиссия OKX" in ap.plan_text(b, "x")
    assert not fk.orders                                                   # building a plan never trades


def test_autopilot_run_live_ioc_orders_sells_before_buys_split_and_armed():
    tmp = tempfile.mkdtemp()
    fk = FakeOkx()
    e = env(tmp, CNQ_LIVE_TRADING="yes", OKX_AUTOPILOT="on", OKX_MAX_ORDER_USD="1500")
    r = ap.run_once(e, fk, MODEL, NOW, nosleep, log_path=os.path.join(tmp, "log.jsonl"))
    assert r["ok"] and r["orders"] == len(fk.orders) > 0 and r["failed"] == 0
    sides = [o["side"] for o in fk.orders]
    assert sides == sorted(sides, key=lambda s: s != "sell")               # every sell before every buy
    for o in fk.orders:
        assert o["ordType"] == "ioc" and o["tdMode"] == "cash" and o["clOrdId"].startswith("cnqap") and o["instId"].endswith("-USDC")
        assert float(o["sz"]) * float(o["px"]) <= 1500 * 1.01                 # one order ≤ the cap (bigger ones were split)
    assert sides.count("buy") >= 3
    paths = {c[2] for c in fk.calls}
    assert not any(("withdraw" in p or "transfer" in p or "leverage" in p or "position" in p) for p in paths)
    st = ap.load_state(e)
    assert st["armed"] and st["last_sig"] == ap.model_sig({**MODEL[0]}) and st["day_usd"] > 0
    assert r["sold_usd"] > 0 and r["bought_usd"] > 0
    assert ap.status(e)["armed"] and "APKEY" not in json.dumps(ap.status(e))


def test_autopilot_refuses_withdraw_wrong_world_and_kill_switch():
    tmp = tempfile.mkdtemp()
    r = ap.run_once(env(tmp, CNQ_LIVE_TRADING="yes"), FakeOkx(perm="read_only,trade,withdraw"), MODEL, NOW, nosleep)
    assert not r["ok"] and "ВЫВОД" in r["why"]
    fk = FakeOkx()                                                         # a real-account key, the window in demo mode
    r = ap.run_once(env(tmp), fk, MODEL, NOW, nosleep)
    assert not r["ok"] and "РЕАЛЬНОГО" in r["why"] and not fk.orders
    fk = FakeOkx(demo_key=True)                                            # a demo key, the window in real-money mode
    r = ap.run_once(env(tmp, CNQ_LIVE_TRADING="yes"), fk, MODEL, NOW, nosleep)
    assert not r["ok"] and "ДЕМО" in r["why"] and not fk.orders
    fk = FakeOkx()
    fk.pend = [{"instId": "BTC-USDC", "ordId": "p1", "clOrdId": "cnqap1"}, {"instId": "ETH-USDC", "ordId": "p2", "clOrdId": "mine"}]
    e = env(tmp, CNQ_LIVE_TRADING="yes", OKX_AUTOPILOT="on")
    msg = ap.stop(e, fk)
    assert ap.stopped(e) and "остановлен" in msg and [c["ordId"] for c in fk.cancels] == ["p1"]   # only the autopilot's own order
    r = ap.run_once(e, fk, MODEL, NOW, nosleep)
    assert not r["ok"] and "стоп" in r["why"] and not fk.orders
    ap.resume(e)
    assert not ap.stopped(e)


def test_autopilot_day_limit_and_partial_fills():
    tmp = tempfile.mkdtemp()
    fk = FakeOkx(fill=0.5)
    e = env(tmp, CNQ_LIVE_TRADING="yes", OKX_MAX_DAY_USD="2500")
    r = ap.run_once(e, fk, MODEL, NOW, nosleep, log_path=os.path.join(tmp, "log.jsonl"))
    assert r["ok"] and r["limit"] and r["skipped"] >= 1 and r["partial"] >= 1
    assert ap.load_state(e)["day_usd"] <= 2500


def test_autopilot_hourly_check_trades_only_when_due():
    tmp = tempfile.mkdtemp()
    e = env(tmp, CNQ_LIVE_TRADING="yes", OKX_AUTOPILOT="on")
    assert ap.tick(e, FakeOkx(), MODEL, NOW, nosleep) is None              # never armed → the hourly check does nothing
    ap.run_once(e, FakeOkx(), MODEL, NOW, nosleep, log_path=os.path.join(tmp, "l"))
    st = ap.load_state(e)
    assert ap.due(st, MODEL[0], NOW + 3600_000, e) is None                  # same model, an hour later
    changed = {**MODEL[0], "gauge": {**MODEL[0]["gauge"], "on": False}}
    assert ap.due(st, changed, NOW + 3600_000, e) == "модель изменилась"
    assert ap.due(st, MODEL[0], MONDAY, e) is None                          # Monday, but only 3 days since the last rebalance
    st["last_run"] = MONDAY - 6 * 86_400_000
    assert ap.due(st, MODEL[0], MONDAY, e) == "понедельник — сверка долей"
    assert ap.due(st, MODEL[0], MONDAY, {**e, "OKX_AUTOPILOT": "off"}) is None
    fk = FakeOkx()
    msg = ap.tick(e, fk, ({**MODEL[0], "basket": ["AAA"]}, MODEL[1], MODEL[2]), NOW + 7200_000, nosleep)
    assert msg and msg.startswith("Автопилот OKX (модель изменилась)") and fk.orders


def test_autopilot_check_lines_name_each_key_and_warn():
    tmp = tempfile.mkdtemp()
    lines = ap.check(env(tmp), FakeOkx(perm="read_only,trade,withdraw"))
    text = "\n".join(lines)
    assert "eea.okx.com" in text and "OKX Europe" in text and "РЕАЛЬНОГО счёта" in text
    assert "ВЫВОДА" in text and "USDC" in text and "203.0.113.7" in text and "вне торгового счёта: SOL" in text
    assert "Мой портфель OKX" in text                                      # the card's read-only key is reported too
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
    usdc, held = a.holdings()
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
    e = env(tmp, OKX_AUTOPILOT="on")
    st = ap.status(e)
    assert st["configured"] and not st["armed"] and st["mode"] == "демо-торговля OKX"
    assert ap.status({"OKX_AUTOPILOT_STATE": os.path.join(tmp, "x")}) is None


def test_autopilot_global_account_uses_www_usdt_and_global_fee():
    """The owner's real case (22.10): the Ukrainian / global OKX account — www.okx.com, USDT pairs, 0.10 % taker."""
    tmp = tempfile.mkdtemp()
    fk = FakeOkx(host="https://www.okx.com", quote="USDT", balances={"BTC": 0.01, "ETH": 1.0, "DOGE": 1000.0, "USDT": 5800.0})
    e = env(tmp, CNQ_LIVE_TRADING="yes")
    cl = ap.connect(e, fk, {}, nosleep)
    assert cl.host == "https://www.okx.com" and not ap.is_eea(cl)
    assert {c[1] for c in fk.calls} == {"https://www.okx.com"}                # the first host works: nothing else is tried
    assert ap.pick_quote(cl, e) == "USDT" and ap.taker_fee(cl, e) == 0.001
    b = ap.build(cl, e, MODEL)
    assert all(o["inst"].endswith("-USDT") for o in b["sells"] + b["buys"]) and not b["eea"]
    text = "\n".join(ap.check(e, FakeOkx(host="https://www.okx.com", quote="USDT")))
    assert "www.okx.com · глобальный счёт OKX" in text and "валюта торговли: USDT" in text and "комиссия 0,1%" in text
    assert "OKX Europe" not in text
