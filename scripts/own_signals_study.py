"""Our own signals with a steady flow (19.10.2026, owner: «нам нужны собственные сигналы … нужно движение, сигналы должны быть»;
«почему мы не будем открывать ничего без резкого пролива — проливы могут не наступить 2–3 недели»).
Runs in GitHub Actions only (.github/workflows/research-own-signals.yml): the research container cannot reach Binance.

Lessons from 3 037 golden signals (docs/research/golden.md): their hit rates match a coin flip exactly (target 1 step vs stop
2 steps → 67 %, the random-walk number), tight stops (≈1 % of price) and 10+ trades a day let costs and leverage eat the account.
So every rule here: (1) has a reason to have a direction (trend, regime), (2) uses wide stops in ATR (moves ≫ costs), (3) trades
a few times a day across many coins, not dozens, (4) is compared with a RANDOM entry of the same coin, side, year and holding time.
Earlier checks (swing.md, quiet_market.md, channels_false_breaks.md) found that intraday rules lose and only the daily swing
breakout S had a plus per trade; here S is extended to both sides, gated by our BTC regime, and joined by two more families.

Data: Binance USDT-M daily candles 2022-01 → today (data.binance.vision), ~40 liquid coins (coins that exist today — a survivor
bias that flatters longs; shorts are the cautious side here). Signal at a day's close, entry at the next open, costs 0.14 % a trade
plus 0.02 % a day of holding (funding), stop checked first inside a day.

Families (each long and short):
  B{N}  breakout: close above the N-day high and above the 20-day average → long (mirror → short). Exits: pde = close beyond
        the previous day's low/high; chand = 3 ATR from the best close; rr2 = stop 1.5 ATR / target 3 ATR. Hard stop 2.5 ATR
        (3–15 %), at most 14 days.
  P     pullback in a trend: above a rising 50-day average and RSI(2) < 10 → long, out above the 5-day average or after 10 days
        (mirror → short).
  M     weekly trend: every Monday the sign of the 28-day return → long or short for a week.
Gates: none; regime = longs only while BTC is above its 200-day average and up over 60 days, shorts only otherwise; soft = BTC vs
its 50-day average; own = the coin vs its own 100-day average.
Pass: plus in 2025 AND in 2026 separately, plus over 2022–2024, better than the random entry, ≥ 0.5 signals a day. Stdlib + numpy."""
import datetime as dt
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from fetch_alt100 import http, months_between, note, read_zip_csv  # noqa: E402

UM = "https://data.binance.vision/data/futures/um"
DAY = 86_400_000
COST, FUND_D = 0.14, 0.02
TODAY = dt.datetime.now(dt.timezone.utc).date()
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "BCH", "TRX", "NEAR", "ATOM", "ETC", "FIL",
         "APT", "ARB", "OP", "INJ", "SUI", "UNI", "AAVE", "XLM", "HBAR", "ICP", "RUNE", "STX", "GRT", "LDO", "SAND", "AXS", "CRV",
         "DYDX", "GALA", "TIA", "SEI", "WLD", "ENA"]
YEARS = ("2022", "2023", "2024", "2025", "2026")


def daily(sym: str):
    rows = []
    last_full = (TODAY.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    for mo in months_between("2021-06", last_full):
        s, b = http(f"{UM}/monthly/klines/{sym}/1d/{sym}-1d-{mo}.zip", binary=True)
        if s == 200:
            rows += read_zip_csv(b)
    d = TODAY.replace(day=1)
    while d < TODAY:
        s, b = http(f"{UM}/daily/klines/{sym}/1d/{sym}-1d-{d:%Y-%m-%d}.zip", binary=True)
        if s == 200:
            rows += read_zip_csv(b)
        d += dt.timedelta(days=1)
    if len(rows) < 120:
        return None
    seen = {}
    for r in rows:
        t = int(r[0]); t = t // 1000 if t > 10 ** 14 else t
        seen[t] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]) if len(r) > 7 and r[7] else 0.0,
                   float(r[5]) if len(r) > 5 and r[5] else 0.0)
    ts = sorted(seen)
    a = np.array([seen[t] for t in ts])
    return {"t": np.array(ts, dtype=np.int64), "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "qv": a[:, 4], "bv": a[:, 5]}


def sma(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def atr(K, n=14):
    pc = np.r_[K["c"][0], K["c"][:-1]]
    tr = np.maximum(K["h"] - K["l"], np.maximum(abs(K["h"] - pc), abs(K["l"] - pc)))
    return sma(tr, n)


def rsi2(c):
    d = np.diff(c, prepend=c[0])
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    au, ad = sma(up, 2), sma(dn, 2)
    return 100 - 100 / (1 + au / np.where(ad == 0, 1e-12, ad))


def prep(K, btc_gate):
    c = K["c"]
    K["sma5"], K["sma20"], K["sma50"], K["sma100"] = sma(c, 5), sma(c, 20), sma(c, 50), sma(c, 100)
    K["atr"] = atr(K)
    K["rsi2"] = rsi2(c)
    K["r28"] = c / np.r_[np.full(28, np.nan), c[:-28]] - 1
    for N in (5, 10, 20):
        hh = np.full(len(c), np.nan); ll = np.full(len(c), np.nan)
        for i in range(N, len(c)):
            hh[i] = K["h"][i - N:i].max(); ll[i] = K["l"][i - N:i].min()
        K[f"hh{N}"], K[f"ll{N}"] = hh, ll
    K["yr"] = np.array([dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc).year for x in K["t"]])
    idx = {t: i for i, t in enumerate(btc_gate["t"])}
    K["bull"] = np.array([btc_gate["bull"][idx[t]] if t in idx else np.nan for t in K["t"]], dtype=float)
    K["soft"] = np.array([btc_gate["soft"][idx[t]] if t in idx else np.nan for t in K["t"]], dtype=float)
    return K


def signals(K, fam, gate):
    """→ (long bool array, short bool array) at each day's close."""
    c = K["c"]
    with np.errstate(invalid="ignore"):
        if fam.startswith("B"):
            N = int(fam[1:])
            L = (c > K[f"hh{N}"]) & (c > K["sma20"])
            S = (c < K[f"ll{N}"]) & (c < K["sma20"])
        elif fam == "P":
            up = (c > K["sma50"]) & (K["sma50"] > np.r_[np.full(5, np.nan), K["sma50"][:-5]])
            dn = (c < K["sma50"]) & (K["sma50"] < np.r_[np.full(5, np.nan), K["sma50"][:-5]])
            L, S = up & (K["rsi2"] < 10), dn & (K["rsi2"] > 90)
        else:   # M — Mondays
            mon = np.array([dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).weekday() == 0 for t in K["t"]])
            L, S = mon & (K["r28"] > 0), mon & (K["r28"] < 0)
        if gate == "regime":
            L, S = L & (K["bull"] == 1), S & (K["bull"] == 0)
        elif gate == "soft":
            L, S = L & (K["soft"] == 1), S & (K["soft"] == 0)
        elif gate == "own":
            L, S = L & (c > K["sma100"]), S & (c < K["sma100"])
    return np.nan_to_num(L).astype(bool), np.nan_to_num(S).astype(bool)


def _trade(K, fam, ex, i, sg, sym):
    """One trade from the signal at the close of day i (entry at the next open). → (trade tuple, exit index j) or None when the
    trade is still open at the end of the data. Tuple: (open ms, close ms, side, net %, days held, sym, stop %, i, fam)."""
    t, o, h, l, c, A = K["t"], K["o"], K["h"], K["l"], K["c"], K["atr"]
    n = len(t)
    hold_max = 7 if fam == "M" else (10 if fam == "P" else 14)
    e = o[i + 1]
    a = A[i]
    if ex == "rr2":
        sd, td = 1.5 * a, 3.0 * a
    else:
        sd, td = min(max(2.5 * a, 0.03 * e), 0.15 * e), None
    stop = e - sg * sd
    tgt = e + sg * td if td else None
    best = e
    j, px, done = i + 1, None, False
    while j < n:
        if (sg == 1 and l[j] <= stop) or (sg == -1 and h[j] >= stop):
            px, done = stop, True
        elif tgt is not None and ((sg == 1 and h[j] >= tgt) or (sg == -1 and l[j] <= tgt)):
            px, done = tgt, True
        else:
            best = max(best, c[j]) if sg == 1 else min(best, c[j])
            if fam == "M":
                done = j - i >= hold_max
            elif fam == "P":
                done = (c[j] > K["sma5"][j]) if sg == 1 else (c[j] < K["sma5"][j])
            elif ex == "pde" and j > i + 1:
                done = (c[j] < l[j - 1]) if sg == 1 else (c[j] > h[j - 1])
            elif ex == "chand":
                done = (c[j] < best - 3 * A[j]) if sg == 1 else (c[j] > best + 3 * A[j])
            if not done and j - i >= hold_max:
                done = True
            if done:
                px = c[j]
        if done:
            break
        j += 1
    if not done:
        return None
    days = j - i
    net = sg * (px / e - 1) * 100 - COST - FUND_D * days
    return (int(t[i + 1]), int(t[j]), sg, float(net), days, sym, float(sd / e * 100), int(i), fam), j


def simulate(K, fam, gate, ex, sym):
    """One position per coin at a time. → list of (open ms, close ms, side, net %, days held, sym, stop %, i, fam)."""
    L, S = signals(K, fam, gate)
    n, out, i = len(K["t"]), [], 0
    while i < n - 2:
        if not (L[i] or S[i]) or (L[i] and S[i]) or np.isnan(K["atr"][i]):
            i += 1
            continue
        r = _trade(K, fam, ex, i, 1 if L[i] else -1, sym)
        if r is None:
            break                                             # still open at the end of the data
        out.append(r[0])
        i = r[1]                                              # a new signal can come at this close
    return out


_BASE: dict = {}


def baseline(KS, trades):
    """Random entry of the same coin, side, year and holding time (exact mean over all days of that coin-year)."""
    cache, vals = _BASE, []
    for o_t, _c, sg, _net, days, sym, *_ in trades:
        K = KS[sym]
        y = dt.datetime.fromtimestamp(o_t / 1000, dt.timezone.utc).year
        key = (sym, y, days)
        if key not in cache:
            ii = np.where(K["yr"] == y)[0]
            ii = ii[ii + days < len(K["c"])]
            cache[key] = float(np.mean(K["c"][ii + days] / K["o"][ii] - 1)) * 100 if len(ii) else 0.0
        vals.append(sg * cache[key] - COST - FUND_D * days)
    return float(np.mean(vals)) if vals else None


def summary(trades, KS, n_days):
    if not trades:
        return {"n": 0}
    net = np.array([x[3] for x in trades])
    yr = {}
    for x in trades:
        yr.setdefault(dt.datetime.fromtimestamp(x[0] / 1000, dt.timezone.utc).strftime("%Y"), []).append(x[3])
    return {"n": len(trades), "per_day": round(len(trades) / n_days, 2), "avg": round(float(net.mean()), 3),
            "win": round(float((net > 0).mean()) * 100), "rand": round(baseline(KS, trades), 3),
            "y": {y: [round(float(np.mean(yr[y])), 2), len(yr[y])] for y in YEARS if y in yr},
            "long": round(float(np.mean([x[3] for x in trades if x[2] == 1])), 3) if any(x[2] == 1 for x in trades) else None,
            "short": round(float(np.mean([x[3] for x in trades if x[2] == -1])), 3) if any(x[2] == -1 for x in trades) else None}


def account(trades, share=0.10, max_open=10, risk=None, per_day=None):
    """Compounding demo account: `share` of equity per trade (notional, 1×), at most `max_open` at once. With `risk` (% of
    equity lost at the stop): notional = risk ÷ stop distance, capped at 25 % of equity."""
    ev = sorted([(o, 0, k) for k, (o, *_r) in enumerate(trades)] + [(cl, 1, k) for k, (_o, cl, *_r) in enumerate(trades)])
    E, peak, dd, size, opened, curve = 1.0, 1.0, 0.0, {}, 0, []
    day_n = {}
    for tt, kind, k in ev:
        if kind == 0:
            dk = tt // 86_400_000
            if per_day is not None and day_n.get(dk, 0) >= per_day:
                continue
            if opened < max_open:
                day_n[dk] = day_n.get(dk, 0) + 1
                size[k] = E * (min(0.25, risk / trades[k][6]) if risk else share)
                opened += 1
            continue
        if k in size:
            E += size.pop(k) * trades[k][3] / 100
            opened -= 1
            peak = max(peak, E)
            dd = max(dd, 1 - E / peak)
            curve.append((tt, E))
    mo, prev, last = {}, 1.0, {}
    for tt, e in curve:
        last[dt.datetime.fromtimestamp(tt / 1000, dt.timezone.utc).strftime("%Y-%m")] = e
    for m in sorted(last):
        mo[m] = last[m] / prev - 1
        prev = last[m]
    v = np.array(list(mo.values())) if mo else np.array([0.0])
    yrs = {}
    for m, r in mo.items():
        yrs.setdefault(m[:4], []).append(r)
    return {"total_x": round(E, 2), "avg_month_pct": round(float(v.mean()) * 100, 2), "months_plus_pct": round(float((v > 0).mean()) * 100),
            "worst_month_pct": round(float(v.min()) * 100, 1), "max_dd_pct": round(-dd * 100, 1),
            "years_pct": {y: round((float(np.prod([1 + r for r in rs])) - 1) * 100, 1) for y, rs in yrs.items()},
            "last_months_pct": {m: round(r * 100, 1) for m, r in list(mo.items())[-6:]}}


def main():
    with ThreadPoolExecutor(12) as ex:
        raw = dict(zip(COINS, ex.map(lambda s: daily(s + "USDT"), COINS)))
    KS = {s: k for s, k in raw.items() if k is not None}
    B = KS["BTC"]
    bc = B["c"]
    s200, s50 = sma(bc, 200), sma(bc, 50)
    with np.errstate(invalid="ignore"):
        bull = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
        soft = np.where(np.isnan(s50), np.nan, (bc > s50).astype(float))
    gate = {"t": B["t"], "bull": bull, "soft": soft}
    for s in KS:
        prep(KS[s], gate)
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    n_days = (TODAY - dt.date(2022, 1, 1)).days
    note("own signals · data", json.dumps({"coins": len(KS), "missing": [s for s in COINS if s not in KS],
                                           "bull_days_since_2022_pct": round(float(np.nanmean(bull[B['t'] >= t0])) * 100)}))
    variants = []
    for fam in ("B5", "B10", "B20"):
        for exk in ("pde", "chand", "rr2"):
            for g in ("none", "regime", "soft", "own"):
                variants.append((fam, g, exk))
    for g in ("none", "regime", "own"):
        variants.append(("P", g, "-"))
        variants.append(("M", g, "-"))
    res, TR = {}, {}
    for fam, g, exk in variants:
        trs = [x for s, K in KS.items() for x in simulate(K, fam, g, exk, s) if x[0] >= t0]
        name = f"{fam}·{exk}·{g}" if exk != "-" else f"{fam}·{g}"
        TR[name] = trs
        res[name] = summary(trs, KS, n_days)

    def ok(r):
        y = r.get("y") or {}
        tr = [y[k] for k in ("2022", "2023", "2024") if k in y]
        tr_avg = sum(a * n for a, n in tr) / max(1, sum(n for _, n in tr))
        return (r.get("n", 0) and y.get("2025", [0])[0] > 0 and y.get("2026", [0])[0] > 0 and tr_avg > 0
                and r["avg"] > (r.get("rand") if r.get("rand") is not None else 0) and r["per_day"] >= 0.5)
    rank = sorted(res, key=lambda k: -min((res[k].get("y") or {}).get("2025", [-9])[0], (res[k].get("y") or {}).get("2026", [-9])[0]))
    compact = {k: [res[k]["n"], res[k].get("per_day"), res[k].get("avg"), res[k].get("rand"), res[k].get("long"), res[k].get("short"),
                   [(res[k].get("y") or {}).get(y, [None])[0] for y in YEARS]] for k in rank}
    note("own signals · all variants n per_day avg rand long short y22-26", json.dumps(compact, ensure_ascii=False))
    passed = [k for k in rank if ok(res[k])]
    note("own signals · passed", json.dumps({k: res[k] for k in passed[:12]}, ensure_ascii=False))
    acc = {}
    for k in (passed[:5] or rank[:3]):
        acc[k] = {"10pct": account(TR[k], 0.10, 10), "20pct": account(TR[k], 0.20, 10)}
    note("own signals · demo accounts for the best", json.dumps(acc, ensure_ascii=False))
    # the last 60 days: would these rules have given signals in today's market?
    recent = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)).timestamp() * 1000)
    rec = {k: {"n": len([x for x in TR[k] if x[0] >= recent]),
               "avg": round(float(np.mean([x[3] for x in TR[k] if x[0] >= recent])), 2) if any(x[0] >= recent for x in TR[k]) else None,
               "long": sum(1 for x in TR[k] if x[0] >= recent and x[2] == 1), "short": sum(1 for x in TR[k] if x[0] >= recent and x[2] == -1)}
           for k in (passed[:8] or rank[:5])}
    note("own signals · last 60 days", json.dumps(rec, ensure_ascii=False))


