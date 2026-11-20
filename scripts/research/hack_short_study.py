"""Backlog (owner 20.11.2026 14:53, after XMR1 on Hyperliquid): «система должна видеть взломы и открывать шорты». RESEARCH ONLY —
no order anywhere. PRE-REGISTERED (this file is committed before its first run):

Events: DefiLlama /hacks, 2022-01-01 → now. The hacked protocol's own token: /protocols symbol of the hack's defillamaId (else of
any protocol with the same parentProtocol). Kept only when a USDT perpetual of that symbol (or 1000{symbol}) has candles at the
event: Binance USDT-M (listed: fapi; delisted: data.binance.vision archive), else OKX {symbol}-USDT-SWAP. Hyperliquid and Bybit
history are not used (no free archive of delisted markets) — said in the report.
Event time — two sets:
  T (primary, precise): the hack's DefiLlama «source» is an X/Twitter post → its time from the post id (snowflake: (id >> 22) +
     1288834974657 ms). This is the first public alert we can date, to the second. Entry = alert + delay d ∈ {5, 15, 60} min
     (primary d = 15) at the 5-minute close.
  D (secondary): DefiLlama «date» only (usually day precision). Entry = date + 24 h (we surely know by then) — no look-ahead;
     «date» itself (the day's start, may precede the hack) only as an upper bound, never for the verdict.
Trade: short at entry, exit after h ∈ {1 h, 4 h, 24 h, 72 h, 7 d}; result % = −(exit / entry − 1) × 100 − 0.10 (costs) − 0.01 per
8 h (funding paid, assumed: history is not fetched for most coins).
Null: for each event 20 windows in the same token, same weekday and UTC hour, 15–180 days away from the event, same horizon →
abnormal = event result − mean of its null windows. Permutation p: share of 2 000 resampled null means (one random null window
per event) ≥ the events' mean.
Splits: loss ÷ the token's average daily perp quote volume over the 30 days before (< 0.1, 0.1–1, > 1; market cap at the time is
not in the free data, volume stands in); kind: «key/bridge» (bridgeHack, or technique / classification names a private key,
compromise, bridge, phishing, social) vs «contract bug» (the rest); «token itself compromised» (technique names an infinite /
unauthorised mint, token contract, or the classification is «Token») vs «protocol funds stolen» (the rest); halves by date.
PASS (set T, d = 15 min): for a group × horizon — mean abnormal > 0, t ≥ 3.26 (Bonferroni over 5 horizons × 9 groups = 45 tests,
two-sided 5 %), permutation p < 0.05 / 45, both halves > 0, n ≥ 20. Only a pass may become the owner-only PAPER desk
«Взломы → шорт (тест)»; never real money without the owner's yes.
Feasibility (set T): the token's move from alert − 6 h to the alert, and from the alert to +5 / +15 / +60 min, as a share of the
move alert − 6 h → alert + 24 h — how much is gone before a realistic delay.
Usage: hack_short_study.py OUT.json"""
import io, json, math, random, statistics as st, sys, time, urllib.request, zipfile
from collections import defaultdict

H, M5, DAY = 3_600_000, 300_000, 86_400_000
START = 1_640_995_200_000          # 2022-01-01
HORIZ = {"1h": H, "4h": 4 * H, "24h": DAY, "72h": 3 * DAY, "7d": 7 * DAY}
DELAYS = (5, 15, 60)
COST, FUND_8H = 0.10, 0.01
Z, NTEST = 3.26, 45
RNG = random.Random(20261120)
KEY_WORDS = ("private key", "compromis", "bridge", "phish", "social", "multisig", "access control")
TOKEN_WORDS = ("infinite mint", "unauthorized mint", "mint", "token contract", "rug")


def get(url, raw=False, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cnq-research"})
            with urllib.request.urlopen(req, timeout=30) as r:
                b = r.read()
            return b if raw else json.loads(b.decode())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            time.sleep(1 + k)
        except Exception:  # noqa: BLE001
            time.sleep(1 + k)
    return None


def snowflake(url):
    import re
    m = re.search(r"(?:twitter|x)\.com/[^/]+/status(?:es)?/(\d{15,20})", str(url or ""))
    return ((int(m.group(1)) >> 22) + 1288834974657) if m else None


# ── prices ───────────────────────────────────────────────────────────────────────────────────────────────────────────
CACHE = {}


def bn_live(sym, itv, t0, t1):
    out, t = [], t0
    while t < t1:
        d = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={itv}&startTime={t}&endTime={t1}&limit=1500")
        if not d:
            break
        out += [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])] for r in d]
        if len(d) < 1500:
            break
        t = int(d[-1][0]) + 1
        time.sleep(0.15)
    return out


