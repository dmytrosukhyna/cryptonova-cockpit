"""Backlog 152, 13.11.2026: «не держать тех, кто вперёд в минусе» on Binance ws_trips — same machinery as bn_whale_selroll.py
(backlog 149). Research only, reads the whale study's database (Actions cache), writes nothing to it.

Pre-registered before any result was seen (13.11 09:25 Athens), one variant:
- R21 = SEL (our bar at review T, open decisions marked at T) minus traders whose copy sum over their decisions opened in
  [T − 21 d, T) is < 0 (copy-test value: copy +2 min, exit at min(close, T) + 2 min). No decisions in those 21 days → kept.
  EXCL = SEL copies removed by the rule. Forward as backlog 149 (copy +6 min, exit +6 min, stop −16 %, costs and funding).
- Verdict «правило помогает»: EXCL mean < 0 with t ≤ −2, AND R21 > SEL in both halves, AND R21 mean > 0. Information only.
Usage: bn_whale_sel21.py DB OUT.json"""
import bisect, json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as D  # noqa: E402

M1, H, DAY, COST = D.M1, D.H, D.DAY, D.COST
STEP, LOOK = 3 * DAY, 60 * DAY


def tstat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "mean": round(xs[0], 3) if xs else None}
    m, sd = st.mean(xs), st.stdev(xs)
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "pf": round(pos / neg, 2) if neg else None, "win": round(sum(x > 0 for x in xs) / len(xs), 3)}


def px(S, t):
    T = S["t"]; i = bisect.bisect_left(T, t)
    if i >= len(T) or T[i] - t > 2 * M1:
        return None
    return S["k"][T[i]][0]


