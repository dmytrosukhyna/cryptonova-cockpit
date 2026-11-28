"""Backlog 291 (28.11.2026): re-gate past whale checks with a day measure that is not swung by days with 1-5 copies.

290 showed that «t over days >= 2» on the equal-weight day mean is noisy when a day has few copies.  Two past checks
(279 SKILL, 283 STAB) used that measure for both of their significance gates on the same book T rows; this script
rebuilds the rows once (exactly as bn_whale_days290 / bn_whale_skill279: book T, copy +2 min, exit +2 min, stop -16 %,
0.20 % a round trip, live bar PIT at entry) and recomputes their gates under three measures, fixed before any result:
  EQ   = equal-weight day mean (the original measure; must reproduce 279 / 283 within rounding);
  SUM  = day sum of the copies (what a desk of equal-size copies earns per day) - the main measure from now on;
  M10  = equal-weight day mean on days with >= 10 copies only (for the difference: >= 10 copies in BOTH arms).
Gates per check (as originally written):
  c1 filtered mean copy > PIT mean copy in both time halves (unchanged by the measure);
  c2 t over days of (filtered day mean - dropped day mean) >= 2: EQ / M10 as above; for SUM (the arms differ in size, so
     a raw sum difference is meaningless) the same daily difference weighted by the day's PIT copy count (weighted t);
  c3 t over days of the filtered copies >= 2;
  c4 copies lost <= 40 % (unchanged).
A verdict that changes from «not passed» to «passed» is only an argument for the owner (the live bar is a real-money
rule); nothing here changes the engine.  Usage: bn_whale_regate291.py DB OUT.json"""
import bisect, json, math, os, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_liq271 as L
import bn_whale_stop275 as S5
import bn_whale_reent276 as R6
from bn_whale_skill279 import passes, tst, mean, DAY, WIN, HALF


def rows_T(db):
    rows = L.lev_trips(db)
    now = time.time() * 1000
    for r in rows:
        if r[6] <= now - 10 * L.MIN:
            L.want(r[3], r[5] - L.H, r[6] + L.H)
    by = defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    T = []
    for n, (tr, lst) in enumerate(by.items()):
        lst.sort(key=lambda r: r[5])
        dec = sorted(((r[6], r[5], (1 if r[4] == "LONG" else -1) * (r[8] / r[7] - 1) * 100 - L.RT) for r in lst if r[8]))
        ct = [d[0] for d in dec]
        for r in lst:
            if r[6] > now - 10 * L.MIN or not r[8]:
                continue
            k = bisect.bisect_left(ct, r[5])
            prior = [d for d in dec[:k] if d[1] >= r[5] - WIN]
            if not passes([d[2] for d in prior]):
                continue
            S = L.ser(r[3])
            if not S:
                continue
            x = L.one(S, r[4], None, r[5] + L.DELAY, r[6] + L.DELAY, r[7], float(r[10]))
            if not x:
                continue
            c16 = S5.copy_stop(S, r[4], r[5] + L.DELAY, r[6] + L.DELAY, S5.S16)
            c25 = S5.copy_stop(S, r[4], r[5] + L.DELAY, r[6] + L.DELAY, S5.S25)
            re = R6.one(S, r[4], r[5] + L.DELAY, r[6] + L.DELAY, r[7])
            if not c16 or not c25 or not re:
                continue
            xs = [d[2] for d in prior]
            tt = tst(xs)
            gp = sum(v for v in xs if v > 0)
            top3 = sum(sorted((v for v in xs if v > 0), reverse=True)[:3])
            skill = tt is not None and tt >= 2 and gp > 0 and top3 / gp <= 0.50
            old = [d[2] for d in prior if d[1] < r[5] - HALF]
            new = [d[2] for d in prior if d[1] >= r[5] - HALF]
            stab = bool(passes(old, need=8)) and bool(passes(new, need=8))
            T.append(dict(t=r[5], who=tr, a16=c16[0], skill=skill, stab=stab))
        if n % 50 == 0:
            print(f"traders {n + 1}/{len(by)} rows {len(T)}", flush=True)
    return T


