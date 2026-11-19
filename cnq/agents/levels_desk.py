"""«Торговля от уровней · CRYPTO NOVA AI (тест)» (owner 19.11.2026 23:42). PAPER ONLY, owner-only, TEST — never routed to the real
account (no «whales:» / «ivan:» signal id; okx_futures opens only copy signals).

One module for the backtest (scripts/research/levels_study.py) and the live paper desk (cnq/api/levels.py), so both use the same
levels and the same signals. PRE-REGISTERED 19.11.2026 (before any result was seen):

Levels at the close of H1 bar i (only bars ≤ i):
  • H1 swings — fractal 3 bars each side, known 3 bars later; the last 8 highs and 8 lows;
  • H4 swings — fractal 2 H4 bars each side, known when the 2nd H4 bar after closes; last 8 + 8;
  • D1 swings — fractal 2 days each side; last 6 + 6;
  • the previous UTC day's high / low and the previous 7 full days' high / low;
  • round numbers — multiples of half the price's order of magnitude (BTC 62 000 → every 5 000; SOL 72 → every 5);
  • volume-profile nodes — the 3 fullest 0.5 %-of-price bins of the last 30 days of H1 volume (recomputed every 24 bars);
  levels within ±15 % of the price; levels closer than 0.3 × ATR(H1, 14) are merged (their mean).
  S = the nearest level below the close, R = the nearest above.
Modes (signal at the close of bar i, entry at the open of bar i + 1; A = ATR(H1, 14)):
  range      long: S and R exist, R − S ≥ 3A, low_i within S ± 0.25A and close_i > S → target R, stop S − 0.5A, reward ≥ 1 × risk;
             short: the mirror at R.
  bo_vol     long: close_{i−1} ≤ R_{i−1} < close_i − 0.1A and volume_i ≥ 1.5 × its 20-bar mean → target the next level above
             (at least 1A away, else entry + 2 × risk), stop R_{i−1} − 0.5A. Short: the mirror below S_{i−1}.
  bo_retest  long: close_i > R_{i−1} + 0.1A (no volume filter) → a limit at R_{i−1} + 0.1A for the next 6 bars; filled when a bar's
             low reaches it; stop R_{i−1} − 0.5A, target as bo_vol. Short: mirror.
  fbo1/fbo3  false breakout: the high pierces R by ≥ 0.1A (previous close below R), and within 1 / 3 bars (the piercing bar
             included) a close comes back below R → short at that close; target S (the other side of the range), stop the highest
             high since the pierce + 0.25A, reward ≥ 1 × risk. Long: the mirror at S.
  crash      (owner: «падения резкие» — crash readiness): close_i < S_{i−1} − 0.1A and volume_i ≥ 2 × its 20-bar mean → short at once;
             exits «asym».
Exit profiles:
  sym   both sides: target / stop as above, at most 72 bars.
  asym  (owner: «падения резкие, рост долгий») shorts: at most 24 bars, the target is the nearer of the level and entry − 1.5 × risk,
        stop to entry after +1 × risk, then trailed 1A behind the best close; longs: at most 120 bars, the target is the 2nd level
        beyond (the 1st when there is no 2nd), no trailing.
  A bar touching both stop and target counts as the stop. Costs 0.10 % a round trip + funding 0.01 % per 8 h held (charged to both
  sides — real funding history is missing for most coins in the data). One position per coin per variant.
21 variants = 5 modes × 2 sides × 2 exits + crash. Verdict and the desk's variant: see levels_study.py."""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request

H = 3_600_000
DAY = 24 * H
COST, FUND_8H = 0.001, 0.0001
MODES = ("range", "bo_vol", "bo_retest", "fbo1", "fbo3")
VARIANTS = [f"{m}|{s}|{x}" for m in MODES for s in ("L", "S") for x in ("sym", "asym")] + ["crash|S|asym"]
START, RISK, MAX_OPEN = 10_000.0, 0.01, 5
CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config", "levels_desk.json")