COINS2 = ["FET", "RENDER", "1000PEPE", "WIF", "JUP", "PYTH", "ONDO", "TAO", "1000BONK", "1000FLOKI", "ORDI", "JTO", "STRK", "PENDLE",
          "ENS", "IMX", "MANA", "EGLD", "THETA", "ALGO", "VET", "QNT", "KAVA", "ZIL", "MASK", "CHZ", "COMP", "SNX", "1INCH", "ZRO"]


def combo():
    """19.10, round 2. No single family is in plus every year: the pullback (P·regime) lost in 2024, the breakouts with a
    trailing exit (B·chand·regime) lost in 2025. Together? Same rules, same costs; accounts with fixed size and with fixed risk.
    Then the same rules on 30 OTHER coins never used to pick them (FET, RENDER, PEPE, WIF, …) — the out-of-sample check."""
    def load(coins):
        with ThreadPoolExecutor(12) as ex:
            raw = dict(zip(coins, ex.map(lambda s: daily(s + "USDT"), coins)))
        return {s: k for s, k in raw.items() if k is not None}
    KS = load(COINS)
    B = KS["BTC"]
    bc = B["c"]
    s200, s50 = sma(bc, 200), sma(bc, 50)
    with np.errstate(invalid="ignore"):
        bull = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
        soft = np.where(np.isnan(s50), np.nan, (bc > s50).astype(float))
    gate = {"t": B["t"], "bull": bull, "soft": soft}
    K2 = load(COINS2)
    for D in (KS, K2):
        for s in D:
            prep(D[s], gate)
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    n_days = (TODAY - dt.date(2022, 1, 1)).days
    rules = {"P": ("P", "regime", "-"), "B5c": ("B5", "regime", "chand"), "B10c": ("B10", "regime", "chand"), "B20c": ("B20", "regime", "chand")}
    combos = {"P": ["P"], "B10c": ["B10c"], "P+B10c": ["P", "B10c"], "P+B5c": ["P", "B5c"], "P+B20c": ["P", "B20c"], "P+B5c+B20c": ["P", "B5c", "B20c"]}
    for label, D in (("main 40 coins", KS), ("other 30 coins", K2)):
        TRr = {r: [x for s, K in D.items() for x in simulate(K, *spec, s) if x[0] >= t0] for r, spec in rules.items()}
        out = {}
        for cname, parts in combos.items():
            trs = sorted([x for r in parts for x in TRr[r]], key=lambda x: x[0])
            sm = summary([x[:6] for x in trs], D, n_days)
            out[cname] = {"n": sm.get("n"), "per_day": sm.get("per_day"), "avg": sm.get("avg"), "rand": sm.get("rand"),
                          "y": {y: v[0] for y, v in (sm.get("y") or {}).items()},
                          "fix10x15": account(trs, 0.10, 15), "risk1x20": account(trs, max_open=20, risk=1.0)}
            for a in ("fix10x15", "risk1x20"):
                out[cname][a].pop("last_months_pct", None)
        note(f"own signals combo · {label}", json.dumps(out, ensure_ascii=False))
        if label.startswith("main"):
            recent = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)).timestamp() * 1000)
            trs = sorted([x for r in ("P", "B10c") for x in TRr[r]], key=lambda x: x[0])
            mo = {}
            for x in trs:
                if x[0] >= recent:
                    m = dt.datetime.fromtimestamp(x[0] / 1000, dt.timezone.utc).strftime("%Y-%m")
                    mo.setdefault(m, []).append(x[3])
            note("own signals combo · P+B10c last 90 days by month", json.dumps({m: [len(v), round(float(np.mean(v)), 2), round(float(np.mean(np.array(v) > 0)) * 100)]
                                                                                for m, v in sorted(mo.items())}))


def sizing():
    """19.10, round 3: the pullback + 20-day trailing breakout (P+B20c) has a plus per trade in every year on both coin sets,
    but 10–15 positions of 10 % each fall together (drawdown −73…−77 %). How big can the account go and keep the drawdown sane?"""
    def load(coins):
        with ThreadPoolExecutor(12) as ex:
            raw = dict(zip(coins, ex.map(lambda s: daily(s + "USDT"), coins)))
        return {s: k for s, k in raw.items() if k is not None}
    KS = load(COINS)
    B = KS["BTC"]
    bc = B["c"]
    s200, s50 = sma(bc, 200), sma(bc, 50)
    with np.errstate(invalid="ignore"):
        bull = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
        soft = np.where(np.isnan(s50), np.nan, (bc > s50).astype(float))
    gate = {"t": B["t"], "bull": bull, "soft": soft}
    K2 = load(COINS2)
    for D in (KS, K2):
        for s in D:
            prep(D[s], gate)
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    for label, D in (("main 40", KS), ("other 30", K2)):
        trs = sorted([x for s, K in D.items() for spec in (("P", "regime", "-"), ("B20", "regime", "chand")) for x in simulate(K, *spec, s)
                      if x[0] >= t0], key=lambda x: x[0])
        out = {}
        for name, kw in (("5pct x10", dict(share=0.05, max_open=10)), ("5pct x20", dict(share=0.05, max_open=20)),
                         ("10pct x5", dict(share=0.10, max_open=5)), ("10pct x10", dict(share=0.10, max_open=10)),
                         ("risk0.5 x10", dict(max_open=10, risk=0.5)), ("risk0.5 x20", dict(max_open=20, risk=0.5)),
                         ("risk0.25 x20", dict(max_open=20, risk=0.25))):
            a = account(trs, **kw)
            a.pop("last_months_pct", None)
            out[name] = a
        note(f"own signals sizing · P+B20c {label}", json.dumps(out, ensure_ascii=False))
        lo = [x for x in trs if x[2] == 1]
        sh = [x for x in trs if x[2] == -1]
        note(f"own signals sizing · sides {label}", json.dumps({
            "long": {"n": len(lo), "avg": round(float(np.mean([x[3] for x in lo])), 2) if lo else None,
                     "stop_pct_median": round(float(np.median([x[6] for x in lo])), 1) if lo else None,
                     "days_median": float(np.median([x[4] for x in lo])) if lo else None},
            "short": {"n": len(sh), "avg": round(float(np.mean([x[3] for x in sh])), 2) if sh else None,
                      "stop_pct_median": round(float(np.median([x[6] for x in sh])), 1) if sh else None,
                      "days_median": float(np.median([x[4] for x in sh])) if sh else None},
            "win_pct": round(float(np.mean([x[3] > 0 for x in trs])) * 100), "per_day": round(len(trs) / ((TODAY - dt.date(2022, 1, 1)).days), 2)}))


def spot():
    """19.10, round 4 — the owner's own spot account: the same two rules, LONGS ONLY (spot cannot short), no leverage, spot
    costs (0.1 % fee per side + 0.02 % slippage → 0.24 % a trade, no funding). Bought only while the BTC regime says «рост»;
    otherwise the money waits in stablecoins at 4 % a year. 40 coins and 30 other coins; account sizes."""
    global COST, FUND_D
    COST, FUND_D = 0.24, 0.0

    def load(coins):
        with ThreadPoolExecutor(12) as ex:
            raw = dict(zip(coins, ex.map(lambda s: daily(s + "USDT"), coins)))
        return {s: k for s, k in raw.items() if k is not None}
    KS = load(COINS)
    B = KS["BTC"]
    bc = B["c"]
    s200, s50 = sma(bc, 200), sma(bc, 50)
    with np.errstate(invalid="ignore"):
        bull = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
        soft = np.where(np.isnan(s50), np.nan, (bc > s50).astype(float))
    gate = {"t": B["t"], "bull": bull, "soft": soft}
    K2 = load(COINS2)
    for D in (KS, K2):
        for s in D:
            prep(D[s], gate)
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    for label, D in (("main 40", KS), ("other 30", K2)):
        trs = sorted([x for s, K in D.items() for spec in (("P", "regime", "-"), ("B20", "regime", "chand")) for x in simulate(K, *spec, s)
                      if x[0] >= t0 and x[2] == 1], key=lambda x: x[0])
        yr = {}
        for x in trs:
            yr.setdefault(dt.datetime.fromtimestamp(x[0] / 1000, dt.timezone.utc).strftime("%Y"), []).append(x[3])
        out = {"trades": len(trs), "per_day": round(len(trs) / ((TODAY - dt.date(2022, 1, 1)).days), 2),
               "avg": round(float(np.mean([x[3] for x in trs])), 2), "win_pct": round(float(np.mean([x[3] > 0 for x in trs])) * 100),
               "y": {y: [round(float(np.mean(v)), 2), len(v)] for y, v in sorted(yr.items())}}
        for name, kw in (("10pct x10", dict(share=0.10, max_open=10)), ("15pct x6", dict(share=0.15, max_open=6)),
                         ("20pct x5", dict(share=0.20, max_open=5)), ("risk1 x10", dict(max_open=10, risk=1.0))):
            a = account(trs, **kw)
            a.pop("last_months_pct", None)
            out[name] = a
        note(f"own signals spot · longs only {label}", json.dumps(out, ensure_ascii=False))


EXPORT = ["BTC", "ETH", "SOL", "LINK", "HBAR", "BNB", "XRP", "SUI", "AVAX", "ADA", "DOGE", "ONDO", "NEAR", "GRASS", "RENDER", "ALGO",
          "VIRTUAL", "WLD", "PENDLE", "INJ", "XLM", "POL", "FET", "PYTH", "TRUMP", "STRK", "TIA", "ZK", "EIGEN", "ARKM", "IMX", "JTO",
          "WIF", "SAND", "SNX", "AR", "AAVE", "TRX", "TON", "JUP", "1000BONK", "1000SHIB", "NOT"]


def export():
    """19.10 — weekly closes (Monday 00:00 UTC) of ~40 coins, the BTC regime by week and the weekly equity of the spot long-only
    sleeve (risk 1 %, ≤ 10 open), so portfolio mixes can be compared offline. Generic coin list, no account data."""
    global COST, FUND_D
    with ThreadPoolExecutor(12) as ex:
        raw = dict(zip(EXPORT, ex.map(lambda s: daily(s + "USDT"), EXPORT)))
    KS = {s: k for s, k in raw.items() if k is not None}
    B = KS["BTC"]
    bc = B["c"]
    s200 = sma(bc, 200)
    with np.errstate(invalid="ignore"):
        bull = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
    mondays = [i for i, t in enumerate(B["t"]) if dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).weekday() == 0
               and t >= int(dt.datetime(2022, 1, 3, tzinfo=dt.timezone.utc).timestamp() * 1000)]
    wt = [int(B["t"][i]) for i in mondays]
    out = {"t0": wt[0], "weeks": len(wt), "bull": [None if np.isnan(bull[i - 1]) else int(bull[i - 1]) for i in mondays]}
    closes = {}
    for s, K in KS.items():
        idx = {int(t): k for k, t in enumerate(K["t"])}
        closes[s] = [round(float(K["o"][idx[t]]), 8) if t in idx else None for t in wt]     # the Monday OPEN = the price we trade at
    # the sleeve: spot longs only (pullback + 20-day breakout in «рост»), risk 1 %, ≤ 10 open
    COST, FUND_D = 0.24, 0.0
    KA = {s: KS[s] for s in KS}
    gate = {"t": B["t"], "bull": bull, "soft": bull}
    for s in KA:
        prep(KA[s], gate)
    trs = sorted([x for s, K in KA.items() if s in COINS for spec in (("P", "regime", "-"), ("B20", "regime", "chand"))
                  for x in simulate(K, *spec, s) if x[2] == 1 and x[0] >= wt[0]], key=lambda x: x[0])
    ev = sorted([(o, 0, k) for k, (o, *_r) in enumerate(trs)] + [(cl, 1, k) for k, (_o, cl, *_r) in enumerate(trs)])
    E, size, opened, curve = 1.0, {}, 0, []
    for tt, kind, k in ev:
        if kind == 0:
            if opened < 10:
                size[k] = E * min(0.25, 1.0 / trs[k][6])
                opened += 1
            continue
        if k in size:
            E += size.pop(k) * trs[k][3] / 100
            opened -= 1
            curve.append((tt, E))
    sleeve, j, e = [], 0, 1.0
    for t in wt:
        while j < len(curve) and curve[j][0] <= t:
            e = curve[j][1]
            j += 1
        sleeve.append(round(e, 4))
    out["sleeve"] = sleeve
    # annotations are cut at 4 096 characters: weekly log returns in 0.01 % as int16 (−32768 = no price), base64, in chunks
    import base64
    syms = list(closes)
    arr = []
    for s in syms:
        c = closes[s]
        for k in range(1, len(c)):
            a, b = c[k - 1], c[k]
            arr.append(-32768 if not a or not b else int(max(-32767, min(32767, round(np.log(b / a) * 10000)))))
    blob = base64.b64encode(np.array(arr, dtype="<i2").tobytes()).decode()
    out["syms"] = syms
    out["chunks"] = (len(blob) + 3999) // 4000
    note("export · meta", json.dumps(out))
    for i in range(out["chunks"]):
        note(f"export · r{i}", blob[i * 4000:(i + 1) * 4000])


def tune():
    """19.10, round 5 — the live desk's knobs: both sides vs longs only, a cap on new trades a day (the drawdown comes from
    many trades opened together), at most 10 / 20 open; risk 0.5 % a trade. 40 coins and 30 other coins."""
    def load(coins):
        with ThreadPoolExecutor(12) as ex:
            raw = dict(zip(coins, ex.map(lambda s: daily(s + "USDT"), coins)))
        return {s: k for s, k in raw.items() if k is not None}
    KS = load(COINS)
    B = KS["BTC"]
    bc = B["c"]
    s200 = sma(bc, 200)
    with np.errstate(invalid="ignore"):
        bull = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
    gate = {"t": B["t"], "bull": bull, "soft": bull}
    K2 = load(COINS2)
    for D in (KS, K2):
        for s in D:
            prep(D[s], gate)
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    for label, D in (("main 40", KS), ("other 30", K2)):
        allt = sorted([x for s, K in D.items() for spec in (("P", "regime", "-"), ("B20", "regime", "chand")) for x in simulate(K, *spec, s)
                       if x[0] >= t0], key=lambda x: x[0])
        out = {}
        for side in ("both", "long"):
            trs = allt if side == "both" else [x for x in allt if x[2] == 1]
            for cap in (None, 2, 4):
                for mo in (10, 20):
                    a = account(trs, max_open=mo, risk=0.5, per_day=cap)
                    out[f"{side} cap{cap or '∞'} x{mo}"] = [a["avg_month_pct"], a["max_dd_pct"], a["worst_month_pct"], list(a["years_pct"].values())]
        note(f"own signals tune · {label} avg_month dd worst years", json.dumps(out, ensure_ascii=False))


