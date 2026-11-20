"""20.11 (owner's explicit decision, real money): the spot core ladder of resting limit buys (cnq/execution/okx_ladder.py) with a fake
OKX client. Prices and sizes are made up; no real API is called."""
import json
import os
import tempfile

from cnq.execution import okx_ladder as L
from cnq.execution import okx_futures as fu

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = 1_795_200_000_000
CFG = json.load(open(os.path.join(ROOT, "config", "owner_overrides.json"), encoding="utf-8"))
INST = {"BTC-USDT": {"lotSz": "0.00000001", "minSz": "0.00001", "tickSz": "0.1"},
        "ETH-USDT": {"lotSz": "0.000001", "minSz": "0.0001", "tickSz": "0.01"},
        "SOL-USDT": {"lotSz": "0.000001", "minSz": "0.001", "tickSz": "0.001"}}


class Fake:
    def __init__(self, px=None, free=2000.0, reject=()):
        self.px = px or {"BTC-USDT": 62000.0, "ETH-USDT": 1760.0, "SOL-USDT": 72.0}
        self.free, self.reject, self.orders, self.cancelled = free, set(reject), {}, []
        self.fills = {}

    def instrument(self, iid):
        return dict(INST[iid]) if iid in INST else None

    def ticker(self, iid):
        return {"last": str(self.px[iid])} if iid in self.px else None

    def avail(self, ccy):
        return self.free

    def call(self, method, path, q=None, body=None):
        assert method == "POST" and path == "/api/v5/trade/order"
        assert body["ordType"] == "post_only" and body["side"] == "buy" and body["tdMode"] == "cash" and body["clOrdId"].startswith("cnqlad")
        if body["instId"] in self.reject:
            return [{"sCode": "51008", "sMsg": "insufficient"}]
        oid = str(len(self.orders) + 1)
        self.orders[oid] = body
        self.free -= float(body["px"]) * float(body["sz"])          # OKX freezes the order's USDT
        return [{"ordId": oid, "sCode": "0"}]

    def order_state(self, iid, ord_id=None, cl_id=None):
        f = self.fills.get(ord_id, 0.0)
        b = self.orders[ord_id]
        return {"state": "filled" if f >= float(b["sz"]) else ("partially_filled" if f else "live"), "accFillSz": str(f), "avgPx": b["px"]}

    def cancel(self, iid, ord_id):
        self.cancelled.append(ord_id)


def test_the_owners_ladder_is_in_the_config_as_decided():
    c = L.cfg(CFG)
    assert c and c["levels"] == [[58200, 150], [57500, 200], [56800, 200], [56000, 200], [55000, 150], [53500, 151]]
    assert sum(u for _, u in c["levels"]) == 1051 and c["split"] == {"BTC": 0.6, "ETH": 0.25, "SOL": 0.15}
    assert c["fut_usd"] == 500 and c["fut_min"] == 600 and c["days"] == 14 and c["id"].isalnum()
    assert os.path.exists(os.path.join(ROOT, "docs", "owner-decision-2026-11-20-ladder.md"))


def test_plan_prices_sizes_and_small_parts_go_to_btc():
    px = {"BTC": 62000.0, "ETH": 1760.0, "SOL": 72.0}
    inst = {c: {**INST[f"{c}-USDT"], "instId": f"{c}-USDT"} for c in px}
    o = L.plan(L.cfg(CFG), px, inst)
    first = [x for x in o if x["lvl"] == 0]
    btc, eth, sol = (next(x for x in first if x["ccy"] == c) for c in ("BTC", "ETH", "SOL"))
    assert btc["px"] == "58200.0" and abs(btc["usd"] - 90.0) < 0.01
    assert abs(float(eth["px"]) - 1760 * 58200 / 62000) < 0.02 and abs(eth["usd"] - 37.5) < 0.05     # the same % below its price
    assert abs(float(sol["px"]) - 72 * 58200 / 62000) < 0.002 and abs(sol["usd"] - 22.5) < 0.05
    assert abs(sum(x["usd"] for x in o) - 1051) < 0.5 and len({x["cl_id"] for x in o}) == len(o) and all(len(x["cl_id"]) <= 32 for x in o)
    tiny = L.plan({"id": "t1", "levels": [[58000, 16]], "split": {"BTC": 0.6, "ETH": 0.25, "SOL": 0.15}}, px, inst)
    assert [x["ccy"] for x in tiny] == ["BTC"] and abs(tiny[0]["usd"] - 16) < 0.01                    # $2.4 SOL / $4 ETH parts → BTC
    above = L.plan({"id": "t2", "levels": [[63000, 100]], "split": {"BTC": 1.0}}, px, inst)
    assert float(above[0]["px"]) < 62000                                                               # never a taker order


