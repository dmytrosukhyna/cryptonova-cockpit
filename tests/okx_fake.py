"""A fake OKX v5 for the autopilot tests: spot + USDT swaps + the lead-trader calls. Every coin, key and amount is made-up test
data; the real OKX API is never called. The fake checks the host and the key's world (demo / real) like OKX does."""
import json
import os
import tempfile
import urllib.parse

WWW, EEA = "https://www.okx.com", "https://eea.okx.com"
NOW = 1_800_000_000_000          # Friday 2027-01-15 08:00 UTC
PX = {"BTC": 100000.0, "ETH": 3000.0, "SOL": 200.0, "AAA": 10.0, "BBB": 0.002, "CCC": 5.0, "DOGE": 0.2, "APT": 1.0, "XRP": 2.0,
      "LINK": 20.0, "AVAX": 30.0, "ADA": 0.5, "DOT": 4.0}
SWAP_CT = {"BTC": "0.01", "ETH": "0.1", "SOL": "1", "APT": "1", "XRP": "10", "LINK": "1", "AVAX": "1", "ADA": "100", "DOT": "1"}


def portfolio():
    """«Наш портфель CRYPTO NOVA» (made up): core + 2 alts + a 1000-ticker + a coin OKX lacks + a coin whose OKX price differs."""
    def p(sym, w, px, stop=None, kind="крупный альт", role="из таблицы: можно брать", bucket="mid"):
        return {"symbol": sym, "weight": w, "price": px, "status": "OPEN", "name": sym, "bucket": bucket, "kind_ru": kind, "role_ru": role,
                "table_why_ru": f"анкета {sym}", "levels": {"stop": stop, "take_half": None}, "target_weight": w}
    return {"asof": NOW, "portfolios": {"ideas": {"name": "CRYPTO NOVA портфель", "positions": [
        p("BTC", 0.30, 100000.0, kind="ядро", bucket="core"), p("ETH", 0.10, 3000.0, kind="ядро", bucket="core"),
        p("SOL", 0.05, 200.0, kind="ядро", bucket="core"), p("AAA", 0.075, 10.0, stop=8.0),
        p("1000BBB", 0.025, 2.0, stop=1.3, kind="мем", role="на небольшую сумму", bucket="meme"),
        p("ZZZ", 0.075, 1.0, stop=0.8), p("CCC", 0.05, 1.0, stop=0.8)],
        "recent_actions": [{"time": NOW - 3600_000, "symbol": "DOGE", "action": "SELL", "reason": "таблица поставила «не брать»"}]}}}


