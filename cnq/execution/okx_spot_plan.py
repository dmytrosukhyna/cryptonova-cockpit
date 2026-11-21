"""The owner's spot deployment plan of 21.11.2026 (08:57 Athens, delegated: «анализируй и действуй», his last money — careful, DCA,
quality only). config/owner_overrides.json «spot_plan_1121»:

  {"id", "usdt_floor_pct": 0.10,           — USDT (the whole account's) never goes under 10 % of the equity because of this plan;
                                             every leg is sized from what is above that floor at the moment it is placed
                                             (minus our own resting buys), and shrinks — it never sells core to fit
   "now": {"id", "usd": {SYM: $}, "max_share": 0.5, "stop_pct": {SYM: -0.20}}
                                           — bought now, ≤ max_share of the money above the floor;
   "breakout": {"id": "breakout60k_1121", "level": 60000, "bar": "4H", "split": {BTC, ETH, SOL}, "t1_share": 0.5,
                "t2_share": 1.0, "retest_band": 0.01, "until"}
                                           — tranche 1 when a 4h candle CLOSES above level (OKX BTC-USDT candles, confirmed bars
                                             only): t1_share of the money above the floor; tranche 2 on a retest that holds: a later
                                             confirmed 4h candle whose low comes within retest_band of the level and closes above it:
                                             t2_share of what is above the floor then. Nothing after «until».
   "fallback_min": 15, "max_slip": 0.003}  — every order: post-only at the best bid; what is not filled after fallback_min minutes
                                             is cancelled and bought with an IOC limit ≤ max_slip over the ask (as the core buy).
The coins bought here are never counted, sold or trimmed by the rebalancer (okx_ladder.held → okx_autopilot.build); a non-core coin
gets an exchange stop at its average fill × (1 + stop_pct) (okx_spot_stops via okx_autopilot.spot_stops). Fills are logged as
ordinary spot buys (public_real actions «spot_buy»). State: autopilot state «spot_plan». Stdlib only."""
from __future__ import annotations

import time

from . import myspot_watch as ms
from .okx_ladder import LIVE, MIN_USD, ceil_to, fmt, ms_floor, place

KEY = "spot_plan_1121"
CORE = ("BTC", "ETH", "SOL")


def cfg(ov: dict | None) -> dict | None:
    c = (ov or {}).get(KEY)
    return c if isinstance(c, dict) and c.get("id") else None


def _until(v) -> int:
    from .owner_overrides import _ms
    return _ms(v)


def orders(st: dict | None) -> list:
    return [o for L in (((st or {}).get("spot_plan") or {}).get("legs") or {}).values() for o in (L.get("orders") or [])]


def committed(st: dict | None) -> float:
    """$ of our own buys still resting (their USDT is frozen on OKX but still in the account's USDT)"""
    return sum(max(0.0, float(o.get("usd") or 0) - (o.get("filled") or 0) * float(o.get("avg_px") or o.get("px") or 0))
               for o in orders(st) if o.get("state") in LIVE)


def above_floor(usdt: float, equity: float, c: dict, st: dict | None, other_resting: float = 0.0) -> float:
    return max(0.0, float(usdt) - float(c.get("usdt_floor_pct") or 0.10) * float(equity) - committed(st) - float(other_resting or 0.0))


def stops(st: dict | None, ov: dict | None) -> dict:
    """{coin: stop px} for the filled non-core coins of the plan"""
    c = cfg(ov) or {}
    sp = {k.upper(): float(v) for k, v in (((c.get("now") or {}).get("stop_pct")) or {}).items()}
    out = {}
    for o in orders(st):
        if o["ccy"] in CORE or not (o.get("filled") or 0) or not o.get("avg_px"):
            continue
        p = sp.get(o["ccy"], -0.20)
        if -0.5 < p < 0:
            out[o["ccy"]] = float(o["avg_px"]) * (1 + p)
    return out


