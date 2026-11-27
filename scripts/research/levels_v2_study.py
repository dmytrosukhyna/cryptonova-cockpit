"""Levels v2 (owner 27.11.2026 07:48 / 07:51 / 07:52): false breakouts, retests, bounces, Quasimodo and their combos on 30 coins.
RESEARCH ONLY — nothing here trades; desks change only by what passes (paper), the real account never.

PRE-REGISTERED 27.11.2026 ~06:30 UTC, before any run of this file (committed first, then run):
Data. lab-data-core18 (alt100.npz: Binance USDT-M 15m, 2022-11-20 → 2026-11-04, 18 coins) + 12 NEW coins fetched from the same archive
  (data.binance.vision 15m klines) for the same window, fixed before the run: ATOM INJ ETC FIL UNI AAVE XLM HBAR FET ICP TIA SEI
  (the liquid USDT perps not in the 18; TIA / SEI list in 2023 → shorter history). H1 bars from full 15m quads, H4 / D1 from H1.
  Halves: IS before 2024-11-12, OOS from then. Reported on all 30, on the original 18 and on the new 12 (the out-of-sample coins).
Levels (known at the close of H1 bar i, only confirmed swings): swing highs / lows = fractals on H4 (2 bars each side) and on D1
  (2 days each side), known when the 2nd bar after closes; the last 60 H4 and 30 D1 swings; points closer than 0.5 × ATR(H4, 14)
  are one level (their mean). Strength = the number of swings in the level + 1 when it holds both an H4 and a D1 swing. No round
  numbers. A1 = ATR(H1, 14), A4 = ATR(H4, 14) at the last H4 bar closed.
Families (long described; short = the mirror; signal at the close of H1 bar i, entry at the open of bar i + 1):
  fb1 / fb3  sweep & reclaim: the close before bar j was above level Lv, bar j's low went below Lv − 0.05·A1 but not below Lv − 1·A4,
             and within N bars (N = 1: the same bar, N = 3: j … j+2) a close is back above Lv (closes in between ≤ Lv) → long;
             stop = the sweep low − 0.1·A1. Volume flag: the max H1 volume of j … i > the 20-bar mean before j.
  rt         breakout + retest: an H4 close above Lv after an H4 close ≤ Lv; within the next 24 H1 bars a bar's low ≤ Lv + 0.15·A1 that
             closes above Lv, up (close > open) and in the upper half of its range → long; cancelled by an H1 close < Lv − 0.25·A1
             first. Stop = min(the bar's low, Lv) − 0.25·A1. Volume flag: the breakout H4 bar's volume > the mean of the 20 before.
  bn         bounce (baseline): Lv with strength ≥ 3 below, the previous close above Lv, low within Lv ± 0.2·A1, close above Lv, up and
             upper half → long; stop = min(low, Lv) − 0.5·A1. Volume flag: the bar's volume > its 20-bar mean.
  fbqm       combo: an fb3 signal while a same-side H4 Quasimodo (scripts/research/levels_qm_study.py rules) broke in the last 120 H1 bars.
  The strongest qualifying level is used when several qualify; one open trade per coin per variant.
Filters: none | reg (BTC H4 EMA20 > EMA50 at the last H4 bar closed for longs, < for shorts) | vol (the family's volume flag) |
  str (strength ≥ 3; for bn ≥ 4).
Targets: 2R | 3R | opp (the nearest level of strength ≥ 2 on the other side ≥ 1R away, else 2R). Max hold 120 H1 bars. A bar touching
  the stop and the target counts as the stop. Risk must be 0.2 %–8 % of the entry. Costs as levels_desk (0.10 % + 0.01 % / 8 h).
Grid = 5 families × 2 sides × 3 targets × 4 filters = 120 variants. PASS (Bonferroni 0.05 / 120 two-sided → z ≥ 3.53, the honest
  count — QM 4h long 3R was counted in its own study of 26.11): on all 30 coins IS mean > 0 AND OOS mean > 0 AND OOS daily t ≥ 3.53
  AND OOS n ≥ 100 AND the OOS mean above all 100 random-entry means (same coin, side, % stop / target, max hold, random OOS bars).
  PILOT-ELIGIBLE (owner 07:51) = PASS and the mean on the new 12 coins > 0.
Not testable on history: «retest confirmed by a whale trade» (whale trades exist for 3 months only) — forward paper only.
Diagnostic (not in the grid): bn0 = bounce on a touch of ANY level without the candle / strength rules, stop Lv − 0.5·A1 (the shape of
  the current «Уровни» desk) — the share of its stops after which the price came back to the target within 72 bars («swept out»).
Risk sheet (owner 07:52) for every PASS variant and for QM 4h long 3R: $10 000, ≤ 5 positions, a position's notional ≤ 1× equity,
  risk 0.5 / 1 / 1.5 / 2 % of equity at the stop and the ladder 1 → 1.5 → 2 % (cnq/execution/risk_ladder.py) — return and max drawdown
  per half (each half from $10 000).
Usage: levels_v2_study.py fetch NEW12.npz  |  levels_v2_study.py run core18.npz NEW12.npz OUT.json"""
import bisect, json, math, os, random, statistics as st, sys, time
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cnq.agents import levels_desk as LD  # noqa: E402
from cnq.execution import risk_ladder as RL  # noqa: E402

