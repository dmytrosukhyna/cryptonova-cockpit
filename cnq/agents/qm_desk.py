"""«Квазимодо 4ч · лонг · 3R (тест)» (owner 26.11.2026: «да» on the paper desk after scripts/research/levels_qm_study.py).
PAPER ONLY, owner-only, TEST — never routed to the real account (no «whales:» / «ivan:» signal id; okx_futures opens only copy signals).

The rules are the variant that passed the pre-registered study (qm|4h|L|3R; docs/research/levels-qm-2026-11-26.md), unchanged:
  swings — fractals on UTC 4h bars (2 bars each side), known 2 bars later; consecutive swings of one type collapse to the more
  extreme one. Bullish QM: the last three known swings are L1 (low), H1 (high), L2 (low) with L2 < L1; the first H1 close above H1
  (before any low below L2) is the break → a LIMIT BUY at L1 («левое плечо»), valid 120 H1 bars, dropped if the price reaches the
  target first. Stop = L2 − 0.25 × ATR(H1, 14) at the break («стоп ниже низа»); risk 0.3 %–8 % of the entry, else skipped.
  Target = entry level + 3 × risk; at most 120 H1 bars; the fill bar checks only the stop (levels_desk.step_trade fill_bar).
Sizing as the levels desk: $10 000 paper, 1 % risk per trade, one position ≤ 20 % of the equity, ≤ 5 positions, one per coin.
Candles: OKX public hourly swaps (levels_desk.hourly, ~800 bars), the same 18 coins as the study."""
from __future__ import annotations

from . import levels_desk as LD

H = LD.H
VARIANT = "qm|4h|L|3R"
WAIT, MAXB, TGT_R = 120, 120, 3.0
START, RISK, MAX_OPEN, POS_CAP = LD.START, LD.RISK, LD.MAX_OPEN, LD.POS_CAP


def swings(P, tf="4h"):
    """[(known_at H1 index, price, 'H'|'L')] collapsed to alternating (the study's rule, scripts/research/levels_qm_study.py)"""
    t, o, h, l, c, v = P["t"], P["o"], P["h"], P["l"], P["c"], P["v"]
    if tf == "1h":
        hi, lo = LD.fractals(h, l, 3)
        raw = [(j + 3, p, "H") for j, p in hi] + [(j + 3, p, "L") for j, p in lo]
    else:
        g = LD.group(t, o, h, l, c, v, 4 * H)
        gh, gl = LD.fractals(g[2], g[3], 2)
        last = g[6]
        raw = [(last[j + 2], p, "H") for j, p in gh if j + 2 < len(last)] + [(last[j + 2], p, "L") for j, p in gl if j + 2 < len(last)]
    raw.sort()
    out = []
    for k, p, ty in raw:
        if out and out[-1][2] == ty:
            if (ty == "H" and p > out[-1][1]) or (ty == "L" and p < out[-1][1]):
                out[-1] = (k, p, ty)
            continue
        out.append((k, p, ty))
    return out


def setups(P, tf="4h"):
    """[(break H1 index, side, entry level, stop, broken swing)] — QM breaks, both sides"""
    sw = swings(P, tf)
    c, h, l, a = P["c"], P["h"], P["l"], P["atr"]
    n = len(c)
    res = []
    for q in range(2, len(sw)):
        s1, s2, s3 = sw[q - 2], sw[q - 1], sw[q]
        k0 = s3[0]
        k1 = sw[q + 1][0] if q + 1 < len(sw) else n
        if s1[2] == "L" and s2[2] == "H" and s3[2] == "L" and s3[1] < s1[1]:
            side, lvl, head, brk = "L", s1[1], s3[1], s2[1]
        elif s1[2] == "H" and s2[2] == "L" and s3[2] == "H" and s3[1] > s1[1]:
            side, lvl, head, brk = "S", s1[1], s3[1], s2[1]
        else:
            continue
        for i in range(k0, min(k1, n)):
            if side == "L" and l[i] < head:
                break
            if side == "S" and h[i] > head:
                break
            if (side == "L" and c[i] > brk) or (side == "S" and c[i] < brk):
                if a[i]:
                    stop = head - 0.25 * a[i] if side == "L" else head + 0.25 * a[i]
                    res.append((i, side, lvl, stop, brk))
                break
    return res


def active(P, side: str, i: int, tf="1h", within=72) -> bool:
    """a same-side QM broke in the last `within` H1 bars up to bar i (the «QM за» whale filter, study part B F3)"""
    return any(b <= i < b + within and s == side for (b, s, *_x) in setups(P, tf))