def _leg(cl, parts: dict, lid: str, now: int, quote: str, why: str) -> dict:
    """a leg {id, t, why, orders} — parts {coin: $}; a core coin too small for OKX goes to BTC, another one is skipped"""
    info, skipped = {}, []
    for coin in list(parts):
        iid = f"{coin}-{quote}"
        i, tk = cl.instrument(iid), cl.ticker(iid)
        bid = ms._f((tk or {}).get("bidPx")) or ms._f((tk or {}).get("last"))
        if not i or not bid or parts[coin] < MIN_USD or parts[coin] / bid < float(i["minSz"]):
            u = parts.pop(coin)
            if coin in CORE and coin != "BTC":
                parts["BTC"] = parts.get("BTC", 0.0) + u
            else:
                skipped.append(coin)
            continue
        info[coin] = (iid, i, bid)
    if "BTC" in parts and "BTC" not in info:
        iid = f"BTC-{quote}"
        i, tk = cl.instrument(iid), cl.ticker(iid)
        bid = ms._f((tk or {}).get("bidPx")) or ms._f((tk or {}).get("last"))
        if i and bid and parts["BTC"] >= MIN_USD and parts["BTC"] / bid >= float(i["minSz"]):
            info["BTC"] = (iid, i, bid)
    out = []
    for coin, u in parts.items():
        if coin not in info:
            continue
        iid, i, bid = info[coin]
        px = ms_floor(bid, float(i["tickSz"]))
        sz = ms_floor(u / px, float(i["lotSz"]))
        if sz >= float(i["minSz"]):
            out.append({"ccy": coin, "inst": iid, "px": fmt(px, i["tickSz"]), "sz": fmt(sz, i["lotSz"]), "usd": round(u, 2),
                        "lot": i["lotSz"], "tick": i["tickSz"], "min": i["minSz"], "cl_id": f"cnqsp{lid}{coin}"[:32],
                        "state": "new", "phase": "maker", "filled": 0.0, "avg_px": None, "tries": 0})
    return {"id": lid, "t": now, "why": why, "status": "open", "orders": out, "skipped": skipped}


def _candles(cl, bar: str = "4H", n: int = 6) -> list:
    """confirmed BTC-USDT candles, oldest first: [(ts, open, high, low, close)]"""
    d = cl.call("GET", "/api/v5/market/candles", {"instId": "BTC-USDT", "bar": bar, "limit": str(n + 1)}, signed=False) or []
    out = []
    for k in d:
        if len(k) >= 9 and str(k[8]) != "1":
            continue
        out.append((int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])))
    return sorted(out)


