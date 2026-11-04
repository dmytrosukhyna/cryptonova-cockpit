"""Backlog #72 (04.11.2026): «quiet AFTER expiry» — is realised volatility LOWER in the 5 days after a quarterly
Deribit options expiry than after an ordinary Friday?

Found as a side observation of #70 on BTC/ETH (after looking at the data) → stricter gates and an INDEPENDENT sample:
SOL, XRP, BNB, DOGE (USDT-M perpetuals, 2021-01 → 2026-09). BTC/ETH are reproduced for reference only (they found it).
Risk-layer hint only (fewer stops on noise), no trades. Forward part (4 expiries 25.12.2026 → 24.09.2027) stays open.

ONE hypothesis, fixed BEFORE the run:
  H: R_after = RV(days +1..+5) / RV(days −34..−5) is LOWER after a quarterly expiry Friday than after an ordinary Friday.
  RV = std of daily close-to-close log returns; day 0 = the expiry-Friday bar.
  The 4 coins share the same dates (strongly correlated), so the test unit is the DATE: per date, the mean log R over
  the coins that have data. Events = quarterly expiry dates, placebo = every Friday that is not a last Friday of a month.
PASS (fixed now), all of:
  Welch t (event dates vs placebo dates) ≤ −3.0; event mean below the 5th percentile of the placebo (3 000 draws);
  both halves of the events (by time) below the placebo mean; ≥ 3 of 4 coins individually below their placebo mean;
  without December expiries (year-end holidays could be the real cause) the event mean still below the placebo mean.
Reported, not gates: per-coin numbers, monthly last Fridays, BTC/ETH reproduction, years.
Luck: 1 hypothesis, family count N = 531 (z printed for reference).
Data: data.binance.vision, public, no keys, no orders.
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
from p70_study import ratios, compare  # noqa: E402

N_LUCK = 531
COINS = ("SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT")
REF = ("BTCUSDT", "ETHUSDT")
START, END = day_of(2021, 1, 1), day_of(2026, 9, 28)


def calendar():
    qexp, mexp, last_all = [], [], set()
    for y in range(2020, 2027):
        for m in range(1, 13):
            d = fridays_last(y, m)
            if START <= d <= END:
                last_all.add(d)
                (qexp if m in (3, 6, 9, 12) else mexp).append(d)
    pool = [f for f in range(START, END + 1) if f % 7 == 1 and f not in last_all]
    return qexp, mexp, pool


def by_date(data, dates):
    """dict date -> mean log R_after over coins with data."""
    acc = {}
    for x in data:
        for d, v in zip(dates, ratios(x, dates, 1, 5)):
            if np.isfinite(v):
                acc.setdefault(d, []).append(v)
    return {d: float(np.mean(v)) for d, v in acc.items()}


def month(d):
    return dt.datetime.fromtimestamp(d * 86400, dt.timezone.utc).month


def year(d):
    return dt.datetime.fromtimestamp(d * 86400, dt.timezone.utc).year


def main():
    t0 = time.time()
    qexp, mexp, pool = calendar()
    rng = np.random.default_rng(72)
    e = lambda v: f"{math.exp(v):.2f}×"
    data = []
    for s in COINS:
        x = f36.load(s)
        if x is None:
            vb.note(f"p72 · {s}", "no data", "warning")
            continue
        data.append(x)
        r = compare(ratios(x, qexp, 1, 5), ratios(x, pool, 1, 5), rng)
        vb.note(f"p72 · {s}", f"{r['n']} quarterly: R_after {e(r['mean'])} vs {r['pool_n']} Fridays "
                f"{e(r['pool_mean'])}; t {r['t']:.2f}, pct {r['pct']:.1f}%, halves {e(r['halves'][0])}/"
                f"{e(r['halves'][1])}")
        x["_below"] = r["mean"] < r["pool_mean"]
    coins_below = sum(1 for x in data if x["_below"])
    ev, pl = by_date(data, qexp), by_date(data, pool)
    evd = sorted(ev)
    ea = np.array([ev[d] for d in evd])
    pa = np.array(list(pl.values()))
    r = compare(ea, pa, rng)
    nodec = np.array([ev[d] for d in evd if month(d) != 12])
    ok = (r["t"] <= -3.0 and r["pct"] <= 5 and max(r["halves"]) < r["pool_mean"] and coins_below >= 3
          and nodec.mean() < pa.mean())
    vb.note("p72 · H (4 coins, by date)", f"{'PASS' if ok else 'no'} — {r['n']} quarterly dates: R_after "
            f"{e(r['mean'])} vs {r['pool_n']} ordinary Fridays {e(r['pool_mean'])}; Welch t {r['t']:.2f} (need ≤ −3), "
            f"placebo pct {r['pct']:.1f}% (need ≤ 5), halves {e(r['halves'][0])}/{e(r['halves'][1])}, coins below "
            f"{coins_below}/{len(data)}, without December {e(nodec.mean())} ({len(nodec)}) vs {e(pa.mean())}; "
            f"below pool median {r['below_med']:.0f}%; luck z for N {N_LUCK} = {math.sqrt(2 * math.log(N_LUCK)):.2f}")
    mo = by_date(data, mexp)
    rm = compare(np.array([mo[d] for d in sorted(mo)]), pa, rng)
    vb.note("p72 · monthly", f"{rm['n']} monthly last Fridays: R_after {e(rm['mean'])} t {rm['t']:.2f} pct "
            f"{rm['pct']:.0f}%")
    yrs = {}
    for d in evd:
        yrs.setdefault(year(d), []).append(f"{month(d)}:{math.exp(ev[d]):.2f}")
    vb.note("p72 · years", f"quarterly R_after by date {yrs}")
    ref = []
    for s in REF:
        x = f36.load(s)
        if x is None:
            continue
        rr = compare(ratios(x, qexp, 1, 5), ratios(x, pool, 1, 5), rng)
        dec = np.array([v for d, v in zip(qexp, ratios(x, qexp, 1, 5)) if month(d) != 12 and np.isfinite(v)])
        ref.append(f"{s} 2021+: {e(rr['mean'])} vs {e(rr['pool_mean'])} t {rr['t']:.2f}, no-Dec {e(dec.mean())}")
    vb.note("p72 · BTC/ETH reference (not a gate)", "; ".join(ref))
    vb.note("p72 · verdict", f"{'PASS' if ok else 'no'}; {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
