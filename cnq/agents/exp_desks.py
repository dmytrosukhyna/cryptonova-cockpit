"""Experiment desks ON PAPER (owner 22.11.2026 16:18: «continuous, bold search for a better strategy — experiments on paper»).

Each variant is a paper copy of the SAME whale copy stream the real desk follows (web/whales_live.json desk: every copy it opens), so
the variants are apples to apples with the real source. Forward-logged from 22.11; owner-only (<run>/data/exp_desks), never real.
  base      the stream as it is (stop −16 % from our entry, exit when the desk's copy closes)
  a_regime  no alt SHORT while the last completed BTC 4h close > EMA20 and EMA20 rising; no alt LONG while < EMA20 and falling
  b_smart   no SHORT on a coin our smart-money radar (web/smart_money.json) shows ≥ 2 smart wallets accumulating (funds / market makers
            included) in the last 7 days; no LONG on a coin they are selling
  c_be1r    stop to the entry after +1R (R = 16 %);   c_trail  + a trailing stop 1.5 × ATR(14, 1h) from the best price
  c_time48  exit after 48 h;   c_stop8  stop −8 %
  d_major2  only when ≥ 2 followed whales hold the coin on the same side (the desk's own open copies)
  e_majors  only BTC / ETH / SOL;   e_btc  only BTC
  f_level   (owner 26.11 «сверять сделки китов с уровнями») only when the copy trades FROM a level: LONG with price − nearest support
            ≤ 1 ATR(14, 1h), SHORT with nearest resistance − price ≤ 1 ATR (levels of cnq/agents/levels_desk.py at the last closed 1h bar)
  f_qm      only when a same-side Quasimodo (1h swings, cnq/agents/qm_desk.py) broke in the last 72 1h bars
            (both: filters F1 / F3 of scripts/research/levels_qm_study.py part B — better in both halves there, not yet significant;
            no level data for the coin → that variant does not take the copy)
Prices: the desk's own marks (updated every desk pass, minutes apart) — stops are checked at those marks, so a fast wick between two
marks is missed (the same for every variant). Each paper trade = $1 000 notional, 0.11 % costs a round trip; results in % of notional.
Promotion (owner 22.11): a variant is proposed for real only after ≥ 30 forward trades AND a better PF and drawdown than the real
source — then the owner is asked with the numbers; nothing here touches the real account. Stdlib only."""
from __future__ import annotations

import json
import os
import time
import urllib.request

VARIANTS = ("base", "a_regime", "b_smart", "c_be1r", "c_trail", "c_time48", "c_stop8", "d_major2", "e_majors", "e_btc", "f_level", "f_qm")
NAMES_RU = {"base": "Поток китов как есть", "a_regime": "Фильтр режима BTC (4ч EMA20)", "b_smart": "Вето смарт-мани",
            "c_be1r": "Стоп в безубыток после +1R", "c_trail": "Трейлинг 1,5 × ATR", "c_time48": "Выход через 48 ч",
            "c_stop8": "Стоп −8 %", "d_major2": "≥ 2 кита на одной стороне", "e_majors": "Только BTC / ETH / SOL", "e_btc": "Только BTC",
            "f_level": "Кит от уровня", "f_qm": "Кит + квазимодо за"}
STOP, STOP8, R_BE, TIME_H, ATR_K = 16.0, 8.0, 16.0, 48, 1.5
COST, NOTIONAL = 0.11, 1000.0
MAJORS = {"BTC", "ETH", "SOL"}
H, H4 = 3_600_000, 4 * 3_600_000
OKX = "https://www.okx.com/api/v5/market/candles"


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-exp"}), timeout=15) as r:
        return json.loads(r.read())


