"""«Уровни» lab (owner 27.11.2026 08:59 + 09:00): the EXACT live desk logic on 30 coins, its exit / stop / entry grid, order blocks,
the earlier families per coin universe, walk-forward. RESEARCH ONLY — nothing here trades.

PRE-REGISTERED 27.11.2026 ~08:30 UTC, before any run of this file (committed first, then run):
Data: as scripts/research/levels_v2_study.py (lab-data-core18 + the fixed 12 new coins, Binance 15m, 2022-11-20 → 2026-11-04, H1 from
  full 15m quads); halves IS < 2024-11-12 ≤ OOS. Levels: cnq/agents/levels_desk.levels_at — the live desk's own levels, unchanged.
Universes (fixed): BTC | ETH | SOL | BES (BTC ETH SOL) | top5 (+ BNB XRP) | top10 (+ DOGE ADA TRX AVAX LINK) | all30.
EVENTS (long described, short = the mirror; A = ATR(H1, 14) at the event bar; S / R = levels_desk.nearest):
  desk   the live desk's «range» signal exactly (levels_desk.signals mode range): S and R exist, R − S ≥ 3A, the low within S ± 0.25A
         and the close > S → events at that close.
  touch  the same range condition on the levels of the bar before, the previous close > S + 0.25A and the low ≤ S + 0.25A (the touch
         itself, before its close is known).
  ob_b / ob_w  order block: an H1 close above the last confirmed H1 swing high (fractal 3) after a close at or below it; the last down
         candle among the 6 before the break = the block; zone = its body (ob_b: close … open) or its wick + body (ob_w: low … open);
         a limit at the zone top valid 48 bars, cancelled by a close below the zone; «S» = the zone bottom, «R» = the next desk level
         above the break.
  One event per coin + side per 6 bars (later ones dropped).
ENTRIES:
  E_close  (desk events) market at the next H1 open — what the desk does;
  E_touch  (touch events) a limit at S + 0.25A filled on the touch bar (min(open, limit));
  E_15m    (touch events) the first 15m close back above S after a 15m low ≤ S + 0.25A within the touch hour or the 3 after → the next
           15m open (the trade is then managed on H1 bars from the next H1 bar; on the fill hour only the stop is checked);
  E_limit  (touch events) a limit at S − 0.25A (inside the sweep) valid 6 bars from the touch;
  E_ob     (ob events) the zone-top limit.
STOPS: S − k·A for k = 0.5, 1, 1.5, 2, 3 | S − 1·ATR(H4) | the last confirmed H1 swing low below the fill − 0.1A | the sweep extreme
  (the lowest low from the touch / break bar to the fill bar) − 0.1A. A stop at or above the fill → no trade; risk 0.2 %–10 % of the fill.
TARGETS: 1.5R | 2R | 3R | opp (R, only when ≥ 0.5R above the fill).
MANAGEMENT: none (max 72 H1 bars) | be (stop to the fill at +1R) | trail (after +1R the stop trails 1.5A under the highest close) |
  part (½ at +1R, the rest to the target with the stop at the fill) | t12 | t24 | t48 (time stop, no other change).
  A bar touching the stop and a target counts as the stop; on a limit's fill bar only the stop is checked; a gap through the stop fills
  at the open. Costs as levels_desk (0.10 % a round trip + 0.01 % per 8 h).
FAMILIES of levels_v2 (fb1 fb3 rt bn fbqm × L/S × 4 filters × 3 targets) are re-scored per universe from their trades.
COUNT: every universe × variant = (desk 1 entry + touch 3 + ob 2) × 8 stops × 4 targets × 7 managements × 2 sides × 7 universes
  + 120 × 7 = 18 816 + 840 = 19 656 tests → Bonferroni 0.05 / 19 656 two-sided → z ≥ 4.75.
SURVIVES (per universe): IS mean > 0, OOS mean > 0, both n ≥ 100, OOS daily t ≥ 4.75; all30 also: the mean on the new 12 > 0.
WALK-FORWARD (per universe): the best IS mean with IS n ≥ 100 → its OOS result; the best OOS mean with OOS n ≥ 100 → its IS result.
  WF score of a variant = min(IS mean, OOS mean) with n ≥ 100 in each half. Single-coin universes: walk-forward only + results by year.
OBSERVATION (owner: «even with no survivors, run the top 5 by walk-forward as paper desks, labelled unproven»): the 5 best WF scores
  over all universes (distinct entry / stop / target / management), plus the best BTC-only and the best ETH-only.
FORWARD (part b): mode «forward» replays the live desk (levels_desk.step, config/levels_desk.json variant) bar by bar on OKX hourly
  candles from its start (20.11) and lists each trade: coin, side, entry, stop distance, MFE / MAE (in R), exit, and for stops
  whether the price reached the target within 72 bars after («выбило и пошло»).
Usage: levels_lab_study.py run core18.npz NEW12.npz OUT.json | levels_lab_study.py forward OUT.json"""
import bisect, json, math, os, statistics as st, sys, time
from array import array
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cnq.agents import levels_desk as LD  # noqa: E402
import levels_v2_study as V2  # noqa: E402
import levels_qm_study as Q  # noqa: E402

