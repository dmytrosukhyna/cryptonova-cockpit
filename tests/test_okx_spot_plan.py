"""21.11 08:57 (owner delegated, his last money): the spot plan — a small buy now above a 10 % USDT floor, a 60k breakout leg
(4h close) and a retest leg, protected from the rebalancer, exchange stops for non-core coins. Fake OKX; made-up prices."""
import json
import os

import test_okx_ladder as TL
from cnq.execution import okx_ladder as L
from cnq.execution import okx_spot_plan as P

ROOT = TL.ROOT
NOW = TL.NOW
TL.INST.setdefault("TRX-USDT", {"lotSz": "0.000001", "minSz": "1", "tickSz": "0.0001"})


class Fake(TL.Fake):
    def __init__(self, candles=(), **kw):
        super().__init__(**kw)
        self.px.setdefault("TRX-USDT", 0.30)
        self.candles = list(candles)
        self.ioc = []

    def call(self, method, path, q=None, body=None, signed=True):
        if method == "GET" and path == "/api/v5/market/candles":
            return [[str(t), "0", str(h), str(lo), str(c), "0", "0", "0", "1"] for t, h, lo, c in reversed(self.candles)]
        body = {**body, "clOrdId": "cnqlad" + body["clOrdId"]} if body["clOrdId"].startswith("cnqsp") else body
        r = super().call(method, path, q, body)
        return r

    def order(self, iid, side, px, sz, cl_id):
        self.ioc.append((iid, px, sz, cl_id))
        oid = str(len(self.orders) + 1)
        self.orders[oid] = {"instId": iid, "px": px, "sz": sz, "clOrdId": cl_id}
        self.fills[oid] = float(sz)
        return {"ordId": oid}


CFG = {"spot_plan_1121": {"id": "plan1121", "usdt_floor_pct": 0.10, "fallback_min": 15, "max_slip": 0.003,
                          "now": {"id": "now1121", "usd": {"TRX": 90}, "max_share": 0.5, "stop_pct": {"TRX": -0.20}},
                          "breakout": {"id": "breakout60k_1121", "level": 60000, "bar": "4H", "split": {"BTC": 0.6, "ETH": 0.25, "SOL": 0.15},
                                       "t1_share": 0.5, "t2_share": 1.0, "retest_band": 0.01, "until": "2026-12-21T00:00:00+00:00"}}}


def test_the_live_config_keeps_the_hard_limits():
    c = json.load(open(os.path.join(ROOT, "config", "owner_overrides.json"), encoding="utf-8"))["spot_plan_1121"]
    assert c["usdt_floor_pct"] >= 0.10 and c["now"]["max_share"] <= 0.5 and c["breakout"]["id"] == "breakout60k_1121"
    assert c["breakout"]["level"] == 60000 and c["breakout"]["bar"] == "4H" and set(c["breakout"]["split"]) == {"BTC", "ETH", "SOL"}
    assert all(-0.5 < v < 0 for v in c["now"]["stop_pct"].values())
    assert set(c["now"]["usd"]) <= {"TRX", "XLM", "BONK"}


def test_now_leg_is_capped_above_the_floor_protected_and_stopped():
    fk, st, logs = Fake(free=2000.0), {}, []
    # equity $10 000, USDT $1 100 → $100 above the 10 % floor → the now leg ≤ $50 (not the $90 asked)
    P.step(fk, st, CFG, NOW, logs.append, "USDT", usdt=1100.0, equity=10000.0)
    (o,) = st["spot_plan"]["legs"]["now1121"]["orders"]
    assert o["ccy"] == "TRX" and abs(o["usd"] - 50.0) < 0.01 and o["state"] == "live" and o["cl_id"].startswith("cnqsp")
    assert not [x for x in st["spot_plan"]["legs"] if x.startswith("breakout")]          # no candles → no breakout leg
    oid = o["ord_id"]
    fk.fills[oid] = float(o["sz"])
    P.step(fk, st, CFG, NOW + 60_000, logs.append, "USDT", usdt=1050.0, equity=10000.0)
    assert o["state"] == "filled" and L.held(st)["TRX"] == float(o["sz"])                 # the rebalancer never counts it
    assert any(e.get("ev") == "order" and e.get("ccy") == "TRX" and e.get("side") == "buy" for e in logs)   # public «spot_buy»
    assert abs(P.stops(st, CFG)["TRX"] - float(o["avg_px"]) * 0.8) < 1e-9
    # under the floor: nothing at all
    st2 = {}
    P.step(Fake(), st2, CFG, NOW, logs.append, "USDT", usdt=900.0, equity=10000.0)
    assert st2["spot_plan"]["legs"]["now1121"]["orders"] == []


def test_breakout_on_a_4h_close_above_60k_then_a_retest_that_holds():
    H = 4 * 3_600_000
    fk = Fake(free=5000.0, candles=[(NOW - 2 * H, 59800, 59000, 59500), (NOW - H, 60500, 59400, 59900)])
    st, logs = {}, []
    P.step(fk, st, CFG, NOW, logs.append, "USDT", usdt=3000.0, equity=10000.0)
    assert "breakout60k_1121:t1" not in st["spot_plan"]["legs"]                          # wick above, close below: nothing
    fk.candles.append((NOW, 60600, 59900, 60200))
    P.step(fk, st, CFG, NOW + 60_000, logs.append, "USDT", usdt=3000.0, equity=10000.0)
    t1 = st["spot_plan"]["legs"]["breakout60k_1121:t1"]
    got = {o["ccy"]: o["usd"] for o in t1["orders"]}
    assert set(got) == {"BTC", "ETH", "SOL"} and abs(sum(got.values()) - 0.5 * (3000 - 1000 - P.committed({"spot_plan": {"legs": {
        k: v for k, v in st["spot_plan"]["legs"].items() if k == "now1121"}}}))) < 1.0
    assert "breakout60k_1121:t2" not in st["spot_plan"]["legs"]
    fk.candles.append((NOW + H, 61500, 60700, 61200))                                    # no retest yet
    P.step(fk, st, CFG, NOW + H + 60_000, logs.append, "USDT", usdt=2500.0, equity=10000.0)
    assert "breakout60k_1121:t2" not in st["spot_plan"]["legs"]
    fk.candles.append((NOW + 2 * H, 60800, 60150, 60400))                                # low within 1 %, close above → tranche 2
    P.step(fk, st, CFG, NOW + 2 * H + 60_000, logs.append, "USDT", usdt=2500.0, equity=10000.0)
    assert "breakout60k_1121:t2" in st["spot_plan"]["legs"]
    assert all(o["ccy"] in ("BTC", "ETH", "SOL") for o in st["spot_plan"]["legs"]["breakout60k_1121:t2"]["orders"])
    assert not P.stops(st, CFG).keys() - {"TRX"}                                          # the core gets no stop


def test_unfilled_maker_falls_back_to_ioc_within_the_slip():
    fk, st = Fake(free=2000.0), {}
    P.step(fk, st, CFG, NOW, lambda e: None, "USDT", usdt=1200.0, equity=10000.0)
    P.step(fk, st, CFG, NOW + 16 * 60_000, lambda e: None, "USDT", usdt=1200.0, equity=10000.0)
    (o,) = st["spot_plan"]["legs"]["now1121"]["orders"]
    assert fk.ioc and o["state"] == "filled" and o["phase"] == "taker" and float(fk.ioc[0][1]) <= 0.30 * 1.003 + 0.0001
