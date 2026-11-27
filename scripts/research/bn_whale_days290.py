"""Backlog 290 (27.11.2026): how robust is the day t-stat of the live bar (PIT)?

In 279 the PIT copies had t over days 2.12, while SKILL (97 % of the same copies, same mean) had 0.23.  So the day
significance of the bar may rest on a few days.  Rows are rebuilt exactly as bn_whale_skill279 (book T, copy +2 min,
exit +2 min, stop -16 %, 0.20 % a round trip) and must reproduce PIT 13 021 copies / +0.63 % / t_days 2.12.

Checks, fixed before any result (one rule, no variants tuned):
 - day mean = equal-weight mean of the copies opened that UTC day; t = mean / (pstdev / sqrt n) over days;
 - t after dropping the 1/3/5 best days and, separately, the 1/3/5 worst days;
 - median day, share of plus days;
 - bootstrap over days (10 000 resamples, seed 290): 95 % interval of the mean day;
 - block bootstrap by 7-day blocks (10 000, seed 290): 95 % interval (days next to each other are not independent);
 - t over days of the day SUM (all copies of a day at equal size = what a desk earns), same intervals;
 - diagnosis: the days where SKILL and PIT day means differ most, and days with PIT copies but no SKILL copy.
Verdict «fragile» if the day bootstrap 95 % interval of PIT touches 0 OR the 7-day block interval does; else «holds».
Info only; no rule changes.  Usage: bn_whale_days290.py DB OUT.json"""
import bisect, json, os, random, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_liq271 as L
import bn_whale_stop275 as S5
import bn_whale_reent276 as R6
from bn_whale_skill279 import passes, tst, mean, DAY, WIN

B = 10_000


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
            T.append(dict(t=r[5], who=tr, a16=c16[0], skill=skill))
        if n % 50 == 0:
            print(f"traders {n + 1}/{len(by)} rows {len(T)}", flush=True)
    return T


def by_day(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["t"] // DAY].append(r["a16"])
    return by


def ci(vals, rng):
    n = len(vals)
    ms = sorted(st.mean(rng.choices(vals, k=n)) for _ in range(B))
    return [round(ms[int(0.025 * B)], 3), round(ms[int(0.975 * B)], 3)]


def block_ci(days, vals_by_day, rng, blk=7):
    d0 = min(days)
    blocks = defaultdict(list)
    for d in days:
        blocks[(d - d0) // blk].append(vals_by_day[d])
    bl = list(blocks.values())
    nb = len(bl)
    ms = []
    for _ in range(B):
        pick = [v for b in rng.choices(bl, k=nb) for v in b]
        ms.append(st.mean(pick))
    ms.sort()
    return [round(ms[int(0.025 * B)], 3), round(ms[int(0.975 * B)], 3)]


def robust(rows, seed=290):
    by = by_day(rows)
    days = sorted(by)
    dm = {d: st.mean(by[d]) for d in days}
    ds = {d: sum(by[d]) for d in days}
    v = [dm[d] for d in days]
    srt = sorted(v)
    O = {"copies": len(rows), "mean_copy": mean([r["a16"] for r in rows]), "days": len(days),
         "mean_day": mean(v), "median_day": round(st.median(v), 3), "plus_days": round(sum(x > 0 for x in v) / len(v), 3),
         "t_days": tst(v), "max_day": round(srt[-1], 2), "min_day": round(srt[0], 2)}
    for k in (1, 3, 5):
        O[f"t_wo_best{k}"] = tst(srt[:-k])
        O[f"t_wo_worst{k}"] = tst(srt[k:])
    rng = random.Random(seed)
    O["boot95_day"] = ci(v, rng)
    O["block7_95_day"] = block_ci(days, dm, random.Random(seed))
    s = [ds[d] for d in days]
    O["sum_day"] = {"mean": mean(s), "t": tst(s), "boot95": ci(s, random.Random(seed)),
                    "block7_95": block_ci(days, ds, random.Random(seed))}
    return O, dm, by


def main():
    db, out = sys.argv[1], sys.argv[2]
    T = rows_T(db)
    T.sort(key=lambda r: r["t"])
    PIT, SK = T, [r for r in T if r["skill"]]
    O = {"meta": {"rows": len(T), "from": time.strftime("%Y-%m-%d", time.gmtime(T[0]["t"] / 1000)),
                  "to": time.strftime("%Y-%m-%d", time.gmtime(T[-1]["t"] / 1000))}}
    O["PIT"], dp, bp = robust(PIT)
    O["SKILL"], dsk, bsk = robust(SK)
    only = [d for d in dp if d not in dsk]
    O["pit_only_days"] = [{"day": time.strftime("%Y-%m-%d", time.gmtime(d * DAY / 1000)), "n": len(bp[d]),
                           "mean": round(dp[d], 2)} for d in only]
    gap = sorted((d for d in dp if d in dsk), key=lambda d: -abs(dp[d] - dsk[d]))[:8]
    O["top_gap_days"] = [{"day": time.strftime("%Y-%m-%d", time.gmtime(d * DAY / 1000)), "n_pit": len(bp[d]),
                          "n_skill": len(bsk[d]), "pit": round(dp[d], 2), "skill": round(dsk[d], 2)} for d in gap]
    P = O["PIT"]
    fragile = P["boot95_day"][0] <= 0 or P["block7_95_day"][0] <= 0
    O["verdict"] = {"fragile": fragile}
    for nm in ("PIT", "SKILL"):
        b = O[nm]
        print(f"::notice title=days290 {nm}::copies {b['copies']} mean {b['mean_copy']} days {b['days']} mean_day {b['mean_day']} "
              f"med {b['median_day']} plus {b['plus_days']} t {b['t_days']} | -best1/3/5 {b['t_wo_best1']}/{b['t_wo_best3']}/{b['t_wo_best5']} "
              f"-worst1/3/5 {b['t_wo_worst1']}/{b['t_wo_worst3']}/{b['t_wo_worst5']} | max {b['max_day']} min {b['min_day']} | "
              f"boot {b['boot95_day']} block7 {b['block7_95_day']} | sum t {b['sum_day']['t']} boot {b['sum_day']['boot95']} block7 {b['sum_day']['block7_95']}")
    print(f"::notice title=days290 diag::pit_only {json.dumps(O['pit_only_days'][:6])} | gap {json.dumps(O['top_gap_days'][:5])} | "
          f"{O['meta']['from']}→{O['meta']['to']} fragile {fragile}")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
