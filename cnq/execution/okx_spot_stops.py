"""Spot stops ON THE EXCHANGE (09.11.2026 22:12, owner: «есть ли на споте защитные ордера от резкого падения?»).

Before: the stops of «CRYPTO NOVA портфель» (medium −20 %, high-risk −35 % then a 35 % trail, smart-money −25 %) lived only in the
model — checked when the GitHub pipeline ran the spot step (~every 30 min) and carried out by the autopilot's next rebalance. A sharp
drop between two looks was not protected.

Now every non-core coin the model holds and the account holds gets ONE conditional sell on OKX (POST /api/v5/trade/order-algo,
ordType «conditional», tdMode cash, slTriggerPx = the model's stop, slOrdPx −1 = market, trigger on the last price), sized to the
coin's available quantity on the trading account. Each pass (the window's spot tick, ~5 min) keeps it in sync: a moved stop or a
changed size → cancel + place; a coin the model no longer holds (or that turned core) → cancelled. Ours carry algoClOrdId «cnqss…».
A stop above the price already (it would fire at once) is not placed — the model's own sell comes on its next run.
When one of ours disappears without our cancel (it fired), the coin is put on hold for HOLD_MS: the rebalance does not buy it back
while the model still lists it (okx_autopilot.build reads held_off). The core BTC / ETH / SOL has no stop by the model's design.
Every place / cancel / fire is logged (okx_autopilot.LOG → public_real.json actions «spot_stop_set / spot_stop_cancel / spot_stop_fired»).
Off with OKX_SPOT_STOPS=off. Stdlib only."""
from __future__ import annotations

import os
import time

CORE = {"BTC", "ETH", "SOL"}
PREFIX = "cnqss"
TOL_PX, TOL_SZ = 0.002, 0.05
HOLD_MS = 14 * 86_400_000              # = the model's cooldown after a sell (spot_profiles IDEAS cooldown_d)
TRAIL = 0.35                           # spot_profiles IDEAS «trail» — the high-risk remainder after ×2


def enabled(env=None) -> bool:
    e = os.environ if env is None else env
    return (e.get("OKX_SPOT_STOPS") or "on").strip().lower() not in ("off", "no", "0", "false")


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def wanted(pf: dict) -> dict:
    """{COIN: stop price} for the non-core positions of «CRYPTO NOVA портфель» (web/portfolio.json): the model's stop level, or for a
    high-risk position after its ×2 half-sale the trailing level (35 % under its high)"""
    out = {}
    for p in (((pf or {}).get("portfolios") or {}).get("ideas") or {}).get("positions") or []:
        s = str(p.get("symbol") or "").upper()
        if not s or s in CORE or p.get("bucket") == "core":
            continue
        stp = _f((p.get("levels") or {}).get("stop"))
        if not stp and p.get("fixes") and _f(p.get("high")):
            stp = _f(p.get("high")) * (1 - TRAIL)
        if stp > 0:
            out[s] = stp
    return out


def _fmt(x: float, step: str) -> str:
    from .okx_autopilot import fmt
    return fmt(x, step)


def last_of(rt: dict, cl, inst_id: str) -> float:
    return _f((rt.get("tk") or cl.ticker(inst_id) or {}).get("last"))


def pending(cl) -> list:
    try:
        return [a for a in cl.call("GET", "/api/v5/trade/orders-algo-pending", {"ordType": "conditional", "instType": "SPOT"}) or []
                if str(a.get("algoClOrdId") or "").startswith(PREFIX)]
    except Exception:  # noqa: BLE001
        return []


LEAD_ALGO = "/api/v5/copytrading/algo-order"