def test_place_fill_log_hold_futures_and_expire():
    cl, st, logs = Fake(), {}, []
    r = L.step(cl, st, CFG, NOW, logs.append)
    assert r["placed"] == len(st["ladder"]["orders"]) == 18 and L.resting(st) and L.fut_hold(st, CFG) == 500
    oid = next(k for k, b in cl.orders.items() if b["instId"] == "BTC-USDT" and b["px"] == "58200.0")
    cl.fills[oid] = float(cl.orders[oid]["sz"])
    L.step(cl, st, CFG, NOW + 300_000, logs.append)
    ev = [e for e in logs if e.get("ev") == "order"]
    assert len(ev) == 1 and ev[0]["side"] == "buy" and ev[0]["ccy"] == "BTC" and ev[0]["usd"] > 89      # public_real: a neutral spot buy
    assert abs(L.held(st)["BTC"] - float(cl.orders[oid]["sz"])) < 1e-12
    # the futures budget lends $500 while orders rest, never below $600
    import cnq.execution.okx_autopilot as ap
    d = tempfile.mkdtemp()
    env = {"OKX_AUTOPILOT_STATE": os.path.join(d, "s.json")}
    ap.save_state(st, env)
    assert fu.ladder_hold({"usd": 1500.0}, env) == {"usd": 1000.0, "ladder_usd": 500.0}
    assert fu.ladder_hold({"usd": 900.0}, env) == {"usd": 600.0, "ladder_usd": 300.0}
    assert fu.ladder_hold({"usd": 550.0}, env) == {"usd": 550.0}
    sm = L.summary(st)
    assert sm["filled_n"] == 1 and sm["open"] == 17 and sm["status"] == "open"
    r = L.step(cl, st, CFG, NOW + 14 * L.DAY + 1, logs.append)                                       # 14 days → the rest is cancelled
    assert r["cancelled"] == 17 and st["ladder"]["status"] == "expired" and not L.resting(st) and L.fut_hold(st, CFG) == 0
    assert L.held(st)["BTC"] > 0                                                                       # the filled BTC stays core
    assert L.step(cl, st, CFG, NOW + 15 * L.DAY, logs.append)["placed"] == 0                         # the same id is never re-placed


def test_no_free_cash_or_a_refusal_is_retried_then_reported_and_the_kill_switch_cancels():
    cl, st, logs = Fake(free=400.0, reject=("SOL-USDT",)), {}, []
    for k in range(3):
        L.step(cl, st, CFG, NOW + k * 300_000, logs.append)
    o = st["ladder"]["orders"]
    assert sum(x["usd"] for x in o if x["state"] == "live") <= 400.0 + 1e-6
    assert all(x["state"] in ("live", "new") for x in o) and any("мало свободных" in (x.get("why") or "") for x in o if x["state"] == "new")
    cl2, st2 = Fake(free=5000.0, reject=("SOL-USDT",)), {}
    for k in range(3):
        L.step(cl2, st2, CFG, NOW + k * 300_000, logs.append)
    o2 = st2["ladder"]["orders"]
    assert all(x["state"] == "failed" and "51008" in x["why"] for x in o2 if x["ccy"] == "SOL") and L.summary(st2)["failed"] == 6
    n = L.cancel_all(cl, st, logs.append)
    assert n == sum(1 for x in o if x["state"] == "canceled") and st["ladder"]["status"] == "stopped" and not L.resting(st)
    src = open(os.path.join(ROOT, "cnq", "execution", "okx_autopilot.py"), encoding="utf-8").read()
    i = src.index("def stop(")
    assert src.index("okx_ladder.cancel_all", i) < src.index("def resume(", i)                     # the kill switch includes the ladder
    j = src.index("def build(")
    assert src.index("okx_ladder.held(load_state(env))", j) < src.index("avail = trading_avail(raw)", j)   # never counted or sold


def test_owner_health_shows_the_ladder_only_on_the_owners_server_copy():
    from cnq.agents import owner_health as OH
    cl, st = Fake(), {}
    L.step(cl, st, CFG, NOW, lambda e: None)
    d = OH.with_ladder({"rows": [{"key": "autopilot"}]}, st)
    r = next(x for x in d["rows"] if x["key"] == "ladder")
    assert r["status"] == OH.GREEN and "открыто 18" in r["note"] and "58 200" in r["note"] and r["ladder"]["planned_usd"] > 1050
    assert OH.with_ladder({"rows": []}, {}) == {"rows": []}
    srv = open(os.path.join(ROOT, "cnq", "api", "server.py"), encoding="utf-8").read()
    assert "with_ladder(d, okx_autopilot.load_state())" in srv


def test_public_real_shows_only_a_neutral_count_of_resting_orders():
    import cnq.execution.okx_autopilot as ap
    from cnq.execution import public_real as pr
    cl, st = Fake(), {}
    L.step(cl, st, CFG, NOW, lambda e: None)
    d = tempfile.mkdtemp()
    env = {"OKX_AUTOPILOT_STATE": os.path.join(d, "s.json")}
    ap.save_state(st, env)
    so = pr.spot_orders(env)
    assert so == {"open": 18, "filled": 0, "since": NOW}
    out = pr.clean({**{k: None for k in pr.KEYS}, "closed": [], "fut_curve": [], "actions": [], "flows": {}, "copy_skips": [],
                    "spot_stops": [], "spot_orders": so})
    assert out["spot_orders"] == so and "58200" not in json.dumps(out) and "58 200" not in json.dumps(out)
