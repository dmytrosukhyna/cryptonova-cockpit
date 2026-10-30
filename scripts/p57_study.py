"""Backlog #57 (30.10.2026): «отскок широты» — buy a liquid basket the day after the RSI(2) pullback P fires on ≥ 10 perps.

Basis — #48 (scripts/p48_study.py): on burst days (≥ 10 coins signal P) random NON-signalling coins on the same dates gave
+2.75 % per trade, i.e. the edge looked like «the whole market bounces», not the coin's own pullback. Found after looking —
so the check is strict and the configuration is fixed BEFORE the run:
  burst day D = BTC in «рост» (close above SMA200 and above the close 60 days ago, as #48) and P true on ≥ 10 perps;
  basket (primary) = the 20 perps with the largest previous-20-day turnover that pass f36.ok_day on D, equal shares (1/20 each,
           gross 1×), entry at the next open, each coin exits with P's own exits (close above its 5-day SMA → next open,
           10 days max, stop 2.5 ATR clipped to 3–15 %); a coin already held is not bought again;
  side check (counted as a variant, not used for the verdict) = BTC + ETH only, 1/2 each, same exits.
Same archive, costs (0.14 % round trip + small-coin slippage) and funding as #36/#48.
Honesty: placebo — random «рост» days (all eligible BTC «рост» days, not burst days), as many as the rule traded, same basket
         construction, 200 draws; mean basket trade by date compared with the placebo p95.
Pass (fixed now): t by date ≥ 2.5, mean basket trade > 0 in both halves, above placebo p95, account Sharpe above BTC
(buy and hold, same span) overall and in both halves. Variants: 2 → luck hurdle N = 519 + 2 = 521.
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

N_LUCK = 521
BURST = 10
TOP = 20


def basket_trades(data, idx, dd, members, held):
    """Trades for one basket bought after day dd. members: list of k. held: dict k → last held day (mutated)."""
    out = []
    w = 1.0 / len(members)
    for k in members:
        i = idx[k].get(dd)
        if i is None or held.get(k, -1) >= dd:
            continue
        p = f36.trade(data[k], i, "P")
        if p:
            out.append((p[0], p[1], w))
            held[k] = p[0][-1]
    return out


def date_means(groups):
    """groups: list of (date, [trades]) → array of basket returns per date (sum of weighted trade returns)."""
    return np.array([sum(t[2] * sum(t[1]) for t in tr) for _, tr in groups if tr])


def main():
    vb.N_LUCK = N_LUCK
    t0 = time.time()
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [f36.prep(x) for x in ex.map(f36.load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    vb.note("p57 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
    kb = next(k for k, x in enumerate(data) if x["sym"] == "BTCUSDT")
    ke = next(k for k, x in enumerate(data) if x["sym"] == "ETHUSDT")
    btc = data[kb]
    bc = btc["c"]
    bull = {}
    for k in range(200, len(bc)):
        bull[int(btc["d"][k])] = bool(bc[k] > bc[k - 199:k + 1].mean() and bc[k] > bc[k - 60])
    d0 = min(int(x["d"][0]) for x in data)
    d1 = live_end
    span = np.arange(d0, d1 + 1)
    bpr = np.zeros(len(span))
    for j in range(1, len(bc)):
        bpr[int(btc["d"][j]) - d0] = bc[j] / bc[j - 1] - 1
    idx = [{int(dd): i for i, dd in enumerate(x["d"])} for x in data]

    size, liq = {}, {}
    for k, x in enumerate(data):
        for i in range(60, len(x["c"]) - 1):
            if not f36.ok_day(x, i):
                continue
            dd = int(x["d"][i])
            if not bull.get(dd, False):
                continue
            liq.setdefault(dd, []).append((float(x["q20"][i]), k))
            if f36.sig(x, i, "P", True):
                size[dd] = size.get(dd, 0) + 1
    bull_days = sorted(liq)
    burst = sorted(dd for dd, n in size.items() if n >= BURST)
    first = bull_days[0] if bull_days else d0
    mid = (first + d1) // 2

    def top(dd):
        return [k for _, k in sorted(liq.get(dd, []), reverse=True)[:TOP]]

    def run(days, members_of):
        held, groups = {}, []
        for dd in days:
            m = members_of(dd)
            if m:
                groups.append((dd, basket_trades(data, idx, dd, m, held)))
        return [g for g in groups if g[1]]

    res = {}
    for name, members_of in (("top20", top), ("btc+eth", lambda dd: [kb, ke])):
        g = run(burst, members_of)
        r = date_means(g)
        dts = np.array([dd for dd, _ in g])
        tt = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 2 else 0.0
        h1, h2 = r[dts < mid], r[dts >= mid]
        trades = [t for _, tr in g for t in tr]
        sp, pr = f36.wport(trades, d0, d1, 1.0)
        st = vb.stats(sp, pr)
        by_year = {}
        for dd, v in zip(dts, r):
            by_year.setdefault(1970 + int(dd // 365.2425), []).append(v)
        res[name] = dict(g=g, r=r, t=tt, h=(h1, h2), st=st,
                         yrs={y: f"{np.mean(v) * 100:+.2f}%/{len(v)}" for y, v in sorted(by_year.items())})

    bb = vb.stats(span[span >= first], bpr[span >= first])
    rng = np.random.default_rng(57)
    plc = {"top20": [], "btc+eth": []}
    n_rule = len(res["top20"]["g"])
    for rep in range(200):
        pick = sorted(int(v) for v in rng.choice(bull_days, size=min(n_rule, len(bull_days)), replace=False))
        for name, members_of in (("top20", top), ("btc+eth", lambda dd: [kb, ke])):
            r = date_means(run(pick, members_of))
            plc[name].append(float(r.mean()) * 100 if len(r) else 0.0)

    for name in ("top20", "btc+eth"):
        x = res[name]
        r, st = x["r"], x["st"]
        m = float(r.mean()) * 100
        h1 = float(x["h"][0].mean()) * 100 if len(x["h"][0]) else float("nan")
        h2 = float(x["h"][1].mean()) * 100 if len(x["h"][1]) else float("nan")
        p95 = float(np.percentile(plc[name], 95))
        ok = (x["t"] >= 2.5 and h1 > 0 and h2 > 0 and m > p95 and st["sharpe"] > bb["sharpe"]
              and st["halves"][0] > bb["halves"][0] and st["halves"][1] > bb["halves"][1])
        tag = "PRIMARY" if name == "top20" else "side check"
        vb.note(f"p57 · {name} ({tag})",
                f"{'PASS' if ok else 'no'} — {len(r)} burst dates of {len(burst)} (bull days {len(bull_days)}), mean basket "
                f"{m:+.2f}% (median {np.median(r) * 100:+.2f}%, win {(r > 0).mean() * 100:.0f}%, t by date {x['t']:.2f}), "
                f"halves {h1:+.2f}% ({len(x['h'][0])}) / {h2:+.2f}% ({len(x['h'][1])}); placebo random «рост» days "
                f"{np.mean(plc[name]):+.2f}% p95 {p95:+.2f}%; account Sharpe {st['sharpe']} (halves {st['halves'][0]}/"
                f"{st['halves'][1]}) vs BTC {bb['sharpe']} ({bb['halves'][0]}/{bb['halves'][1]}), {st['cagr_pct']}%/yr, DD "
                f"{st['dd_pct']}%, hurdle {st['hurdle']}; by year {x['yrs']}; account years {st['years']}")
    g = res["top20"]["g"]
    worst = sorted(zip(res["top20"]["r"], [dd for dd, _ in g]))[:4]
    vb.note("p57 · worst / burst sizes", "worst: " + ", ".join(
        f"{time.strftime('%Y-%m-%d', time.gmtime(dd * 86400))} {v * 100:+.1f}%" for v, dd in worst)
        + f"; burst size median {int(np.median([size[d] for d in burst])) if burst else 0}, max "
        f"{max(size[d] for d in burst) if burst else 0}; BTC buy-hold from {time.strftime('%Y-%m-%d', time.gmtime(first * 86400))}"
        f" {bb['cagr_pct']}%/yr DD {bb['dd_pct']}%; {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
