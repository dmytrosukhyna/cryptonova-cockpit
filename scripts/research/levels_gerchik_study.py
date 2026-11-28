"""«Герчик»: level quality and the classic level entries (owner 28.11.2026 16:03). RESEARCH ONLY (+ the same code runs the paper
desks through cnq/agents/levels_lab_desk.py for variants «g|…»).

PRE-REGISTERED 28.11.2026 ~14:30 UTC, before any run of this file (committed first, then run):
Data: as levels_v2_study (lab-data-core18 + the fixed 12 new coins, Binance 15m, 2022-11-20 → 2026-11-04; H1 from full 15m quads; D1
  from H1). Halves IS < 2024-11-12 ≤ OOS. Universes: all30 | BTC | ETH | BES (BTC ETH SOL).
LEVELS: swing extremes — H1 fractal 3 (last 40), H4 fractal 2 (last 20), D1 fractal 2 (last 15), each known when confirmed; points
  within 0.3·A (A = ATR(H1, 14)) merged (the oldest forming bar kept). QUALITY at the event bar i (only bars < i):
    bsu   ≥ 1 later bar touched the level to within 0.05·A without closing beyond it (БСУ / БПУ);
    mirror a close above AND a close below the level (by 0.1·A) since it formed (support became resistance or back);
    sharp the forming bar was the extreme of an impulse: within 6 bars after it the price went ≥ 2·A away;
    round within 0.1·A of a round number (half the price's order of magnitude, as levels_desk).
  strength = bsu + mirror + sharp + round (0–4). Only levels within 3·ATR(H4) of the price are watched.
PATTERNS (long described; short = the mirror; R = a level above the price, S = below; signal at the close of H1 bar i):
  p1  break → return → second break → retest: within the 48 bars before i the closes went ≤ R, then > R (1st break), then < R
      (return), then > R (2nd break, bar b2); bar i (≤ 12 bars after b2, all closes since b2 > R) has low ≤ R + 0.15·A and close > R.
  p2b squeeze → break: the 4 bars before i have ranges < 0.6·A, highs within 0.3·A under R and rising lows; bar i closes > R.
  p2l the same squeeze, a buy-stop at R + 0.05·A filled on bar i (the squeeze is known at the close of i − 1).
  p3a / p3b false breakout by 1 / 2 bars: 1 (2) closes below S after a close ≥ S, then bar i closes back above S → long (the mirror
      at R → short). The break bars' extreme is the «sweep».
  p4  near retest: a close > R after a close ≤ R at bar b (1–3 bars before i), bar i low ≤ R + 0.15·A, close > R.
ENTRIES: h1 = the next H1 open; m15 (p1, p4 only) = after the signal bar's close, the first 15m close back above R after a 15m
  low ≤ R + 0.15·A within the next 12 hours → the next 15m open (managed on H1 from that hour; only the stop on the fill hour);
  p2l = its stop order.
STOPS (long; A_d = ATR(D1, 14)): g10 / g20 = level − 0.10 / 0.20·A_d − 0.02·A_d люфт | bar = the signal bar's low − 0.1·A |
  fvg = the far edge of the break impulse's 3-candle gap (h[b−1] when l[b+1] > h[b−1], b = the (2nd) break bar; no gap → no trade)
  − 0.1·A | swing = the last confirmed H1 swing low below the entry − 0.1·A. Risk 0.2 %–10 % of the entry.
EXITS: r3 = 3R target | h2t / h3t = half at 2R / 3R, the rest trails 1.5·A under the highest close | be1 = 3R target, stop to the entry
  at +1R | besw = 3R target, stop to the entry once a new H1 swing low above the entry is confirmed | opp = the next level beyond
  (≥ 1R away, else 3R). Max 72 bars (h2t / h3t 120). A bar touching the stop and a target = the stop. Costs as levels_desk.
FILTERS (applied to the same trades): none | range (запас хода: today's move in the trade's direction < 75 % of A_d AND the next
  level beyond ≥ 3 × the stop distance) | reg (BTC 4h EMA20 vs EMA50 with the side) | vol (the break bar's volume > its 20-bar mean)
  | asia (00–08 UTC) | eu (08–13) | us (13–21). Strength minimum: 1 | 2 | 3.
COUNT: (p1 ×2 entries + p2b + p2l + p3a + p3b + p4 ×2) = 8 × 2 sides × 5 stops × 6 exits × 7 filters × 3 strengths = 10 080 per universe
  × 4 universes = 40 320 → Bonferroni 0.05 / 40 320 two-sided → z ≥ 4.93.
SURVIVES: IS and OOS mean > 0, both n ≥ 100, OOS daily t ≥ 4.93; all30 also > 0 on the new 12. WALK-FORWARD per universe as the lab;
  OBSERVATION: the top 5 WF scores (min of the half means, n ≥ 100 each), plus the best p1 and the best fvg-stop variant if not among
  them. By year for the picks. DIAGNOSTIC: the «stopped, then the target within 72 bars» share of p1 / p3 (g10, r3) next to the
  current desk's 37 % (levels_wide_study).
Usage: levels_gerchik_study.py core18.npz NEW12.npz OUT.json"""
import bisect, json, math, os, statistics as st, sys, time
from array import array
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cnq.agents import levels_desk as LD  # noqa: E402
import levels_v2_study as V2  # noqa: E402

