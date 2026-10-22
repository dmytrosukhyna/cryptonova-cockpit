"""«Поддержка проекта» — the honest check (22.10.2026, docs/research/support_score.md). Did the parts of the support score that
can be rebuilt on history predict the next 30 / 90 days against BTC?

Data (no network here; the price file comes from GitHub Actions):
  • spot1d.npz — daily closes + quote volume of EVERY Binance spot USDT pair 2021-01 → 2026-10, delisted included
    (scripts/fetch_spot1d.py, workflow research-spot-averaging);
  • data/agents.sqlite coin_cards — CoinGecko categories of today's ~138 research coins («… Portfolio» = the fund's portfolio);
  • web/coin_profiles.json — investors paragraph of the project profiles (51 coins).
Tests (a month-start snapshot each month; forward return minus BTC's over the same days):
  A «фонды»  — coins with ≥ 1 tier-1 fund (cnq/agents/meme_spot.TIER1) vs coins with none, inside today's research coins
               (SURVIVORS: only coins that are big today — the bias is named in the report), history ≥ 180 days, 30-day median
               turnover ≥ $1M;
  C «маркет-мейкер» — the same coins: a market maker among the tier-1 investors (Wintermute, GSR, DWF, Jump …) vs none;
  B «свежий листинг» — coins whose first Binance spot day is ≤ 90 days before the date vs coins older than 365 days, ALL Binance
               spot pairs incl. delisted (no survivorship), turnover ≥ $1M; a delisted coin is valued at its last close.
Per month: mean excess of each group and their difference; t = mean ÷ (sd ÷ √months) of the monthly differences. 30 days — every
month (no overlap); 90 days — every third month (no overlap). Stdlib + numpy.
Usage: python scripts/support_study.py --spot1d spot1d.npz [--db data/agents.sqlite] [--profiles web/coin_profiles.json]"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from cnq.agents.meme_spot import tier1_of  # noqa: E402

DAY = 86_400_000
STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "EUR", "USD1", "PAXG", "XUSD", "USDE", "AEUR", "EURI", "BFUSD", "RLUSD",
          "WBTC", "WBETH", "BETH", "XAUT", "USDS", "PYUSD", "EURC"}


def load(path):
    z = np.load(path, allow_pickle=True)
    return [str(s) for s in z["syms"]], z["days"].astype(np.int64), z["close"].astype(np.float64), z["qvol"].astype(np.float64)


def backers(db, prof_path):
    out = {}
    con = sqlite3.connect(db)
    prof = (json.load(open(prof_path, encoding="utf-8")).get("coins") or {}) if os.path.exists(prof_path) else {}
    for sym, data in con.execute("SELECT symbol, data FROM coin_cards"):
        cats = [c for c in (json.loads(data).get("categories") or []) if "portfolio" in c.lower()]
        out[sym.upper()] = tier1_of(cats + [(prof.get(sym.upper()) or {}).get("investors_ru") or ""])
    return out


MM = {"Wintermute", "GSR", "DWF", "Jump", "Amber", "Flow Traders", "Cumberland"}   # market makers inside meme_spot.TIER1


def fwd(close, i, h):
    """Return from day i to day i+h; a pair that stopped trading is valued at its last close (no survivorship)."""
    c0 = close[:, i]
    j = min(i + h, close.shape[1] - 1)
    seg = close[:, i:j + 1]
    last = np.where(np.isnan(seg), -np.inf, np.arange(seg.shape[1])).max(axis=1)
    c1 = np.array([seg[k, int(last[k])] if last[k] >= 0 else np.nan for k in range(seg.shape[0])])
    return c1 / c0 - 1


def run_test(name, syms, days, close, qvol, grp_a, grp_b, h, step, start, end):
    """grp_a / grp_b: fn(i) → boolean mask over coins at day index i."""
    bi = syms.index("BTC")
    months, rows_a, rows_b = [], [], []
    d = dt.date.fromisoformat(start)
    idx = {int(x): k for k, x in enumerate(days)}
    while d.isoformat() <= end:
        t = int(dt.datetime(d.year, d.month, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        i = idx.get(t)
        if i is not None and i + h < len(days):
            r = fwd(close, i, h)
            ex = r - r[bi]
            vol = np.nanmedian(qvol[:, max(0, i - 30):i + 1], axis=1)
            base = ~np.isnan(close[:, i]) & (vol >= 1e6) & ~np.isnan(ex)
            base[bi] = False
            ma, mb = base & grp_a(i), base & grp_b(i)
            if ma.sum() >= 3 and mb.sum() >= 3:
                months.append((d.isoformat()[:7], float(np.mean(ex[ma])), float(np.mean(ex[mb])), int(ma.sum()), int(mb.sum()), float(r[bi])))
                rows_a += list(ex[ma]); rows_b += list(ex[mb])
        m = d.month - 1 + step
        d = dt.date(d.year + m // 12, m % 12 + 1, 1)
    diff = np.array([a - b for _, a, b, *_ in months])
    t = float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)))) if len(diff) > 2 else float("nan")
    ya = {}
    for m, a, b, *_ in months:
        ya.setdefault(m[:4], []).append(a - b)
    return {"name": name, "h": h, "months": len(months), "n_a": len(rows_a), "n_b": len(rows_b),
            "mean_a": float(np.mean(rows_a)) * 100, "mean_b": float(np.mean(rows_b)) * 100,
            "med_a": float(np.median(rows_a)) * 100, "med_b": float(np.median(rows_b)) * 100,
            "beat_a": float(np.mean(np.array(rows_a) > 0)) * 100, "beat_b": float(np.mean(np.array(rows_b) > 0)) * 100,
            "diff": float(diff.mean()) * 100, "t": t, "pos_months": float(np.mean(diff > 0)) * 100,
            "years": {y: round(float(np.mean(v)) * 100, 1) for y, v in sorted(ya.items())}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--spot1d", required=True)
    ap.add_argument("--db", default="data/agents.sqlite")
    ap.add_argument("--profiles", default="web/coin_profiles.json")
    a = ap.parse_args(argv)
    syms, days, close, qvol = load(a.spot1d)
    keep = np.array([s not in STABLE and not s.endswith(("UP", "DOWN", "BULL", "BEAR")) for s in syms])
    first = np.array([int(np.argmax(~np.isnan(close[k]))) if (~np.isnan(close[k])).any() else 10**9 for k in range(len(syms))])
    bk = backers(a.db, a.profiles)
    known = np.array([s in bk for s in syms]) & keep
    t1 = np.array([bool(bk.get(s)) for s in syms]) & known
    mm = np.array([bool(set(bk.get(s) or []) & MM) for s in syms]) & known
    old = lambda i, n: (i - first) >= n  # noqa: E731
    out = []
    for h, step in ((30, 1), (90, 3)):
        out.append(run_test("A фонды tier-1 / без tier-1 (сегодняшние монеты анкеты)", syms, days, close, qvol,
                            lambda i: t1 & old(i, 180), lambda i: known & ~t1 & old(i, 180), h, step, "2022-01-01", "2026-09-01"))
        out.append(run_test("C маркет-мейкер среди инвесторов / нет", syms, days, close, qvol,
                            lambda i: mm & old(i, 180), lambda i: known & ~mm & old(i, 180), h, step, "2022-01-01", "2026-09-01"))
        out.append(run_test("B листинг на Binance ≤ 90 дней / старше года (все пары, со снятыми)", syms, days, close, qvol,
                            lambda i: keep & (i - first >= 1) & (i - first <= 90) & (first > 0), lambda i: keep & old(i, 365),
                            h, step, "2021-06-01", "2026-09-01"))
    print(f"coins: {len(syms)} pairs, research coins with Binance history {int(known.sum())}, of them with tier-1 {int(t1.sum())}, with a market maker {int(mm.sum())}")
    for r in out:
        print(json.dumps(r, ensure_ascii=False))
    return out


if __name__ == "__main__":
    main()
