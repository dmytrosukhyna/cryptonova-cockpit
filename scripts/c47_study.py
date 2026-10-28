"""Backlog #47 (28.10.2026): breakout C («Сигналы CRYPTO NOVA») on liquid perps with a position cap fixed BEFORE the run.

Basis — #36 (scripts/families36_study.py): C held per trade on all 820 perps (+2.84 %, t 3.9) but the account lost 74 % at
the worst point because stops came in bunches. Question: does a hard cap on simultaneous bets turn the per-trade edge into
an account that beats BTC?

One configuration, fixed now (no tuning):
  universe — every Binance USDT-M perpetual 2020-01 → 2026-09, delisted included (same archive, costs, funding and C rule
             as #36), but a signal counts only when the coin's 20-day turnover is ≥ $20M (vb.LIQ_Q);
  sizing   — 10 % of equity per trade, at most 10 open trades (gross ≤ 1×); one position per coin;
  crowding — when a day has more signals than free slots, the larger signal-day dollar volume goes first, the rest are skipped.
Pass (fixed now): account drawdown better than −35 % AND account Sharpe above BTC's in BOTH halves of the period.
Also reported (not extra variants): mean trade vs a placebo (random «рост» days on the same coins, same cap and order, 20
draws) and the account Sharpe of those placebo draws; years; the uncapped liquid C for reference.
Variants: 1; luck hurdle N = 516 + 1 = 517. Public data only, no keys, no orders.
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voltbreak_all_study as vb  # noqa: E402
import families36_study as f36  # noqa: E402

N_LUCK = 517
SLOTS, W, DD_MAX = 10, 0.10, -35.0


def capped(cands):
    """cands: list of (signal_day, volume, sym, (days, rets, w)). → accepted list in the same form (weight 10 %)."""
    by_day = {}
    for c in cands:
        by_day.setdefault(c[0], []).append(c)
    open_until = []                                        # last held day of each open trade
    busy = {}                                              # sym → last held day
    out = []
    for day in sorted(by_day):
        entry = day + 1
        open_until = [u for u in open_until if u >= entry]
        for c in sorted(by_day[day], key=lambda c: -c[1]):
            if len(open_until) >= SLOTS:
                break
            if busy.get(c[2], -1) >= entry:
                continue
            days, rets, _ = c[3]
            out.append((c[0], c[1], c[2], (days, rets, W)))
            open_until.append(days[-1])
            busy[c[2]] = days[-1]
    return out


def main():
    vb.N_LUCK = N_LUCK
    t0 = time.time()
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [f36.prep(x) for x in ex.map(f36.load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    vb.note("c47 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
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

    cands, elig, cnt = [], [], {}
    for k, x in enumerate(data):
        last = -1
        ek = []
        for i in range(60, len(x["c"]) - 1):
            if not f36.ok_day(x, i) or x["q20"][i] < vb.LIQ_Q:
                continue
            b = bull.get(int(x["d"][i]), False)
            if b:
                ek.append(i)
            if i <= last or not f36.sig(x, i, "C", b):
                continue
            p = f36.trade(x, i, "C")
            if p:
                cands.append((int(x["d"][i]), float(x["q"][i]), x["sym"], p))
                cnt[x["sym"]] = cnt.get(x["sym"], 0) + 1
                last = i + len(p[0])
        elig.append(ek)

    def account(sel, gross=1.0):
        sp, pr = f36.wport([c[3] for c in sel], d0, d1, gross)
        return vb.stats(sp, pr)

    # reference: uncapped liquid C with the live desk's risk sizing (as #36)
    ref = account(cands, 2.0) if cands else None
    acc = capped(cands)
    st = account(acc)
    pt = vb.per_trade([(c[3][0], c[3][1]) for c in acc], [c[0] for c in acc])
    m1 = f36.tmean([c[3] for c in acc if c[0] < mid])
    m2 = f36.tmean([c[3] for c in acc if c[0] >= mid])
    skipped = len(cands) - len(acc)
    by_year = {}
    for c in acc:
        by_year.setdefault(1970 + int(c[0] // 365.2425), []).append(sum(c[3][1]))
    yrs = {y: f"{np.mean(v) * 100:+.2f}%/{len(v)}" for y, v in sorted(by_year.items())}

    rng = np.random.default_rng(47)
    pmean, psh = [], []
    for rep in range(20):
        pc = []
        for k, x in enumerate(data):
            nk = cnt.get(x["sym"], 0)
            if not nk or not elig[k]:
                continue
            for j in rng.choice(elig[k], size=nk, replace=True):
                p = f36.trade(x, int(j), "C")
                if p:
                    pc.append((int(x["d"][j]), float(x["q"][j]), x["sym"], p))
        pa = capped(pc)
        if pa:
            pmean.append(float(np.mean([sum(c[3][1]) for c in pa])) * 100)
            psh.append(account(pa)["sharpe"])
    p95 = float(np.percentile(pmean, 95)) if pmean else 0.0
    sh95 = float(np.percentile(psh, 95)) if psh else 0.0
    ok = st["dd_pct"] > DD_MAX and st["halves"][0] > bb["halves"][0] and st["halves"][1] > bb["halves"][1]
    vb.note("c47 · C capped", f"{'PASS' if ok else 'no'} — candidates {len(cands)}, taken {len(acc)}, skipped {skipped} (cap "
            f"{SLOTS}×{int(W * 100)}%); trades mean {pt['mean_pct']}% (median {pt['median_pct']}%, win {pt['win']}%, t by date "
            f"{pt['t_by_date']}, {pt['dates']} days), halves {m1}% / {m2}%; placebo mean {round(float(np.mean(pmean)), 2) if pmean else None}% "
            f"p95 {round(p95, 2)}%; account Sharpe {st['sharpe']} (halves {st['halves'][0]}/{st['halves'][1]} vs BTC "
            f"{bb['halves'][0]}/{bb['halves'][1]}), {st['cagr_pct']}%/yr, DD {st['dd_pct']}%, hurdle {st['hurdle']}; placebo "
            f"account Sharpe mean {round(float(np.mean(psh)), 2) if psh else None} p95 {round(sh95, 2)}; years {st['years']}; "
            f"trade mean by year {yrs}")
    if ref:
        vb.note("c47 · reference", f"uncapped liquid C, live risk sizing, gross ≤ 2×: {len(cands)} trades, Sharpe {ref['sharpe']} "
                f"(halves {ref['halves']}), {ref['cagr_pct']}%/yr, DD {ref['dd_pct']}%, years {ref['years']}")
    vb.note("c47 · BTC", f"Sharpe {bb['sharpe']} halves {bb['halves']} {bb['cagr_pct']}%/yr DD {bb['dd_pct']}% years {bb['years']}; "
            f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
