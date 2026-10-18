"""Usage: python scripts/lab/portfolio_rules.py <alt100_all2023.npz>  (the wide set; not in the repo)

Портфолио-level rules on 2023-06 → 2026-10 daily data (BTC/ETH/SOL equal basket vs stables at 4 %/yr):
A fixed 40 % spot · B regime 60/35 by BTC (close > SMA200 and 90d return > 0) · C/D = A/B + «докупка на просадке за счёт стейблов».
Weekly (Monday) rebalance to target + immediate trade when the target changes; 0.1 % cost on the moved amount."""
import itertools
import datetime as dt
import json
import sys

import numpy as np

Z = np.load(sys.argv[1], allow_pickle=True)
syms = list(Z["syms"])
times = Z["times"]
close = Z["close"]
DAY = 86_400_000
idx = [syms.index(s) for s in ("BTC", "ETH", "SOL")]
c = close[idx].astype(float)
# last 15m bar of each UTC day
end_mask = (times % DAY) == DAY - 900_000
d_t = times[end_mask] // DAY * DAY
d_c = c[:, end_mask]
for i in range(3):                                  # forward-fill gaps
    for j in range(1, d_c.shape[1]):
        if not np.isfinite(d_c[i, j]):
            d_c[i, j] = d_c[i, j - 1]
ok = np.isfinite(d_c).all(0)
d_t, d_c = d_t[ok], d_c[:, ok]
ret = d_c[:, 1:] / d_c[:, :-1] - 1
rb = ret.mean(0)                                    # equal basket, daily rebalanced
t = d_t[1:]
btc = d_c[0, 1:]
basket = np.cumprod(1 + rb)
n = len(t)
sma200 = np.array([btc[max(0, i - 199):i + 1].mean() if i >= 199 else np.nan for i in range(n)])
r90 = np.array([btc[i] / btc[i - 90] - 1 if i >= 90 else np.nan for i in range(n)])
bull = (btc > sma200) & (r90 > 0)
START = 199
RS = 0.04 / 365
COST = 0.001
years = np.array([dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc).year for x in t])
monday = np.array([dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc).weekday() == 0 for x in t])


def sim(base_on, base_off, regime, dip=None):
    vs, vc = 0.0, 10_000.0
    extra, held = 0.0, 0
    vals, sh = [], []
    target_prev = None
    for i in range(START, n):
        if i > START:
            vs *= 1 + rb[i]
            vc *= 1 + RS
        on = bool(bull[i]) if regime else True
        T = base_on if on else base_off
        if dip:
            N, X, E, ex, filt = dip
            hi = basket[max(0, i - N + 1):i + 1].max()
            dd = basket[i] / hi - 1
            if extra > 0:
                held += 1
                rec = (ex == "rec3" and dd >= -0.03) or (ex == "rech" and dd >= -X / 2) or (ex == "h30" and held >= 30) or (ex == "h14" and held >= 14)
                if rec:
                    extra, held = 0.0, 0
            allow = (not filt) or on
            if allow:
                if dd <= -2 * X and extra < 2 * E:
                    extra = 2 * E
                elif dd <= -X and extra < E:
                    extra, held = E, 0
            T = min(0.95, T + extra)
        tot = vs + vc
        if target_prev is None or T != target_prev or monday[i]:
            want = tot * T
            moved = abs(want - vs)
            cost = moved * COST
            vs, vc = want, tot - want - cost
            target_prev = T
        vals.append(vs + vc)
        sh.append(vs / (vs + vc))
    v = np.array(vals)
    y = years[START:]
    peak = np.maximum.accumulate(v)
    out = {"total_pct": round((v[-1] / 10_000 - 1) * 100, 1), "max_dd_pct": round(((v / peak) - 1).min() * 100, 1),
           "avg_spot_pct": round(float(np.mean(sh)) * 100, 1), "years": {}}
    for yr in sorted(set(y)):
        m = np.where(y == yr)[0]
        a = v[m[0] - 1] if m[0] > 0 else 10_000
        out["years"][int(yr)] = round((v[m[-1]] / a - 1) * 100, 1)
    tr = np.where(y < 2025)[0]
    te = np.where(y >= 2025)[0]
    out["train_pct"] = round((v[tr[-1]] / 10_000 - 1) * 100, 1)
    out["test_pct"] = round((v[te[-1]] / v[te[0] - 1] - 1) * 100, 1)
    vt = v[te]
    out["test_dd_pct"] = round((vt / np.maximum.accumulate(vt) - 1).min() * 100, 1)
    return out


res = {"period": [dt.datetime.fromtimestamp(t[START] / 1000, dt.timezone.utc).strftime("%Y-%m-%d"), dt.datetime.fromtimestamp(t[-1] / 1000, dt.timezone.utc).strftime("%Y-%m-%d")],
       "bull_share_pct": round(float(bull[START:].mean()) * 100, 1)}
res["basket_hold_pct"] = round((basket[-1] / basket[START] - 1) * 100, 1)
res["A_fixed40"] = sim(0.40, 0.40, False)
res["B_regime60_35"] = sim(0.60, 0.35, True)
res["B2_regime60_20"] = sim(0.60, 0.20, True)
res["F_fixed60"] = sim(0.60, 0.60, False)
res["regime_grid"] = [{"on": on, "off": off, **{k: v for k, v in sim(on, off, True).items()}}
                      for on in (0.5, 0.6, 0.7) for off in (0.10, 0.20, 0.25, 0.35)]   # 18.10: lower «off» better in 2025–26 for every «on»
grid = []
for base in ("A", "B"):
    for N, X, E, ex, filt in itertools.product((14, 30), (0.07, 0.10, 0.15), (0.10, 0.20), ("rec3", "rech", "h30", "h14"), (False, True)):
        r = sim(0.40, 0.40, False, (N, X, E, ex, filt)) if base == "A" else sim(0.60, 0.35, True, (N, X, E, ex, filt))
        grid.append({"base": base, "N": N, "X": X, "E": E, "exit": ex, "bull_only": filt, **r})
res["grid_n"] = len(grid)
for base in ("A", "B"):
    ref = res["A_fixed40"] if base == "A" else res["B_regime60_35"]
    g = [x for x in grid if x["base"] == base]
    beat_train = [x for x in g if x["train_pct"] > ref["train_pct"]]
    beat_both = [x for x in g if x["train_pct"] > ref["train_pct"] and x["test_pct"] > ref["test_pct"]]
    best_train = max(g, key=lambda x: x["train_pct"] / max(1, -x["max_dd_pct"]))
    res[f"dip_on_{base}"] = {"variants": len(g), "beat_ref_train": len(beat_train), "beat_ref_train_and_test": len(beat_both),
                             "median_test_minus_ref": round(float(np.median([x["test_pct"] - ref["test_pct"] for x in g])), 2),
                             "median_train_minus_ref": round(float(np.median([x["train_pct"] - ref["train_pct"] for x in g])), 2),
                             "picked_on_train": best_train,
                             "top5_by_train": sorted(g, key=lambda x: -x["train_pct"])[:5]}
for b in ("A", "B"):
    res[f"dip_on_{b}"].pop("top5_by_train", None)
print(json.dumps(res, ensure_ascii=False, indent=1, default=float))
