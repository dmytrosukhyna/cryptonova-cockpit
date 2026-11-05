"""Binance lead traders: copy with delay, stop distance / leverage and the time a copy is held (05.11.2026, owner: «Binance надо
срочно исследовать … продумай всю систему»). Research only — reads the whale study's database (Actions cache), writes nothing to it.

Data: ws_trips rows with src='bn' (closed Binance lead-portfolio positions, ≤ 92 days, cnq/agents/whale_sources.py) + Binance
USDT-M 5-minute candles and funding from data.binance.vision (public archive). Decision = entries of one trader in the same coin + side
within 4 h (as in the OKX/HL studies). Copy: enter at the open of the first 5m bar starting ≥ 2 min after the whale (2–7 min late),
exit at the open of the first bar ≥ 2 min after the whale's close, costs 0.11 % a round trip + funding, stop checked on 5m bars.
Size by risk → result in R = return ÷ stop distance.

Pre-registered (8 variants): stop −4 % (≈ the whale's 20×), −5, −8, −12, −16 (now), −20, «как у кита» (80 ÷ his leverage, 4…20 %),
and «без стопа» (exit only with the whale; reported in % and in R at a 16 % risk unit, for comparison only).
Pools: every trader with ≥ 20 decisions; walk-forward «good» = win ≥ 70 % and PF ≥ 2 on his own decisions in the first half, judged on
the second half only. Also reported: how long the copies were held (does any time limit make sense?).
Usage: bn_whale_stops.py DB OUT.json"""
import calendar, io, json, math, os, sqlite3, statistics as st, sys, time, urllib.request, zipfile
from collections import defaultdict

M5, H, DAY = 300_000, 3_600_000, 86_400_000
COST = 0.11
ARC = "https://data.binance.vision/data/futures/um"