def altport():
    """19.10 — the live «ядро + альтсезон» model exactly as cnq/agents/alt_portfolio.py runs it, on DAILY closes:
    gauge = equal-weight alt index / BTC above its 91-day average AND ≥ 60 % of alts beat BTC over 91 days AND BTC «рост»;
    shares of the whole: futures 15 % (the own-signals long account, risk 0.5 %, ≤ 4 new a day, ≤ 20 open), dollars 10 % at 4 %,
    core BTC 15.8 / ETH 6.3 / SOL 4.7 / LINK 2.4 / NEAR 2.4, alt part 43.4 % in 10 alts while the gauge is on (else BTC);
    not «рост» → coins at 35 % of their size. Rebalanced daily when the state changes, else weekly; 0.2 % of the turnover."""
    basket = ["SOL", "LINK", "NEAR", "AVAX", "SUI", "ONDO", "AAVE", "HBAR", "PENDLE", "INJ"]
    coins = sorted(set(COINS) | set(basket))
    with ThreadPoolExecutor(12) as ex:
        raw = dict(zip(coins, ex.map(lambda s: daily(s + "USDT"), coins)))
    KS = {s: k for s, k in raw.items() if k is not None}
    B = KS["BTC"]
    days = [int(t) for t in B["t"]]
    cl = {s: {int(t): float(c) for t, c in zip(K["t"], K["c"])} for s, K in KS.items()}
    bc = B["c"]
    s200 = sma(bc, 200)
    with np.errstate(invalid="ignore"):
        bullv = ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]]))
    alts = [s for s in COINS if s not in ("BTC", "ETH") and s in cl]
    idx, ratio = 1.0, []
    for i, d in enumerate(days):
        if i:
            rs = [cl[s][d] / cl[s][days[i - 1]] - 1 for s in alts if d in cl[s] and days[i - 1] in cl[s]]
            idx *= 1 + (sum(rs) / len(rs) if rs else 0.0)
        ratio.append(idx / bc[i])
    ratio = np.array(ratio)
    # the futures part: the own-signals long account (risk 0.5 %, cap 4 a day, ≤ 20 open) as a daily curve
    prep_gate = {"t": B["t"], "bull": bullv.astype(float), "soft": bullv.astype(float)}
    for s in KS:
        prep(KS[s], prep_gate)
    trs = sorted([x for s, K in KS.items() if s in COINS for spec in (("P", "regime", "-"), ("B20", "regime", "chand"))
                  for x in simulate(K, *spec, s) if x[2] == 1], key=lambda x: x[0])
    ev = sorted([(o, 0, k) for k, (o, *_r) in enumerate(trs)] + [(c2, 1, k) for k, (_o, c2, *_r) in enumerate(trs)])
    E, size, opened, curve, day_n = 1.0, {}, 0, {}, {}
    for tt, kind, k in ev:
        if kind == 0:
            dk = tt // 86_400_000
            if day_n.get(dk, 0) >= 4 or opened >= 20:
                continue
            day_n[dk] = day_n.get(dk, 0) + 1
            size[k] = E * min(0.25, 0.5 / trs[k][6])
            opened += 1
        elif k in size:
            E += size.pop(k) * trs[k][3] / 100
            opened -= 1
            curve[tt // 86_400_000 * 86_400_000] = E
    fut, last = [], 1.0
    for d in days:
        last = curve.get(d, last)
        fut.append(last)
    core = {"BTC": 15.8, "ETH": 6.3, "SOL": 4.7, "LINK": 2.4, "NEAR": 2.4}
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    start = next(i for i, d in enumerate(days) if d >= t0)
    out = {}
    for name, use_gauge in (("model", True), ("always alts", False), ("never alts", None)):
        V, hold, prev_state, curve2 = 1.0, None, None, []
        peak, dd, on_days = 1.0, 0.0, 0
        for i in range(start, len(days) - 1):
            d = days[i]
            bull = bool(bullv[i])
            if use_gauge is None:
                on = False
            elif use_gauge is False:
                on = True
            else:
                d0 = days[i - 91]
                b_ret = cl["BTC"][d] / cl["BTC"][d0] - 1
                beat = [cl[s][d] / cl[s][d0] - 1 > b_ret for s in alts if d0 in cl[s] and d in cl[s]]
                br = sum(beat) / len(beat) if len(beat) >= 10 else 0
                on = bool(bull and br >= 0.60 and ratio[i] > ratio[i - 90:i + 1].mean())
            on_days += on
            state = (bull, on)
            if hold is None or state != prev_state or (d // 86_400_000) % 7 == 0:
                t = dict(core)
                if on:
                    av = [s for s in basket if s in cl and d in cl[s]]
                    for s in av:
                        t[s] = t.get(s, 0) + 43.4 / len(av)
                else:
                    t["BTC"] += 43.4
                if not bull:
                    t = {s: w * 0.35 for s, w in t.items()}
                coins_w = sum(t.values())
                new = {s: w / 100 * V for s, w in t.items()}
                new["_fut"] = 0.15 * V
                new["_cash"] = V - sum(new.values())
                if hold is not None:
                    turn = sum(abs(new.get(s, 0) - hold.get(s, 0)) for s in set(new) | set(hold)) / 2
                    V -= turn * 0.002
                    new = {s: v * (V / sum(new.values())) for s, v in new.items()}
                hold, prev_state = new, state
            d1 = days[i + 1]
            for s in list(hold):
                if s == "_cash":
                    hold[s] *= 1 + 0.04 / 365
                elif s == "_fut":
                    hold[s] *= fut[i + 1] / fut[i] if fut[i] else 1
                elif d in cl[s] and d1 in cl[s]:
                    hold[s] *= cl[s][d1] / cl[s][d]
            V = sum(hold.values())
            peak = max(peak, V)
            dd = max(dd, 1 - V / peak)
            curve2.append((d1, V))
        yrs, pv = {}, 1.0
        for y in ("2022", "2023", "2024", "2025", "2026"):
            pts = [v for t, v in curve2 if dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y") == y]
            if pts:
                yrs[y] = round((pts[-1] / pv - 1) * 100)
                pv = pts[-1]
        out[name] = {"x": round(V, 2), "dd": round(-dd * 100), "years": yrs, "alt_days": on_days}
    note("altport · the live model on daily closes since 2022", json.dumps(out, ensure_ascii=False))



# ------------------------------------------------------------------------------------------------ round 6: how many a day, which
FLOW_SCORES = ("order", "rs_hi", "rs_lo", "atr_lo", "atr_hi", "surge", "liq", "ext_hi", "rsi_lo", "P_first", "C_first")


def _load(coins):
    with ThreadPoolExecutor(12) as ex:
        raw = dict(zip(coins, ex.map(lambda s: daily(s + "USDT"), coins)))
    return {s: k for s, k in raw.items() if k is not None}


def flow_cands(D, order, t0, side=1, gate="regime"):
    """EVERY long signal day of P and C (not one trade after another): the account decides which to take, like the live desk,
    which sees the same coin signal again the next day when it skipped it. Features are known at the signal day's close."""
    out = []
    for s, K in D.items():
        for fam, ex in (("P", "-"), ("B20", "chand")):
            L, S = signals(K, fam, gate)
            for i in np.where((L & ~S) if side == 1 else (S & ~L))[0]:
                i = int(i)
                if i >= len(K["t"]) - 2 or np.isnan(K["atr"][i]):
                    continue
                r = _trade(K, fam, ex, i, side, s)
                if r is None or r[0][0] < t0:
                    continue
                tr, j = r
                f = lambda k: float(K[k][i]) if not np.isnan(K[k][i]) else 0.0  # noqa: E731
                out.append({"o": tr[0], "c": tr[1], "net": tr[3], "stop": tr[6], "sym": s, "fam": "P" if fam == "P" else "C", "sg": side,
                            "i": i, "j": j, "e": float(K["o"][i + 1]), "rank": order.get(s, 999),
                            "rs_hi": f("rs60"), "atr_hi": f("atrp"), "surge": f("surge"), "liq": f("vol30"), "ext_hi": f("ext"),
                            "surge_b30": f("surge_b30"), "surge_q20": f("surge_q20"), "surge_q50": f("surge_q50"),
                            "surge_vc30": f("surge_vc30"),
                            "rsi_lo": -f("rsi2") if fam == "P" else -100.0})
    return out


def flow_feats(D, btc):
    bidx = {int(t): k for k, t in enumerate(btc["t"])}
    bc = btc["c"]
    br60 = bc / np.r_[np.full(60, np.nan), bc[:-60]] - 1
    for K in D.values():
        c = K["c"]
        r60 = c / np.r_[np.full(60, np.nan), c[:-60]] - 1
        K["rs60"] = r60 - np.array([br60[bidx[int(t)]] if int(t) in bidx else np.nan for t in K["t"]])
        K["atrp"] = K["atr"] / c
        K["vol30"] = sma(K["qv"], 30)
        with np.errstate(invalid="ignore", divide="ignore"):
            K["surge"] = K["qv"] / np.where(K["vol30"] > 0, K["vol30"], np.nan)
            for nm, ser, n in (("surge_b30", K["bv"], 30), ("surge_q20", K["qv"], 20), ("surge_q50", K["qv"], 50),
                               ("surge_vc30", K["bv"] * c, 30)):
                av = sma(ser, n)
                K[nm] = ser / np.where(av > 0, av, np.nan)
            K["ext"] = (c - K["sma50"]) / K["atr"]


_IDX: dict = {}


def flow_account(C, D, days, cap=None, score="order", risk=0.5, budget=None, heat=None, max_open=20, gross=2.0, seed=None,
                 fam_cap=None, mult=1.0, min_surge=None, surge_key="surge", fam_only=None, top=None, curve_out=None):
    """Daily mark-to-market account. Each day: today's entries (signals of yesterday's close) → skip a coin+rule already open →
    rank by `score` → take up to `cap` (None = all) within max_open, the gross notional cap (× equity) and the open-risk cap
    `heat` (% of equity); size = risk % of the (realised) equity lost at the stop, notional ≤ 25 %. With `budget` the day's
    new risk is split equally among the trades taken (each ≤ risk). Then today's exits; equity = realised + open at the close."""
    risk, budget, heat = risk * mult, (budget * mult if budget else None), (heat * mult if heat else None)
    rng = np.random.default_rng(seed) if score == "random" else None
    by_o, by_c = {}, {}
    for k, x in enumerate(C):
        by_o.setdefault(x["o"], []).append(k)
    key = id(D)
    if key not in _IDX:
        _IDX.clear()
        _IDX[key] = {s: {int(t): q for q, t in enumerate(K["t"])} for s, K in D.items()}
    idx = _IDX[key]
    E, opn, keys, curve, taken, skipped_good = 1.0, {}, set(), [], [], []
    n_c, n_t, max_o, opened_m = 0, 0, 0, {}
    for d in days:
        cand = [k for k in by_o.get(d, []) if (C[k]["sym"], C[k]["fam"]) not in keys
                and (min_surge is None or C[k][surge_key] >= min_surge) and (fam_only is None or C[k]["fam"] == fam_only)
                and (top is None or C[k].get("vrank", 0) < top)]
        if cand:
            n_c += len(cand)
            if score == "random":
                rng.shuffle(cand)
            elif score == "order":
                cand.sort(key=lambda k: (C[k]["rank"], C[k]["fam"]))
            elif score == "P_first":
                cand.sort(key=lambda k: (C[k]["fam"] != "P", C[k]["rank"]))
            elif score == "C_first":
                cand.sort(key=lambda k: (C[k]["fam"] != "C", C[k]["rank"]))
            elif score in ("rs_lo", "atr_lo"):
                cand.sort(key=lambda k: C[k]["rs_hi" if score == "rs_lo" else "atr_hi"])
            else:
                cand.sort(key=lambda k: -C[k][score])
            room = max_open - len(opn)
            n_take = min(len(cand), room, cap if cap is not None else 10 ** 6)
            r_each = min(risk, budget / max(1, n_take)) if budget else risk
            got, per_fam = 0, {}
            g_now = sum(v[0] for v in opn.values())
            h_now = sum(v[2] for v in opn.values())
            for k in cand:
                x = C[k]
                if got >= n_take:
                    break
                if fam_cap and per_fam.get(x["fam"], 0) >= fam_cap:
                    continue
                if heat is not None and h_now + r_each > heat + 1e-9:
                    break
                size = E * min(0.25, r_each / x["stop"])
                if g_now + size > gross * E:
                    continue
                opn[k] = (size, x["e"], r_each)
                keys.add((x["sym"], x["fam"]))
                by_c.setdefault(x["c"], []).append(k)
                g_now += size; h_now += r_each; got += 1
                per_fam[x["fam"]] = per_fam.get(x["fam"], 0) + 1
                taken.append(x["net"])
                mk = dt.datetime.fromtimestamp(d / 1000, dt.timezone.utc).strftime("%Y-%m")
                opened_m[mk] = opened_m.get(mk, 0) + 1
            n_t += got
        for k in by_c.pop(d, []):
            if k in opn:
                size = opn.pop(k)[0]
                E += size * C[k]["net"] / 100
                keys.discard((C[k]["sym"], C[k]["fam"]))
        mtm = 0.0
        for k, (size, e, _r) in opn.items():
            q = idx[C[k]["sym"]].get(d)
            if q is not None:
                mtm += size * C[k].get("sg", 1) * (D[C[k]["sym"]]["c"][q] / e - 1)
        curve.append((d, E + mtm))
        max_o = max(max_o, len(opn))
    if curve_out is not None:
        curve_out.extend(curve)
    eq = np.array([v for _, v in curve])
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    last = {}
    for d, v in curve:
        last[dt.datetime.fromtimestamp(d / 1000, dt.timezone.utc).strftime("%Y-%m")] = v
    mo, prev = [], 1.0
    yrs = {}
    for m in sorted(last):
        r = last[m] / prev - 1
        mo.append(r)
        yrs.setdefault(m[:4], []).append(r)
        prev = last[m]
    v = np.array(mo)
    return {"m": round(float(v.mean()) * 100, 2), "dd": round(dd * 100, 1), "wm": round(float(v.min()) * 100, 1),
            "tpd": round(n_t / len(days), 2), "x": round(float(eq[-1]), 2), "avg": round(float(np.mean(taken)), 2) if taken else None,
            "y": [round((float(np.prod([1 + r for r in yrs.get(y, [])])) - 1) * 100) for y in YEARS], "cand_pd": round(n_c / len(days), 2),
            "max_open": max_o, "plus_m": round(float((v > 0).mean()) * 100), "last12": [round(r * 100, 1) for r in mo[-12:]],
            "n": n_t, "active_m": round(len(opened_m) / max(1, len(last)) * 100)}


def _row(a):
    return [a["m"], a["dd"], a["wm"], a["tpd"], a["avg"]] + a["y"]


def flow():
    """19.10, round 6 — owner: «что это за правило не больше 4 сделок в день? … главное качество, а не количество, но хочется и
    не упускать возможности». The live desk takes at most 4 new trades a day — the FIRST 4 in the coin list (largest coins),
    which is no selection at all. Here, on a daily mark-to-market account (the drawdown counts open trades), longs only, P + C
    in «рост», risk 0.5 %, ≤ 20 open, gross ≤ 2× equity: (1) caps 1…8 and none; (2) which ones to take when there are more
    signals than the cap — by strength vs BTC, volatility, volume surge, liquidity, trend extension, pullback depth, rule —
    against a RANDOM pick (20 seeds); (3) take all but split a daily risk budget; (4) a cap on the total open risk; (5) more
    open positions; (6) 70 coins instead of 40. Fair comparison: the same policies at ½…2× risk → the monthly return at the
    SAME drawdown (−25 %). Rows: [avg month %, max dd %, worst month %, trades a day, avg trade %, 2022…2026 %]."""
    KS, K2 = _load(COINS), _load(COINS2)
    B = KS["BTC"]
    bc = B["c"]
    s200 = sma(bc, 200)
    with np.errstate(invalid="ignore"):
        bull = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
    gate = {"t": B["t"], "bull": bull, "soft": bull}
    for D in (KS, K2):
        for s in D:
            prep(D[s], gate)
        flow_feats(D, B)
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    days = [int(t) for t in B["t"] if t >= t0]
    order = {s: k for k, s in enumerate(COINS + COINS2)}
    K70 = dict(KS, **K2)
    summary_best = {}
    for label, D in (("40", KS), ("30", K2), ("70", K70)):
        C = flow_cands(D, order, t0)
        # candidates per «рост» day and what the cap of 4 (in list order) leaves out
        pd = {}
        for x in C:
            pd[x["o"]] = pd.get(x["o"], 0) + 1
        cnt = np.array(list(pd.values()))
        res = {}
        for cap in (1, 2, 3, 4, 6, 8, None):
            res[f"order c{cap or 'all'}"] = _row(flow_account(C, D, days, cap=cap))
        for cap in (2, 4, 6):
            rs = [flow_account(C, D, days, cap=cap, score="random", seed=sd) for sd in range(20)]
            ms_ = np.array([r["m"] for r in rs]); dds = np.array([r["dd"] for r in rs])
            res[f"random c{cap}"] = [round(float(ms_.mean()), 2), round(float(dds.mean()), 1), round(float(ms_.min()), 2), round(float(ms_.max()), 2),
                                     round(float(dds.min()), 1)]
            for sc in FLOW_SCORES[1:]:
                res[f"{sc} c{cap}"] = _row(flow_account(C, D, days, cap=cap, score=sc))
        note(f"own flow · {label} coins · caps & picks", json.dumps({"cand_days": len(cnt), "cand_per_day": [round(float(cnt.mean()), 1), int(np.median(cnt)),
                                                                     int(np.quantile(cnt, 0.9)), int(cnt.max())],
                                                                     "days_over4_pct": round(float((cnt > 4).mean()) * 100), "res": res}, ensure_ascii=False))
        res2 = {}
        for b in (1.0, 1.5, 2.0, 3.0):
            res2[f"budget{b}"] = _row(flow_account(C, D, days, budget=b, max_open=40))
        for h in (3.0, 5.0, 7.5, 10.0):
            res2[f"heat{h}"] = _row(flow_account(C, D, days, heat=h, max_open=40))
        for mo_ in (10, 30, 40):
            res2[f"open{mo_}"] = _row(flow_account(C, D, days, max_open=mo_))
        res2["P2+C2"] = _row(flow_account(C, D, days, cap=4, fam_cap=2))
        # the same drawdown: each policy at ½…2.5× its risk → the monthly return where the drawdown crosses −25 %
        best_rank = sorted([k for k in res if not k.startswith("random") and not k.startswith("order")],
                           key=lambda k: -(res[k][0] / max(1.0, -res[k][1])))[:4]
        pols = {"order c4": dict(cap=4), "order call": dict(cap=None), "budget2": dict(budget=2.0, max_open=40), "heat5": dict(heat=5.0, max_open=40),
                "open40": dict(max_open=40)}
        for k in best_rank:
            sc, cp = k.rsplit(" c", 1)
            pols[k] = dict(cap=int(cp), score=sc)
        at25 = {}
        pols["random c4"] = dict(cap=4, score="random")
        for name, kw in pols.items():
            pts = []
            for m_ in (0.25, 0.5, 1.0, 1.5, 2.0, 3.0):
                if kw.get("score") == "random":
                    rs = [flow_account(C, D, days, mult=m_, seed=sd, **kw) for sd in range(8)]
                    pts.append((round(float(np.mean([r["dd"] for r in rs])), 1), round(float(np.mean([r["m"] for r in rs])), 2)))
                else:
                    a = flow_account(C, D, days, mult=m_, **kw)
                    pts.append((a["dd"], a["m"]))
            pts.sort(key=lambda z: -z[0])            # drawdown from small to large
            val = None
            seg = next(((p1, p2) for p1, p2 in zip(pts, pts[1:]) if p1[0] >= -25 >= p2[0]), None)
            if seg is None:                          # not crossed: extrapolate from the nearest two points
                seg = (pts[-2], pts[-1]) if pts[-1][0] > -25 else (pts[0], pts[1])
            (d1, m1), (d2, m2) = seg
            val = round(m1 + (m2 - m1) * (-25 - d1) / (d2 - d1), 2) if d2 != d1 else m1
            at25[name] = {"m@dd25": val, "pts": [list(p_) for p_ in pts]}
        res2["at_dd25"] = at25
        summary_best[label] = {k: v["m@dd25"] for k, v in at25.items()}
        note(f"own flow · {label} coins · budget heat open, same dd", json.dumps(res2, ensure_ascii=False))
    note("own flow · monthly % at the same drawdown −25 %", json.dumps(summary_best, ensure_ascii=False))


def _at25(C, D, days, seeds=None, **kw):
    """The monthly return of a policy at the drawdown −25 % (its risk scaled ¼…3×), with its points."""
    pts = []
    for m_ in (0.25, 0.5, 1.0, 1.5, 2.0, 3.0):
        if seeds:
            rs = [flow_account(C, D, days, mult=m_, seed=sd, **kw) for sd in range(seeds)]
            pts.append((float(np.mean([r["dd"] for r in rs])), float(np.mean([r["m"] for r in rs]))))
        else:
            a = flow_account(C, D, days, mult=m_, **kw)
            pts.append((a["dd"], a["m"]))
    pts.sort(key=lambda z: -z[0])
    seg = next(((p1, p2) for p1, p2 in zip(pts, pts[1:]) if p1[0] >= -25 >= p2[0]), None)
    if seg is None:
        seg = (pts[-2], pts[-1]) if pts[-1][0] > -25 else (pts[0], pts[1])
    (d1, m1), (d2, m2) = seg
    return round(m1 + (m2 - m1) * (-25 - d1) / (d2 - d1), 2) if d2 != d1 else round(m1, 2)


def flow2():
    """19.10, round 7 — the check of round 6's finding: ranking the day's signals by the VOLUME SURGE (the signal day's
    volume ÷ its 30-day average) was at or near the top on 40 coins, on 30 other coins and on all 70, at caps 2, 4 and 6.
    Picked from 10 scores, so it may be luck. Here: the same at caps 2…6 against a random pick at the SAME drawdown (−25 %,
    8 seeds), with other volume windows (20 / 50 days) and base volume instead of quote volume (BingX gives base volume),
    a volume filter instead of a cap (take every signal with volume above its average), and ranking + a daily risk budget.
    Rows: [monthly % at dd −25 %, monthly % at risk 0.5 %, dd %, avg trade %, trades a day, 2023…2026 % at risk 0.5 %]."""
    KS, K2 = _load(COINS), _load(COINS2)
    B = KS["BTC"]
    bc = B["c"]
    s200 = sma(bc, 200)
    with np.errstate(invalid="ignore"):
        bull = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
    gate = {"t": B["t"], "bull": bull, "soft": bull}
    for D in (KS, K2):
        for s in D:
            prep(D[s], gate)
        flow_feats(D, B)
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    days = [int(t) for t in B["t"] if t >= t0]
    order = {s: k for k, s in enumerate(COINS + COINS2)}
    allres = {}
    for label, D in (("40", KS), ("30", K2), ("70", dict(KS, **K2))):
        C = flow_cands(D, order, t0)
        res = {}

        def row(name, seeds=None, **kw):
            if seeds:
                rs = [flow_account(C, D, days, seed=sd, **kw) for sd in range(seeds)]
                a = {k: round(float(np.mean([r[k] for r in rs])), 2) for k in ("m", "dd", "avg", "tpd")}
                a["y"] = [round(float(np.mean([r["y"][q] for r in rs]))) for q in range(len(YEARS))]
            else:
                a = flow_account(C, D, days, **kw)
            res[name] = [_at25(C, D, days, seeds=seeds, **kw), a["m"], a["dd"], a["avg"], a["tpd"]] + a["y"][1:]
        for cap in (2, 3, 4, 5, 6):
            row(f"random c{cap}", seeds=8, cap=cap, score="random")
            row(f"order c{cap}", cap=cap)
            for sc in ("surge", "surge_b30", "surge_q20", "surge_q50"):
                row(f"{sc} c{cap}", cap=cap, score=sc)
        row("order all", cap=None)
        for ms in (1.0, 1.5, 2.0):
            row(f"vol>{ms} all", min_surge=ms, max_open=40, score="surge")
            row(f"vol>{ms} c4", min_surge=ms, cap=4, score="surge")
        for b in (1.5, 2.0):
            row(f"surge top6 budget{b}", cap=6, score="surge", budget=b)
        allres[label] = res
        note(f"own flow2 · {label} coins", json.dumps(res, ensure_ascii=False))
    # the verdict: surge vs random at the same drawdown, every cap, every coin set
    note("own flow2 · surge minus random, monthly % at dd −25 %", json.dumps(
        {lab: {f"c{c}": [round(r[f"{sc} c{c}"][0] - r[f"random c{c}"][0], 2) for sc in ("surge", "surge_b30", "surge_q20", "surge_q50", "order")]
               for c in (2, 3, 4, 5, 6)} for lab, r in allres.items()}))


def flow3():
    """19.10, round 8 — the live version of round 7's winner: take EVERY signal whose day's volume is ≥ 1.5× its 30-day average
    (no daily cap), ≤ 40 open, gross ≤ 2× equity. Live uses BingX candles (base volume) → the volume in dollars = base volume ×
    close; thresholds 1.3 / 1.5 / 1.7; the pullback and the breakout apart; caps 4 / 6 as a safety; risk 0.5 / 0.75 %; months
    in plus; the last 12 months; which of the 70 coins BingX lists."""
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request("https://open-api.bingx.com/openApi/swap/v2/quote/contracts",
                                                           headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
            bx = {x["symbol"].split("-")[0] for x in json.loads(r.read().decode()).get("data") or []}
        note("own flow3 · BingX", json.dumps({"missing": [s for s in COINS + COINS2 if s not in bx],
                                              "alt_names": {s: [b for b in bx if b.endswith(s.replace("1000", "")) and b != s][:3]
                                                            for s in COINS + COINS2 if s not in bx}}))
    except Exception as e:  # noqa: BLE001
        note("own flow3 · BingX", str(e)[:200])
    KS, K2 = _load(COINS), _load(COINS2)
    B = KS["BTC"]
    bc = B["c"]
    s200 = sma(bc, 200)
    with np.errstate(invalid="ignore"):
        bull = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
    gate = {"t": B["t"], "bull": bull, "soft": bull}
    for D in (KS, K2):
        for s in D:
            prep(D[s], gate)
        flow_feats(D, B)
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    days = [int(t) for t in B["t"] if t >= t0]
    order = {s: k for k, s in enumerate(COINS + COINS2)}
    for label, D in (("40", KS), ("30", K2), ("70", dict(KS, **K2))):
        C = flow_cands(D, order, t0)
        res = {}

        def row(name, **kw):
            a = flow_account(C, D, days, **kw)
            res[name] = [a["m"], a["dd"], a["wm"], a["plus_m"], a["avg"], a["tpd"], a["max_open"]] + a["y"][1:]
            return a
        row("live now (order c4)", cap=4)
        for th in (1.3, 1.5, 1.7):
            row(f"vc>{th} all", min_surge=th, surge_key="surge_vc30", max_open=40, score="surge_vc30")
        best = row("vc>1.5 all r0.75", min_surge=1.5, surge_key="surge_vc30", max_open=40, score="surge_vc30", risk=0.75)
        a = row("vc>1.5 all", min_surge=1.5, surge_key="surge_vc30", max_open=40, score="surge_vc30")
        for cap in (4, 6):
            row(f"vc>1.5 c{cap}", min_surge=1.5, surge_key="surge_vc30", max_open=40, score="surge_vc30", cap=cap)
        for fam in ("P", "C"):
            row(f"vc>1.5 {fam} only", min_surge=1.5, surge_key="surge_vc30", max_open=40, score="surge_vc30", fam_only=fam)
            row(f"{fam} only, no filter", max_open=40, fam_only=fam)
        res["vc>1.5 all · last 12 months"] = a["last12"]
        res["vc>1.5 all · trades"] = a["n"]
        res["at dd −25 %"] = _at25(C, D, days, min_surge=1.5, surge_key="surge_vc30", max_open=40, score="surge_vc30")
        note(f"own flow3 · {label} coins · [m, dd, worst m, months+ %, avg trade, a day, max open, 2023…2026]",
             json.dumps(res, ensure_ascii=False))


S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
TRADFI = {"XAU", "XAG", "XPT", "XPD", "CL", "BZ", "NG", "HG", "TSLA", "NVDA", "AAPL", "AMZN", "GOOGL", "META", "MSFT", "MSTR", "COIN",
          "HOOD", "CRCL", "QQQ", "SPY", "INTC", "AMD", "PLTR", "NFLX", "BABA", "ORCL", "SPX", "NDX"}     # 2025–26 TradFi perps
NOT_COINS = re.compile(r"^(BTCDOM|DEFI|USDC|BUSD|TUSD|FDUSD|USDP|DAI|EUR|GBP|XAU|XAG|PAXG|BLUEBIRD|FOOTBALL)")


def all_um_symbols():
    syms, marker = set(), ""
    for _ in range(20):
        s, b = http(f"{S3}?delimiter=/&prefix=data/futures/um/monthly/klines/" + (f"&marker={marker}" if marker else ""))
        if s != 200:
            break
        p = re.findall(r"<Prefix>data/futures/um/monthly/klines/([A-Z0-9]+)/</Prefix>", b)
        syms |= set(p)
        if "<IsTruncated>true</IsTruncated>" not in b or not p:
            break
        marker = f"data/futures/um/monthly/klines/{p[-1]}/"
    return sorted(x for x in syms if re.fullmatch(r"[A-Z0-9]+USDT", x) and not NOT_COINS.match(x) and x[:-4] not in TRADFI)


def daily_wide(sym):
    """Daily candles of one USDT-M perp from its monthly archives (listed months only — delisted coins included)."""
    s, b = http(f"{S3}?prefix=data/futures/um/monthly/klines/{sym}/1d/")
    if s != 200:
        return None
    months = sorted(m for m in re.findall(rf"{sym}-1d-(\d{{4}}-\d{{2}})\.zip</Key>", b) if m >= "2021-06")
    rows = []
    for mo in months:
        st, z = http(f"{UM}/monthly/klines/{sym}/1d/{sym}-1d-{mo}.zip", binary=True)
        if st == 200:
            rows += read_zip_csv(z)
    if len(rows) < 120:
        return None
    seen = {}
    for r in rows:
        t = int(r[0]); t = t // 1000 if t > 10 ** 14 else t
        seen[t] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]) if len(r) > 7 and r[7] else 0.0,
                   float(r[5]) if len(r) > 5 and r[5] else 0.0)
    ts = sorted(seen)
    a = np.array([seen[t] for t in ts])
    return {"t": np.array(ts, dtype=np.int64), "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "qv": a[:, 4], "bv": a[:, 5]}


def _wide_data():
    syms = all_um_symbols()
    with ThreadPoolExecutor(48) as ex:
        raw = dict(zip(syms, ex.map(daily_wide, syms)))
    D = {s[:-4]: k for s, k in raw.items() if k is not None}
    B = D["BTC"]
    bc = B["c"]
    s200, s50 = sma(bc, 200), sma(bc, 50)
    with np.errstate(invalid="ignore"):
        bull = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
        soft = np.where(np.isnan(s50), np.nan, (bc > s50).astype(float))
    gate = {"t": B["t"], "bull": bull, "soft": soft}
    for s in D:
        prep(D[s], gate)
    flow_feats(D, B)
    by_day = {}
    for s, K in D.items():
        v = sma(K["bv"] * K["c"], 30)          # 30-day dollar volume (base × close), as live computes it
        for q in range(119, len(K["t"])):
            if not np.isnan(v[q]):
                by_day.setdefault(int(K["t"][q]), []).append((float(v[q]), s))
    rank, day_top = {}, {}
    for d, lst in by_day.items():
        lst.sort(reverse=True)
        day_top[d] = [s for _v, s in lst]
        for r_, (_v, s) in enumerate(lst):
            rank[(s, d)] = r_
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    days = [int(t) for t in B["t"] if t >= t0]
    C = flow_cands(D, {}, t0)
    for x in C:
        x["vrank"] = rank.get((x["sym"], int(D[x["sym"]]["t"][x["i"]])), 10 ** 6)
    _WIDE.update(rank=rank, day_top=day_top, bull=bull, t0=t0)
    return D, C, days


_WIDE: dict = {}


def with_rank(C, D):
    for x in C:
        x["vrank"] = _WIDE["rank"].get((x["sym"], int(D[x["sym"]]["t"][x["i"]])), 10 ** 6)
    return C


def wide2():
    """19.10, round 10 — round 9 (every Binance perp, delisted included, point-in-time top N by volume): more coins = WORSE
    (top 40 → dd −24 %, top 100 → −57 %, all → −72 %), and today's 70 were flattered by survivors (×4 at the same drawdown).
    Here the knobs around the honest winner, the point-in-time top 40: N 20…60, the volume mark 1.3…2.0 or none, 20 / 40
    open, a random pick of 4 as the baseline. Rows: [m @dd −25 %, m, dd, worst m, avg trade, a day, 2023…2026]."""
    D, C, days = _wide_data()
    res = {}

    def row(name, seeds=None, **kw):
        if seeds:
            rs = [flow_account(C, D, days, seed=sd, **kw) for sd in range(seeds)]
            a = {k: round(float(np.mean([r[k] for r in rs])), 2) for k in ("m", "dd", "wm", "avg", "tpd")}
            a["y"] = [round(float(np.mean([r["y"][q] for r in rs]))) for q in range(len(YEARS))]
        else:
            a = flow_account(C, D, days, **kw)
        res[name] = [_at25(C, D, days, seeds=seeds, **kw), a["m"], a["dd"], a["wm"], a["avg"], a["tpd"]] + a["y"][1:]
    for top in (20, 30, 40, 50, 60):
        row(f"top{top} vol≥1.5", min_surge=1.5, surge_key="surge_vc30", score="surge_vc30", max_open=40, top=top)
    for thr in (1.3, 1.7, 2.0):
        row(f"top40 vol≥{thr}", min_surge=thr, surge_key="surge_vc30", score="surge_vc30", max_open=40, top=40)
    row("top40 vol≥1.5 open20", min_surge=1.5, surge_key="surge_vc30", score="surge_vc30", max_open=20, top=40)
    row("top40 no mark, all", max_open=40, top=40)
    row("top40 random 4", seeds=6, cap=4, score="random", max_open=40, top=40)
    row("top30 vol≥1.3", min_surge=1.3, surge_key="surge_vc30", score="surge_vc30", max_open=40, top=30)
    row("top50 vol≥1.3", min_surge=1.3, surge_key="surge_vc30", score="surge_vc30", max_open=40, top=50)
    row("top40 vol≥1.5 C only", min_surge=1.5, surge_key="surge_vc30", score="surge_vc30", max_open=40, top=40, fam_only="C")
    note("own wide2 · point-in-time top N, knobs", json.dumps(res, ensure_ascii=False))


def wide():
    """19.10, round 9 — owner: «раз статистика улучшается с количеством монет … хотя бы до 100, либо топ 1000». The honest test:
    EVERY Binance USDT-M perp in the archive, delisted ones included (no survivor bias), and each day the coin set is the top N
    by its 30-day dollar volume ON THAT DAY (a coin needs 120 days of history). N = 40 / 70 / 100 / 150 / 200 / 300 / all.
    Rule = the live one: P + C longs in «рост», the signal day's dollar volume ≥ 1.5× its 30-day average, no daily cap,
    ≤ 40 (or 60) open, gross ≤ 2× equity, risk 0.5 %, the higher volume first; also without the volume mark. Rows:
    [monthly % at dd −25 %, monthly % at risk 0.5 %, dd %, worst month %, avg trade %, trades a day, 2023…2026 %]."""
    t_start = dt.datetime.now()
    syms = all_um_symbols()
    with ThreadPoolExecutor(48) as ex:
        raw = dict(zip(syms, ex.map(daily_wide, syms)))
    D = {s[:-4]: k for s, k in raw.items() if k is not None}
    note("own wide · data", json.dumps({"symbols": len(syms), "with_120_days": len(D), "minutes": round((dt.datetime.now() - t_start).seconds / 60, 1),
                                        "delisted_like": sum(1 for k in D.values() if k["t"][-1] < int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=40)).timestamp() * 1000))}))
    B = D["BTC"]
    bc = B["c"]
    s200 = sma(bc, 200)
    with np.errstate(invalid="ignore"):
        bull = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
    gate = {"t": B["t"], "bull": bull, "soft": bull}
    for s in D:
        prep(D[s], gate)
    flow_feats(D, B)
    # the point-in-time volume rank: each day, coins with ≥ 120 days of history ranked by their 30-day dollar volume
    by_day = {}
    for s, K in D.items():
        v = K["vol30"]
        for q in range(119, len(K["t"])):
            if not np.isnan(v[q]):
                by_day.setdefault(int(K["t"][q]), []).append((float(v[q]), s))
    rank = {}
    for d, lst in by_day.items():
        lst.sort(reverse=True)
        for r_, (_v, s) in enumerate(lst):
            rank[(s, d)] = r_
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    days = [int(t) for t in B["t"] if t >= t0]
    C = flow_cands(D, {}, t0)
    for x in C:
        x["vrank"] = rank.get((x["sym"], int(D[x["sym"]]["t"][x["i"]])), 10 ** 6)
    note("own wide · candidates", json.dumps({"n": len(C), "in_top100": sum(1 for x in C if x["vrank"] < 100),
                                              "coins_ever_top100": len({x["sym"] for x in C if x["vrank"] < 100})}))
    res = {}
    for top in (40, 70, 100, 150, 200, 300, None):
        for mo_ in (40, 60):
            if mo_ == 60 and (top or 999) < 100:
                continue
            kw = dict(min_surge=1.5, surge_key="surge_vc30", score="surge_vc30", max_open=mo_, top=top)
            a = flow_account(C, D, days, **kw)
            res[f"top{top or 'all'} vol≥1.5 open{mo_}"] = [_at25(C, D, days, **kw), a["m"], a["dd"], a["wm"], a["avg"], a["tpd"]] + a["y"][1:]
        a = flow_account(C, D, days, max_open=40, top=top, cap=4)
        res[f"top{top or 'all'} no mark, 4 a day"] = [_at25(C, D, days, max_open=40, top=top, cap=4), a["m"], a["dd"], a["wm"], a["avg"], a["tpd"]] + a["y"][1:]
    note("own wide · point-in-time top N by volume (delisted coins included)", json.dumps(res, ensure_ascii=False))
    # the fixed 70 of today (survivors) vs the same rule on the point-in-time top 70
    fixed = set(COINS) | set(COINS2)
    Cf = [x for x in C if x["sym"] in fixed]
    kw = dict(min_surge=1.5, surge_key="surge_vc30", score="surge_vc30", max_open=40)
    a = flow_account(Cf, D, days, **kw)
    note("own wide · today's 70 (survivors)", json.dumps([_at25(Cf, D, days, **kw), a["m"], a["dd"], a["wm"], a["avg"], a["tpd"]] + a["y"][1:]))
    for thr in (1.3, 2.0):
        kw = dict(min_surge=thr, surge_key="surge_vc30", score="surge_vc30", max_open=60, top=150)
        a = flow_account(C, D, days, **kw)
        res[f"top150 vol≥{thr} open60"] = [_at25(C, D, days, **kw), a["m"], a["dd"], a["wm"], a["avg"], a["tpd"]] + a["y"][1:]
    note("own wide · thresholds on top150", json.dumps({k: v for k, v in res.items() if k.startswith("top150 vol≥1.3") or k.startswith("top150 vol≥2")}))


