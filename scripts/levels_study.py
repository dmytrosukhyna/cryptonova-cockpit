"""Levels by volume — do trades from strong volume levels work? (19.10.2026, owner: «если работать от уровней, грамотно расставить
их на графике, учитывать объёмы, стаканы и основные метрики — у нас всё получится»). Runs in GitHub Actions only
(.github/workflows/research-levels.yml). Order books have no history to test on (we keep only today's snapshots), so this checks
the part history can answer: levels drawn from VOLUME.

Earlier checks: channel edges and false breaks lose (channels_false_breaks.md), trading inside a range loses (range.md), a retest
of a broken level gives a small plus (+0.04…+0.07 R, retest.md). New here: the volume profile.

Data: Binance USDT-M 1-hour candles with volume, 2023-01 → today, 20 liquid coins. Every 4 hours the volume profile of the last
30 days (60 price bins, volume at the typical price) gives the strong levels: bins with volume ≥ 1.5× the median bin that are
local peaks (high-volume nodes).
Long setup: an hour candle dips to the nearest strong level BELOW the price (low within 0.3 % of it or through it by less than
0.5 ATR) and closes back above it; filters: volume of that hour ≥ 1.5× its 20-hour average (absorption) or not; BTC regime.
Short: the mirror at the nearest strong level ABOVE. Entry at the next hour's open; stop 0.5 / 1 ATR beyond the level;
exit at the next strong level (or 2 R if it is farther), or after 48 hours. Costs 0.14 % a trade; stop first inside an hour.
Compared with a random entry of the same coin and year with the same stop and target distances (in R). Stdlib + numpy."""
import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from fetch_alt100 import http, months_between, note, read_zip_csv  # noqa: E402

UM = "https://data.binance.vision/data/futures/um"
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "BCH", "NEAR", "ATOM", "APT", "ARB", "OP",
         "INJ", "SUI", "UNI"]
TODAY = dt.datetime.now(dt.timezone.utc).date()
COST = 0.14
H = 3_600_000
PROF_H, BINS, EVERY = 720, 60, 4