def bn_vision(sym, itv, t0, t1):
    """delisted symbols: monthly (1h) / daily (5m) archives"""
    import datetime as dt
    out = []
    a = dt.datetime.fromtimestamp(t0 / 1000, dt.UTC); b = dt.datetime.fromtimestamp(t1 / 1000, dt.UTC)
    if itv == "1h":
        y, m = a.year, a.month
        while (y, m) <= (b.year, b.month):
            z = get(f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1h/{sym}-1h-{y}-{m:02d}.zip", raw=True)
            if z:
                out += _unzip(z)
            m += 1
            if m > 12:
                y, m = y + 1, 1
    else:
        d = a.date()
        while d <= b.date():
            z = get(f"https://data.binance.vision/data/futures/um/daily/klines/{sym}/{itv}/{sym}-{itv}-{d.isoformat()}.zip", raw=True)
            if z:
                out += _unzip(z)
            d += dt.timedelta(days=1)
    return [r for r in out if t0 <= r[0] <= t1]


def _unzip(z):
    rows = []
    with zipfile.ZipFile(io.BytesIO(z)) as f:
        for n in f.namelist():
            for ln in f.read(n).decode().splitlines():
                p = ln.split(",")
                if p and p[0].isdigit():
                    rows.append([int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[7])])
    return rows


def okx(inst, bar, t0, t1):
    out, after = {}, t1
    for _ in range(200):
        d = get(f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar={bar}&limit=100&after={after}")
        rows = (d or {}).get("data") or []
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])]
        after = int(rows[-1][0])
        if after <= t0:
            break
        time.sleep(0.12)
    return sorted(v for k, v in out.items() if t0 <= k <= t1)