def groups(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["t"] // DAY].append(r["a16"])
    return by


def wt(xs, ws):
    """t of a weighted mean over days (weights = PIT copies of the day), effective n = (sum w)^2 / sum w^2"""
    if len(xs) < 3:
        return None
    W = sum(ws)
    m = sum(x * w for x, w in zip(xs, ws)) / W
    var = sum(w * (x - m) ** 2 for x, w in zip(xs, ws)) / W
    neff = W * W / sum(w * w for w in ws)
    return round(m / math.sqrt(var / neff), 2) if var else None


def check(T, key):
    mid = T[len(T) // 2]["t"]
    F = [r for r in T if r[key]]
    D = [r for r in T if not r[key]]
    half = lambda rs, h: mean([r["a16"] for r in rs if (r["t"] < mid) == (h == 1)])  # noqa: E731
    c1 = bool(half(F, 1) is not None and half(F, 1) > half(T, 1) and half(F, 2) > half(T, 2))
    lost = round(len(D) / len(T), 3)
    c4 = lost <= 0.40
    gp, gf, gd = groups(T), groups(F), groups(D)
    both = [d for d in gf if d in gd]
    O = {"n_pit": len(T), "n_f": len(F), "n_d": len(D), "mean_pit": mean([r["a16"] for r in T]), "mean_f": mean([r["a16"] for r in F]),
         "mean_d": mean([r["a16"] for r in D]), "h1_f": half(F, 1), "h1_pit": half(T, 1), "h2_f": half(F, 2), "h2_pit": half(T, 2),
         "c1": c1, "lost": lost, "c4": c4, "measures": {}}
    eq_diff = [st.mean(gf[d]) - st.mean(gd[d]) for d in both]
    eq_f = [st.mean(v) for v in gf.values()]
    m10 = [d for d in both if len(gf[d]) >= 10 and len(gd[d]) >= 10]
    m10_diff = [st.mean(gf[d]) - st.mean(gd[d]) for d in m10]
    m10_f = [st.mean(v) for v in gf.values() if len(v) >= 10]
    sum_f = [sum(v) for v in gf.values()]
    sum_diff_t = wt(eq_diff, [len(gp[d]) for d in both])
    for nm, t2, n2, t3, n3 in (("EQ", tst(eq_diff), len(eq_diff), tst(eq_f), len(eq_f)),
                               ("SUM", sum_diff_t, len(both), tst(sum_f), len(sum_f)),
                               ("M10", tst(m10_diff), len(m10_diff), tst(m10_f), len(m10_f))):
        c2 = bool(t2 is not None and t2 >= 2)
        c3 = bool(t3 is not None and t3 >= 2)
        O["measures"][nm] = {"t_diff": t2, "days_diff": n2, "t_f": t3, "days_f": n3, "c2": c2, "c3": c3,
                             "passed": bool(c1 and c2 and c3 and c4)}
    return O


def main():
    db, out = sys.argv[1], sys.argv[2]
    T = rows_T(db)
    T.sort(key=lambda r: r["t"])
    O = {"meta": {"rows": len(T), "from": time.strftime("%Y-%m-%d", time.gmtime(T[0]["t"] / 1000)),
                  "to": time.strftime("%Y-%m-%d", time.gmtime(T[-1]["t"] / 1000))}}
    for nm, key in (("skill279", "skill"), ("stab283", "stab")):
        O[nm] = c = check(T, key)
        ms = " | ".join(f"{k}: t_diff {v['t_diff']} ({v['days_diff']} d) t_f {v['t_f']} ({v['days_f']} d) pass {v['passed']}"
                        for k, v in c["measures"].items())
        print(f"::notice title=regate291 {nm}::pit {c['n_pit']} f {c['n_f']} ({c['mean_f']}%) d {c['n_d']} ({c['mean_d']}%) "
              f"h1 {c['h1_f']}/{c['h1_pit']} h2 {c['h2_f']}/{c['h2_pit']} c1 {c['c1']} lost {c['lost']} | {ms}")
    print(f"::notice title=regate291 meta::rows {len(T)} {O['meta']['from']}→{O['meta']['to']}")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