def hourly(sym):
    rows = []
    last_full = (TODAY.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    for mo in months_between("2022-11", last_full):
        s, b = http(f"{UM}/monthly/klines/{sym}/1h/{sym}-1h-{mo}.zip", binary=True)
        if s == 200:
            rows += read_zip_csv(b)
    if len(rows) < 5000:
        return None
    seen = {}
    for r in rows:
        t = int(r[0]); t = t // 1000 if t > 10 ** 14 else t
        seen[t] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]) if len(r) > 7 else float(r[5]))
    ts = sorted(seen)
    a = np.array([seen[t] for t in ts])
    return {"t": np.array(ts, dtype=np.int64), "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "v": a[:, 4]}


def atr(K, n=14):
    pc = np.r_[K["c"][0], K["c"][:-1]]
    tr = np.maximum(K["h"] - K["l"], np.maximum(abs(K["h"] - pc), abs(K["l"] - pc)))
    out = np.full(len(tr), np.nan)
    cs = np.cumsum(np.insert(tr, 0, 0.0))
    out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def levels_at(K, i):
    """Strong volume levels from the 30 days before bar i → sorted array of prices."""
    a = i - PROF_H
    hi, lo = K["h"][a:i].max(), K["l"][a:i].min()
    if hi <= lo:
        return np.array([])
    tp = (K["h"][a:i] + K["l"][a:i] + K["c"][a:i]) / 3
    hist, edges = np.histogram(tp, bins=BINS, range=(lo, hi), weights=K["v"][a:i])
    med = np.median(hist[hist > 0]) if (hist > 0).any() else 0
    peaks = [j for j in range(1, BINS - 1) if hist[j] >= hist[j - 1] and hist[j] >= hist[j + 1] and hist[j] >= 1.5 * med]
    return np.array([(edges[j] + edges[j + 1]) / 2 for j in peaks])


def trades_for(K, btc_bull, vol_filter, stop_k, use_regime):
    t, o, h, l, c, v = K["t"], K["o"], K["h"], K["l"], K["c"], K["v"]
    A = atr(K)
    v20 = np.r_[np.full(20, np.nan), np.convolve(v, np.ones(20) / 20, mode="valid")[:-1]]
    out, i, lv, lv_i = [], PROF_H + 1, None, -1
    n = len(t)
    while i < n - 50:
        if i - lv_i >= EVERY or lv is None:
            lv, lv_i = levels_at(K, i), i
        if len(lv) < 2 or np.isnan(A[i]):
            i += 1
            continue
        px = c[i - 1]
        below, above = lv[lv < px], lv[lv > px]
        sig = 0
        if len(below):
            L = below.max()
            if l[i] <= L * 1.003 and l[i] >= L - 0.5 * A[i] and c[i] > L:
                sig = 1
        if not sig and len(above):
            L = above.min()
            if h[i] >= L * 0.997 and h[i] <= L + 0.5 * A[i] and c[i] < L:
                sig = -1
        if sig and vol_filter and not (v20[i] > 0 and v[i] >= 1.5 * v20[i]):
            sig = 0
        if sig and use_regime:
            b = btc_bull.get(int(t[i] // 86_400_000 * 86_400_000))
            if b is None or (sig == 1 and not b) or (sig == -1 and b):
                sig = 0
        if not sig:
            i += 1
            continue
        e = o[i + 1]
        stop = L - sig * stop_k * A[i]
        risk = abs(e - stop)
        if risk <= 0 or risk / e > 0.08:
            i += 1
            continue
        nxt = (lv[lv > e].min() if (lv > e).any() else e + 2 * risk) if sig == 1 else (lv[lv < e].max() if (lv < e).any() else e - 2 * risk)
        tgt = nxt if abs(nxt - e) <= 2 * risk else e + sig * 2 * risk
        if abs(tgt - e) < 0.5 * risk:
            tgt = e + sig * 0.5 * risk
        j, px_out = i + 1, None
        while j < min(n, i + 49):
            if (sig == 1 and l[j] <= stop) or (sig == -1 and h[j] >= stop):
                px_out = stop
                break
            if (sig == 1 and h[j] >= tgt) or (sig == -1 and l[j] <= tgt):
                px_out = tgt
                break
            j += 1
        if px_out is None:
            j = min(n - 1, i + 48)
            px_out = c[j]
        r = (sig * (px_out - e) - COST / 100 * e) / risk
        out.append((int(t[i + 1]), sig, float(r), float(abs(tgt - e) / risk), float(risk / e)))
        i = j + 1
    return out


def random_r(K, trades, seed=7):
    """Random entries of the same coin and year with each trade's own stop and target distances (in R)."""
    rng = np.random.default_rng(seed)
    t, o, h, l, c = K["t"], K["o"], K["h"], K["l"], K["c"]
    yrs = np.array([dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc).year for x in t])
    out = []
    for tt, sig, _r, tr, rp in trades:
        y = dt.datetime.fromtimestamp(tt / 1000, dt.timezone.utc).year
        idx = np.where(yrs == y)[0]
        idx = idx[idx < len(t) - 50]
        if not len(idx):
            continue
        for i in rng.choice(idx, 3):
            e = o[i]
            risk = rp * e
            stop, tgt = e - sig * risk, e + sig * tr * risk
            px = None
            for j in range(i, i + 48):
                if (sig == 1 and l[j] <= stop) or (sig == -1 and h[j] >= stop):
                    px = stop
                    break
                if (sig == 1 and h[j] >= tgt) or (sig == -1 and l[j] <= tgt):
                    px = tgt
                    break
            if px is None:
                px = c[i + 47]
            out.append((sig * (px - e) - COST / 100 * e) / risk)
    return float(np.mean(out)) if out else None


def main():
    with ThreadPoolExecutor(10) as ex:
        raw = dict(zip(COINS, ex.map(lambda s: hourly(s + "USDT"), COINS)))
    KS = {s: k for s, k in raw.items() if k is not None}
    # BTC regime by day from the hourly closes (200-day average, up over 60 days)
    B = KS["BTC"]
    day_t = B["t"] // 86_400_000 * 86_400_000
    days = sorted(set(day_t.tolist()))
    dclose = np.array([B["c"][day_t == d][-1] for d in days])
    bull = {}
    for k, d in enumerate(days):
        if k >= 200:
            bull[d + 86_400_000] = bool(dclose[k] > dclose[k - 199:k + 1].mean() and dclose[k] > dclose[k - 60])
    t0 = int(dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    n_days = (TODAY - dt.date(2023, 1, 1)).days
    res = {}
    for vf in (False, True):
        for sk in (0.5, 1.0):
            for rg in (False, True):
                name = f"{'vol' if vf else 'any'}·stop{sk}·{'regime' if rg else 'both'}"
                allr, base, by_y = [], [], {}
                for s, K in KS.items():
                    tr = [x for x in trades_for(K, bull, vf, sk, rg) if x[0] >= t0]
                    if not tr:
                        continue
                    allr += tr
                    b = random_r(K, tr)
                    if b is not None:
                        base.append((b, len(tr)))
                    for x in tr:
                        by_y.setdefault(dt.datetime.fromtimestamp(x[0] / 1000, dt.timezone.utc).strftime("%Y"), []).append(x[2])
                if not allr:
                    continue
                rr = np.array([x[2] for x in allr])
                res[name] = {"n": len(allr), "per_day": round(len(allr) / n_days, 2), "avg_R": round(float(rr.mean()), 3),
                             "win_pct": round(float((rr > 0).mean()) * 100),
                             "random_R": round(sum(b * w for b, w in base) / max(1, sum(w for _, w in base)), 3),
                             "long_R": round(float(np.mean([x[2] for x in allr if x[1] == 1])), 3) if any(x[1] == 1 for x in allr) else None,
                             "short_R": round(float(np.mean([x[2] for x in allr if x[1] == -1])), 3) if any(x[1] == -1 for x in allr) else None,
                             "y": {y: round(float(np.mean(v)), 3) for y, v in sorted(by_y.items())},
                             "risk_pct_median": round(float(np.median([x[4] for x in allr])) * 100, 2)}
    note("levels · volume-profile levels, R per trade after costs", json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
