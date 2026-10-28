"""Backlog #48 (28.10.2026): is the RSI(2) pullback P («Сигналы CRYPTO NOVA») an edge only on «burst» days?

Basis — #36 (scripts/families36_study.py): P on all 820 perps averaged +1.56 % per trade but t by date was −1.8, i.e. the
mean is carried by a few days when many coins signalled at once. Question: does the edge sit only in such bursts, and does
the rule «buy only when ≥ 10 coins signal P the same day» survive honest checks?

Fixed BEFORE the run (no tuning): same archive, costs, funding, P signal and exits as #36 (f36.sig / f36.trade).
  burst size of a day = number of coins whose P condition is true that day (one position per coin is NOT applied to the count);
  buckets (descriptive, not variants): 1–3, 4–9, ≥ 10 signals.
  rule tested (1 variant): take P trades only on days with ≥ 10 signals.
Honesty:
  placebo A — same coins, random eligible «рост» days (as #36), as many as the rule traded there, 20 draws;
  placebo B — same burst dates, random eligible coins that did NOT signal, same count per date, 20 draws
              (is it the coin's own pullback or just «the market bounced that day»?).
Pass (fixed now): mean trade above BOTH placebo p95, t by date ≥ 2, mean trade > 0 in both halves of the period, account
Sharpe (live sizing, gross ≤ 2×) above BTC's in both halves. Variants: 1; luck hurdle N = 517 + 1 = 518.
Public data only, no keys, no orders.
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voltbreak_all_study as vb  # noqa: E402
import families36_study as f36  # noqa: E402

N_LUCK = 518
BURST = 10
BUCKETS = ((1, 3), (4, 9), (10, 10 ** 6))


def bucket_of(n):
    for lo, hi in BUCKETS:
        if lo <= n <= hi:
            return f"{lo}-{hi}" if hi < 10 ** 6 else f"≥{lo}"
    return "0"


def main():
    vb.N_LUCK = N_LUCK
    t0 = time.time()
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [f36.prep(x) for x in ex.map(f36.load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    vb.note("p48 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
    btc = next(x for x in data if x["sym"] == "BTCUSDT")
    bc = btc["c"]
    bull = {}
    for k in range(200, len(bc)):
        bull[int(btc["d"][k])] = bc[k] > bc[k - 199:k + 1].mean() and bc[k] > bc[k - 60]
    d0 = min(int(x["d"][0]) for x in data)
    d1 = live_end
    mid = (d0 + d1) // 2
    span = np.arange(d0, d1 + 1)
    bpr = np.zeros(len(span))
    for j in range(1, len(bc)):
        bpr[int(btc["d"][j]) - d0] = bc[j] / bc[j - 1] - 1
    bb = vb.stats(span, bpr)

    # pass 1: raw P condition per coin/day and eligible days
    sigs, elig_by_day, elig = {}, {}, []
    for k, x in enumerate(data):
        ek = []
        for i in range(60, len(x["c"]) - 1):
            if not f36.ok_day(x, i):
                continue
            b = bull.get(int(x["d"][i]), False)
            if not b:
                continue
            ek.append(i)
            dd = int(x["d"][i])
            if f36.sig(x, i, "P", b):
                sigs.setdefault(dd, set()).add(k)
            else:
                elig_by_day.setdefault(dd, []).append((k, i))
        elig.append(ek)
    size = {dd: len(v) for dd, v in sigs.items()}

    # pass 2: trades with one position per coin (as #36), tagged by the day's burst size
    allt = []                                              # (date, k, path, bucket)
    for k, x in enumerate(data):
        last = -1
        for i in range(60, len(x["c"]) - 1):
            dd = int(x["d"][i])
            if i <= last or k not in sigs.get(dd, ()):
                continue
            p = f36.trade(x, i, "P")
            if p:
                allt.append((dd, k, p, bucket_of(size[dd])))
                last = i + len(p[0])
    parts = []
    for lo, hi in BUCKETS:
        b = bucket_of(lo)
        sel = [t for t in allt if t[3] == b]
        if not sel:
            continue
        pt = vb.per_trade([(t[2][0], t[2][1]) for t in sel], [t[0] for t in sel])
        parts.append(f"{b}: {pt['n']} trades on {pt['dates']} days, mean {pt['mean_pct']}% (median {pt['median_pct']}%, "
                     f"win {pt['win']}%, t by date {pt['t_by_date']}), halves {f36.tmean([t[2] for t in sel if t[0] < mid])}% / "
                     f"{f36.tmean([t[2] for t in sel if t[0] >= mid])}%")
    dist = {b: sum(1 for v in size.values() if bucket_of(v) == b) for b in ("1-3", "4-9", f"≥{BURST}")}
    vb.note("p48 · buckets", f"all P {len(allt)} trades; signal days by size {dist}; " + " | ".join(parts))

    # the rule: only days with ≥ BURST signals
    rt = [t for t in allt if size[t[0]] >= BURST]
    pt = vb.per_trade([(t[2][0], t[2][1]) for t in rt], [t[0] for t in rt])
    m1 = f36.tmean([t[2] for t in rt if t[0] < mid])
    m2 = f36.tmean([t[2] for t in rt if t[0] >= mid])
    sp, pr = f36.wport([t[2] for t in rt], d0, d1, 2.0)
    st = vb.stats(sp, pr)
    by_year = {}
    for t in rt:
        by_year.setdefault(1970 + int(t[0] // 365.2425), []).append(sum(t[2][1]))
    yrs = {y: f"{np.mean(v) * 100:+.2f}%/{len(v)}" for y, v in sorted(by_year.items())}

    rng = np.random.default_rng(48)
    cnt = {}
    for t in rt:
        cnt[t[1]] = cnt.get(t[1], 0) + 1
    pa = []
    for rep in range(20):
        r = []
        for k, nk in cnt.items():
            for j in rng.choice(elig[k], size=nk, replace=True):
                p = f36.trade(data[k], int(j), "P")
                if p:
                    r.append(sum(p[1]))
        pa.append(np.mean(r) * 100 if r else 0.0)
    per_date = {}
    for t in rt:
        per_date[t[0]] = per_date.get(t[0], 0) + 1
    pb = []
    for rep in range(20):
        r = []
        for dd, nk in per_date.items():
            pool = elig_by_day.get(dd, [])
            if not pool:
                continue
            for j in rng.choice(len(pool), size=min(nk, len(pool)), replace=False):
                k, i = pool[int(j)]
                p = f36.trade(data[k], i, "P")
                if p:
                    r.append(sum(p[1]))
        pb.append(np.mean(r) * 100 if r else 0.0)
    pa95, pb95 = float(np.percentile(pa, 95)), float(np.percentile(pb, 95))
    ok = (pt["mean_pct"] > pa95 and pt["mean_pct"] > pb95 and pt["t_by_date"] >= 2 and (m1 or 0) > 0 and (m2 or 0) > 0
          and st["halves"][0] > bb["halves"][0] and st["halves"][1] > bb["halves"][1])
    vb.note("p48 · P only on ≥10-signal days", f"{'PASS' if ok else 'no'} — {pt['n']} trades on {pt['dates']} days, mean "
            f"{pt['mean_pct']}% (median {pt['median_pct']}%, win {pt['win']}%, t by date {pt['t_by_date']}), halves {m1}% / {m2}%; "
            f"placebo A (random days) {round(float(np.mean(pa)), 2)}% p95 {round(pa95, 2)}%; placebo B (same dates, non-signalling "
            f"coins) {round(float(np.mean(pb)), 2)}% p95 {round(pb95, 2)}%; account Sharpe {st['sharpe']} (halves "
            f"{st['halves'][0]}/{st['halves'][1]} vs BTC {bb['halves'][0]}/{bb['halves'][1]}), {st['cagr_pct']}%/yr, DD "
            f"{st['dd_pct']}%, hurdle {st['hurdle']}; trade mean by year {yrs}")
    top = sorted(per_date.items(), key=lambda kv: -kv[1])[:5]
    vb.note("p48 · biggest burst dates", ", ".join(
        f"{time.strftime('%Y-%m-%d', time.gmtime(dd * 86400))}: {n} trades, "
        f"{np.mean([sum(t[2][1]) for t in rt if t[0] == dd]) * 100:+.1f}%" for dd, n in top) + f"; {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
