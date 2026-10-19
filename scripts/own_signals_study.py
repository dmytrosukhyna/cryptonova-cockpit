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
import sys
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


def flow_cands(D, order, t0):
    """EVERY long signal day of P and C (not one trade after another): the account decides which to take, like the live desk,
    which sees the same coin signal again the next day when it skipped it. Features are known at the signal day's close."""
    out = []
    for s, K in D.items():
        for fam, ex in (("P", "-"), ("B20", "chand")):
            L, S = signals(K, fam, "regime")
            for i in np.where(L & ~S)[0]:
                i = int(i)
                if i >= len(K["t"]) - 2 or np.isnan(K["atr"][i]):
                    continue
                r = _trade(K, fam, ex, i, 1, s)
                if r is None or r[0][0] < t0:
                    continue
                tr, j = r
                f = lambda k: float(K[k][i]) if not np.isnan(K[k][i]) else 0.0  # noqa: E731
                out.append({"o": tr[0], "c": tr[1], "net": tr[3], "stop": tr[6], "sym": s, "fam": "P" if fam == "P" else "C",
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


def flow_account(C, D, days, cap=None, score="order", risk=0.5, budget=None, heat=None, max_open=20, gross=2.0, seed=None,
                 fam_cap=None, mult=1.0, min_surge=None, surge_key="surge", fam_only=None):
    """Daily mark-to-market account. Each day: today's entries (signals of yesterday's close) → skip a coin+rule already open →
    rank by `score` → take up to `cap` (None = all) within max_open, the gross notional cap (× equity) and the open-risk cap
    `heat` (% of equity); size = risk % of the (realised) equity lost at the stop, notional ≤ 25 %. With `budget` the day's
    new risk is split equally among the trades taken (each ≤ risk). Then today's exits; equity = realised + open at the close."""
    risk, budget, heat = risk * mult, (budget * mult if budget else None), (heat * mult if heat else None)
    rng = np.random.default_rng(seed) if score == "random" else None
    by_o, by_c = {}, {}
    for k, x in enumerate(C):
        by_o.setdefault(x["o"], []).append(k)
    idx = {s: {int(t): q for q, t in enumerate(K["t"])} for s, K in D.items()}
    E, opn, keys, curve, taken, skipped_good = 1.0, {}, set(), [], [], []
    n_c, n_t, max_o = 0, 0, 0
    for d in days:
        cand = [k for k in by_o.get(d, []) if (C[k]["sym"], C[k]["fam"]) not in keys
                and (min_surge is None or C[k][surge_key] >= min_surge) and (fam_only is None or C[k]["fam"] == fam_only)]
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
                mtm += size * (D[C[k]["sym"]]["c"][q] / e - 1)
        curve.append((d, E + mtm))
        max_o = max(max_o, len(opn))
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
            "n": n_t}


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

if __name__ == "__main__":
    (flow3() if "--flow3" in sys.argv else flow2() if "--flow2" in sys.argv else flow() if "--flow" in sys.argv else altport() if "--altport" in sys.argv else tune() if "--tune" in sys.argv else export() if "--export" in sys.argv else combo() if "--combo" in sys.argv else sizing() if "--sizing" in sys.argv else spot() if "--spot" in sys.argv else main())