def candles(venue, sym, itv, t0, t1):
    k = (venue, sym, itv, t0 // DAY, t1 // DAY)
    if k in CACHE:
        return CACHE[k]
    if venue == "binance":
        r = bn_live(sym, itv, t0, t1) or bn_vision(sym, itv, t0, t1)
    else:
        r = okx(sym, {"1h": "1H", "5m": "5m"}[itv], t0, t1)
    CACHE[k] = sorted({x[0]: x for x in r}.values())
    return CACHE[k]


def find_market(symbol, t):
    for venue, sym in (("binance", f"{symbol}USDT"), ("binance", f"1000{symbol}USDT"), ("okx", f"{symbol}-USDT-SWAP")):
        k = candles(venue, sym, "1h", t - 2 * DAY, t + 2 * DAY)
        if len(k) >= 40:
            return venue, sym
    return None


def px_at(K, t):
    """the close of the last bar that closed at or before t (K: [t, o, h, l, c, v], bar length from the data)"""
    if len(K) < 2:
        return None
    step = K[1][0] - K[0][0]
    lo, hi = 0, len(K)
    while lo < hi:
        m = (lo + hi) // 2
        if K[m][0] + step <= t:
            lo = m + 1
        else:
            hi = m
    j = lo - 1
    return K[j][4] if j >= 0 and t - (K[j][0] + step) <= 2 * step else None


def short_ret(K, t, h):
    a, b = px_at(K, t), px_at(K, t + h)
    if not a or not b:
        return None
    return -(b / a - 1) * 100 - COST - FUND_8H * h / (8 * H)


def tstat(xs):
    if len(xs) < 3:
        return None
    sd = st.stdev(xs)
    return round(st.mean(xs) / (sd / math.sqrt(len(xs))), 2) if sd else None


def main():
    hacks = get("https://api.llama.fi/hacks") or []
    protos = get("https://api.llama.fi/protocols") or []
    by_id = {str(p.get("id")): p for p in protos}
    by_parent = defaultdict(list)
    for p in protos:
        if p.get("parentProtocol"):
            by_parent[str(p["parentProtocol"])].append(p)
    prec = {"n": 0, "midnight": 0}
    ev = []
    for x in hacks:
        t = int(float(x.get("date") or 0) * 1000)
        if t < START:
            continue
        prec["n"] += 1
        prec["midnight"] += 1 if t % DAY == 0 else 0
        p = by_id.get(str(x.get("defillamaId") or ""))
        sym = (p or {}).get("symbol")
        if (not sym or sym == "-") and x.get("parentProtocolId"):
            for q in by_parent.get(str(x["parentProtocolId"]), []):
                if q.get("symbol") and q["symbol"] != "-":
                    sym = q["symbol"]; break
        if not sym or sym == "-":
            continue
        tech = f"{x.get('technique') or ''} {x.get('classification') or ''} {x.get('targetType') or ''}".lower()
        ev.append({"name": x.get("name"), "sym": str(sym).upper(), "t_day": t, "t_alert": snowflake(x.get("source")), "loss": float(x.get("amount") or 0),
                   "kind": "key/bridge" if x.get("bridgeHack") or any(w in tech for w in KEY_WORDS) else "contract",
                   "token": "token" if any(w in tech for w in TOKEN_WORDS) or str(x.get("classification") or "").lower() == "token" else "funds",
                   "tech": (x.get("technique") or "")[:60]})
    print(f"hacks since 2022: {prec['n']}, at 00:00 UTC: {prec['midnight']}; with a token symbol: {len(ev)}; with an X post: "
          f"{sum(1 for e in ev if e['t_alert'])}", flush=True)
    rows = []
    for e in ev:
        t_ref = e["t_alert"] or e["t_day"]
        mk = find_market(e["sym"], t_ref)
        if not mk:
            continue
        venue, sym = mk
        K1 = candles(venue, sym, "1h", t_ref - 200 * DAY, t_ref + 200 * DAY)
        if len(K1) < 24 * 30:
            continue
        pre = [k for k in K1 if t_ref - 30 * DAY <= k[0] < t_ref]
        vol = sum(k[5] for k in pre) / 30 if pre else None
        r = {**e, "venue": venue, "symbol": sym, "vol_day": vol, "loss_vol": (e["loss"] / vol) if vol else None, "T": {}, "D": {}, "null": {}}
        if e["t_alert"]:
            K5 = candles(venue, sym, "5m", e["t_alert"] - 7 * H, e["t_alert"] + 8 * DAY)
            for d in DELAYS:
                r["T"][str(d)] = {hn: short_ret(K5 if hv <= DAY else K1, e["t_alert"] + d * 60_000, hv) for hn, hv in HORIZ.items()}
            p6, p0, p24 = px_at(K5, e["t_alert"] - 6 * H), px_at(K5, e["t_alert"]), px_at(K5, e["t_alert"] + DAY)
            r["feas"] = {"pre6h_pct": (p0 / p6 - 1) * 100 if p6 and p0 else None, "move_6h_24h_pct": (p24 / p6 - 1) * 100 if p6 and p24 else None,
                         **{f"to_{d}m_pct": ((px_at(K5, e["t_alert"] + d * 60_000) or 0) / p0 - 1) * 100 if p0 and px_at(K5, e["t_alert"] + d * 60_000) else None
                            for d in DELAYS}}
        r["D"] = {"next_day": {hn: short_ret(K1, e["t_day"] + DAY, hv) for hn, hv in HORIZ.items()},
                  "day_start_upper_bound": {hn: short_ret(K1, e["t_day"], hv) for hn, hv in HORIZ.items()}}
        t_e = (e["t_alert"] + 15 * 60_000) if e["t_alert"] else e["t_day"] + DAY
        cands = [t_e + s * k * 7 * DAY for k in range(3, 26) for s in (-1, 1)]       # same weekday and hour, 21–175 days away
        RNG.shuffle(cands)
        for hn, hv in HORIZ.items():
            xs = [v for v in (short_ret(K1, c_, hv) for c_ in cands[:40]) if v is not None][:20]
            r["null"][hn] = xs
        rows.append(r)
        print(f"{e['name'][:24]:24} {sym:14} {venue} alert={'y' if e['t_alert'] else 'n'}", flush=True)

    def groups(rs):
        g = {"all": rs}
        for lo, hi, nm in ((0, 0.1, "loss<0.1vol"), (0.1, 1, "loss0.1-1vol"), (1, 1e18, "loss>1vol")):
            g[nm] = [r for r in rs if r["loss_vol"] is not None and lo <= r["loss_vol"] < hi]
        for k in ("key/bridge", "contract"):
            g[k] = [r for r in rs if r["kind"] == k]
        for k in ("token", "funds"):
            g["token_" + k] = [r for r in rs if r["token"] == k]
        return g

    def block(rs, getter):
        out = {}
        mid = sorted(r["t_day"] for r in rs)[len(rs) // 2] if rs else 0
        for gname, g in groups(rs).items():
            gb = {}
            for hn in HORIZ:
                pts = [(r, getter(r, hn)) for r in g]
                pts = [(r, v) for r, v in pts if v is not None and r["null"].get(hn)]
                ab = [v - st.mean(r["null"][hn]) for r, v in pts]
                raw = [v for _, v in pts]
                if len(ab) < 3:
                    gb[hn] = {"n": len(ab)}
                    continue
                m = st.mean(raw)
                perm = 0
                for _ in range(2000):
                    perm += st.mean(RNG.choice(r["null"][hn]) for r, _ in pts) >= m
                h1 = [a for (r, _), a in zip(pts, ab) if r["t_day"] < mid]
                h2 = [a for (r, _), a in zip(pts, ab) if r["t_day"] >= mid]
                gb[hn] = {"n": len(ab), "mean_raw": round(m, 2), "median_raw": round(st.median(raw), 2), "win": round(sum(v > 0 for v in raw) / len(raw), 2),
                          "worst": round(min(raw), 1), "mean_abn": round(st.mean(ab), 2), "t_abn": tstat(ab), "p_perm": round(perm / 2000, 4),
                          "h1_abn": round(st.mean(h1), 2) if h1 else None, "h2_abn": round(st.mean(h2), 2) if h2 else None}
                gb[hn]["pass"] = bool(gb[hn]["mean_abn"] > 0 and (gb[hn]["t_abn"] or 0) >= Z and gb[hn]["p_perm"] < 0.05 / NTEST
                                      and (gb[hn]["h1_abn"] or -1) > 0 and (gb[hn]["h2_abn"] or -1) > 0 and gb[hn]["n"] >= 20)
            out[gname] = gb
        return out

    T = [r for r in rows if r["T"]]
    O = {"precision": prec, "events_with_symbol": len(ev), "events_priced": len(rows), "events_T": len(T),
         "T": {str(d): block(T, lambda r, hn, d=d: r["T"][str(d)][hn]) for d in DELAYS},
         "D_next_day": block(rows, lambda r, hn: r["D"]["next_day"][hn]),
         "D_day_start_upper_bound": block(rows, lambda r, hn: r["D"]["day_start_upper_bound"][hn])}
    fe = [r["feas"] for r in T if r.get("feas") and r["feas"].get("move_6h_24h_pct")]
    O["feasibility"] = {"n": len(fe), **{k: (round(st.median(x[k] for x in fe if x.get(k) is not None), 2) if fe else None)
                                          for k in ("pre6h_pct", "move_6h_24h_pct", "to_5m_pct", "to_15m_pct", "to_60m_pct")}}
    big = [x for x in fe if x["move_6h_24h_pct"] <= -10]
    O["feasibility"]["big_drops_n"] = len(big)
    if big:
        O["feasibility"]["big_share_gone_before_alert"] = round(st.median(x["pre6h_pct"] / x["move_6h_24h_pct"] for x in big), 2)
        for d in DELAYS:
            O["feasibility"][f"big_share_gone_by_{d}m"] = round(st.median((((1 + x["pre6h_pct"] / 100) * (1 + (x[f"to_{d}m_pct"] or 0) / 100) - 1) * 100) / x["move_6h_24h_pct"] for x in big), 2)
    O["passed"] = [f"T{d}|{g}|{hn}" for d in DELAYS for g, gb in O["T"][str(d)].items() for hn, b in gb.items() if d == 15 and b.get("pass")]
    O["events"] = [{k: r.get(k) for k in ("name", "sym", "symbol", "venue", "t_day", "t_alert", "loss", "loss_vol", "kind", "token", "tech")}
                   | {"T15": (r["T"].get("15") if r["T"] else None), "Dnext": r["D"]["next_day"], "feas": r.get("feas")} for r in rows]
    json.dump(O, open(sys.argv[1], "w"), default=lambda o: None)
    print("passed:", O["passed"], flush=True)


if __name__ == "__main__":
    main()
