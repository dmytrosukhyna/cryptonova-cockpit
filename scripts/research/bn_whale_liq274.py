"""Backlog 274 (25.11.2026): «passed his liquidation» as a TRADER trait, not an exit signal.

Follow-up of 271: copies where the price went against the trader's entry beyond 1/lev are much worse
(T-bar -0.82 % vs +1.66 %), but closing them at that moment is worse still. Here: does a trader's recent
SHARE of such copies predict his NEXT copies, so that we could size them down?
Research only; real rules are not touched.

Pre-registered before any result was seen:
 Book T = the trip book with known lever (as in 271, bn_whale_liq271.lev_trips); traders with >= 20 decisions;
   copy = in 2 min after the trader, out 2 min after him, stop -16 %, costs 0.20 % a round trip (variant a of 271).
   Sub-group T-bar = traders passing win >= 70 % and PF >= 2 on their own book (the live bar proxy).
 Trait of a copy = share of flagged copies among the SAME trader's copies that opened in the previous 30 days
   AND were already closed (copy exit) before this copy opened; needs >= 10 such copies, otherwise «no trait».
 High = trait >= the 75th percentile of the trait over T-bar copies of the FIRST half (by entry time).
   No threshold search; the second half is out of sample for the threshold.
 Variants vs A (all copies full size): H = high-trait copies at half size; S = high-trait copies skipped.
 Pass (a candidate for the owner only): in T-bar, H beats A in BOTH halves, the high group's mean copy is lower
   than the rest's in both halves, and the second-half difference has t <= -2. 2 variants x 2 groups (T-bar, T) noted.
 Book D (the desk's own journal) gets the trait from the trip book of the same trader; report only (small).
Usage: bn_whale_liq274.py DB OUT.json"""
import json, math, statistics as st, sys, time
from bisect import bisect_left
from collections import defaultdict
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_liq271 as L

DAY = 86_400_000
WIN = 30 * DAY
MINN = 10


def tstat(x, y):
    if len(x) < 2 or len(y) < 2:
        return None
    se = math.sqrt(st.pvariance(x) / len(x) + st.pvariance(y) / len(y))
    return round((st.mean(x) - st.mean(y)) / se, 2) if se else None


def trait_of(hist, t_open):
    """hist = sorted list of (copy_close_t, copy_open_t, flag) of one trader"""
    n = f = 0
    for ct, ot, fl in hist:
        if ct > t_open:
            break
        if ot >= t_open - WIN:
            n += 1
            f += fl
    return (f / n) if n >= MINN else None


