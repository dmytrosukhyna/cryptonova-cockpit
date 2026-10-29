"""Backlog #53 (29.10.2026): do «different clusters» of the risk layer still differ on crash days?

Public idea: Mallela & Leonelli 2026 (arXiv 2606.16840) — the lower tails of 13 large coins are almost fully dependent;
ordinary models underestimate the chance of a joint crash about 8-fold.
Our risk layer (cnq/risk) caps one «cluster of similar coins» (30-day daily-return correlation > 0.8 for every pair,
greedy complete linkage) at 50 % of gross. If coins from DIFFERENT clusters crash together anyway, the cap gives less
protection than it looks.

Fixed BEFORE the run (one descriptive test + one forward check, no tuning):
  Crash day = BTCUSDT perp close-to-close ≤ −8 %, 2020-01 → 2026-09.
  Universe that day = every Binance USDT-M perp with 20-day average turnover ≥ $20M and 30 clean days before the day,
  delisted included. Clusters are built exactly like cnq/risk on the 30 days ENDING THE DAY BEFORE (ρ > 0.8, order by
  turnover, BTC first). «Other clusters» = every coin outside BTC's cluster.
  A) share of «other cluster» coins that fell that day, and that fell ≥ 5 %. Control: ordinary BTC down days −2…−4 %.
     Decision rule: if the median share that fell on crash days is > 80 % → «in stress all alts are one cluster».
  B) the cost of acting on it. The only real-time trigger the layer could use is «BTC is already −5 % below yesterday's
     close» (daily low ≤ −5 %). Treating all alts as one cluster then mostly BLOCKS NEW LONG ENTRIES that day. Forward
     check: equal-weight basket of the liquid universe bought at the close of trigger days, return over the next 1 and
     5 days, against all days (t by dates). If trigger-day entries do better than average, blocking them costs money.
  Also reported: how many clusters the universe had the day before (is the cap even meaningful), false alarms of the
  trigger (low ≤ −5 % but close > −8 %).
Data: data.binance.vision 1d klines, every USDT-M perp, delisted included. Public data only, no keys, no orders.
"""
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voltbreak_all_study as vb  # noqa: E402
import families36_study as f36  # noqa: E402

LIQ = 20e6
RHO = 0.80
CORR = 30
CRASH = -0.08
TRIG = -0.05


def iso(d):
    return str(np.datetime64(int(d), "D"))


def clusters(Rw, order):
    """Rw: (k × 30) returns; order: indices sorted by weight. Greedy complete linkage, like cnq/risk/portfolio.clusters."""
    C = np.corrcoef(Rw)
    C = np.nan_to_num(C, nan=-1.0)
    done = np.zeros(len(order), bool)
    out = []
    pos = {k: j for j, k in enumerate(order)}
    for s in order:
        if done[pos[s]]:
            continue
        mem = [s]
        for t in order:
            if done[pos[t]] or t in mem:
                continue
            if all(C[t, m] > RHO for m in mem):
                mem.append(t)
        for m in mem:
            done[pos[m]] = True
        out.append(mem)
    return out