H, DAY = LD.H, LD.DAY
MID = V2.MID
NEW12 = set(V2.NEW12)
UNIV = {"all30": None, "BTC": ["BTC"], "ETH": ["ETH"], "BES": ["BTC", "ETH", "SOL"]}
STREAMS = (("p1", "h1"), ("p1", "m15"), ("p2b", "h1"), ("p2l", "stop"), ("p3a", "h1"), ("p3b", "h1"), ("p4", "h1"), ("p4", "m15"))
STOPS = ("g10", "g20", "bar", "fvg", "swing")
EXITS = ("r3", "h2t", "h3t", "be1", "besw", "opp")
FILTS = ("none", "range", "reg", "vol", "asia", "eu", "us")
SMIN = (1, 2, 3)
NTEST = len(STREAMS) * 2 * len(STOPS) * len(EXITS) * len(FILTS) * len(SMIN) * len(UNIV)
Z = 4.93
COST, F8 = LD.COST * 100, LD.FUND_8H * 100


# ── preparation ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def prep(P):
    """adds to a levels_desk.prep dict: swings, ATR(D1) / ATR(H4) per H1 bar, day extremes so far, the level lists"""
    t, o, h, l, c, v = P["t"], P["o"], P["h"], P["l"], P["c"], P["v"]
    n = len(c)
    hi1, lo1 = LD.fractals(h, l, 3)
    P["_sl"] = sorted((j + 3, j, p) for j, p in lo1)            # (known at, formed at, price)
    P["_sh"] = sorted((j + 3, j, p) for j, p in hi1)
    piv = [(k, j, p, "1h") for k, j, p in P["_sl"] + P["_sh"]]
    for size, kk, tf in ((4 * H, 2, "4h"), (DAY, 2, "1d")):
        g = LD.group(t, o, h, l, c, v, size)
        gh, gl = LD.fractals(g[2], g[3], kk)
        last = g[6]
        first = [0] + [x + 1 for x in last[:-1]]
        for j, p in gh + gl:
            if j + kk < len(last):
                # the H1 bar of the extreme inside the TF bar
                a_, b_ = first[j], last[j]
                jj = max(range(a_, b_ + 1), key=lambda q: h[q]) if (j, p) in gh else min(range(a_, b_ + 1), key=lambda q: l[q])
                piv.append((last[j + kk], jj, p, tf))
        if tf == "1d":
            ad = LD.atr(g[2], g[3], g[4])
            P["_atrd"] = [None] * n
            P["_dlo"], P["_dhi"] = [None] * n, [None] * n
            di = -1
            for i in range(n):
                while di + 1 < len(g[0]) and g[0][di + 1] <= t[i]:
                    di += 1
                P["_atrd"][i] = ad[di - 1] if di >= 1 else None              # the last COMPLETED day
                d0 = first[di] if di >= 0 else i
                P["_dlo"][i], P["_dhi"][i] = min(l[d0:i + 1]), max(h[d0:i + 1])
        else:
            a4 = LD.atr(g[2], g[3], g[4])
            k4 = [bisect.bisect_right(last, i) - 1 for i in range(n)]
            P["_a4"] = [a4[k] if k >= 0 else None for k in k4]
    piv.sort()
    P["_piv"] = piv
    P["_pk"] = [x[0] for x in piv]
    return P


