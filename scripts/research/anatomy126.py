"""Backlog 126 — own CRYPTO NOVA strategy from the «anatomy» of the locked whales' decisions (10.11.2026, research only).

Step 1 (description): every decision (fills of one coin/side within 4 h = one decision, as bn_whale_stops.py) of the traders on
 the locked list (web/whales_top.json → following, src bn/okx) gets its features at entry, from DAILY Binance USDT-M bars that
 closed before the entry: BTC above its SMA200, distance to the 20-day high and low in ATR14, momentum 3/7/28 days, UTC hour,
 hold time, leverage. Funding/OI at entry are not in the archive at this resolution — not used.
Step 2 (rules, pre-registered on 10.11 before any result was looked at; parameters are taken from step 1 mechanically):
 for each side s (LONG / SHORT) separately, from the whales' s-decisions:
  R1 regime   : BTC regime (above/below SMA200) equals the regime of the majority of whale s-decisions
  R2 position : distance to the 20-day high (ATR) inside the whales' s inter-quartile range
  R3 impulse  : 7-day momentum inside the whales' s inter-quartile range
  R4 all      : R1 and R2 and R3
 Entry at the next daily open after the signal day; exit after H days (H = whales' median hold rounded up to whole days, ≥ 1)
 or at a −16 % stop (daily high/low); one open trade per coin and side; costs 0.11 % round trip + real funding.
Step 3 (test without the whales): 2022-01-01 … the day before the first whale decision in the data (dates outside their trades),
 coins = whale-traded coins + a fixed list of other coins incl. delisted ones (FTT, SRM, LUNA …).
 8 variants (4 rules × 2 sides). Passes only if: mean net per trade > 0 with t ≥ 2.7 (Bonferroni 8 at 5 % two-sided ≈ 2.73;
 t over entry dates, divided by √H for overlap) in BOTH halves of the test period, AND the mean beats the unconditional same-side
 trade (every coin-day) in both halves. Reported also: whale-coins and other-coins separately.
Usage: anatomy126.py DB OUT.json
"""
import bisect, calendar, io, json, math, sqlite3, statistics as st, sys, time, urllib.request, zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

DAY, H = 86_400_000, 3_600_000
ARC = "https://data.binance.vision/data/futures/um"
COST, STOP = 0.11, 16.0
OTHER = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "BCH", "TRX", "ATOM", "NEAR", "APT", "ARB",
         "OP", "SUI", "INJ", "FIL", "ETC", "UNI", "AAVE", "XLM", "HBAR", "TIA", "SEI", "WIF", "1000PEPE", "ORDI", "RUNE", "GALA",
         "SAND", "MANA", "AXS", "CRV", "DYDX", "LDO", "STX", "FTT", "SRM", "LUNA", "ANT", "BLZ", "CVC", "WAVES", "OMG"]


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-research"}), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1 + i)
    return None


def rows(blob):
    if not blob: return []
    z = zipfile.ZipFile(io.BytesIO(blob)); out = []
    for ln in z.read(z.namelist()[0]).decode().splitlines():
        p = ln.split(",")
        if p and p[0][:1].isdigit(): out.append(p)
    return out


def months(t0, t1):
    y, m = time.gmtime(t0 / 1000)[:2]
    while (y, m) <= time.gmtime(t1 / 1000)[:2]:
        yield y, m
        m += 1
        if m > 12: y, m = y + 1, 1


def load(coin, t0, t1):
    sym = coin + "USDT"; k = {}; fund = []; cur = time.gmtime()[:2]
    for y, m in months(t0, t1):
        if (y, m) == cur:
            t = calendar.timegm((y, m, 1, 0, 0, 0)) * 1000
            while t < time.time() * 1000 - DAY:
                for p in rows(get(f"{ARC}/daily/klines/{sym}/1d/{sym}-1d-{time.strftime('%Y-%m-%d', time.gmtime(t / 1000))}.zip")):
                    k[int(p[0])] = tuple(map(float, p[1:5]))
                t += DAY
            continue
        tag = f"{y}-{m:02d}"
        for p in rows(get(f"{ARC}/monthly/klines/{sym}/1d/{sym}-1d-{tag}.zip")): k[int(p[0])] = tuple(map(float, p[1:5]))
        for p in rows(get(f"{ARC}/monthly/fundingRate/{sym}/{sym}-fundingRate-{tag}.zip")):
            try: fund.append((int(p[0]), float(p[2])))
            except Exception: pass  # noqa: BLE001,E701
    if len(k) < 60: return None
    t = sorted(k)
    return {"t": t, "o": [k[x][0] for x in t], "h": [k[x][1] for x in t], "l": [k[x][2] for x in t], "c": [k[x][3] for x in t],
            "ft": [f[0] for f in sorted(fund)], "fr": [f[1] for f in sorted(fund)]}