H, DAY = LD.H, LD.DAY
MID = V2.MID
NEW12 = set(V2.NEW12)
UNIV = {"BTC": ["BTC"], "ETH": ["ETH"], "SOL": ["SOL"], "BES": ["BTC", "ETH", "SOL"], "top5": ["BTC", "ETH", "SOL", "BNB", "XRP"],
        "top10": ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "TRX", "AVAX", "LINK"], "all30": None}
STOPS = ("a0.5", "a1", "a1.5", "a2", "a3", "a4h", "swing", "sweep")
TGTS = ("1.5R", "2R", "3R", "opp")
MGMT = ("none", "be", "trail", "part", "t12", "t24", "t48")
ENTRIES = {"desk": ("E_close",), "touch": ("E_touch", "E_15m", "E_limit"), "ob_b": ("E_ob",), "ob_w": ("E_ob",)}
NTEST = (1 + 3 + 2) * len(STOPS) * len(TGTS) * len(MGMT) * 2 * len(UNIV) + 120 * len(UNIV)
Z = 4.75
COST, F8 = LD.COST * 100, LD.FUND_8H * 100


def sim(long, p, stp, tg, A, O, Hh, Ll, C, mg, stop_only0):
    """one trade on forward H1 arrays (index 0 = the fill bar) → (ret %, why, bars) or None"""
    s = 1 if long else -1
    R = s * (p - stp)
    if R <= 0 or not (0.002 <= R / p <= 0.10) or s * (tg - p) <= 0:
        return None
    n = min(len(C), {"t12": 12, "t24": 24, "t48": 48}.get(mg, 72))
    if n < 2:
        return None
    O, Hh, Ll, C = O[:n], Hh[:n], Ll[:n], C[:n]
    fav = Hh if long else -Ll                         # favourable extreme (signed)
    adv = Ll if long else -Hh
    sp, tgs, ps = s * stp, s * tg, s * p
    def first(mask, start=0):
        m = mask[start:]
        k = int(np.argmax(m)) if m.size else 0
        return start + k if m.size and m[k] else None
    def stop_px(k, lvl):
        o = s * O[k]
        return s * (o if (k > 0 or not stop_only0) and o < lvl else lvl)
    def ret(px, k):
        return s * (px / p - 1) * 100 - COST - F8 * (k + 1) / 8
    hit_s0 = adv <= sp
    hit_t = fav >= tgs
    if stop_only0:
        hit_t = hit_t.copy(); hit_t[0] = False
    if mg in ("be", "trail", "part"):
        one = fav >= ps + R
        if stop_only0:
            one = one.copy(); one[0] = False
        i1 = first(one)
        ks = first(hit_s0)
        if i1 is None or (ks is not None and ks <= i1):          # stopped before +1R (a bar with both → the stop)
            kt = first(hit_t)
            if ks is None and kt is None:
                return ret(C[-1], n - 1), "время", n
            if ks is not None and (kt is None or ks <= kt):
                return ret(stop_px(ks, sp), ks), "стоп", ks
            return ret(tg, kt), "цель", kt
        if mg == "part":
            kt = first(hit_t, i1) if not (tgs <= ps + R) else i1
            hb = first(adv <= ps, i1 + 1)
            half1 = ret(p + s * R, i1)
            if kt is not None and (hb is None or kt < hb):
                return 0.5 * half1 + 0.5 * ret(tg, kt), "цель", kt
            if hb is not None:
                return 0.5 * half1 + 0.5 * ret(stop_px(hb, ps), hb), "безубыток", hb
            return 0.5 * half1 + 0.5 * ret(C[-1], n - 1), "время", n
        # be / trail: from bar i1 + 1 the stop is at least the fill
        lvl = np.full(n, ps)
        if mg == "trail":
            cm = np.maximum.accumulate(s * C)
            tr = np.concatenate([[ps], cm[:-1]]) - 1.5 * A
            lvl = np.maximum(ps, tr)
        kt = first(hit_t, i1)
        kb = None
        for k in range(i1 + 1, n):
            if adv[k] <= lvl[k]:
                kb = k
                break
        if kt is not None and (kb is None or kt < kb):
            return ret(tg, kt), "цель", kt
        if kb is not None:
            return ret(stop_px(kb, lvl[kb]), kb), ("трейл" if mg == "trail" else "безубыток"), kb
        return ret(C[-1], n - 1), "время", n
    ks, kt = first(hit_s0), first(hit_t)
    if ks is None and kt is None:
        return ret(C[-1], n - 1), "время", n
    if ks is not None and (kt is None or ks <= kt):
        return ret(stop_px(ks, sp), ks), "стоп", ks
    return ret(tg, kt), "цель", kt


