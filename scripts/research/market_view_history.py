"""«Направление рынка» — history study (backlog 217, daily 09:23 run of the analyst, 20.11.2026).

Question (Dmitry 19.11): which public signals predicted strong BTC/ETH dumps / pumps 1–3 days ahead in 2023–2026?
Source: data.binance.vision (USDⓈ-M BTCUSDT/ETHUSDT: 1d klines, fundingRate, metrics = OI + long/short, bookDepth ±1%),
stooq ^SPX daily (US stocks). Public files only, no keys, no trading.

RULES — fixed in this file BEFORE the first run (20.11.2026 07:40 UTC), no tuning afterwards:
  signals are computed on day d close (00:00 UTC of d+1); trade = BTC/ETH perp from close d to close d+1 (24h) / d+3 (72h)
  R1 funding  : day-avg funding in top 10% of the trailing 365 d → short; bottom 10% → long          (crowding, contrarian)
  R2 oi_dump  : OI +3% or more AND price −2% or more on the day → short                          (new shorts pile in = continuation)
  R3 cascade  : price −5% AND OI −5% → long (capitulation bounce); price +5% AND OI −5% → short (squeeze fade)
  R4 book     : day-mean (bid−ask)/(bid+ask) notional within 1% in top 10% of trailing 180 d → long; bottom 10% → short
  R5 ls_ratio : day-mean global account long/short ratio top 10% of trailing 365 d → short; bottom 10% → long
  R6 regime   : close < SMA50 < SMA200 → short; close > SMA50 > SMA200 → long
  R7 spx      : S&P 500 fell 2%+ in the US session that closed on day d → short
  R8 combo    : sum of votes R1..R7 ≥ +2 → long, ≤ −2 → short  («сильный склон»)
Costs: 0.10% round trip (2×0.05% taker) + actual funding paid/received over the hold (8h prints).
Independence: consecutive signal days of the same rule/side closer than the horizon are one EPISODE; only its first day counts.
Halves: A = 2023-01-01..2024-12-31, B = 2025-01-01..end.  Tests: 8 rules × 2 horizons = 16 (Bonferroni).
PASS (BTC): ≥ 30 episodes, ≥ 12 in each half; net mean > 0 in BOTH halves; one-sided t-test p·16 < 0.05; ETH net mean > 0 full period.
Also reported (not a pass criterion): lift of «strong move» (72h ≤ −5% for short / ≥ +5% for long) vs the base rate.
Side filter for copies: desk copies (web/whales_live.json) and followed traders' closed trips (data/whale_trades) — what if
LONG copies were skipped when R8 = short (and, separately, when R6 = short)? Unlevered return = pnl / notional.

usage: python scripts/research/market_view_history.py docs/research/market_view_history.md docs/research/market_view_history.json
"""
import csv, glob, io, json, math, os, sys, time, urllib.request, urllib.error, zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

BASE = "https://data.binance.vision/data/futures/um"
START, EVAL0, SPLIT = date(2022, 1, 1), date(2023, 1, 1), date(2025, 1, 1)
END = (datetime.now(timezone.utc) - timedelta(days=2)).date()
COST, NTESTS = 0.10, 16
CACHE = os.environ.get("MV_CACHE", "/tmp/mvcache")
DAY = 86400000


def fetch(url, tries=4):
    os.makedirs(CACHE, exist_ok=True)
    loc = os.path.join(CACHE, url.rsplit("/", 1)[1])
    if os.path.exists(loc):
        return loc
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-research/1.0"}), timeout=60) as r:
                b = r.read()
            open(loc, "wb").write(b)
            return loc
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1 + k)
    return None


def rows(path):
    if not path:
        return []
    with zipfile.ZipFile(path) as z:
        txt = z.open(z.namelist()[0]).read().decode()
    out = list(csv.reader(io.StringIO(txt)))
    if out and any(c.isalpha() for c in out[0][0]):
        hdr = out[0]
        return [dict(zip(hdr, r)) for r in out[1:]]
    return out


