"""Backlog #70 (04.11.2026): «quiet before expiry» — is BTC / ETH realised volatility LOWER in the 5 days before a
quarterly Deribit options expiry than before an ordinary Friday?

Found as a side observation of #69 (after looking at the data) → stricter gates. Risk filter only, no trades.
ONE hypothesis, fixed BEFORE the run:
  H: the volatility ratio  R = RV(days −4..0) / RV(days −34..−5)  is LOWER before a quarterly expiry Friday than before
     an ordinary Friday.  RV = standard deviation of daily close-to-close log returns; day 0 = the expiry-Friday bar
     (expiry 08:00 UTC sits inside it). Dividing by the trailing 30 days removes the market regime (2021 vs 2023 etc.),
     which the raw |move| comparison in #69 did not.
PASS (fixed now), for BTC AND ETH each:
  mean log R of the events below the 5th percentile of the placebo (3 000 draws), Welch t (events vs pool) ≤ −2.5,
  both halves of the events below the placebo mean, and ≥ 60 % of events below the placebo median.
Placebo: every Friday that is not a last Friday of a month (so neither quarterly nor monthly expiry) — 7+ days away.
Reported, not gates: monthly (non-quarterly) last Fridays separately, raw |5-day move|, the 5 days AFTER expiry
(R_after = RV(+1..+5) / RV(−34..−5)), years. Luck: 1 hypothesis, family count N = 528 (z printed for reference).
Part (b) of the backlog row (do breakouts reach target less often in those days) runs only if (a) passes.
Data: data.binance.vision USDT-M perpetual 1d klines BTCUSDT, ETHUSDT 2020-01 → 2026-09. Public data, no keys, no orders.
"""
import datetime as dt
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voltbreak_all_study as vb  # noqa: E402
import families36_study as f36  # noqa: E402
from p69_study import day_of, fridays_last  # noqa: E402

N_LUCK = 528


def ratios(x, events, a, b):
    """log( RV(days a..b) / RV(days −34..−5) ) per event; NaN if bars are missing."""
    pos = {int(d): i for i, d in enumerate(x["d"])}
    lr = np.diff(np.log(x["c"]))  # lr[i-1] = return of bar i
    out = []
    for e in events:
        idx_ev = [pos.get(e + k) for k in range(a, b + 1)]
        idx_base = [pos.get(e + k) for k in range(-34, -4)]
        if any(i is None or i < 1 for i in idx_ev + idx_base):
            out.append(np.nan)
            continue
        rv = np.std([lr[i - 1] for i in idx_ev], ddof=1)
        base = np.std([lr[i - 1] for i in idx_base], ddof=1)
        out.append(math.log(rv / base) if rv > 0 and base > 0 else np.nan)
    return np.array(out)


def absmove(x, events, a, b):
    pos = {int(d): i for i, d in enumerate(x["d"])}
    out = []
    for e in events:
        i, j = pos.get(e + a), pos.get(e + b)
        out.append(np.nan if i is None or j is None else abs(x["c"][j] / x["c"][i] - 1))
    return np.array(out)


def welch(a, b):
    return (a.mean() - b.mean()) / math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))


def compare(ev, pl, rng):
    ev, pl = ev[np.isfinite(ev)], pl[np.isfinite(pl)]
    boot = np.array([rng.choice(pl, len(ev)).mean() for _ in range(3000)])
    h = len(ev) // 2
    return {"n": len(ev), "pool_n": len(pl), "mean": ev.mean(), "pool_mean": pl.mean(),
            "pct": (boot <= ev.mean()).mean() * 100, "t": welch(ev, pl),
            "halves": (ev[:h].mean(), ev[h:].mean()), "below_med": (ev < np.median(pl)).mean() * 100, "ev": ev}


def main():
    t0 = time.time()
    qexp, mexp, last_all = [], [], set()
    for y in range(2020, 2027):
        for m in range(1, 13):
            d = fridays_last(y, m)
            if d > day_of(2026, 9, 28) or d < day_of(2020, 1, 10):
                continue
            last_all.add(d)
            (qexp if m in (3, 6, 9, 12) else mexp).append(d)
    ok_all = True
    rng = np.random.default_rng(70)
    for s in ("BTCUSDT", "ETHUSDT"):
        x = f36.load(s)
        d0, d1 = int(x["d"][0]) + 36, int(x["d"][-1]) - 6
        q = [e for e in qexp if d0 <= e <= d1]
        mm = [e for e in mexp if d0 <= e <= d1]
        pool = [f for f in range(d0, d1 + 1) if f % 7 == 1 and f not in last_all]
        pl = ratios(x, pool, -4, 0)
        r = compare(ratios(x, q, -4, 0), pl, rng)
        ok = (r["pct"] <= 5 and r["t"] <= -2.5 and max(r["halves"]) < r["pool_mean"] and r["below_med"] >= 60)
        ok_all &= ok
        e = lambda v: f"{math.exp(v):.2f}×"
        vb.note(f"p70 · {s} H", f"{'PASS' if ok else 'no'} — {r['n']} quarterly expiries: RV(−4..0)/RV(−34..−5) mean "
                f"{e(r['mean'])} vs {r['pool_n']} ordinary Fridays {e(r['pool_mean'])}; Welch t {r['t']:.2f}, placebo "
                f"percentile {r['pct']:.1f}%, halves {e(r['halves'][0])}/{e(r['halves'][1])}, below pool median "
                f"{r['below_med']:.0f}%; luck z for N {N_LUCK} = {math.sqrt(2 * math.log(N_LUCK)):.2f}")
        rm = compare(ratios(x, mm, -4, 0), pl, rng)
        ra = compare(ratios(x, q, 1, 5), ratios(x, pool, 1, 5), rng)
        am = absmove(x, q, -5, 0); ap = absmove(x, pool, -5, 0)
        vb.note(f"p70 · {s} other", f"monthly last Fridays ({rm['n']}): ratio {e(rm['mean'])} t {rm['t']:.2f} pct "
                f"{rm['pct']:.0f}% halves {e(rm['halves'][0])}/{e(rm['halves'][1])}; quarterly AFTER +1..+5: "
                f"{e(ra['mean'])} vs {e(ra['pool_mean'])} t {ra['t']:.2f}; raw |−5..0 move| {np.nanmean(am) * 100:.2f}% "
                f"vs {np.nanmean(ap) * 100:.2f}%")
        by_year = {}
        for d, v in zip([d for d in q], ratios(x, q, -4, 0)):
            if np.isfinite(v):
                by_year.setdefault(dt.datetime.fromtimestamp(d * 86400, dt.timezone.utc).year, []).append(
                    round(math.exp(v), 2))
        vb.note(f"p70 · {s} years", f"quarterly ratio by year {by_year}")
    vb.note("p70 · verdict", f"{'PASS' if ok_all else 'no'} (both coins must pass); {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