def _track(cl, L: dict, c: dict, now: int, log, quote: str, res: dict) -> None:
    """the core-buy mechanics for one leg: place post-only, record fills, after fallback_min the rest as IOC ≤ max_slip"""
    free = None
    for o in L["orders"]:
        if o["state"] == "new" and o["tries"] < 3:
            if free is None:
                free = cl.avail(quote)
            if free < o["usd"]:
                o["why"], o["tries"] = f"мало свободных {quote}: {free:.2f} < {o['usd']:.2f}", o["tries"] + 1
                continue
            try:
                r = place(cl, o)
                o.update(state="live", ord_id=r.get("ordId"), placed_t=now)
                free -= o["usd"]
                res["placed"] += 1
                log({"ev": "spot_plan_order", "id": L["id"], "ccy": o["ccy"], "inst": o["inst"], "px": o["px"], "sz": o["sz"], "usd": o["usd"]})
            except Exception as e:  # noqa: BLE001
                o["tries"] += 1
                o["why"] = f"OKX: {getattr(e, 'code', '') or type(e).__name__}"
                res["errors"].append(f"{o['ccy']}: {o['why']}")
    for o in L["orders"]:
        if o["state"] in LIVE:
            s = cl.order_state(o["inst"], ord_id=o.get("ord_id"))
            if s:
                fsz, apx = ms._f(s.get("accFillSz")) or 0.0, ms._f(s.get("avgPx")) or 0.0
                new = fsz - (o.get("maker_filled") or 0.0)
                if new > 0:
                    log({"ev": "order", "src": "spot_plan", "ccy": o["ccy"], "inst": o["inst"], "side": "buy", "filled": round(new, 10),
                         "avg_px": apx, "usd": round(new * apx, 2), "id": None})
                    res["filled"] += 1
                o["maker_filled"], o["maker_px"], o["filled"] = fsz, apx or o.get("maker_px"), fsz
                o["avg_px"] = apx or o.get("avg_px")
                o["state"] = s.get("state") or o["state"]
        late = now - int(o.get("placed_t") or now) >= int(c.get("fallback_min") or 15) * 60_000
        if o["state"] in LIVE and late:
            try:
                cl.cancel(o["inst"], o["ord_id"])
            except Exception:  # noqa: BLE001
                pass
            s = cl.order_state(o["inst"], ord_id=o.get("ord_id")) or {}
            got = ms._f(s.get("accFillSz")) or o.get("filled") or 0.0
            o["filled"] = got
            rest = o["usd"] - got * (ms._f(s.get("avgPx")) or float(o["px"]))
            tk = cl.ticker(o["inst"])
            ask = ms._f((tk or {}).get("askPx")) or ms._f((tk or {}).get("last"))
            o["state"] = "canceled"
            if ask and rest >= MIN_USD:
                px = ceil_to(ask * (1 + float(c.get("max_slip") or 0.003)), float(o["tick"]))
                sz = ms_floor(rest / ask, float(o["lot"]))
                if sz >= float(o["min"]):
                    try:
                        oid = cl.order(o["inst"], "buy", fmt(px, o["tick"]), fmt(sz, o["lot"]), f"cnqspt{L['id']}{o['ccy']}"[:32]).get("ordId")
                        s2 = {}
                        for _ in range(6):
                            s2 = cl.order_state(o["inst"], ord_id=oid) or {}
                            if s2.get("state") not in LIVE + (None,):
                                break
                            time.sleep(0.4)
                        f2, p2 = ms._f(s2.get("accFillSz")) or 0.0, ms._f(s2.get("avgPx")) or 0.0
                        if f2 > 0:
                            log({"ev": "order", "src": "spot_plan", "ccy": o["ccy"], "inst": o["inst"], "side": "buy", "filled": round(f2, 10),
                                 "avg_px": p2, "usd": round(f2 * p2, 2), "id": None})
                            res["filled"] += 1
                        tot = got * (o.get("maker_px") or 0.0) + f2 * p2
                        o["filled"] = got + f2
                        o["avg_px"] = tot / o["filled"] if o["filled"] else None
                        o["phase"] = "taker"
                    except Exception as e:  # noqa: BLE001
                        o["why"] = f"OKX IOC: {getattr(e, 'code', '') or type(e).__name__}"
                        res["errors"].append(o["why"])
            o["state"] = "filled" if o["filled"] > 0 else "canceled"
    if all(o["state"] not in LIVE + ("new",) or o["tries"] >= 3 for o in L["orders"]):
        if L.get("status") == "open":
            L["status"] = "done"
            log({"ev": "spot_plan_leg_done", "id": L["id"], "usd": round(sum((o["filled"] or 0) * (o["avg_px"] or 0) for o in L["orders"]), 2)})


