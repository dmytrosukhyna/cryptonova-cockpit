"""Автолаборатория — the engine that checks rules by itself every night (19.10.2026, owner: «движок, который будет анализировать
24 на 7 без остановки, исправлять ошибки … сам догадывался, искал, проверял … варианты перевёрнутые … золото, металлы, фонда»).

What it does each night (.github/workflows/autolab.yml → web/autolab.json, shown in the panel's «Лаборатория»):
  1. DATA — daily candles of crypto (Binance spot), metals (gold, silver, platinum, palladium, copper, aluminium), stock indices
     (S&P 500, Nasdaq 100, Dow, Russell 2000, DAX, Nikkei, Euro Stoxx 50), oil and US 10-year Treasuries (Yahoo, Stooq fallback).
  2. RULES — 5 families × their parameters × 3 directions (only long / long and short / INVERTED) on every market:
     trend above the N-day average · Donchian breakout · time-series momentum · RSI(2) pullback · buy and hold (the yardstick).
     Every position is sized to 15 % yearly volatility (so markets are comparable); costs per change of position.
  3. HONEST TEST (walk-forward) — every quarter the engine picks the best parameter on the past 5 years only and trades it the next
     quarter; only those «next quarters» are counted. A rule PASSES if out of sample: Sharpe ≥ 0.4, ≥ 60 % of years in plus,
     worst drawdown ≥ −35 %, the last 3 years in plus. RELIABLE if its Sharpe also beats what the best of that many random rules
     would show by luck (√(2·ln N) ÷ √years).
  4. PORTFOLIOS — set in advance, nothing fitted: «Мультирынок · тренд» (each market: the average of 4 momentum signals, long or
     short; equal risk), its long-only version, and with BTC/ETH added.
  5. LIVE CHECK — every demo desk of the panel against what its history promised (web/paper.json): too early / on track /
     below / drawdown deeper than history → «тревога».
  6. CHANGES — what passed or dropped out since the previous night.
Nothing here trades; promotion into a demo desk and anything with real money stays a human decision. Stdlib + numpy."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
OUT = "web/autolab.json"

# (key, name_ru, class, yahoo, stooq, binance)
MARKETS = [
    ("BTC", "Биткоин", "crypto", "BTC-USD", None, "BTCUSDT"),
    ("ETH", "Эфир", "crypto", "ETH-USD", None, "ETHUSDT"),
    ("BNB", "BNB", "crypto", None, None, "BNBUSDT"),
    ("SOL", "Solana", "crypto", None, None, "SOLUSDT"),
    ("XRP", "XRP", "crypto", None, None, "XRPUSDT"),
    ("GOLD", "Золото", "metal", "GC=F", "xauusd", None),
    ("SILVER", "Серебро", "metal", "SI=F", "xagusd", None),
    ("PLAT", "Платина", "metal", "PL=F", "xptusd", None),
    ("PALL", "Палладий", "metal", "PA=F", "xpdusd", None),
    ("COPPER", "Медь", "metal", "HG=F", "hg.f", None),
    ("ALU", "Алюминий", "metal", "ALI=F", None, None),
    ("SPX", "S&P 500", "index", "^GSPC", "^spx", None),
    ("NDX", "Nasdaq 100", "index", "^NDX", "^ndx", None),
    ("DJI", "Dow Jones", "index", "^DJI", "^dji", None),
    ("RUT", "Russell 2000", "index", "^RUT", None, None),
    ("DAX", "DAX", "index", "^GDAXI", "^dax", None),
    ("NIKKEI", "Nikkei 225", "index", "^N225", "^nkx", None),
    ("STOXX", "Euro Stoxx 50", "index", "^STOXX50E", None, None),
    ("OIL", "Нефть WTI", "other", "CL=F", "cl.f", None),
    ("UST10", "Облигации США 10 лет", "other", "ZN=F", None, None),
]
CLASS_RU = {"crypto": "крипта", "metal": "металлы", "index": "фондовые индексы", "other": "нефть и облигации"}
COST = {"crypto": 0.001, "metal": 0.0005, "index": 0.0003, "other": 0.0005}      # per unit of position change
VOL_TGT, LEV_MAX = 0.15, 2.0
IS_YEARS, STEP = 5, 63
FAMILIES = {
    "trend": ("Выше средней N дней", [50, 100, 200]),
    "donch": ("Пробой канала N дней", [20, 55, 100]),
    "tsmom": ("Импульс за N дней", [21, 63, 126, 252]),
    "rsi2": ("Откат RSI(2) ниже N в тренде", [5, 10, 20]),
    "hold": ("Купить и держать", [0]),
}
MODES = {"long": "только покупки", "ls": "покупки и шорты", "inv": "перевёрнутый"}
# what each demo desk's honest history promised (monthly %, worst drawdown %) — the live check compares against these
EXPECT = {"cons": (1.5, 9), "bal": (3.4, 18), "aggr": (4.8, 29), "brk": (0.8, 49), "imp": (1.3, 12), "imps": (1.3, 12),
          "lst": (2.0, 30), "lstx": (6.0, 56), "btcx": (2.9, 69), "own": (2.0, 23), "lsn": (1.6, 25)}


def note(title, msg):
    print(f"{title}: {msg}", flush=True)
    if os.environ.get("GITHUB_ACTIONS"):
        m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::notice title={title}::{m[:4000]}", flush=True)


def http(url, tries=3, timeout=40):
    err = None
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2 * (a + 1))
    raise RuntimeError(f"{url[:80]}: {err}")


# ------------------------------------------------------------------------------------------------------------------ data
def yahoo(sym):
    b = http(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?period1=0&period2={int(time.time())}"
             f"&interval=1d&includeAdjustedClose=true")
    r = json.loads(b)["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = {}
    for t, o, h, lo, c in zip(r.get("timestamp") or [], q["open"], q["high"], q["low"], q["close"]):
        if c and h and lo and c > 0:
            out[dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()] = (o or c, h, lo, c)
    return out


def yfin(sym):
    """yfinance (browser-like TLS) — the plain Yahoo URL is refused from GitHub Actions with 429."""
    import yfinance as yf
    df = yf.Ticker(sym).history(period="max", interval="1d", auto_adjust=False)
    out = {}
    for ts, row in df.iterrows():
        c = float(row["Close"])
        if c > 0 and not math.isnan(c):
            o, h, lo = float(row["Open"]), float(row["High"]), float(row["Low"])
            out[ts.date().isoformat()] = (o if o > 0 else c, h if h > 0 else c, lo if lo > 0 else c, c)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    out.pop(today, None)
    return out


def stooq(sym):
    b = http(f"https://stooq.com/q/d/l/?s={urllib.parse.quote(sym)}&i=d").decode()
    out = {}
    for row in csv.DictReader(io.StringIO(b)):
        try:
            c = float(row["Close"])
            out[row["Date"]] = (float(row["Open"]), float(row["High"]), float(row["Low"]), c)
        except (KeyError, ValueError):
            continue
    return out


def binance(sym):
    out, start = {}, 1_500_000_000_000
    for _ in range(12):
        d = json.loads(http(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}&interval=1d&startTime={start}&limit=1000"))
        if not d:
            break
        for k in d:
            out[dt.datetime.fromtimestamp(k[0] / 1000, dt.timezone.utc).date().isoformat()] = (float(k[1]), float(k[2]), float(k[3]), float(k[4]))
        start = int(d[-1][0]) + 86_400_000
        if len(d) < 1000:
            break
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    out.pop(today, None)                    # the forming candle
    return out


def load(m):
    key, _n, cls, y, s, b = m
    errs = []
    for src, fn, arg in (("Binance", binance, b), ("Yahoo", yfin, y), ("Yahoo URL", yahoo, y), ("Stooq", stooq, s)):
        if not arg:
            continue
        try:
            d = fn(arg)
            if len(d) >= 3 * 252:
                return key, d, src, None
            errs.append(f"{src}: {len(d)} дней")
        except Exception as e:  # noqa: BLE001
            errs.append(f"{src}: {str(e)[:60]}")
    return key, None, None, "; ".join(errs)


# ----------------------------------------------------------------------------------------------------------- indicators
def sma(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        cs = np.cumsum(np.r_[0.0, x])
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def rsi2(c):
    d = np.diff(c, prepend=c[0])
    up, dn = np.maximum(d, 0), np.maximum(-d, 0)
    a = 1 / 2
    ru, rd = np.zeros(len(c)), np.zeros(len(c))
    for i in range(1, len(c)):
        ru[i] = ru[i - 1] + a * (up[i] - ru[i - 1])
        rd[i] = rd[i - 1] + a * (dn[i] - rd[i - 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(rd == 0, 100.0, 100 - 100 / (1 + ru / rd))


def signal(fam, p, c):
    """Raw position at each close in [-1, 1] (long and short version); NaN-free."""
    n = len(c)
    if fam == "hold":
        return np.ones(n)
    if fam == "trend":
        s = sma(c, p)
        return np.where(np.isnan(s), 0.0, np.where(c > s, 1.0, -1.0))
    if fam == "tsmom":
        out = np.zeros(n)
        out[p:] = np.sign(c[p:] / c[:-p] - 1)
        return out
    if fam == "donch":
        out, pos, h = np.zeros(n), 0.0, max(2, p // 2)
        for i in range(p, n):
            hi, lo = c[i - p:i].max(), c[i - p:i].min()
            if c[i] >= hi:
                pos = 1.0
            elif c[i] <= lo:
                pos = -1.0
            elif pos > 0 and c[i] <= c[i - h:i].min():
                pos = 0.0
            elif pos < 0 and c[i] >= c[i - h:i].max():
                pos = 0.0
            out[i] = pos
        return out
    if fam == "rsi2":
        r, s200, s5 = rsi2(c), sma(c, 200), sma(c, 5)
        out, pos = np.zeros(n), 0.0
        for i in range(200, n):
            if pos == 0:
                if r[i] < p and c[i] > s200[i]:
                    pos = 1.0
                elif r[i] > 100 - p and c[i] < s200[i]:
                    pos = -1.0
            elif pos > 0 and c[i] > s5[i]:
                pos = 0.0
            elif pos < 0 and c[i] < s5[i]:
                pos = 0.0
            out[i] = pos
        return out
    raise ValueError(fam)


_W = {}


def vol_weight(c, ppy):
    """Position size per unit of signal: VOL_TGT ÷ the 20-day realised yearly volatility, capped at LEV_MAX (cached per series)."""
    key = (id(c), ppy)
    if key not in _W:
        r = np.r_[0.0, c[1:] / c[:-1] - 1]
        vol = np.full(len(c), np.nan)
        if len(c) > 21:
            sq = np.r_[0.0, np.cumsum(r[1:] ** 2)]                # sq[i] = Σ r[1..i]²
            vol[21:] = np.sqrt((sq[21:] - sq[1:-20]) / 20 * ppy)
        _W[key] = np.where(np.isnan(vol) | (vol <= 0), 0.0, np.minimum(LEV_MAX, VOL_TGT / np.where(np.isnan(vol) | (vol <= 0), 1, vol)))
    return _W[key]


def strat_returns(c, raw, mode, cost, ppy):
    """Daily strategy returns: the position set at close t earns the return of t+1; sized to VOL_TGT a year (capped)."""
    pos = {"long": np.maximum(raw, 0.0), "ls": raw, "inv": -raw}[mode]
    r = np.r_[0.0, c[1:] / c[:-1] - 1]
    expo = pos * vol_weight(c, ppy)
    out = np.zeros(len(c))
    out[1:] = expo[:-1] * r[1:] - cost * np.abs(np.diff(expo))
    return out


def stats(r, dates, ppy):
    if len(r) < 60:
        return None
    eq = np.cumprod(1 + r)
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    yrs = {}
    for d, x in zip(dates, r):
        yrs.setdefault(d[:4], []).append(x)
    ys = {y: float(np.prod(1 + np.array(v)) - 1) for y, v in yrs.items() if len(v) >= ppy // 3}
    sd = float(r.std())
    last = r[-ppy:]
    l3 = r[-3 * ppy:]
    return {"cagr": round((float(eq[-1]) ** (ppy / len(r)) - 1) * 100, 1), "sharpe": round(float(r.mean()) / sd * math.sqrt(ppy), 2) if sd else 0.0,
            "dd": round(dd * 100, 1), "years": {y: round(v * 100, 1) for y, v in sorted(ys.items())},
            "pos_years": sum(1 for v in ys.values() if v > 0), "n_years": len(ys),
            "last12": round((float(np.prod(1 + last)) - 1) * 100, 1),
            "sharpe3y": round(float(l3.mean()) / float(l3.std()) * math.sqrt(ppy), 2) if l3.std() else 0.0,
            "from": dates[0], "to": dates[-1]}


def walk_forward(series_by_param, ppy):
    """series_by_param {p: daily returns}; every STEP days choose the p with the best Sharpe over the past IS_YEARS (≥ 3 years)."""
    ps = list(series_by_param)
    n = len(series_by_param[ps[0]])
    start = 3 * ppy
    oos, chosen = np.zeros(n), []
    for b in range(start, n, STEP):
        lo = max(0, b - IS_YEARS * ppy)

        def sh(p):
            x = series_by_param[p][lo:b]
            return x.mean() / x.std() if x.std() else -9
        best = max(ps, key=sh) if len(ps) > 1 else ps[0]
        oos[b:b + STEP] = series_by_param[best][b:b + STEP]
        chosen.append(best)
    return oos[start:], start, chosen


# ------------------------------------------------------------------------------------------------------------- switch test
def switch_test(OOS, meta, looks=(3, 7, 14, 30, 90), holds=(1, 7)):
    """19.10, owner: «автоматически переключать на самую эффективную стратегию … не надо месяц ждать». On each market, the 12
    rule versions (4 families × 3 directions, each already walk-forward) are the candidates. SWITCHER: every `hold` days move the
    whole account to the version with the best result over the last `look` days (cost: 2 × the market's cost per switch).
    Against: the plain MIX of all 12 (equal parts, never switching). Rows per market class: average yearly %, Sharpe, worst
    drawdown, share of markets where the switcher beat the mix."""
    out = {}
    for cls in ("crypto", "metal", "index", "other"):
        mk = [k for k in OOS if meta[k][2] == cls]
        if not mk:
            continue
        res = {}
        for key in mk:
            cands = OOS[key]
            ks = list(cands)
            n = min(len(cands[k][1]) for k in ks)
            M = np.array([cands[k][1][-n:] for k in ks])          # versions × days
            ppy = 365 if cls == "crypto" else 252
            cost = 2 * COST[cls]
            mix = M.mean(axis=0)
            res.setdefault("mix", []).append(mix)
            cum = np.cumsum(M, axis=1)
            for look in looks:
                for hold in holds:
                    r, cur, sw = np.zeros(n), None, 0
                    for t in range(look, n):
                        if (t - look) % hold == 0:
                            past = cum[:, t - 1] - (cum[:, t - 1 - look] if t - 1 - look >= 0 else 0)
                            best = int(np.argmax(past))
                            if best != cur:
                                sw += 1
                                r[t] -= cost if cur is not None else 0
                                cur = best
                        r[t] += M[cur, t]
                    res.setdefault(f"switch {look}d/{'day' if hold == 1 else 'week'}", []).append(r)
            # SOFT: «the system adapts faster» — half the account stays in the equal mix, half goes to the versions in plus over the
            # last `look` days in proportion to that plus; reweighted weekly; cost on the change of weights
            for look in (7, 14, 30, 90):
                r, w = np.zeros(n), np.full(len(ks), 1 / len(ks))
                for t in range(look, n):
                    if (t - look) % 7 == 0:
                        past = cum[:, t - 1] - (cum[:, t - 1 - look] if t - 1 - look >= 0 else 0)
                        pos = np.maximum(past, 0)
                        tilt = pos / pos.sum() if pos.sum() > 0 else np.full(len(ks), 1 / len(ks))
                        w_new = 0.5 / len(ks) + 0.5 * tilt
                        r[t] -= COST[cls] * float(np.abs(w_new - w).sum())
                        w = w_new
                    r[t] += float(w @ M[:, t])
                res.setdefault(f"tilt {look}d/week", []).append(r)
        def agg(series):
            ppy = 365 if cls == "crypto" else 252
            S = [stats(x, ["2000-01-01"] * len(x), ppy) for x in series]
            S = [x for x in S if x]
            return {"cagr": round(float(np.mean([x["cagr"] for x in S])), 1), "sharpe": round(float(np.mean([x["sharpe"] for x in S])), 2),
                    "dd": round(float(np.mean([x["dd"] for x in S])), 1)} if S else None
        row = {k: agg(v) for k, v in res.items()}
        for k, v in res.items():
            if k != "mix" and row.get(k):
                wins = sum(1 for a, b in zip(v, res["mix"]) if a.mean() / (a.std() or 1) > b.mean() / (b.std() or 1))
                row[k]["beats_mix_share"] = round(wins / len(v), 2)
        out[cls] = {"markets": mk, **row}
    return out


def scale_test(OOS, meta, looks=(7, 14, 30, 90)):
    """19.10, owner: «когда система видит, что сигналы идут чётко по направлению … задействовать свободный кэш, рычаги». Each rule
    version that PASSED (or every version, as a control): exposure ×1.5 while its own last N days are in plus, ×0.5 while in
    minus (on average about the same money in the market) — against the constant ×1. Also the reverse (more after losses)."""
    out = {}
    for cls in ("crypto", "metal", "index"):
        series = [(k, fm, v[1]) for k in OOS if meta[k][2] == cls for fm, v in OOS[k].items()]
        if not series:
            continue
        ppy = 365 if cls == "crypto" else 252
        acc = {}
        for _k, _fm, r in series:
            cum = np.cumsum(r)
            acc.setdefault("const ×1", []).append(r)
            for look in looks:
                past = np.r_[np.zeros(look + 1), cum[look:-1] - cum[:-look - 1]][:len(r)]
                for name, up, dn in (("больше после плюса", 1.5, 0.5), ("больше после минуса", 0.5, 1.5)):
                    m = np.where(past > 0, up, dn)
                    m[: look + 1] = 1.0
                    turn = np.abs(np.diff(np.r_[1.0, m])) * COST[meta[_k][2]] * 0.5
                    acc.setdefault(f"{name} {look}d", []).append(r * m - turn)
        row = {}
        for k, v in acc.items():
            S = [stats(x, ["2000-01-01"] * len(x), ppy) for x in v]
            S = [x for x in S if x]
            row[k] = {"cagr": round(float(np.mean([x["cagr"] for x in S])), 2), "sharpe": round(float(np.mean([x["sharpe"] for x in S])), 3),
                      "dd": round(float(np.mean([x["dd"] for x in S])), 1)}
            if k != "const ×1":
                row[k]["better_sharpe_share"] = round(sum(1 for a, b in zip(v, acc["const ×1"])
                                                          if a.mean() / (a.std() or 1) > b.mean() / (b.std() or 1)) / len(v), 2)
        out[cls] = {"versions": len(series), **row}
    return out


# ------------------------------------------------------------------------------------------------------------- live check
def live_check(paper):
    rows = []
    now = time.time() * 1000
    for k, d in ((paper or {}).get("profiles") or {}).items():
        since = d.get("since") or d.get("asof") or now
        days = max(0.0, (now - since) / 86_400_000)
        st = d.get("stats") or {}
        n = st.get("trades") or 0
        pnl = d.get("pnl_pct") or 0.0
        dd = st.get("max_dd_pct") or 0.0
        exp = EXPECT.get(k)
        pace = pnl / days * 30 if days >= 1 else None
        if exp and dd > exp[1]:
            status, why = "alarm", f"просадка {dd:.1f}% глубже худшей исторической {exp[1]}%"
        elif days < 30 or n < 20:
            status, why = "early", f"{days:.0f} дн., {n} сделок, результат {pnl:+.2f}% (судить можно от 30 дней и 20 сделок)"
        elif exp and pace is not None and pace < 0 and days >= 60:
            status, why = "below", f"темп {pace:+.1f}% в месяц при ожидании {exp[0]:+.1f}%"
        elif exp and pace is not None and pace < exp[0] / 2:
            status, why = "watch", f"темп {pace:+.1f}% в месяц, ожидание {exp[0]:+.1f}%"
        else:
            status, why = "ok", f"темп {pace:+.1f}% в месяц" + (f", ожидание {exp[0]:+.1f}%" if exp else "") if pace is not None else "в норме"
        rows.append({"key": k, "name": d.get("name") or k, "days": round(days, 1), "trades": n, "pnl_pct": round(pnl, 2), "dd_pct": dd,
                     "expect_month_pct": exp[0] if exp else None, "expect_dd_pct": exp[1] if exp else None, "status": status, "why_ru": why})
    order = {"alarm": 0, "below": 1, "watch": 2, "ok": 3, "early": 4}
    return sorted(rows, key=lambda r: order[r["status"]])


# ------------------------------------------------------------------------------------------------------------------ main
def main():
    t0 = time.time()
    with ThreadPoolExecutor(5) as ex:
        got = list(ex.map(load, [m for m in MARKETS if m[5]]))
    got += [load(m) for m in MARKETS if not m[5]]
    meta = {m[0]: m for m in MARKETS}
    data, cover = {}, {}
    for key, d, src, err in got:
        m = meta[key]
        if d:
            dates = sorted(d)
            data[key] = (dates, np.array([d[x][3] for x in dates]))
            cover[key] = {"name": m[1], "class": m[2], "source": src, "from": dates[0], "to": dates[-1], "days": len(dates)}
        else:
            cover[key] = {"name": m[1], "class": m[2], "error": err}
    note("autolab · data", json.dumps({k: (v.get("source"), v.get("from"), v.get("days")) if "source" in v else v.get("error")
                                       for k, v in cover.items()}, ensure_ascii=False))
    rows, port_parts, tried = [], {}, 0
    OOS = {}
    for key, (dates, c) in data.items():
        cls = meta[key][2]
        ppy = 365 if cls == "crypto" else 252
        for fam, (fam_ru, params) in FAMILIES.items():
            raws = {p: signal(fam, p, c) for p in params}
            for mode, mode_ru in MODES.items():
                if fam == "hold" and mode != "long":
                    continue
                tried += len(params)
                ser = {p: strat_returns(c, raws[p], mode, COST[cls], ppy) for p in params}
                oos, start, chosen = walk_forward(ser, ppy)
                if fam != "hold":
                    OOS.setdefault(key, {})[(fam, mode)] = (dates[start:], oos)
                st = stats(oos, dates[start:], ppy)
                if not st:
                    continue
                rows.append({"market": key, "market_ru": meta[key][1], "class": cls, "family": fam, "family_ru": fam_ru, "mode": mode,
                             "mode_ru": mode_ru, "param_now": chosen[-1] if chosen else None, **st})
        # pre-set portfolio piece: the average of the 4 momentum signals, long and short, sized to VOL_TGT
        ppy_ = 365 if cls == "crypto" else 252
        raw = np.mean([signal("tsmom", L, c) for L in (21, 63, 126, 252)], axis=0)
        port_parts[key] = (dates, strat_returns(c, raw, "ls", COST[cls], ppy_), strat_returns(c, raw, "long", COST[cls], ppy_), cls)
    years_test = {r["market"]: max(1.0, len(r["years"])) for r in rows}
    hurdle_n = max(1, len(rows))
    for r in rows:
        hurdle = math.sqrt(2 * math.log(hurdle_n)) / math.sqrt(years_test.get(r["market"], 10))
        r["hurdle"] = round(hurdle, 2)
        r["passes"] = (r["sharpe"] >= 0.4 and r["n_years"] >= 3 and r["pos_years"] / r["n_years"] >= 0.6 and r["dd"] >= -35
                       and r["sharpe3y"] > 0 and r["family"] != "hold")
        r["reliable"] = bool(r["passes"] and r["sharpe"] >= hurdle)
    # portfolios (nothing fitted): equal weight of the pieces on the union of dates
    def combine(keys, which):
        cal = sorted({d for k in keys for d in port_parts[k][0]})
        idx = {d: i for i, d in enumerate(cal)}
        M = np.zeros((len(cal), len(keys)))
        live = np.zeros((len(cal), len(keys)), dtype=bool)
        for j, k in enumerate(keys):
            ds, ls, lo, _cls = port_parts[k]
            x = ls if which == "ls" else lo
            for d, v in zip(ds, x):
                M[idx[d], j] = v
                live[idx[d], j] = True
        first = {k: idx[port_parts[k][0][3 * 252]] if len(port_parts[k][0]) > 3 * 252 else len(cal) for k in keys}
        for j, k in enumerate(keys):
            live[:first[k], j] = False
        cnt = np.maximum(1, live.sum(axis=1))
        r = (M * live).sum(axis=1) / cnt
        start = min(first.values())
        return cal[start:], r[start:]
    ports = {}
    nonc = [k for k in port_parts if port_parts[k][3] != "crypto"]
    sets = {"Мультирынок · тренд (металлы, индексы, нефть, облигации; лонг и шорт)": (nonc, "ls"),
            "Мультирынок · тренд, только покупки": (nonc, "long"),
            "Мультирынок + BTC и ETH (лонг и шорт)": (nonc + [k for k in ("BTC", "ETH") if k in port_parts], "ls"),
            "Только металлы · тренд (лонг и шорт)": ([k for k in nonc if port_parts[k][3] == "metal"], "ls"),
            "Только индексы · тренд (лонг и шорт)": ([k for k in nonc if port_parts[k][3] == "index"], "ls")}
    for name, (keys, which) in sets.items():
        if len(keys) < 2:
            continue
        cal, r = combine(keys, which)
        st = stats(r, cal, 252)
        if st:
            lev = None
            for L in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0):
                eq = np.cumprod(1 + L * r)
                if float((eq / np.maximum.accumulate(eq) - 1).min()) >= -0.25:
                    lev = L
            if lev:
                eq = np.cumprod(1 + lev * r)
                st["at_dd25"] = {"lev": lev, "cagr": round((float(eq[-1]) ** (252 / len(r)) - 1) * 100, 1),
                                 "month": round(((float(eq[-1]) ** (252 / len(r))) ** (1 / 12) - 1) * 100, 2)}
            ports[name] = {"markets": keys, **st}
    for name, p in ports.items():
        note(f"autolab · portfolio · {name[:40]}", json.dumps({k: p.get(k) for k in ("cagr", "sharpe", "dd", "pos_years", "n_years", "last12", "at_dd25", "from")}, ensure_ascii=False))
    switch = switch_test(OOS, meta)
    scale = scale_test(OOS, meta)
    note("autolab · scale test", json.dumps(scale, ensure_ascii=False)[:3900])
    note("autolab · switch test", json.dumps(switch, ensure_ascii=False))
    # previous night → changes
    try:
        prev = json.load(open(OUT, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        prev = {}
    pid = lambda r: f"{r['market']}·{r['family']}·{r['mode']}"  # noqa: E731
    was = {pid(r) for r in prev.get("rows") or [] if r.get("passes")}
    now_ = {pid(r) for r in rows if r["passes"]}
    changes = ([{"t": "new", "id": x} for x in sorted(now_ - was)] + [{"t": "dropped", "id": x} for x in sorted(was - now_)]) if prev else []
    try:
        paper = json.load(open("web/paper.json", encoding="utf-8"))
    except Exception:  # noqa: BLE001
        paper = None
    live = live_check(paper)
    rows.sort(key=lambda r: (-int(r["reliable"]), -int(r["passes"]), -r["sharpe"]))
    best = {}
    for r in rows:
        if r["passes"] and r["class"] not in best:
            best[r["class"]] = r
    inv = [r for r in rows if r["mode"] == "inv"]
    doc = {"asof": int(time.time() * 1000), "minutes": round((time.time() - t0) / 60, 1), "tried": tried, "tested": len(rows),
           "passed": sum(r["passes"] for r in rows), "reliable": sum(r["reliable"] for r in rows),
           "inverted_passed": sum(r["passes"] for r in inv), "coverage": cover, "classes_ru": CLASS_RU,
           "best_by_class": {k: {x: v[x] for x in ("market_ru", "family_ru", "mode_ru", "sharpe", "cagr", "dd", "last12", "reliable")}
                             for k, v in best.items()},
           "portfolios": ports, "switch": switch, "scale": scale, "live": live, "changes": changes, "rows": rows,
           "method_ru": [
               "Каждую ночь движок сам берёт свежие дневные свечи: крипта (Binance), металлы, фондовые индексы, нефть и облигации США (Yahoo, запасной — Stooq).",
               "5 семей правил × параметры × 3 направления: только покупки, покупки и шорты, перевёрнутое правило. Размер позиции — под 15% годовых колебаний, комиссии учтены.",
               "Честная проверка «вперёд»: каждый квартал движок выбирает параметр только по прошлым 5 годам и торгует им следующий квартал. Считаются только эти следующие кварталы.",
               "Прошло — если на проверке Sharpe от 0.4, в плюсе от 60% лет, просадка не глубже −35% и последние 3 года в плюсе. Надёжно — если Sharpe ещё и выше того, что лучшее из стольких случайных правил показало бы по везению.",
               "Портфели «Мультирынок» заданы заранее, ничего не подгоняется: средний сигнал импульса за 1, 3, 6 и 12 месяцев на каждом рынке, равный риск.",
               "Живые демо-счета сравниваются с тем, что обещала их история: рано судить / в норме / ниже ожиданий / тревога.",
               "Движок ничего не торгует сам: перевод правила в демо-счёт и любые реальные деньги — решение человека.",
               "Ограничения: фьючерсы Yahoo склеены из контрактов (скачки при смене контракта), индексы без дивидендов, крипта — только живые сегодня монеты."]}
    os.makedirs("web", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    note("autolab · summary", json.dumps({k: doc[k] for k in ("tried", "tested", "passed", "reliable", "inverted_passed", "minutes")}))
    note("autolab · best by class", json.dumps(doc["best_by_class"], ensure_ascii=False))
    top = [{k: r[k] for k in ("market", "family", "mode", "param_now", "sharpe", "cagr", "dd", "pos_years", "n_years", "last12", "sharpe3y", "reliable")}
           for r in rows if r["passes"]][:25]
    note("autolab · passed (best first)", json.dumps(top, ensure_ascii=False))
    note("autolab · live desks", json.dumps([(r["key"], r["status"], r["why_ru"]) for r in live], ensure_ascii=False))
    if changes:
        note("autolab · changes", json.dumps(changes, ensure_ascii=False))


def probe():
    """Which free daily sources answer from GitHub Actions (Yahoo is often refused there)."""
    urls = {
        "yahoo1": "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?range=5d&interval=1d",
        "yahoo2": "https://query2.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=5d&interval=1d",
        "stooq": "https://stooq.com/q/d/l/?s=xauusd&i=d",
        "lbma_gold": "https://prices.lbma.org.uk/json/gold_pm.json",
        "lbma_silver": "https://prices.lbma.org.uk/json/silver.json",
        "lbma_plat": "https://prices.lbma.org.uk/json/platinum_pm.json",
        "fred_sp500": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500",
        "fred_ndx": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQ100",
        "fred_oil": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILWTICO",
        "fred_copper": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PCOPPUSDM",
        "binance_xau": "https://data-api.binance.vision/api/v3/klines?symbol=PAXGUSDT&interval=1d&limit=3",
        "binance_um_xau": "https://data.binance.vision/?prefix=data/futures/um/monthly/klines/XAUUSDT/1d/",
    }
    out = {}
    for k, u in urls.items():
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
                b = r.read()
            out[k] = [r.status, len(b), b[:160].decode("utf-8", "replace")]
        except urllib.error.HTTPError as e:
            out[k] = [e.code, (e.read() or b"")[:160].decode("utf-8", "replace")]
        except Exception as e:  # noqa: BLE001
            out[k] = ["ERR", str(e)[:160]]
    note("autolab · probe", json.dumps(out, ensure_ascii=False))


def probe2():
    out = {}
    try:
        import yfinance as yf
        for sym in ("GC=F", "^GSPC", "ALI=F", "PA=F"):
            try:
                df = yf.Ticker(sym).history(period="max", interval="1d", auto_adjust=False)
                out["yf " + sym] = [len(df), str(df.index[0])[:10] if len(df) else None, str(df.index[-1])[:10] if len(df) else None]
            except Exception as e:  # noqa: BLE001
                out["yf " + sym] = ["ERR", str(e)[:160]]
    except Exception as e:  # noqa: BLE001
        out["yfinance import"] = str(e)[:160]
    for k, u in {"fred_sp500": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500",
                 "cboe_spx": "https://cdn.cboe.com/api/global/us_indices/daily_prices/SPX_History.csv",
                 "cboe_vix": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"}.items():
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=90) as r:
                b = r.read()
            out[k] = [r.status, len(b), b[:120].decode("utf-8", "replace")]
        except Exception as e:  # noqa: BLE001
            out[k] = ["ERR", str(e)[:160]]
    note("autolab · probe2", json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    probe() if "--probe" in sys.argv else probe2() if "--probe2" in sys.argv else main()
