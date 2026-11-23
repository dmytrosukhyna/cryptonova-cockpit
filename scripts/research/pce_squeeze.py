"""PCE event study + the «pre-event squeeze» condition + Thanksgiving week + the macro-bias rule (owner 23.11.2026 11:44). RESEARCH ONLY.
Pre-registered (23.11 ~12:00 Athens), the method of docs/research/macro_events.md on 1h bars:
- Events: PCE (BEA Personal Income and Outlays, 08:30 ET; scripts/macro_events_ext.json — dates exist 2023-2026 only, 2021-22 are
  not in our calendar and are not guessed), CPI and FOMC (scripts/macro_events.json, 2021-2026).
- Assets: BTCUSDT and an equal-weight basket of ETH SOL BNB XRP DOGE ADA AVAX LINK LTC (Binance USDT-M 1h klines, archive).
- Windows (h from the release): −48…−24, −24…0, 0…+1, +1…+24, +24…+72 — log return %. Baseline: the same weekday and hour 1-12 weeks
  before / after in weeks with no FOMC / CPI / NFP / PCE within ±72 h; per event the difference event − mean(baseline); t of those
  differences; per year; placebo = every event moved by a random 7-21 days (20 draws) — the share of placebo |t| ≥ the real |t|.
- Squeeze condition (CPI + PCE): in the 48 h before the release BTC up ≥ +2 %, average funding of the last 24 h > the 24 h before and
  > 0, and BTC open interest (Binance metrics) up ≥ 3 % over the 48 h. Outcome: BTC 0…+24 h and 0…+48 h; compared with the other
  events (Welch t).
- Thanksgiving week (Mon 00:00 UTC → Sun 24:00 of the week of the 4th Thursday of November, 2021-2025): BTC return vs every other week.
- Macro-bias rule (CPI / PCE / FOMC): flag = in the 24 h before the release the last completed 4h close < EMA20(4h) and its high <
  the previous 4h high. Outcome: BTC −24 h…+24 h around the release when flagged vs not — does a down-flip predict a drop?
Usage: pce_squeeze.py OUT.json"""
import bisect, io, json, math, os, random, statistics as st, sys, time, urllib.request, zipfile
from datetime import datetime, timedelta, timezone

H, DAY = 3_600_000, 86_400_000
ARC = "https://data.binance.vision/data/futures/um"
SYMS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC"]
WIN = [(-48, -24), (-24, 0), (0, 1), (1, 24), (24, 72)]
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")


def get(url):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-research"}), timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:  # noqa: BLE001
            time.sleep(2 + 2 * k)
    return None


def rows(blob):
    if not blob:
        return []
    z = zipfile.ZipFile(io.BytesIO(blob))
    return [p for p in (ln.split(",") for ln in z.read(z.namelist()[0]).decode().splitlines()) if p and p[0][:1].isdigit()]


def months(y0=2021):
    now = datetime.now(timezone.utc)
    y, m = y0, 1
    while (y, m) < (now.year, now.month):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def klines(sym):
    out = {}
    for y, m in months():
        for p in rows(get(f"{ARC}/monthly/klines/{sym}USDT/1h/{sym}USDT-1h-{y}-{m:02d}.zip")):
            out[int(p[0])] = (float(p[1]), float(p[2]), float(p[3]), float(p[4]))
    now = datetime.now(timezone.utc)
    d = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    while d < now - timedelta(days=1):
        for p in rows(get(f"{ARC}/daily/klines/{sym}USDT/1h/{sym}USDT-1h-{d:%Y-%m-%d}.zip")):
            out[int(p[0])] = (float(p[1]), float(p[2]), float(p[3]), float(p[4]))
        d += timedelta(days=1)
    return out


def funding(sym="BTC"):
    out = []
    for y, m in months():
        for p in rows(get(f"{ARC}/monthly/fundingRate/{sym}USDT/{sym}USDT-fundingRate-{y}-{m:02d}.zip")):
            try:
                out.append((int(p[0]), float(p[2])))
            except ValueError:
                pass
    return sorted(out)