def swing_lows_highs(P):
    hi, lo = LD.fractals(P["h"], P["l"], 3)
    return sorted((j + 3, p) for j, p in lo), sorted((j + 3, p) for j, p in hi)


def events(P, raw15):
    """{stream: [dict]} for both sides"""
    o, h, l, c, a = P["o"], P["h"], P["l"], P["c"], P["atr"]
    n = len(c)
    Lc = P["_L"]
    out = {"desk": [], "touch": [], "ob_b": [], "ob_w": []}
    last = defaultdict(lambda: -99)
    lows, highs = P["_swl"], P["_swh"]
    for i in range(300, n - 2):
        A = a[i]
        if not A:
            continue
        for side in ("L", "S"):
            for sg in LD.signals(P, i, Lc[i - 1], Lc[i], "range", side):
                if i - last[("desk", side)] >= 6:
                    last[("desk", side)] = i
                    out["desk"].append({"i": i, "side": side, "S": sg["lvl"], "R": sg["tgt"], "A": A, "sweep": l[i] if side == "L" else h[i]})
            Sp, Rp, _, _ = LD.nearest(Lc[i - 1], c[i - 1])
            if Sp and Rp and Rp - Sp >= 3 * A:
                if side == "L" and c[i - 1] > Sp + 0.25 * A and l[i] <= Sp + 0.25 * A and i - last[("touch", "L")] >= 6:
                    last[("touch", "L")] = i
                    out["touch"].append({"i": i, "side": "L", "S": Sp, "R": Rp, "A": A})
                if side == "S" and c[i - 1] < Rp - 0.25 * A and h[i] >= Rp - 0.25 * A and i - last[("touch", "S")] >= 6:
                    last[("touch", "S")] = i
                    out["touch"].append({"i": i, "side": "S", "S": Rp, "R": Sp, "A": A})
        # order blocks: structure break at the close of i
        for side, sw in (("L", highs), ("S", lows)):
            q = bisect.bisect_right(sw, (i, 1e30)) - 1
            if q < 0:
                continue
            lvl = sw[q][1]
            brk = (c[i - 1] <= lvl < c[i]) if side == "L" else (c[i - 1] >= lvl > c[i])
            if not brk or i - last[("ob", side)] < 6:
                continue
            ob = next((j for j in range(i - 1, max(0, i - 7), -1) if (c[j] < o[j] if side == "L" else c[j] > o[j])), None)
            if ob is None:
                continue
            last[("ob", side)] = i
            S_, R_, _, above = LD.nearest(Lc[i], c[i])
            nxt = (R_ if side == "L" else S_)
            for kind in ("ob_b", "ob_w"):
                if side == "L":
                    top, bot = o[ob], (c[ob] if kind == "ob_b" else l[ob])
                else:
                    top, bot = o[ob], (c[ob] if kind == "ob_b" else h[ob])
                out[kind].append({"i": i, "side": side, "S": bot, "top": top, "R": nxt, "A": A})
    return out


