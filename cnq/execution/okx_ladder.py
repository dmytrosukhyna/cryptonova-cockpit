"""Owner's ladder (20.11.2026 18:17, owner's explicit decision, REAL money): buy the spot core around the July BTC low ($57.8k) with
resting LIMIT orders — the free spot cash ($551) + $500 taken from the futures budget while the ladder works.

Config: config/owner_overrides.json «ladder» {id, set, days, levels [[BTC price, $], …], split {BTC, ETH, SOL}, fut_usd, fut_min}.
Once per ladder id (the autopilot's spot cycle, every 5 min, live + armed + the lease):
  • for each level: BTC — a post-only GTC limit buy AT the level; ETH and SOL — at the same % below their price now as the level is
    below BTC now (they may fill at other moments); split 60 / 25 / 15; a part under OKX's minimum (or $5) is merged into BTC;
  • an order OKX refuses is retried (3 tries), then reported; an order the free USDT does not cover yet waits (retried every
    cycle until the expiry) — nothing is placed past the USDT that is free right now.
Every cycle: the orders' fills are read; filled coins are core holdings — the rebalancer never counts, sells or trims them
(okx_autopilot.build treats them like the active sleeve's coins); no stop is put on them. After `days` (14) the open orders are
cancelled and their USDT is free again. While an order rests, the futures budget is reduced by fut_usd ($500), never below fut_min
($600) (okx_futures.budget). The kill switch (okx_autopilot.stop) cancels the ladder's open orders too.
Client order ids start with «cnqlad»; no other part of the autopilot cancels them. Fills are logged as ordinary spot buys
(ev «order», side «buy») — public_real shows them as neutral spot buys; the levels and open orders are owner-only (owner health)."""
from __future__ import annotations

import time

from . import myspot_watch as ms

PREFIX = "cnqlad"
DAY = 86_400_000
MIN_USD = 5.0
MAX_TRIES = 3
LIVE = ("live", "partially_filled")


def cfg(ov: dict | None) -> dict | None:
    c = (ov or {}).get("ladder")
    if not isinstance(c, dict) or not c.get("id") or not c.get("levels"):
        return None
    return c


def plan(c: dict, px: dict, inst: dict) -> list[dict]:
    """the orders of a ladder from the prices now. px = {BTC, ETH, SOL: last}; inst = {coin: {instId, lotSz, minSz, tickSz}}"""
    split = c.get("split") or {"BTC": 0.60, "ETH": 0.25, "SOL": 0.15}
    btc = float(px["BTC"])
    out = []
    for k, (level, usd) in enumerate(c["levels"]):
        level, usd = float(level), float(usd)
        f = min(level / btc, 0.999)                 # a level already above the price → a resting bid just under the price
        parts = {coin: usd * float(w) for coin, w in split.items()}
        for coin in [x for x in parts if x != "BTC"]:
            i, p = inst.get(coin), px.get(coin)
            lim = round(float(p) * f, 12) if p else None
            ok = i and lim and parts[coin] >= MIN_USD and ms_floor(parts[coin] / lim, float(i["lotSz"])) >= float(i["minSz"])
            if not ok:
                parts["BTC"] = parts.get("BTC", 0.0) + parts.pop(coin)      # too small / no pair → into BTC
        for coin, u in parts.items():
            i = inst[coin]
            lim = min(level, btc * 0.999) if coin == "BTC" else float(px[coin]) * f
            lim = ms_floor(lim, float(i["tickSz"]))
            sz = ms_floor(u / lim, float(i["lotSz"]))
            if sz < float(i["minSz"]):
                continue
            out.append({"lvl": k, "level": level, "ccy": coin, "inst": i["instId"], "px": fmt(lim, i["tickSz"]), "sz": fmt(sz, i["lotSz"]),
                        "usd": round(sz * lim, 2), "pct": round((f - 1) * 100, 2), "cl_id": f"{PREFIX}{c['id']}{k}{coin}"[:32],
                        "state": "new", "tries": 0, "filled": 0.0, "avg_px": None})
    return out