class FakeOkx:
    def __init__(self, perm="read_only,trade", demo_key=True, balances=None, funding=None, fill=1.0, host=WWW, quote="USDT",
                 acct_lv="2", pos_mode="net_mode", role="0", attach=True, algo_ok=True, hist=None, lead_insts=None):
        self.perm, self.demo_key, self.fill, self.host, self.quote = perm, demo_key, fill, host, quote
        self.acct_lv, self.pos_mode, self.role, self.attach, self.algo_ok = acct_lv, pos_mode, role, attach, algo_ok
        self.bal = dict(balances or {"USDT": 10000.0})
        self.funding = funding or {}
        self.calls, self.orders, self.cancels, self.algo_posts, self.amends, self.levers, self.lead_calls = [], [], [], [], [], [], []
        self.pos = {}           # instId → signed contracts (net) ; avg px
        self.algos = []         # pending stop / target orders
        self.hist = hist or []
        self.lead_insts = lead_insts or []
        self.pend = []
        self.n = 0

    # ── helpers
    def _px(self, inst):
        return PX[inst.split("-")[0]]

    def _swap(self, inst):
        return inst.endswith("-SWAP")

    def __call__(self, method, url, headers, body):
        u = urllib.parse.urlparse(url)
        host, path, q = f"{u.scheme}://{u.netloc}", u.path, dict(urllib.parse.parse_qsl(u.query))
        b = json.loads(body) if body else None
        self.calls.append((method, host, path, q, b, dict(headers)))
        signed = "OK-ACCESS-KEY" in headers
        if signed and host != self.host:
            return {"code": "60032", "msg": "API key is not valid for this domain", "data": []}
        if signed and (headers.get("x-simulated-trading") == "1") != self.demo_key:
            return {"code": "50101", "msg": "APIKey does not match current environment", "data": []}
        ok = lambda d: {"code": "0", "data": d}  # noqa: E731
        if path == "/api/v5/account/balance":
            det = [{"ccy": c, "eq": str(v), "availBal": str(v), "cashBal": str(v), "eqUsd": str(v * (PX.get(c) or 1.0))}
                   for c, v in self.bal.items() if not q.get("ccy") or q["ccy"] == c]
            return ok([{"totalEq": str(sum(v * (PX.get(c) or 1.0) for c, v in self.bal.items())), "details": det}])
        if path == "/api/v5/asset/balances":
            return ok([{"ccy": c, "bal": str(v)} for c, v in self.funding.items()])
        if path == "/api/v5/finance/savings/balance":
            return ok([])
        if path == "/api/v5/account/config":
            return ok([{"perm": self.perm, "ip": "203.0.113.7", "acctLv": self.acct_lv, "posMode": self.pos_mode, "roleType": self.role,
                        "traderInsts": self.lead_insts}])
        if path == "/api/v5/market/tickers":
            return ok([{"instId": f"{c}-{self.quote}", "last": str(p), "open24h": str(p)} for c, p in PX.items()])
        if path == "/api/v5/public/instruments":
            inst = q.get("instId", "")
            if q.get("instType") == "SWAP":
                base = inst.split("-")[0]
                if not self._swap(inst) or base not in SWAP_CT:
                    return ok([])
                tick = "0.1" if PX[base] >= 1000 else "0.001" if PX[base] >= 1 else "0.00001"
                return ok([{"instId": inst, "state": "live", "ctVal": SWAP_CT[base], "ctValCcy": base, "lotSz": "0.01", "minSz": "0.01",
                            "tickSz": tick, "settleCcy": "USDT", "ctType": "linear", "lever": "50"}])
            base, quote = inst.split("-")[:2]
            if quote != self.quote or base not in PX or base == "ZZZ":
                return ok([])
            tick = "0.01" if PX[base] >= 1000 else "0.001" if PX[base] >= 1 else "0.0000001"
            return ok([{"instId": inst, "state": "live", "lotSz": "0.0001" if PX[base] >= 1 else "1", "minSz": "0.0001" if PX[base] >= 1 else "1",
                        "tickSz": tick}])
        if path == "/api/v5/market/ticker":
            base = q["instId"].split("-")[0]
            if base not in PX:
                return ok([])
            p = PX[base]
            return ok([{"instId": q["instId"], "last": str(p), "bidPx": str(p * 0.999), "askPx": str(p * 1.001)}])
        if path == "/api/v5/trade/order" and method == "POST":
            self.orders.append(b)
            oid = f"o{len(self.orders)}"
            inst = b["instId"]
            want = float(b["sz"])
            filled = want if b.get("ordType") == "market" else want * self.fill
            if self._swap(inst):
                cur = self.pos.get(inst, 0.0)
                sgn = 1 if b["side"] == "buy" else -1
                if b.get("reduceOnly") or (b.get("posSide") and ((b["posSide"] == "long") != (b["side"] == "buy"))):
                    filled = min(filled, abs(cur))
                if filled:
                    self.pos[inst] = round(cur + sgn * filled, 10)
                    if abs(self.pos[inst]) < 1e-12:
                        self.pos.pop(inst)
                if filled and self.attach and b.get("attachAlgoOrds"):
                    a = b["attachAlgoOrds"][0]
                    self.n += 1
                    self.algos.append({"algoId": f"a{self.n}", "algoClOrdId": a.get("attachAlgoClOrdId"), "instId": inst,
                                       "ordType": "oco" if a.get("tpTriggerPx") else "conditional", "sz": str(filled),
                                       "slTriggerPx": a.get("slTriggerPx"), "tpTriggerPx": a.get("tpTriggerPx", ""),
                                       "slTriggerPxType": a.get("slTriggerPxType"), "closeFraction": ""})
            else:
                base, quote = inst.split("-")
                if b["side"] == "buy":
                    self.bal[base] = self.bal.get(base, 0) + filled
                    self.bal[quote] = self.bal.get(quote, 0) - filled * float(b["px"])
                else:
                    self.bal[base] = self.bal.get(base, 0) - filled
                    self.bal[quote] = self.bal.get(quote, 0) + filled * float(b["px"])
            b["_filled"] = filled
            return ok([{"ordId": oid, "clOrdId": b.get("clOrdId"), "sCode": "0", "sMsg": ""}])
        if path == "/api/v5/trade/order" and method == "GET":
            o = self.orders[int(q["ordId"][1:]) - 1]
            f = o.get("_filled", 0.0)
            return ok([{"ordId": q["ordId"], "state": "filled" if f >= float(o["sz"]) else "canceled", "accFillSz": str(f),
                        "avgPx": o.get("px") or str(self._px(o["instId"])), "fee": "-0.1"}])
        if path == "/api/v5/trade/orders-pending":
            return ok(self.pend)
        if path == "/api/v5/trade/cancel-order":
            self.cancels.append(b)
            return ok([{"sCode": "0"}])
        if path == "/api/v5/account/set-leverage":
            self.levers.append(b)
            return ok([{"lever": b["lever"]}])
        if path == "/api/v5/account/positions":
            return ok([{"instId": i, "pos": str(v), "posSide": "net", "avgPx": str(self._px(i)), "upl": "0",
                        "notionalUsd": str(abs(v) * float(SWAP_CT[i.split("-")[0]]) * self._px(i)), "mgnMode": "isolated"}
                       for i, v in self.pos.items()])
        if path == "/api/v5/account/positions-history":
            return ok(self.hist)
        if path == "/api/v5/trade/orders-algo-pending":
            return ok([a for a in self.algos if a["ordType"] == q.get("ordType") and (not q.get("instId") or a["instId"] == q["instId"])])
        if path == "/api/v5/trade/amend-algos":
            self.amends.append(b)
            for a in self.algos:
                if a["algoId"] == b["algoId"]:
                    a["slTriggerPx"] = b.get("newSlTriggerPx", a["slTriggerPx"])
                    if b.get("newTpTriggerPx"):
                        a["tpTriggerPx"] = b["newTpTriggerPx"]
                    if b.get("newSz"):
                        a["sz"] = b["newSz"]
            return ok([{"algoId": b["algoId"], "sCode": "0"}])
        if path == "/api/v5/trade/order-algo":
            self.algo_posts.append(b)
            if not self.algo_ok:
                return {"code": "1", "msg": "", "data": [{"sCode": "51277", "sMsg": "TP/SL rejected"}]}
            self.n += 1
            self.algos.append({"algoId": f"a{self.n}", "algoClOrdId": b.get("algoClOrdId"), "instId": b["instId"], "ordType": "conditional",
                               "sz": "", "closeFraction": b.get("closeFraction", ""), "slTriggerPx": b.get("slTriggerPx"),
                               "tpTriggerPx": b.get("tpTriggerPx", "")})
            return ok([{"algoId": f"a{self.n}", "sCode": "0"}])
        if path == "/api/v5/trade/cancel-algos":
            ids = {x["algoId"] for x in b}
            self.cancels += b
            self.algos = [a for a in self.algos if a["algoId"] not in ids]
            return ok([{"sCode": "0"}])
        if path == "/api/v5/copytrading/current-subpositions":
            i = q.get("instId")
            return ok([{"subPosId": f"s-{i}", "instId": i, "subPos": str(abs(self.pos[i])),
                        "slTriggerPx": next((a["slTriggerPx"] for a in self.algos if a["instId"] == i and a.get("lead")), "")}]
                      if i in self.pos else [])
        if path == "/api/v5/copytrading/algo-order":
            self.lead_calls.append(("stop", b))
            i = b["subPosId"][2:]
            self.algos = [a for a in self.algos if a["instId"] != i] + [{"algoId": f"L{i}", "instId": i, "ordType": "x", "lead": True,
                                                                         "slTriggerPx": b["slTriggerPx"]}]
            return ok([{"subPosId": b["subPosId"], "sCode": "0"}])
        if path == "/api/v5/copytrading/close-subposition":
            self.lead_calls.append(("close", b))
            self.pos.pop(b["subPosId"][2:], None)
            return ok([{"subPosId": b["subPosId"], "sCode": "0"}])
        return {"code": "51000", "msg": f"unexpected {path}", "data": []}