def get(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-research"}), timeout=60) as r:
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
    out = []
    for line in z.read(z.namelist()[0]).decode().splitlines():
        p = line.split(",")
        if p and p[0][:1].isdigit():
            out.append(p)
    return out


def months(t0, t1):
    y, m = time.gmtime(t0 / 1000)[:2]
    y1, m1 = time.gmtime(t1 / 1000)[:2]
    while (y, m) <= (y1, m1):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def load_sym(sym, t0, t1):
    k = {}
    fund = []
    cur_m = time.gmtime(time.time())[:2]
    for y, m in months(t0, t1):
        if (y, m) == cur_m:
            # the current month has no monthly file yet: daily files
            d0 = calendar.timegm((y, m, 1, 0, 0, 0)) * 1000
            t = d0
            while t <= t1 and t < time.time() * 1000 - DAY:
                ds = time.strftime("%Y-%m-%d", time.gmtime(t / 1000))
                for p in csv_rows(get(f"{ARC}/daily/klines/{sym}/5m/{sym}-5m-{ds}.zip")):
                    k[int(p[0])] = (float(p[1]), float(p[2]), float(p[3]), float(p[4]))
                t += DAY
            continue
        tag = f"{y}-{m:02d}"
        for p in csv_rows(get(f"{ARC}/monthly/klines/{sym}/5m/{sym}-5m-{tag}.zip")):
            k[int(p[0])] = (float(p[1]), float(p[2]), float(p[3]), float(p[4]))
        for p in csv_rows(get(f"{ARC}/monthly/fundingRate/{sym}/{sym}-fundingRate-{tag}.zip")):
            try:
                fund.append((int(p[0]), float(p[2])))
            except Exception:  # noqa: BLE001
                pass
    if not k:
        return None
    ts = sorted(k)
    return {"t": ts, "k": k, "fund": sorted(fund)}


def bar_at(S, t):
    """first 5m bar starting at or after t"""
    import bisect
    i = bisect.bisect_left(S["t"], t)
    return i if i < len(S["t"]) else None


def copy_ret(S, side, t_in, t_out, stop):
    i, j = bar_at(S, t_in), bar_at(S, t_out)
    if i is None or j is None or j <= i:
        return None
    s = 1 if side == "LONG" else -1
    a = S["k"][S["t"][i]][0]
    b = S["k"][S["t"][j]][0]
    if stop:
        lvl = a * (1 - s * stop / 100)
        for q in range(i, j):
            o, h, l, c = S["k"][S["t"][q]]
            if (s > 0 and l <= lvl) or (s < 0 and h >= lvl):
                b = o if q > i and ((s > 0 and o < lvl) or (s < 0 and o > lvl)) else lvl
                t_out = S["t"][q]
                break
    f = sum(r for tt, r in S["fund"] if S["t"][i] < tt <= t_out) * 100 * s       # longs pay positive funding
    return s * (b / a - 1) * 100 - COST - f


def stat(xs):
    if len(xs) < 2:
        return {"n": len(xs)}
    m = st.mean(xs); sd = st.pstdev(xs); eq = pk = dd = 0.0
    for x in xs:
        eq += x; pk = max(pk, eq); dd = min(dd, eq - pk)
    return {"n": len(xs), "R": round(m, 4), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None, "sumR": round(sum(xs), 1),
            "ddR": round(dd, 1), "win": round(sum(x > 0 for x in xs) / len(xs), 3), "full_loss": round(sum(x <= -0.99 for x in xs) / len(xs), 3)}


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    names = {}
    for a, lb in c.execute("SELECT addr, lb FROM ws_whale WHERE addr LIKE 'bn:%'"):
        try:
            names[a] = (json.loads(lb or "{}") or {}).get("name") or a
        except Exception:  # noqa: BLE001
            names[a] = a
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, entry, exit, pnl, notional, lever FROM ws_trips WHERE src='bn' AND close_t > 0 "
                     "ORDER BY trader, open_t").fetchall()
    print(f"bn closed positions: {len(rows)}, traders: {len({r[0] for r in rows})}", flush=True)
    eps = []
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    for tr, lst in by.items():
        cur = {}
        mine = []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H:
                e["g"].append(r)
            else:
                e = {"trader": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        if len(mine) >= 20:
            eps += mine
    for e in eps:
        g = e["g"]; w = [max(x[8] or 0, 1e-9) for x in g]
        e["close_t"] = max(x[4] for x in g)
        s = 1 if e["side"] == "LONG" else -1
        e["wret"] = sum(s * (x[6] / x[5] - 1) * 100 * wi for x, wi in zip(g, w) if x[5] and x[6]) / sum(w)
        e["lev"] = g[0][9] or None
        e["hold_h"] = (e["close_t"] - e["open_t"]) / H
    print(f"decisions (traders with ≥ 20): {len(eps)}", flush=True)
    syms = defaultdict(list)
    for e in eps:
        syms[e["coin"] + "USDT"].append(e)
    data = {}
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        t0 = min(x["open_t"] for x in es) - DAY; t1 = max(x["close_t"] for x in es) + DAY
        data[sym] = load_sym(sym, t0, t1)
        if n % 10 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if data[sym] else 'none'}", flush=True)
    VAR = {"S4": 4, "S5": 5, "S8": 8, "S12": 12, "S16": 16, "S20": 20, "SW": None, "NONE": 0,
           # 05.11 second run (after seeing that copies held > 3 days lost — so stricter: must help in BOTH halves and on good_h2):
           # the −16 % stop plus a time exit after 24 / 48 / 72 / 120 h
           "S16_T24": 16, "S16_T48": 16, "S16_T72": 16, "S16_T120": 16}
    res = []
    for e in eps:
        S = data.get(e["coin"] + "USDT")
        if not S:
            continue
        r = {"trader": e["trader"], "t": e["open_t"], "wret": e["wret"], "hold_h": e["hold_h"]}
        ok = True
        for k, sv in VAR.items():
            if k == "SW":
                sv = min(20, max(4, 80 / e["lev"])) if e["lev"] else 16
            t_out = e["close_t"] + 120_000
            if "_T" in k:
                t_out = min(t_out, e["open_t"] + 120_000 + int(k.split("_T")[1]) * H)
            v = copy_ret(S, e["side"], e["open_t"] + 120_000, t_out, sv)
            if v is None or not math.isfinite(v):
                ok = False; break
            r[k] = v / (sv or 16)
            if k == "NONE":
                r["NONE_pct"] = v
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
    O = {"n": len(res), "traders": len({r["trader"] for r in res}), "good": [names.get(g, g) for g in sorted(good)],
         "from": time.strftime("%Y-%m-%d", time.gmtime(res[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(res[-1]["t"] / 1000))}
    parts = {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid, "good_h2": lambda r: r["t"] >= mid and r["trader"] in good}
    for p, f in parts.items():
        rs = [r for r in res if f(r)]
        O[p] = {k: stat([r[k] for r in rs]) for k in VAR}
        O[p]["NONE_pct"] = stat([r["NONE_pct"] for r in rs])
        O[p]["whale_pct"] = stat([r["wret"] - COST for r in rs])
    hs = sorted(r["hold_h"] for r in res)
    O["hold_h"] = {q: round(hs[int(q / 100 * (len(hs) - 1))], 1) for q in (10, 25, 50, 75, 90, 99)}
    # does a time limit help? the S16 copy result by hold length (known only afterwards — descriptive, not a rule)
    O["by_hold_S16"] = {b: stat([r["S16"] for r in res if lo <= r["hold_h"] < hi]) for b, (lo, hi) in
                        {"<4h": (0, 4), "4-24h": (4, 24), "1-3d": (24, 72), "3-7d": (72, 168), ">7d": (168, 1e9)}.items()}
    per = defaultdict(list)
    for r in res:
        per[r["trader"]].append(r)
    O["per_trader"] = sorted([{"name": names.get(t, t), "n": len(v), "S8": round(sum(x["S8"] for x in v), 1), "S16": round(sum(x["S16"] for x in v), 1),
                               "whale_pct_mean": round(st.mean(x["wret"] for x in v), 2)} for t, v in per.items()], key=lambda d: -d["S16"])
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=bn n::{O['n']} decisions, {O['traders']} traders, {O['from']} → {O['to']}, good(h1)={len(good)}")
    for p in parts:
        line = " ".join(f"{k}:{v.get('R')}/{v.get('t')}/{v.get('ddR')}" for k, v in O[p].items() if k in VAR)
        print(f"::notice title=bn {p}::{line} | none%={O[p]['NONE_pct'].get('R')} whale%={O[p]['whale_pct'].get('R')}")
    print(f"::notice title=bn hold::{O['hold_h']} | S16 by hold: " + " ".join(f"{b}:{v.get('n')}/{v.get('R')}" for b, v in O["by_hold_S16"].items()))
    print("::notice title=bn traders::" + " | ".join(f"{d['name'][:12]} {d['n']} S8 {d['S8']} S16 {d['S16']}" for d in O["per_trader"][:15]))


if __name__ == "__main__":
    main()
