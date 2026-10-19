"""Polymarket research (19.10.2026, owner: «попробовать стратегию на Полимаркете … боты, которые моментально предугадывают
вероятности … 50 на 50 … небольшие комиссии, как арбитраж … где самые высокодоходные инструменты»).
Runs in GitHub Actions only (.github/workflows/research-polymarket.yml); public read APIs, no keys, no trading.

Modes:
  probe   — what the public APIs return (Gamma markets/events, CLOB book / prices-history, Data API trades / leaderboard).
  bonds   — «high-probability bonds» / favourite-longshot bias: on resolved markets, the favourite's price 1 / 3 / 7 days before
            the end vs how often it actually won → the return of buying favourites at 0.80–0.995 (+1 cent slippage).
  arb     — a live snapshot: sum-to-one (YES ask + NO ask < 1) and negRisk events (sum of YES asks < 1) with the depth at those
            prices; how much money such gaps actually hold.
Stdlib only."""
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 cryptonova-research/1.0", "Accept": "application/json"}


def note(title, msg):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::notice title={title}::{m}", flush=True)


def get(url, tries=4):
    err = None
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 451):
                return e.code, None
            err = e
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(1.5 * (a + 1))
    return -1, str(err)[:200]


def _js(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:  # noqa: BLE001
            return None
    return v


def probe():
    out = {}
    st, mk = get(f"{GAMMA}/markets?closed=false&limit=3&order=volume24hr&ascending=false")
    out["gamma markets open"] = [st, sorted(mk[0].keys()) if isinstance(mk, list) and mk else str(mk)[:200]]
    tok = None
    if isinstance(mk, list) and mk:
        m = mk[0]
        out["sample"] = {k: m.get(k) for k in ("question", "endDate", "outcomes", "outcomePrices", "clobTokenIds", "negRisk", "volumeNum",
                                                "bestBid", "bestAsk", "spread", "rewardsMinSize", "rewardsMaxSpread", "feesEnabled")}
        tok = (_js(m.get("clobTokenIds")) or [None])[0]
    st, cl = get(f"{GAMMA}/markets?closed=true&limit=3&order=volumeNum&ascending=false")
    out["gamma markets closed"] = [st, [{k: x.get(k) for k in ("question", "endDate", "closedTime", "outcomePrices", "umaResolutionStatus")}
                                        for x in (cl or [])[:2]] if isinstance(cl, list) else str(cl)[:200]]
    st, ev = get(f"{GAMMA}/events?closed=false&limit=2&order=volume24hr&ascending=false")
    out["gamma events"] = [st, sorted(ev[0].keys())[:40] if isinstance(ev, list) and ev else str(ev)[:200]]
    if tok:
        st, b = get(f"{CLOB}/book?token_id={tok}")
        out["clob book"] = [st, {k: (v[:2] if isinstance(v, list) else v) for k, v in (b or {}).items()} if isinstance(b, dict) else str(b)[:200]]
        st, h = get(f"{CLOB}/prices-history?market={tok}&interval=1w&fidelity=60")
        out["clob prices-history"] = [st, (len((h or {}).get("history") or []), ((h or {}).get("history") or [None])[:2]) if isinstance(h, dict) else str(h)[:200]]
    st, tr = get(f"{DATA}/trades?limit=2")
    out["data trades"] = [st, tr[:1] if isinstance(tr, list) else str(tr)[:200]]
    for path in ("/v1/leaderboard?timePeriod=month&orderBy=PNL&limit=3", "/leaderboard?window=1m&limit=3"):
        st, lb = get(f"{DATA}{path}")
        out[f"data {path.split('?')[0]}"] = [st, lb[:1] if isinstance(lb, list) else str(lb)[:200]]
    st, sm = get(f"{CLOB}/sampling-markets")
    out["clob sampling-markets (rewards)"] = [st, sorted(((sm or {}).get("data") or [{}])[0].keys())[:30] if isinstance(sm, dict) else str(sm)[:200]]
    note("polymarket probe", json.dumps(out, ensure_ascii=False, default=str)[:3900])


def closed_markets(n_max=8000, vol_min=20_000):
    """Resolved binary markets, month by month 2024-01 → now (the API pages poorly past ~500 rows of one query)."""
    out, seen, diag = [], set(), []
    y, mo = 2024, 1
    now = dt.datetime.now(dt.timezone.utc)
    while (y, mo) <= (now.year, now.month) and len(out) < n_max:
        a = dt.datetime(y, mo, 1, tzinfo=dt.timezone.utc)
        y2, mo2 = (y + 1, 1) if mo == 12 else (y, mo + 1)
        b = dt.datetime(y2, mo2, 1, tzinfo=dt.timezone.utc)
        got = 0
        for off in range(0, 2000, 500):
            q = urllib.parse.urlencode({"closed": "true", "limit": 500, "offset": off, "order": "volumeNum", "ascending": "false",
                                        "volume_num_min": vol_min, "end_date_min": a.strftime("%Y-%m-%dT00:00:00Z"),
                                        "end_date_max": b.strftime("%Y-%m-%dT00:00:00Z")})
            st, page = get(f"{GAMMA}/markets?{q}")
            if st != 200 or not page:
                break
            for m in page:
                if m.get("id") in seen:
                    continue
                seen.add(m.get("id"))
                prices = [float(x) for x in (_js(m.get("outcomePrices")) or [])]
                toks = _js(m.get("clobTokenIds")) or []
                if len(prices) == 2 and len(toks) == 2 and sorted(prices) == [0.0, 1.0]:
                    out.append({"q": m.get("question"), "end": m.get("closedTime") or m.get("endDate"), "tok": toks[0],
                                "yes_won": prices[0] == 1.0, "vol": float(m.get("volumeNum") or 0), "neg": bool(m.get("negRisk")),
                                "updown": "Up or Down" in str(m.get("question"))})
                    got += 1
            if len(page) < 500:
                break
        diag.append(got)
        y, mo = y2, mo2
    note("bonds · markets by month 2024→", json.dumps(diag))
    return out


def _ts(s):
    if not s:
        return None
    try:
        return int(dt.datetime.fromisoformat(str(s).replace("Z", "+00:00").replace(" ", "T")[:25]).timestamp())
    except ValueError:
        try:
            return int(dt.datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp())
        except ValueError:
            return None


def history(tok, end_ts):
    st, h = get(f"{CLOB}/prices-history?market={tok}&startTs={end_ts - 10 * 86400}&endTs={end_ts}&fidelity=60")
    pts = (h or {}).get("history") if isinstance(h, dict) else None
    return [(int(p["t"]), float(p["p"])) for p in pts or []]


def bonds():
    ms = [m for m in closed_markets() if not m["updown"]]
    note("bonds · markets", json.dumps({"binary resolved, volume ≥ $50k": len(ms), "negRisk": sum(m["neg"] for m in ms)}))

    def one(m):
        e = _ts(m["end"])
        if not e:
            return None
        h = history(m["tok"], e)
        if len(h) < 24:
            return None
        res = {}
        last_t = h[-1][0]
        for d in (1, 3, 7):
            t = last_t - d * 86400
            before = [p for tt, p in h if tt <= t]
            if not before:
                continue
            p_yes = before[-1]
            fav_yes = p_yes >= 0.5
            p_fav = p_yes if fav_yes else 1 - p_yes
            res[d] = (p_fav, fav_yes == m["yes_won"])
        return res
    with ThreadPoolExecutor(8) as ex:
        rows = [r for r in ex.map(one, ms) if r]
    buckets = [(0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 0.98), (0.98, 0.995)]
    out = {}
    for d in (1, 3, 7):
        for lo, hi in buckets:
            xs = [r[d] for r in rows if d in r and lo <= r[d][0] < hi]
            if len(xs) < 20:
                continue
            p = sum(x[0] for x in xs) / len(xs)
            win = sum(1 for x in xs if x[1]) / len(xs)
            cost = [min(0.999, x[0] + 0.01) for x in xs]          # +1 cent: the ask is above the mid
            ret = sum((1.0 if x[1] else 0.0) / c - 1 for x, c in zip(xs, cost)) / len(xs)
            out[f"{d}d {lo:.2f}-{hi:.3f}"] = {"n": len(xs), "price": round(p, 3), "won": round(win, 3), "ret_pct": round(ret * 100, 2),
                                             "per_year_pct": round(((1 + ret) ** (365 / d) - 1) * 100) if ret > -1 else None,
                                             "worst_losses": sum(1 for x in xs if not x[1])}
    note("bonds · favourite at the price → how often it won, return with +1 cent", json.dumps(out, ensure_ascii=False))


def arb():
    """Live snapshot of sum-to-one gaps in the most active markets."""
    st, ms = get(f"{GAMMA}/markets?closed=false&limit=500&order=volume24hr&ascending=false")
    ms = ms if isinstance(ms, list) else []

    def books(m):
        toks = _js(m.get("clobTokenIds")) or []
        if len(toks) != 2:
            return None
        res = []
        for t in toks:
            s, b = get(f"{CLOB}/book?token_id={t}")
            asks = sorted(((float(a["price"]), float(a["size"])) for a in (b or {}).get("asks") or []), key=lambda x: x[0])
            bids = sorted(((float(a["price"]), float(a["size"])) for a in (b or {}).get("bids") or []), key=lambda x: -x[0])
            res.append((asks, bids))
        return m, res
    with ThreadPoolExecutor(8) as ex:
        got = [g for g in ex.map(books, ms[:300]) if g]
    gaps, spreads = [], []
    for m, ((a1, b1), (a2, b2)) in got:
        if a1 and a2:
            s = a1[0][0] + a2[0][0]
            spreads.append(s)
            if s < 1:
                size = min(a1[0][1], a2[0][1])
                gaps.append({"q": (m.get("question") or "")[:60], "sum": round(s, 4), "usd_at_gap": round(size * s, 2)})
    spreads.sort()
    note("arb · YES ask + NO ask on the 300 most active markets", json.dumps({
        "markets": len(spreads), "below_1": len(gaps), "gaps": gaps[:12],
        "sum_quantiles": [round(spreads[int(q * (len(spreads) - 1))], 4) for q in (0.0, 0.1, 0.5, 0.9)] if spreads else None},
        ensure_ascii=False))


def _norm_cdf(x):
    import math
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def binance_1m(start_ms, end_ms):
    """BTCUSDT spot 1-minute closes {minute ms: (open, close)} from data.binance.vision (monthly + daily zips)."""
    import csv
    import io
    import zipfile
    base = "https://data.binance.vision/data/spot"
    out = {}
    d = dt.datetime.fromtimestamp(start_ms / 1000, dt.timezone.utc).date().replace(day=1)
    end = dt.datetime.fromtimestamp(end_ms / 1000, dt.timezone.utc).date()
    today = dt.datetime.now(dt.timezone.utc).date()
    urls = []
    while d <= end:
        if (d.year, d.month) < (today.year, today.month):
            urls.append(f"{base}/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-{d:%Y-%m}.zip")
        else:
            x = d
            while x < today:
                urls.append(f"{base}/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{x:%Y-%m-%d}.zip")
                x += dt.timedelta(days=1)
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)

    def load(u):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                z = zipfile.ZipFile(io.BytesIO(r.read()))
            rows = list(csv.reader(io.StringIO(z.read(z.namelist()[0]).decode())))
            return [(int(x[0]) // 1000 if int(x[0]) > 10 ** 14 else int(x[0]), float(x[1]), float(x[4])) for x in rows if x and x[0].isdigit()]
        except Exception:  # noqa: BLE001
            return []
    with ThreadPoolExecutor(8) as ex:
        for rows in ex.map(load, urls):
            for t, o, c in rows:
                out[t] = (o, c)
    return out


def updown():
    """Crypto «Up or Down» 15-minute markets: does Polymarket's price lag Bitcoin's own move inside the window?
    Model price = Φ(r ÷ (σ1m·√minutes left)) from Binance (σ from the previous 60 minutes — known at the time). Trade when the model
    differs from Polymarket's price by ≥ 5 / 10 / 15 cents; pay the price + 1 cent (+ the market's taker fee if any), hold to
    the end. Resolution uses Chainlink, our check uses Binance — close calls can differ."""
    import re
    rx = re.compile(r"^Bitcoin Up or Down - .*?(\d{1,2}):(\d{2})(AM|PM)-(\d{1,2}):(\d{2})(AM|PM) ET")
    ms, off = [], 0
    while off < 20000 and len(ms) < 3000:
        st, page = get(f"{GAMMA}/markets?closed=true&limit=500&offset={off}&order=endDate&ascending=false")
        if st != 200 or not page:
            break
        for m in page:
            q = str(m.get("question") or "")
            mm = rx.match(q)
            if not mm:
                continue
            h1, m1, a1, h2, m2, a2 = mm.groups()
            t1 = (int(h1) % 12 + (12 if a1 == "PM" else 0)) * 60 + int(m1)
            t2 = (int(h2) % 12 + (12 if a2 == "PM" else 0)) * 60 + int(m2)
            if (t2 - t1) % 1440 != 15:
                continue
            prices = [float(x) for x in (_js(m.get("outcomePrices")) or [])]
            toks = _js(m.get("clobTokenIds")) or []
            outs = _js(m.get("outcomes")) or []
            if len(prices) != 2 or sorted(prices) != [0.0, 1.0] or len(toks) != 2:
                continue
            up_i = 0 if str(outs[0]).lower().startswith("up") else 1
            end = _ts(m.get("endDate"))
            if not end:
                continue
            ms.append({"end": end, "start": end - 900, "tok_up": toks[up_i], "up_won": prices[up_i] == 1.0,
                       "fee": m.get("takerBaseFee"), "fees": m.get("feesEnabled"), "q": q})
        off += 500
    note("updown · markets", json.dumps({"btc 15m resolved": len(ms), "fee sample": [(x["fees"], x["fee"]) for x in ms[:3]],
                                         "span": [ms[-1]["q"] if ms else None, ms[0]["q"] if ms else None]}, ensure_ascii=False))
    if not ms:
        return
    K = binance_1m((min(x["start"] for x in ms) - 7200) * 1000, max(x["end"] for x in ms) * 1000)

    def one(m):
        st, h = get(f"{CLOB}/prices-history?market={m['tok_up']}&startTs={m['start'] - 300}&endTs={m['end']}&fidelity=1")
        pts = [(int(p["t"]), float(p["p"])) for p in ((h or {}).get("history") or [])] if isinstance(h, dict) else []
        return m, pts
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(one, ms))
    import math
    rows = []
    for m, pts in got:
        if len(pts) < 5:
            continue
        t0 = m["start"] // 60 * 60 * 1000
        o = K.get(t0)
        prev = [K.get(t0 - k * 60000) for k in range(1, 61)]
        prev = [x for x in prev if x]
        if not o or len(prev) < 40:
            continue
        rets = [math.log(c / op) for op, c in prev if op > 0]
        sig = (sum(r * r for r in rets) / len(rets)) ** 0.5
        for k in (3, 6, 9, 12, 14):
            bar = K.get(t0 + (k - 1) * 60000)
            if not bar or sig <= 0:
                continue
            r = math.log(bar[1] / o[0])
            left = 15 - k
            model = _norm_cdf(r / (sig * math.sqrt(left))) if left > 0 else (1.0 if r > 0 else 0.0)
            tk = m["start"] + k * 60
            before = [p for t, p in pts if t <= tk]
            if not before:
                continue
            rows.append((k, before[-1], model, m["up_won"]))
    out = {"rows": len(rows)}
    for k in (3, 6, 9, 12, 14):
        rk = [x for x in rows if x[0] == k]
        if not rk:
            continue
        cal = {}
        for lo in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9):
            xs = [x for x in rk if lo <= x[1] < lo + (0.1 if lo in (0.0, 0.9) else 0.2)]
            if len(xs) >= 20:
                cal[f"poly {lo:.1f}"] = [len(xs), round(sum(x[3] for x in xs) / len(xs), 3)]
        tr = {}
        for th in (0.05, 0.10, 0.15):
            pl = []
            for _k, p, mdl, won in rk:
                if mdl - p >= th and p < 0.98:
                    cost = p + 0.01
                    pl.append((1.0 if won else 0.0) / cost - 1)
                elif p - mdl >= th and p > 0.02:
                    cost = (1 - p) + 0.01
                    pl.append((0.0 if won else 1.0) / cost - 1)
            if pl:
                tr[f"edge≥{th:.2f}"] = [len(pl), round(sum(pl) / len(pl) * 100, 2), round(sum(1 for x in pl if x > 0) / len(pl) * 100)]
        out[f"min {k}"] = {"calibration (price bucket → up share)": cal, "trades [n, avg %, wins %]": tr}
    note("updown · Polymarket price vs Bitcoin's move inside the window", json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    {"probe": probe, "bonds": bonds, "arb": arb, "updown": updown}[mode]()
