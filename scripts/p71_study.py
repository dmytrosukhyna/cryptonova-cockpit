"""Backlog #71 (04.11.2026): CME bitcoin futures calendar — the «roll week» and the 16:00 London settlement hour.

CME BTC futures (monthly) expire on the last Friday of the month; the final settlement is the CME CF Bitcoin Reference
Rate averaged over 15:00–16:00 London time. Popular claims: (1) in the 8 days before expiry funds roll the front month
and BTC drifts in one direction; (2) the settlement hour itself moves the price. Family «derivatives calendar»
(#69, #70 closed). Two hypotheses, fixed BEFORE the run, both two-sided (no sign chosen in advance):
  H1 roll week: BTC return close(day −8) → close(day −1), day 0 = the CME expiry Friday (Thursday when the Friday was
     a CME holiday: 25.12.2020, 29.03.2024).
  H2 settlement hour: BTC return over the London hour 15:00–16:00 on the expiry day (14:00–15:00 UTC in British summer
     time, 15:00–16:00 UTC otherwise), 1h perpetual bars.
PASS (fixed now), each hypothesis separately, for BTCUSDT:
  |Welch t| (events vs placebo) ≥ 2.5, placebo percentile of the event mean ≤ 5 % or ≥ 95 % (3 000 draws),
  both halves of the events on the same side of the placebo mean, and ≥ 70 % of events on that side of 0.
Placebo: H1 — the same Fri-anchored window for every Friday that is neither a last Friday nor the Friday right
  before one; H2 — the same London hour on those Fridays.
Reported, not gates: ETHUSDT (CME ETH futures exist since 02.2021), |hour move| ratio (is the hour more volatile),
  halves, years. The CME–Binance basis cannot be tested: CME prices are not public data here — said so honestly.
Luck: 2 hypotheses, family count N = 530 (z printed for reference).
Data: data.binance.vision USDT-M perpetual 1d and 1h klines, 2020-01 → 2026-09. Public data, no keys, no orders.
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

N_LUCK = 530
HOUR = 3_600_000
HOLIDAY_SHIFT = {day_of(2020, 12, 25), day_of(2024, 3, 29)}


def last_sunday(y, m):
    nxt = day_of(y + (m == 12), 1 if m == 12 else m + 1, 1)
    d = nxt - 1
    while d % 7 != 3:  # day 0 (1970-01-01) was a Thursday → Sunday ≡ 3 (mod 7)
        d -= 1
    return d


def london_15_utc_hour(day):
    """UTC hour index (since epoch) of 15:00 London on this day: BST from the last Sunday of March to that of October."""
    y = dt.datetime.fromtimestamp(day * 86400, dt.timezone.utc).year
    bst = last_sunday(y, 3) <= day < last_sunday(y, 10)
    return day * 24 + (14 if bst else 15)


def load_1h(sym):
    """{hour index: (open, close)} from the monthly 1h archive."""
    out = {}
    for m in [m for m in vb.s3_months(f"data/futures/um/monthly/klines/{sym}/1h/") if vb.M0 <= m <= vb.M1]:
        s, b = vb.get(f"{vb.VISION}/monthly/klines/{sym}/1h/{sym}-1h-{m}.zip")
        if s == 200:
            for r in vb.zrows(b):
                t = int(r[0])
                t = t // 1000 if t > 10 ** 14 else t
                out[t // HOUR] = (float(r[1]), float(r[4]))
    return out


def day_ret(x, events, a, b):
    pos = {int(d): i for i, d in enumerate(x["d"])}
    out = []
    for e in events:
        i, j = pos.get(e + a), pos.get(e + b)
        out.append(np.nan if i is None or j is None else x["c"][j] / x["c"][i] - 1)
    return np.array(out)


def hour_ret(h1, days):
    out = []
    for d in days:
        bar = h1.get(london_15_utc_hour(d))
        out.append(np.nan if bar is None or bar[0] <= 0 else bar[1] / bar[0] - 1)
    return np.array(out)


def welch(a, b):
    return (a.mean() - b.mean()) / math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))


def gate(ev, pl, rng):
    ev, pl = ev[np.isfinite(ev)], pl[np.isfinite(pl)]
    boot = np.array([rng.choice(pl, len(ev)).mean() for _ in range(3000)])
    pct = (boot <= ev.mean()).mean() * 100
    t = welch(ev, pl)
    side = 1 if ev.mean() >= pl.mean() else -1
    h = len(ev) // 2
    halves = (ev[:h].mean(), ev[h:].mean())
    same_halves = all((v - pl.mean()) * side > 0 for v in halves)
    share = ((ev * side) > 0).mean() * 100
    ok = abs(t) >= 2.5 and (pct <= 5 or pct >= 95) and same_halves and share >= 70
    return ok, {"n": len(ev), "pool_n": len(pl), "mean": ev.mean(), "med": np.median(ev), "pool_mean": pl.mean(),
                "t": t, "pct": pct, "halves": halves, "share": share, "side": side,
                "absratio": np.abs(ev).mean() / np.abs(pl).mean()}


def fmt(r):
    return (f"{r['n']} events mean {r['mean'] * 100:+.3f}% (median {r['med'] * 100:+.3f}%) vs {r['pool_n']} placebo "
            f"{r['pool_mean'] * 100:+.3f}%; Welch t {r['t']:+.2f}; placebo pct {r['pct']:.1f}%; halves "
            f"{r['halves'][0] * 100:+.3f}%/{r['halves'][1] * 100:+.3f}%; share on the {'+' if r['side'] > 0 else '−'} "
            f"side {r['share']:.0f}%; |move| ratio {r['absratio']:.2f}×")


def main():
    t0 = time.time()
    events, lastf = [], set()
    for y in range(2020, 2027):
        for m in range(1, 13):
            d = fridays_last(y, m)
            if d < day_of(2020, 1, 20) or d > day_of(2026, 9, 25):
                continue
            lastf.add(d)
            events.append(d - 1 if d in HOLIDAY_SHIFT else d)
    rng = np.random.default_rng(71)
    verdict = {}
    for s in ("BTCUSDT", "ETHUSDT"):
        x = f36.load(s)
        h1 = load_1h(s)
        d0, d1 = int(x["d"][0]) + 10, int(x["d"][-1]) - 2
        ev = [e for e in events if d0 <= e <= d1]
        if s == "ETHUSDT":
            ev = [e for e in ev if e >= day_of(2021, 2, 8)]
        pool = [f for f in range(d0, d1 + 1) if f % 7 == 1 and f not in lastf and f + 7 not in lastf]
        ok1, r1 = gate(day_ret(x, ev, -8, -1), day_ret(x, pool, -8, -1), rng)
        ok2, r2 = gate(hour_ret(h1, ev), hour_ret(h1, pool), rng)
        verdict[s] = (ok1, ok2)
        vb.note(f"p71 · {s} H1 roll week", f"{'PASS' if ok1 else 'no'} — close(−8)→close(−1): {fmt(r1)}")
        vb.note(f"p71 · {s} H2 settle hour", f"{'PASS' if ok2 else 'no'} — London 15–16h on expiry day: {fmt(r2)}; "
                f"1h bars loaded {len(h1)}")
        ok3, r3 = gate(day_ret(x, ev, -1, 0), day_ret(x, pool, -1, 0), rng)
        ok4, r4 = gate(day_ret(x, ev, 0, 3), day_ret(x, pool, 0, 3), rng)
        vb.note(f"p71 · {s} other", f"expiry day itself (−1→0): {fmt(r3)} || after (0→+3): {fmt(r4)}")
        by_year = {}
        for d, v in zip(ev, day_ret(x, ev, -8, -1)):
            if np.isfinite(v):
                by_year.setdefault(dt.datetime.fromtimestamp(d * 86400, dt.timezone.utc).year, []).append(v)
        vb.note(f"p71 · {s} years", "roll-week mean by year " + ", ".join(
            f"{y}: {np.mean(v) * 100:+.2f}% ({sum(1 for a in v if a > 0)}/{len(v)} up)" for y, v in sorted(by_year.items())))
    btc = verdict.get("BTCUSDT", (False, False))
    vb.note("p71 · verdict", f"H1 {'PASS' if btc[0] else 'no'}, H2 {'PASS' if btc[1] else 'no'} (BTC is the gate; "
            f"ETH reported); luck z for N {N_LUCK} = {math.sqrt(2 * math.log(N_LUCK)):.2f}; {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
