"""Backlog 282 (26.11.2026): re-check the main «T-bar» conclusions with a point-in-time bar instead of the full-history bar.

Side result of 262: a bar computed on ALL of a trader's decisions (incl. the copied trip itself and everything after it)
nearly doubles the mean OKX copy (+1.04 % vs +0.49 % at the moment of entry).  Items 271, 274-276 used exactly such a
full-history «T-bar» (win >= 70 %, PF >= 2 on the trader's own book).  Items 253/255/263/270 already use reviews with
trailing 60-day stats only (no look-ahead) and are not re-run here.

Pre-registered before any result was seen (no new variants):
 Book T exactly as bn_whale_liq271.lev_trips (copy in +2 min, out +2 min, stop -16 %, 0.20 % a round trip).
 Universes: FULL = the old T-bar (traders with >= 20 trips, win >= 70 %, PF >= 2 on all their trips) - must reproduce
   271/275/276; PIT = at the trip's open t the trader's trips CLOSED before t and opened within 60 d before t:
   >= 20, win >= 70 %, PF >= 2, sum > 0 (as the live selection and as 262).
 The three re-checked conclusions, each with its ORIGINAL pass rule unchanged:
   271  flagged («passed his liquidation») copies worse than the rest; b = close at the flag bar - pass_b (both halves,
        worst not worse, <= 20 % of plus copies cut) - was «not passed».
   275  stop -25 % at size x0.64 for high-trait copies (trait = 274, threshold = 75th pct of the FIRST half of the
        universe) - pass_W - was «not passed».
   276  one re-entry after our -16 % stop when the price is back in the -3...+1 % window - pass_R (both halves,
        2nd-half t over days >= 2) - was «not passed».
 Plus the base level: mean copy A (-16 %) per universe and half.
 Question answered: does any sign or pass/fail flip from FULL to PIT?  A flip to «pass» would only make a demo-desk
 candidate; nothing here touches real rules.
Usage: bn_whale_pit282.py DB OUT.json"""
import bisect, contextlib, io, json, math, os, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_liq271 as L
import bn_whale_liq274 as P
import bn_whale_stop275 as S5
import bn_whale_reent276 as R6

DAY = 86_400_000
WIN = 60 * DAY


def passes(xs, need=20, sum_pos=True):
    if len(xs) < need:
        return None
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    ok = sum(x > 0 for x in xs) / len(xs) >= 0.70 and (neg == 0 or pos / neg >= 2)
    return bool(ok and (sum(xs) > 0 if sum_pos else True))


