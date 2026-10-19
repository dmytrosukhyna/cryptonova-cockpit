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
        seen[t] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
    ts = sorted(seen)
    a = np.array([seen[t] for t in ts])
    return {"t": np.array(ts, dtype=np.int64), "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3]}


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


def simulate(K, fam, gate, ex, sym):
    """One position per coin at a time. → list of (open ms, close ms, side, net %, days held, sym)."""
    L, S = signals(K, fam, gate)
    t, o, h, l, c, A = K["t"], K["o"], K["h"], K["l"], K["c"], K["atr"]
    n, out, i = len(t), [], 0
    hold_max = 7 if fam == "M" else (10 if fam == "P" else 14)
    while i < n - 2:
        if not (L[i] or S[i]) or (L[i] and S[i]) or np.isnan(A[i]):
            i += 1
            continue
        sg = 1 if L[i] else -1
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
            break                                             # still open at the end of the data
        days = j - i
        net = sg * (px / e - 1) * 100 - COST - FUND_D * days
        out.append((int(t[i + 1]), int(t[j]), sg, float(net), days, sym, float(sd / e * 100)))
        i = j                                                 # a new signal can come at this close
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


def account(trades, share=0.10, max_open=10, risk=None):
    """Compounding demo account: `share` of equity per trade (notional, 1×), at most `max_open` at once. With `risk` (% of
    equity lost at the stop): notional = risk ÷ stop distance, capped at 25 % of equity."""
    ev = sorted([(o, 0, k) for k, (o, *_r) in enumerate(trades)] + [(cl, 1, k) for k, (_o, cl, *_r) in enumerate(trades)])
    E, peak, dd, size, opened, curve = 1.0, 1.0, 0.0, {}, 0, []
    for tt, kind, k in ev:
        if kind == 0:
            if opened < max_open:
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


if __name__ == "__main__":
    combo() if "--combo" in sys.argv else main()
