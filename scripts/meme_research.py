#!/usr/bin/env python3
"""Meme research — Hyperliquid meme perpetuals (docs/research/memes.md). Run by .github/workflows/research-memes.yml.

  python scripts/meme_research.py --md results.md --json summary.json     download + test + write tables
  python scripts/meme_research.py --probe probe.md                         check the live-scanner data sources
  python scripts/meme_research.py --synthetic --md r.md --json s.json      offline smoke test on random-walk data

Data (all public, no keys): Hyperliquid info API — `meta` (incl. delisted perps), `metaAndAssetCtxs` (now),
`candleSnapshot` 1d / 4h / 1h (HL serves only the most recent ~5000 candles per interval: 4h ≈ 2.3 years, 1h ≈ 7 months),
`fundingHistory` (last 90 days → average funding a long pays per day). OKX open-interest history (proxy for HL OI, in coins)
where OKX lists the same meme and serves history.

Rules (long only, costs 0.15% round trip + each coin's average funding per day held, entry at the NEXT bar's open):
  A  breakout: close above the previous 24 h high and bar volume ≥ K × median bar volume of the previous 7 days
  B  relative-strength rotation: every day hold the top-N memes by L-day return (daily rebalance, cost on turnover)
  C  dip-buy: close ≥ D% below the 24 h high (fresh cross); variant with OKX open interest −10% over 24 h
  D  trend filter: price above its 20-day mean (standalone basket and as a filter on A / C)
Reference exits (= the paper account): hard stop −25%, half off at +50%, time exit; grids are reported in full.
Baselines: random entries in the same coin within ±15 days (same exits), equal-weight basket of all memes, random-N rotation.
Honesty: small sample (memes, ~2 years at most), survivorship (HL lists a meme after it is already popular; delisted perps
are included only where HL still serves their candles), 4h/1h bars, stops filled at the stop or the next open (gaps)."""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
import warnings
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as swv

warnings.filterwarnings("ignore", category=RuntimeWarning)          # all-NaN medians on not-yet-listed coins are expected
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cnq.agents.memes import MEME_COINS  # noqa: E402  (single source of the meme list)

HL = "https://api.hyperliquid.xyz/info"
UA = "Mozilla/5.0 cryptonova-research/1.0"
DAY = 86_400_000
COST = 0.0015                     # round trip: HL taker 0.045% × 2 + slippage
BASE_FUND_DAY = 0.0003            # HL baseline funding 0.00125%/h × 24 — used when a coin has no funding history
STOP, TP, TP_FRAC = -0.25, 0.50, 0.5
LIQ_DAY = 1_000_000               # all rules: median daily USD volume (7 days) ≥ $1M, else the coin is skipped at that time
START = 10_000.0


# ── network (weight-aware throttle: HL allows 1200 weight / min / IP) ─────────────────────────────────────────────────

_W: list[tuple[float, float]] = []


def _throttle(w: float, cap: float = 1000.0) -> None:
    while True:
        now = time.time()
        while _W and now - _W[0][0] > 60:
            _W.pop(0)
        if sum(x for _, x in _W) + w <= cap:
            _W.append((now, w)); return
        time.sleep(1.0)