def bar_ok(vals):
    """vals = list of (whale_ret_net, copy_d2)"""
    if len(vals) < 20:
        return False
    w = [a for a, _ in vals]
    pos, neg = sum(x for x in w if x > 0), -sum(x for x in w if x < 0)
    if sum(x > 0 for x in w) / len(w) < 0.70 or sum(w) <= 0 or (neg and pos / neg < 2):
        return False
    return sum(b for _, b in vals) > 0


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, entry, exit, notional FROM ws_trips WHERE src='bn' "
                     "ORDER BY trader, open_t").fetchall()
    now = time.time() * 1000
    by = defaultdict(list)
    for r in rows:
        if r[3] and r[5]:
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
    n_open = 0
    for e in eps:
        cl = [x[4] if (x[4] and x[4] > 0) else None for x in e["g"]]
        e["close_t"] = None if any(v is None for v in cl) else max(cl)
        n_open += e["close_t"] is None
    print(f"bn rows {len(rows)}, decisions (traders ≥ 20): {len(eps)}, still open: {n_open}", flush=True)
    end_cap = now - 1.5 * DAY
    syms = defaultdict(list)
    for e in eps:
        syms[e["coin"] + "USDT"].append(e)
    data = {}
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        data[sym] = D.load_sym(sym, [(x["open_t"], x["close_t"] or end_cap) for x in es])
        if n % 10 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if data[sym] else 'none'}", flush=True)
    eps = [e for e in eps if data.get(e["coin"] + "USDT")]
    for e in eps:
        S = data[e["coin"] + "USDT"]
        e["S"] = S
        e["last"] = S["t"][-1] - M1
        e["s"] = 1 if e["side"] == "LONG" else -1
    eps.sort(key=lambda e: e["open_t"])
    t0, t_end = eps[0]["open_t"], max(e["open_t"] for e in eps)
    reviews = []
    T = t0 + LOOK
    while T <= t_end - STEP:
        reviews.append(T); T += STEP
    by_tr = defaultdict(list)
    for e in eps:
        by_tr[e["trader"]].append(e)

    def whale_at(e, T):
        """MTM whale result (net of cost) of the legs opened before T; None if no price"""
        num = den = 0.0
        for x in e["g"]:
            if x[3] >= T:
                continue
            w = max(x[7] or 0, 1e-9)
            if x[4] and 0 < x[4] <= T and x[6]:
                ex = x[6]
            else:
                ex = px(e["S"], T)
                if ex is None:
                    return None
            num += e["s"] * (ex / x[5] - 1) * 100 * w; den += w
        return num / den - COST if den else None

    cache = {}

    def look_val(e, T, closed_only):
        done = e["close_t"] is not None and e["close_t"] <= T
        if closed_only and not done:
            return None
        if done:
            if id(e) not in cache:
                w = whale_at(e, e["close_t"] + 1)
                cp = D.copy(e["S"], e["side"], e["open_t"] + 2 * M1, e["close_t"] + 2 * M1)
                cache[id(e)] = None if (w is None or cp is None) else (w, cp)
            return cache[id(e)]
        w = whale_at(e, T)
        cp = D.copy(e["S"], e["side"], e["open_t"] + 2 * M1, T)
        return None if (w is None or cp is None) else (w, cp)

    fwd_cache = {}

    def fwd(e):
        if id(e) not in fwd_cache:
            t_out = (e["close_t"] + 6 * M1) if e["close_t"] else e["last"]
            t_out = min(t_out, e["last"])
            v = D.copy(e["S"], e["side"], e["open_t"] + 6 * M1, t_out)
            fwd_cache[id(e)] = v if (v is not None and math.isfinite(v)) else None
        return fwd_cache[id(e)]

    groups = {"SEL": [], "R21": [], "EXCL": [], "POOL": []}
    per_review = []
    for T in reviews:
        sel, selc, pool, excl = set(), set(), set(), set()
        for tr, lst in by_tr.items():
            lk = [e for e in lst if T - LOOK <= e["open_t"] < T]
            if len(lk) < 20:
                continue
            pool.add(tr)
            v = [x for x in (look_val(e, T, False) for e in lk) if x]
            vc = [x for x in (look_val(e, T, True) for e in lk) if x]
            if bar_ok(v):
                sel.add(tr)
            if bar_ok(vc):
                selc.add(tr)
            if tr in sel:
                r21v = [look_val(e, T, False) for e in lst if T - 21 * DAY <= e["open_t"] < T]
                if sum(x[1] for x in r21v if x) < 0:
                    excl.add(tr)
        nxt = {"SEL": [], "CLOSED": [], "POOL": []}
        for tr in pool:
            for e in by_tr[tr]:
                if T <= e["open_t"] < T + STEP:
                    r = fwd(e)
                    if r is None:
                        continue
                    row = {"t": e["open_t"], "trader": tr, "r": r, "T": T, "open_end": e["close_t"] is None}
                    groups["POOL"].append(row); nxt["POOL"].append(r)
                    if tr in sel:
                        groups["SEL"].append(row); nxt["SEL"].append(r)
                    if tr in sel and tr not in excl:
                        groups["R21"].append(row)
                    if tr in excl:
                        groups["EXCL"].append(row)
        per_review.append({"T": time.strftime("%Y-%m-%d", time.gmtime(T / 1000)), "pool": len(pool), "sel": len(sel), "closed": len(selc), "excl": sorted(excl),
                           "sel_fwd": tstat(nxt["SEL"]), "pool_fwd": tstat(nxt["POOL"]), "closed_fwd": tstat(nxt["CLOSED"])})
    O = {"decisions": len(eps), "open_now": n_open, "reviews": len(reviews),
         "from": time.strftime("%Y-%m-%d", time.gmtime(t0 / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(t_end / 1000)),
         "first_review": per_review[0]["T"] if per_review else None, "per_review": per_review}
    midT = reviews[len(reviews) // 2] if reviews else 0
    for g, rs in groups.items():
        xs = [r["r"] for r in rs]
        bytr = defaultdict(list)
        for r in rs:
            bytr[r["trader"]].append(r["r"])
        months = defaultdict(list)
        for r in rs:
            months[time.strftime("%Y-%m", time.gmtime(r["t"] / 1000))].append(r["r"])
        O[g] = {"all": tstat(xs), "traders": len(bytr), "by_trader": tstat([st.mean(v) for v in bytr.values()]),
                "half1": tstat([r["r"] for r in rs if r["T"] < midT]), "half2": tstat([r["r"] for r in rs if r["T"] >= midT]),
                "open_at_end": sum(r["open_end"] for r in rs),
                "months": {m: tstat(v) for m, v in sorted(months.items())}}
    S_, R_, E_ = O["SEL"], O["R21"], O["EXCL"]
    m = lambda X, k: X[k].get("mean")
    ok = (m(E_, "all") is not None and m(E_, "all") < 0 and (E_["all"].get("t") or 0) <= -2
          and all(m(R_, h) is not None and m(S_, h) is not None and m(R_, h) > m(S_, h) for h in ("half1", "half2"))
          and (m(R_, "all") or -1) > 0)
    O["verdict"] = "правило помогает" if ok else "правило не доказано"
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=sel21 n::{O['decisions']} decisions ({n_open} open), {O['reviews']} reviews {O['first_review']} → "
          f"{per_review[-1]['T'] if per_review else None}, data {O['from']} → {O['to']}")
    for g in groups:
        G = O[g]
        print(f"::notice title=sel21 {g}::all {G['all']} | by trader {G['by_trader']} (tr {G['traders']}) | h1 {G['half1'].get('mean')}"
              f"/t{G['half1'].get('t')}/n{G['half1'].get('n')} h2 {G['half2'].get('mean')}/t{G['half2'].get('t')}/n{G['half2'].get('n')}"
              f" | open at end {G['open_at_end']}")
    for g in ("SEL", "R21", "EXCL", "POOL"):
        print(f"::notice title=sel21 months {g}::" + " ".join(f"{m} {v.get('mean')}/{v.get('n')}" for m, v in O[g]["months"].items()))
    sizes = " ".join(f"{p['T'][5:]} {p['sel']}/{p['pool']} x{len(p['excl'])}" for p in per_review)
    print(f"::notice title=sel21 sizes (sel/pool xexcl)::{sizes}")
    xt = defaultdict(list)
    for r in groups["EXCL"]:
        xt[r["trader"]].append(r["r"])
    print("::notice title=sel21 excluded traders::" + " ".join(f"{k} {tstat(v).get('mean')}/{len(v)}" for k, v in sorted(xt.items(), key=lambda kv: -len(kv[1]))[:15]))
    print(f"::notice title=sel21 verdict::{O['verdict']}")


if __name__ == "__main__":
    main()
