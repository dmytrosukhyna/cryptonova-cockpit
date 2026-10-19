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


def closed_markets(n_max=4000, vol_min=50_000):
    out, off = [], 0
    while len(out) < n_max and off < 30_000:
        st, page = get(f"{GAMMA}/markets?closed=true&limit=500&offset={off}&order=volumeNum&ascending=false&volume_num_min={vol_min}")
        if st != 200 or not page:
            break
        for m in page:
            prices = [float(x) for x in (_js(m.get("outcomePrices")) or [])]
            toks = _js(m.get("clobTokenIds")) or []
            if len(prices) == 2 and len(toks) == 2 and sorted(prices) == [0.0, 1.0]:
                out.append({"q": m.get("question"), "end": m.get("closedTime") or m.get("endDate"), "tok": toks[0],
                            "yes_won": prices[0] == 1.0, "vol": float(m.get("volumeNum") or 0), "neg": bool(m.get("negRisk")),
                            "cat": m.get("category") or ""})
        off += 500
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
    ms = closed_markets()
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


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    {"probe": probe, "bonds": bonds, "arb": arb}[mode]()