def report(name, rows, thr):
    rows = sorted(rows, key=lambda r: r["t"])
    if not rows:
        print(f"::notice title=liq274 {name}::empty")
        return {"n": 0}
    mid = rows[len(rows) // 2]["t"]
    O = {"n": len(rows), "with_trait": sum(r["trait"] is not None for r in rows)}
    for half, sel in (("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid), ("all", lambda r: True)):
        hs = [r for r in rows if sel(r)]
        hi = [r["a"] * r.get("w", 1) for r in hs if r["trait"] is not None and r["trait"] >= thr]
        lo = [r["a"] * r.get("w", 1) for r in hs if not (r["trait"] is not None and r["trait"] >= thr)]
        A = sum(hi) + sum(lo)
        O[half] = {"n_hi": len(hi), "n_rest": len(lo), "mean_hi": round(st.mean(hi), 3) if hi else None,
                   "mean_rest": round(st.mean(lo), 3) if lo else None, "t": tstat(hi, lo),
                   "A": round(A, 2), "H": round(A - sum(hi) / 2, 2), "S": round(A - sum(hi), 2)}
    h1, h2 = O["h1"], O["h2"]
    O["pass_H"] = bool(h1["H"] > h1["A"] and h2["H"] > h2["A"] and h1["mean_hi"] is not None and h2["mean_hi"] is not None
                       and h1["mean_hi"] < h1["mean_rest"] and h2["mean_hi"] < h2["mean_rest"] and (h2["t"] or 0) <= -2)
    O["pass_S"] = bool(O["pass_H"] and h1["S"] > h1["A"] and h2["S"] > h2["A"])
    print(f"::notice title=liq274 {name}::n {O['n']} with trait {O['with_trait']} thr {round(thr, 3)} | "
          + " | ".join(f"{h}: hi {O[h]['n_hi']} {O[h]['mean_hi']} vs rest {O[h]['mean_rest']} t {O[h]['t']}; A {O[h]['A']} H {O[h]['H']} S {O[h]['S']}"
                       for h in ("h1", "h2", "all")) + f" | pass H {O['pass_H']} S {O['pass_S']}")
    return O


def main():
    db, out = sys.argv[1], sys.argv[2]
    OUT = {}
    rows = L.lev_trips(db)
    now = time.time() * 1000
    desk = json.load(open("web/whales_live.json"))["desk"]
    for c in desk["closed"]:
        if c.get("opened_t") and c.get("closed_t"):
            L.want(c["coin"], c["opened_t"] - L.H, c["closed_t"] + L.H)
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
            if r[6] > now - 10 * L.MIN:
                continue
            S = L.ser(r[3])
            if not S:
                continue
            x = L.one(S, r[4], None, r[5] + L.DELAY, r[6] + L.DELAY, r[7], float(r[10]))
            if not x:
                continue
            H_.append((r[6] + L.DELAY, r[5] + L.DELAY, int(x["flag"])))
            if len(lst) >= 20:
                T.append({"t": r[5], "who": tr, "a": x["a"], "flag": x["flag"], "bar": tr in barset})
        hist[tr] = sorted(H_)
    for r in T:
        r["trait"] = trait_of(hist[r["who"]], r["t"] + L.DELAY)
    Tbar = [r for r in T if r["bar"]]
    first = sorted(Tbar, key=lambda r: r["t"])
    first = [r for r in first[: len(first) // 2] if r["trait"] is not None]
    tr_vals = sorted(r["trait"] for r in first)
    thr = tr_vals[int(0.75 * (len(tr_vals) - 1))] if tr_vals else 1.0
    if thr <= 0:                       # a zero quartile would mark every trader with any flag; keep it strict
        thr = min([v for v in tr_vals if v > 0] or [1.0])
    OUT["thr"] = thr
    OUT["T_bar"] = report("T_bar %", Tbar, thr)
    OUT["T"] = report("T %", T, thr)
    # does the trait persist at all? correlation of a trader's trait with the flag of his next copy
    fl_hi = [r["flag"] for r in Tbar if r["trait"] is not None and r["trait"] >= thr]
    fl_lo = [r["flag"] for r in Tbar if r["trait"] is not None and r["trait"] < thr]
    print(f"::notice title=liq274 persistence::T-bar next-copy flag rate: high trait {round(st.mean(fl_hi), 3) if fl_hi else None} "
          f"(n {len(fl_hi)}) vs low {round(st.mean(fl_lo), 3) if fl_lo else None} (n {len(fl_lo)})")
    # ---- D: the desk's own journal, trait from the trip book of the same trader
    D = []
    for c in desk["closed"]:
        if not (c.get("entry") and c.get("exit") and c.get("opened_t")):
            continue
        who = str(c.get("whale") or "")
        key = who.split(":", 1)[-1]
        h = hist.get(key) or hist.get(who)
        D.append({"t": c["opened_t"], "who": who, "a": L.r_(c["side"], c["entry"], c["exit"]), "w": (c.get("notional") or 0) / 100,
                  "trait": trait_of(h, c["opened_t"]) if h else None})
    OUT["D"] = report("D $", D, thr)
    # current followed traders
    cur = []
    for f in json.load(open("web/whales_top.json")).get("following", []):
        a = str(f.get("addr") or "")
        h = hist.get(a.split(":", 1)[-1]) or hist.get(a)
        v = trait_of(h, now) if h else None
        cur.append(f"{f.get('short') or f.get('name')}: {None if v is None else round(v, 2)}")
    print(f"::notice title=liq274 now::followed traders, trait (thr {round(thr, 3)}): {'; '.join(cur)}")
    print(f"::notice title=liq274 verdict::H passes T-bar: {OUT['T_bar'].get('pass_H')}; S passes: {OUT['T_bar'].get('pass_S')}; "
          f"T all H {OUT['T'].get('pass_H')}")
    json.dump(OUT, open(out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
