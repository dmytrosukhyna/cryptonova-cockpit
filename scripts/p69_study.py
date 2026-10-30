"""Backlog #69 (30.10.2026): quarterly Deribit options expiry — does BTC / ETH fall into the expiry?

New family «derivatives calendar». Quarterly expiry = the last Friday of March, June, September and December, 08:00 UTC.
2020-03 → 2026-09 = 27 events. Daily bars close at 00:00 UTC, so the expiry sits inside the Friday bar (day 0).
ONE hypothesis, fixed BEFORE the run (it would justify «do not open longs 2 days before the expiry»):
  H: the return from the Wednesday close (day −2) to the expiry-Friday close (day 0) is NEGATIVE on average.
PASS (fixed now), for BTC AND ETH each:
  share of negative events ≥ 70 %, both halves (first 13 / last 14 events) negative on average,
  mean below the 5th percentile of the placebo, t ≤ −2.5.
Placebo: every other Friday at least 8 days away from any quarterly expiry; 3 000 draws of 27 Fridays (with
replacement) → distribution of the mean; plus the full pool of random Fridays as a plain comparison.
Luck: 1 hypothesis, family count N = 527 → the Bonferroni-style z = sqrt(2 ln N) is printed for reference.
Reported, not gates: windows −5..0, 0..+5, −1..0 (expiry day alone), absolute move after vs before (volatility),
monthly (non-quarterly) last Fridays, years. Long-only «avoid» rule → no costs; funding ignored (≤ 3 days).
Data: data.binance.vision USDT-M perpetual 1d klines BTCUSDT, ETHUSDT. Public data only, no keys, no orders.
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

N_LUCK = 527
DAY = 86_400_000


def day_of(y, m, d):
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp() * 1000 // DAY)


def fridays_last(y, m):
    nxt = day_of(y + (m == 12), 1 if m == 12 else m + 1, 1)
    d = nxt - 1
    while d % 7 != 1:
        d -= 1
    return d


def window(x, ev, a, b):
    """close(day ev+a) → close(day ev+b) return; None if a bar is missing."""
    pos = {int(d): i for i, d in enumerate(x["d"])}
    out = []
    for e in ev:
        i, j = pos.get(e + a), pos.get(e + b)
        out.append(None if i is None or j is None else x["c"][j] / x["c"][i] - 1)
    return np.array([np.nan if v is None else v for v in out])


def tstat(v):
    v = v[np.isfinite(v)]
    return v.mean() / (v.std(ddof=1) / math.sqrt(len(v))) if len(v) > 2 and v.std() > 0 else 0.0


def study(x, qexp, pool, rng):
    res = {}
    for name, a, b in (("H −2..0", -2, 0), ("−5..0", -5, 0), ("−1..0", -1, 0), ("0..+5", 0, 5)):
        ev = window(x, qexp, a, b)
        ev = ev[np.isfinite(ev)]
        pl = window(x, pool, a, b)
        pl = pl[np.isfinite(pl)]
        boot = np.array([rng.choice(pl, len(ev)).mean() for _ in range(3000)])
        h = len(ev) // 2
        res[name] = {
            "n": len(ev), "mean": ev.mean() * 100, "median": float(np.median(ev)) * 100,
            "neg": (ev < 0).mean() * 100, "t": tstat(ev),
            "halves": (ev[:h].mean() * 100, ev[h:].mean() * 100),
            "pct": (boot <= ev.mean()).mean() * 100,
            "pool_mean": pl.mean() * 100, "pool_n": len(pl), "ev": ev}
    # volatility: |0..+5| vs |−5..0|
    pre = np.abs(window(x, qexp, -5, 0)); post = np.abs(window(x, qexp, 0, 5))
    ppre = np.abs(window(x, pool, -5, 0)); ppost = np.abs(window(x, pool, 0, 5))
    res["vol"] = (np.nanmean(pre) * 100, np.nanmean(post) * 100, np.nanmean(ppre) * 100, np.nanmean(ppost) * 100)
    return res


def main():
    t0 = time.time()
    qexp, mexp = [], []
    for y in range(2020, 2027):
        for m in range(1, 13):
            d = fridays_last(y, m)
            if d > day_of(2026, 9, 28) or d < day_of(2020, 1, 10):
                continue
            (qexp if m in (3, 6, 9, 12) else mexp).append(d)
    xs = {s: f36.load(s) for s in ("BTCUSDT", "ETHUSDT")}
    ok_all = True
    rng = np.random.default_rng(69)
    for s, x in xs.items():
        d0, d1 = int(x["d"][0]) + 6, int(x["d"][-1]) - 6
        q = [e for e in qexp if d0 <= e <= d1]
        far = lambda f: min(abs(f - e) for e in qexp) >= 8
        pool = [f for f in range(d0, d1 + 1) if f % 7 == 1 and far(f)]
        r = study(x, q, pool, rng)
        h = r["H −2..0"]
        ok = h["neg"] >= 70 and max(h["halves"]) < 0 and h["pct"] <= 5 and h["t"] <= -2.5
        ok_all &= ok
        first = dt.datetime.fromtimestamp(q[0] * 86400, dt.timezone.utc).date()
        last = dt.datetime.fromtimestamp(q[-1] * 86400, dt.timezone.utc).date()
        vb.note(f"p69 · {s} H", f"{'PASS' if ok else 'no'} — {h['n']} expiries {first}→{last}: Wed close→Fri close "
                f"mean {h['mean']:+.2f}% (median {h['median']:+.2f}%), negative {h['neg']:.0f}%, t {h['t']:.2f}, halves "
                f"{h['halves'][0]:+.2f}%/{h['halves'][1]:+.2f}%; placebo {h['pool_n']} Fridays mean {h['pool_mean']:+.2f}%, "
                f"event mean at placebo percentile {h['pct']:.1f}%; luck z for N {N_LUCK} = {math.sqrt(2 * math.log(N_LUCK)):.2f}")
        rows = []
        for k in ("−5..0", "−1..0", "0..+5"):
            v = r[k]
            rows.append(f"{k}: mean {v['mean']:+.2f}% neg {v['neg']:.0f}% t {v['t']:.2f} halves {v['halves'][0]:+.2f}/"
                        f"{v['halves'][1]:+.2f} pct {v['pct']:.0f}% (pool {v['pool_mean']:+.2f}%)")
        vp = r["vol"]
        rows.append(f"|move| 5d before {vp[0]:.2f}% after {vp[1]:.2f}% (random Fridays {vp[2]:.2f}% / {vp[3]:.2f}%)")
        vb.note(f"p69 · {s} other", "; ".join(rows))
        mm = [e for e in mexp if d0 <= e <= d1]
        rm = study(x, mm, pool, rng)["H −2..0"]
        by_year = {}
        for e, v in zip(q, h["ev"]):
            yy = dt.datetime.fromtimestamp(e * 86400, dt.timezone.utc).year
            by_year.setdefault(yy, []).append(round(v * 100, 1))
        vb.note(f"p69 · {s} monthly+years", f"monthly last Fridays ({rm['n']}): −2..0 mean {rm['mean']:+.2f}% neg "
                f"{rm['neg']:.0f}% t {rm['t']:.2f} pct {rm['pct']:.0f}%; quarterly by year {by_year}")
    vb.note("p69 · verdict", f"{'PASS' if ok_all else 'no'} (both coins must pass); {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