def fills(P, ev, stream, raw15):
    """[(entry name, fill index, fill price, stop_only0, sweep extreme)]"""
    o, h, l, c = P["o"], P["h"], P["l"], P["c"]
    n = len(c)
    i, long, S, A = ev["i"], ev["side"] == "L", ev["S"], ev["A"]
    s = 1 if long else -1
    res = []
    if stream == "desk":
        res.append(("E_close", i + 1, o[i + 1], False, ev["sweep"]))
        return res
    if stream == "touch":
        lim = S + s * 0.25 * A
        res.append(("E_touch", i, (min(o[i], lim) if long else max(o[i], lim)), True, S - s * 0.25 * A))
        T, O1, H1, L1, C1 = raw15
        t0 = P["t"][i]
        a, b = int(np.searchsorted(T, t0)), int(np.searchsorted(T, t0 + 4 * H))
        touched, f15 = False, None
        for k in range(a, min(b, len(T) - 1)):
            if (long and L1[k] <= lim) or ((not long) and H1[k] >= lim):
                touched = True
            if touched and ((long and C1[k] > S) or ((not long) and C1[k] < S)):
                f15 = k + 1
                break
        if f15 is not None and f15 < len(T):
            fi = int(np.searchsorted(P["_tarr"], T[f15] - T[f15] % H, "left"))
            if fi < n:
                ext = (min(L1[a:f15]) if long else max(H1[a:f15]))
                res.append(("E_15m", fi, O1[f15], True, ext))
        lim2 = S - s * 0.25 * A
        for k in range(i, min(n, i + 7)):
            if (long and l[k] <= lim2) or ((not long) and h[k] >= lim2):
                ext = min(l[i:k + 1]) if long else max(h[i:k + 1])
                res.append(("E_limit", k, (min(o[k], lim2) if long else max(o[k], lim2)) if k > i else lim2, True, ext))
                break
        return res
    top, bot = ev["top"], ev["S"]
    for k in range(i + 1, min(n, i + 49)):
        if (long and c[k - 1] < bot) or ((not long) and c[k - 1] > bot):
            break
        if (long and l[k] <= top) or ((not long) and h[k] >= top):
            ext = min(l[i:k + 1]) if long else max(h[i:k + 1])
            res.append(("E_ob", k, min(o[k], top) if long else max(o[k], top), True, ext))
            break
    return res


def stop_of(name, long, P, fi, ev, ext):
    s = 1 if long else -1
    S, A = ev["S"], ev["A"]
    if name.startswith("a") and name != "a4h":
        return S - s * float(name[1:]) * A
    if name == "a4h":
        A4 = (P["a4"][fi] if fi < len(P["a4"]) else None) or 2 * A
        return S - s * A4
    if name == "sweep":
        return ext - s * 0.1 * A
    sw = P["_swl"] if long else P["_swh"]
    q = bisect.bisect_right(sw, (fi, 1e30)) - 1
    px = P["o"][fi]
    while q >= 0 and (sw[q][1] >= px if long else sw[q][1] <= px):
        q -= 1
    return (sw[q][1] - s * 0.1 * A) if q >= 0 else None


def tgt_of(name, long, p, stp, ev):
    s = 1 if long else -1
    R = s * (p - stp)
    if name == "opp":
        r_ = ev.get("R")
        return r_ if r_ and s * (r_ - p) >= 0.5 * R else None
    return p + s * float(name[:-1]) * R


def summ(r, xt):
    """r = returns (np), xt = exit times (np) → stats with the t of daily means"""
    if r.size == 0:
        return {"n": 0}
    gp, gl = float(r[r > 0].sum()), float(-r[r < 0].sum())
    d = xt // DAY
    u, inv = np.unique(d, return_inverse=True)
    dm = np.bincount(inv, weights=r) / np.bincount(inv)
    t_ = float(dm.mean() / (dm.std(ddof=1) / math.sqrt(dm.size))) if dm.size > 2 and dm.std(ddof=1) > 0 else None
    return {"n": int(r.size), "mean": round(float(r.mean()), 3), "win": round(float((r > 0).mean()), 3), "pf": round(gp / gl, 2) if gl else None,
            "t": round(t_, 2) if t_ is not None else None}


