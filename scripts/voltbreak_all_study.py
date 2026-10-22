"""Volatility breakout on EVERY Binance USDT-M perpetual, delisted included (backlog #5 + #33, 22.10.2026).

Rule fixed in advance (no tuning, from docs/research/autolab_families.md): daily move |r| > 2.5 × σ(20 previous daily returns)
→ enter at the next day's open in the direction of the move, hold 5 days, exit at the close of day 5.
Variants (all reported, N counted): long-only, long+short, short-only (the down-breakouts alone), long-only on liquid coins
(20-day average turnover ≥ $20M). Costs: 0.05% taker + 0.02% slippage per side (0.15% extra slippage when 20-day turnover < $5M),
real funding from the Binance archive (longs pay, shorts receive). A coin trades only after 30 days of history and with 20-day
turnover ≥ $1M. Portfolio: 10% of equity per trade, at most 100% gross (more than 10 open → equal split).

Honesty: placebo = the same coins on random dates (same count, same direction mix), 20 draws; luck hurdle with N = 4 + 504
(this study + the families study + the nightly lab). Data: data.binance.vision monthly 1d klines + fundingRate, 2020-01 → 2026-09.
Public data only, no keys.
"""
import datetime as dt
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

UA = {"User-Agent": "Mozilla/5.0 cryptonova-research/1.0", "Accept": "*/*"}
VISION = "https://data.binance.vision/data/futures/um"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
M0, M1 = "2020-01", "2026-09"
DAY = 86_400_000
K, LOOK, HOLD, WARM = 2.5, 20, 5, 30
FEE, SLIP, SLIP_SMALL = 0.0005, 0.0002, 0.0015
MIN_Q, SMALL_Q, LIQ_Q = 1e6, 5e6, 20e6
SLOT, N_LUCK = 0.10, 4 + 504
SURV = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
CACHE = os.environ.get("VB_DIR", "/tmp/vb")


def note(title, msg, level="notice"):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")[:4000]
    print(f"::{level} title={title}::{m}", flush=True)


def get(url, tries=4):
    err = None
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 + 5 * a)
                continue
            if 400 <= e.code < 500:
                return e.code, None
            err = e
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(1.5 * (a + 1))
    return -1, str(err)[:200]


def list_symbols():
    syms, marker = [], ""
    for _ in range(40):
        s, b = get(f"{S3}?delimiter=/&prefix=data/futures/um/monthly/klines/" + (f"&marker={marker}" if marker else ""))
        if s != 200:
            break
        t = b.decode()
        p = re.findall(r"<Prefix>data/futures/um/monthly/klines/([A-Z0-9]+)/</Prefix>", t)
        syms += p
        if "<IsTruncated>true</IsTruncated>" not in t or not p:
            break
        marker = f"data/futures/um/monthly/klines/{p[-1]}/"
    return sorted(s for s in set(syms) if s.endswith("USDT") and "_" not in s)


def s3_months(prefix):
    s, b = get(f"{S3}?prefix={prefix}", tries=3)
    if s != 200:
        return []
    return sorted(set(m.decode() for m in re.findall(rb"<Key>[^<]*?-(\d{4}-\d{2})\.zip</Key>", b)))


def zrows(b):
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        return [ln.split(",") for ln in z.read(z.namelist()[0]).decode().splitlines() if ln[:1].isdigit()]