def sync(cl, pf: dict, st: dict, now: int, log, quote: str = "USDT", avail: dict | None = None, lead: dict | None = None) -> dict:
    """one pass → {"set": [...], "cancelled": [...], "fired": [...], "skipped": [...]}; st (autopilot state) keeps «spot_stops»
    {coin: {algoId, px, sz}} and «spot_hold» {coin: until ms}"""
    from .okx_autopilot import floor_to, route, via_rate
    res = {"set": [], "cancelled": [], "fired": [], "skipped": []}
    miss_prev = st.get("spot_stop_missing") or {}
    miss: dict = {}

    def skip(c, why):
        """11.11 (MORPHO / LIT held 35+ min with no stop and no trace): a wanted stop that is NOT on the exchange is never silent —
        logged once per reason («spot_stop_skip»), kept in the state («spot_stop_missing» {coin: {t, why}}) for public_real.json
        and the owner's health row"""
        res["skipped"].append({"ccy": c, "why": why})
        prev = miss_prev.get(c) or {}
        miss[c] = {"t": int(prev.get("t") or now), "why": why}
        if prev.get("why") != why:
            log({"ev": "spot_stop_skip", "ccy": c, "t": now, "why": why})
    memo: dict = {}
    want = wanted(pf)
    mine = {str(a.get("instId") or "").split("-")[0]: a for a in pending(cl)}
    known = st.setdefault("spot_stops", {})
    hold = {c: t for c, t in (st.get("spot_hold") or {}).items() if int(t) > now}
    # fired: a stop we placed is gone without our cancel
    lead = lead or {}
    for c, k in list(known.items()):
        if k.get("lead") and not k.get("algoId"):                    # a lead position's stop lives on its sub-positions
            if (lead.get(c) or {}).get("subs"):
                continue
            known.pop(c)
            if c in want and not (avail or {}).get(c):               # the sub-positions are gone and so is the coin: the stop fired
                hold[c] = now + HOLD_MS
                res["fired"].append(c)
                log({"ev": "spot_stop_fired", "ccy": c, "px": k.get("px"), "t": now,
                     "why": f"стоп {c} (лид-позиция) на OKX сработал ({k.get('px')}) — монету не докупаем {HOLD_MS // 86_400_000} дней"})
            continue
        if c not in mine:
            known.pop(c)
            if c in want:
                hold[c] = now + HOLD_MS
                res["fired"].append(c)
                log({"ev": "spot_stop_fired", "ccy": c, "px": k.get("px"), "t": now,
                     "why": f"стоп {c} на OKX сработал ({k.get('px')}) — монету не докупаем {HOLD_MS // 86_400_000} дней, пока модель её держит"})
    for c, a in mine.items():                                        # not wanted any more (sold by the model, became core)
        if c not in want:
            try:
                cl.call("POST", "/api/v5/trade/cancel-algos", body=[{"instId": a["instId"], "algoId": a["algoId"]}])
                known.pop(c, None)
                res["cancelled"].append(c)
                log({"ev": "spot_stop_cancel", "ccy": c, "why": "модель больше не держит монету (или она стала ядром)"})
            except Exception as e:  # noqa: BLE001
                res["skipped"].append({"ccy": c, "why": f"отмена не прошла: {str(e)[:100]}"})
    have = avail if avail is not None else {}
    for c, px in sorted(want.items()):
        try:
            # 11.11: the pair the autopilot really trades the coin on — {COIN}-{quote}, or {COIN}-USDC for a coin with no USDT pair
            # (the buy goes through USDC; the stop looked only at {COIN}-USDT, found nothing and said nothing)
            rt = route(cl, c, quote, memo)
            inst_id, info = rt["inst"], rt["info"]
            if not info:
                skip(c, f"на OKX нет пары {rt.get('none') or inst_id}")
                continue
            k = via_rate(cl, memo) if rt.get("via") else 1.0             # dollars per unit of the pair's quote
            tick, lot, mn = info["tickSz"], info["lotSz"], _f(info.get("minSz"))
            have_q = have.get(c, 0.0) if avail is not None else cl.avail(c)
            # 11.11 (the real cause for MORPHO / LIT: «available balance is insufficient»): on a spot LEAD-trader account a coin
            # bought on a copy-enabled pair sits in lead sub-positions — an ordinary conditional order cannot sell it. Its stop is
            # set on every sub-position (POST /api/v5/copytrading/algo-order, instType SPOT); what is left outside them gets the
            # ordinary stop below.
            subs = [x for x in ((lead.get(c) or {}).get("subs") or []) if x and x[0]]
            if subs:
                kl = dict((known.get(c) or {}).get("lead") or {})
                done, lq = {}, 0.0
                for sid, s_inst, sq in subs:
                    s_info = cl.instrument(s_inst) or info
                    s_k = via_rate(cl, memo) if s_inst.endswith("-USDC") and quote == "USDT" else 1.0
                    s_trig = _fmt(px / s_k, s_info["tickSz"])
                    lq += _f(sq)
                    if kl.get(sid) and abs(_f(kl[sid]) / float(s_trig) - 1) <= TOL_PX:
                        done[sid] = kl[sid]
                        continue
                    cl.call("POST", LEAD_ALGO, body={"instType": "SPOT", "subPosId": sid, "slTriggerPx": s_trig, "slTriggerPxType": "last"})
                    done[sid] = float(s_trig)
                    res["set"].append({"ccy": c, "px": float(s_trig), "sz": _f(sq)})
                    log({"ev": "spot_stop_set", "ccy": c, "px": float(s_trig), "sz": _f(sq), "usd": round(_f(sq) * (last_of(rt, cl, inst_id) or px), 2),
                         "t": now, "why": f"стоп-лосс модели на лид-позиции {s_inst}: продать {_f(sq):g} {c} по рынку, если цена ≤ {s_trig}"})
                known[c] = {**{k_: v for k_, v in (known.get(c) or {}).items() if k_ in ("algoId", "sz", "inst")},
                            "lead": done, "px": next(iter(done.values()))}
                have_q = max(0.0, have_q - lq)
                if floor_to(have_q, float(lot)) < mn:
                    continue                                            # everything is in lead positions — done
            qty = floor_to(have_q, float(lot))
            last = _f((rt.get("tk") or cl.ticker(inst_id) or {}).get("last"))
            px_q = px / k
            a = mine.get(c)
            if a and str(a.get("instId")) != inst_id:                    # ours on another pair of this coin → replaced below
                cl.call("POST", "/api/v5/trade/cancel-algos", body=[{"instId": a["instId"], "algoId": a["algoId"]}])
                a = None
            if qty < mn or qty * (last or px_q) * k < 1.0:
                if a:
                    cl.call("POST", "/api/v5/trade/cancel-algos", body=[{"instId": inst_id, "algoId": a["algoId"]}])
                    known.pop(c, None)
                    res["cancelled"].append(c)
                    log({"ev": "spot_stop_cancel", "ccy": c, "why": "на торговом счёте монеты меньше минимума OKX"})
                elif have_q > 0:
                    skip(c, f"на торговом счёте {have_q:g} {c} — меньше минимального ордера OKX ({mn:g})")
                else:
                    skip(c, f"модель держит {c}, а на торговом счёте OKX монеты нет (ещё не куплена, на Funding / Earn или в позиции лид-трейдера)")
                continue
            if last and last <= px_q:
                skip(c, f"цена {last} уже ниже стопа {px_q:.6g} — продаст модель на своём ходе")
                continue
            trig = _fmt(px_q, tick)
            if a and abs(_f(a.get("slTriggerPx")) / float(trig) - 1) <= TOL_PX and abs(_f(a.get("sz")) / qty - 1) <= TOL_SZ:
                known[c] = {**({"lead": known[c]["lead"]} if (known.get(c) or {}).get("lead") else {}),
                            "algoId": a["algoId"], "px": float(trig), "sz": _f(a.get("sz")), "inst": inst_id}
                continue
            if a:
                cl.call("POST", "/api/v5/trade/cancel-algos", body=[{"instId": inst_id, "algoId": a["algoId"]}])
                log({"ev": "spot_stop_cancel", "ccy": c, "why": f"стоп переставляется: {a.get('slTriggerPx')} → {trig}, размер → {qty:g}"})
            body = {"instId": inst_id, "tdMode": "cash", "side": "sell", "ordType": "conditional", "sz": _fmt(qty, lot),
                    "slTriggerPx": trig, "slOrdPx": "-1", "slTriggerPxType": "last", "algoClOrdId": f"{PREFIX}{c.lower()[:8]}{now // 1000}"[:32]}
            d = cl.call("POST", "/api/v5/trade/order-algo", body=body)
            aid = (d[0] if d else {}).get("algoId")
            known[c] = {**({"lead": known[c]["lead"]} if (known.get(c) or {}).get("lead") else {}),
                        "algoId": aid, "px": float(trig), "sz": qty, "inst": inst_id}
            res["set"].append({"ccy": c, "px": float(trig), "sz": qty})
            log({"ev": "spot_stop_set", "ccy": c, "px": float(trig), "sz": qty, "usd": round(qty * (last or px_q) * k, 2), "t": now,
                 "why": f"стоп-лосс модели на бирже ({inst_id}): продать {qty:g} {c} по рынку, если цена ≤ {trig}"})
        except Exception as e:  # noqa: BLE001 — one coin's failure never leaves the others without a stop
            known.pop(c, None) if c not in mine else None
            skip(c, f"стоп не поставился: {str(e)[:260]}")
            log({"ev": "spot_stop_error", "ccy": c, "t": now, "error": str(e)[:200]})
    st["spot_stop_missing"] = miss
    st["spot_hold"] = hold
    st["spot_stops_t"] = now
    return res


