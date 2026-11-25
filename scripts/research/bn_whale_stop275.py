"""Backlog 275 (25.11.2026): for traders who often «pass» their own liquidation, is our -16 % stop the problem?

Follow-up of 274: copies of high-trait traders (share of «passed 1/lev» copies in the previous 30 days >= 75th pct of
the first half) are not worse on average, but the path is deeper. Hypothesis: the -16 % stop cuts copies that the
trader himself later closes in profit. Research only; real rules are not touched.

Pre-registered before any result was seen:
 Book T and the trait exactly as in 274 (bn_whale_liq274: same 30-day window, >= 10 closed copies, threshold = 75th pct
   of the trait over T-bar copies of the FIRST half; the second half is out of sample). T-bar = win >= 70 %, PF >= 2.
 Copy = in 2 min after the trader, out 2 min after him, costs 0.20 % a round trip.
 Diagnostics: share of copies «stopped at -16 % by us, the trader closed in plus» for high vs rest.
 Variant W (the only candidate): for HIGH-trait copies the stop is -25 % and the size is 16/25 = 0.64 of normal
   (same money at risk per trade); all other copies unchanged (stop -16 %, full size).
   Info only (not a candidate): W0 = stop -25 % at full size for high copies (more risk per trade).
 Pass (a candidate for the owner only): in T-bar, W beats A in BOTH halves, and in the second half the paired
   difference over high copies has t >= 2; the worst high copy of W (in % of normal size) is not worse than A's.
 1 candidate x 2 groups (T-bar, T all) noted.
Usage: bn_whale_stop275.py DB OUT.json"""
import json, math, statistics as st, sys, time
from collections import defaultdict
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_liq271 as L
import bn_whale_liq274 as P
import bn_whale_stops as B

S16, S25 = 16.0, 25.0
K = S16 / S25


def copy_stop(S, side, t_in, t_out, stop):
    """-> (ret %, stopped) for a copy with the given stop %"""
    i, j = B.bar_at(S, t_in), B.bar_at(S, t_out)
    if i is None or j is None or j <= i:
        return None
    s = 1 if side == "LONG" else -1
    entry = S["k"][S["t"][i]][0]
    sp = entry * (1 - s * stop / 100)
    for q in range(i, j):
        o, h, l, c = S["k"][S["t"][q]]
        if (s > 0 and l <= sp) or (s < 0 and h >= sp):
            px = o if q > i and ((s > 0 and o < sp) or (s < 0 and o > sp)) else sp
            return L.r_(side, entry, px), True
    return L.r_(side, entry, S["k"][S["t"][j]][0]), False


def tpaired(d):
    if len(d) < 2 or not st.pstdev(d):
        return None
    return round(st.mean(d) / (st.pstdev(d) / math.sqrt(len(d))), 2)


