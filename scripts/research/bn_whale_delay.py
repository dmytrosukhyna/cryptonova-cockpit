"""Backlog 88(в), 06.11.2026: what does each minute of copy delay cost on Binance lead traders? 1-minute candles.

Research only — reads ws_trips (src='bn') from the whale study's database (Actions cache), writes nothing to it.
Data: Binance USDT-M 1-minute klines + funding (data.binance.vision). Decision = one trader's entries in the same coin + side within 4 h
(as in bn_whale_stops.py). Copy: entry at the open of the first 1m bar starting ≥ whale open + d, exit at the open of the first 1m bar
starting ≥ whale close + d (so the effective delay is d … d + 1 min, ≈ d + 0.5 on average), stop −16 %, 0.11 % a round trip + funding.

Pre-registered (written before any result was seen):
- delays d = 0, 0.5, 1, 2, 3, 5, 10 min (same delay on entry and exit);
- variant «окно»: the live join window — no copy when at the entry the price is outside −3 % … +1 % of the whale's entry (side-adjusted);
- main question, paired per decision: Δ(0 − 2 min) and Δ(0.5 − 2 min) and Δ(2 − 5 min). «Speed matters» only if the paired Δ > 0 with
  t ≥ 2.5 (3 looks × 2 variants) on all decisions AND > 0 in both halves of time. Engineering conclusion only — no money rule changes.
Usage: bn_whale_delay.py DB OUT.json"""
import bisect, calendar, io, json, math, sqlite3, statistics as st, sys, time, urllib.request, zipfile
from collections import defaultdict

M1, H, DAY = 60_000, 3_600_000, 86_400_000
COST, STOP = 0.11, 16.0
ARC = "https://data.binance.vision/data/futures/um"
DELAYS = [0, 0.5, 1, 2, 3, 5, 10]
PAIRS = [(0, 2), (0.5, 2), (2, 5)]


