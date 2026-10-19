"""Pre-placed limit orders vs the current market entry for the CNPTS cascade buy (class B), 18 coins, 15m, 2020-09 → 2026-09."""
import numpy as np, datetime as dt
z = np.load(__import__("os").environ.get("CNQ_AP_NPZ", "aligned18.npz"), allow_pickle=True)
syms, times = list(z["syms"]), z["times"]
O, H, L, C = z["open"], z["high"], z["low"], z["close"]
NS, T = C.shape
years = np.array([dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).year for t in times])
TEST0 = np.searchsorted(times, int(dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000))
START = np.searchsorted(times, int(dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000))
TAKER, MAKER, SLIP = 0.0005, 0.0002, 0.0002
HOLD = 32  # 8 h

def atr14(k):
    tr = np.maximum(H[k][1:] - L[k][1:], np.maximum(abs(H[k][1:] - C[k][:-1]), abs(L[k][1:] - C[k][:-1])))
    tr = np.r_[np.nan, tr]
    out = np.full(T, np.nan); a = None
    for i in range(1, T):
        if not np.isfinite(tr[i]): continue
        a = tr[i] if a is None else a + (tr[i] - a) / 14
        out[i] = a
    return out

def sigma30(k):
    r = np.r_[np.nan, C[k][1:] / C[k][:-1] - 1]
    r2 = np.where(np.isfinite(r), r * r, 0.0); n = np.where(np.isfinite(r), 1, 0)
    cs, cn = np.cumsum(r2), np.cumsum(n)
    W = 96 * 30
    out = np.full(T, np.nan)
    out[W:] = np.sqrt((cs[W:] - cs[:-W]) / np.maximum(1, cn[W:] - cn[:-W]))
    # σ known at the close of bar i uses bars up to i−1 (like the desk: returns up to the previous closed bar)
    return np.r_[np.nan, out[:-1]]

def manage(k, e, entry, stop, first_check_bar):
    """1R target, stop first, time exit after HOLD bars from e. Returns (ret_gross, exit_bar)."""
    r = entry - stop
    tgt = entry + r
    end = min(T - 1, e + HOLD - 1)
    for j in range(first_check_bar, end + 1):
        if not np.isfinite(L[k, j]): break
        if L[k, j] <= stop: return (stop * (1 - SLIP) - entry) / entry, j
        if H[k, j] >= tgt: return (tgt - entry) / entry, j
    j = end
    while j > e and not np.isfinite(C[k, j]): j -= 1
    return (C[k, j] - entry) / entry, j

res = {}
def add(name, k, i, ret):
    res.setdefault(name, []).append((k, i, ret))

for k in range(NS):
    A, S = atr14(k), sigma30(k)
    busy = {}
    for i in range(max(START, 96 * 31), T - 2):
        if not (np.isfinite(C[k, i]) and np.isfinite(C[k, i - 1]) and np.isfinite(S[i]) and S[i] > 0 and np.isfinite(A[i])): continue
        # baseline B: signal at the close of bar i, market entry at the open of i+1
        ret = C[k, i] / C[k, i - 1] - 1
        rng = H[k, i] - L[k, i]
        if ret <= -3.5 * S[i] and rng > 0 and (min(O[k, i], C[k, i]) - L[k, i]) / rng >= 0.5 and i > busy.get("B", -1):
            entry = O[k, i + 1] * (1 + SLIP)
            stop = L[k, i] - 0.5 * A[i]
            if entry > stop:
                g, x = manage(k, i + 1, entry, stop, i + 1)
                add("B: рынок после свечи (как сейчас)", k, i, g - 2 * TAKER)
                busy["B"] = x
        # the same B signal, entered later (the cost of a slow step): +15 / +30 minutes
        if ret <= -3.5 * S[i] and rng > 0 and (min(O[k, i], C[k, i]) - L[k, i]) / rng >= 0.5:
            for d in (1, 2):
                key = f"B: вход на {15 * d} мин позже"
                if i <= busy.get(key, -1) or i + 1 + d >= T or not np.isfinite(O[k, i + 1 + d]): continue
                entry = O[k, i + 1 + d] * (1 + SLIP)
                stop = L[k, i] - 0.5 * A[i]
                lo_between = np.nanmin(L[k, i + 1:i + 1 + d])
                if entry > stop and lo_between > stop:
                    g, x = manage(k, i + 1 + d, entry, stop, i + 1 + d)
                    add(key, k, i, g - 2 * TAKER)
                    busy[key] = x
        # pre-placed: a buy limit at prev close × (1 − m·σ) for bar i (re-placed every bar), stop m2 ATR below, 1R target
        for m, m2 in ((3.5, 1.0), (4.5, 1.0), (5.5, 1.0), (4.5, 1.5)):
            key = f"лимитка заранее −{m}σ, стоп {m2} ATR"
            if i <= busy.get(key, -1): continue
            lvl = C[k, i - 1] * (1 - m * S[i])
            if L[k, i] <= lvl and O[k, i] > lvl:
                entry = lvl
                stop = lvl - m2 * A[i - 1]
                if L[k, i] <= stop:          # the fill bar went through the stop too → stopped (conservative)
                    g, x = (stop * (1 - SLIP) - entry) / entry, i
                else:
                    g, x = manage(k, i, entry, stop, i + 1)
                add(key, k, i, g - MAKER - TAKER)
                busy[key] = x

def summ(rows):
    r = np.array([x[2] for x in rows]); ii = np.array([x[1] for x in rows])
    tr, te = r[ii < TEST0], r[ii >= TEST0]
    yrs = {}
    for (_, i, v) in rows: yrs.setdefault(int(years[i]), []).append(v)
    return {"n": len(r), "avg %": round(r.mean() * 100, 3), "win %": round((r > 0).mean() * 100), "2021–24 avg %": round(tr.mean() * 100, 3) if len(tr) else None,
            "2025–26 avg %": round(te.mean() * 100, 3) if len(te) else None, "n 2025–26": len(te),
            "years +": f"{sum(1 for v in yrs.values() if np.mean(v) > 0)}/{len(yrs)}", "sum %": round(r.sum() * 100)}
for k_, v in res.items():
    print(k_, summ(v))