def levels(P, i):
    """[(price, formed index)] known at bar i near the price, merged"""
    a, a4 = P["atr"][i], P["_a4"][i]
    if not a or not a4:
        return []
    q = bisect.bisect_right(P["_pk"], i)
    cnt = {"1h": 0, "4h": 0, "1d": 0}
    lim = {"1h": 40, "4h": 20, "1d": 15}
    pts = []
    px = P["c"][i]
    for k in range(q - 1, -1, -1):
        _, j, p, tf = P["_piv"][k]
        if cnt[tf] >= lim[tf]:
            if all(cnt[x] >= lim[x] for x in cnt):
                break
            continue
        cnt[tf] += 1
        if abs(p - px) <= 3 * a4:
            pts.append((p, j))
    pts.sort()
    out = []
    for p, j in pts:
        if out and p - out[-1][0] < 0.3 * a:
            m = out[-1]
            out[-1] = ((m[0] + p) / 2, min(m[1], j))
        else:
            out.append((p, j))
    return out


def strength(P, lv, j0, i):
    h, l, c, a = P["h"], P["l"], P["c"], P["atr"][i]
    if not a or j0 >= i - 1:
        return 0
    above = lv > c[i]
    bsu = 0
    k0 = max(j0 + 1, i - 720)                       # the last 30 days are enough to judge a level
    for k in range(k0, i):
        if above and abs(h[k] - lv) <= 0.05 * a and c[k] <= lv:
            bsu = 1; break
        if not above and abs(l[k] - lv) <= 0.05 * a and c[k] >= lv:
            bsu = 1; break
    seg = c[k0:i]
    mirror = int(any(x > lv + 0.1 * a for x in seg) and any(x < lv - 0.1 * a for x in seg))
    e = j0 + 7
    sharp = int(max((abs(h[k] - lv) for k in range(j0 + 1, min(i, e))), default=0) >= 2 * a or
                max((abs(l[k] - lv) for k in range(j0 + 1, min(i, e))), default=0) >= 2 * a)
    step = 10 ** math.floor(math.log10(lv)) / 2 if lv > 0 else 1
    rnd = int(abs(lv - round(lv / step) * step) <= 0.1 * a)
    return bsu + mirror + sharp + rnd