def step(st, now, candles=LD.hourly, coins=LD.COINS):
    st = st or {"start": START, "cash": START, "open": [], "closed": [], "pending": [], "started": now, "last_bar": {}, "seen": []}
    for k in ("open", "closed", "pending", "seen"):
        st.setdefault(k, [])
    st.setdefault("last_bar", {})
    errs = []
    for coin in coins:
        try:
            K = candles(coin)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{coin}: {type(e).__name__}")
            continue
        K = [k for k in K if k[0] + H <= now]
        if len(K) < 300:
            continue
        lb = int(st["last_bar"].get(coin) or 0)
        new = [i for i, k in enumerate(K) if k[0] > lb]
        if not new:
            continue
        t, o, h, l, c, vv = (list(x) for x in zip(*K))
        P = LD.prep(t, o, h, l, c, vv)
        brk = {t[i]: (lvl, stop) for (i, sd, lvl, stop, _b) in setups(P, "4h") if sd == "L"}
        for i in new:
            for pd in [x for x in st["pending"] if x["coin"] == coin]:     # 1. the limit at the left shoulder
                if l[i] <= pd["px"]:
                    st["pending"].remove(pd)
                    if len(st["open"]) < MAX_OPEN:
                        _open(st, coin, min(o[i], pd["px"]), pd, t[i])
                elif h[i] >= pd["tgt"] or t[i] - pd["t"] >= WAIT * H:
                    st["pending"].remove(pd)                              # ran to the target without a retest / expired
            for tr in [x for x in st["open"] if x["coin"] == coin]:       # 2. open positions
                fb = tr.get("fill_t") == t[i]
                r = LD.step_trade(tr, ((tr["entry"] if fb else o[i]), h[i], l[i], c[i]), P["atr"][i], fill_bar=fb)
                if r:
                    px, why = r
                    pct = LD.ret_pct("L", tr["entry"], px, tr["bars"])
                    usd = round(tr["notional"] * pct / 100, 2)
                    st["cash"] += usd
                    st["open"].remove(tr)
                    st["closed"].append({**tr, "exit": px, "closed_t": t[i] + H, "pct": round(pct, 3), "usd": usd, "why": why})
            if t[i] in brk:                                               # 3. a new QM break at this bar
                lvl, stop = brk[t[i]]
                key = f"{coin}|{t[i]}"
                risk = lvl - stop
                if key in st["seen"] or not (0.003 <= risk / lvl <= 0.08):
                    continue
                st["seen"].append(key)
                if any(x["coin"] == coin for x in st["open"] + st["pending"]):
                    continue
                st["pending"].append({"coin": coin, "side": "L", "px": lvl, "stop": stop, "tgt": lvl + TGT_R * risk, "risk": risk,
                                      "t": t[i], "head_stop": stop})
        st["last_bar"][coin] = t[new[-1]]
    st["equity"] = round(st["cash"], 2)
    st["errors"] = errs[:5]
    st["t"] = now
    st["closed"] = st["closed"][-500:]
    st["seen"] = st["seen"][-2000:]
    return st


def _open(st, coin, entry, pd, t):
    eq = st["cash"]
    risk = entry - pd["stop"]
    if risk <= 0:
        return
    want = RISK * eq / (risk / entry)
    notional = min(want, POS_CAP * eq)
    capped = notional < want - 1e-9
    st["open"].append({"coin": coin, "side": "L", "entry": entry, "opened_t": t, "fill_t": t, "stop": pd["stop"], "stop0": pd["stop"],
                       "tgt": pd["tgt"], "maxb": MAXB, "trail": False, "risk": risk, "bars": 0, "notional": round(notional, 2),
                       "risk_usd": round(notional * risk / entry, 2), "capped": capped, **({"capped_ru": LD.CAPPED_RU} if capped else {}),
                       "level": pd["px"], "break_t": pd["t"]})


def public(st, now, bt=None):
    cl = st.get("closed") or []
    return {"name": "Квазимодо 4ч · лонг · 3R · CRYPTO NOVA AI (тест)", "test": True, "owner_only": True, "asof": now, "variant": VARIANT,
            "start": START, "equity": st.get("equity", START), "pnl_usd": round(st.get("equity", START) - START, 2),
            "open": st.get("open") or [], "pending": st.get("pending") or [], "closed": list(reversed(cl[-50:])), "trades": len(cl),
            "wins": sum(1 for x in cl if x["usd"] > 0), "started": st.get("started"), "errors": st.get("errors") or [], "backtest": bt,
            "rule_ru": ("ТЕСТ, бумажные $10 000. Паттерн «квазимодо» на 4-часовых свечах: низ → вершина → более низкий низ → закрытие выше "
                        "вершины; покупка лимитом на первом низу («левое плечо»), стоп ниже нижнего низа, цель — 3 риска, до 5 суток. "
                        "Риск 1% счёта, позиция ≤ 20% счёта, ≤ 5 позиций, одна на монету. На реальный счёт сделки не попадают.")}