def pool_probe():
    """19.10 — the live desk's daily trading set, computed now from BingX exactly as cnq/agents/own_desk.py does it."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from cnq.agents import own_desk as od
    vols = od.fetch_volumes()
    ranked = [x for x, _v in sorted(vols.items(), key=lambda kv: -kv[1]) if od.tradable(x)][:od.POOL_N]
    pool = list(dict.fromkeys(od.COINS + ranked))
    with ThreadPoolExecutor(6) as ex:
        rows = dict(zip(pool, ex.map(lambda s: _safe_call(od.fetch_daily, s), pool)))
    dv = {s: od.dollar_vol30(k[:-1]) for s, k in rows.items() if k and len(k) >= 120 and od.tradable(s)}
    top = sorted((s for s in dv if dv[s]), key=lambda s: -dv[s])[:od.TOP_N]
    note("own pool probe", json.dumps({"tickers": len(vols), "pool": len(pool), "with_candles": sum(1 for k in rows.values() if k),
                                       "no_volume": [s for s in rows if rows[s] and s not in dv][:10],
                                       "top40": [[s, round(dv[s] / 1e6)] for s in top], "core_out": [s for s in od.COINS if s not in top]}))


def _safe_call(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return None


def own_bear():
    """19.10, round 11 — the desk waits in dollars whenever the market is not «рост» (most of the last 12 months). On the honest
    universe (every Binance perp, delisted included, the point-in-time top 40 by volume): (1) the SHORTS of the same rules with
    the volume mark in a market that is not «рост»; (2) a softer gate — BTC above / below its 50-day average. Rows:
    [m @dd −25 %, m, dd, worst m, avg trade, a day, months with trades %, 2022…2026]."""
    D, CL, days = _wide_data()
    t0 = _WIDE["t0"]
    CS = with_rank(flow_cands(D, {}, t0, side=-1, gate="regime"), D)
    CLs = with_rank(flow_cands(D, {}, t0, side=1, gate="soft"), D)
    CSs = with_rank(flow_cands(D, {}, t0, side=-1, gate="soft"), D)
    res = {}

    def row(name, C, **kw):
        kw = dict(dict(surge_key="surge_vc30", score="surge_vc30", max_open=40, top=40), **kw)
        a = flow_account(C, D, days, **kw)
        res[name] = [_at25(C, D, days, **kw), a["m"], a["dd"], a["wm"], a["avg"], a["tpd"], a["active_m"]] + a["y"]
    row("longs «рост» vol≥1.5 (live)", CL, min_surge=1.5)
    row("shorts «не рост» vol≥1.5", CS, min_surge=1.5)
    row("shorts «не рост» vol≥2.0", CS, min_surge=2.0)
    row("shorts «не рост» no mark", CS)
    row("longs + shorts vol≥1.5", CL + CS, min_surge=1.5)
    row("longs 1.5 + shorts 2.0", CL + [x for x in CS if x["surge_vc30"] >= 2.0], min_surge=1.5)
    row("soft gate longs vol≥1.5", CLs, min_surge=1.5)
    row("soft gate shorts vol≥1.5", CSs, min_surge=1.5)
    row("soft gate both vol≥1.5", CLs + CSs, min_surge=1.5)
    row("shorts «не рост» vol≥1.5 C only", CS, min_surge=1.5, fam_only="C")
    note("own bear · shorts and a softer gate, top 40 (delisted included)", json.dumps(res, ensure_ascii=False))


def altport2():
    """19.10, round 12 — the honest re-check of «ядро + альтсезон» before the owner's rebalance: the alt basket was picked TODAY
    (SOL, LINK, NEAR, AVAX, SUI, ONDO, AAVE, HBAR, PENDLE, INJ) — survivors. Here, on every Binance perp (delisted included):
    the gauge on the point-in-time top 40 alts by volume, the basket at each rebalance = the 10 largest alts by 30-day volume
    (≥ 180 days of history), the futures part = the honest own-signals curve (top 40, volume mark). Variants: the gauge, alts
    whenever «рост», never alts, all coins in BTC, the live fixed basket, a core of BTC + ETH only."""
    D, CL, days_all = _wide_data()
    rank, day_top, bull_arr, t0 = _WIDE["rank"], _WIDE["day_top"], _WIDE["bull"], _WIDE["t0"]
    B = D["BTC"]
    days = [int(t) for t in B["t"]]
    bullv = {int(t): (b == 1) for t, b in zip(B["t"], bull_arr)}
    cl = {s: {int(t): float(c) for t, c in zip(K["t"], K["c"])} for s, K in D.items()}
    first = {s: int(K["t"][0]) for s, K in D.items()}
    alt_ok = lambda s: s not in ("BTC", "ETH")  # noqa: E731
    # the honest futures part
    fc = []
    flow_account(CL, D, days_all, min_surge=1.5, surge_key="surge_vc30", score="surge_vc30", max_open=40, top=40, curve_out=fc)
    fut = dict(fc)
    # gauge on the point-in-time top 40 alts
    idx, ratio, gauge = 1.0, {}, {}
    rlist = []
    for i, d in enumerate(days):
        top = [s for s in day_top.get(d, []) if alt_ok(s)][:40]
        if i and top:
            p = days[i - 1]
            rs = [cl[s][d] / cl[s][p] - 1 for s in top if p in cl[s] and d in cl[s] and cl[s][p] > 0]
            idx *= 1 + (float(np.mean(rs)) if rs else 0.0)
        ratio[d] = idx / cl["BTC"][d]
        rlist.append(ratio[d])
        if i >= 91 and top:
            d0 = days[i - 91]
            b_ret = cl["BTC"][d] / cl["BTC"][d0] - 1
            beat = [cl[s][d] / cl[s][d0] - 1 > b_ret for s in top if d0 in cl[s] and d in cl[s]]
            br = sum(beat) / len(beat) if len(beat) >= 10 else 0
            gauge[d] = bool(bullv.get(d) and br >= 0.60 and rlist[-1] > float(np.mean(rlist[-91:])))
    fixed_basket = ["SOL", "LINK", "NEAR", "AVAX", "SUI", "ONDO", "AAVE", "HBAR", "PENDLE", "INJ"]

    vol180 = {}
    for s_, K in D.items():
        v = sma(K["bv"] * K["c"], 180)
        vol180[s_] = {int(t): float(x) for t, x in zip(K["t"], v) if not np.isnan(x)}

    def basket(d, how):
        if how == "fixed":
            return [s for s in fixed_basket if d in cl.get(s, {})]
        if how == "pit180":                      # steadier: the 180-day volume, coins with ≥ 365 days of history
            c_ = [(vol180[s][d], s) for s in vol180 if d in vol180[s] and alt_ok(s) and d - first[s] >= 365 * DAY]
            return [s for _v, s in sorted(c_, reverse=True)[:10]]
        if how == "pit5":
            return [s for s in day_top.get(d, []) if alt_ok(s) and d - first[s] >= 180 * DAY][:5]
        return [s for s in day_top.get(d, []) if alt_ok(s) and d - first[s] >= 180 * DAY][:10]

    def sim(mode, how="pit", core=None, alt_part=43.4, extra_btc=0.0):
        core = dict(core or {"BTC": 15.8, "ETH": 6.3, "SOL": 4.7, "LINK": 2.4, "NEAR": 2.4})
        core["BTC"] = core.get("BTC", 0) + extra_btc
        start = next(i for i, d in enumerate(days) if d >= t0)
        V, hold, prev, curve = 1.0, None, None, []
        for i in range(start, len(days) - 1):
            d, d1 = days[i], days[i + 1]
            bull = bool(bullv.get(d))
            on = {"gauge": gauge.get(d, False), "regime": bull, "never": False, "btc": False}[mode]
            state = (bull, on)
            if hold is None or state != prev or (d // DAY) % 7 == 0:
                t = {s: w for s, w in core.items() if d in cl.get(s, {})}
                if mode == "btc":
                    t = {"BTC": sum(core.values())}
                if on:
                    bk = basket(d, how)
                    for s in bk:
                        t[s] = t.get(s, 0) + alt_part / len(bk)
                else:
                    t["BTC"] = t.get("BTC", 0) + alt_part
                if not bull:
                    t = {s: w * 0.35 for s, w in t.items()}
                new = {s: w / 100 * V for s, w in t.items()}
                new["_fut"] = 0.15 * V
                new["_cash"] = V - sum(new.values())
                if hold is not None:
                    turn = sum(abs(new.get(s, 0) - hold.get(s, 0)) for s in set(new) | set(hold)) / 2
                    V -= turn * 0.002
                    k = V / sum(new.values())
                    new = {s: v * k for s, v in new.items()}
                hold, prev = new, state
            for s in list(hold):
                if s == "_cash":
                    hold[s] *= 1 + 0.04 / 365
                elif s == "_fut":
                    hold[s] *= (fut.get(d1) / fut.get(d)) if fut.get(d) and fut.get(d1) else 1
                elif d in cl.get(s, {}) and d1 in cl.get(s, {}):
                    hold[s] *= cl[s][d1] / cl[s][d]
            V = sum(hold.values())
            curve.append((d1, V))
        eq = np.array([v for _, v in curve])
        dd = float((eq / np.maximum.accumulate(eq) - 1).min())
        yrs, pv = [], 1.0
        for y in YEARS:
            pts = [v for t, v in curve if dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y") == y]
            if pts:
                yrs.append(round((pts[-1] / pv - 1) * 100))
                pv = pts[-1]
        return [round(float(eq[-1]), 2), round(dd * 100), *yrs]
    be = {"BTC": 22.1, "ETH": 9.5}
    res = {"gauge · basket by volume (honest)": sim("gauge"), "alts whenever «рост» · by volume": sim("regime"),
           "never alts (alt part in BTC)": sim("never"), "all coins in BTC (+ regime)": sim("btc"),
           "gauge · the live fixed basket (survivors)": sim("gauge", how="fixed"),
           "alts whenever «рост» · fixed basket": sim("regime", how="fixed"),
           "gauge · core BTC+ETH · by volume": sim("gauge", core=be), "never alts · core BTC+ETH": sim("never", core=be),
           "gauge · alt part 20 % · by volume": sim("gauge", alt_part=20.0),
           "gauge · basket by 180-day volume, ≥1 year": sim("gauge", how="pit180"),
           "alts whenever «рост» · by 180-day volume": sim("regime", how="pit180"),
           "gauge · top 5 alts by volume": sim("gauge", how="pit5"),
           "gauge · alt part 20 % · by 180-day volume": sim("gauge", how="pit180", alt_part=20.0),
           "gauge · alts 20 % + BTC 23.4 % · 180-day volume": sim("gauge", how="pit180", alt_part=20.0, extra_btc=23.4),
           "gauge · alts 20 % + BTC 23.4 % · 30-day volume": sim("gauge", how="pit", alt_part=20.0, extra_btc=23.4),
           "gauge · alts 10 % + BTC 33.4 % · 180-day volume": sim("gauge", how="pit180", alt_part=10.0, extra_btc=33.4),
           "alts 20 % whenever «рост» + BTC 23.4 % · 180-day": sim("regime", how="pit180", alt_part=20.0, extra_btc=23.4)}
    on_days = sum(1 for d, v in gauge.items() if v and d >= t0)
    today = days[-1]
    res["gauge days on since 2022"] = on_days
    res["gauge now"] = gauge.get(today)
    res["basket by volume now"] = basket(today, "pit")
    res["basket by 180-day volume now"] = basket(today, "pit180")
    note("altport2 · honest [x, dd %, 2022…2026 %]", json.dumps(res, ensure_ascii=False))


def spot_probe():
    """19.10 — which free data sources for a fundamental coin card answer from GitHub Actions (status, size, a sample)."""
    urls = {
        "llama protocols": "https://api.llama.fi/protocols",
        "llama fees overview": "https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue",
        "llama fees hist (aave)": "https://api.llama.fi/summary/fees/aave?dataType=dailyRevenue",
        "llama raises": "https://api.llama.fi/raises",
        "llama hacks": "https://api.llama.fi/hacks",
        "llama emissions": "https://api.llama.fi/emissions",
        "llama chains": "https://api.llama.fi/v2/chains",
        "llama coins chart": "https://coins.llama.fi/chart/coingecko:solana?start=1640995200&span=10&period=30d",
        "gecko markets": "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1",
        "gecko coin": "https://api.coingecko.com/api/v3/coins/solana?localization=false&tickers=true&market_data=true&community_data=true&developer_data=true",
        "gecko categories": "https://api.coingecko.com/api/v3/coins/categories",
        "github repo": "https://api.github.com/repos/solana-foundation/solana-web3.js",
        "cryptorank": "https://api.cryptorank.io/v1/currencies?limit=5",
        "tokenomist": "https://api.tokenomist.ai/v1/list",
        "defillama yields": "https://yields.llama.fi/pools",
    }
    out = {}
    for k, u in urls.items():
        st, b = http(u, tries=2)
        txt = b if isinstance(b, str) else ""
        sample = ""
        try:
            j = json.loads(txt)
            if isinstance(j, list):
                sample = f"list[{len(j)}] keys={sorted(j[0].keys())[:25] if j and isinstance(j[0], dict) else ''}"
            elif isinstance(j, dict):
                sample = f"dict keys={sorted(j.keys())[:25]}"
                for kk in ("protocols", "raises", "data"):
                    if isinstance(j.get(kk), list) and j[kk]:
                        sample += f" | {kk}[{len(j[kk])}] keys={sorted(j[kk][0].keys())[:30] if isinstance(j[kk][0], dict) else ''}"
        except Exception:  # noqa: BLE001
            sample = txt[:120]
        out[k] = [st, len(txt), sample[:420]]
    note("spot probe · free fundamental sources", json.dumps(out, ensure_ascii=False)[:3900])


# ------------------------------------------------------------------------------------------------ round 13: spot fundamentals
LL = "https://api.llama.fi"


def _jget(url, tries=3):
    st, b = http(url, tries=tries)
    if st != 200:
        return None
    try:
        return json.loads(b)
    except Exception:  # noqa: BLE001
        return None


def _llama_series(slug, dtype):
    j = _jget(f"{LL}/summary/fees/{slug}?dataType={dtype}")
    out = {}
    for row in (j or {}).get("totalDataChart") or []:
        try:
            t, v = int(row[0]), float(row[1] or 0)
        except (TypeError, ValueError, IndexError):
            continue
        out[t * 1000 // DAY * DAY] = v
    return out


def _spot_data():
    """19.10, round 13 — owner: «по споту можно проанализировать максимально всё … спот мы можем сделать идеальный, подойди
    профессионально». Which fundamentals actually predicted the next month of a token, honestly (every Binance perp, delisted
    included; monthly rebalance 2022–2026; terciles; equal weight):
      revenue universe (DefiLlama: protocol / chain revenue mapped to its token): revenue yield = revenue 30 d × 12 ÷ FDV, revenue
      growth 90 d vs the 90 d before, holders revenue (buybacks / burns / payouts) yield and «pays holders at all», fees yield;
      price universe (the point-in-time top 100 alts by 30-day dollar volume, ≥ 1 year of history): 12- and 3-month strength vs
      BTC, distance from the 1-year high, volume trend, age.
    FDV(t) = price(t) × today's total supply (CoinGecko top 1000) — an approximation; DefiLlama may revise old data. Rows:
    [top − bottom tercile a month %, t-stat, months top > bottom %, top − universe %, n months, coins a month, 2022…2026 spread]."""
    t_start = dt.datetime.now()
    syms = all_um_symbols()
    with ThreadPoolExecutor(48) as ex:
        raw = dict(zip(syms, ex.map(daily_wide, syms)))
    D = {s[:-4]: k for s, k in raw.items() if k is not None}
    cl = {s: {int(t): float(c) for t, c in zip(K["t"], K["c"])} for s, K in D.items()}
    B = D["BTC"]
    days = [int(t) for t in B["t"]]
    # token supply (CoinGecko top 1000, by symbol; the largest market cap wins a symbol clash)
    supply = {}
    for page in (1, 2, 3, 4):
        for c in _jget(f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page={page}") or []:
            sym = str(c.get("symbol") or "").upper()
            ts = c.get("total_supply") or c.get("max_supply") or c.get("circulating_supply")
            if sym and ts and sym not in supply:
                supply[sym] = float(ts)
        time.sleep(6)
    alias = {"1000PEPE": ("PEPE", 1000), "1000SHIB": ("SHIB", 1000), "1000BONK": ("BONK", 1000), "1000FLOKI": ("FLOKI", 1000),
             "1000SATS": ("SATS", 1000), "1000RATS": ("RATS", 1000), "1000LUNC": ("LUNC", 1000), "1000XEC": ("XEC", 1000)}
    # DefiLlama: protocols and chains → token symbol → slugs with revenue
    prot = _jget(f"{LL}/protocols") or []
    ov = _jget(f"{LL}/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue") or {}
    chains = _jget(f"{LL}/v2/chains") or []
    by_id = {str(p.get("defillamaId") or p.get("id")): p for p in prot}
    parent_sym = {}
    for p in prot:
        if p.get("parentProtocol") and p.get("symbol") and p["symbol"] != "-":
            parent_sym.setdefault(p["parentProtocol"], p["symbol"].upper())
    chain_sym = {str(c.get("name") or "").lower(): str(c.get("tokenSymbol") or "").upper() for c in chains if c.get("tokenSymbol")}
    tok_slugs = {}
    ov_rows = ov.get("protocols") or []
    for e in ov_rows:
        slug = e.get("slug") or e.get("module")
        if not slug or not (e.get("total30d") or e.get("total1y")):
            continue
        sym = None
        p = by_id.get(str(e.get("defillamaId") or e.get("id")))
        if p and p.get("symbol") and p["symbol"] != "-":
            sym = p["symbol"].upper()
        elif e.get("parentProtocol") and e["parentProtocol"] in parent_sym:
            sym = parent_sym[e["parentProtocol"]]
        elif str(e.get("protocolType") or e.get("category") or "").lower() in ("chain",):
            sym = chain_sym.get(str(e.get("name") or "").lower())
        if sym:
            tok_slugs.setdefault(sym, set()).add(slug)
    perp_for = {}
    for s in D:
        base, _m = alias.get(s, (s, 1))
        perp_for[base] = s
    want = {sym: sl for sym, sl in tok_slugs.items() if sym in perp_for}
    note("factors · data", json.dumps({"perps": len(D), "supply": len(supply), "llama_protocols": len(prot), "fees_rows": len(ov_rows),
                                       "ov_keys": sorted(ov_rows[0].keys())[:40] if ov_rows else [], "tokens_with_revenue": len(tok_slugs),
                                       "with_perp": len(want), "sample": {k: sorted(v)[:3] for k, v in list(want.items())[:8]},
                                       "minutes": round((dt.datetime.now() - t_start).seconds / 60, 1)}, ensure_ascii=False))
    jobs = [(sym, slug, dtp) for sym, sl in want.items() for slug in sl for dtp in ("dailyRevenue", "dailyFees", "dailyHoldersRevenue")]
    with ThreadPoolExecutor(8) as ex:
        series = list(ex.map(lambda j: (j, _llama_series(j[1], j[2])), jobs))
    rev, fees, hold = {}, {}, {}
    for (sym, _slug, dtp), ser in series:
        tgt = {"dailyRevenue": rev, "dailyFees": fees, "dailyHoldersRevenue": hold}[dtp].setdefault(sym, {})
        for d, v in ser.items():
            tgt[d] = tgt.get(d, 0.0) + v
    first = {s: int(K["t"][0]) for s, K in D.items()}
    v30 = {}
    for s, K in D.items():
        v = sma(K["bv"] * K["c"], 30)
        v30[s] = {int(t): float(x) for t, x in zip(K["t"], v) if not np.isnan(x)}
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    reb_day = 15 if "--mid" in sys.argv else 1      # robustness: rebalance on the 15th instead of the 1st
    months = [d for d in days if d >= t0 and dt.datetime.fromtimestamp(d / 1000, dt.timezone.utc).day == reb_day]
    return dict(D=D, cl=cl, days=days, months=months, perp_for=perp_for, want=want, rev=rev, fees=fees, hold=hold, supply=supply,
                alias=alias, v30=v30, first=first)


def factors():
    """19.10, round 13 — see _spot_data(). Which fundamentals predicted the next month (terciles, equal weight)."""
    X = _spot_data()
    D, cl, days, months, perp_for, want = X["D"], X["cl"], X["days"], X["months"], X["perp_for"], X["want"]
    rev, fees, hold, supply, alias, v30, first = X["rev"], X["fees"], X["hold"], X["supply"], X["alias"], X["v30"], X["first"]

    def ssum(ser, d, n):
        return sum(ser.get(d - k * DAY, 0.0) for k in range(n)) if ser else 0.0

    def has_data(ser, d, n):
        return bool(ser) and any((d - k * DAY) in ser for k in range(n))

    def ret(s, a, b):
        if a not in cl[s] or cl[s][a] <= 0:
            return None
        if b in cl[s]:
            return cl[s][b] / cl[s][a] - 1
        last = max((t for t in cl[s] if a <= t <= b), default=a)      # delisted inside the month: its last close
        return cl[s][last] / cl[s][a] - 1
    fac_rows = {}

    def add(name, d, d1, scored):
        """scored: [(score, sym)] for month start d → terciles → next-month returns."""
        sc = [(v, s) for v, s in scored if v is not None and not (isinstance(v, float) and np.isnan(v))]
        rs = {s: ret(s, d, d1) for _v, s in sc}
        sc = [(v, s) for v, s in sc if rs.get(s) is not None]
        if len(sc) < 9:
            return
        sc.sort()
        n3 = len(sc) // 3
        lo = float(np.mean([rs[s] for _v, s in sc[:n3]]))
        hi = float(np.mean([rs[s] for _v, s in sc[-n3:]]))
        allm = float(np.mean([rs[s] for _v, s in sc]))
        fac_rows.setdefault(name, []).append((d, hi - lo, hi - allm, len(sc)))
    for i, d in enumerate(months[:-1]):
        d1 = months[i + 1]
        # revenue universe
        R = []
        for sym, perp in perp_for.items():
            if sym not in want or d not in cl[perp] or d - first[perp] < 120 * DAY:
                continue
            base, mul = sym, alias.get(perp, (perp, 1))[1]
            ts = supply.get(base)
            r30, r90, r90p = ssum(rev.get(sym), d, 30), ssum(rev.get(sym), d, 90), ssum(rev.get(sym), d - 90 * DAY, 90)
            f30, h30 = ssum(fees.get(sym), d, 30), ssum(hold.get(sym), d, 30)
            if not has_data(rev.get(sym), d, 30):
                continue
            fdv = cl[perp][d] / mul * ts if ts else None
            R.append({"s": perp, "ry": r30 * 12 / fdv if fdv else None, "fy": f30 * 12 / fdv if fdv else None,
                      "hy": h30 * 12 / fdv if fdv else None, "hb": 1.0 if h30 > 0 else 0.0,
                      "rg": (r90 / r90p - 1) if r90p > 0 and r90 > 0 else None})
        for key, name in (("ry", "revenue yield"), ("fy", "fees yield"), ("hy", "holders yield"), ("rg", "revenue growth 90d")):
            add(name, d, d1, [(x[key], x["s"]) for x in R])
        # pays holders vs not: two groups
        g1 = [x["s"] for x in R if x["hb"] == 1.0]
        g0 = [x["s"] for x in R if x["hb"] == 0.0]
        r1 = [ret(s, d, d1) for s in g1]
        r0 = [ret(s, d, d1) for s in g0]
        r1 = [x for x in r1 if x is not None]
        r0 = [x for x in r0 if x is not None]
        if len(r1) >= 3 and len(r0) >= 3:
            fac_rows.setdefault("pays holders vs not", []).append((d, float(np.mean(r1)) - float(np.mean(r0)), 0.0, len(r1) + len(r0)))
        # revenue tokens vs the other alts of the top 100 (by volume)
        top = sorted((v30[s][d], s) for s in D if d in v30[s] and s not in ("BTC", "ETH") and d - first[s] >= 365 * DAY)[-100:]
        uni = [s for _v, s in top]
        rev_set = {x["s"] for x in R}
        a1 = [ret(s, d, d1) for s in uni if s in rev_set]
        a0 = [ret(s, d, d1) for s in uni if s not in rev_set]
        a1 = [x for x in a1 if x is not None]
        a0 = [x for x in a0 if x is not None]
        if len(a1) >= 3 and len(a0) >= 3:
            fac_rows.setdefault("revenue tokens vs other alts", []).append((d, float(np.mean(a1)) - float(np.mean(a0)), 0.0, len(a1) + len(a0)))
        # price universe
        P = []
        for s in uni:
            def back(n):
                t = d - n * DAY
                return cl[s].get(t), cl["BTC"].get(t)
            c0, b0 = cl[s][d], cl["BTC"][d]
            c12, b12 = back(365)
            c1, b1 = back(30)
            c3, b3 = back(91)
            hi = max((cl[s][t] for t in cl[s] if d - 365 * DAY <= t <= d), default=None)
            v180 = np.mean([v30[s][t] for t in v30[s] if d - 180 * DAY <= t <= d]) if v30[s] else None
            P.append({"s": s, "m12": (c1 / c12) / (b1 / b12) - 1 if c12 and c1 and b12 and b1 else None,
                      "m3": (c0 / c3) / (b0 / b3) - 1 if c3 and b3 else None, "dd": c0 / hi - 1 if hi else None,
                      "vt": v30[s].get(d) / v180 if v180 else None, "age": (d - first[s]) / DAY})
        for key, name in (("m12", "strength vs BTC 12 m (skip 1 m)"), ("m3", "strength vs BTC 3 m"), ("dd", "distance from 1-year high"),
                          ("vt", "volume trend 30 d / 180 d"), ("age", "age")):
            add(name, d, d1, [(x[key], x["s"]) for x in P])
    out = {}
    for name, rows in fac_rows.items():
        sp = np.array([r[1] for r in rows])
        yr = {}
        for d, v, _w, _n in rows:
            yr.setdefault(dt.datetime.fromtimestamp(d / 1000, dt.timezone.utc).strftime("%Y"), []).append(v)
        out[name] = [round(float(sp.mean()) * 100, 2), round(float(sp.mean() / (sp.std() + 1e-12) * np.sqrt(len(sp))), 2),
                     round(float((sp > 0).mean()) * 100), round(float(np.mean([r[2] for r in rows])) * 100, 2), len(rows),
                     round(float(np.mean([r[3] for r in rows]))), [round(float(np.mean(yr[y])) * 100, 1) if y in yr else None for y in YEARS]]
    note("factors · [top−bottom %/month, t, months+ %, top−all %, months, coins, 2022…2026]", json.dumps(out, ensure_ascii=False))


def _mret(cl, s, a, b):
    if a not in cl[s] or cl[s][a] <= 0:
        return None
    if b in cl[s]:
        return cl[s][b] / cl[s][a] - 1
    last = max((t for t in cl[s] if a <= t <= b), default=a)          # delisted inside the month: its last close
    return cl[s][last] / cl[s][a] - 1


def quality():
    """19.10, round 14 — round 13 found, on every Binance perp (delisted included), 2022–2026, monthly:
      tokens of projects WITH REVENUE (DefiLlama) beat the other alts of the top 100 by +2.0 % a month (t 3.1, 69 % of months,
      plus 2023–2026); OLDER coins beat younger (+3.0 %, t 1.9, plus every year); a RISING volume trend lost (−2.9 %, t −2.0) and
      the 3-month winners vs BTC lost (−2.4 %, minus every year); revenue growth slightly positive; «cheap» revenue yield,
      buybacks and the distance from the high — nothing.
    Here the monthly portfolios built from that, against BTC and against the model's current basket (top 10 by 180-day volume):
      Q10 = top 10 of the top-100 alts by the composite (age ↑, 3-month strength ↓, volume trend ↓, revenue growth ↑, + revenue);
      Q10 rev = the same among revenue tokens only; REV = all revenue tokens of the top 100; OLD10 = the 10 oldest; VOL10; ALL; BTC.
    The signs come from round 13 on the same data — in-sample; halves are shown. Cost 0.2 % of the replaced part a month.
    Rows: [×, max dd % (monthly), avg month %, months beating BTC %, 2022…2026 %, 1st half ×, 2nd half ×]."""
    X = _spot_data()
    D, cl, months, perp_for, want = X["D"], X["cl"], X["months"], X["perp_for"], X["want"]
    rev, v30, first = X["rev"], X["v30"], X["first"]
    sym_of = {perp: sym for sym, perp in perp_for.items()}

    def ssum(ser, d, n):
        return sum(ser.get(d - k * DAY, 0.0) for k in range(n)) if ser else 0.0
    v180 = {}
    for s, K in D.items():
        v = sma(K["bv"] * K["c"], 180)
        v180[s] = {int(t): float(x) for t, x in zip(K["t"], v) if not np.isnan(x)}
    names = ("Q10", "Q10 rev", "REV", "OLD10", "VOL10", "ALL", "BTC")
    books = {k: {"V": 1.0, "hold": set(), "curve": [], "rets": []} for k in names + tuple(f"{k} +regime" for k in ("Q10", "VOL10", "BTC"))
             + ("BTC80+Q10 20 +regime", "BTC80+VOL10 20 +regime")}
    bt = sorted(cl["BTC"])
    bc_ = np.array([cl["BTC"][t] for t in bt])
    s200_ = sma(bc_, 200)
    bull_at = {t: bool(not np.isnan(s200_[k]) and bc_[k] > s200_[k] and k >= 60 and bc_[k] > bc_[k - 60]) for k, t in enumerate(bt)}
    for i, d in enumerate(months[:-1]):
        d1 = months[i + 1]
        top = sorted((v30[s][d], s) for s in D if d in v30[s] and s not in ("BTC", "ETH") and d - first[s] >= 365 * DAY)[-100:]
        uni = [s for _v, s in top]
        F = {}
        for s in uni:
            c0, b0 = cl[s][d], cl["BTC"][d]
            c3, b3 = cl[s].get(d - 91 * DAY), cl["BTC"].get(d - 91 * DAY)
            vv = [v30[s][t] for t in v30[s] if d - 180 * DAY <= t <= d]
            sym = sym_of.get(s)
            r30 = ssum(rev.get(sym), d, 30) if sym in want else 0.0
            r90, r90p = (ssum(rev.get(sym), d, 90), ssum(rev.get(sym), d - 90 * DAY, 90)) if sym in want else (0.0, 0.0)
            F[s] = {"age": (d - first[s]) / DAY, "m3": (c0 / c3) / (b0 / b3) - 1 if c3 and b3 else 0.0,
                    "vt": v30[s][d] / np.mean(vv) if vv else 1.0, "rev": 1.0 if r30 > 0 else 0.0,
                    "rg": (r90 / r90p - 1) if r90p > 0 and r90 > 0 else None}

        def prank(key, rev_=False):
            xs = sorted((F[s][key], s) for s in uni if F[s][key] is not None)
            n = max(1, len(xs) - 1)
            out = {s: k / n for k, (_v, s) in enumerate(xs)}
            return {s: out.get(s, 0.5) for s in uni}
        ra, rm, rv, rg = prank("age"), prank("m3"), prank("vt"), prank("rg")
        comp = {s: (ra[s] + (1 - rm[s]) + (1 - rv[s]) + rg[s]) / 4 + 0.5 * F[s]["rev"] for s in uni}
        picks = {
            "Q10": sorted(uni, key=lambda s: -comp[s])[:10],
            "Q10 rev": sorted([s for s in uni if F[s]["rev"]], key=lambda s: -comp[s])[:10],
            "REV": [s for s in uni if F[s]["rev"]],
            "OLD10": sorted(uni, key=lambda s: -F[s]["age"])[:10],
            "VOL10": [s for _v, s in sorted(((v180[s].get(d, 0), s) for s in uni), reverse=True)[:10]],
            "ALL": uni, "BTC": ["BTC"]}
        bull = bull_at.get(d, False)
        for k in ("Q10", "VOL10", "BTC"):
            picks[f"{k} +regime"] = picks[k] if bull else []
        mixr = {}
        for k, ps in picks.items():
            if not ps:                               # not «рост»: dollars at 4 % a year
                bk = books[k]
                r = 0.04 / 12
                bk["hold"] = set()
                bk["V"] *= 1 + r
                bk["curve"].append((d1, bk["V"]))
                bk["rets"].append(r)
                mixr[k] = r
                continue
            bk = books[k]
            rs = [_mret(cl, s, d, d1) for s in ps]
            rs = [x for x in rs if x is not None]
            r = float(np.mean(rs)) if rs else 0.0
            new = set(ps)
            if bk["hold"] and k != "BTC":
                r -= 0.002 * (1 - len(new & bk["hold"]) / max(1, len(new)))
            bk["hold"] = new
            bk["V"] *= 1 + r
            bk["curve"].append((d1, bk["V"]))
            bk["rets"].append(r)
            mixr[k] = r
        for mk, ak in (("BTC80+Q10 20 +regime", "Q10 +regime"), ("BTC80+VOL10 20 +regime", "VOL10 +regime")):
            r = 0.8 * mixr["BTC +regime"] + 0.2 * mixr[ak]
            bk = books[mk]
            bk["V"] *= 1 + r
            bk["curve"].append((d1, bk["V"]))
            bk["rets"].append(r)
    btc = np.array(books["BTC"]["rets"])
    half = len(btc) // 2
    out = {}
    for k, bk in books.items():
        eq = np.array([v for _, v in bk["curve"]])
        rr = np.array(bk["rets"])
        yrs, pv = [], 1.0
        for y in YEARS:
            pts = [v for t, v in bk["curve"] if dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y") == y]
            if pts:
                yrs.append(round((pts[-1] / pv - 1) * 100))
                pv = pts[-1]
        out[k] = [round(float(eq[-1]), 2), round(float((eq / np.maximum.accumulate(np.r_[1.0, eq])[1:] - 1).min()) * 100),
                  round(float(rr.mean()) * 100, 2), round(float((rr > btc).mean()) * 100), yrs,
                  round(float(np.prod(1 + rr[:half])), 2), round(float(np.prod(1 + rr[half:])), 2)]
    note("quality · monthly alt baskets vs BTC [x, dd %, month %, months > BTC %, 2022…2026, halves]", json.dumps(out, ensure_ascii=False))

def _funding_month(sym, ym):
    """Sum of the funding rates of one USDT-M perp over one month (Binance monthly archive; missing = 0)."""
    st, z = http(f"{UM}/monthly/fundingRate/{sym}USDT/{sym}USDT-fundingRate-{ym}.zip", binary=True)
    if st != 200 or not z:
        return None
    tot = 0.0
    for r in read_zip_csv(z):
        try:
            tot += float(r[-1])
        except (ValueError, IndexError):
            continue
    return tot


def ls_neutral(iv=None, ret_out=None):
    """19.10, round 15 — owner: «продолжай выполнять тесты, искать лучшие возможности … усовершенствовать систему».
    Round 13–14 found which alts do well and badly a month ahead (revenue, age ↑; 3-month spike and swelling volume ↓) and that
    alts as a class lost to BTC. Perps can SHORT, so: is there a market-neutral book that earns in any market?
      LS10      long the top 10 by the quality composite, short the bottom 10 (top-100 alts by 30-day $ volume, ≥ 1 year);
      LS33      long the top third, short the bottom third;
      BTC/B10   long BTC, short the bottom 10;          BTC/ALL  long BTC, short all top-100 alts (equal weight);
      BTC/YNG   long BTC, short the 20 most traded young coins (60–365 days since listing);
      Q10/BTC   long the top 10, short BTC.
    Equal weights fixed at the month start (no intra-month rebalance) → DAILY marks; half the gross long, half short.
    Costs 0.2 % of the replaced notional (in + out, taker + slippage); REAL funding from the Binance monthly archives
    (longs pay the month's sum of rates, shorts receive it). Delisted coins: their last close. In-sample signs — halves shown.
    Rows: [× gross 1, max dd % daily, month %, t, months + %, 2022…2026 %, halves ×, funding %/month, cost %/month,
    gross at dd 25 %, × there, month % there]."""
    X = _spot_data()
    D, cl, days, months, perp_for, want = X["D"], X["cl"], X["days"], X["months"], X["perp_for"], X["want"]
    rev, v30, first = X["rev"], X["v30"], X["first"]
    sym_of = {perp: sym for sym, perp in perp_for.items()}

    def ssum(ser, d, n):
        return sum(ser.get(d - k * DAY, 0.0) for k in range(n)) if ser else 0.0
    plan = []
    for i, d in enumerate(months[:-1]):
        d1 = months[i + 1]
        top = sorted((v30[s][d], s) for s in D if d in v30[s] and s not in ("BTC", "ETH") and d - first[s] >= 365 * DAY)[-100:]
        uni = [s for _v, s in top]
        young = [s for _v, s in sorted((v30[s][d], s) for s in D if d in v30[s] and s not in ("BTC", "ETH")
                                       and 60 * DAY <= d - first[s] < 365 * DAY)[-20:]]
        F = {}
        for s in uni:
            c0, b0 = cl[s][d], cl["BTC"][d]
            c3, b3 = cl[s].get(d - 91 * DAY), cl["BTC"].get(d - 91 * DAY)
            vv = [v30[s][t] for t in v30[s] if d - 180 * DAY <= t <= d]
            sym = sym_of.get(s)
            r30 = ssum(rev.get(sym), d, 30) if sym in want else 0.0
            r90, r90p = (ssum(rev.get(sym), d, 90), ssum(rev.get(sym), d - 90 * DAY, 90)) if sym in want else (0.0, 0.0)
            F[s] = {"age": (d - first[s]) / DAY, "m3": (c0 / c3) / (b0 / b3) - 1 if c3 and b3 else 0.0,
                    "vt": v30[s][d] / np.mean(vv) if vv else 1.0, "rev": 1.0 if r30 > 0 else 0.0,
                    "rg": (r90 / r90p - 1) if r90p > 0 and r90 > 0 else None}

        def prank(key):
            xs = sorted((F[s][key], s) for s in uni if F[s][key] is not None)
            n = max(1, len(xs) - 1)
            o = {s: k / n for k, (_v, s) in enumerate(xs)}
            return {s: o.get(s, 0.5) for s in uni}
        ra, rm, rv, rg = prank("age"), prank("m3"), prank("vt"), prank("rg")
        comp = {s: (ra[s] + (1 - rm[s]) + (1 - rv[s]) + rg[s]) / 4 + 0.5 * F[s]["rev"] for s in uni}
        order = sorted(uni, key=lambda s: -comp[s])
        n3 = len(order) // 3
        plan.append((d, d1, {"LS10": (order[:10], order[-10:]), "LS33": (order[:n3], order[-n3:]),
                             "BTC/B10": (["BTC"], order[-10:]), "BTC/ALL": (["BTC"], uni), "BTC/YNG": (["BTC"], young),
                             "Q10/BTC": (order[:10], ["BTC"])}))
    # real funding for every (coin, month) held
    need = sorted({(s, dt.datetime.fromtimestamp(d / 1000, dt.timezone.utc).strftime("%Y-%m"))
                   for d, _d1, bk in plan for lg, sh in bk.values() for s in lg + sh})
    with ThreadPoolExecutor(48) as ex:
        FR = dict(zip(need, ex.map(lambda k: _funding_month(*k), need)))
    have_f = sum(1 for v in FR.values() if v is not None)

    IV = iv if iv is not None else "--iv" in sys.argv   # inverse-volatility weights (30-day daily σ) instead of equal — the standard risk fix

    def vol30(s, d):
        xs = [cl[s].get(d - k * DAY) for k in range(31)]
        xs = [x for x in xs if x]
        if len(xs) < 15:
            return None
        r = np.diff(np.log(np.array(xs[::-1])))
        return float(r.std()) or None

    def leg(ps, d, d1):
        ts = [t for t in days if d < t <= d1]
        out, w = [], []
        for s in ps:
            c0 = cl[s].get(d)
            if not c0:
                continue
            last, path = c0, []
            for t in ts:
                c = cl[s].get(t)
                if c:
                    last = c
                path.append(last / c0)
            out.append(path)
            sv = vol30(s, d) if IV else None
            w.append(1.0 / sv if sv else None)
        if not out:
            return np.ones(len(ts))
        if IV and len(out) > 1:
            med = float(np.median([x for x in w if x])) if any(w) else 1.0
            w = np.array([x if x else med for x in w])
            w = np.minimum(w / w.sum(), 2.5 / len(w))     # no coin above 2.5× an equal share
            w = w / w.sum()
            return (np.array(out) * w[:, None]).sum(axis=0)
        return np.mean(np.array(out), axis=0)
    names = list(plan[0][2])
    comp_ = {k: [] for k in names}        # per month: (Lpath, Spath, turnover L, turnover S, funding L, funding S, d)
    prev = {k: (set(), set()) for k in names}
    for d, d1, bk in plan:
        ym = dt.datetime.fromtimestamp(d / 1000, dt.timezone.utc).strftime("%Y-%m")
        for k in names:
            lg, sh = bk[k]
            pl, ps_ = prev[k]
            tl = 1 - len(set(lg) & pl) / max(1, len(lg))
            tsh = 1 - len(set(sh) & ps_) / max(1, len(sh))
            fl = float(np.mean([FR.get((s, ym)) or 0.0 for s in lg])) if lg else 0.0
            fs = float(np.mean([FR.get((s, ym)) or 0.0 for s in sh])) if sh else 0.0
            comp_[k].append((leg(lg, d, d1), leg(sh, d, d1), tl, tsh, fl, fs, d1))
            prev[k] = (set(lg), set(sh))

    def sim(k, g):
        V, curve, rets, fsum, csum = 1.0, [], [], 0.0, 0.0
        for Lp, Sp, tl, tsh, fl, fs, d1 in comp_[k]:
            cost = 0.002 * g * 0.5 * (tl + tsh)
            fund = g * 0.5 * (-fl + fs)
            path = 1 + g * 0.5 * ((Lp - 1) - (Sp - 1)) - cost
            path = np.maximum(path, 0.0)
            V0 = V
            curve += list(V0 * path)
            V = V0 * (path[-1] + fund)
            curve[-1] = V
            rets.append((d1, V / V0 - 1))
            fsum += fund
            csum += cost
        eq = np.array(curve)
        dd = float((eq / np.maximum.accumulate(np.r_[1.0, eq])[1:] - 1).min()) if len(eq) else 0.0
        return V, dd, rets, fsum / max(1, len(rets)), csum / max(1, len(rets))
    if ret_out is not None:                    # blend_study: the live book (LS10, gross 0.75) as monthly returns, no note
        ret_out.update({g: sim("LS10", g)[2] for g in (0.75, 1.0)})
        return
    out = {}
    for k in names:
        V, dd, rets, fm, cm = sim(k, 1.0)
        rr = np.array([r for _d, r in rets])
        yrs = {}
        for d1, r in rets:
            y = dt.datetime.fromtimestamp((d1 - DAY) / 1000, dt.timezone.utc).strftime("%Y")
            yrs[y] = yrs.get(y, 1.0) * (1 + r)
        half = len(rr) // 2
        g25, best = None, None
        for g in (0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
            Vg, ddg, rg_, _f, _c = sim(k, g)
            if ddg >= -0.25:
                g25, best = g, (Vg, float(np.mean([r for _d, r in rg_])))
        out[k] = [round(V, 2), round(dd * 100), round(float(rr.mean()) * 100, 2),
                  round(float(rr.mean() / (rr.std() + 1e-12) * np.sqrt(len(rr))), 2), round(float((rr > 0).mean()) * 100),
                  [round((yrs[y] - 1) * 100) if y in yrs else None for y in YEARS],
                  round(float(np.prod(1 + rr[:half])), 2), round(float(np.prod(1 + rr[half:])), 2),
                  round(fm * 100, 2), round(cm * 100, 2), g25, round(best[0], 2) if best else None,
                  round(best[1] * 100, 2) if best else None]
    note(("ls neutral IV" if IV else "ls neutral") + (" mid-month" if "--mid" in sys.argv else "") + " · [x g1, dd %, month %, t, months+ %, 2022…2026 %, halves, funding %/m, cost %/m, g@dd25, x, month %]",
         json.dumps({"funding files": f"{have_f}/{len(need)}", "months": len(plan), **out}, ensure_ascii=False))


def ls_robust():
    """22.10, backlog item 6 — «Нейтральная корзина»: how much of the result is the rebalance DAY, and 15/20 coins instead of 10.
    Same book as ls_neutral() LS10 with --iv (quality composite, top-100 alts by 30-day $ volume, ≥ 1 year, inverse-vol weights
    capped at 2.5× an equal share, 0.2 % of the replaced notional, real Binance monthly funding — a period that spans two calendar
    months takes each month's sum in proportion to its days), now for every rebalance day 1 / 8 / 15 / 22 and N = 10 / 15 / 20.
    Staggered books: the capital split into 2 (days 1 + 15) or 4 (1 / 8 / 15 / 22) independent sub-accounts, each its own monthly
    book — the standard cure for «timing luck». Every curve is cut to the SAME window (the latest first day … the earliest last
    day) and reported at gross 0.75 (the live setting). 12 single + 6 staggered = 18 variants; the composite's signs were chosen on
    this history (round 13), so this is a robustness check of a live desk, not a new discovery."""
    X = _spot_data()
    D, cl, days, perp_for, want = X["D"], X["cl"], X["days"], X["perp_for"], X["want"]
    rev, v30, first = X["rev"], X["v30"], X["first"]
    sym_of = {perp: sym for sym, perp in perp_for.items()}
    t0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    G = 0.75

    def ym(d):
        return dt.datetime.fromtimestamp(d / 1000, dt.timezone.utc).strftime("%Y-%m")

    def ssum(ser, d, n):
        return sum(ser.get(d - k * DAY, 0.0) for k in range(n)) if ser else 0.0

    rank_cache = {}

    def ranking(d):
        if d in rank_cache:
            return rank_cache[d]
        top = sorted((v30[s][d], s) for s in D if d in v30[s] and s not in ("BTC", "ETH") and d - first[s] >= 365 * DAY)[-100:]
        uni = [s for _v, s in top]
        F = {}
        for s in uni:
            c0, b0 = cl[s][d], cl["BTC"][d]
            c3, b3 = cl[s].get(d - 91 * DAY), cl["BTC"].get(d - 91 * DAY)
            vv = [v30[s][t] for t in v30[s] if d - 180 * DAY <= t <= d]
            sym = sym_of.get(s)
            r30 = ssum(rev.get(sym), d, 30) if sym in want else 0.0
            r90, r90p = (ssum(rev.get(sym), d, 90), ssum(rev.get(sym), d - 90 * DAY, 90)) if sym in want else (0.0, 0.0)
            F[s] = {"age": (d - first[s]) / DAY, "m3": (c0 / c3) / (b0 / b3) - 1 if c3 and b3 else 0.0,
                    "vt": v30[s][d] / np.mean(vv) if vv else 1.0, "rev": 1.0 if r30 > 0 else 0.0,
                    "rg": (r90 / r90p - 1) if r90p > 0 and r90 > 0 else None}

        def prank(key):
            xs = sorted((F[s][key], s) for s in uni if F[s][key] is not None)
            n = max(1, len(xs) - 1)
            o = {s: k / n for k, (_v, s) in enumerate(xs)}
            return {s: o.get(s, 0.5) for s in uni}
        ra, rm, rv, rg = prank("age"), prank("m3"), prank("vt"), prank("rg")
        comp = {s: (ra[s] + (1 - rm[s]) + (1 - rv[s]) + rg[s]) / 4 + 0.5 * F[s]["rev"] for s in uni}
        rank_cache[d] = sorted(uni, key=lambda s: -comp[s])
        return rank_cache[d]

    REB = (1, 8, 15, 22)
    NS = (10, 15, 20)
    starts = {r: [d for d in days if d >= t0 and dt.datetime.fromtimestamp(d / 1000, dt.timezone.utc).day == r] for r in REB}
    books = {}                       # (r, n) -> [(d, d1, long, short)]
    for r in REB:
        ms = starts[r]
        for n in NS:
            books[(r, n)] = [(d, ms[i + 1], ranking(d)[:n], ranking(d)[-n:]) for i, d in enumerate(ms[:-1])]
    need = set()
    for bk in books.values():
        for d, d1, lg, sh in bk:
            for s in lg + sh:
                need.add((s, ym(d)))
                need.add((s, ym(d1 - DAY)))
    need = sorted(need)
    with ThreadPoolExecutor(48) as ex:
        FR = dict(zip(need, ex.map(lambda k: _funding_month(*k), need)))
    have_f = sum(1 for v in FR.values() if v is not None)

    def vol30(s, d):
        xs = [cl[s].get(d - k * DAY) for k in range(31)]
        xs = [x for x in xs if x]
        if len(xs) < 15:
            return None
        rr = np.diff(np.log(np.array(xs[::-1])))
        return float(rr.std()) or None

    def leg(ps, d, ts):
        out, w = [], []
        for s in ps:
            c0 = cl[s].get(d)
            if not c0:
                continue
            last, path = c0, []
            for t in ts:
                c = cl[s].get(t)
                if c:
                    last = c
                path.append(last / c0)
            out.append(path)
            sv = vol30(s, d)
            w.append(1.0 / sv if sv else None)
        if not out:
            return np.ones(len(ts))
        if len(out) > 1:
            med = float(np.median([x for x in w if x])) if any(w) else 1.0
            w = np.array([x if x else med for x in w])
            w = np.minimum(w / w.sum(), 2.5 / len(w))
            w = w / w.sum()
            return (np.array(out) * w[:, None]).sum(axis=0)
        return np.array(out[0])

    def fund_period(ps, d, d1):
        """mean over coins of the funding sum for the period: each calendar month's sum × (days of it in the period ÷ its days)."""
        if not ps:
            return 0.0
        tot = []
        for s in ps:
            f, t = 0.0, d
            while t < d1:
                m = ym(t)
                dd_ = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc)
                nxt = (dd_.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
                mdays = (nxt - dd_.replace(day=1)).days
                end = min(d1, int(nxt.timestamp() * 1000))
                f += (FR.get((s, m)) or 0.0) * ((end - t) / DAY) / mdays
                t = end
            tot.append(f)
        return float(np.mean(tot))

    def curve(r, n):
        """daily equity {t: V} of one sub-account, gross G."""
        V, out, pl, ps_ = 1.0, {}, set(), set()
        for d, d1, lg, sh in books[(r, n)]:
            ts = [t for t in days if d < t <= d1]
            if not ts:
                continue
            tl = 1 - len(set(lg) & pl) / max(1, len(lg))
            tsh = 1 - len(set(sh) & ps_) / max(1, len(sh))
            cost = 0.002 * G * 0.5 * (tl + tsh)
            fund = G * 0.5 * (-fund_period(lg, d, d1) + fund_period(sh, d, d1))
            path = np.maximum(1 + G * 0.5 * ((leg(lg, d, ts) - 1) - (leg(sh, d, ts) - 1)) - cost, 0.0)
            V0 = V
            for t, p in zip(ts, path):
                out[t] = V0 * p
            V = V0 * (path[-1] + fund)
            out[ts[-1]] = V
            pl, ps_ = set(lg), set(sh)
        return out
    C = {(r, n): curve(r, n) for r in REB for n in NS}
    lo = max(min(c) for c in C.values())
    hi = min(max(c) for c in C.values())
    grid = [t for t in days if lo <= t <= hi]

    def on_grid(c):
        last, xs = None, []
        for t in grid:
            last = c.get(t, last)
            xs.append(last)
        x = np.array(xs, dtype=float)
        return x / x[0]
    S = {f"d{r} N{n}": on_grid(C[(r, n)]) for r in REB for n in NS}
    for n in NS:
        S[f"d1+15 N{n}"] = (S[f"d1 N{n}"] + S[f"d15 N{n}"]) / 2
        S[f"4part N{n}"] = sum(S[f"d{r} N{n}"] for r in REB) / 4

    def stats(eq):
        dd = float((eq / np.maximum.accumulate(eq) - 1).min())
        mon, prev_m, base = [], None, eq[0]
        for t, v in zip(grid, eq):
            m = ym(t)
            if prev_m is not None and m != prev_m:
                mon.append(lastv / base - 1)
                base = lastv
            prev_m, lastv = m, v
        mon.append(eq[-1] / base - 1)
        mr = np.array(mon)
        yrs, pv = {}, eq[0]
        for t, v in zip(grid, eq):
            yrs.setdefault(ym(t)[:4], [pv, v])[1] = v
            pv = v
        half = len(eq) // 2
        return [round(float(eq[-1]), 2), round(dd * 100), round(float(mr.mean()) * 100, 2),
                round(float(mr.mean() / (mr.std() + 1e-12) * np.sqrt(len(mr))), 2), round(float((mr > 0).mean()) * 100),
                [round((b / a - 1) * 100) for _y, (a, b) in sorted(yrs.items())],
                round(float(eq[half] / eq[0]), 2), round(float(eq[-1] / eq[half]), 2)]
    out = {k: stats(v) for k, v in S.items()}
    for n in NS:
        xs = [out[f"d{r} N{n}"][0] for r in REB]
        out[f"spread N{n}"] = [min(xs), max(xs), round(float(np.std(xs)), 2)]
    note("ls robust · meta", json.dumps({"funding files": f"{have_f}/{len(need)}", "variants": 18, "gross": G,
                                          "window": [ym(lo), ym(hi)], "days": len(grid),
                                          "cols": "x, dd %, month %, t(months), months+ %, years %, half1 x, half2 x"}))
    note("ls robust · single days", json.dumps({k: v for k, v in out.items() if k.startswith("d") and "+" not in k}, ensure_ascii=False))
    note("ls robust · staggered + spread", json.dumps({k: v for k, v in out.items() if not (k.startswith("d") and "+" not in k)},
                                                      ensure_ascii=False))


if __name__ == "__main__":
    (ls_robust() if "--lsrob" in sys.argv else ls_neutral() if "--ls" in sys.argv else quality() if "--quality" in sys.argv else factors() if "--factors" in sys.argv else spot_probe() if "--spotprobe" in sys.argv else altport2() if "--altport2" in sys.argv else own_bear() if "--bear" in sys.argv else pool_probe() if "--pool" in sys.argv else wide2() if "--wide2" in sys.argv else wide() if "--wide" in sys.argv else flow3() if "--flow3" in sys.argv else flow2() if "--flow2" in sys.argv else flow() if "--flow" in sys.argv else altport() if "--altport" in sys.argv else tune() if "--tune" in sys.argv else export() if "--export" in sys.argv else combo() if "--combo" in sys.argv else sizing() if "--sizing" in sys.argv else spot() if "--spot" in sys.argv else main())