# ── features ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def atr(h, l, c, n=14):
    out = [None] * len(c)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    s = 0.0
    for i, x in enumerate(tr):
        s += x
        if i >= n:
            s -= tr[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def fractals(h, l, k):
    """[(index, price)] of swing highs and lows (a high above / a low below the k bars on each side)"""
    hi, lo = [], []
    for j in range(k, len(h) - k):
        if h[j] == max(h[j - k:j + k + 1]) and h[j] > max(h[j - k:j]):
            hi.append((j, h[j]))
        if l[j] == min(l[j - k:j + k + 1]) and l[j] < min(l[j - k:j]):
            lo.append((j, l[j]))
    return hi, lo


def group(t, o, h, l, c, v, size_ms):
    """H1 bars → bigger bars aligned on UTC (size_ms): (start times, o, h, l, c, v, index of the last H1 bar of each)"""
    out = ([], [], [], [], [], [], [])
    for i in range(len(t)):
        b = t[i] - t[i] % size_ms
        if not out[0] or out[0][-1] != b:
            for arr, x in zip(out, (b, o[i], h[i], l[i], c[i], v[i], i)):
                arr.append(x)
        else:
            out[2][-1] = max(out[2][-1], h[i]); out[3][-1] = min(out[3][-1], l[i]); out[4][-1] = c[i]; out[5][-1] += v[i]; out[6][-1] = i
    return out


def prep(t, o, h, l, c, v):
    """everything the levels need, known bar by bar: per H1 index the confirmed swing lists are cut by «known at»"""
    n = len(c)
    P = {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v, "atr": atr(h, l, c)}
    sh, sl = fractals(h, l, 3)
    P["sw1"] = sorted([(j + 3, p) for j, p in sh] + [(j + 3, p) for j, p in sl])
    for name, size, k, keep in (("sw4", 4 * H, 2, 8), ("swd", DAY, 2, 6)):
        g = group(t, o, h, l, c, v, size)
        gh, gl = fractals(g[2], g[3], k)
        last = g[6]
        P[name] = sorted([(last[j + k], p) for j, p in gh] + [(last[j + k], p) for j, p in gl])
    d = group(t, o, h, l, c, v, DAY)                      # previous day / previous 7 days high-low, known from the next day on
    P["pdhl"] = [None] * n
    di = 0
    for i in range(n):
        while di + 1 < len(d[0]) and d[0][di + 1] <= t[i]:
            di += 1
        if di >= 1:
            ph, pl = d[2][di - 1], d[3][di - 1]
            wk = range(max(0, di - 7), di)
            P["pdhl"][i] = (ph, pl, max(d[2][x] for x in wk), min(d[3][x] for x in wk))
    vm = [None] * n
    s = 0.0
    for i in range(n):
        s += v[i]
        if i >= 20:
            s -= v[i - 20]
        if i >= 19:
            vm[i] = s / 20
    P["vmean"] = vm
    P["vp"] = {}
    return P


def vp_nodes(P, i):
    """the 3 fullest 0.5 % bins of the last 720 H1 bars' volume (cached per 24 bars)"""
    key = i - i % 24
    if key in P["vp"]:
        return P["vp"][key]
    a = max(0, key - 720)
    if key - a < 240:
        P["vp"][key] = []
        return []
    px = P["c"][key]
    w = px * 0.005
    bins: dict = {}
    for j in range(a, key + 1):
        b = int((P["h"][j] + P["l"][j] + P["c"][j]) / 3 // w)
        bins[b] = bins.get(b, 0.0) + P["v"][j]
    top = sorted(bins.items(), key=lambda kv: -kv[1])[:3]
    P["vp"][key] = [(b + 0.5) * w for b, _ in top]
    return P["vp"][key]


def _last(lst, i, keep_hi, keep_lo, px):
    """the last confirmed swings known at bar i (lst sorted by «known at»): keep_hi highs above or below, by recency"""
    out, j = [], len(lst)
    lo_ = 0
    hi_ = len(lst)
    while lo_ < hi_:                                      # bisect on «known at»
        m = (lo_ + hi_) // 2
        if lst[m][0] <= i:
            lo_ = m + 1
        else:
            hi_ = m
    j = lo_
    return [p for _, p in lst[max(0, j - (keep_hi + keep_lo)):j]]


def levels_at(P, i):
    """sorted merged levels known at the close of bar i"""
    px, a = P["c"][i], P["atr"][i]
    if not a:
        return []
    L = _last(P["sw1"], i, 8, 8, px) + _last(P["sw4"], i, 8, 8, px) + _last(P["swd"], i, 6, 6, px)
    if P["pdhl"][i]:
        L += list(P["pdhl"][i])
    step = 10 ** math.floor(math.log10(px)) / 2
    k0 = math.floor(px * 0.85 / step)
    L += [k * step for k in range(k0, k0 + int(px * 0.30 / step) + 3)]
    L += vp_nodes(P, i)
    L = sorted(x for x in L if x and 0.85 * px <= x <= 1.15 * px)
    out = []
    for x in L:
        if out and x - out[-1][-1] < 0.3 * a:
            out[-1].append(x)
        else:
            out.append([x])
    return [sum(g) / len(g) for g in out]


def nearest(L, px):
    below = [x for x in L if x < px]
    above = [x for x in L if x > px]
    return (below[-1] if below else None), (above[0] if above else None), below, above


# ── signals ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def signals(P, i, Lp, L, mode, side):
    """at the close of bar i → [{kind: "market"|"limit", px (limit), stop, tgt, tgt2, ref}] (prices; entry at the next open).
    Lp = the levels at bar i − 1, L = at bar i"""
    h, l, c, a = P["h"], P["l"], P["c"], P["atr"][i]
    if not a or i < 1:
        return []
    px = c[i]
    S, R, below, above = nearest(L, px)
    Sp, Rp, belowp, abovep = nearest(Lp, c[i - 1])
    vm = P["vmean"][i - 1]
    vol_ok = lambda m: vm is not None and vm > 0 and P["v"][i] >= m * vm      # noqa: E731
    out = []
    if mode == "range" and S and R and R - S >= 3 * a:
        if side == "L" and S - 0.25 * a <= l[i] <= S + 0.25 * a and px > S:
            out.append({"kind": "market", "stop": S - 0.5 * a, "tgt": R, "tgt2": above[1] if len(above) > 1 else R, "lvl": S})
        if side == "S" and R - 0.25 * a <= h[i] <= R + 0.25 * a and px < R:
            out.append({"kind": "market", "stop": R + 0.5 * a, "tgt": S, "tgt2": below[-2] if len(below) > 1 else S, "lvl": R})
    elif mode in ("bo_vol", "bo_retest", "crash"):
        need = {"bo_vol": 1.5, "bo_retest": 0.0, "crash": 2.0}[mode]
        if side == "L" and mode != "crash" and Rp and c[i - 1] <= Rp < px - 0.1 * a and (need == 0 or vol_ok(need)):
            nx = [x for x in above if x >= px + a]
            ent = px if mode == "bo_vol" else Rp + 0.1 * a
            stop = Rp - 0.5 * a
            tgt = nx[0] if nx else ent + 2 * (ent - stop)
            out.append({"kind": "market" if mode == "bo_vol" else "limit", "px": ent, "stop": stop, "tgt": tgt,
                        "tgt2": nx[1] if len(nx) > 1 else tgt, "lvl": Rp})
        if side == "S" and Sp and c[i - 1] >= Sp > px + 0.1 * a and (need == 0 or vol_ok(need)):
            nx = [x for x in reversed(below) if x <= px - a]
            ent = px if mode != "bo_retest" else Sp - 0.1 * a
            stop = Sp + 0.5 * a
            tgt = nx[0] if nx else ent - 2 * (stop - ent)
            out.append({"kind": "limit" if mode == "bo_retest" else "market", "px": ent, "stop": stop, "tgt": tgt,
                        "tgt2": nx[1] if len(nx) > 1 else tgt, "lvl": Sp})
    elif mode in ("fbo1", "fbo3"):
        N = 1 if mode == "fbo1" else 3
        for back in range(N):                              # the pierce happened `back` bars ago, the close comes back now
            j = i - back
            if j < 1:
                break
            Lj = levels_at(P, j - 1) if back else Lp
            Sj, Rj, _, _ = nearest(Lj, c[j - 1])
            if side == "S" and Rj and c[j - 1] < Rj and h[j] >= Rj + 0.1 * a and px < Rj and all(c[x] >= Rj for x in range(j, i)):
                top = max(h[j:i + 1])
                tg = Sj if Sj and Sj < px else None
                if tg:
                    out.append({"kind": "market", "stop": top + 0.25 * a, "tgt": tg, "tgt2": tg, "lvl": Rj})
                break
            if side == "L" and Sj and c[j - 1] > Sj and l[j] <= Sj - 0.1 * a and px > Sj and all(c[x] <= Sj for x in range(j, i)):
                bot = min(l[j:i + 1])
                tg = Rj if Rj and Rj > px else None
                if tg:
                    out.append({"kind": "market", "stop": bot - 0.25 * a, "tgt": tg, "tgt2": tg, "lvl": Sj})
                break
    return out


def plan(sig, side, exit_, entry):
    """the trade's stop / target / max bars / trailing from a signal and the actual entry; None when reward < 1 × risk where required"""
    long = side == "L"
    risk = (entry - sig["stop"]) if long else (sig["stop"] - entry)
    if risk <= 0:
        return None
    tgt = sig["tgt"]
    if exit_ == "asym":
        if long:
            tgt, maxb, trail = sig.get("tgt2") or tgt, 120, False
        else:
            tgt, maxb, trail = max(tgt, entry - 1.5 * risk), 24, True
    else:
        maxb, trail = 72, False
    rew = (tgt - entry) if long else (entry - tgt)
    if rew <= 0:
        return None
    return {"stop": sig["stop"], "tgt": tgt, "maxb": maxb, "trail": trail, "risk": risk}


def step_trade(tr, bar, a):
    """advance one open trade by one H1 bar (o, h, l, c) → None while open, else (exit price, why)"""
    o, h, l, c = bar
    long = tr["side"] == "L"
    st, tg = tr["stop"], tr["tgt"]
    if long:
        if l <= st:
            return (min(o, st) if o < st else st), "стоп"
        if h >= tg:
            return (max(o, tg) if o > tg else tg), "цель"
    else:
        if h >= st:
            return (max(o, st) if o > st else st), "стоп"
        if l <= tg:
            return (min(o, tg) if o < tg else tg), "цель"
    tr["bars"] += 1
    if tr.get("trail") and a:
        r = tr["risk"]
        best = tr["best"] = (max(tr.get("best", tr["entry"]), c) if long else min(tr.get("best", tr["entry"]), c))
        if long and best - tr["entry"] >= r:
            tr["stop"] = max(tr["stop"], tr["entry"], best - a)
        if not long and tr["entry"] - best >= r:
            tr["stop"] = min(tr["stop"], tr["entry"], best + a)
    if tr["bars"] >= tr["maxb"]:
        return c, "время"
    return None


def ret_pct(side, entry, exit_px, bars):
    s = 1 if side == "L" else -1
    return (s * (exit_px / entry - 1) - COST - FUND_8H * bars / 8) * 100


# ── the live paper desk ──────────────────────────────────────────────────────────────────────────────────────────────
OKX = "https://www.okx.com"
COINS = ["BTC", "ETH", "SOL", "LINK", "ARB", "LTC", "OP", "DOT", "BNB", "NEAR", "XRP", "BCH", "DOGE", "TRX", "ADA", "SUI", "AVAX", "APT"]


def _http(path, **q):
    req = urllib.request.Request(f"{OKX}{path}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "cnq-levels-desk/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    if str(d.get("code")) != "0":
        raise RuntimeError(f"OKX {path}: {d.get('code')}")
    return d.get("data") or []


def hourly(coin, pages=8, http=_http):
    """the last ~800 closed H1 candles of the OKX swap: [t, o, h, l, c, vol (quote)] oldest first"""
    rows, after = {}, None
    for _ in range(pages):
        d = http("/api/v5/market/history-candles", instId=f"{coin}-USDT-SWAP", bar="1H", limit=100, **({"after": after} if after else {}))
        if not d:
            break
        for r in d:
            if str(r[8] if len(r) > 8 else "1") == "1":
                rows[int(r[0])] = [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7] if len(r) > 7 else r[5])]
        after = d[-1][0]
    return [rows[k] for k in sorted(rows)]


def cfg(path=None):
    try:
        with open(path or CFG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"variant": "range|L|sym"}


def context(P, i, L):
    """the snapshot stored with a trade: the levels around, ATR, trend vs SMA 20/50/200 (H1), volume vs its mean, hour"""
    c = P["c"]
    px = c[i]
    sma = lambda n: sum(c[i - n + 1:i + 1]) / n if i + 1 >= n else None      # noqa: E731
    S, R, below, above = nearest(L, px)
    return {"px": px, "atr_pct": round(P["atr"][i] / px * 100, 3) if P["atr"][i] else None,
            "levels_below": [round(x, 8) for x in below[-3:]], "levels_above": [round(x, 8) for x in above[:3]],
            "sma_pct": {str(n): (round((px / sma(n) - 1) * 100, 2) if sma(n) else None) for n in (20, 50, 200)},
            "vol_x": round(P["v"][i] / P["vmean"][i - 1], 2) if i and P["vmean"][i - 1] else None,
            "utc_hour": (P["t"][i] // H) % 24, "pdhl": P["pdhl"][i]}


def step(st, now, v, candles=hourly, coins=COINS):
    """one run: manage open positions on the closed H1 bars since the last run, then look for new signals on the last closed bar.
    st = the desk state (dict), v = the variant «mode|side|exit»"""
    mode, side, ex = v.split("|")
    st = st or {"start": START, "cash": START, "open": [], "closed": [], "started": now, "last_bar": {}, "pending": []}
    for k in ("open", "closed", "pending"):
        st.setdefault(k, [])
    st.setdefault("last_bar", {})
    errs = []
    for coin in coins:
        try:
            K = candles(coin)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{coin}: {type(e).__name__}")
            continue
        K = [k for k in K if k[0] + H <= now]                      # closed bars only
        if len(K) < 300:
            continue
        lb = int(st["last_bar"].get(coin) or 0)
        new = [i for i, k in enumerate(K) if k[0] > lb]
        if not new:
            continue
        t, o, h, l, c, vv = (list(x) for x in zip(*K))
        P = prep(t, o, h, l, c, vv)
        for i in new:
            bar = (o[i], h[i], l[i], c[i])
            for tr in [x for x in st["open"] if x["coin"] == coin]:   # 1. open positions
                r = step_trade(tr, bar, P["atr"][i])
                if r:
                    px, why = r
                    pct = ret_pct(tr["side"], tr["entry"], px, tr["bars"])
                    usd = round(tr["notional"] * pct / 100, 2)
                    st["cash"] += usd
                    st["open"].remove(tr)
                    st["closed"].append({**tr, "exit": px, "closed_t": t[i] + H, "pct": round(pct, 3), "usd": usd, "why": why})
            for pd in [x for x in st["pending"] if x["coin"] == coin]:  # 2. retest limits
                if (pd["side"] == "L" and l[i] <= pd["px"]) or (pd["side"] == "S" and h[i] >= pd["px"]):
                    st["pending"].remove(pd)
                    _open(st, coin, pd["side"], ex, pd["sig"], pd["px"], t[i], pd["ctx"])
                elif i - pd.get("i0", i) >= 6 or t[i] - pd["t"] >= 6 * H:
                    st["pending"].remove(pd)
        st["last_bar"][coin] = t[new[-1]]
        i = new[-1]                                               # 3. new signals at the last closed bar
        if any(x["coin"] == coin for x in st["open"] + st["pending"]) or len(st["open"]) >= MAX_OPEN:
            continue
        L, Lp = levels_at(P, i), levels_at(P, i - 1)
        for sg in signals(P, i, Lp, L, mode, side):
            ctx = context(P, i, L)
            if sg["kind"] == "limit":
                st["pending"].append({"coin": coin, "side": side, "px": sg["px"], "sig": sg, "t": t[i], "ctx": ctx})
            else:
                _open(st, coin, side, ex, sg, c[i], t[i] + H, ctx)   # the next bar's open ≈ this close
            break
    eq = st["cash"]
    st["equity"] = round(eq, 2)
    st["errors"] = errs[:5]
    st["t"] = now
    st["closed"] = st["closed"][-500:]
    return st


def _open(st, coin, side, ex, sg, entry, t, ctx):
    p = plan(sg, side, ex, entry)
    if not p or len(st["open"]) >= MAX_OPEN:
        return
    eq = st["cash"]
    risk_usd = RISK * eq
    notional = min(risk_usd / (p["risk"] / entry), eq)            # 1 % of equity at risk, never more than the equity in one trade
    st["open"].append({"coin": coin, "side": side, "entry": entry, "opened_t": t, "stop": p["stop"], "stop0": p["stop"], "tgt": p["tgt"],
                       "maxb": p["maxb"], "trail": p["trail"], "risk": p["risk"], "bars": 0, "notional": round(notional, 2),
                       "risk_usd": round(risk_usd, 2), "level": sg.get("lvl"), "ctx": ctx})


def public(st, v, now, bt=None):
    """the owner's card (served only to the signed-in owner)"""
    cl = st.get("closed") or []
    wins = sum(1 for x in cl if x["usd"] > 0)
    mode, side, ex = v.split("|")
    return {"name": "Торговля от уровней · CRYPTO NOVA AI (тест)", "test": True, "owner_only": True, "asof": now, "variant": v,
            "mode": mode, "side": side, "exit": ex, "start": START, "equity": st.get("equity", START),
            "pnl_usd": round(st.get("equity", START) - START, 2), "open": st.get("open") or [], "pending": st.get("pending") or [],
            "closed": list(reversed(cl[-50:])), "trades": len(cl), "wins": wins, "started": st.get("started"),
            "errors": st.get("errors") or [], "backtest": bt,
            "rule_ru": ("ТЕСТ, бумажные деньги $10 000. Уровни H1/H4/D1 (свинги, вчерашний день и неделя, круглые числа, узлы объёма); "
                        "риск 1% счёта на сделку, не больше 5 позиций, одна на монету. На реальный счёт сделки не попадают.")}