# ── events ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def events(P, raw15=None, regime=None):
    """[{stream, i, side, lv, b (break bar), ext (sweep extreme), s (strength), nxt (next level beyond), flags}]"""
    o, h, l, c, v, vm, a1 = P["o"], P["h"], P["l"], P["c"], P["v"], P["vmean"], P["atr"]
    t = P["t"]
    n = len(c)
    out = []
    last = defaultdict(lambda: -99)
    for i in range(300, n - 1):
        A = a1[i]
        if not A or not P["_atrd"][i]:
            continue
        L = levels(P, i)
        if not L:
            continue
        prices = [x[0] for x in L]
        for lv, j0 in L:
            for side in ("L", "S"):
                s = 1 if side == "L" else -1
                hits = []
                # breakout patterns: level beyond in the trade direction (R for longs, S for shorts)
                beyond = lambda x: s * (x - lv) > 0            # noqa: E731
                if beyond(c[i]) and i >= 60:
                    # p1: retest hold at i
                    if s * (lv - (l[i] if s > 0 else h[i])) >= -0.15 * A:
                        st_ = []
                        k = i - 1
                        while k > i - 13 and beyond(c[k]):
                            k -= 1
                        b2 = k + 1
                        if b2 < i and not beyond(c[k]):
                            # before b2: a return (close on the other side) after a 1st break within 48 bars
                            w = [beyond(c[x]) for x in range(max(0, i - 48), b2)]
                            if w and not w[-1]:
                                try:
                                    q = len(w) - 1 - w[::-1].index(True)          # last close beyond before the return
                                    if not all(w[:q + 1]) and q > 0:
                                        hits.append(("p1", b2))
                                except ValueError:
                                    pass
                        # p4: near retest 1–3 bars after a break
                        for b in range(i - 3, i):
                            if b > 0 and beyond(c[b]) and not beyond(c[b - 1]) and all(beyond(c[x]) for x in range(b, i)):
                                hits.append(("p4", b))
                                break
                    # p2b: squeeze into the level, then the break at i
                    if not beyond(c[i - 1]):
                        sq = all((h[k] - l[k]) < 0.6 * A and s * (lv - (h[k] if s > 0 else l[k])) <= 0.3 * A and s * (lv - (h[k] if s > 0 else l[k])) >= 0
                                 for k in range(i - 4, i)) and all(s * ((l[k] if s > 0 else h[k]) - (l[k - 1] if s > 0 else h[k - 1])) > 0 for k in range(i - 3, i))
                        if sq:
                            hits.append(("p2b", i))
                # p2l: the squeeze known at i − 1, a stop order beyond the level filled on bar i
                if i >= 6 and not beyond(c[i - 1]) and s * ((h[i] if s > 0 else l[i]) - (lv + s * 0.05 * A)) >= 0:
                    sq = all((h[k] - l[k]) < 0.6 * A and 0 <= s * (lv - (h[k] if s > 0 else l[k])) <= 0.3 * A for k in range(i - 5, i - 1)) and \
                        all(s * ((l[k] if s > 0 else h[k]) - (l[k - 1] if s > 0 else h[k - 1])) > 0 for k in range(i - 4, i - 1))
                    if sq:
                        hits.append(("p2l", i))
                # false breakouts: the level BEHIND (support for longs), N closes beyond it, then back
                for N, nm in ((1, "p3a"), (2, "p3b")):
                    if i - N - 1 < 0:
                        continue
                    if s * (c[i] - lv) > 0 and all(s * (c[k] - lv) < 0 for k in range(i - N, i)) and s * (c[i - N - 1] - lv) >= 0:
                        hits.append((nm, i - N))
                for nm, b in hits:
                    if i - last[(nm, side)] < 6:
                        continue
                    last[(nm, side)] = i
                    if nm in ("p3a", "p3b"):
                        ext = min(l[b:i + 1]) if s > 0 else max(h[b:i + 1])
                    else:
                        ext = l[i] if s > 0 else h[i]
                    nx = [x for x in prices if s * (x - lv) > 0.3 * A and s * (x - c[i]) > 0]
                    nxt = (min(nx) if s > 0 else max(nx)) if nx else None
                    hr = (t[i] // H) % 24
                    day_move = (c[i] - P["_dlo"][i]) if s > 0 else (P["_dhi"][i] - c[i])
                    rg = regime(t[i]) if regime else None
                    out.append({"stream": nm, "i": i, "side": side, "lv": lv, "b": b, "ext": ext, "s": strength(P, lv, j0, i), "nxt": nxt,
                                "range_ok": bool(day_move < 0.75 * P["_atrd"][i]), "reg_ok": (rg is None) or (rg == s),
                                "vol_ok": bool(vm[b - 1] and v[b] > vm[b - 1]), "sess": "asia" if hr < 8 else ("eu" if hr < 13 else ("us" if hr < 21 else "late")),
                                "A": A, "Ad": P["_atrd"][i]})
    return out


def fill(P, ev, entry, raw15):
    """→ (fill H1 index, price, stop_only_on_fill_bar) or None"""
    o, h, l = P["o"], P["h"], P["l"]
    n = len(o)
    i, s, lv, A = ev["i"], (1 if ev["side"] == "L" else -1), ev["lv"], ev["A"]
    if entry == "h1":
        return (i + 1, o[i + 1], False) if i + 1 < n else None
    if entry == "stop":                                      # p2l: the stop order at lv + 0.05 A filled on bar i
        px = lv + s * 0.05 * A
        return (i, max(o[i], px) if s > 0 else min(o[i], px), True)
    if raw15 is None or not len(raw15[0]):
        return None
    T, O1, H1, L1, C1 = raw15
    t0 = P["t"][ev["i"]] + H                                 # after the signal bar closed — no look-ahead
    a_, b_ = int(np.searchsorted(T, t0)), int(np.searchsorted(T, t0 + 12 * H))
    touched = False
    for k in range(a_, min(b_, len(T) - 1)):
        if (s > 0 and L1[k] <= lv + 0.15 * A) or (s < 0 and H1[k] >= lv - 0.15 * A):
            touched = True
        if touched and s * (C1[k] - lv) > 0:
            fi = int(np.searchsorted(P["_tarr"], T[k + 1] - T[k + 1] % H, "left"))
            return (fi, O1[k + 1], True) if fi < n else None
    return None


def stop_of(P, ev, name, fi, p):
    s = 1 if ev["side"] == "L" else -1
    lv, A, Ad = ev["lv"], ev["A"], ev["Ad"]
    if name in ("g10", "g20"):
        base = ev["ext"] if ev["stream"] in ("p3a", "p3b") else lv
        return base - s * (float(name[1:]) / 100 * Ad + 0.02 * Ad)
    if name == "bar":
        return ev["ext"] - s * 0.1 * A
    h, l = P["h"], P["l"]
    if name == "fvg":
        b = ev["b"]
        if b < 1 or b + 1 >= len(h):
            return None
        if s > 0 and l[b + 1] > h[b - 1]:
            return h[b - 1] - 0.1 * A
        if s < 0 and h[b + 1] < l[b - 1]:
            return l[b - 1] + 0.1 * A
        return None
    sw = P["_sl"] if s > 0 else P["_sh"]
    kn = [x[0] for x in sw]
    q = bisect.bisect_right(kn, fi) - 1
    while q >= 0 and s * (sw[q][2] - p) >= 0:
        q -= 1
    return (sw[q][2] - s * 0.1 * A) if q >= 0 else None


def sim(P, ev, fi, p, stp, ex, stop_only0):
    """→ (ret %, why, exit index) or None"""
    o, h, l, c, a = P["o"], P["h"], P["l"], P["c"], P["atr"]
    n = len(c)
    s = 1 if ev["side"] == "L" else -1
    R = s * (p - stp)
    if R <= 0 or not (0.002 <= R / p <= 0.10):
        return None
    tg = p + s * 3 * R
    if ex == "opp" and ev["nxt"] and s * (ev["nxt"] - p) >= R:
        tg = ev["nxt"]
    half = {"h2t": 2, "h3t": 3}.get(ex)
    maxb = 120 if half else 72
    lvl, got_half, best, be = stp, None, p, False
    sw_known = P["_sl"] if s > 0 else P["_sh"]
    swk = [x[0] for x in sw_known]
    A = ev["A"]
    for k in range(fi, min(n, fi + maxb)):
        ok_t = not (k == fi and stop_only0)
        hk, lk, ok_ = h[k], l[k], o[k]
        adv = lk if s > 0 else hk
        if s * (adv - lvl) <= 0:
            px = ok_ if (k > fi or not stop_only0) and s * (ok_ - lvl) < 0 else lvl
            r = s * (px / p - 1) * 100
            res = (0.5 * got_half + 0.5 * r) if got_half is not None else r
            return res - COST - F8 * (k - fi + 1) / 8, ("безубыток" if be or got_half is not None else "стоп"), k
        fav = hk if s > 0 else lk
        if half:
            if got_half is None and ok_t and s * (fav - (p + s * half * R)) >= 0:
                got_half = half * R / p * 100
                lvl = p if s * (p - lvl) > 0 else lvl
            if got_half is not None:
                best = max(best, c[k]) if s > 0 else min(best, c[k])
                tl = best - s * 1.5 * A
                lvl = max(lvl, tl) if s > 0 else min(lvl, tl)
            continue
        if ok_t and s * (fav - tg) >= 0:
            return s * (tg / p - 1) * 100 - COST - F8 * (k - fi + 1) / 8, "цель", k
        if ex == "be1" and not be and ok_t and s * (fav - (p + s * R)) >= 0:
            be, lvl = True, p
        if ex == "besw" and not be:
            q = bisect.bisect_right(swk, k) - 1
            if q >= 0 and sw_known[q][1] > fi and s * (sw_known[q][2] - p) > 0:
                be, lvl = True, p
    k = min(n, fi + maxb) - 1
    r = s * (c[k] / p - 1) * 100
    res = (0.5 * got_half + 0.5 * r) if got_half is not None else r
    return res - COST - F8 * (k - fi + 1) / 8, "время", k


def keep(ev, flt, smin, stp_dist):
    if ev["s"] < smin:
        return False
    if flt == "none":
        return True
    if flt == "range":
        return ev["range_ok"] and ev["nxt"] is not None and abs(ev["nxt"] - ev["lv"]) >= 3 * stp_dist
    if flt == "reg":
        return ev["reg_ok"]
    if flt == "vol":
        return ev["vol_ok"]
    return ev["sess"] == flt


def btc_regime(PB):
    if "g4" not in PB:
        PB["g4"] = LD.group(PB["t"], PB["o"], PB["h"], PB["l"], PB["c"], PB["v"], 4 * H)
    R = V2.btc_regime(PB)
    keys = sorted(R)

    def f(t):
        q = bisect.bisect_right(keys, t - t % (4 * H) - 4 * H) - 1
        return R[keys[q]] if q >= 0 else None
    return f


def trades_variant(P, variant, raw15, since, regime=None):
    """the live desks (levels_lab_desk, «g|stream|side|entry|stop|exit|filter|smin»): [{key, side, t, fill_t, entry, stop, tgt, pct?, why?, open}]"""
    _, stream, side, entry, sn, ex, flt, sm = variant.split("|")
    n = len(P["c"])
    out = []
    for ev in events(P, raw15, regime):
        if ev["stream"] != stream or ev["side"] != side or P["t"][ev["i"]] < since:
            continue
        f = fill(P, ev, entry, raw15)
        if not f:
            continue
        fi, p, so = f
        stp = stop_of(P, ev, sn, fi, p)
        if stp is None or not keep(ev, flt, int(sm), abs(p - stp)):
            continue
        r = sim(P, ev, fi, p, stp, ex, so)
        if r is None:
            continue
        horizon = 120 if ex in ("h2t", "h3t") else 72
        is_open = r[1] == "время" and n - fi < horizon
        s = 1 if side == "L" else -1
        out.append({"key": f"{P['t'][ev['i']]}|{side}|{stream}", "side": side, "t": int(P["t"][ev["i"]]), "fill_t": int(P["t"][fi]), "entry": float(p),
                    "stop": float(stp), "tgt": float(p + s * 3 * abs(p - stp)), "open": bool(is_open),
                    **({} if is_open else {"pct": round(float(r[0]), 3), "why": r[1], "exit_t": int(P["t"][r[2]]) + H})})
    return out


# ── the study ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def summ(r, xt):
    if r.size == 0:
        return {"n": 0}
    gp, gl = float(r[r > 0].sum()), float(-r[r < 0].sum())
    u, inv = np.unique(xt // DAY, return_inverse=True)
    dm = np.bincount(inv, weights=r) / np.bincount(inv)
    t_ = float(dm.mean() / (dm.std(ddof=1) / math.sqrt(dm.size))) if dm.size > 2 and dm.std(ddof=1) > 0 else None
    return {"n": int(r.size), "mean": round(float(r.mean()), 3), "win": round(float((r > 0).mean()), 3), "pf": round(gp / gl, 2) if gl else None,
            "t": round(t_, 2) if t_ is not None else None}


def main(core, new, outp):
    D = V2.load(core)
    D.update(V2.load(new))
    import levels_qm_study as Q
    raw = {}
    for pth in (core, new):
        raw.update(Q.load(pth)[1])
    D = {c_: K for c_, K in D.items() if len(K) >= 2000}
    COINS = sorted(D)
    t0 = time.time()
    PB = None
    PP = {}
    for coin in ["BTC"] + [x for x in COINS if x != "BTC"]:
        K = D[coin]
        t, o, h, l, c, v = (list(x) for x in zip(*K))
        P = prep(LD.prep(t, o, h, l, c, v))
        P["_tarr"] = np.asarray(t, dtype=np.int64)
        PP[coin] = P
        if coin == "BTC":
            PB = P
    reg = btc_regime(PB)
    # per trade storage: key = stream|entry|side|stop|exit → arrays
    S = {}
    def put(k, *vals):
        a_ = S.get(k)
        if a_ is None:
            a_ = S[k] = tuple(array(tc) for tc in ("h", "f", "q", "q", "b", "b", "b", "b", "b", "f", "b"))
        for arr, x in zip(a_, vals):
            arr.append(x)
    SESS = {"asia": 0, "eu": 1, "us": 2, "late": 3}
    for coin in COINS:
        P = PP[coin]
        T, O1, H1, L1, C1 = raw[coin]
        r15 = (np.asarray(T, dtype=np.int64), np.asarray(O1), np.asarray(H1), np.asarray(L1), np.asarray(C1))
        evs = events(P, r15, reg)
        ci = COINS.index(coin)
        n = len(P["c"])
        for ev in evs:
            for stream, entry in STREAMS:
                if stream != ev["stream"]:
                    continue
                f = fill(P, ev, entry, r15)
                if not f:
                    continue
                fi, p, so = f
                for sn in STOPS:
                    stp = stop_of(P, ev, sn, fi, p)
                    if stp is None:
                        continue
                    dist = abs(p - stp)
                    rng = bool(ev["range_ok"] and ev["nxt"] is not None and abs(ev["nxt"] - ev["lv"]) >= 3 * dist)
                    for ex in EXITS:
                        r = sim(P, ev, fi, p, stp, ex, so)
                        if r is None:
                            continue
                        swept = 0
                        if ex == "r3" and r[1] == "стоп":
                            s = 1 if ev["side"] == "L" else -1
                            tg = p + s * 3 * dist
                            swept = int(any(s * ((P["h"][q] if s > 0 else P["l"][q]) - tg) >= 0 for q in range(r[2] + 1, min(n, r[2] + 73))))
                        put(f"{stream}|{entry}|{ev['side']}|{sn}|{ex}", ci, r[0], int(P["t"][r[2]]) + H, int(P["t"][ev["i"]]), ev["s"], int(rng),
                            int(ev["reg_ok"]), int(ev["vol_ok"]), SESS[ev["sess"]], 0.0, swept)
        print(coin, len(evs), "events", f"{time.time() - t0:.0f}s", flush=True)
    NP = {k: tuple(np.frombuffer(a_, dtype=dt) if dt != "f" else np.frombuffer(a_, dtype=np.float32).astype(float)
                   for a_, dt in zip(v, (np.int16, "f", np.int64, np.int64, np.int8, np.int8, np.int8, np.int8, np.int8, "f", np.int8)))
          for k, v in S.items()}
    newi = np.array([COINS.index(c_) for c_ in COINS if c_ in NEW12], dtype=np.int16)
    res = {"ntest": NTEST, "z": Z, "universes": {}, "swept": {}}
    for k, (ci_, r_, xt_, t_, s_, rg_, re_, vo_, se_, _x, sw_) in NP.items():
        if k.endswith("|g10|r3") and k.split("|")[0] in ("p1", "p3a", "p3b", "p4"):
            stops = (sw_ >= 0) & (r_ < 0)
            res["swept"][k] = {"n": int(r_.size), "stopped": int(stops.sum()), "swept": int(sw_.sum()),
                               "share": round(float(sw_.sum()) / max(1, int((r_ < 0).sum())), 3)}
    for un, cs in UNIV.items():
        cidx = np.array([COINS.index(c_) for c_ in (cs or COINS) if c_ in COINS], dtype=np.int16)
        rows = {}
        for k, (ci_, r_, xt_, t_, s_, rg_, re_, vo_, se_, _x, sw_) in NP.items():
            mu = np.isin(ci_, cidx)
            yr_all = t_.astype("datetime64[ms]").astype("datetime64[Y]").astype(int) + 1970
            for flt in FILTS:
                fm = {"none": np.ones_like(mu), "range": rg_ == 1, "reg": re_ == 1, "vol": vo_ == 1, "asia": se_ == 0, "eu": se_ == 1, "us": se_ == 2}[flt]
                for sm in SMIN:
                    m = mu & fm & (s_ >= sm)
                    if m.sum() < 30:
                        continue
                    ma, mb = m & (t_ < MID), m & (t_ >= MID)
                    row = {"is": summ(r_[ma], xt_[ma]), "oos": summ(r_[mb], xt_[mb])}
                    if un == "all30":
                        mn = m & np.isin(ci_, newi)
                        row["new12"] = summ(r_[mn], xt_[mn])
                    row["survives"] = bool(row["is"]["n"] >= 100 and row["oos"]["n"] >= 100 and row["is"]["mean"] > 0 and row["oos"]["mean"] > 0
                                           and (row["oos"].get("t") or 0) >= Z and (un != "all30" or row["new12"].get("mean", -1) > 0))
                    if row["is"]["n"] >= 100 and row["oos"]["n"] >= 100:
                        row["wf"] = round(min(row["is"]["mean"], row["oos"]["mean"]), 3)
                        yrs = yr_all[m]
                        row["by_year"] = {int(y): [int((yrs == y).sum()), round(float(r_[m][yrs == y].mean()), 3)] for y in np.unique(yrs)}
                    rows[f"g|{k.split('|')[0]}|{k.split('|')[2]}|{k.split('|')[1]}|{k.split('|')[3]}|{k.split('|')[4]}|{flt}|{sm}"] = row
        ok = {v: r for v, r in rows.items() if r.get("wf") is not None}
        b1 = max(ok, key=lambda v: ok[v]["is"]["mean"]) if ok else None
        b2 = max(ok, key=lambda v: ok[v]["oos"]["mean"]) if ok else None
        top = sorted(ok, key=lambda v: -ok[v]["wf"])[:12]
        res["universes"][un] = {"n_variants": len(rows), "survivors": [v for v, r in rows.items() if r["survives"]],
                                "pick_is": [b1, ok[b1]["is"], ok[b1]["oos"]] if b1 else None, "pick_oos": [b2, ok[b2]["is"], ok[b2]["oos"]] if b2 else None,
                                "top_wf": {v: rows[v] for v in top},
                                "best_p1": max(((v, r) for v, r in ok.items() if v.split("|")[1] == "p1"), key=lambda x: x[1]["wf"], default=(None, None)),
                                "best_fvg": max(((v, r) for v, r in ok.items() if v.split("|")[4] == "fvg"), key=lambda x: x[1]["wf"], default=(None, None)),
                                "plain": {f"{s_}|{sd}": rows.get(f"g|{s_}|{sd}|{e_}|g10|r3|none|1", {}) for s_, e_ in STREAMS if e_ != "m15" for sd in ("L", "S")}}
    allrows = [(un, v, r) for un, U in res["universes"].items() for v, r in U["top_wf"].items()]
    obs, seen = [], set()
    for un, v, r in sorted(allrows, key=lambda x: -(x[2].get("wf") or -99)):
        if v not in seen:
            seen.add(v); obs.append({"universe": un, "v": v, "is": r["is"], "oos": r["oos"], "new12": r.get("new12"), "by_year": r.get("by_year")})
        if len(obs) >= 5:
            break
    for tag in ("best_p1", "best_fvg"):
        v, r = res["universes"]["all30"][tag]
        if v and v not in seen:
            seen.add(v); obs.append({"universe": "all30", "v": v, "tag": tag, "is": r["is"], "oos": r["oos"], "new12": r.get("new12"), "by_year": r.get("by_year")})
    res["observation"] = obs
    json.dump(res, open(outp, "w"), ensure_ascii=False, indent=1, default=float)
    sh = lambda x: [x.get("n"), x.get("mean"), x.get("pf"), x.get("t")] if x else None      # noqa: E731
    print("::notice title=g summary::" + json.dumps({un: {"n": U["n_variants"], "surv": len(U["survivors"]), "surv3": U["survivors"][:3],
                                                         "pick_is": [U["pick_is"][0], sh(U["pick_is"][1]), sh(U["pick_is"][2])] if U["pick_is"] else None,
                                                         "pick_oos": [U["pick_oos"][0], sh(U["pick_oos"][1]), sh(U["pick_oos"][2])] if U["pick_oos"] else None}
                                                    for un, U in res["universes"].items()})[:4000])
    print("::notice title=g plain all30 (g10, 3R, no filter; is / oos)::" + json.dumps({k: [sh(r.get("is")), sh(r.get("oos"))] for k, r in res["universes"]["all30"]["plain"].items()})[:4000])
    print("::notice title=g swept (p1 / p3 / p4, g10, 3R)::" + json.dumps(res["swept"]))
    for q in range(0, len(obs), 3):
        print(f"::notice title=g observation {q // 3 + 1}::" + json.dumps(obs[q:q + 3])[:4000])
    for un in UNIV:
        U = res["universes"][un]
        print(f"::notice title=g top {un}::" + json.dumps({v: [sh(r["is"]), sh(r["oos"]), sh(r.get("new12"))] for v, r in list(U["top_wf"].items())[:8]})[:4000])


if __name__ == "__main__":
    main(*sys.argv[1:4])