def main_run(core, new, outp):
    D = V2.load(core)
    D.update(V2.load(new))
    raw = {}
    for pth in (core, new):
        _, r_ = Q.load(pth)
        raw.update(r_)
    t0 = time.time()
    R = {}                          # variant → (coin idx, ret, exit_t, t) as compact arrays
    COINS = sorted(D)

    def put(v, ci, r_, xt, t_):
        a_ = R.get(v)
        if a_ is None:
            a_ = R[v] = (array("h"), array("f"), array("q"), array("q"))
        a_[0].append(ci); a_[1].append(r_); a_[2].append(xt); a_[3].append(t_)
    v2rows = {}
    for coin, K in D.items():
        if len(K) < 2000:
            continue
        P = V2.prep(K)
        P["_L"] = [LD.levels_at(P, i) if i >= 200 else [] for i in range(len(P["c"]))]
        P["_swl"], P["_swh"] = swing_lows_highs(P)
        P["_tarr"] = np.asarray(P["t"], dtype=np.int64)
        ci = COINS.index(coin)
        T, O1, H1, L1, C1 = raw[coin]
        raw15 = (np.asarray(T, dtype=np.int64), np.asarray(O1), np.asarray(H1), np.asarray(L1), np.asarray(C1))
        EV = events(P, raw15)
        o, h, l, c = (np.asarray(P[k]) for k in ("o", "h", "l", "c"))
        n = len(c)
        cnt = 0
        for stream, evs in EV.items():
            for ev in evs:
                long = ev["side"] == "L"
                for ent, fi, p, so, ext in fills(P, ev, stream, raw15):
                    if fi + 2 >= n:
                        continue
                    seg = slice(fi, min(n, fi + 72))
                    O_, H_, L_, C_ = o[seg], h[seg], l[seg], c[seg]
                    for sn in STOPS:
                        stp = stop_of(sn, long, P, fi, ev, ext)
                        if stp is None:
                            continue
                        for tn in TGTS:
                            tg = tgt_of(tn, long, p, stp, ev)
                            if tg is None:
                                continue
                            for mg in MGMT:
                                r = sim(long, p, stp, tg, ev["A"], O_, H_, L_, C_, mg, so)
                                if r is None:
                                    continue
                                v = f"{stream}|{ev['side']}|{ent}|{sn}|{tn}|{mg}"
                                put(v, ci, r[0], int(P["t"][min(n - 1, fi + r[2])]) + H, int(P["t"][ev["i"]]))
                                cnt += 1
        # levels_v2 families on this coin
        P["_set"] = V2.setups(P)
        if coin == "BTC":
            REG = V2.btc_regime(P)
            v2rows["_reg"] = REG
        v2rows[coin] = P["_set"], P
        print(coin, {k: len(v) for k, v in EV.items()}, "sims", cnt, f"{time.time() - t0:.0f}s", flush=True)
    REG = v2rows.pop("_reg")
    PP = {}
    for coin, (_s, P) in v2rows.items():
        PP[coin] = P
    for fam in V2.FAMS:
        for side in ("L", "S"):
            for flt in V2.FILTS:
                for tg in V2.TGTS:
                    for x in V2.run(PP, fam, side, flt, tg, REG):
                        put(f"v2:{fam}|{side}|{flt}|{tg}", COINS.index(x["coin"]), x["ret"], int(x["exit_t"]), int(x["t"]))
    import levels_study as LS                    # the EXACT desk replay (one position per coin, levels_study.run_variant)
    Dk = {c_: K for c_, K in D.items() if c_ in PP}
    for vv in ("range|L|sym", "range|S|sym"):
        for x in LS.run_variant(Dk, PP, vv):
            put(f"exact:{vv}", COINS.index(x["coin"]), x["ret"], int(x["exit_t"]), int(x["t"]))
    print(f"variants {len(R)}, tests counted {NTEST}", flush=True)
    res = {"ntest": NTEST, "z": Z, "universes": {}}
    NP = {v: (np.frombuffer(a_[0], dtype=np.int16), np.frombuffer(a_[1], dtype=np.float32).astype(float),
              np.frombuffer(a_[2], dtype=np.int64), np.frombuffer(a_[3], dtype=np.int64)) for v, a_ in R.items()}
    newi = np.array([COINS.index(c_) for c_ in COINS if c_ in NEW12], dtype=np.int16)
    for un, cs in UNIV.items():
        cidx = np.array([COINS.index(c_) for c_ in (cs or COINS) if c_ in COINS], dtype=np.int16)
        rows = {}
        for v, (ci_, r_, xt_, t_) in NP.items():
            m = np.isin(ci_, cidx)
            if m.sum() < 30:
                continue
            ma, mb = m & (t_ < MID), m & (t_ >= MID)
            row = {"is": summ(r_[ma], xt_[ma]), "oos": summ(r_[mb], xt_[mb])}
            if un == "all30":
                mn = np.isin(ci_, newi)
                row["new12"] = summ(r_[mn], xt_[mn])
                row["orig18"] = summ(r_[~mn], xt_[~mn])
            row["survives"] = bool(row["is"]["n"] >= 100 and row["oos"]["n"] >= 100 and row["is"]["mean"] > 0 and row["oos"]["mean"] > 0
                                   and (row["oos"].get("t") or 0) >= Z and (un != "all30" or row["new12"].get("mean", -1) > 0))
            if row["is"]["n"] >= 100 and row["oos"]["n"] >= 100:
                row["wf"] = round(min(row["is"]["mean"], row["oos"]["mean"]), 3)
            rows[v] = row
        ok = {v: r for v, r in rows.items() if r.get("wf") is not None}
        b1 = max(ok, key=lambda v: ok[v]["is"]["mean"]) if ok else None
        b2 = max(ok, key=lambda v: ok[v]["oos"]["mean"]) if ok else None
        top = sorted(ok, key=lambda v: -ok[v]["wf"])[:10]
        U = {"n_variants": len(rows), "survivors": [v for v, r in rows.items() if r["survives"]],
             "wf_pick_is": {"v": b1, "is": ok[b1]["is"], "oos": ok[b1]["oos"]} if b1 else None,
             "wf_pick_oos": {"v": b2, "oos": ok[b2]["oos"], "is": ok[b2]["is"]} if b2 else None,
             "top_wf": {v: {k: rows[v].get(k) for k in ("is", "oos", "new12", "wf")} for v in top},
             "desk_exact": {k: rows.get("exact:range|L|sym", {}).get(k) for k in ("is", "oos", "new12", "orig18")},
             "desk_events_a05_opp": {k: rows.get("desk|L|E_close|a0.5|opp|none", {}).get(k) for k in ("is", "oos", "new12")}}
        if cs and len(cs) == 1 and top:                                  # by year for the single-coin top picks
            U["by_year"] = {}
            for v in top[:3]:
                ci_, r_, xt_, t_ = NP[v]
                m = np.isin(ci_, cidx)
                yrs = np.array([time.gmtime(x / 1000).tm_year for x in t_[m]])
                U["by_year"][v] = {int(y): [int((yrs == y).sum()), round(float(r_[m][yrs == y].mean()), 3)] for y in np.unique(yrs)}
        res["universes"][un] = U
    def sh(x):
        return [x.get("n"), x.get("mean"), x.get("pf"), x.get("t")] if x else None
    summ_line = {un: {"n": U["n_variants"], "surv": len(U["survivors"]), "surv5": U["survivors"][:5],
                      "exact": [sh(U["desk_exact"].get("is")), sh(U["desk_exact"].get("oos"))],
                      "pick_is": [U["wf_pick_is"]["v"], sh(U["wf_pick_is"]["is"]), sh(U["wf_pick_is"]["oos"])] if U["wf_pick_is"] else None,
                      "pick_oos": [U["wf_pick_oos"]["v"], sh(U["wf_pick_oos"]["is"]), sh(U["wf_pick_oos"]["oos"])] if U["wf_pick_oos"] else None}
                 for un, U in res["universes"].items()}
    items = list(summ_line.items())
    print("::notice title=lab summary 1 ([n mean pf t] is / oos)::" + json.dumps(dict(items[:4]))[:4000])
    print("::notice title=lab summary 2::" + json.dumps(dict(items[4:]) | {"exact_all30_new12_orig18": [sh(res["universes"]["all30"]["desk_exact"].get("new12")),
                                                                                                       sh(res["universes"]["all30"]["desk_exact"].get("orig18"))]})[:4000])
    allrows = [(un, v, r) for un, U in res["universes"].items() for v, r in U["top_wf"].items()]
    obs, seen = [], set()
    for un, v, r in sorted(allrows, key=lambda x: -(x[2].get("wf") or -99)):
        if v not in seen:
            seen.add(v)
            obs.append({"universe": un, "v": v, **r})
        if len(obs) >= 5:
            break
    for un in ("BTC", "ETH"):
        tw = res["universes"][un]["top_wf"]
        if tw:
            v = next(iter(tw))
            obs.append({"universe": un, "v": v, **tw[v]})
    res["observation"] = obs
    json.dump(res, open(outp, "w"), ensure_ascii=False, indent=1, default=float)
    print("::notice title=lab observation::" + json.dumps(obs)[:4000])
    for un in ("BTC", "ETH", "SOL", "BES", "top5", "top10", "all30"):
        U = res["universes"][un]
        tw = {v: [sh(r.get("is")), sh(r.get("oos")), sh(r.get("new12"))] for v, r in list(U["top_wf"].items())[:6]}
        print(f"::notice title=lab top {un}::" + json.dumps({"top_wf": tw, "by_year": U.get("by_year")})[:4000])