def step(cl, st: dict, ov: dict, now: int, log, quote: str = "USDT", usdt: float = 0.0, equity: float = 0.0,
         other_resting: float = 0.0) -> dict:
    res = {"placed": 0, "filled": 0, "errors": [], "new_legs": []}
    c = cfg(ov)
    S = st.get("spot_plan") or {}
    if c and S.get("id") != c["id"]:
        S = {"id": c["id"], "legs": {}}
        st["spot_plan"] = S
    if not S:
        return res
    legs = S.setdefault("legs", {})
    if c:
        free = above_floor(usdt, equity, c, st, other_resting)
        nw = c.get("now") or {}
        if nw.get("id") and nw["id"] not in legs:
            want = {k.upper(): float(v) for k, v in (nw.get("usd") or {}).items() if float(v) > 0}
            cap = float(nw.get("max_share") or 0.5) * free
            k = min(1.0, cap / sum(want.values())) if want else 0.0
            parts = {s: u * k for s, u in want.items()}
            legs[nw["id"]] = L = _leg(cl, parts, nw["id"], now, quote, f"покупка сейчас: ≤ {nw.get('max_share', 0.5):g} денег выше пола USDT ({free:.2f})")
            L["free"], L["scale"] = round(free, 2), round(k, 4)
            log({"ev": "spot_plan_leg", "id": nw["id"], "free": round(free, 2), "usd": round(sum(parts.values()), 2), "orders": len(L["orders"])})
            res["new_legs"].append(nw["id"])
            free = above_floor(usdt, equity, c, st, other_resting) - sum(o["usd"] for o in L["orders"])
        bk = c.get("breakout") or {}
        bid_ = bk.get("id")
        if bid_ and (not bk.get("until") or now < _until(bk["until"])) and (f"{bid_}:t1" not in legs or f"{bid_}:t2" not in legs):
            try:
                cs = _candles(cl, bk.get("bar") or "4H")
            except Exception:  # noqa: BLE001
                cs = []
            lvl, band = float(bk.get("level") or 60000), float(bk.get("retest_band") or 0.01)
            split = {k.upper(): float(v) for k, v in (bk.get("split") or {"BTC": 0.6, "ETH": 0.25, "SOL": 0.15}).items()}
            if cs and f"{bid_}:t1" not in legs and cs[-1][4] > lvl:
                u = float(bk.get("t1_share") or 0.5) * max(0.0, free)
                L = _leg(cl, {s: u * f for s, f in split.items()}, f"{bid_}:t1", now, quote,
                         f"пробой: свеча {bk.get('bar') or '4H'} закрылась {cs[-1][4]:.0f} > {lvl:.0f}")
                L["bar_t"] = cs[-1][0]
                legs[f"{bid_}:t1"] = L
                log({"ev": "spot_plan_leg", "id": L["id"], "free": round(free, 2), "usd": round(u, 2), "orders": len(L["orders"]), "close": cs[-1][4]})
                res["new_legs"].append(L["id"])
            elif cs and f"{bid_}:t1" in legs and f"{bid_}:t2" not in legs:
                t1 = int(legs[f"{bid_}:t1"].get("bar_t") or 0)
                k_ = next((k for k in cs if k[0] > t1 and k[3] <= lvl * (1 + band) and k[4] >= lvl), None)
                if k_:
                    u = float(bk.get("t2_share") or 1.0) * max(0.0, free)
                    L = _leg(cl, {s: u * f for s, f in split.items()}, f"{bid_}:t2", now, quote,
                             f"ретест удержан: минимум {k_[3]:.0f} у уровня {lvl:.0f}, закрытие {k_[4]:.0f}")
                    legs[f"{bid_}:t2"] = L
                    log({"ev": "spot_plan_leg", "id": L["id"], "free": round(free, 2), "usd": round(u, 2), "orders": len(L["orders"]), "low": k_[3]})
                    res["new_legs"].append(L["id"])
    for L in legs.values():
        if L.get("status") == "open":
            _track(cl, L, c or {}, now, log, quote, res)
    return res


def summary(st: dict | None) -> dict | None:
    S = (st or {}).get("spot_plan")
    if not S:
        return None
    return {"id": S.get("id"), "legs": {k: {"status": L.get("status"), "why": L.get("why"),
                                            "bought": {o["ccy"]: round((o.get("filled") or 0) * (o.get("avg_px") or 0), 2) for o in L.get("orders") or []}}
                                        for k, L in (S.get("legs") or {}).items()}}