def get(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-research"}), timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2 * (k + 1))
    return None


def csv_rows(blob):
    if not blob:
        return []
    z = zipfile.ZipFile(io.BytesIO(blob))
    return [p for p in (ln.split(",") for ln in z.read(z.namelist()[0]).decode().splitlines()) if p and p[0][:1].isdigit()]


def days(t0, t1):
    t = (t0 // DAY) * DAY
    while t <= t1:
        yield t
        t += DAY


def load_sym(sym, spans):
    """1m bars only for the days that the decisions of this symbol cover (daily files; monthly when a month is mostly needed)"""
    need = set()
    for a, b in spans:
        need.update(days(a - 15 * M1, b + 15 * M1))
    now = time.time() * 1000
    k, fund = {}, []
    by_m = defaultdict(set)
    for d in need:
        if d < now - DAY:
            by_m[time.gmtime(d / 1000)[:2]].add(d)
    cur = time.gmtime(now / 1000)[:2]
    for (y, m), ds in sorted(by_m.items()):
        tag = f"{y}-{m:02d}"
        if (y, m) != cur and len(ds) >= 12:
            for p in csv_rows(get(f"{ARC}/monthly/klines/{sym}/1m/{sym}-1m-{tag}.zip")):
                k[int(p[0])] = (float(p[1]), float(p[2]), float(p[3]))
        else:
            for d in sorted(ds):
                s = time.strftime("%Y-%m-%d", time.gmtime(d / 1000))
                for p in csv_rows(get(f"{ARC}/daily/klines/{sym}/1m/{sym}-1m-{s}.zip")):
                    k[int(p[0])] = (float(p[1]), float(p[2]), float(p[3]))
        if (y, m) != cur:
            for p in csv_rows(get(f"{ARC}/monthly/fundingRate/{sym}/{sym}-fundingRate-{tag}.zip")):
                try:
                    fund.append((int(p[0]), float(p[2])))
                except Exception:  # noqa: BLE001
                    pass
    if not k:
        return None
    return {"t": sorted(k), "k": k, "fund": sorted(fund)}


def copy(S, side, t_in, t_out, w_entry=None, window=False):
    T = S["t"]
    i, j = bisect.bisect_left(T, t_in), bisect.bisect_left(T, t_out)
    if i >= len(T) or j >= len(T) or j <= i or T[i] - t_in > 2 * M1 or T[j] - t_out > 2 * M1:
        return None
    s = 1 if side == "LONG" else -1
    a = S["k"][T[i]][0]
    if window:
        if not w_entry:
            return None
        gap = s * (a / w_entry - 1) * 100
        if gap < -3 or gap > 1:
            return "skip"
    b = S["k"][T[j]][0]
    lvl = a * (1 - s * STOP / 100)
    for q in range(i, j):
        o, h, l = S["k"][T[q]]
        if (s > 0 and l <= lvl) or (s < 0 and h >= lvl):
            b = o if q > i and ((s > 0 and o < lvl) or (s < 0 and o > lvl)) else lvl
            t_out = T[q]
            break
    f = sum(r for tt, r in S["fund"] if T[i] < tt <= t_out) * 100 * s
    return s * (b / a - 1) * 100 - COST - f


def stat(xs):
    if len(xs) < 2:
        return {"n": len(xs)}
    m, sd = st.mean(xs), st.pstdev(xs)
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "pf": round(pos / neg, 2) if neg else None, "win": round(sum(x > 0 for x in xs) / len(xs), 3)}


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, entry, exit, notional FROM ws_trips WHERE src='bn' AND close_t > 0 "
                     "ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    eps = []
    for tr, lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H:
                e["g"].append(r)
            else:
                e = {"trader": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        if len(mine) >= 20:
            eps += mine
    for e in eps:
        g = e["g"]; w = [max(x[7] or 0, 1e-9) for x in g]
        e["close_t"] = max(x[4] for x in g)
        e["w_entry"] = g[0][5]
        s = 1 if e["side"] == "LONG" else -1
        e["wret"] = sum(s * (x[6] / x[5] - 1) * 100 * wi for x, wi in zip(g, w) if x[5] and x[6]) / sum(w)
    print(f"bn rows {len(rows)}, decisions (traders ≥ 20): {len(eps)}", flush=True)
    syms = defaultdict(list)
    for e in eps:
        syms[e["coin"] + "USDT"].append(e)
    data = {}
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        data[sym] = load_sym(sym, [(x["open_t"], x["close_t"]) for x in es])
        if n % 10 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if data[sym] else 'none'}", flush=True)
    res = []
    for e in eps:
        S = data.get(e["coin"] + "USDT")
        if not S:
            continue
        r = {"trader": e["trader"], "t": e["open_t"], "wret": e["wret"], "hold_min": (e["close_t"] - e["open_t"]) / M1}
        ok = True
        for d in DELAYS:
            dm = int(d * M1)
            v = copy(S, e["side"], e["open_t"] + dm, e["close_t"] + dm)
            if v is None:
                ok = False; break
            r[f"d{d}"] = v
            wv = copy(S, e["side"], e["open_t"] + dm, e["close_t"] + dm, e["w_entry"], window=True)
            r[f"w{d}"] = None if wv in (None, "skip") else wv
        if ok:
            res.append(r)
    res.sort(key=lambda r: r["t"])
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    mid = res[len(res) // 2]["t"]
    h1 = defaultdict(list)
    for r in res:
        if r["t"] < mid:
            h1[r["trader"]].append(r["wret"] - COST)
    good = {w for w, xs in h1.items() if len(xs) >= 20 and sum(x > 0 for x in xs) / len(xs) >= 0.70
            and sum(x for x in xs if x > 0) / max(1e-9, -sum(x for x in xs if x < 0)) >= 2}
    O = {"n": len(res), "traders": len({r["trader"] for r in res}), "good_h1": len(good),
         "from": time.strftime("%Y-%m-%d", time.gmtime(res[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(res[-1]["t"] / 1000))}
    parts = {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid, "good_h2": lambda r: r["t"] >= mid and r["trader"] in good,
             "short_hold<60m": lambda r: r["hold_min"] < 60}
    for p, f in parts.items():
        rs = [r for r in res if f(r)]
        O[p] = {"whale": stat([r["wret"] - COST for r in rs])}
        for d in DELAYS:
            O[p][f"d{d}"] = stat([r[f"d{d}"] for r in rs])
            O[p][f"w{d}"] = stat([r[f"w{d}"] for r in rs if r[f"w{d}"] is not None])
        for a, b in PAIRS:
            O[p][f"Δ{a}-{b}"] = stat([r[f"d{a}"] - r[f"d{b}"] for r in rs])
            both = [r for r in rs if r[f"w{a}"] is not None and r[f"w{b}"] is not None]
            O[p][f"wΔ{a}-{b}"] = stat([r[f"w{a}"] - r[f"w{b}"] for r in both])
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=delay n::{O['n']} decisions, {O['traders']} traders, {O['from']} → {O['to']}, good(h1)={len(good)}")
    for p in parts:
        q = O[p]
        line = " ".join(f"{d}m:{q[f'd{d}'].get('mean')}" for d in DELAYS)
        wl = " ".join(f"{d}m:{q[f'w{d}'].get('mean')}/{q[f'w{d}'].get('n')}" for d in DELAYS)
        dl = " ".join(f"{k}:{q[k].get('mean')}/t{q[k].get('t')}" for k in q if k.startswith("Δ") or k.startswith("wΔ"))
        print(f"::notice title=delay {p}::n={q['d0'].get('n')} whale={q['whale'].get('mean')} | {line} || окно {wl} || {dl}")


if __name__ == "__main__":
    main()