H, DAY = LD.H, LD.DAY
H4 = 4 * H
MID = 1_731_369_600_000
NEW12 = "ATOM INJ ETC FIL UNI AAVE XLM HBAR FET ICP TIA SEI".split()
Z = 3.53
MAXB = 120
FAMS = ("fb1", "fb3", "rt", "bn", "fbqm")
FILTS = ("none", "reg", "vol", "str")
TGTS = ("2R", "3R", "opp")
RNG = random.Random(20261127)


def fetch(out):
    import fetch_alt100 as FA
    d = np.load(os.environ.get("CORE18", "alt100.npz"))
    times = d["times"].astype(np.int64)
    s0, s1 = int(times[0]), int(times[-1]) + 900_000
    A = {k: np.full((len(NEW12), len(times)), np.nan, dtype=np.float32) for k in ("open", "high", "low", "close", "volume_quote")}
    for k, c in enumerate(NEW12):
        kl = FA.kl_series(f"{c}USDT", "klines", s0, s1)
        if kl:
            ts = np.fromiter(kl, dtype=np.int64)
            v = np.array(list(kl.values()))
            ii = (ts - s0) // 900_000
            ok = (ii >= 0) & (ii < len(times))
            for j, col in enumerate(("open", "high", "low", "close", "volume_quote")):
                A[col][k, ii[ok]] = v[ok, j]
        print(f"::notice title=v2 data {c}::15m bars {len(kl)}", flush=True)
    np.savez_compressed(out, syms=np.array(NEW12), times=times, **A)


# ── data ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def load(path):
    import levels_qm_study as Q
    D, _raw = Q.load(path)
    return D


def ema(xs, n):
    out, e = [], None
    for x in xs:
        e = x if e is None else e + (x - e) * 2 / (n + 1)
        out.append(e)
    return out


