"""Backlog #76 + #78 (05.11.2026): rule B «откуп каскада» — is the −3.5σ threshold and the «lower wick ≥ 0.5» filter right?

Rule B (cnq/agents/paper_desk.py, live): last closed 15m candle return ≤ −3.5σ (σ = RMS of the 15m returns of the last
30 days, the candle included) with a lower wick ≥ 0.5 of its range → market long, stop = candle low − 0.5 ATR(14, 15m),
target 1R, time exit after 8 h. Live diary 04–05.11: BTC −3.12σ bounced (near miss, #76); 8 candles ≤ −3.5σ with a
short wick gave 0 entries, NEAR then +6 % (#78). Both are «found after looking at one case» — so the test is strict and
the decision rules are fixed BEFORE the run, without tuning any threshold:

  G1 «near zone»  −3.5σ < r ≤ −3.0σ, wick ≥ 0.5   (#76)
  G2 «current B»  r ≤ −3.5σ,          wick ≥ 0.5   (reference, the live rule)
  G3 «short tail» r ≤ −3.5σ,          wick < 0.5   (#78)
Same B geometry for all: entry at the next 15m open, stop low − 0.5 ATR, target entry + R, exit at the close of the 32nd
bar; stop is assumed first when stop and target sit in the same bar (conservative); cost 0.14 % round trip; one open
trade per coin per group. 18 terminal coins, Binance USDT-M perpetuals, 2023-01 → last full month.

Decision (fixed now):
  #76: G1 mean ≤ 0 after costs → the −3.5σ threshold is right, close the item. G1 «candidate for discussion» only if the
       mean is > 0 in EVERY year 2023–2026 AND day-clustered t ≥ 2.5 AND both halves > 0. Anything between → no change.
  #78: G3 mean ≤ 0 in ≥ 3 of 4 years → the wick filter is right. G3 candidate only if > 0 in every year, t ≥ 2.5, both
       halves > 0. Anything between → no change.
Reported, not gates: sessions (UTC 0–8 / 8–16 / 16–24), G3 within 2 h after a BTC +2 % hour, per-coin counts.
Luck: 2 hypotheses, family count N = 533 (z printed for reference). Public data only, no keys, no orders.
"""
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voltbreak_all_study as vb  # noqa: E402

N_LUCK = 533
COINS = ["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "BCH", "TRX", "NEAR",
         "APT", "ARB", "OP", "SUI"]
M_FROM = "2023-01"
WIN = 96 * 30          # σ window: 30 days of 15m bars
HOLD = 32              # 8 h
COST = 0.0014
YEARS = (2023, 2024, 2025, 2026)


def load15(sym):
    months = [m for m in vb.s3_months(f"data/futures/um/monthly/klines/{sym}/15m/") if m >= M_FROM]
    rows = []
    for m in months:
        s, b = vb.get(f"{vb.VISION}/monthly/klines/{sym}/15m/{sym}-15m-{m}.zip")
        if s == 200:
            rows += [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in vb.zrows(b)]
    if not rows:
        return None
    a = np.array(sorted(set(rows)), dtype=float)
    return {"t": a[:, 0].astype(np.int64), "o": a[:, 1], "h": a[:, 2], "l": a[:, 3], "c": a[:, 4], "last": months[-1]}


def signals(x):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]
    n = len(c)
    r = np.zeros(n); r[1:] = c[1:] / c[:-1] - 1
    cs = np.concatenate([[0.0], np.cumsum(r * r)])
    sig = np.full(n, np.nan)
    idx = np.arange(WIN, n)
    sig[idx] = np.sqrt((cs[idx + 1] - cs[idx + 1 - WIN]) / WIN)
    tr = np.zeros(n); tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    ct = np.concatenate([[0.0], np.cumsum(tr)])
    atr = np.full(n, np.nan); j = np.arange(14, n); atr[j] = (ct[j + 1] - ct[j + 1 - 14]) / 14
    rng = h - l
    with np.errstate(invalid="ignore", divide="ignore"):
        z = r / sig
        wick = np.where(rng > 0, (np.minimum(o, c) - l) / rng, np.nan)
    return z, wick, atr


def trade(x, j, atr):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]
    if j + 1 + HOLD > len(c):
        return None
    e = o[j + 1]; stop = l[j] - 0.5 * atr[j]; R = e - stop
    if not (R > 0 and np.isfinite(R)):
        return None
    tgt = e + R
    for k in range(j + 1, j + 1 + HOLD):
        if l[k] <= stop:
            return stop / e - 1 - COST, k
        if h[k] >= tgt:
            return tgt / e - 1 - COST, k
    k = j + HOLD
    return c[k] / e - 1 - COST, k


def stats(tr):
    if not tr:
        return {"n": 0}
    ret = np.array([t["ret"] for t in tr])
    days = {}
    for t in tr:
        days.setdefault(t["day"], []).append(t["ret"])
    dm = np.array([np.mean(v) for v in days.values()])
    tt = dm.mean() / (dm.std(ddof=1) / math.sqrt(len(dm))) if len(dm) > 2 and dm.std() > 0 else 0.0
    srt = sorted(tr, key=lambda t: t["ts"]); half = len(srt) // 2
    h1 = np.mean([t["ret"] for t in srt[:half]]) if half else float("nan")
    h2 = np.mean([t["ret"] for t in srt[half:]])
    yrs = {y: [t["ret"] for t in tr if t["year"] == y] for y in YEARS}
    return {"n": len(tr), "mean": ret.mean(), "med": float(np.median(ret)), "win": (ret > 0).mean(), "t": tt,
            "days": len(dm), "h1": h1, "h2": h2,
            "years": {y: (len(v), float(np.mean(v)) if v else float("nan")) for y, v in yrs.items()}}