def candles(inst: str, bar: str, n: int = 60, get=None) -> list:
    """confirmed OKX candles oldest first [(t, o, h, l, c)]"""
    d = (get or _get)(f"{OKX}?instId={inst}&bar={bar}&limit={n}").get("data") or []
    return sorted((int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in d if len(k) < 9 or str(k[8]) == "1")


def regime(c4: list) -> tuple | None:
    """(close, ema20, ema20 a bar before) of the last completed BTC 4h bar"""
    if len(c4) < 22:
        return None
    e, prev = None, None
    for _t, _o, _h, _l, c in c4:
        prev = e
        e = c if e is None else e + (c - e) * 2 / 21
    return c4[-1][4], e, prev


def regime_ok(rg, coin: str, side: str) -> bool:
    if coin == "BTC" or not rg or rg[2] is None:
        return True
    c, e, pe = rg
    return not ((side == "SHORT" and c > e and e > pe) or (side == "LONG" and c < e and e < pe))


def atr(c1: list, n: int = 14) -> float | None:
    if len(c1) < n + 1:
        return None
    trs = [max(c1[i][2], c1[i - 1][4]) - min(c1[i][3], c1[i - 1][4]) for i in range(1, len(c1))]
    return sum(trs[-n:]) / n


def smart_flags(sm: dict | None) -> dict:
    """{COIN: "acc" | "dist"} from the smart-money radar's watch list"""
    out = {}
    for t in (((sm or {}).get("watch") or {}).get("tokens") or []):
        s = str(t.get("symbol") or "").upper()
        st = str(t.get("status") or "")
        buyers = [b for b in t.get("buyers") or [] if not b.get("sold")]
        if st == "накопление" and len(buyers) >= 2:
            out[s] = "acc"
        elif st in ("продают", "выбыла"):
            out[s] = "dist"
    return out


def allowed(v: str, coin: str, side: str, ctx: dict) -> bool:
    if v == "a_regime":
        return regime_ok(ctx.get("regime"), coin, side)
    if v == "b_smart":
        f = (ctx.get("smart") or {}).get(coin)
        return not ((side == "SHORT" and f == "acc") or (side == "LONG" and f == "dist"))
    if v == "d_major2":
        return ctx.get("same_side", 0) >= 2
    if v == "e_majors":
        return coin in MAJORS
    if v == "e_btc":
        return coin == "BTC"
    if v in ("f_level", "f_qm"):
        lv = ctx.get("lv")
        if not lv:
            return False
        return bool(lv["near_s"] if side == "LONG" else lv["near_r"]) if v == "f_level" else bool(lv["qm_l"] if side == "LONG" else lv["qm_s"])
    return True


def level_ctx(K: list) -> dict | None:
    """the level context at the last closed 1h bar of K = [[t, o, h, l, c, vol], …] (oldest first, ≥ 300 bars)"""
    from . import levels_desk as LD, qm_desk as QD
    if len(K) < 300:
        return None
    t, o, h, l, c, v = (list(x) for x in zip(*K))
    P = LD.prep(t, o, h, l, c, v)
    i = len(c) - 1
    a, px = P["atr"][i], c[i]
    if not a:
        return None
    S, R, _b, _a = LD.nearest(LD.levels_at(P, i), px)
    return {"px": px, "atr": a, "S": S, "R": R, "near_s": S is not None and px - S <= a, "near_r": R is not None and R - px <= a,
            "qm_l": QD.active(P, "L", i), "qm_s": QD.active(P, "S", i)}


def _ret(side: str, a: float, b: float) -> float:
    return (1 if side == "LONG" else -1) * (b / a - 1) * 100 - COST


def step(st: dict, desk: dict, now: int, ctx: dict, atr_of=None) -> dict:
    """one pass over the whale desk (web/whales_live.json «desk»): open new copies in every variant that allows them, manage exits"""
    st.setdefault("started", now)
    st.setdefault("open", [])
    st.setdefault("closed", [])
    seen = st.setdefault("seen", {})
    opens = [o for o in desk.get("open") or [] if isinstance(o, dict) and o.get("coin") and o.get("entry")]
    closed_desk = {f"{c.get('whale')}|{c.get('coin')}|{c.get('opened_t')}": c for c in desk.get("closed") or [] if isinstance(c, dict)}
    marks = {f"{o.get('whale')}|{o.get('coin')}|{o.get('opened_t')}": float(o.get("price") or o["entry"]) for o in opens}
    res = {"opened": 0, "closed": 0}
    for o in opens:
        k = f"{o.get('whale')}|{o.get('coin')}|{o.get('opened_t')}"
        if k in seen or int(o.get("opened_t") or 0) < int(st["started"]) - 10 * 60_000:
            seen.setdefault(k, now)
            continue
        seen[k] = now
        coin, side = str(o["coin"]).upper(), str(o.get("side") or "").upper()
        same = sum(1 for x in opens if str(x.get("coin")).upper() == coin and str(x.get("side")).upper() == side)
        c = {**ctx, "same_side": same}
        if ctx.get("lvl_of"):
            try:
                c["lv"] = ctx["lvl_of"](coin)
            except Exception:  # noqa: BLE001 — no level data: the f_* variants skip this copy
                c["lv"] = None
        a_ = None
        for v in VARIANTS:
            if not allowed(v, coin, side, c):
                continue
            if v == "c_trail" and a_ is None and atr_of:
                try:
                    a_ = atr_of(coin)
                except Exception:  # noqa: BLE001
                    a_ = None
            px = float(o.get("price") or o["entry"])
            st["open"].append({"v": v, "key": k, "coin": coin, "side": side, "entry": px, "opened_t": now, "best": px,
                               "stop_pct": STOP8 if v == "c_stop8" else STOP, "atr": a_ if v == "c_trail" else None,
                               "be": False, "whale": o.get("whale"),
                               **({"lv": {k: c["lv"].get(k) for k in ("px", "atr", "S", "R")}} if v.startswith("f_") and c.get("lv") else {})})
            res["opened"] += 1
    keep = []
    for p in st["open"]:
        s = 1 if p["side"] == "LONG" else -1
        dc = closed_desk.get(p["key"])
        px = float(dc.get("exit") or p["entry"]) if dc else marks.get(p["key"])
        why = None
        if px is None:                       # the copy is gone from the desk without a record: close at the last mark
            px, why = p.get("last") or p["entry"], "копия стола исчезла"
        p["last"] = px
        p["best"] = max(p["best"], px) if s > 0 else min(p["best"], px)
        lvl = p["entry"] * (1 - s * p["stop_pct"] / 100)
        if p["v"] == "c_be1r" and s * (p["best"] / p["entry"] - 1) * 100 >= R_BE:
            p["be"] = True
        if p.get("be"):
            lvl = max(lvl, p["entry"]) if s > 0 else min(lvl, p["entry"])
        if p["v"] == "c_trail" and p.get("atr"):
            tl = p["best"] - s * ATR_K * p["atr"]
            lvl = max(lvl, tl) if s > 0 else min(lvl, tl)
        if why is None:
            if (s > 0 and px <= lvl) or (s < 0 and px >= lvl):
                why, px = "стоп", lvl
            elif p["v"] == "c_time48" and now - p["opened_t"] >= TIME_H * H:
                why = "48 ч"
            elif dc:
                why = "кит вышел"
        if why:
            r = _ret(p["side"], p["entry"], px)
            st["closed"].append({**p, "exit": px, "closed_t": now, "pct": round(r, 3), "usd": round(r / 100 * NOTIONAL, 2), "why": why})
            res["closed"] += 1
        else:
            keep.append(p)
    st["open"] = keep
    st["closed"] = st["closed"][-5000:]
    if len(seen) > 5000:
        for k in sorted(seen, key=seen.get)[:-3000]:
            seen.pop(k, None)
    return res


def stats(xs: list) -> dict:
    if not xs:
        return {"n": 0}
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    cum = peak = dd = 0.0
    for x in xs:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {"n": len(xs), "win": round(sum(x > 0 for x in xs) / len(xs), 3), "pf": round(pos / neg, 2) if neg else None,
            "mean_pct": round(sum(xs) / len(xs), 3), "sum_pct": round(sum(xs), 2), "max_dd_pct": round(dd, 2)}


def summary(st: dict, real: dict | None = None) -> dict:
    """per variant: forward stats; «promote» only with ≥ 30 trades AND PF and drawdown better than the real source (owner 22.11)"""
    out = {}
    for v in VARIANTS:
        xs = [c["pct"] for c in sorted(st.get("closed") or [], key=lambda c: c["closed_t"]) if c["v"] == v]
        s = stats(xs)
        s["open"] = sum(1 for p in st.get("open") or [] if p["v"] == v)
        s["name_ru"] = NAMES_RU[v]
        r = real or {}
        s["promote"] = bool(s["n"] >= 30 and r.get("pf") is not None and (s.get("pf") or 0) > r["pf"]
                            and s["max_dd_pct"] < float(r.get("max_dd_pct") or 1e9))
        out[v] = s
    return {"started": st.get("started"), "variants": out}