def prep(K):
    import levels_qm_study as Q
    t, o, h, l, c, v = ([r[j] for r in K] for j in range(6))
    P = LD.prep(t, o, h, l, c, v)
    n = len(c)
    g4 = LD.group(t, o, h, l, c, v, H4)
    a4 = LD.atr(g4[2], g4[3], g4[4])
    P["g4"] = g4
    last4 = g4[6]
    # per H1 index: index of the last COMPLETED H4 bar (its last H1 index ≤ i)
    P["k4"] = [bisect.bisect_right(last4, i) - 1 for i in range(n)]
    P["a4"] = [a4[k] if k >= 0 else None for k in P["k4"]]
    gd = LD.group(t, o, h, l, c, v, DAY)
    piv = []
    for g, kk, tf in ((g4, 2, "4h"), (gd, 2, "1d")):
        hi, lo = LD.fractals(g[2], g[3], kk)
        lastg = g[6]
        piv += [(lastg[j + kk], p, tf) for j, p in hi + lo if j + kk < len(lastg)]
    piv.sort()
    ev_idx, ev_lv = [], []
    cur4, curd = [], []
    for k, p, tf in piv:
        (cur4 if tf == "4h" else curd).append(p)
        cur4, curd = cur4[-60:], curd[-30:]
        a = P["a4"][k] or (P["atr"][k] or 0) * 2
        if not a:
            continue
        pts = sorted([(x, "4h") for x in cur4] + [(x, "1d") for x in curd])
        cl = []
        for x, ty in pts:
            if cl and x - cl[-1][-1][0] < 0.5 * a:
                cl[-1].append((x, ty))
            else:
                cl.append([(x, ty)])
        lv = [(sum(x for x, _ in g_) / len(g_), len(g_) + (1 if {ty for _, ty in g_} == {"4h", "1d"} else 0)) for g_ in cl]
        if ev_idx and ev_idx[-1] == k:
            ev_lv[-1] = lv
        else:
            ev_idx.append(k)
            ev_lv.append(lv)
    P["ev_idx"], P["ev_lv"] = ev_idx, ev_lv
    P["_qm"] = {"4h": Q.qm_setups(P, "4h")}
    return P


def levels(P, i):
    q = bisect.bisect_right(P["ev_idx"], i) - 1
    return P["ev_lv"][q] if q >= 0 else []


def btc_regime(P):
    g4 = P["g4"]
    e20, e50 = ema(g4[4], 20), ema(g4[4], 50)
    return {int(g4[0][k]): int(e20[k] > e50[k]) - int(e20[k] < e50[k]) for k in range(len(g4[0])) if k >= 50}