# ── part b: the live desk's forward trades replayed on OKX candles ─────────────────────────────────────────────────────────────
def main_forward(outp):
    v = (LD.cfg() or {}).get("variant") or "range|L|sym"
    start = 1795125730600
    now = int(time.time() * 1000)
    trades = []
    for coin in LD.COINS:
        K = LD.hourly(coin, pages=12)
        if len(K) < 400:
            continue
        st_ = {"start": LD.START, "cash": LD.START, "open": [], "closed": [], "started": start, "last_bar": {}, "pending": []}
        first = next(j for j, k in enumerate(K) if k[0] >= start - H)
        for j in range(first, len(K)):
            if K[j][0] + H > now:
                break
            st_ = LD.step(st_, K[j][0] + H + 1, v, candles=lambda c_, K=K, j=j: K[:j + 1], coins=[coin])
        t_, o, h, l, c = ([k[x] for k in K] for x in range(5))
        for tr in st_["closed"] + st_["open"]:
            i0 = next(j for j in range(len(t_)) if t_[j] >= tr["opened_t"] - H)
            i1 = next((j for j in range(len(t_)) if t_[j] + H >= tr.get("closed_t", now)), len(t_) - 1)
            s = 1 if tr["side"] == "L" else -1
            R_ = abs(tr["entry"] - tr["stop0"])
            mfe = max((s * (x - tr["entry"]) for x in (h[i0:i1 + 1] if s > 0 else l[i0:i1 + 1])), default=0) / R_
            mae = min((s * (x - tr["entry"]) for x in (l[i0:i1 + 1] if s > 0 else h[i0:i1 + 1])), default=0) / R_
            swept = None
            if tr.get("why") == "стоп":
                swept = any((s * (x - tr["tgt"]) >= 0) for x in (h[i1 + 1:i1 + 73] if s > 0 else l[i1 + 1:i1 + 73]))
            trades.append({"coin": coin, "side": tr["side"], "opened": time.strftime("%d.%m %H:%M", time.gmtime(tr["opened_t"] / 1000)),
                           "entry": tr["entry"], "level": tr.get("level"), "stop_pct": round(R_ / tr["entry"] * 100, 2),
                           "tgt_pct": round(abs(tr["tgt"] - tr["entry"]) / tr["entry"] * 100, 2), "mfe_r": round(mfe, 2), "mae_r": round(mae, 2),
                           "exit": tr.get("why", "открыта"), "pct": tr.get("pct"), "swept": swept})
    trades.sort(key=lambda x: x["opened"])
    json.dump(trades, open(outp, "w"), ensure_ascii=False, indent=1)
    cl = [x for x in trades if x["pct"] is not None]
    print("::notice title=fwd summary::" + json.dumps({"variant": v, "trades": len(trades), "closed": len(cl),
                                                        "mean": round(st.mean([x["pct"] for x in cl]), 3) if cl else None,
                                                        "win": round(sum(x["pct"] > 0 for x in cl) / len(cl), 3) if cl else None,
                                                        "stops": sum(x["exit"] == "стоп" for x in cl), "swept": sum(bool(x["swept"]) for x in cl)}))
    for q in range(0, len(trades), 12):
        print(f"::notice title=fwd trades {q // 12 + 1}::" + json.dumps(trades[q:q + 12], ensure_ascii=False)[:4000])


if __name__ == "__main__":
    if sys.argv[1] == "forward":
        main_forward(sys.argv[2])
    else:
        main_run(*sys.argv[2:5])