def env(tmp=None, **kw):
    tmp = tmp or tempfile.mkdtemp()
    e = {"OKX_API_KEY": "APKEY-test", "OKX_API_SECRET": "APSECRET-test", "OKX_API_PASSPHRASE": "APPHRASE-test",
         "OKX_AUTOPILOT_STATE": os.path.join(tmp, "state.json"), "OKX_STOP_FILE": os.path.join(tmp, "stop"),
         "OKX_FUT_STATE": os.path.join(tmp, "fut.json"), "OKX_ACT_STATE": os.path.join(tmp, "act.json"),
         "OKX_LEASE_FILE": os.path.join(tmp, "lease.json")}      # the lease (okx_lease.py) in a local file, never GitHub
    e.update(kw)
    return e


def nosleep(_s):
    return None


def trade(tid="T1", sym="SOL", side="LONG", entry=200.0, stop=190.0, tp=230.0, risk=150.0, opened=NOW - 120_000, qty=None, qty_left=None):
    q = qty if qty is not None else risk / abs(entry - stop)
    return {"id": tid, "symbol": sym, "side": side, "entry": entry, "stop": stop, "stop_now": stop, "tps": [tp, tp, tp],
            "parts": [1.0, 0.0, 0.0], "qty": q, "qty_left": q if qty_left is None else qty_left, "risk_usd": risk, "opened": opened}


def desks(*trades_bal, imp=()):
    return [("bal", {"equity": 10000.0, "open": list(trades_bal)}), ("imp", {"equity": 10000.0, "open": list(imp)})]