def load(sym):
    out = {"sym": sym}
    kl = {}
    for m in [m for m in s3_months(f"data/futures/um/monthly/klines/{sym}/1d/") if M0 <= m <= M1]:
        s, b = get(f"{VISION}/monthly/klines/{sym}/1d/{sym}-1d-{m}.zip")
        if s == 200:
            for r in zrows(b):
                t = int(r[0])
                t = t // 1000 if t > 10 ** 14 else t
                kl[t // DAY] = (float(r[1]), float(r[4]), float(r[7]))
    if len(kl) < WARM + HOLD + 2:
        return None
    fd = {}
    for m in [m for m in s3_months(f"data/futures/um/monthly/fundingRate/{sym}/") if M0 <= m <= M1]:
        s, b = get(f"{VISION}/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip")
        if s == 200:
            for r in zrows(b):
                d = int(r[0]) // DAY
                fd[d] = fd.get(d, 0.0) + float(r[2])
    days = np.array(sorted(kl), dtype=np.int64)
    v = np.array([kl[d] for d in days])
    out.update(d=days, o=v[:, 0], c=v[:, 1], q=v[:, 2], f=np.array([fd.get(int(d), 0.0) for d in days]))
    return out


def signals(x):
    """→ list of (signal_day_index, direction, z, turnover20)."""
    c, q = x["c"], x["q"]
    r = np.full(len(c), np.nan)
    r[1:] = c[1:] / c[:-1] - 1
    contiguous = np.diff(x["d"], prepend=x["d"][0] - 1) == 1
    out = []
    for i in range(WARM, len(c) - 1):
        w = r[i - LOOK:i]
        if not contiguous[i - LOOK:i + 2].all() or np.isnan(w).any():
            continue
        sd = w.std(ddof=1)
        q20 = q[i - LOOK:i].mean()
        if sd <= 0 or q20 < MIN_Q:
            continue
        z = r[i] / sd
        if abs(z) > K:
            out.append((i, 1 if z > 0 else -1, z, q20))
    return out


def trade_path(x, i, side, q20):
    """Daily net returns of one trade entered at open of i+1, held HOLD days (fewer if the data ends = delisting)."""
    o, c, f, d = x["o"], x["c"], x["f"], x["d"]
    n = min(HOLD, len(c) - 1 - i)
    if n <= 0:
        return None
    cost = FEE + SLIP + (SLIP_SMALL if q20 < SMALL_Q else 0.0)
    days, rets = [], []
    for k in range(1, n + 1):
        j = i + k
        if d[j] - d[j - 1] != 1:
            break
        base = o[j] if k == 1 else c[j - 1]
        rr = side * (c[j] / base - 1) - side * f[j]
        if k == 1:
            rr -= cost
        days.append(int(d[j]))
        rets.append(rr)
    if not rets:
        return None
    rets[-1] -= cost  # exit (also when the data stops early)
    return days, rets


def portfolio(trades, d0, d1):
    """Equal 10% slots, gross ≤ 100%. → (day array, daily return array)."""
    span = np.arange(d0, d1 + 1)
    s = np.zeros(len(span))
    n = np.zeros(len(span))
    for days, rets in trades:
        for dd, rr in zip(days, rets):
            k = dd - d0
            if 0 <= k < len(span):
                s[k] += rr
                n[k] += 1
    w = np.where(n > 0, np.minimum(SLOT, 1.0 / np.maximum(n, 1)), 0.0)
    return span, s * w


def stats(span, pr):
    eq = np.cumprod(1 + pr)
    yrs = len(pr) / 365
    cagr = eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1
    sh = pr.mean() / pr.std() * math.sqrt(365) if pr.std() > 0 else 0
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    h = len(pr) // 2
    sh_h = [round(p.mean() / p.std() * math.sqrt(365), 2) if p.std() > 0 else 0 for p in (pr[:h], pr[h:])]
    years = {}
    for y in range(2020, 2027):
        a = dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000 // DAY
        b = dt.datetime(y + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000 // DAY
        m = (span >= a) & (span < b)
        if m.any():
            years[y] = round((np.prod(1 + pr[m]) - 1) * 100, 1)
    return {"sharpe": round(sh, 2), "halves": sh_h, "cagr_pct": round(cagr * 100, 1), "dd_pct": round(dd * 100, 1),
            "years": years, "hurdle": round(math.sqrt(2 * math.log(N_LUCK)) / math.sqrt(yrs), 2)}


def per_trade(trades, dates):
    tot = np.array([sum(r) for _, r in trades])
    by = {}
    for t, dd in zip(tot, dates):
        by.setdefault(dd, []).append(t)
    m = np.array([np.mean(v) for v in by.values()])
    tstat = m.mean() / (m.std(ddof=1) / math.sqrt(len(m))) if len(m) > 2 else 0
    return {"n": len(tot), "dates": len(m), "mean_pct": round(tot.mean() * 100, 2), "median_pct": round(float(np.median(tot)) * 100, 2),
            "win": round(float((tot > 0).mean()) * 100, 1), "t_by_date": round(tstat, 2)}


def main():
    t0 = time.time()
    os.makedirs(CACHE, exist_ok=True)
    syms = list_symbols()
    note("vb · universe", f"{len(syms)} USDT-M symbols in the archive (live + delisted)")
    with ThreadPoolExecutor(24) as ex:
        data = [x for x in ex.map(load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    note("vb · loaded", f"{len(data)} with ≥{WARM + HOLD + 2} daily bars, {dead} of them stopped before the end (delisted/renamed); "
         f"{time.time() - t0:.0f}s")

    sig = []  # (sym_idx, i, side, z, q20)
    for k, x in enumerate(data):
        for i, side, z, q20 in signals(x):
            sig.append((k, i, side, z, q20))
    d0 = min(int(x["d"][0]) for x in data)
    d1 = live_end

    variants = {
        "long": lambda s: s[2] > 0,
        "long+short": lambda s: True,
        "short": lambda s: s[2] < 0,
        "long liquid ≥$20M": lambda s: s[2] > 0 and s[4] >= LIQ_Q,
    }
    rows = {}
    rng = np.random.default_rng(7)
    for name, keep in variants.items():
        ss = [s for s in sig if keep(s)]
        tr, dates = [], []
        for k, i, side, z, q20 in ss:
            p = trade_path(data[k], i, side, q20)
            if p:
                tr.append(p)
                dates.append(int(data[k]["d"][i]))
        span, pr = portfolio(tr, d0, d1)
        st = stats(span, pr)
        pt = per_trade(tr, dates)
        # placebo: same coin, same side, random eligible day
        plac = []
        for rep in range(20):
            ptr = []
            for k, i, side, z, q20 in ss:
                x = data[k]
                j = int(rng.integers(WARM, len(x["c"]) - 1))
                p = trade_path(x, j, side, x["q"][max(0, j - LOOK):j].mean())
                if p:
                    ptr.append(p)
            plac.append(np.mean([sum(r) for _, r in ptr]) * 100 if ptr else 0)
        surv = [t for t, s in zip(tr, ss) if data[s[0]]["sym"] in SURV]
        rows[name] = {**st, "trades": pt, "placebo_mean_pct": round(float(np.mean(plac)), 2),
                      "placebo_p95_pct": round(float(np.percentile(plac, 95)), 2),
                      "beats_placebo": bool(pt["mean_pct"] > np.percentile(plac, 95)),
                      "survivors_mean_pct": round(float(np.mean([sum(r) for _, r in surv]) * 100), 2) if surv else None,
                      "survivors_n": len(surv)}
        r = rows[name]
        note(f"vb · {name}", f"Sharpe {r['sharpe']} (halves {r['halves'][0]}/{r['halves'][1]}), {r['cagr_pct']}%/yr, DD {r['dd_pct']}%, "
             f"hurdle {r['hurdle']}; trades {pt['n']} on {pt['dates']} days, mean {pt['mean_pct']}% (median {pt['median_pct']}%, "
             f"win {pt['win']}%, t by date {pt['t_by_date']}); placebo mean {r['placebo_mean_pct']}% p95 {r['placebo_p95_pct']}% → "
             f"{'BEATS' if r['beats_placebo'] else 'no'}; 5 survivors: {r['survivors_mean_pct']}% on {r['survivors_n']}; years {r['years']}")

    # benchmark: equal-weight long every eligible coin every day (same costs ignored = generous), and BTC hold
    s = {}
    for x in data:
        c, q = x["c"], x["q"]
        for j in range(WARM, len(c)):
            if x["d"][j] - x["d"][j - 1] == 1 and q[j - LOOK:j].mean() >= MIN_Q:
                s.setdefault(int(x["d"][j]), []).append(c[j] / c[j - 1] - 1 - x["f"][j])
    span = np.arange(d0, d1 + 1)
    pr = np.array([np.mean(s[int(d)]) if int(d) in s else 0.0 for d in span])
    b = stats(span, pr)
    btc = next(x for x in data if x["sym"] == "BTCUSDT")
    bpr = np.zeros(len(span))
    for j in range(1, len(btc["c"])):
        k = int(btc["d"][j]) - d0
        if 0 <= k < len(span):
            bpr[k] = btc["c"][j] / btc["c"][j - 1] - 1
    bb = stats(span, bpr)
    note("vb · benchmarks", f"equal-weight all coins: Sharpe {b['sharpe']} {b['cagr_pct']}%/yr DD {b['dd_pct']}% years {b['years']}; "
         f"BTC hold: Sharpe {bb['sharpe']} {bb['cagr_pct']}%/yr DD {bb['dd_pct']}%")
    summary = {"universe": len(syms), "loaded": len(data), "delisted": dead, "signals": len(sig), "variants": len(variants),
               "N_luck": N_LUCK, "rows": rows, "bench_ew": b, "bench_btc": bb, "sec": round(time.time() - t0)}
    with open(os.path.join(CACHE, "summary.json"), "w") as fh:
        json.dump(summary, fh, ensure_ascii=False, default=str)
    note("vb · summary", json.dumps(summary, ensure_ascii=False, default=str)[:3800])


if __name__ == "__main__":
    sys.exit(main())