def report(name, rows, thr):
    rows = sorted(rows, key=lambda r: r["t"])
    if not rows:
        print(f"::notice title=stop275 {name}::empty")
        return {"n": 0}
    mid = rows[len(rows) // 2]["t"]
    O = {"n": len(rows)}
    for half, sel in (("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid), ("all", lambda r: True)):
        hs = [r for r in rows if sel(r)]
        hi = [r for r in hs if r["trait"] is not None and r["trait"] >= thr]
        lo = [r for r in hs if not (r["trait"] is not None and r["trait"] >= thr)]
        A = sum(r["a16"] for r in hs)
        W = sum(r["a16"] for r in lo) + sum(r["a25"] * K for r in hi)
        W0 = sum(r["a16"] for r in lo) + sum(r["a25"] for r in hi)

        def bad(g):
            st_ = [r for r in g if r["stop16"]]
            return (round(sum(r["wh"] > 0 for r in st_) / len(g), 4) if g else None,
                    round(sum(r["wh"] > 0 for r in st_) / len(st_), 3) if st_ else None, round(len(st_) / len(g), 4) if g else None)
        bh, bl = bad(hi), bad(lo)
        O[half] = {"n_hi": len(hi), "n_rest": len(lo), "A": round(A, 2), "W": round(W, 2), "W0": round(W0, 2),
                   "t_W": tpaired([r["a25"] * K - r["a16"] for r in hi]),
                   "stop_share_hi": bh[2], "stop_share_rest": bl[2],
                   "stop_whale_plus_hi": bh[0], "stop_whale_plus_rest": bl[0],
                   "of_stops_whale_plus_hi": bh[1], "of_stops_whale_plus_rest": bl[1],
                   "worst_A_hi": round(min((r["a16"] for r in hi), default=0), 2),
                   "worst_W_hi": round(min((r["a25"] * K for r in hi), default=0), 2)}
    h1, h2 = O["h1"], O["h2"]
    O["pass_W"] = bool(h1["W"] > h1["A"] and h2["W"] > h2["A"] and (h2["t_W"] or 0) >= 2
                       and h2["worst_W_hi"] >= h2["worst_A_hi"])
    print(f"::notice title=stop275 {name}::n {O['n']} thr {round(thr, 3)} | "
          + " | ".join(f"{h}: hi {O[h]['n_hi']} stop16 {O[h]['stop_share_hi']} vs rest {O[h]['stop_share_rest']}; "
                       f"stopped+whale plus {O[h]['stop_whale_plus_hi']} vs {O[h]['stop_whale_plus_rest']} "
                       f"(of stops {O[h]['of_stops_whale_plus_hi']} vs {O[h]['of_stops_whale_plus_rest']}); "
                       f"A {O[h]['A']} W {O[h]['W']} t {O[h]['t_W']} W0 {O[h]['W0']}; worst hi A {O[h]['worst_A_hi']} W {O[h]['worst_W_hi']}"
                       for h in ("h1", "h2", "all")) + f" | pass W {O['pass_W']}")
    return O


def main():
    db, out = sys.argv[1], sys.argv[2]
    OUT = {}
    rows = L.lev_trips(db)
    now = time.time() * 1000
    for r in rows:
        if r[6] <= now - 10 * L.MIN:
            L.want(r[3], r[5] - L.H, r[6] + L.H)
    print(f"coins to load: {len(L.SPAN)}", flush=True)
    by = defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    T, hist, barset = [], {}, set()
    for tr, lst in by.items():
        xs = [(1 if r[4] == "LONG" else -1) * (r[8] / r[7] - 1) * 100 - L.RT for r in lst if r[8]]
        pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
        if bool(xs) and sum(x > 0 for x in xs) / len(xs) >= 0.70 and (neg == 0 or pos / neg >= 2):
            barset.add(tr)
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
            if len(lst) >= 20:
                c16 = copy_stop(S, r[4], r[5] + L.DELAY, r[6] + L.DELAY, S16)
                c25 = copy_stop(S, r[4], r[5] + L.DELAY, r[6] + L.DELAY, S25)
                if not c16 or not c25:
                    continue
                T.append({"t": r[5], "who": tr, "a16": c16[0], "stop16": c16[1], "a25": c25[0], "stop25": c25[1],
                          "wh": L.r_(r[4], r[7], r[8]), "bar": tr in barset})
        hist[tr] = sorted(H_)
    for r in T:
        r["trait"] = P.trait_of(hist[r["who"]], r["t"] + L.DELAY)
    Tbar = [r for r in T if r["bar"]]
    first = sorted(Tbar, key=lambda r: r["t"])
    first = [r for r in first[: len(first) // 2] if r["trait"] is not None]
    vals = sorted(r["trait"] for r in first)
    thr = vals[int(0.75 * (len(vals) - 1))] if vals else 1.0
    if thr <= 0:
        thr = min([v for v in vals if v > 0] or [1.0])
    OUT["thr"] = thr
    OUT["T_bar"] = report("T_bar %", Tbar, thr)
    OUT["T"] = report("T %", T, thr)
    print(f"::notice title=stop275 verdict::W passes T-bar: {OUT['T_bar'].get('pass_W')}; T all: {OUT['T'].get('pass_W')}")
    json.dump(OUT, open(out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
