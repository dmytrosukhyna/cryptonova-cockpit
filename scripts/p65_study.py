"""Backlog #65 (30.10.2026): «buy the stress» — liquid alt basket bought at the close of a day BTC touched −5 %.

Found as a side result of #53 (+1.34 % next day, t 2.67, 234 days, before costs and funding) → stricter gates.
Fixed BEFORE the run (two variants: hold 1 day and hold 5 days → luck hurdle N = 527):
  Trigger day t: BTCUSDT perp daily low ≤ −5 % below the previous close (known in real time; entry at the close of t).
  Basket: every Binance USDT-M perp except BTC with previous-20-day average turnover ≥ $20M on day t, equal weights,
  delisted coins included (a coin whose data ends is closed at its last close).
  Net trade = mean over coins of the compounded close-to-close move over t+1 … t+H, minus funding paid by a long over
  the same days, minus 0.14 % per round trip (0.05 % taker + 0.02 % slippage each side).
  Trades never overlap (a trigger inside an open hold is skipped). Account = 100 % in the basket during holds, cash else.
  PASS (both needed for a variant): mean net trade > 0 with t (non-overlapping trades) ≥ 2.5; both halves of trades
  > 0; 2024–2026 alone > 0; mean above the p95 of a placebo (random days with the same BTC regime — BTC close above /
  below its 200-day average — same count, 2000 draws, same costs); account Sharpe above the luck hurdle
  sqrt(2 ln N)/sqrt(years) and above BTC hold.
  Reported, not a gate: years, gross vs funding vs costs, trigger days where BTC recovered (close in the upper half of
  the day's range — the daily cousin of the desk «Откуп каскада») vs closed near the low.
Data: data.binance.vision 1d klines + fundingRate, every USDT-M perp, delisted included, 2020-01 → 2026-09.
Public data only, no keys, no orders.
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
TRIG = -0.05
COST = 2 * (vb.FEE + vb.SLIP)
N_LUCK = 527
DRAWS = 2000


def iso(d):
    return str(np.datetime64(int(d), "D"))


def tstat(a):
    a = np.asarray(a, float)
    if len(a) < 3 or a.std(ddof=1) == 0:
        return 0.0
    return float(a.mean() / (a.std(ddof=1) / math.sqrt(len(a))))


def main():
    t0 = time.time()
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [f36.prep(x) for x in ex.map(f36.load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    vb.note("p65 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
    d0 = min(int(x["d"][0]) for x in data)
    m = live_end - d0 + 1
    n = len(data)
    R = np.full((n, m), np.nan); F = np.zeros((n, m)); Q = np.zeros((n, m))
    for k, x in enumerate(data):
        idx = x["d"] - d0
        c = x["c"]
        r = np.r_[np.nan, c[1:] / c[:-1] - 1]
        r[~x["cont"]] = np.nan
        R[k, idx] = r
        F[k, idx] = x["f"]
        Q[k, idx] = np.nan_to_num(x["q20"])
    bi = next(k for k, x in enumerate(data) if x["sym"] == "BTCUSDT")
    bx = data[bi]
    bidx = bx["d"] - d0
    bc = np.full(m, np.nan); bl = np.full(m, np.nan); bh = np.full(m, np.nan)
    bc[bidx], bl[bidx], bh[bidx] = bx["c"], bx["l"], bx["h"]
    prev = np.r_[np.nan, bc[:-1]]
    low_r = bl / prev - 1
    sma200 = np.full(m, np.nan)
    for t in range(200, m):
        w = bc[t - 199:t + 1]
        if np.isfinite(w).all():
            sma200[t] = w.mean()
    bull = bc > sma200
    rb = np.r_[np.nan, bc[1:] / bc[:-1] - 1]

    def trade(t, H):
        """Net, gross, funding of the basket bought at the close of t, held H days."""
        if t + H >= m:
            return None
        u = np.flatnonzero(Q[:, t] >= LIQ)
        u = u[u != bi]
        if len(u) < 10:
            return None
        nxt = np.nan_to_num(R[np.ix_(u, range(t + 1, t + H + 1))])      # missing bar after the end = closed
        g = np.prod(1 + nxt, axis=1) - 1
        fu = F[np.ix_(u, range(t + 1, t + H + 1))].sum(axis=1)
        gross, fund = float(g.mean()), float(fu.mean())
        return gross - fund - COST, gross, fund, len(u)

    start = 230                                                         # SMA200 + 30 days of turnover
    trig = [t for t in range(start, m - 6) if np.isfinite(low_r[t]) and low_r[t] <= TRIG]
    pool = {True: [t for t in range(start, m - 6) if np.isfinite(sma200[t]) and bull[t]],
            False: [t for t in range(start, m - 6) if np.isfinite(sma200[t]) and not bull[t]]}
    yrs = (m - start) / 365.25
    hurdle = math.sqrt(2 * math.log(N_LUCK)) / math.sqrt(yrs)
    # BTC hold Sharpe on the same window
    bdr = rb[start:]; bdr = bdr[np.isfinite(bdr)]
    btc_sh = bdr.mean() / bdr.std() * math.sqrt(365)
    rng = np.random.default_rng(65)
    verdicts = []
    for H in (1, 5):
        taken, busy = [], -1
        for t in trig:
            if t <= busy:
                continue
            tr = trade(t, H)
            if tr is None:
                continue
            taken.append((t, *tr))
            busy = t + H
        if len(taken) < 10:
            vb.note(f"p65 · H{H}", f"only {len(taken)} trades"); continue
        T = np.array([x[0] for x in taken]); net = np.array([x[1] for x in taken])
        gross = np.array([x[2] for x in taken]); fund = np.array([x[3] for x in taken])
        h = len(net) // 2
        y24 = np.array([iso(d0 + t) >= "2024-01-01" for t in T])
        # placebo — random days of the same BTC regime, same count, same costs
        regs = [bool(bull[t]) for t in T]
        cache = {}
        def pn(t):
            if t not in cache:
                tr = trade(t, H)
                cache[t] = tr[0] if tr else np.nan
            return cache[t]
        pm = []
        for _ in range(DRAWS):
            v = [pn(int(rng.choice(pool[r]))) for r in regs]
            pm.append(np.nanmean(v))
        p95 = float(np.percentile(pm, 95))
        # account: daily series, 100 % in basket during holds
        acct = np.zeros(m - start)
        for t, nt, *_ in taken:
            daily = (1 + nt) ** (1 / H) - 1
            acct[t + 1 - start:t + 1 + H - start] = daily
        sh = acct.mean() / acct.std() * math.sqrt(365) if acct.std() > 0 else 0.0
        eq = np.cumprod(1 + acct); dd = float((eq / np.maximum.accumulate(eq) - 1).min())
        cagr = eq[-1] ** (1 / yrs) - 1
        tt = tstat(net)
        ok = (net.mean() > 0 and tt >= 2.5 and net[:h].mean() > 0 and net[h:].mean() > 0
              and y24.any() and net[y24].mean() > 0 and net.mean() > p95 and sh > hurdle and sh > btc_sh)
        verdicts.append((H, ok))
        years = {}
        for t, nt, *_ in taken:
            years.setdefault(iso(d0 + t)[:4], []).append(nt)
        ys = ", ".join(f"{y}: {np.mean(v) * 100:+.2f}% ({len(v)})" for y, v in sorted(years.items()))
        rec = np.array([(bc[t] - bl[t]) / (bh[t] - bl[t]) >= 0.5 if bh[t] > bl[t] else False for t in T])
        vb.note(f"p65 · H{H} verdict", f"{'PASS' if ok else 'no'} — {len(net)} trades (of {len(trig)} trigger days), "
                f"net {net.mean() * 100:+.2f}% per trade (t {tt:.2f}), win {np.mean(net > 0) * 100:.0f}%; halves "
                f"{net[:h].mean() * 100:+.2f}% / {net[h:].mean() * 100:+.2f}%; 2024–26 {net[y24].mean() * 100:+.2f}% "
                f"({int(y24.sum())}); placebo same-regime mean {np.mean(pm) * 100:+.2f}% p95 {p95 * 100:+.2f}%; account "
                f"Sharpe {sh:.2f} (hurdle {hurdle:.2f}, BTC hold {btc_sh:.2f}), {cagr * 100:+.1f}%/yr, DD {dd * 100:.1f}%, "
                f"in market {np.mean(acct != 0) * 100:.0f}% of days")
        vb.note(f"p65 · H{H} parts", f"gross {gross.mean() * 100:+.2f}%, funding paid {fund.mean() * 100:+.3f}%, costs "
                f"{COST * 100:.2f}%; years: {ys}; BTC recovered (close upper half of range) {int(rec.sum())} trades "
                f"{net[rec].mean() * 100 if rec.any() else 0:+.2f}% vs closed low {int((~rec).sum())} "
                f"{net[~rec].mean() * 100 if (~rec).any() else 0:+.2f}%; in BTC bull {int(np.sum(regs))} trades "
                f"{net[np.array(regs)].mean() * 100 if any(regs) else 0:+.2f}%, bear "
                f"{net[~np.array(regs)].mean() * 100 if not all(regs) else 0:+.2f}%")
        worst = sorted(taken, key=lambda x: x[1])[:5]
        vb.note(f"p65 · H{H} worst", "; ".join(f"{iso(d0 + t)} {nt * 100:+.1f}% ({k} coins)" for t, nt, _, _, k in worst))
    vb.note("p65 · done", f"{verdicts}; {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
