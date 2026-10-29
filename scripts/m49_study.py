"""Backlog #49 (29.10.2026): does the terminal trend rule M hold on the terminal's own 18 coins, 2025–2026 out of sample?

Basis — #36 (scripts/families36_study.py): M on all 820 perps averaged +0.42 % per trade, t by date 0.2, 2025–26 negative.
The «Решения» texts (cnq/terminal/report.py → regime()) call it «правило тренда». Question for Dmytro's decision: on the 18
coins the terminal actually shows, is it a rule (positive, above placebo out of sample) or only an observation?

Fixed BEFORE the run (copied from cnq/terminal/report.py, no tuning):
  LONG  — 28-day return > 0 and close ≥ the previous 20-day high → buy at the next open.
  SHORT — 28-day return < 0 and close ≤ the previous 20-day low  → sell at the next open (the text shows it as «тренд вниз»).
  Both: hold 5 days (exit at the close of day 5), stop 2 ATR(14) from the entry («стоп по правилу 2 ATR»), one position per
  coin and side. Costs and funding as #36 (0.05 % taker + 0.02 % slippage per side, real Binance funding: longs pay, shorts
  receive). Size 10 % of equity per trade, gross ≤ 100 %.
  Coins: the 18 of web/terminal.json (ADA APT ARB AVAX BCH BNB BTC DOGE DOT ETH LINK LTC NEAR OP SOL SUI TRX XRP), Binance USDT-M.
  In sample = signals before 2025-01-01 (shown only); OUT OF SAMPLE = 2025-01-01 → 2026-09-30 (the verdict).
Honesty: placebo = the same coins on random days (as many per coin and period as the rule traded), the same exits, 50 draws.
Variants: 2 (long, short); luck hurdle N = 520 + 2 = 522.
Verdict per side (fixed now): «правило» only if the out-of-sample mean trade > 0 AND above the out-of-sample placebo p95 AND
t by date ≥ 2; otherwise «наблюдение». Public data only, no keys, no orders.
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import datetime as dt

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voltbreak_all_study as vb  # noqa: E402
import families36_study as f36  # noqa: E402

N_LUCK = 522
COINS = ["ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "DOGE", "DOT", "ETH", "LINK", "LTC", "NEAR", "OP", "SOL", "SUI",
         "TRX", "XRP"]
OOS = int(dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000) // vb.DAY


def add_ll(x):
    n = len(x["c"])
    ll = np.full(n, np.nan)
    for i in range(20, n):
        ll[i] = x["l"][i - 20:i].min()
    x["ll20"] = ll
    return x


def sig(x, i, side):
    c = x["c"]
    r28 = c[i] / c[i - 28] - 1
    if side == "L":
        return r28 > 0 and c[i] >= x["hh20"][i]
    return r28 < 0 and c[i] <= x["ll20"][i]


def trade(x, i, side):
    """Enter at the open of i+1, 5 days, 2 ATR stop. → (days, daily net returns) or None."""
    o, h, l, c, f, d = x["o"], x["h"], x["l"], x["c"], x["f"], x["d"]
    n = len(c)
    if i + 1 >= n or not x["cont"][i + 1]:
        return None
    e = o[i + 1]
    a = x["atr"][i]
    sgn = 1 if side == "L" else -1
    stop = e - sgn * 2 * a
    cost = vb.FEE + vb.SLIP + (vb.SLIP_SMALL if x["q20"][i] < vb.SMALL_Q else 0.0)
    days, rets = [], []
    prev = e
    for k, j in enumerate(range(i + 1, min(n, i + 6)), 1):
        if j > i + 1 and not x["cont"][j]:
            break
        hit = l[j] <= stop if side == "L" else h[j] >= stop
        if hit:
            px = (min(o[j], stop) if side == "L" else max(o[j], stop)) if j > i + 1 else stop
            days.append(int(d[j])); rets.append(sgn * (px / prev - 1) - sgn * f[j])
            break
        days.append(int(d[j])); rets.append(sgn * (c[j] / prev - 1) - sgn * f[j])
        prev = c[j]
    if not rets:
        return None
    rets[0] -= cost
    rets[-1] -= cost
    return days, rets


def main():
    vb.N_LUCK = N_LUCK
    t0 = time.time()
    syms = [c + "USDT" for c in COINS]
    with ThreadPoolExecutor(18) as ex:
        data = [add_ll(f36.prep(x)) for x in ex.map(f36.load, syms) if x is not None]
    vb.note("m49 · loaded", f"{len(data)} of {len(syms)} coins: " + ", ".join(
        f"{x['sym'][:-4]} {dt.datetime.utcfromtimestamp(int(x['d'][0]) * 86400):%Y-%m}" for x in data) + f"; {time.time() - t0:.0f}s")
    d0 = min(int(x["d"][0]) for x in data)
    d1 = max(int(x["d"][-1]) for x in data)
    btc = next(x for x in data if x["sym"] == "BTCUSDT")
    span = np.arange(d0, d1 + 1)
    bpr = np.zeros(len(span))
    for j in range(1, len(btc["c"])):
        bpr[int(btc["d"][j]) - d0] = btc["c"][j] / btc["c"][j - 1] - 1
    rng = np.random.default_rng(49)
    for side in ("L", "S"):
        tr, dates, syml, elig = [], [], [], []
        for x in data:
            last = -1
            ek = []
            for i in range(60, len(x["c"]) - 1):
                if not f36.ok_day(x, i):
                    continue
                ek.append(i)
                if i <= last or not sig(x, i, side):
                    continue
                p = trade(x, i, side)
                if p:
                    tr.append(p); dates.append(int(x["d"][i])); syml.append(x["sym"])
                    last = i + len(p[0])
            elig.append(ek)
        res = {}
        for per, keep in (("IS", lambda dd: dd < OOS), ("OOS", lambda dd: dd >= OOS)):
            idx = [k for k, dd in enumerate(dates) if keep(dd)]
            sub = [tr[k] for k in idx]
            pt = vb.per_trade(sub, [dates[k] for k in idx]) if len(sub) > 2 else {"n": len(sub), "mean_pct": None, "t_by_date": 0}
            cnt = {}
            for k in idx:
                cnt[syml[k]] = cnt.get(syml[k], 0) + 1
            plac = []
            for rep in range(50):
                ptr = []
                for x, ek in zip(data, elig):
                    pool = [i for i in ek if keep(int(x["d"][i]))]
                    nk = cnt.get(x["sym"], 0)
                    if not nk or not pool:
                        continue
                    for j in rng.choice(pool, size=nk, replace=True):
                        p = trade(x, int(j), side)
                        if p:
                            ptr.append(sum(p[1]))
                plac.append(np.mean(ptr) * 100 if ptr else 0.0)
            by_coin = {}
            for k in idx:
                by_coin.setdefault(syml[k][:-4], []).append(sum(tr[k][1]))
            pos = sum(1 for v in by_coin.values() if np.mean(v) > 0)
            sp, pr = vb.portfolio(sub, d0, d1)
            m = (sp >= OOS) if per == "OOS" else (sp < OOS)
            st = vb.stats(sp[m], pr[m])
            bt = vb.stats(span[m], bpr[m])
            res[per] = (pt, float(np.mean(plac)), float(np.percentile(plac, 95)), pos, len(by_coin), st, bt)
        pt, pm, p95, pos, nc, st, bt = res["OOS"]
        ok = pt["mean_pct"] is not None and pt["mean_pct"] > 0 and pt["mean_pct"] > p95 and pt["t_by_date"] >= 2
        ip, ipm, ip95, ipos, inc, ist, ibt = res["IS"]
        yrs = {}
        for t, dd in zip(tr, dates):
            yrs.setdefault(1970 + int(dd // 365.2425), []).append(sum(t[1]))
        ys = {y: f"{np.mean(v) * 100:+.2f}%/{len(v)}" for y, v in sorted(yrs.items())}
        vb.note(f"m49 · {'LONG' if side == 'L' else 'SHORT'}",
                f"{'ПРАВИЛО' if ok else 'НАБЛЮДЕНИЕ'} — OOS 2025-01→2026-09: trades {pt['n']}, mean {pt['mean_pct']}% "
                f"(median {pt.get('median_pct')}%, win {pt.get('win')}%, t by date {pt['t_by_date']}), placebo {pm:.2f}% p95 {p95:.2f}%, "
                f"coins with mean>0 {pos}/{nc}, account Sharpe {st['sharpe']} {st['cagr_pct']}%/yr DD {st['dd_pct']}% vs BTC Sharpe "
                f"{bt['sharpe']} | IS 2020–2024: trades {ip['n']}, mean {ip['mean_pct']}% (t {ip['t_by_date']}), placebo {ipm:.2f}% "
                f"p95 {ip95:.2f}%, coins>0 {ipos}/{inc}, Sharpe {ist['sharpe']} | by year {ys}")
    vb.note("m49 · done", f"N {N_LUCK}; {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