# ── setups ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def setups(P):
    """{family: [(i, side, Lv, stop, strength, vol_ok)]}"""
    o, h, l, c, v, vm, a1 = P["o"], P["h"], P["l"], P["c"], P["v"], P["vmean"], P["atr"]
    n = len(c)
    out = {f: [] for f in FAMS + ("bn0",)}
    qm_l = sorted(b for b, s, *_ in P["_qm"]["4h"] if s == "L")
    qm_s = sorted(b for b, s, *_ in P["_qm"]["4h"] if s == "S")
    pend = []                                       # rt: [side, Lv, strength, vol_ok, expiry]
    g4, k4 = P["g4"], P["k4"]
    v4 = g4[5]
    for i in range(300, n - 1):
        A, A4 = a1[i], P["a4"][i]
        if not A or not A4:
            continue
        L = levels(P, i)
        if not L:
            continue
        # fb (N = 1, 3)
        for N, fam in ((1, "fb1"), (3, "fb3")):
            best = None
            for Lv, s_ in L:
                if abs(Lv - c[i]) > 3 * A4:
                    continue
                for side in ("L", "S"):
                    for j in range(i - N + 1, i + 1):
                        if j < 1:
                            continue
                        if side == "L":
                            ok = (c[j - 1] > Lv and Lv - A4 <= l[j] <= Lv - 0.05 * A and c[i] > Lv and all(c[x] <= Lv for x in range(j, i))
                                  and min(l[j:i + 1]) >= Lv - A4)
                            ext = min(l[j:i + 1]) if ok else None
                        else:
                            ok = (c[j - 1] < Lv and Lv + 0.05 * A <= h[j] <= Lv + A4 and c[i] < Lv and all(c[x] >= Lv for x in range(j, i))
                                  and max(h[j:i + 1]) <= Lv + A4)
                            ext = max(h[j:i + 1]) if ok else None
                        if ok:
                            vok = bool(vm[j - 1] and max(v[j:i + 1]) > vm[j - 1])
                            stop = ext - 0.1 * A if side == "L" else ext + 0.1 * A
                            if best is None or s_ > best[4]:
                                best = (i, side, Lv, stop, s_, vok)
                            break
            if best:
                out[fam].append(best)
                if fam == "fb3":
                    qs = qm_l if best[1] == "L" else qm_s
                    q = bisect.bisect_right(qs, i) - 1
                    if q >= 0 and i - qs[q] <= 120:
                        out["fbqm"].append(best)
        # rt: breakout registered at the last H1 of an H4 bar
        k = k4[i]
        if k >= 21 and g4[6][k] == i:
            c4, p4 = g4[4][k], g4[4][k - 1]
            vok = v4[k] > sum(v4[k - 20:k]) / 20
            for Lv, s_ in L:
                if p4 <= Lv < c4:
                    pend.append(["L", Lv, s_, vok, i + 24])
                elif p4 >= Lv > c4:
                    pend.append(["S", Lv, s_, vok, i + 24])
        keep = []
        fired = None
        for pd in pend:
            side, Lv, s_, vok, exp = pd
            if i > exp:
                continue
            if g4[6][k] == i and exp == i + 24:
                keep.append(pd)                      # registered on this bar: the retest starts with the next bar
                continue
            if (side == "L" and c[i] < Lv - 0.25 * A) or (side == "S" and c[i] > Lv + 0.25 * A):
                continue
            rng = h[i] - l[i]
            if side == "L" and l[i] <= Lv + 0.15 * A and c[i] > Lv and c[i] > o[i] and rng and c[i] >= l[i] + 0.5 * rng:
                cand = (i, "L", Lv, min(l[i], Lv) - 0.25 * A, s_, vok)
            elif side == "S" and h[i] >= Lv - 0.15 * A and c[i] < Lv and c[i] < o[i] and rng and c[i] <= h[i] - 0.5 * rng:
                cand = (i, "S", Lv, max(h[i], Lv) + 0.25 * A, s_, vok)
            else:
                keep.append(pd)
                continue
            if fired is None or cand[4] > fired[4]:
                fired = cand
        pend = keep
        if fired:
            out["rt"].append(fired)
        # bn / bn0
        rng = h[i] - l[i]
        vok = bool(vm[i - 1] and v[i] > vm[i - 1])
        bb, b0 = None, None
        for Lv, s_ in L:
            if c[i - 1] > Lv and Lv - 0.2 * A <= l[i] <= Lv + 0.2 * A and c[i] > Lv:
                if b0 is None or s_ > b0[4]:
                    b0 = (i, "L", Lv, Lv - 0.5 * A, s_, vok)
                if s_ >= 3 and c[i] > o[i] and rng and c[i] >= l[i] + 0.5 * rng and (bb is None or s_ > bb[4]):
                    bb = (i, "L", Lv, min(l[i], Lv) - 0.5 * A, s_, vok)
            if c[i - 1] < Lv and Lv - 0.2 * A <= h[i] <= Lv + 0.2 * A and c[i] < Lv:
                if b0 is None or s_ > b0[4]:
                    b0 = (i, "S", Lv, Lv + 0.5 * A, s_, vok)
                if s_ >= 3 and c[i] < o[i] and rng and c[i] <= h[i] - 0.5 * rng and (bb is None or s_ > bb[4]):
                    bb = (i, "S", Lv, max(h[i], Lv) + 0.5 * A, s_, vok)
        if bb:
            out["bn"].append(bb)
        if b0:
            out["bn0"].append(b0)
    return out


def target(P, i, side, entry, risk, tg):
    if tg == "2R":
        return entry + 2 * risk if side == "L" else entry - 2 * risk
    if tg == "3R":
        return entry + 3 * risk if side == "L" else entry - 3 * risk
    L = levels(P, i)
    if side == "L":
        xs = sorted(x for x, s_ in L if s_ >= 2 and x >= entry + risk)
        return xs[0] if xs else entry + 2 * risk
    xs = sorted((x for x, s_ in L if s_ >= 2 and x <= entry - risk), reverse=True)
    return xs[0] if xs else entry - 2 * risk