def fmt(s):
    if not s["n"]:
        return "0 trades"
    p = lambda v: f"{v * 100:+.2f}%"  # noqa: E731
    ys = ", ".join(f"{y}: {p(m)} ({k})" if k else f"{y}: —" for y, (k, m) in s["years"].items())
    return (f"{s['n']} trades / {s['days']} days, mean {p(s['mean'])}, median {p(s['med'])}, win {s['win'] * 100:.0f}%, "
            f"day-t {s['t']:.2f}, halves {p(s['h1'])}/{p(s['h2'])}; {ys}")


def main():
    t0 = time.time()
    btc_jump = {}
    groups = {"G1": [], "G2": [], "G3": []}
    last_month, loaded = None, 0
    for coin in COINS:
        x = load15(f"{coin}USDT")
        if x is None:
            vb.note("p76 · data", f"{coin}: no data", "warning"); continue
        loaded += 1; last_month = x["last"]
        z, wick, atr = signals(x)
        if coin == "BTC":
            c = x["c"]; h1 = np.zeros(len(c)); h1[4:] = c[4:] / c[:-4] - 1
            jt = x["t"][h1 >= 0.02]
            btc_jump = set(int(v) for v in jt)
        free = {g: -1 for g in groups}
        for j in np.where(np.isfinite(z) & (z <= -3.0))[0]:
            if not np.isfinite(wick[j]) or not np.isfinite(atr[j]):
                continue
            g = "G1" if z[j] > -3.5 and wick[j] >= 0.5 else ("G2" if z[j] <= -3.5 and wick[j] >= 0.5 else
                                                              ("G3" if z[j] <= -3.5 else None))
            if g is None or j <= free[g]:
                continue
            res = trade(x, j, atr)
            if res is None:
                continue
            ret, k = res; free[g] = k
            ts = int(x["t"][j])
            yr = time.gmtime(ts / 1000).tm_year
            hr = time.gmtime(ts / 1000).tm_hour
            groups[g].append({"coin": coin, "ts": ts, "day": ts // 86_400_000, "year": yr, "ret": ret,
                              "sess": "Asia 0–8" if hr < 8 else ("Europe 8–16" if hr < 16 else "US 16–24")})
    for g in groups:   # BTC flag needs BTC loaded first — BTC is COINS[0]
        for t in groups[g]:
            t["btcjump"] = any((t["ts"] - d * 900_000) in btc_jump for d in range(0, 9))
    vb.note("p76 · data", f"{loaded}/18 coins, 15m perps {M_FROM} → {last_month}; cost {COST * 100:.2f}% RT; "
                          f"{time.time() - t0:.0f}s")
    S = {g: stats(v) for g, v in groups.items()}
    names = {"G1": "G1 near zone −3.5σ<r≤−3.0σ, wick≥0.5 (#76)", "G2": "G2 current B r≤−3.5σ, wick≥0.5 (reference)",
             "G3": "G3 short tail r≤−3.5σ, wick<0.5 (#78)"}
    for g in groups:
        vb.note(f"p76 · {names[g]}", fmt(S[g]))
        sess = {}
        for t in groups[g]:
            sess.setdefault(t["sess"], []).append(t["ret"])
        vb.note(f"p76 · {g} sessions", "; ".join(f"{k}: {np.mean(v) * 100:+.2f}% ({len(v)})" for k, v in sorted(sess.items())))
        pc = {}
        for t in groups[g]:
            pc.setdefault(t["coin"], []).append(t["ret"])
        vb.note(f"p76 · {g} coins", "; ".join(f"{k} {len(v)}:{np.mean(v) * 100:+.1f}%" for k, v in pc.items()))
    bj = [t for t in groups["G3"] if t["btcjump"]]
    vb.note("p76 · G3 within 2 h after BTC +2% hour (not a gate)", fmt(stats(bj)))
    s1, s3 = S["G1"], S["G3"]
    allpos = lambda s: s["n"] and all(k and m > 0 for k, m in s["years"].values()) and s["t"] >= 2.5 and s["h1"] > 0 and s["h2"] > 0  # noqa: E731
    v76 = ("threshold right — close" if s1["n"] and s1["mean"] <= 0 else
           ("CANDIDATE for discussion" if allpos(s1) else "no change (mixed)"))
    neg_years = sum(1 for k, m in s3["years"].values() if k and m <= 0) if s3["n"] else 0
    v78 = ("wick filter right" if neg_years >= 3 else ("CANDIDATE for discussion" if allpos(s3) else "no change (mixed)"))
    vb.note("p76 · verdict", f"#76: {v76}; #78: {v78} (G3 negative years {neg_years}/4); luck z for N {N_LUCK} = "
                             f"{math.sqrt(2 * math.log(N_LUCK)):.2f}; {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