def release(cl, coins, st: dict, log, quote: str = "USDT") -> list:
    """before the rebalance sells a coin: its stop is cancelled first (whether or not OKX reserves the coin for it), the next sync
    re-places it on what is left"""
    out = []
    mine = {str(a.get("instId") or "").split("-")[0]: a for a in pending(cl)}
    for c in coins:
        a = mine.get(c)
        if not a:
            continue
        cl.call("POST", "/api/v5/trade/cancel-algos", body=[{"instId": a["instId"], "algoId": a["algoId"]}])
        (st.get("spot_stops") or {}).pop(c, None)
        log({"ev": "spot_stop_cancel", "ccy": c, "why": "ребаланс продаёт монету — стоп снят и будет поставлен заново на остаток"})
        out.append(c)
    return out


def totals(raw: dict) -> dict:
    """{CCY: total quantity on the trading account} (cashBal — what a stop must cover even if part of it is reserved)"""
    out = {}
    for c in (raw or {}).get("trading") or []:
        q = _f(c.get("cashBal")) or _f(c.get("eq")) or _f(c.get("availBal"))
        if q > 0:
            out[str(c.get("ccy")).upper()] = q
    return out


def held_off(st: dict, now: int | None = None) -> set:
    """coins whose exchange stop fired recently — the rebalance does not buy them back while the model still lists them"""
    now = int(now or time.time() * 1000)
    return {c for c, t in ((st or {}).get("spot_hold") or {}).items() if int(t) > now}