def ms_floor(x: float, step: float) -> float:
    from decimal import Decimal
    d = Decimal(str(step))
    return float((Decimal(str(x)) // d) * d)


def fmt(x: float, step: str) -> str:
    dec = len(str(step).split(".")[1].rstrip("0")) if "." in str(step) else 0
    return f"{x:.{dec}f}"


def held(st: dict | None) -> dict:
    """{coin: filled quantity} — core holdings bought by the ladder (never counted, sold or trimmed by the rebalancer)"""
    out = {}
    for o in ((st or {}).get("ladder") or {}).get("orders") or []:
        q = ms._f(o.get("filled")) or 0.0
        if q > 0:
            out[o["ccy"]] = out.get(o["ccy"], 0.0) + q
    return out


def resting(st: dict | None) -> bool:
    L = (st or {}).get("ladder") or {}
    return L.get("status") == "open" and any(o.get("state") in LIVE + ("new",) for o in L.get("orders") or [])


def fut_hold(st: dict | None, ov: dict | None = None) -> float:
    """$ taken from the futures budget now (0 when no order rests)"""
    c = cfg(ov) or ((st or {}).get("ladder") or {}).get("cfg") or {}
    return float(c.get("fut_usd") or 0.0) if resting(st) else 0.0


def place(cl, o: dict) -> dict:
    body = {"instId": o["inst"], "tdMode": "cash", "side": "buy", "ordType": "post_only", "px": o["px"], "sz": o["sz"], "clOrdId": o["cl_id"]}
    d = cl.call("POST", "/api/v5/trade/order", body=body)
    r = d[0] if d else {}
    if str(r.get("sCode", "0")) != "0":
        raise ms.OkxError(r.get("sCode"), r.get("sMsg") or "", "/api/v5/trade/order")
    return r


def step(cl, st: dict, ov: dict, now: int, log, quote: str = "USDT") -> dict:
    """one cycle. Returns {"placed", "filled", "cancelled", "errors"}; the state is st["ladder"]"""
    res = {"placed": 0, "filled": 0, "cancelled": 0, "errors": []}
    c = cfg(ov)
    L = st.get("ladder") or {}
    if c and L.get("id") != c["id"]:
        coins = ("BTC", "ETH", "SOL")
        px, inst = {}, {}
        for coin in coins:
            iid = f"{coin}-{quote}"
            i, tk = cl.instrument(iid), cl.ticker(iid)
            last = ms._f((tk or {}).get("last"))
            if i and last:
                inst[coin], px[coin] = {**i, "instId": iid}, last
        if "BTC" not in px:
            res["errors"].append("нет цены BTC — лестница не выставлена")
            return res
        days = int(c.get("days") or 14)
        L = {"id": c["id"], "cfg": c, "placed_t": now, "expires": now + days * DAY, "btc_at_place": px["BTC"], "px_at_place": px,
             "status": "open", "orders": plan(c, px, inst)}
        st["ladder"] = L
        log({"ev": "ladder_plan", "id": c["id"], "btc": px["BTC"], "orders": len(L["orders"]), "usd": round(sum(o["usd"] for o in L["orders"]), 2)})
    if not L:
        return res
    if L.get("status") == "open":
        free = None
        for o in L["orders"]:
            if o["state"] == "new" and o["tries"] < MAX_TRIES and now < L["expires"]:
                if free is None:
                    free = cl.avail(quote)
                if free < o["usd"] * 1.002:
                    o["why"] = f"мало свободных {quote}: {free:.2f} < {o['usd']:.2f}"     # retried every cycle until the expiry
                    continue
                try:
                    r = place(cl, o)
                    o.update(state="live", ord_id=r.get("ordId"), placed_t=now)
                    free -= o["usd"]
                    res["placed"] += 1
                    log({"ev": "ladder_order", "id": L["id"], "ccy": o["ccy"], "inst": o["inst"], "px": o["px"], "sz": o["sz"], "usd": o["usd"]})
                except Exception as e:  # noqa: BLE001
                    o["tries"] += 1
                    o["why"] = f"OKX: {getattr(e, 'code', '') or type(e).__name__}"
                    res["errors"].append(f"{o['ccy']} {o['px']}: {o['why']}")
                    if o["tries"] >= MAX_TRIES:
                        o["state"] = "failed"
        for o in L["orders"]:                                      # fills
            if o["state"] not in LIVE:
                continue
            s = cl.order_state(o["inst"], ord_id=o.get("ord_id")) if o.get("ord_id") else cl.order_state(o["inst"], cl_id=o["cl_id"])
            if not s:
                continue
            fsz, apx = ms._f(s.get("accFillSz")) or 0.0, ms._f(s.get("avgPx")) or 0.0
            new = fsz - (o.get("filled") or 0.0)
            if new > 0:
                log({"ev": "order", "src": "ladder", "ccy": o["ccy"], "inst": o["inst"], "side": "buy", "filled": round(new, 10),
                     "avg_px": apx, "usd": round(new * apx, 2), "id": None})
                res["filled"] += 1
            o.update(filled=fsz, avg_px=apx or o.get("avg_px"), state=s.get("state") or o["state"])
        if now >= L["expires"]:                                    # 14 days: what still rests is cancelled, its USDT is free again
            for o in L["orders"]:
                if o["state"] in LIVE:
                    try:
                        cl.cancel(o["inst"], o["ord_id"])
                        o["state"] = "canceled"
                        res["cancelled"] += 1
                    except Exception as e:  # noqa: BLE001
                        res["errors"].append(f"отмена {o['ccy']}: {type(e).__name__}")
                elif o["state"] == "new":
                    o["state"] = "expired"
            if not any(o["state"] in LIVE for o in L["orders"]):
                L["status"] = "expired"
                log({"ev": "ladder_expired", "id": L["id"], "cancelled": res["cancelled"]})
        elif L["orders"] and all(o["state"] in ("filled", "failed") for o in L["orders"]):
            L["status"] = "done"
            log({"ev": "ladder_done", "id": L["id"]})
    L["t"] = now
    return res


def cancel_all(cl, st: dict, log) -> int:
    """the kill switch: every resting ladder order is cancelled"""
    n = 0
    L = st.get("ladder") or {}
    for o in L.get("orders") or []:
        if o.get("state") in LIVE and o.get("ord_id"):
            try:
                cl.cancel(o["inst"], o["ord_id"])
                o["state"] = "canceled"
                n += 1
            except Exception:  # noqa: BLE001
                pass
    if n or L.get("status") == "open":
        L["status"] = "stopped"
        log({"ev": "ladder_stopped", "id": L.get("id"), "cancelled": n})
    return n


def summary(st: dict | None) -> dict | None:
    """the owner's view: levels, open / filled per order"""
    L = (st or {}).get("ladder")
    if not L:
        return None
    rows = [{"level": o["level"], "ccy": o["ccy"], "px": o["px"], "usd": o["usd"], "state": o["state"], "filled": o.get("filled"),
             "avg_px": o.get("avg_px"), "why": o.get("why")} for o in L.get("orders") or []]
    filled_usd = round(sum((o.get("filled") or 0) * (o.get("avg_px") or 0) for o in L.get("orders") or []), 2)
    return {"id": L.get("id"), "status": L.get("status"), "placed_t": L.get("placed_t"), "expires": L.get("expires"),
            "btc_at_place": L.get("btc_at_place"), "orders": rows, "open": sum(1 for o in rows if o["state"] in LIVE),
            "filled_n": sum(1 for o in rows if (o.get("filled") or 0) > 0), "filled_usd": filled_usd,
            "planned_usd": round(sum(o["usd"] for o in rows), 2), "failed": sum(1 for o in rows if o["state"] == "failed")}