def oi_at(t):
    """BTC open interest (USD) at the 5m mark nearest t (Binance metrics daily file)"""
    d = datetime.fromtimestamp(t / 1000, timezone.utc)
    rs = rows(get(f"{ARC}/daily/metrics/BTCUSDT/BTCUSDT-metrics-{d:%Y-%m-%d}.zip"))
    best = None
    for p in rs:
        try:
            ts = int(datetime.strptime(p[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
            v = float(p[3])
        except Exception:  # noqa: BLE001
            continue
        if best is None or abs(ts - t) < abs(best[0] - t):
            best = (ts, v)
    return best[1] if best else None


def events():
    out = []
    for fn in ("macro_events.json", "macro_events_ext.json"):
        for e in json.load(open(os.path.join(ROOT, "scripts", fn)))["events"]:
            if e.get("type") in ("PCE", "CPI", "FOMC", "NFP") and e.get("status", "released") == "released" and e.get("utc"):
                t = int(datetime.strptime(e["utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
                out.append({"type": e["type"], "t": t})
    return sorted(out, key=lambda e: e["t"])


def tstat(xs):
    if len(xs) < 3:
        return None
    sd = st.stdev(xs)
    return round(st.mean(xs) / (sd / math.sqrt(len(xs))), 2) if sd else None


def main():
    outp = sys.argv[1]
    K = {s: klines(s) for s in SYMS}
    T = sorted(K["BTC"])
    print(f"BTC 1h bars {len(T)}", flush=True)
    EV = events()
    ALL = [e["t"] for e in EV]

    def px(s, t):
        k = K[s]
        tt = (t // H) * H
        v = k.get(tt)
        return v[0] if v else None

    def lr(s, a, b):
        x, y = px(s, a), px(s, b)
        return math.log(y / x) * 100 if x and y else None

    def ret(asset, a, b):
        if asset == "BTC":
            return lr("BTC", a, b)
        xs = [lr(s, a, b) for s in SYMS[1:]]
        xs = [x for x in xs if x is not None]
        return st.mean(xs) if len(xs) >= 6 else None

    def quiet(t):
        return all(abs(t - u) > 72 * H for u in ALL)

    def base_times(t):
        return [t + k * 7 * DAY for k in list(range(-12, 0)) + list(range(1, 13)) if quiet(t + k * 7 * DAY)]

    def study(typ, shift=None):
        res = {}
        evs = [e for e in EV if e["type"] == typ and e["t"] > T[0] + 30 * DAY and e["t"] < T[-1] - 4 * DAY]
        for a in ("BTC", "basket"):
            for w in WIN:
                diffs, by = [], {}
                for e in evs:
                    t = e["t"] + (shift(e) if shift else 0)
                    r = ret(a, t + w[0] * H, t + w[1] * H)
                    bs = [x for x in (ret(a, b + w[0] * H, b + w[1] * H) for b in base_times(t)) if x is not None]
                    if r is None or len(bs) < 3:
                        continue
                    d = r - st.mean(bs)
                    diffs.append(d)
                    by.setdefault(time.gmtime(t / 1000).tm_year, []).append(d)
                res[f"{a} {w[0]}..{w[1]}h"] = {"n": len(diffs), "mean": round(st.mean(diffs), 3) if diffs else None, "t": tstat(diffs),
                                               "by_year": {y: round(st.mean(v), 2) for y, v in sorted(by.items())}}
        return res, len(evs)
    O = {"pce": {}, "cpi": {}, "fomc": {}}
    for typ, key in (("PCE", "pce"), ("CPI", "cpi"), ("FOMC", "fomc")):
        r, n = study(typ)
        O[key] = {"events": n, "windows": r}
    rnd = random.Random(7)
    plc = {}
    for k in range(20):
        sh = {}
        r, _ = study("PCE", shift=lambda e: sh.setdefault(e["t"], rnd.choice([-1, 1]) * rnd.randint(7, 21) * DAY))
        for w, v in r.items():
            plc.setdefault(w, []).append(abs(v["t"] or 0))
    O["pce"]["placebo_share_ge"] = {w: round(sum(x >= abs(O["pce"]["windows"][w]["t"] or 0) for x in v) / len(v), 2) for w, v in plc.items()}
    # squeeze condition
    F = funding()
    FT = [t for t, _ in F]

    def fund_avg(a, b):
        i, j = bisect.bisect_left(FT, a), bisect.bisect_right(FT, b)
        xs = [F[k][1] for k in range(i, j)]
        return st.mean(xs) if xs else None
    sq, nsq = {"24": [], "48": []}, {"24": [], "48": []}
    rows_sq = []
    for e in EV:
        if e["type"] not in ("CPI", "PCE") or e["t"] > T[-1] - 3 * DAY or e["t"] < T[0] + 3 * DAY:
            continue
        t = e["t"]
        up = lr("BTC", t - 48 * H, t - H)
        f1, f0 = fund_avg(t - 24 * H, t), fund_avg(t - 48 * H, t - 24 * H)
        o1, o0 = oi_at(t - H), oi_at(t - 48 * H)
        oiu = (o1 / o0 - 1) * 100 if o1 and o0 else None
        cond = bool(up is not None and up >= 2 and f1 is not None and f0 is not None and f1 > f0 and f1 > 0 and oiu is not None and oiu >= 3)
        r24, r48 = lr("BTC", t, t + 24 * H), lr("BTC", t, t + 48 * H)
        if r24 is None or r48 is None:
            continue
        (sq if cond else nsq)["24"].append(r24)
        (sq if cond else nsq)["48"].append(r48)
        if cond:
            rows_sq.append([time.strftime("%Y-%m-%d", time.gmtime(t / 1000)), e["type"], round(up, 1), round(oiu, 1), round(r24, 2), round(r48, 2)])

    def welch(a, b):
        if len(a) < 2 or len(b) < 2:
            return None
        return round((st.mean(a) - st.mean(b)) / math.sqrt(st.variance(a) / len(a) + st.variance(b) / len(b)), 2)
    O["squeeze"] = {k: {"cond_n": len(sq[k]), "cond_mean": round(st.mean(sq[k]), 2) if sq[k] else None,
                        "cond_down_share": round(sum(x < 0 for x in sq[k]) / len(sq[k]), 2) if sq[k] else None,
                        "other_n": len(nsq[k]), "other_mean": round(st.mean(nsq[k]), 2) if nsq[k] else None, "welch_t": welch(sq[k], nsq[k])}
                    for k in ("24", "48")}
    O["squeeze"]["cases"] = rows_sq
    # Thanksgiving week
    tg, other = [], []
    for y in range(2021, 2026):
        d = datetime(y, 11, 1, tzinfo=timezone.utc)
        th = [d + timedelta(days=i) for i in range(30) if (d + timedelta(days=i)).weekday() == 3][3]
        mon = th - timedelta(days=3)
        a = int(mon.timestamp() * 1000)
        r = lr("BTC", a, a + 7 * DAY)
        if r is not None:
            tg.append([y, round(r, 2)])
    for t in range(int(datetime(2021, 1, 4, tzinfo=timezone.utc).timestamp() * 1000), T[-1] - 7 * DAY, 7 * DAY):
        r = lr("BTC", t, t + 7 * DAY)
        if r is not None:
            other.append(r)
    O["thanksgiving"] = {"weeks": tg, "mean": round(st.mean([x for _, x in tg]), 2) if tg else None, "all_weeks_mean": round(st.mean(other), 2),
                         "all_weeks_sd": round(st.stdev(other), 2), "rank_pct": [round(sum(o < x for o in other) / len(other) * 100) for _, x in tg]}
    # macro-bias flag
    c4 = {}
    for t in T:
        k = (t // (4 * H)) * 4 * H
        o, h, l, c = K["BTC"][t]
        if k not in c4:
            c4[k] = [o, h, l, c]
        else:
            c4[k][1], c4[k][2], c4[k][3] = max(c4[k][1], h), min(c4[k][2], l), c
    k4 = sorted(c4)
    ema, E = None, {}
    for k in k4:
        ema = c4[k][3] if ema is None else ema + (c4[k][3] - ema) * 2 / 21
        E[k] = ema
    fl, nfl = [], []
    for e in EV:
        if e["type"] not in ("CPI", "PCE", "FOMC") or e["t"] > T[-1] - 2 * DAY or e["t"] < T[0] + 10 * DAY:
            continue
        t = e["t"]
        flag = False
        for k in [x for x in k4 if t - 24 * H <= x + 4 * H <= t]:
            i = k4.index(k)
            if i > 0 and c4[k][3] < E[k] and c4[k][1] < c4[k4[i - 1]][1]:
                flag = True
        r = lr("BTC", t, t + 24 * H)
        if r is not None:
            (fl if flag else nfl).append(r)
    O["macro_bias"] = {"flagged_n": len(fl), "flagged_mean_0_24": round(st.mean(fl), 2) if fl else None,
                       "flagged_down_share": round(sum(x < 0 for x in fl) / len(fl), 2) if fl else None,
                       "other_n": len(nfl), "other_mean_0_24": round(st.mean(nfl), 2) if nfl else None, "welch_t": welch(fl, nfl)}
    json.dump(O, open(outp, "w"), ensure_ascii=False, indent=1)
    print("::notice title=pce windows::" + json.dumps(O["pce"], ensure_ascii=False)[:4000])
    print("::notice title=cpi fomc::" + json.dumps({"cpi": {k: {x: v[x] for x in ("n", "mean", "t")} for k, v in O["cpi"]["windows"].items()},
                                                     "fomc": {k: {x: v[x] for x in ("n", "mean", "t")} for k, v in O["fomc"]["windows"].items()}})[:4000])
    print("::notice title=squeeze::" + json.dumps(O["squeeze"], ensure_ascii=False)[:4000])
    print("::notice title=thanksgiving + macro bias::" + json.dumps({"thanksgiving": O["thanksgiving"], "macro_bias": O["macro_bias"]}, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