def hl(body: dict, weight: float = 20, timeout: int = 30, tries: int = 6):
    _throttle(weight)
    for k in range(tries):
        try:
            req = urllib.request.Request(HL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(15 * (k + 1)); continue
            if e.code >= 500:
                time.sleep(5); continue
            return None
        except Exception:  # noqa: BLE001
            time.sleep(3)
    return None


def get(url: str, timeout: int = 20, headers: dict | None = None):
    try:
        h = {"User-Agent": UA, "Accept": "application/json"}
        h.update(headers or {})
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return {"__error__": f"{type(e).__name__}: {e}"[:200]}


def post(url: str, body, timeout: int = 20):
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return {"__error__": f"{type(e).__name__}: {e}"[:200]}


def candles(coin: str, interval: str, start: int, end: int) -> dict | None:
    rows, s = {}, start
    step = {"1d": DAY, "4h": DAY // 6, "1h": DAY // 24}[interval]
    for _ in range(20):
        r = hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": s, "endTime": end}},
               weight=20 + min(5000, (end - s) // step) / 60)
        if not r:
            break
        for x in r:
            rows[int(x["t"])] = (float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"]), float(x["v"]), int(x["T"]))
        last = max(int(x["t"]) for x in r)
        if last + 2 * step >= end or last < s:          # reached now (or no progress)
            break
        s = last + 1
    if not rows:
        return None
    now = int(time.time() * 1000)
    ts = sorted(t for t in rows if rows[t][5] < now)          # closed candles only
    if len(ts) < 2:
        return None
    a = np.array([rows[t][:5] for t in ts], dtype=float)
    return {"t": np.array(ts, dtype=np.int64), "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "v": a[:, 4] * a[:, 3]}  # v in USD


def funding_mean(coin: str, days: int = 90) -> tuple[float | None, int]:
    s, rates = int(time.time() * 1000) - days * DAY, []
    for _ in range(12):
        r = hl({"type": "fundingHistory", "coin": coin, "startTime": s}, weight=20 + 500 / 20)
        if not r:
            break
        rates += [float(x["fundingRate"]) for x in r]
        if len(r) < 500:
            break
        s = int(r[-1]["time"]) + 1
    return (float(np.mean(rates)) * 24, len(rates)) if rates else (None, 0)


def okx_base(coin: str) -> str:
    return coin[1:] if coin.startswith("k") and coin[1:].isupper() else coin


def okx_oi(coin: str, inst: set[str]) -> dict | None:
    """Hourly open interest in coins from OKX (proxy for HL). None when OKX has no such swap or no history."""
    iid = f"{okx_base(coin)}-USDT-SWAP"
    if iid not in inst:
        return None
    rows, end = {}, None
    for _ in range(80):
        url = f"https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?instId={iid}&period=1H&limit=100" + (f"&end={end}" if end else "")
        j = get(url)
        d = (j or {}).get("data") if isinstance(j, dict) else None
        if not d:
            break
        for x in d:
            try:
                rows[int(x[0])] = float(x[2])          # oiCcy — open interest in coins (USD OI falls mechanically with price)
            except (ValueError, IndexError, TypeError):
                pass
        end = min(int(x[0]) for x in d)
        time.sleep(0.25)
        if len(d) < 100:
            break
    if len(rows) < 48:
        return None
    ts = sorted(rows)
    return {"t": np.array(ts, dtype=np.int64), "oi": np.array([rows[t] for t in ts])}


# ── data set ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def download(log=print) -> dict:
    meta = hl({"type": "meta"}) or {}
    uni = meta.get("universe", [])
    names = {u["name"]: bool(u.get("isDelisted")) for u in uni}
    ctx = hl({"type": "metaAndAssetCtxs"}) or [{}, []]
    now_ctx = {}
    for u, c in zip((ctx[0] or {}).get("universe", []), ctx[1] or []):
        try:
            px = float(c.get("markPx") or 0)
            now_ctx[u["name"]] = {"vol24": float(c.get("dayNtlVlm") or 0), "oi_usd": float(c.get("openInterest") or 0) * px,
                                  "funding_h": float(c.get("funding") or 0), "px": px}
        except (TypeError, ValueError):
            pass
    memes = [m for m in MEME_COINS if m in names]
    log(f"HL perps: {len(names)} (delisted {sum(names.values())}); memes in list present: {len(memes)}")
    now = int(time.time() * 1000)
    data = {"meta": {"n_perps": len(names), "memes": memes, "delisted": [m for m in memes if names[m]], "now": now_ctx},
            "d1": {}, "h4": {}, "h1": {}, "fund": {}, "oi": {}}
    for coin in memes + ["BTC"]:
        for key, iv, back in (("d1", "1d", 1200), ("h4", "4h", 900), ("h1", "1h", 220)):
            c = candles(coin, iv, now - back * DAY, now)
            if c is not None:
                data[key][coin] = c
        if coin != "BTC":
            data["fund"][coin] = funding_mean(coin)
        log(f"{coin}: d1={len(data['d1'].get(coin, {}).get('t', []))} h4={len(data['h4'].get(coin, {}).get('t', []))} "
            f"h1={len(data['h1'].get(coin, {}).get('t', []))} fund={data['fund'].get(coin)}")
    ins = get("https://www.okx.com/api/v5/public/instruments?instType=SWAP")
    inst = {x.get("instId") for x in (ins.get("data") or [])} if isinstance(ins, dict) else set()
    for coin in memes:
        o = okx_oi(coin, inst)
        if o is not None:
            data["oi"][coin] = o
    log(f"OKX OI history: {len(data['oi'])} coins; " + ", ".join(f"{c} {datetime.fromtimestamp(o['t'][0] / 1000, timezone.utc):%Y-%m-%d}" for c, o in data["oi"].items()))
    return data


def synthetic(seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    now = int(time.time() * 1000) // DAY * DAY

    def walk(n, step, vol, drift=0.0):
        r = rng.standard_t(3, n) * vol + drift
        c = 1.0 * np.exp(np.cumsum(r))
        o = np.r_[c[0], c[:-1]]
        h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2, n)))
        lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2, n)))
        v = np.exp(rng.normal(15, 1, n))
        t = now - (n - np.arange(n)) * step
        return {"t": t.astype(np.int64), "o": o, "h": h, "l": lo, "c": c, "v": v}
    memes = ["kPEPE", "WIF", "POPCAT", "FARTCOIN", "kBONK", "MOODENG", "PNUT", "GOAT"]
    data = {"meta": {"n_perps": 0, "memes": memes, "delisted": ["GOAT"], "now": {m: {"vol24": 5e7, "oi_usd": 2e7, "funding_h": 1e-5, "px": 1} for m in memes}},
            "d1": {}, "h4": {}, "h1": {}, "fund": {}, "oi": {}}
    for k, m in enumerate(memes + ["BTC"]):
        vol = 0.02 if m == "BTC" else 0.06
        n0 = 700 - 60 * k if m != "BTC" else 900
        data["d1"][m] = walk(n0, DAY, vol)
        data["h4"][m] = walk(min(5000, n0 * 6), DAY // 6, vol / 2.4)
        data["h1"][m] = walk(5000, DAY // 24, vol / 4.9)
        if m != "BTC":
            data["fund"][m] = (0.0003, 2000)
    o = data["h1"]["WIF"]
    data["oi"]["WIF"] = {"t": o["t"].copy(), "oi": np.exp(np.cumsum(rng.normal(0, 0.02, len(o["t"])))) * 1e7}
    return data


# ── helpers ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def roll_prev(x, w, fn):
    """out[i] = fn(x[i-w:i]) (previous w bars, current excluded)."""
    out = np.full(len(x), np.nan)
    if len(x) > w:
        out[w:] = fn(swv(x[:-1], w), axis=1)
    return out


def roll_incl(x, w, fn):
    """out[i] = fn(x[i-w+1:i+1])."""
    out = np.full(len(x), np.nan)
    if len(x) >= w:
        out[w - 1:] = fn(swv(x, w), axis=1)
    return out


def month(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m")


def day(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")


def pct(x, nd=2, sign=True):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    return f"{x * 100:+.{nd}f}%" if sign else f"{x * 100:.{nd}f}%"


def max_dd(eq: np.ndarray) -> float:
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float(np.min(eq / peak - 1))


def fund_day(data, coin) -> float:
    f = (data["fund"].get(coin) or (None, 0))[0]
    return f if f is not None else BASE_FUND_DAY


# ── signals (bars per day W: 4h → 6, 1h → 24) ──────────────────────────────────────────────────────────────────────────

def base_ok(b, W):
    """Liquidity filter + enough warm-up (20 days for the trend mean)."""
    med = roll_prev(b["v"], 7 * W, np.median)
    ok = (med * W >= LIQ_DAY)
    ok[: 20 * W] = False
    return ok, med


def sig_breakout(b, W, K):
    ok, med = base_ok(b, W)
    hh = roll_prev(b["h"], W, np.max)
    ratio = b["v"] / np.where(med > 0, med, np.nan)
    s = ok & (b["c"] > hh) & (ratio >= K)
    return np.nan_to_num(s, nan=0).astype(bool), np.nan_to_num(ratio)


def sig_dip(b, W, D, oi=None, oi_drop=-0.10):
    ok, _ = base_ok(b, W)
    hh = roll_incl(b["h"], W, np.max)
    dd = b["c"] / hh - 1
    s = ok & (dd <= -D) & (np.r_[0, dd[:-1]] > -D)
    s = np.nan_to_num(s, nan=0).astype(bool)
    if oi is not None:
        oc = oi_change(b["t"], oi, W)
        s = s & (np.nan_to_num(oc, nan=1.0) <= oi_drop)
    return s, -np.nan_to_num(dd)


def oi_change(t, oi, W):
    """OI change over the last 24 h at each bar close (bar open time + bar length), coins units; nan when not covered."""
    step = int(np.median(np.diff(t))) if len(t) > 1 else DAY // W
    tc = t + step
    def at(tt):
        j = np.searchsorted(oi["t"], tt, side="right") - 1
        v = np.where(j >= 0, oi["oi"][np.clip(j, 0, None)], np.nan)
        stale = np.where(j >= 0, tt - oi["t"][np.clip(j, 0, None)] > 2 * 3_600_000, True)
        return np.where(stale, np.nan, v)
    return at(tc) / at(tc - DAY) - 1


def trend_up(b, W):
    sma = roll_incl(b["c"], 20 * W, np.mean)
    return np.nan_to_num(b["c"] > sma, nan=0).astype(bool)


# ── event-rule simulation ──────────────────────────────────────────────────────────────────────────────────────────────

def exit_path(b, e, hold, stop, tp):
    """Return (gross return per 1 notional, exit index, kind) for a long entered at open of bar e."""
    px = b["o"][e]; n = len(b["c"]); last = min(n - 1, e + hold - 1)
    rem, real, tp_done = 1.0, 0.0, False
    for j in range(e, last + 1):
        if stop is not None and b["l"][j] <= px * (1 + stop):
            fill = min(b["o"][j], px * (1 + stop))
            return real + rem * (fill / px - 1), j, "стоп"
        if tp is not None and not tp_done and b["h"][j] >= px * (1 + tp):
            fill = max(b["o"][j], px * (1 + tp))
            real += TP_FRAC * (fill / px - 1); rem -= TP_FRAC; tp_done = True
    return real + rem * (b["c"][last] / px - 1), last, ("время+тейк" if tp_done else "время")


def trades_for(b, sig, W, hold_days, stop, tp, fday, coin):
    out, i, n, hold = [], 0, len(b["c"]), int(round(hold_days * W))
    idx = np.flatnonzero(sig)
    k = 0
    while k < len(idx):
        i = idx[k]
        e = i + 1
        if e >= n:
            break
        if b["o"][e] <= 0:
            k += 1; continue
        g, j, kind = exit_path(b, e, hold, stop, tp)
        days = (j - e + 1) / W
        out.append({"coin": coin, "e": int(e), "j": int(j), "t": int(b["t"][e]), "ret": g - COST - fday * days, "gross": g, "kind": kind, "days": days})
        while k < len(idx) and idx[k] <= j:
            k += 1
    return out


def random_baseline(data, key, trades, W, hold_days, stop, tp, reps=50, seed=1):
    """Avg/trade of random entries: same coin, within ±15 days of each real entry, same exits."""
    if not trades:
        return None, None
    rng = random.Random(seed)
    hold, avgs = int(round(hold_days * W)), []
    for _ in range(reps):
        rs = []
        for tr in trades:
            b = data[key][tr["coin"]]; n = len(b["c"])
            lo, hi = max(20 * W + 1, tr["e"] - 15 * W), min(n - 2, tr["e"] + 15 * W)
            if hi <= lo:
                continue
            e = rng.randint(lo, hi)
            g, j, _ = exit_path(b, e, hold, stop, tp)
            rs.append(g - COST - fund_day(data, tr["coin"]) * (j - e + 1) / W)
        if rs:
            avgs.append(float(np.mean(rs)))
    if not avgs:
        return None, None
    s = float(np.mean([t["ret"] for t in trades]))
    return float(np.mean(avgs)), float(np.mean([a >= s for a in avgs]))


def stats(trades) -> dict:
    if not trades:
        return {"n": 0}
    r = np.array([t["ret"] for t in trades])
    by_m = defaultdict(list)
    for t in trades:
        by_m[month(t["t"])].append(t["ret"])
    ms = {m: float(np.sum(v)) for m, v in by_m.items()}
    return {"n": len(r), "avg": float(r.mean()), "med": float(np.median(r)), "win": float((r > 0).mean()),
            "months": len(ms), "months_pos": float(np.mean([v > 0 for v in ms.values()])), "by_month": ms,
            "best": float(r.max()), "worst": float(r.min())}


def halves(trades, t_mid):
    a = [t["ret"] for t in trades if t["t"] < t_mid]; b = [t["ret"] for t in trades if t["t"] >= t_mid]
    return (float(np.mean(a)) if a else None, len(a)), (float(np.mean(b)) if b else None, len(b))


def run_rule(data, key, W, sigs, hold_days, stop, tp):
    out = []
    for coin, s in sigs.items():
        out += trades_for(data[key][coin], s, W, hold_days, stop, tp, fund_day(data, coin), coin)
    return sorted(out, key=lambda t: t["t"])


# ── portfolio simulation of an event rule (10% per position, max 6, mark-to-market every bar) ─────────────────────────

def portfolio(data, key, W, sigs, strength, hold_days, stop, tp, max_pos=6, size=0.10):
    coins = list(sigs)
    tl = np.unique(np.concatenate([data[key][c]["t"] for c in coins])) if coins else np.array([], dtype=np.int64)
    if len(tl) == 0:
        return None
    g2c = {}
    for c in coins:
        t = data[key][c]["t"]; m = np.full(len(tl), -1, dtype=np.int64)
        pos_ = np.searchsorted(tl, t); m[pos_] = np.arange(len(t)); g2c[c] = m
    hold = int(round(hold_days * W)); half = COST / 2
    cash, pos, pending, eq, trades, last_px = START, {}, [], np.zeros(len(tl)), [], {}
    first_sig = None
    for gi in range(len(tl)):
        if pending:
            equity = cash + sum(p["qty"] * last_px[c] for c, p in pos.items())
            for c in sorted(pending, key=lambda c: -pending_str.get(c, 0)):
                if len(pos) >= max_pos:
                    break
                i = g2c[c][gi]
                if c in pos or i < 0 or data[key][c]["o"][i] <= 0:
                    continue
                b = data[key][c]; px = b["o"][i]; notional = size * equity
                cash -= notional * (1 + half)
                pos[c] = {"px": px, "qty": notional / px, "qty0": notional / px, "i0": i, "tp": False, "cash_in": notional * (1 + half), "cash_out": 0.0, "t": int(b["t"][i])}
                last_px[c] = px
            pending = []
        for c in list(pos):
            i = g2c[c][gi]
            if i < 0:
                continue
            b, p = data[key][c], pos[c]; px = p["px"]
            fee = p["qty"] * b["o"][i] * fund_day(data, c) / W
            cash -= fee; p["cash_in"] += fee
            done = False
            if stop is not None and b["l"][i] <= px * (1 + stop):
                fill = min(b["o"][i], px * (1 + stop)); v = p["qty"] * fill * (1 - half)
                cash += v; p["cash_out"] += v; done = "стоп"
            else:
                if tp is not None and not p["tp"] and b["h"][i] >= px * (1 + tp):
                    fill = max(b["o"][i], px * (1 + tp)); q = p["qty0"] * TP_FRAC; v = q * fill * (1 - half)
                    cash += v; p["cash_out"] += v; p["qty"] -= q; p["tp"] = True
                if i - p["i0"] + 1 >= hold or i == len(b["c"]) - 1:
                    v = p["qty"] * b["c"][i] * (1 - half); cash += v; p["cash_out"] += v; done = "время"
            if done:
                trades.append({"coin": c, "t": p["t"], "ret": p["cash_out"] / p["cash_in"] - 1, "kind": done})
                del pos[c]
            else:
                last_px[c] = b["c"][i]
        pending_str = {}
        for c in coins:
            i = g2c[c][gi]
            if i >= 0 and sigs[c][i] and c not in pos:
                pending.append(c); pending_str[c] = float(strength[c][i])
                first_sig = first_sig or int(tl[gi])
        eq[gi] = cash + sum(p["qty"] * last_px[c] for c, p in pos.items())
    # measure from the first bar on which the rule could trade (first signal), not from the first candle
    k0 = int(np.searchsorted(tl, first_sig)) if first_sig else 0
    eqs, tls = eq[k0:], tl[k0:]
    days = max(1.0, (tls[-1] - tls[0]) / DAY) if len(tls) > 1 else 1.0
    me = {}
    for t, v in zip(tls, eqs):
        me[month(int(t))] = v
    mk = sorted(me); mret = {}
    prev = eqs[0] if len(eqs) else START
    for m in mk:
        mret[m] = me[m] / prev - 1; prev = me[m]
    return {"final": float(eqs[-1]) if len(eqs) else START, "dd": max_dd(eqs), "trades": len(trades), "per_day": len(trades) / days,
            "days": days, "from": day(int(tls[0])) if len(tls) else None, "months_pos": float(np.mean([v > 0 for v in mret.values()])) if mret else None,
            "mret": mret, "avg": float(np.mean([t["ret"] for t in trades])) if trades else None,
            "win": float(np.mean([t["ret"] > 0 for t in trades])) if trades else None}


# ── daily rotation / baskets ───────────────────────────────────────────────────────────────────────────────────────────

def daily_matrix(data, coins):
    tl = np.unique(np.concatenate([data["d1"][c]["t"] for c in coins]))
    C = np.full((len(tl), len(coins)), np.nan); V = np.full_like(C, np.nan)
    for k, c in enumerate(coins):
        b = data["d1"][c]; ix = np.searchsorted(tl, b["t"]); C[ix, k] = b["c"]; V[ix, k] = b["v"]
    return tl, C, V


def rotation(data, coins, L=5, N=3, mode="top", trend=False, seed=None, tl=None, C=None, V=None):
    if tl is None:
        tl, C, V = daily_matrix(data, coins)
    rng = random.Random(seed)
    nT, nK = C.shape
    R = np.full_like(C, np.nan); R[1:] = C[1:] / C[:-1] - 1
    fd = np.array([fund_day(data, c) for c in coins])
    w = np.zeros(nK); eq = [START]; rets = []; entries = 0; ts = []
    half = COST / 2
    for d in range(21, nT - 1):
        hist = C[d - 20:d + 1]
        elig = ~np.isnan(hist).any(axis=0)
        vmed = np.nanmedian(V[d - 6:d + 1], axis=0)
        elig &= np.nan_to_num(vmed) >= LIQ_DAY
        if trend:
            elig &= C[d] > np.nanmean(hist, axis=0)
        idx = np.flatnonzero(elig)
        if mode in ("top", "bottom") and d - L >= 0:
            sc = C[d, idx] / C[d - L, idx] - 1
            ok = ~np.isnan(sc); idx, sc = idx[ok], sc[ok]
            order = np.argsort(-sc if mode == "top" else sc)
            pick = idx[order[:N]]
            nw = np.zeros(nK); nw[pick] = 1.0 / N
        elif mode == "random":
            pick = rng.sample(list(idx), min(N, len(idx))) if len(idx) else []
            nw = np.zeros(nK); nw[list(pick)] = 1.0 / N
        else:  # "all" (equal weight)
            nw = np.zeros(nK)
            if len(idx):
                nw[idx] = 1.0 / len(idx)
        entries += int(((nw > 0) & (w <= 0)).sum())
        turn = np.abs(nw - w).sum()
        r_next = np.nan_to_num(R[d + 1])          # delisted next day → treated as exit at the last close (0)
        pr = float((nw * r_next).sum() - turn * half - (nw * fd).sum())
        rets.append(pr); eq.append(eq[-1] * (1 + pr)); ts.append(int(tl[d + 1]))
        gross = nw * (1 + r_next); tot = 1 + float((nw * r_next).sum())
        w = gross / tot if tot > 0 else np.zeros(nK)
    eq = np.array(eq[1:]); rets = np.array(rets)
    if len(eq) == 0:
        return None
    me = {}
    for t, v in zip(ts, eq):
        me[month(t)] = v
    prev, mret = START, {}
    for m in sorted(me):
        mret[m] = me[m] / prev - 1; prev = me[m]
    return {"final": float(eq[-1]), "dd": max_dd(np.r_[START, eq]), "days": len(eq), "from": day(ts[0]), "per_day": entries / len(eq),
            "months_pos": float(np.mean([v > 0 for v in mret.values()])), "mret": mret, "rets": rets, "ts": np.array(ts)}


def half_ret(res):
    r = res["rets"]; h = len(r) // 2
    return float(np.prod(1 + r[:h]) - 1), float(np.prod(1 + r[h:]) - 1)


# ── regimes ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def btc_regime(data):
    b = data["d1"].get("BTC")
    if b is None:
        return None
    sma = roll_incl(b["c"], 50, np.mean)
    t_close = b["t"] + DAY

    def f(t):
        j = np.searchsorted(t_close, t, side="right") - 1
        return None if j < 49 else bool(b["c"][j] > sma[j])
    return f


def meme_breadth(data, coins):
    tl, C, _ = daily_matrix(data, coins)
    S = np.full_like(C, np.nan)
    for k in range(C.shape[1]):
        S[:, k] = roll_incl(np.nan_to_num(C[:, k], nan=np.nan), 20, np.mean)
    above = (C > S) & ~np.isnan(S)
    have = ~np.isnan(S)
    br = np.where(have.sum(1) >= 3, above.sum(1) / np.maximum(1, have.sum(1)), np.nan)
    t_close = tl + DAY

    def f(t):
        j = np.searchsorted(t_close, t, side="right") - 1
        return None if j < 0 or np.isnan(br[j]) else bool(br[j] >= 0.5)
    return f, tl, br


def split_by(trades, f):
    a, b = [], []
    for t in trades:
        v = f(t["t"])
        if v is True: a.append(t["ret"])
        elif v is False: b.append(t["ret"])
    return (float(np.mean(a)) if a else None, len(a)), (float(np.mean(b)) if b else None, len(b))


# ── the study ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def study(data, log=print) -> tuple[str, dict]:
    memes = [m for m in data["meta"]["memes"] if m in data["h4"]]
    md, S = [], {"memes": memes, "delisted": data["meta"]["delisted"]}
    now_ctx = data["meta"]["now"]
    md.append("## Монеты и покрытие данных\n")
    md.append("| монета | свечи 1д с | 4ч с | 1ч с | объём 24ч сейчас | ОИ сейчас | фандинг/день (90д) | делистинг |")
    md.append("|---|---|---|---|---|---|---|---|")
    cov = []
    for m in data["meta"]["memes"]:
        d1, h4, h1 = data["d1"].get(m), data["h4"].get(m), data["h1"].get(m)
        nc = now_ctx.get(m, {})
        f = data["fund"].get(m) or (None, 0)
        cov.append(m)
        md.append(f"| {m} | {day(int(d1['t'][0])) if d1 else '—'} | {day(int(h4['t'][0])) if h4 else '—'} | {day(int(h1['t'][0])) if h1 else '—'} | "
                  f"${nc.get('vol24', 0) / 1e6:.1f}M | ${nc.get('oi_usd', 0) / 1e6:.1f}M | {pct(f[0], 3) if f[0] is not None else '—'} | {'да' if m in data['meta']['delisted'] else ''} |")
    fds = [v[0] for v in data["fund"].values() if v and v[0] is not None]
    S["funding_day_median"] = float(np.median(fds)) if fds else None
    oi_cov = {c: (day(int(o["t"][0])), day(int(o["t"][-1])), len(o["t"])) for c, o in data["oi"].items()}
    S["oi_coverage"] = oi_cov
    md.append(f"\nМедианный фандинг для лонга: {pct(S['funding_day_median'], 3)} в день. OKX ОИ-история: "
              + (", ".join(f"{c} {a}→{b}" for c, (a, b, _) in oi_cov.items()) or "нет") + "\n")

    breg = btc_regime(data)
    mb, _, _ = meme_breadth(data, [m for m in memes if m in data["d1"]]) if memes else (None, None, None)
    h4_all = np.concatenate([data["h4"][c]["t"] for c in memes]) if memes else np.array([0])
    t_mid = int((h4_all.min() + h4_all.max()) / 2)
    S["h4_from"], S["h4_to"], S["t_mid"] = day(int(h4_all.min())), day(int(h4_all.max())), day(t_mid)

    def describe(name, key, W, sigs, strength, hold, stop, tp, with_rand=True, with_port=True, extra_f=None):
        tr = run_rule(data, key, W, sigs, hold, stop, tp)
        st = stats(tr)
        out = {"name": name, **{k: v for k, v in st.items() if k != "by_month"}}
        if not tr:
            return out, tr
        if with_rand:
            ra, p = random_baseline(data, key, tr, W, hold, stop, tp)
            out["rand_avg"], out["p"] = ra, p
        (a1, n1), (a2, n2) = halves(tr, t_mid if key == "h4" else int(np.median([t["t"] for t in tr])))
        out["half1"], out["half2"], out["n1"], out["n2"] = a1, a2, n1, n2
        if with_port:
            pf = portfolio(data, key, W, sigs, strength, hold, stop, tp)
            out["port"] = {k: v for k, v in pf.items() if k != "mret"} if pf else None
            out["port_mret"] = pf["mret"] if pf else None
        if breg:
            (x1, k1), (x2, k2) = split_by(tr, breg)
            out["btc_up"], out["btc_up_n"], out["btc_dn"], out["btc_dn_n"] = x1, k1, x2, k2
        if mb:
            (x1, k1), (x2, k2) = split_by(tr, mb)
            out["season"], out["season_n"], out["noseason"], out["noseason_n"] = x1, k1, x2, k2
        out["by_month"] = st["by_month"]
        out["exits"] = {k: sum(1 for t in tr if t["kind"] == k) for k in ("стоп", "время", "время+тейк")}
        return out, tr

    def row(o):
        p = o.get("port") or {}
        return (f"| {o['name']} | {o['n']} | {p.get('per_day', 0):.2f} | {pct(o.get('avg'))} | {pct(o.get('rand_avg'))} | "
                f"{'—' if o.get('p') is None else f'{o['p']:.2f}'} | {pct(o.get('win'), 0, False)} | {pct(p.get('dd'), 0)} | "
                f"${p.get('final', START):,.0f} | {pct(p.get('months_pos'), 0, False)} | {pct(o.get('half1'))} / {pct(o.get('half2'))} |")
    head = ("| правило | сделок | в день | средняя | случайно | p | в плюс | макс. просадка | итог $10k | месяцев в плюсе | 1-я / 2-я половина |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|")

    # ── A breakout (4h) ────────────────────────────────────────────────────────────────────────────────────────────────
    W = 6
    S["rules"] = {}
    md.append(f"## A. Пробой 24-часового максимума на всплеске объёма (свечи 4ч, {S['h4_from']} → {S['h4_to']})\n")
    md.append("Сетка (средняя сделка после издержек и фандинга; выход: стоп/тейк/время). Эталон заранее: K=3, держим 3 дня, стоп −25%, половина на +50%.\n")
    md.append("| K × медиана объёма | держим | без стопа | стоп −15% | стоп −25% | стоп −25% + половина на +50% |")
    md.append("|---|---|---|---|---|---|")
    grid_pos = grid_n = 0
    sigsA = {}
    for K in (2, 3, 5):
        sg = {c: sig_breakout(data["h4"][c], W, K) for c in memes}
        sigsA[K] = sg
        for hold in (1, 3, 7):
            cells = []
            for stop, tp in ((None, None), (-0.15, None), (STOP, None), (STOP, TP)):
                tr = run_rule(data, "h4", W, {c: s for c, (s, _) in sg.items()}, hold, stop, tp)
                a = float(np.mean([t["ret"] for t in tr])) if tr else None
                grid_n += 1; grid_pos += bool(a is not None and a > 0)
                cells.append(f"{pct(a)} ({len(tr)})")
            md.append(f"| {K}× | {hold} д | " + " | ".join(cells) + " |")
    S["rules"]["A_grid_pos"] = f"{grid_pos}/{grid_n}"
    md.append(f"\nЯчеек в плюсе: **{grid_pos} из {grid_n}**.\n")
    resA = {}
    variants = [("A эталон K=3, 3д", 3, 3, None), ("A K=3, 3д + выше 20-дн. средней", 3, 3, "trend"), ("A K=5, 3д", 5, 3, None), ("A K=3, 7д", 3, 7, None)]
    md.append(head)
    for name, K, hold, flt in variants:
        sg = sigsA[K]
        sigs = {c: (s & trend_up(data["h4"][c], W)) if flt == "trend" else s for c, (s, _) in sg.items()}
        strg = {c: r for c, (_, r) in sg.items()}
        o, _ = describe(name, "h4", W, sigs, strg, hold, STOP, TP)
        resA[name] = o; md.append(row(o))
    S["rules"]["A"] = resA

    # ── C dip-buy (4h) ─────────────────────────────────────────────────────────────────────────────────────────────────
    md.append(f"\n## C. Откуп обвала: цена на D% ниже максимума за 24 ч (свечи 4ч)\n")
    md.append("| D | держим | без стопа | стоп −25% | стоп −25% + половина на +50% |")
    md.append("|---|---|---|---|---|")
    gp = gn = 0
    sigsC = {}
    for D in (0.20, 0.25, 0.30):
        sg = {c: sig_dip(data["h4"][c], W, D) for c in memes}
        sigsC[D] = sg
        for hold in (1, 3, 7):
            cells = []
            for stop, tp in ((None, None), (STOP, None), (STOP, TP)):
                tr = run_rule(data, "h4", W, {c: s for c, (s, _) in sg.items()}, hold, stop, tp)
                a = float(np.mean([t["ret"] for t in tr])) if tr else None
                gn += 1; gp += bool(a is not None and a > 0)
                cells.append(f"{pct(a)} ({len(tr)})")
            md.append(f"| −{int(D * 100)}% | {hold} д | " + " | ".join(cells) + " |")
    S["rules"]["C_grid_pos"] = f"{gp}/{gn}"
    md.append(f"\nЯчеек в плюсе: **{gp} из {gn}**.\n")
    md.append(head)
    resC = {}
    for name, D, hold, flt in (("C эталон −25%, 3д", 0.25, 3, None), ("C −25%, 3д + выше 20-дн. средней", 0.25, 3, "trend"), ("C −20%, 3д", 0.20, 3, None), ("C −30%, 3д", 0.30, 3, None)):
        sg = sigsC[D]
        sigs = {c: (s & trend_up(data["h4"][c], W)) if flt == "trend" else s for c, (s, _) in sg.items()}
        o, _ = describe(name, "h4", W, sigs, {c: r for c, (_, r) in sg.items()}, hold, STOP, TP)
        resC[name] = o; md.append(row(o))
    S["rules"]["C"] = resC
    # OI variant (1h bars where OKX OI exists; compared on the same coins / period)
    oi_coins = [c for c in memes if c in data["oi"] and c in data["h1"]]
    if oi_coins:
        md.append(f"\n**С открытым интересом (OKX, в монетах), свечи 1ч, только {len(oi_coins)} монет с историей ОИ:**\n")
        md.append("| вариант | сделок | средняя | в плюс | случайно | p |")
        md.append("|---|---|---|---|---|---|")
        resOI = {}
        for name, D, oi_on in (("обвал −20%, без ОИ", 0.20, False), ("обвал −20% и ОИ −10% за 24ч", 0.20, True),
                               ("обвал −25%, без ОИ", 0.25, False), ("обвал −25% и ОИ −10% за 24ч", 0.25, True)):
            sigs = {}
            for c in oi_coins:
                b = data["h1"][c]
                s, _ = sig_dip(b, 24, D, oi=data["oi"][c] if oi_on else None)
                if not oi_on:   # restrict to the period where OI exists, for a fair comparison
                    oc = oi_change(b["t"], data["oi"][c], 24)
                    s = s & ~np.isnan(oc)
                sigs[c] = s
            tr = run_rule(data, "h1", 24, sigs, 3, STOP, TP)
            st = stats(tr)
            ra, p = random_baseline(data, "h1", tr, 24, 3, STOP, TP) if tr else (None, None)
            resOI[name] = {"n": st["n"], "avg": st.get("avg"), "win": st.get("win"), "rand_avg": ra, "p": p}
            md.append(f"| {name} | {st['n']} | {pct(st.get('avg'))} | {pct(st.get('win'), 0, False)} | {pct(ra)} | {'—' if p is None else f'{p:.2f}'} |")
        S["rules"]["C_oi"] = resOI
    else:
        md.append("\nОИ-вариант не проверен: нет истории открытого интереса (OKX не отдал данные по этим монетам).\n")
        S["rules"]["C_oi"] = None

    # ── 1h replication of A and C (last ~7 months) ─────────────────────────────────────────────────────────────────────
    h1c = [c for c in memes if c in data["h1"]]
    if h1c:
        t1 = np.concatenate([data["h1"][c]["t"] for c in h1c])
        md.append(f"\n## Повтор на свечах 1ч ({day(int(t1.min()))} → {day(int(t1.max()))}) — проверка, что результат не артефакт 4ч\n")
        md.append(head)
        res1 = {}
        for name, mk in (("A K=3, 3д (1ч)", lambda b: sig_breakout(b, 24, 3)), ("A K=5, 3д (1ч)", lambda b: sig_breakout(b, 24, 5)),
                         ("C −25%, 3д (1ч)", lambda b: sig_dip(b, 24, 0.25)), ("C −20%, 3д (1ч)", lambda b: sig_dip(b, 24, 0.20))):
            sg = {c: mk(data["h1"][c]) for c in h1c}
            o, _ = describe(name, "h1", 24, {c: s for c, (s, _) in sg.items()}, {c: r for c, (_, r) in sg.items()}, 3, STOP, TP)
            res1[name] = o; md.append(row(o))
        S["rules"]["h1"] = res1

    # ── B rotation + D trend basket (daily) ────────────────────────────────────────────────────────────────────────────
    dc = [m for m in memes if m in data["d1"]]
    if len(dc) >= 4:
        tl, C, V = daily_matrix(data, dc)
        md.append(f"\n## B. Ротация по силе (дневные свечи, {day(int(tl[21]))} → {day(int(tl[-1]))}) и D. корзина «выше 20-дневной средней»\n")
        md.append("Каждый день держим N мем-монет с лучшей доходностью за L дней (равные доли, ребаланс раз в день, издержки на оборот + фандинг).\n")
        md.append("| вариант | итог $10k | макс. просадка | месяцев в плюсе | входов в день | 1-я / 2-я половина |")
        md.append("|---|---|---|---|---|---|")
        resB = {}

        def brow(name, r):
            h1_, h2_ = half_ret(r)
            md.append(f"| {name} | ${r['final']:,.0f} | {pct(r['dd'], 0)} | {pct(r['months_pos'], 0, False)} | {r['per_day']:.2f} | {pct(h1_, 0)} / {pct(h2_, 0)} |")
            return {"final": r["final"], "dd": r["dd"], "months_pos": r["months_pos"], "per_day": r["per_day"], "half1": h1_, "half2": h2_, "from": r["from"], "mret": r["mret"]}
        base = rotation(data, dc, mode="all", tl=tl, C=C, V=V)
        resB["все мемы поровну"] = brow("все мемы поровну (база)", base)
        resB["D: выше 20-дн. средней"] = brow("D: все мемы выше 20-дн. средней, иначе кэш", rotation(data, dc, mode="all", trend=True, tl=tl, C=C, V=V))
        grid = []
        for L in (3, 5, 7):
            for N in (2, 3, 5):
                r = rotation(data, dc, L=L, N=N, tl=tl, C=C, V=V)
                grid.append((L, N, r["final"]))
                if (L, N) in ((5, 3), (3, 3), (7, 3), (5, 2), (5, 5)):
                    resB[f"топ-{N} по {L}д"] = brow(f"топ-{N} по {L} дням", r)
        r = rotation(data, dc, L=5, N=3, trend=True, tl=tl, C=C, V=V)
        resB["топ-3 по 5д + выше средней"] = brow("топ-3 по 5 дням + только выше 20-дн. средней", r)
        r = rotation(data, dc, L=5, N=3, mode="bottom", tl=tl, C=C, V=V)
        resB["худшие-3 по 5д"] = brow("худшие-3 по 5 дням (проверка: это не просто «рынок»)", r)
        rnd = [rotation(data, dc, N=3, mode="random", seed=s, tl=tl, C=C, V=V)["final"] for s in range(200)]
        ref = rotation(data, dc, L=5, N=3, tl=tl, C=C, V=V)
        S["rules"]["B_random3"] = {"mean": float(np.mean(rnd)), "p95": float(np.percentile(rnd, 95)), "p_ref": float(np.mean([x >= ref["final"] for x in rnd]))}
        md.append(f"\nСлучайные 3 мема каждый день (200 прогонов): в среднем ${np.mean(rnd):,.0f}, 95-й процентиль ${np.percentile(rnd, 95):,.0f}; "
                  f"эталон «топ-3 по 5 дням» лучше случайного в {100 - S['rules']['B_random3']['p_ref'] * 100:.0f}% прогонов.\n")
        gpos = sum(1 for *_, f in grid if f > START); gbeat = sum(1 for *_, f in grid if f > base["final"])
        md.append(f"Сетка L∈{{3,5,7}} × N∈{{2,3,5}}: в плюсе {gpos} из 9, лучше корзины «все поровну» {gbeat} из 9. "
                  + "; ".join(f"L{L}/N{N} ${f:,.0f}" for L, N, f in grid) + "\n")
        S["rules"]["B_grid"] = {"pos": gpos, "beat_base": gbeat, "cells": [[L, N, f] for L, N, f in grid]}
        S["rules"]["B"] = resB
        if breg:
            for nm, rr in (("ротация топ-3/5д", ref), ("все мемы", base)):
                up = [x for x, t in zip(rr["rets"], rr["ts"]) if breg(int(t)) is True]
                dn = [x for x, t in zip(rr["rets"], rr["ts"]) if breg(int(t)) is False]
                S["rules"].setdefault("B_btc", {})[nm] = {"up": float(np.mean(up)) if up else None, "up_n": len(up), "dn": float(np.mean(dn)) if dn else None, "dn_n": len(dn)}
        # monthly table of the reference rotation and base
        md.append("\nПо месяцам (эталон «топ-3 по 5 дням» / все мемы поровну): " + ", ".join(f"{m} {pct(ref['mret'][m], 0)}/{pct(base['mret'].get(m), 0)}" for m in sorted(ref["mret"])) + "\n")

    # ── regime table ───────────────────────────────────────────────────────────────────────────────────────────────────
    md.append("\n## Влияет ли рынок (BTC) и «мем-сезон»\n")
    md.append("BTC «в росте» = дневное закрытие выше 50-дневной средней. «Мем-сезон» = ≥ 50% мемов выше своей 20-дневной средней. Средняя сделка (число сделок).\n")
    md.append("| правило | BTC в росте | BTC ниже средней | мем-сезон | не сезон |")
    md.append("|---|---|---|---|---|")
    for grp in ("A", "C"):
        for name, o in S["rules"].get(grp, {}).items():
            if "эталон" in name:
                md.append(f"| {name} | {pct(o.get('btc_up'))} ({o.get('btc_up_n', 0)}) | {pct(o.get('btc_dn'))} ({o.get('btc_dn_n', 0)}) | "
                          f"{pct(o.get('season'))} ({o.get('season_n', 0)}) | {pct(o.get('noseason'))} ({o.get('noseason_n', 0)}) |")
    for nm, v in (S["rules"].get("B_btc") or {}).items():
        md.append(f"| {nm} (средний день) | {pct(v['up'], 3)} ({v['up_n']} дн.) | {pct(v['dn'], 3)} ({v['dn_n']} дн.) | | |")

    # ── per-month for the reference event rules ───────────────────────────────────────────────────────────────────────
    md.append("\n## По месяцам: сумма доходностей сделок (эталоны)\n")
    for grp in ("A", "C"):
        for name, o in S["rules"].get(grp, {}).items():
            if "эталон" in name and o.get("by_month"):
                md.append(f"- {name}: " + ", ".join(f"{m} {pct(v, 0)}" for m, v in sorted(o["by_month"].items())))
                if o.get("port_mret"):
                    md.append(f"  - счёт $10k по месяцам: " + ", ".join(f"{m} {pct(v, 1)}" for m, v in sorted(o["port_mret"].items())))

    # ── verdict (criteria fixed before looking) ────────────────────────────────────────────────────────────────────────
    md.append("\n## Критерии «устойчиво» (заданы до прогона)\n")
    md.append("Средняя после издержек > 0 и выше случайной на ≥ 0.5 п.п. при p ≤ 0.05; обе половины периода в плюсе; ≥ 60% месяцев в плюсе (счёт); "
              "≥ 30 сделок; повтор на 1ч в плюсе. Для ротации: обгоняет «все поровну» и ≥ 95% случайных прогонов, обе половины в плюсе, ≥ 6 из 9 ячеек в плюсе.\n")
    verdict = {}
    h1r = S["rules"].get("h1", {})
    for grp, rep1 in (("A", "A K=3, 3д (1ч)"), ("C", "C −25%, 3д (1ч)")):
        for name, o in S["rules"].get(grp, {}).items():
            if not o.get("n"):
                verdict[name] = False; continue
            p = o.get("port") or {}
            ok = (o["avg"] > 0 and o.get("rand_avg") is not None and o["avg"] - o["rand_avg"] >= 0.005 and (o.get("p") or 1) <= 0.05
                  and (o.get("half1") or -1) > 0 and (o.get("half2") or -1) > 0 and (p.get("months_pos") or 0) >= 0.6 and o["n"] >= 30
                  and (h1r.get(rep1, {}).get("avg") or -1) > 0)
            verdict[name] = bool(ok)
    if S["rules"].get("B"):
        b = S["rules"]["B"]; ref = b.get("топ-3 по 5д", {}); base_f = b["все мемы поровну"]["final"]
        verdict["B топ-3 по 5д"] = bool(ref and ref["final"] > base_f and S["rules"]["B_random3"]["p_ref"] <= 0.05
                                        and ref["half1"] > 0 and ref["half2"] > 0 and S["rules"]["B_grid"]["pos"] >= 6)
        dd = b["D: выше 20-дн. средней"]
        verdict["D корзина выше средней"] = bool(dd["final"] > START and dd["final"] > base_f and dd["half1"] > 0 and dd["half2"] > 0)
    S["verdict"] = verdict
    for k, v in verdict.items():
        md.append(f"- {k}: **{'устойчиво' if v else 'не прошло'}**")
    return "\n".join(md), S


# ── probe of the live-scanner sources ──────────────────────────────────────────────────────────────────────────────────

def probe() -> str:
    out = []

    def show(title, obj, n=1500):
        out.append(f"### {title}\n```\n{json.dumps(obj, ensure_ascii=False, default=str)[:n]}\n```")
    ctx = hl({"type": "metaAndAssetCtxs"}) or [{}, []]
    uni = (ctx[0] or {}).get("universe", [])
    names = [u["name"] for u in uni]
    present = [m for m in MEME_COINS if m in names]
    show("HL metaAndAssetCtxs", {"n": len(uni), "memes_present": present, "delisted_memes": [u["name"] for u in uni if u.get("isDelisted") and u["name"] in MEME_COINS],
                                 "sample_universe": uni[:1], "sample_ctx": (ctx[1] or [None])[:1]}, 2500)
    cg = get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category=meme-token&per_page=250&page=1")
    if isinstance(cg, list):
        syms = {x.get("symbol", "").upper(): x.get("market_cap_rank") for x in cg}
        extra = [n for n in names if (n[1:] if n.startswith("k") and n[1:].isupper() else n).upper() in syms and n not in MEME_COINS]
        show("HL perps in CoinGecko meme-token category but NOT in MEME_COINS", extra, 2000)
    else:
        show("CoinGecko meme category", cg)
    gh = {"Accept": "application/json;version=20230302"}
    tp = get("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?include=base_token,dex&page=1", headers=gh)
    d = (tp.get("data") or []) if isinstance(tp, dict) else []
    show("GeckoTerminal trending_pools (first pool)", {"n": len(d), "first": d[:1], "included0": (tp.get("included") or [])[:1] if isinstance(tp, dict) else None, "err": tp.get("__error__") if isinstance(tp, dict) else None}, 3000)
    npj = get("https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1", headers=gh)
    show("GeckoTerminal new_pools", {"n": len((npj or {}).get("data") or []) if isinstance(npj, dict) else None, "err": npj.get("__error__") if isinstance(npj, dict) else None,
                                     "names": [x["attributes"].get("name") for x in ((npj or {}).get("data") or [])[:5]] if isinstance(npj, dict) else None})
    mints = []
    for x in d[:6]:
        bt = (((x.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
        if bt.startswith("solana_"):
            mints.append(bt[len("solana_"):])
    show("mints", mints)
    if mints:
        ds = get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(mints[:3]))
        show("DexScreener tokens/v1 (first pair)", ds[:1] if isinstance(ds, list) else ds, 2500)
        rpc = post("https://api.mainnet-beta.solana.com", {"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts", "params": [mints[:3], {"encoding": "jsonParsed"}]})
        show("Solana RPC getMultipleAccounts jsonParsed", rpc, 2500)
        rc = get(f"https://api.rugcheck.xyz/v1/tokens/{mints[0]}/report/summary")
        show("RugCheck summary", rc, 2000)
    bo = get("https://api.dexscreener.com/token-boosts/top/v1")
    show("DexScreener token-boosts/top (first 2)", bo[:2] if isinstance(bo, list) else bo, 1500)
    sr = get("https://api.dexscreener.com/latest/dex/search?q=solana%20meme")
    show("DexScreener search", {"n": len((sr or {}).get("pairs") or []) if isinstance(sr, dict) else None, "err": sr.get("__error__") if isinstance(sr, dict) else None})
    inst = get("https://www.okx.com/api/v5/public/instruments?instType=SWAP")
    ok = isinstance(inst, dict) and inst.get("data")
    oi = get("https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?instId=WIF-USDT-SWAP&period=1H&limit=3")
    show("OKX OI history WIF (3 rows)", {"instruments_ok": bool(ok), "oi": oi}, 1500)
    return "\n\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md"); ap.add_argument("--json"); ap.add_argument("--probe"); ap.add_argument("--synthetic", action="store_true")
    a = ap.parse_args()
    if a.probe:
        open(a.probe, "w").write(probe()); print("probe written"); return 0
    t0 = time.time()
    data = synthetic() if a.synthetic else download()
    t1 = time.time()
    md, S = study(data)
    S["seconds"] = {"download": round(t1 - t0), "study": round(time.time() - t1)}
    hdr = f"# Мем-перпы Hyperliquid — результаты {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC{' (СИНТЕТИКА — тест)' if a.synthetic else ''}\n\n"
    if a.md:
        open(a.md, "w").write(hdr + md + "\n")
    if a.json:
        def clean(o):
            if isinstance(o, dict):
                return {k: clean(v) for k, v in o.items() if k not in ("by_month", "port_mret", "mret")}
            if isinstance(o, (list, tuple)):
                return [clean(v) for v in o]
            if isinstance(o, (np.floating, float)):
                return None if not math.isfinite(float(o)) else round(float(o), 5)
            if isinstance(o, (np.integer,)):
                return int(o)
            return o
        open(a.json, "w").write(json.dumps(clean(S), ensure_ascii=False))
    print(hdr + md[:3000])
    print("seconds", S["seconds"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