def main():
    t0 = time.time()
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [f36.prep(x) for x in ex.map(f36.load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    vb.note("p53 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
    d0 = min(int(x["d"][0]) for x in data)
    m = live_end - d0 + 1
    n = len(data)
    R = np.full((n, m), np.nan); Q = np.zeros((n, m)); LOW = np.full((n, m), np.nan)
    for k, x in enumerate(data):
        idx = x["d"] - d0
        c = x["c"]
        r = np.r_[np.nan, c[1:] / c[:-1] - 1]
        r[~x["cont"]] = np.nan
        R[k, idx] = r
        lr = np.r_[np.nan, x["l"][1:] / c[:-1] - 1]
        lr[~x["cont"]] = np.nan
        LOW[k, idx] = lr
        Q[k, idx] = np.nan_to_num(x["q20"])
    btc = next(k for k, x in enumerate(data) if x["sym"] == "BTCUSDT")
    rb, lb = R[btc], LOW[btc]

    def universe(t):
        if t < CORR + 1:
            return np.array([], int)
        w = R[:, t - CORR:t]
        ok = (Q[:, t] >= LIQ) & np.isfinite(w).all(axis=1) & np.isfinite(R[:, t])
        return np.flatnonzero(ok)

    def day_stats(t):
        u = universe(t)
        if btc not in u or len(u) < 10:
            return None
        order = sorted(u, key=lambda k: (k != btc, -Q[k, t]))
        cl = clusters(R[:, t - CORR:t], order)
        bcl = next(c for c in cl if btc in c)
        other = np.array([k for k in u if k not in bcl])
        if len(other) == 0:
            return None
        ro = R[other, t]
        big = max(len(c) for c in cl)
        return {"n": len(u), "ncl": len(cl), "big": big / len(u), "btc_cl": len(bcl) / len(u), "nother": len(other),
                "fell": float((ro < 0).mean()), "fell5": float((ro <= -0.05).mean()), "med": float(np.median(ro)),
                "beat_btc": float((ro > rb[t]).mean())}

    days = np.arange(m)
    crash = [t for t in days if np.isfinite(rb[t]) and rb[t] <= CRASH]
    ctrl = [t for t in days if np.isfinite(rb[t]) and -0.04 <= rb[t] <= -0.02]
    rows = [(t, day_stats(t)) for t in crash]
    rows = [(t, s) for t, s in rows if s]
    cr = [(t, day_stats(t)) for t in ctrl]
    cr = [s for _, s in cr if s]
    lines = [f"{iso(d0 + t)} BTC {rb[t] * 100:+.1f}%: {s['n']} coins, {s['ncl']} clusters (biggest {s['big'] * 100:.0f}%), "
             f"outside BTC's cluster {s['nother']}: fell {s['fell'] * 100:.0f}%, ≤−5% {s['fell5'] * 100:.0f}%, "
             f"median {s['med'] * 100:+.1f}%" for t, s in rows]
    for j in range(0, max(len(lines), 1), 12):
        vb.note(f"p53 · crash days {j // 12 + 1}", f"{len(rows)} of {len(crash)} BTC ≤ −8% days with a universe:\n"
                + "\n".join(lines[j:j + 12]))
    if rows:
        fell = np.array([s["fell"] for _, s in rows]); fell5 = np.array([s["fell5"] for _, s in rows])
        cf = np.array([s["fell"] for s in cr]); cf5 = np.array([s["fell5"] for s in cr])
        verdict = "YES — one cluster in stress" if np.median(fell) > 0.80 else "no"
        vb.note("p53 · A verdict", f"{verdict}: crash days ({len(rows)}) median share of «other cluster» coins that fell "
                f"{np.median(fell) * 100:.0f}% (min {fell.min() * 100:.0f}%), fell ≤−5% median {np.median(fell5) * 100:.0f}%; "
                f"control BTC −2…−4% days ({len(cr)}): fell {np.median(cf) * 100:.0f}%, ≤−5% {np.median(cf5) * 100:.0f}%; "
                f"clusters the day before: crash median {int(np.median([s['ncl'] for _, s in rows]))} "
                f"(BTC's cluster {np.median([s['btc_cl'] for _, s in rows]) * 100:.0f}% of coins), control median "
                f"{int(np.median([s['ncl'] for s in cr]))} (BTC's cluster {np.median([s['btc_cl'] for s in cr]) * 100:.0f}%)")

    # B) forward check of the real-time trigger
    fw1 = np.full(m, np.nan); fw5 = np.full(m, np.nan)
    for t in range(CORR + 1, m - 5):
        u = np.flatnonzero((Q[:, t] >= LIQ) & np.isfinite(R[:, t]))
        u = u[u != btc]
        if len(u) < 10:
            continue
        nxt = R[np.ix_(u, range(t + 1, t + 6))]
        ok = np.isfinite(nxt).all(axis=1)
        if ok.sum() < 10:
            continue
        g = np.prod(1 + nxt[ok], axis=1)
        fw1[t] = float(np.mean(nxt[ok, 0]))
        fw5[t] = float(np.mean(g - 1))
    trig = np.array([t for t in days if np.isfinite(lb[t]) and lb[t] <= TRIG and np.isfinite(fw5[t])])
    allv = np.flatnonzero(np.isfinite(fw5))
    false_al = sum(1 for t in trig if rb[t] > CRASH)

    def tt(a, b):
        a, b = a[np.isfinite(a)], b[np.isfinite(b)]
        if len(a) < 3:
            return 0.0
        return (a.mean() - b.mean()) / math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    h = len(trig) // 2
    halves = [f"{fw5[trig[:h]].mean() * 100:+.2f}%", f"{fw5[trig[h:]].mean() * 100:+.2f}%"] if h else []
    vb.note("p53 · B trigger", f"days with BTC low ≤ −5% below yesterday's close: {len(trig)} ({false_al} of them closed above "
            f"−8%); alt basket bought at that close: next day {np.nanmean(fw1[trig]) * 100:+.2f}% vs all days "
            f"{np.nanmean(fw1[allv]) * 100:+.2f}% (t {tt(fw1[trig], fw1[allv]):.2f}); next 5 days {np.nanmean(fw5[trig]) * 100:+.2f}% "
            f"vs {np.nanmean(fw5[allv]) * 100:+.2f}% (t {tt(fw5[trig], fw5[allv]):.2f}, overlapping windows → t too high); "
            f"halves of trigger days 5d: {halves}; share of trigger days with basket 5d > 0: "
            f"{np.mean(fw5[trig] > 0) * 100:.0f}%")
    vb.note("p53 · done", f"{time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