def feats(S, i, btc):
    """features from bars 0..i (bar i closed); None if not enough history"""
    if i < 29: return None
    c, h, l = S["c"], S["h"], S["l"]
    tr = [max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1])) for j in range(i - 13, i + 1)]
    atr = sum(tr) / 14
    if atr <= 0: return None
    f = {"d20h": (max(h[i - 19:i + 1]) - c[i]) / atr, "d20l": (c[i] - min(l[i - 19:i + 1])) / atr,
         "m3": c[i] / c[i - 3] - 1, "m7": c[i] / c[i - 7] - 1, "m28": c[i] / c[i - 28] - 1}
    bi = bisect.bisect_right(btc["t"], S["t"][i]) - 1
    if bi < 199: return None
    f["btc_up"] = btc["c"][bi] > sum(btc["c"][bi - 199:bi + 1]) / 200
    return f


def trade(S, i, side, Hd):
    """enter at open of bar i+1, exit at close of bar i+Hd or at the stop"""
    if i + Hd >= len(S["t"]): return None
    s = 1 if side == "LONG" else -1; a = S["o"][i + 1]; lvl = a * (1 - s * STOP / 100); b = S["c"][i + Hd]; t_out = S["t"][i + Hd] + DAY
    for q in range(i + 1, i + Hd + 1):
        if (s > 0 and S["l"][q] <= lvl) or (s < 0 and S["h"][q] >= lvl):
            b = lvl; t_out = S["t"][q] + DAY; break
    lo, hi = bisect.bisect_right(S["ft"], S["t"][i + 1]), bisect.bisect_right(S["ft"], t_out)
    f = sum(S["fr"][lo:hi]) * 100 * s
    return s * (b / a - 1) * 100 - COST - f


def q(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))]


def tstat(tr, Hd):
    by = defaultdict(list)
    for d, r in tr: by[d].append(r)
    v = [sum(x) / len(x) for x in by.values()]
    if len(v) < 10: return None, len(tr)
    sd = st.pstdev(v)
    return (st.mean(v) / (sd / math.sqrt(len(v))) / math.sqrt(Hd) if sd > 0 else None), len(tr)


