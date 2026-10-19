"""«Пробой 20 дней» (own desk): entry after the daily close with the volume mark (now) vs a buy-stop order placed in advance at
the 20-day high (fills during the day), with or without a check at the day's close. 18 coins, daily from 15m, 2021 → 2026-09."""
import numpy as np, datetime as dt
z = np.load(__import__("os").environ.get("CNQ_AP_NPZ", "aligned18.npz"), allow_pickle=True)
syms, times = list(z["syms"]), z["times"]
O, H, L, C, V = z["open"], z["high"], z["low"], z["close"], z["volume_quote"]
day = times // 86_400_000
udays = np.unique(day)
idx = np.searchsorted(udays, day)
ND = len(udays)
def daily(k):
    o = np.full(ND, np.nan); h = np.full(ND, np.nan); l = np.full(ND, np.nan); c = np.full(ND, np.nan); v = np.zeros(ND)
    ok = np.isfinite(C[k])
    for j in np.flatnonzero(ok):
        d = idx[j]
        if np.isnan(o[d]): o[d] = O[k, j]
        h[d] = H[k, j] if np.isnan(h[d]) else max(h[d], H[k, j]); l[d] = L[k, j] if np.isnan(l[d]) else min(l[d], L[k, j])
        c[d] = C[k, j]; v[d] += V[k, j] if np.isfinite(V[k, j]) else 0
    return o, h, l, c, v
D = [daily(k) for k in range(len(syms))]
bk = syms.index("BTCUSDT")
bc = D[bk][3]
def sma(x, n):
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        if np.all(np.isfinite(w)): out[i] = w.mean()
    return out
s200 = sma(bc, 200)
bull = np.array([np.isfinite(s200[i]) and bc[i] > s200[i] and i >= 60 and bc[i] > bc[i - 60] for i in range(ND)])
years = np.array([dt.datetime.fromtimestamp(d * 86400, dt.timezone.utc).year for d in udays])
TEST0 = np.searchsorted(udays, int(dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp() // 86400))
START = np.searchsorted(udays, int(dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp() // 86400))
FEE, SLIP = 0.0005, 0.0002
res = {}
for k in range(len(syms)):
    o, h, l, c, v = D[k]
    tr = np.r_[np.nan, np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))]
    atr = np.full(ND, np.nan); a = None
    for i in range(1, ND):
        if np.isfinite(tr[i]): a = tr[i] if a is None else a + (tr[i] - a) / 14; atr[i] = a
    s20 = sma(c, 20)
    dv30 = np.full(ND, np.nan)
    for i in range(30, ND): dv30[i] = np.nanmean(v[i - 30:i])
    def run_trade(e_day, entry, a_):
        stop = entry - min(max(2.5 * a_, 0.03 * entry), 0.15 * entry)
        best = entry
        for j in range(e_day, min(ND, e_day + 14)):
            if not np.isfinite(l[j]): break
            if l[j] <= stop: return (stop * (1 - SLIP) - entry) / entry - 2 * FEE, j
            best = max(best, c[j])
            if c[j] < best - 3 * a_ and j > e_day: return (c[j] - entry) / entry - 2 * FEE, j
        j = min(ND - 1, e_day + 13)
        while j > e_day and not np.isfinite(c[j]): j -= 1
        return (c[j] - entry) / entry - 2 * FEE, j
    busy = {}
    for d in range(max(START, 31), ND - 1):
        if not (bull[d] and np.isfinite(c[d]) and np.isfinite(atr[d - 1]) and np.isfinite(s20[d - 1])): continue
        hi20 = np.nanmax(c[d - 20:d]) if np.all(np.isfinite(c[d - 20:d])) else np.nan
        if not np.isfinite(hi20): continue
        volok = np.isfinite(dv30[d]) and v[d] >= 1.5 * dv30[d]
        # (a) now: confirmed at the close (close > 20-day high and > SMA20, volume ≥ 1.5×) → next day's open
        if c[d] > hi20 and c[d] > s20[d] and volok and d > busy.get("a", -1) and np.isfinite(o[d + 1]):
            r, x = run_trade(d + 1, o[d + 1] * (1 + SLIP), atr[d]); res.setdefault("сейчас: после закрытия дня, с объёмом", []).append((d, r)); busy["a"] = x
        # (b) buy-stop placed in advance at the 20-day high (valid for day d), filled during the day; no volume check
        if h[d] >= hi20 and o[d] < hi20 and c[d - 1] > s20[d - 1] * 0.97 and d > busy.get("b", -1):
            entry = hi20 * (1 + SLIP)
            r, x = run_trade(d, entry, atr[d - 1]); res.setdefault("ордер заранее на пробой, без проверки", []).append((d, r)); busy["b"] = x
            # (c) the same, but at the day's close: if the close fell back under the level or the volume is under 1.5× → out
            if c[d] <= hi20 or not volok:
                r2 = (c[d] - entry) / entry - 2 * FEE
                res.setdefault("ордер заранее + проверка на закрытии дня", []).append((d, r2))
            else:
                res.setdefault("ордер заранее + проверка на закрытии дня", []).append((d, r))
for k_, rows in res.items():
    r = np.array([x[1] for x in rows]); dd = np.array([x[0] for x in rows])
    yrs = {}
    for d, x in rows: yrs.setdefault(int(years[d]), []).append(x)
    print(k_, {"n": len(r), "avg %": round(r.mean() * 100, 2), "win %": round((r > 0).mean() * 100), "2021–24 %": round(r[dd < TEST0].mean() * 100, 2),
               "2025–26 %": round(r[dd >= TEST0].mean() * 100, 2) if (dd >= TEST0).any() else None, "n 2025–26": int((dd >= TEST0).sum()),
               "years +": f"{sum(1 for v in yrs.values() if np.mean(v) > 0)}/{len(yrs)}"})