def quiet(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(*a)


def tst(xs):
    if len(xs) < 2 or not st.pstdev(xs):
        return None
    return round(st.mean(xs) / (st.pstdev(xs) / math.sqrt(len(xs))), 2)


def base(rows):
    rows = sorted(rows, key=lambda r: r["t"])
    if not rows:
        return {"n": 0}
    mid = rows[len(rows) // 2]["t"]
    h1 = [r["a16"] for r in rows if r["t"] < mid]
    h2 = [r["a16"] for r in rows if r["t"] >= mid]
    by = defaultdict(float)
    for r in rows:
        by[r["t"] // DAY] += r["a16"]
    return {"n": len(rows), "traders": len({r["who"] for r in rows}), "mean": round(st.mean(r["a16"] for r in rows), 3),
            "h1": round(st.mean(h1), 3) if h1 else None, "h2": round(st.mean(h2), 3) if h2 else None,
            "t_days": tst(list(by.values())), "stop_share": round(sum(r["stop16"] for r in rows) / len(rows), 4),
            "from": time.strftime("%Y-%m-%d", time.gmtime(rows[0]["t"] / 1000)),
            "to": time.strftime("%Y-%m-%d", time.gmtime(rows[-1]["t"] / 1000))}


def thr_first_half(rows):
    first = sorted(rows, key=lambda r: r["t"])
    first = [r for r in first[: len(first) // 2] if r["trait"] is not None]
    vals = sorted(r["trait"] for r in first)
    thr = vals[int(0.75 * (len(vals) - 1))] if vals else 1.0
    if thr <= 0:
        thr = min([v for v in vals if v > 0] or [1.0])
    return thr


def main():
    db, out = sys.argv[1], sys.argv[2]
    rows = L.lev_trips(db)
    now = time.time() * 1000
    for r in rows:
        if r[6] <= now - 10 * L.MIN:
            L.want(r[3], r[5] - L.H, r[6] + L.H)
    print(f"coins to load: {len(L.SPAN)}", flush=True)
    by = defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    T, hist = [], {}
    for n, (tr, lst) in enumerate(by.items()):
        lst.sort(key=lambda r: r[5])
        own = [(1 if r[4] == "LONG" else -1) * (r[8] / r[7] - 1) * 100 - L.RT for r in lst if r[8]]
        full = len(lst) >= 20 and bool(passes(own, need=1, sum_pos=False))
        dec = sorted(((r[6], r[5], (1 if r[4] == "LONG" else -1) * (r[8] / r[7] - 1) * 100 - L.RT) for r in lst if r[8]))
        ct = [d[0] for d in dec]
        H_ = []
        for r in lst:
            if r[6] > now - 10 * L.MIN or not r[8]:
                continue
            S = L.ser(r[3])
            if not S:
                continue
            x = L.one(S, r[4], None, r[5] + L.DELAY, r[6] + L.DELAY, r[7], float(r[10]))
            if not x:
                continue
            H_.append((r[6] + L.DELAY, r[5] + L.DELAY, int(x["flag"])))
            k = bisect.bisect_left(ct, r[5])
            pit = passes([d[2] for d in dec[:k] if d[1] >= r[5] - WIN])
            if not full and not pit:
                continue
            c16 = S5.copy_stop(S, r[4], r[5] + L.DELAY, r[6] + L.DELAY, S5.S16)
            c25 = S5.copy_stop(S, r[4], r[5] + L.DELAY, r[6] + L.DELAY, S5.S25)
            re = R6.one(S, r[4], r[5] + L.DELAY, r[6] + L.DELAY, r[7])
            if not c16 or not c25 or not re:
                continue
            row = dict(x)
            row.update(t=r[5], who=tr, src=r[0], w=1, a16=c16[0], stop16=c16[1], a25=c25[0], stop25=c25[1],
                       wh=L.r_(r[4], r[7], r[8]), full=full, pit=bool(pit),
                       stopped=re["stopped"], re=re["re"], re_t=re["re_t"], re_stopped=re["re_stopped"])
            T.append(row)
        hist[tr] = sorted(H_)
        if n % 50 == 0:
            print(f"traders {n + 1}/{len(by)} rows {len(T)}", flush=True)
    for r in T:
        r["trait"] = P.trait_of(hist[r["who"]], r["t"] + L.DELAY)
    U = {"FULL": [r for r in T if r["full"]], "PIT": [r for r in T if r["pit"]]}
    U["PIT_not_FULL"] = [r for r in U["PIT"] if not r["full"]]
    O = {"meta": {"trips": len(rows), "traders": len(by), "rows": len(T)}}
    for name in ("FULL", "PIT"):
        rs = U[name]
        b = base(rs)
        l1 = quiet(L.summ, rs, "pct")
        thr = thr_first_half(rs)
        s5 = quiet(S5.report, name, rs, thr)
        r6 = quiet(R6.report, name, rs)
        O[name] = {"base": b, "liq271": {k: v for k, v in l1.items() if k != "rows"}, "stop275": s5, "thr275": thr, "reent276": r6}
        print(f"::notice title=pit282 {name} base::n {b['n']} traders {b.get('traders')} mean A {b.get('mean')}% h1 {b.get('h1')} h2 {b.get('h2')} "
              f"t_days {b.get('t_days')} stop16 share {b.get('stop_share')} | {b.get('from')}→{b.get('to')}")
        print(f"::notice title=pit282 {name} 271::flagged {l1.get('flagged')} a mean {l1.get('flag_a_mean')}% vs unflagged {l1.get('unflag_a_mean')}% "
              f"stop-share {l1.get('flag_stop_share')} | a {l1['a']['h1']}/{l1['a']['h2']} b {l1['b']['h1']}/{l1['b']['h2']} c {l1['c']['h1']}/{l1['c']['h2']} "
              f"t_b {l1.get('t_b')} plus_cut {l1.get('plus_cut')} | pass_b {l1.get('pass_b')} pass_c {l1.get('pass_c')}")
        print(f"::notice title=pit282 {name} 275::thr {round(thr, 3)} | " + " | ".join(
            f"{h}: hi {s5[h]['n_hi']} A {s5[h]['A']} W {s5[h]['W']} t {s5[h]['t_W']} stop16 hi {s5[h]['stop_share_hi']} vs {s5[h]['stop_share_rest']}"
            for h in ("h1", "h2")) + f" | pass_W {s5.get('pass_W')}")
        print(f"::notice title=pit282 {name} 276::" + " | ".join(
            f"{h}: stops {r6[h]['stops']} re {r6[h]['re_n']} A {r6[h]['A']} R {r6[h]['R']} re_mean {r6[h]['re_mean']} t_days {r6[h]['t_days']} "
            f"double {r6[h]['double_stop_share']}" for h in ("h1", "h2")) + f" | pass_R {r6.get('pass_R')}")
    bn = base(U["PIT_not_FULL"])
    O["PIT_not_FULL"] = bn
    flips = {k: [O["FULL"][k2].get(p), O["PIT"][k2].get(p)] for k, k2, p in
             (("271_b", "liq271", "pass_b"), ("275_W", "stop275", "pass_W"), ("276_R", "reent276", "pass_R"))}
    O["flips"] = flips
    print(f"::notice title=pit282 verdict::PIT-only copies n {bn.get('n')} mean {bn.get('mean')}% | pass FULL→PIT: "
          + ", ".join(f"{k} {v[0]}→{v[1]}" for k, v in flips.items()) + f" | any flip: {any(bool(a) != bool(b) for a, b in flips.values())}")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