def run(PP, fam, side, flt, tg, REG, diag=False):
    trades = []
    for coin, P in PP.items():
        o, h, l, c, t = P["o"], P["h"], P["l"], P["c"], P["t"]
        n = len(c)
        busy = -1
        for (i, sd, Lv, stop, s_, vok) in P["_set"][fam]:
            if sd != side or i <= busy or i + 1 >= n:
                continue
            if flt == "vol" and not vok:
                continue
            if flt == "str" and s_ < (4 if fam == "bn" else 3):
                continue
            if flt == "reg":
                r = REG.get(t[i] - t[i] % H4 - H4)
                if r is None or (side == "L" and r <= 0) or (side == "S" and r >= 0):
                    continue
            e = o[i + 1]
            risk = (e - stop) if side == "L" else (stop - e)
            if risk <= 0 or not (0.002 <= risk / e <= 0.08):
                continue
            tgt = target(P, i, side, e, risk, tg)
            tr = {"side": side, "entry": e, "stop": stop, "tgt": tgt, "risk": risk, "bars": 0, "maxb": MAXB}
            k = i
            res = None
            while res is None and k + 1 < n:
                k += 1
                res = LD.step_trade(tr, (o[k], h[k], l[k], c[k]), P["atr"][k])
            if res is None:
                break
            px, why = res
            busy = k
            x = {"coin": coin, "t": t[i + 1], "exit_t": t[k] + H, "ret": LD.ret_pct(side, e, px, tr["bars"]), "why": why, "side": side,
                 "stop_pct": abs(stop / e - 1), "tgt_pct": abs(tgt / e - 1)}
            if diag and why == "стоп":                                     # did the price come back to the target within 72 bars?
                x["swept"] = any((h[q] >= tgt) if side == "L" else (l[q] <= tgt) for q in range(k + 1, min(n, k + 73)))
            trades.append(x)
    return trades


def tstat(xs):
    if len(xs) < 3:
        return None
    sd = st.stdev(xs)
    return round(st.mean(xs) / (sd / math.sqrt(len(xs))), 2) if sd else None


def summ(tr):
    if not tr:
        return {"n": 0}
    xs = [x["ret"] for x in tr]
    days = defaultdict(list)
    for x in tr:
        days[x["exit_t"] // DAY].append(x["ret"])
    gp, gl = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(st.mean(xs), 3), "win": round(sum(x > 0 for x in xs) / len(xs), 3), "pf": round(gp / gl, 2) if gl else None,
            "t": tstat(xs), "t_daily": tstat([st.mean(v) for v in days.values()]), "stopped": round(sum(x["why"] == "стоп" for x in tr) / len(tr), 3)}


def null_means(PP, oos, reps=100):
    by = defaultdict(list)
    for x in oos:
        by[x["coin"]].append(x)
    out = []
    for _ in range(reps):
        rs = []
        for coin, xs in by.items():
            P = PP[coin]
            if "_oosidx" not in P:
                P["_oosidx"] = [i for i in range(len(P["t"])) if P["t"][i] >= MID]
            idx = P["_oosidx"]
            if len(idx) < MAXB + 10:
                continue
            for x in xs:
                i = RNG.choice(idx[:-MAXB - 2])
                e = P["o"][i]
                sd = x["side"]
                stop = e * (1 - x["stop_pct"]) if sd == "L" else e * (1 + x["stop_pct"])
                tgt = e * (1 + x["tgt_pct"]) if sd == "L" else e * (1 - x["tgt_pct"])
                tr = {"side": sd, "entry": e, "stop": stop, "tgt": tgt, "risk": abs(e - stop), "bars": 0, "maxb": MAXB}
                k, r = i - 1, None
                while r is None:
                    k += 1
                    r = LD.step_trade(tr, (P["o"][k], P["h"][k], P["l"][k], P["c"][k]), P["atr"][k])
                rs.append(LD.ret_pct(sd, e, r[0], tr["bars"]))
        out.append(st.mean(rs) if rs else 0.0)
    return out