def months(a, b):
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def days(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def pool(urls):
    with ThreadPoolExecutor(24) as ex:
        return list(ex.map(fetch, urls))


def dkey(ms):
    return datetime.fromtimestamp(int(ms) / (1000 if int(ms) < 10**14 else 1e6), timezone.utc).date()


def load(sym, book=True):
    D = {}
    # klines 1d: monthly + daily for the current month
    urls = [f"{BASE}/monthly/klines/{sym}/1d/{sym}-1d-{y}-{m:02d}.zip" for y, m in months(START, END)]
    urls += [f"{BASE}/daily/klines/{sym}/1d/{sym}-1d-{d:%Y-%m-%d}.zip" for d in days(END.replace(day=1), END)]
    for p in pool(urls):
        for r in rows(p):
            r = list(r.values()) if isinstance(r, dict) else r
            d = dkey(r[0])
            D.setdefault(d, {})["close"] = float(r[4])
    # funding (monthly files; 8h prints)
    fund = {}
    for p in pool([f"{BASE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{y}-{m:02d}.zip" for y, m in months(START, END)]):
        for r in rows(p):
            v = list(r.values()) if isinstance(r, dict) else r
            fund.setdefault(dkey(v[0]), []).append(float(v[2]))
    for d, v in fund.items():
        D.setdefault(d, {})["fund"] = sum(v) / len(v)
        D[d]["fund_sum"] = sum(v)
    # metrics (5 min): OI at the last print of the day, mean global long/short
    ds = list(days(START, END))
    for d, p in zip(ds, pool([f"{BASE}/daily/metrics/{sym}/{sym}-metrics-{d:%Y-%m-%d}.zip" for d in ds])):
        rr = rows(p)
        if not rr or not isinstance(rr[0], dict):
            continue
        try:
            oi = float(rr[-1]["sum_open_interest"])
            ls = [float(r["count_long_short_ratio"]) for r in rr if r.get("count_long_short_ratio")]
            if oi > 0:
                D.setdefault(d, {})["oi"] = oi
            if ls:
                D[d]["ls"] = sum(ls) / len(ls)
        except (KeyError, ValueError):
            pass
    if book:
        for d, p in zip(ds, pool([f"{BASE}/daily/bookDepth/{sym}/{sym}-bookDepth-{d:%Y-%m-%d}.zip" for d in ds])):
            rr = rows(p)
            if not rr or not isinstance(rr[0], dict):
                continue
            snap = {}
            for r in rr:
                try:
                    pc = float(r["percentage"])
                except (KeyError, ValueError):
                    continue
                if abs(pc) == 1:
                    snap.setdefault(r["timestamp"], {})[pc] = float(r["notional"])
            im = [(s[-1] - s[1]) / (s[-1] + s[1]) for s in snap.values() if -1 in s and 1 in s and s[-1] + s[1] > 0]
            if im:
                D.setdefault(d, {})["book"] = sum(im) / len(im)
    return D


def spx():
    """S&P 500 daily % change by session date: FRED SP500 first, stooq as a fallback"""
    srcs = (("https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500", None), ("https://stooq.com/q/d/l/?s=%5Espx&i=d", "Close"))
    for url, col in srcs:
        out = {}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
                txt = r.read().decode()
            rd = csv.reader(io.StringIO(txt))
            hdr = next(rd)
            ci = hdr.index(col) if col else 1
            prev = None
            for row in rd:
                try:
                    c = float(row[ci])
                except (ValueError, IndexError):
                    continue
                if prev:
                    out[date.fromisoformat(row[0])] = (c / prev - 1) * 100
                prev = c
        except Exception as e:  # noqa: BLE001
            print("spx failed", url, repr(e)[:120])
        if len(out) > 500:
            return out
    try:  # Yahoo chart API as the last fallback
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=10y&interval=1d"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
            j = json.loads(r.read())["chart"]["result"][0]
        out, prev = {}, None
        for t, c in zip(j["timestamp"], j["indicators"]["quote"][0]["close"]):
            if c is None:
                continue
            if prev:
                out[datetime.fromtimestamp(t, timezone.utc).date()] = (c / prev - 1) * 100
            prev = c
        return out
    except Exception as e:  # noqa: BLE001
        print("spx failed yahoo", repr(e)[:120])
    return {}


def pct_rank(hist, x):
    return sum(1 for h in hist if h < x) / len(hist)


def signals(D, SPX):
    ds = sorted(d for d in D if "close" in D[d])
    S = {}
    for i, d in enumerate(ds):
        if d < EVAL0 or i < 200:
            continue
        x, y = D[d], D[ds[i - 1]]
        v = {}
        f_hist = [D[k]["fund"] for k in ds[max(0, i - 365):i] if "fund" in D[k]]
        if "fund" in x and len(f_hist) >= 200:
            q = pct_rank(f_hist, x["fund"])
            v["R1"] = -1 if q >= 0.9 else 1 if q <= 0.1 else 0
        dp = (x["close"] / y["close"] - 1) * 100
        if x.get("oi") and y.get("oi"):
            do = (x["oi"] / y["oi"] - 1) * 100
            v["R2"] = -1 if do >= 3 and dp <= -2 else 0
            v["R3"] = 1 if dp <= -5 and do <= -5 else -1 if dp >= 5 and do <= -5 else 0
        b_hist = [D[k]["book"] for k in ds[max(0, i - 180):i] if "book" in D[k]]
        if "book" in x and len(b_hist) >= 60:
            q = pct_rank(b_hist, x["book"])
            v["R4"] = 1 if q >= 0.9 else -1 if q <= 0.1 else 0
        l_hist = [D[k]["ls"] for k in ds[max(0, i - 365):i] if "ls" in D[k]]
        if "ls" in x and len(l_hist) >= 200:
            q = pct_rank(l_hist, x["ls"])
            v["R5"] = -1 if q >= 0.9 else 1 if q <= 0.1 else 0
        c = [D[k]["close"] for k in ds[i - 199:i + 1]]
        s50, s200 = sum(c[-50:]) / 50, sum(c) / 200
        v["R6"] = -1 if x["close"] < s50 < s200 else 1 if x["close"] > s50 > s200 else 0
        if SPX:
            v["R7"] = -1 if SPX.get(d, 0) <= -2 else 0
        tot = sum(v.values())
        v["R8"] = 1 if tot >= 2 else -1 if tot <= -2 else 0
        S[d] = v
    return S


def fwd(D, d, h):
    ds = [d + timedelta(days=k) for k in range(h + 1)]
    if any(k not in D or "close" not in D[k] for k in ds):
        return None
    mv = (D[ds[-1]]["close"] / D[d]["close"] - 1) * 100
    fund = sum(D[k].get("fund_sum", 0) for k in ds[1:]) * 100  # paid by longs over the hold (approx: next h days' prints)
    return mv, fund


def tstat(x):
    n = len(x)
    if n < 3:
        return None, None
    m = sum(x) / n
    sd = math.sqrt(sum((a - m) ** 2 for a in x) / (n - 1)) or 1e-9
    t = m / (sd / math.sqrt(n))
    p = 0.5 * math.erfc(t / math.sqrt(2))  # one-sided, normal approx
    return t, p


def evaluate(D, S, rule, h):
    eps, last = [], {}
    for d in sorted(S):
        s = S[d].get(rule, 0)
        if not s:
            continue
        if s in last and (d - last[s]).days <= h:  # same episode (gap ≤ horizon): chain, do not count again
            last[s] = d
            continue
        last[s] = d
        f = fwd(D, d, h)
        if f is None:
            continue
        mv, fund = f
        net = s * mv - COST - s * fund
        eps.append({"d": d, "s": s, "mv": mv, "net": net, "big": (mv <= -5) if s < 0 else (mv >= 5)})
    return eps


def summarize(eps, D, h):
    def part(e):
        x = [a["net"] for a in e]
        t, p = tstat(x)
        return {"n": len(e), "mean": round(sum(x) / len(x), 3) if x else None, "hit": round(sum(a["net"] > 0 for a in e) / len(e) * 100) if e else None,
                "t": round(t, 2) if t else None, "p": p}
    A = [e for e in eps if e["d"] < SPLIT]
    B = [e for e in eps if e["d"] >= SPLIT]
    out = {"all": part(eps), "A": part(A), "B": part(B), "short": part([e for e in eps if e["s"] < 0]), "long": part([e for e in eps if e["s"] > 0])}
    # big-move lift
    ds = [d for d in D if d >= EVAL0 and fwd(D, d, h)]
    for side, cond in (("short", lambda m: m <= -5), ("long", lambda m: m >= 5)):
        base = sum(cond(fwd(D, d, h)[0]) for d in ds) / len(ds) if ds else 0
        e = [a for a in eps if (a["s"] < 0) == (side == "short")]
        out[f"big_{side}"] = {"rate": round(sum(a["big"] for a in e) / len(e) * 100) if e else None, "base": round(base * 100)}
    return out


def passed(sb, se):
    a, A, B = sb["all"], sb["A"], sb["B"]
    return bool(a["n"] >= 30 and A["n"] >= 12 and B["n"] >= 12 and (A["mean"] or 0) > 0 and (B["mean"] or 0) > 0
                and a["p"] is not None and a["p"] * NTESTS < 0.05 and (se["all"]["mean"] or 0) > 0)


def side_filter(S):
    def sig(t):
        d = dkey(t) - timedelta(days=1)
        ks = [k for k in S if k <= d]
        return S[max(ks)] if ks else {}
    out = {}
    try:
        desk = json.load(open("web/whales_live.json", encoding="utf-8"))["desk"]
        cop = [{"t": c["opened_t"], "side": c["side"], "r": c["pnl"] / c["notional"] * 100} for c in desk.get("closed") or []
               if c.get("notional") and c.get("pnl") is not None]
    except Exception:  # noqa: BLE001
        cop = []
    trips, seen = [], set()
    for fpath in sorted(glob.glob("data/whale_trades/*.jsonl")):
        for line in open(fpath, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") != "closed" or not r.get("notional") or r.get("pnl") is None:
                continue
            k = (r.get("trader"), r.get("id"))
            if k in seen:
                continue
            seen.add(k)
            trips.append({"t": r["open_t"], "side": r["side"], "r": r["pnl"] / r["notional"] * 100})
    for name, rows_ in (("desk", cop), ("traders", trips)):
        res = {}
        for rule in ("R8", "R6"):
            g = {}
            for c in rows_:
                lean = sig(c["t"]).get(rule, 0)
                key = f"{c['side'].lower()}|{'short' if lean < 0 else 'long' if lean > 0 else 'neutral'}"
                g.setdefault(key, []).append(c["r"])
            res[rule] = {k: {"n": len(v), "sum": round(sum(v), 2), "mean": round(sum(v) / len(v), 3), "win": round(sum(x > 0 for x in v) / len(v) * 100)}
                         for k, v in sorted(g.items())}
            lv = g.get("long|short", [])
            res[rule]["filter_effect_sum"] = round(-sum(lv), 2)  # skipping those longs adds −(their sum)
        out[name] = {"n": len(rows_), **res}
    return out


def main(md, js):
    SPX = spx()
    data = {"BTC": load("BTCUSDT", True), "ETH": load("ETHUSDT", False)}
    sigs = {k: signals(v, SPX) for k, v in data.items()}
    res = {}
    for rule in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"):
        for h in (1, 3):
            sb = summarize(evaluate(data["BTC"], sigs["BTC"], rule, h), data["BTC"], h)
            se = summarize(evaluate(data["ETH"], sigs["ETH"], rule, h), data["ETH"], h)
            res[f"{rule}_{h * 24}h"] = {"BTC": sb, "ETH": se, "pass": passed(sb, se)}
    last = max(sigs["BTC"])
    sf = side_filter(sigs["BTC"])
    cov = {k: {f: sum(1 for d in v if f in v[d]) for f in ("close", "fund", "oi", "ls", "book")} for k, v in data.items()}
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "end": str(END), "spx_days": len(SPX), "coverage": cov,
           "results": res, "last_signal_day": str(last), "last_signals": sigs["BTC"][last], "side_filter": sf,
           "recent_btc": {str(d): sigs["BTC"][d] for d in sorted(sigs["BTC"])[-10:]}}
    json.dump(out, open(js, "w"), ensure_ascii=False, indent=1, default=str)
    L = [f"# Направление рынка — проверка на истории (п. 217), {out['generated'][:16]} UTC", "",
         "Скрипт: `scripts/research/market_view_history.py` (правила записаны в нём до прогона). Данные: data.binance.vision BTCUSDT/ETHUSDT, S&P 500 (stooq).",
         f"Период оценки {EVAL0}..{END}; половины A = 2023–2024, B = 2025–2026. Издержки 0.10% + фандинг. Эпизоды (без перекрытий). Тестов 16 (Бонферрони).",
         f"Покрытие BTC: {cov['BTC']}; ETH: {cov['ETH']}; дней S&P: {len(SPX)}.", "",
         "| Правило | Срок | BTC эпизодов (A/B) | BTC нетто, % (A / B) | p·16 | ETH нетто, % | Сильный ход: при сигнале / база (шорт; лонг) | Прошло |",
         "|---|---|---|---|---|---|---|---|"]
    for k, v in res.items():
        b, e = v["BTC"], v["ETH"]
        p = b["all"]["p"]
        L.append(f"| {k.split('_')[0]} | {k.split('_')[1]} | {b['all']['n']} ({b['A']['n']}/{b['B']['n']}) | {b['A']['mean']} / {b['B']['mean']} | "
                 f"{round(min(1, p * NTESTS), 3) if p is not None else '—'} | {e['all']['mean']} | {b['big_short']['rate']}/{b['big_short']['base']}; "
                 f"{b['big_long']['rate']}/{b['big_long']['base']} | {'ДА' if v['pass'] else 'нет'} |")
    L += ["", f"Сигналы BTC на {last}: {sigs['BTC'][last]}", "", "## Фильтр стороны для копий", "", "```", json.dumps(sf, ensure_ascii=False, indent=1), "```"]
    open(md, "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