def main():
    db, out = sys.argv[1], sys.argv[2]
    fol = json.load(open("web/whales_top.json")).get("following") or []
    ids = {f["addr"]: f.get("name") or f["addr"] for f in fol if f.get("src") in ("bn", "okx")}
    c = sqlite3.connect(db)
    raw = c.execute("SELECT trader, coin, side, open_t, close_t, pnl, lever FROM ws_trips WHERE close_t > 0 AND trader IN (%s) "
                    "ORDER BY trader, open_t" % ",".join("?" * len(ids)), list(ids)).fetchall()
    dec = []
    by = defaultdict(list)
    for r in raw: by[r[0]].append(r)
    for tr, lst in by.items():
        cur = {}
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H:
                e["pnl"] += r[5] or 0; e["close_t"] = max(e["close_t"], r[4])
            else:
                e = {"trader": tr, "coin": r[1].upper(), "side": r[2].upper(), "open_t": r[3], "close_t": r[4], "pnl": r[5] or 0, "lev": r[6]}
                cur[k] = e; dec.append(e)
    print(f"locked traders {len(ids)}, with trips {len(by)}, decisions {len(dec)}", flush=True)
    if not dec:
        json.dump({"error": "no decisions"}, open(out, "w")); print("::error title=126::no decisions"); return
    first = min(d["open_t"] for d in dec); now = int(time.time() * 1000)
    t0 = calendar.timegm((2021, 6, 1, 0, 0, 0)) * 1000
    wcoins = sorted({d["coin"] for d in dec})
    coins = sorted(set(wcoins) | set(OTHER))
    with ThreadPoolExecutor(16) as ex:
        data = dict(zip(coins, ex.map(lambda x: load(x, t0, now), coins)))
    data = {k: v for k, v in data.items() if v}
    btc = data.get("BTC")
    print(f"coins with data {len(data)} of {len(coins)}", flush=True)
    # step 1: anatomy
    an = {"LONG": [], "SHORT": []}
    for d in dec:
        S = data.get(d["coin"])
        if not S: continue
        i = bisect.bisect_right(S["t"], d["open_t"] - DAY) - 1   # last daily bar fully closed before entry
        f = feats(S, i, btc) if i >= 0 else None
        if not f or d["side"] not in an: continue
        f.update(hour=time.gmtime(d["open_t"] / 1000).tm_hour, hold_h=(d["close_t"] - d["open_t"]) / H, lev=d["lev"], win=d["pnl"] > 0)
        an[d["side"]].append(f)
    desc, rules = {}, {}
    for s, L in an.items():
        if len(L) < 10: continue
        dd = {"n": len(L), "win": sum(x["win"] for x in L) / len(L), "btc_up_share": sum(x["btc_up"] for x in L) / len(L)}
        for k in ("d20h", "d20l", "m3", "m7", "m28", "hold_h", "hour"):
            v = [x[k] for x in L]; dd[k] = [round(q(v, .25), 4), round(q(v, .5), 4), round(q(v, .75), 4)]
        for k in ("d20h", "m7"):
            dd[k + "_win_vs_loss"] = [round(st.median([x[k] for x in L if x["win"]] or [0]), 4), round(st.median([x[k] for x in L if not x["win"]] or [0]), 4)]
        desc[s] = dd
        rules[s] = {"regime_up": dd["btc_up_share"] >= 0.5, "d20h": dd["d20h"][::2], "m7": dd["m7"][::2],
                    "H": max(1, math.ceil(dd["hold_h"][1] / 24))}
    print("anatomy", json.dumps(desc, ensure_ascii=False), flush=True)
    # step 3: test without the whales
    end = first - DAY
    mid = calendar.timegm((2024, 6, 1, 0, 0, 0)) * 1000
    res = {}
    for s, R in rules.items():
        Hd = R["H"]
        bucket = {k: {"all": [], "wh": [], "oth": []} for k in ("BASE", "R1", "R2", "R3", "R4")}
        for coin, S in data.items():
            busy = {k: -1 for k in bucket}
            for i in range(len(S["t"]) - Hd - 1):
                if S["t"][i] < calendar.timegm((2022, 1, 1, 0, 0, 0)) * 1000 - DAY or S["t"][i + Hd] + DAY > end: continue
                f = feats(S, i, btc)
                if not f: continue
                ok = {"BASE": True, "R1": f["btc_up"] == R["regime_up"], "R2": R["d20h"][0] <= f["d20h"] <= R["d20h"][1],
                      "R3": R["m7"][0] <= f["m7"] <= R["m7"][1]}
                ok["R4"] = ok["R1"] and ok["R2"] and ok["R3"]
                r = None
                for k, v in ok.items():
                    if not v or i <= busy[k]: continue
                    r = trade(S, i, s, Hd) if r is None else r
                    if r is None: break
                    busy[k] = i + Hd
                    item = (S["t"][i], r)
                    bucket[k]["all"].append(item); bucket[k]["wh" if coin in wcoins else "oth"].append(item)
        out_s = {}
        for k, b in bucket.items():
            row = {}
            for g, tr in b.items():
                for half, sel in (("h1", [x for x in tr if x[0] < mid]), ("h2", [x for x in tr if x[0] >= mid]), ("full", tr)):
                    if not sel: continue
                    t, n = tstat(sel, Hd)
                    row[f"{g}_{half}"] = {"n": n, "mean": round(st.mean(x[1] for x in sel), 3), "t": round(t, 2) if t is not None else None,
                                          "winr": round(sum(1 for x in sel if x[1] > 0) / n, 3)}
            out_s[k] = row
        for k in ("R1", "R2", "R3", "R4"):
            a, base = out_s[k], out_s["BASE"]
            ok = all(a.get(f"all_{h}") and a[f"all_{h}"]["mean"] > 0 and (a[f"all_{h}"]["t"] or 0) >= 2.7
                     and a[f"all_{h}"]["mean"] > base[f"all_{h}"]["mean"] for h in ("h1", "h2"))
            out_s[k]["pass"] = ok
            print(f"::notice title=126 {s} {k}::" + " | ".join(
                f"{g}: n {a[g]['n']} mean {a[g]['mean']}% t {a[g]['t']} (base {base[g]['mean']}%)" for g in ("all_h1", "all_h2", "wh_full", "oth_full")
                if g in a and g in base) + f" → {'ПРОШЛО' if ok else 'не прошло'}", flush=True)
        res[s] = {"H_days": Hd, "rule": R, "table": out_s}
    print("::notice title=126 anatomy::" + json.dumps({s: {k: v for k, v in d.items() if k in ("n", "win", "btc_up_share", "d20h", "m7", "hold_h")}
                                                       for s, d in desc.items()}, ensure_ascii=False))
    print(f"::notice title=126 data::decisions {len(dec)} (locked {len(ids)} traders), coins {len(data)}, test 2022-01-01…"
          f"{time.strftime('%Y-%m-%d', time.gmtime(end / 1000))}, halves split 2024-06-01, 8 variants")
    json.dump({"decisions": len(dec), "anatomy": desc, "results": res, "test_end": end}, open(out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