def book(trades, risk, cap=5, start=10_000.0):
    """risk = 0.005 … 0.02 or "ladder" → {ret %, max dd %, taken, skipped}"""
    E, peak, dd, openp, taken, skip = start, start, 0.0, [], 0, 0
    S, hist = {}, []
    for x in sorted(trades, key=lambda x: x["t"]):
        for p in sorted([p for p in openp if p[0] <= x["t"]]):
            E += p[1]
            hist.append((p[0], p[1]))
            peak = max(peak, E)
            dd = max(dd, (peak - E) / peak)
            if risk == "ladder":
                RL.decide(S, hist, 0.0, start, p[0])
            openp.remove(p)
        if len(openp) >= cap:
            skip += 1
            continue
        r = RL.level(S) if risk == "ladder" else risk
        notional = min(r * E / max(x["stop_pct"], 1e-4), E)
        openp.append((x["exit_t"], notional * x["ret"] / 100))
        taken += 1
    for p in sorted(openp):
        E += p[1]
        peak = max(peak, E)
        dd = max(dd, (peak - E) / peak)
    return {"ret_pct": round((E / start - 1) * 100, 1), "max_dd_pct": round(dd * 100, 1), "taken": taken, "skipped": skip}


def main():
    if sys.argv[1] == "fetch":
        return fetch(sys.argv[2])
    core, new, outp = sys.argv[2:5]
    D = load(core)
    D.update(load(new))
    t0 = time.time()
    PP = {}
    for coin, K in D.items():
        if len(K) < 2000:
            print(f"::warning title=v2 data::{coin} only {len(K)} H1 bars — skipped")
            continue
        P = prep(K)
        P["_set"] = setups(P)
        PP[coin] = P
        print(coin, len(K), {f: len(v) for f, v in P["_set"].items()}, f"{time.time() - t0:.0f}s", flush=True)
    REG = btc_regime(PP["BTC"])
    sets = {"all30": set(PP), "orig18": set(PP) - set(NEW12), "new12": set(PP) & set(NEW12)}
    print(f"::notice title=v2 coins::{len(PP)} coins: {sorted(PP)}")
    res, rows = {"variants": {}, "z": Z, "grid": len(FAMS) * 2 * len(TGTS) * len(FILTS)}, []
    for fam in FAMS:
        for side in ("L", "S"):
            for flt in FILTS:
                for tg in TGTS:
                    v = f"{fam}|{side}|{flt}|{tg}"
                    tr = run(PP, fam, side, flt, tg, REG)
                    row = {}
                    for sn, cs in sets.items():
                        xs = [x for x in tr if x["coin"] in cs]
                        row[sn] = {"is": summ([x for x in xs if x["t"] < MID]), "oos": summ([x for x in xs if x["t"] >= MID]), "all": summ(xs)}
                    a = row["all30"]
                    pre = (a["is"].get("mean", -1) > 0 and a["oos"].get("mean", -1) > 0 and (a["oos"].get("t_daily") or 0) >= Z and a["oos"]["n"] >= 100)
                    if pre:
                        nm = null_means(PP, [x for x in tr if x["t"] >= MID])
                        row["null"] = {"max": round(max(nm), 3), "mean": round(st.mean(nm), 3)}
                        row["passed"] = a["oos"]["mean"] > max(nm)
                    else:
                        row["passed"] = False
                    row["pilot_eligible"] = bool(row["passed"] and row["new12"]["all"].get("mean", -1) > 0)
                    if row["passed"]:
                        row["risk"] = {h_: {str(r): book([x for x in tr if (x["t"] < MID) == (h_ == "is")], r) for r in (0.005, 0.01, 0.015, 0.02, "ladder")}
                                       for h_ in ("is", "oos")}
                    res["variants"][v] = row
                    rows.append((v, row))
                    print(v, json.dumps({k: row["all30"][k] for k in ("is", "oos")}), row["passed"], flush=True)
    # QM 4h long 3R risk sheet (rules of levels_qm_study, same coins)
    import levels_qm_study as Q
    qt = Q.run_qm(PP, "4h", "L", "3R")
    for x in qt:
        x["side"] = "L"
    res["qm4hL3R"] = {sn: {"is": summ([x for x in qt if x["coin"] in cs and x["t"] < MID]), "oos": summ([x for x in qt if x["coin"] in cs and x["t"] >= MID])}
                      for sn, cs in sets.items()}
    res["qm4hL3R"]["risk"] = {h_: {str(r): book([x for x in qt if (x["t"] < MID) == (h_ == "is")], r) for r in (0.005, 0.01, 0.015, 0.02, "ladder")}
                              for h_ in ("is", "oos")}
    # diagnostic bn0 (the shape of the current «Уровни» desk)
    dg = run(PP, "bn0", "L", "none", "opp", REG, diag=True) + run(PP, "bn0", "S", "none", "opp", REG, diag=True)
    stops = [x for x in dg if x["why"] == "стоп"]
    res["diag_bn0"] = {"all": summ(dg), "is": summ([x for x in dg if x["t"] < MID]), "oos": summ([x for x in dg if x["t"] >= MID]),
                       "stops": len(stops), "swept_share": round(sum(x.get("swept", False) for x in stops) / len(stops), 3) if stops else None,
                       "stop_atr_med": round(st.median(x["stop_pct"] for x in dg) * 100, 2) if dg else None}
    json.dump(res, open(outp, "w"), ensure_ascii=False, indent=1, default=float)
    # annotations: passing ones, then the best 30 by OOS t (compact), the family table, QM, diag
    passed = [v for v, r in rows if r["passed"]]
    print("::notice title=v2 pass::" + json.dumps({"passed": passed, "pilot": [v for v, r in rows if r["pilot_eligible"]], "grid": res["grid"]}))
    def cmp(r, sn="all30"):
        return [r[sn]["is"].get("n"), r[sn]["is"].get("mean"), r[sn]["is"].get("win"), r[sn]["is"].get("pf"), r[sn]["is"].get("t_daily"),
                r[sn]["oos"].get("n"), r[sn]["oos"].get("mean"), r[sn]["oos"].get("win"), r[sn]["oos"].get("pf"), r[sn]["oos"].get("t_daily")]
    best = sorted(rows, key=lambda vr: -((vr[1]["all30"]["oos"].get("t_daily") or -99)))
    for q in range(0, 40, 20):
        print(f"::notice title=v2 top {q // 20 + 1} (is n mean win pf t | oos n mean win pf t)::" +
              json.dumps({v: cmp(r) for v, r in best[q:q + 20]})[:4000])
    fam_best = {}
    for fam in FAMS:
        for side in ("L", "S"):
            v, r = max(((v, r) for v, r in rows if v.startswith(f"{fam}|{side}|")), key=lambda vr: vr[1]["all30"]["oos"].get("t_daily") or -99)
            fam_best[v] = {"all30": cmp(r), "orig18": cmp(r, "orig18"), "new12": cmp(r, "new12"), "null": r.get("null"), "passed": r["passed"]}
    print("::notice title=v2 family best::" + json.dumps(fam_best)[:4000])
    base = {f"{fam}|{side}": cmp(res["variants"][f"{fam}|{side}|none|2R"]) for fam in FAMS for side in ("L", "S")}
    print("::notice title=v2 family plain (none, 2R)::" + json.dumps(base)[:4000])
    print("::notice title=v2 qm4hL3R::" + json.dumps(res["qm4hL3R"])[:4000])
    print("::notice title=v2 diag bn0::" + json.dumps(res["diag_bn0"]))
    for v in passed[:3]:
        print(f"::notice title=v2 risk {v}::" + json.dumps({"risk": res["variants"][v]["risk"], "null": res["variants"][v]["null"],
                                                         "orig18": cmp(res["variants"][v], "orig18"), "new12": cmp(res["variants"][v], "new12")})[:4000])


if __name__ == "__main__":
    main()
