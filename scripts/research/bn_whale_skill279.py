"""Backlog 279 (27.11.2026): «skill, not luck» (idea from «Beyond Profit Rankings», arXiv 2610.34754) - among traders who
pass the live point-in-time bar, keep only those whose 60-day record does not look like luck: does that pick better copies?

Stability across windows was already tested in 283 (failed).  The two remaining features of the paper are tested as ONE
composite rule, written down before any result was seen (no variants, no thresholds tuned):

 Book T and row filters exactly as bn_whale_stab283 / pit282 (copy in +2 min, out +2 min, stop -16 %, 0.20 % a round
   trip), so the PIT base must reproduce 283: 13 021 copies, +0.63 %.
 PIT   = at the trip's open t: trips CLOSED before t and opened within 60 d before t: >= 20, win >= 70 %, PF >= 2,
         sum > 0 (the live selection).
 SKILL = PIT and both luck checks on those same prior decisions (net of 0.20 %):
         (a) randomness: t of the mean decision return = mean / (sd / sqrt n) >= 2;
         (b) concentration: the 3 best decisions bring <= 50 % of the gross profit (sum of positive decisions).
 DROP  = PIT and not SKILL.
 Pass (all four, else «not passed»), the same gates as 283:
   1. SKILL mean copy > PIT mean copy in BOTH time halves (split at the PIT median entry time);
   2. t over days of (SKILL day mean - DROP day mean), days with both, >= 2;
   3. t over days of the SKILL copies >= 2;
   4. copies lost (DROP / PIT) <= 40 %.
 Info only: which check drops how many copies, and the mean of copies failing (a) only / (b) only.
 Selection is continuous (at each entry), not every 3 days; the 3-day lock only delays both arms alike.
 A pass is only an argument for the owner (the live bar is a real-money rule); nothing here changes the engine.
Usage: bn_whale_skill279.py DB OUT.json"""
import bisect, json, math, os, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_liq271 as L
import bn_whale_stop275 as S5
import bn_whale_reent276 as R6

DAY = 86_400_000
WIN = 60 * DAY
HALF = 30 * DAY


def passes(xs, need=20, sum_pos=True):
    if len(xs) < need:
        return None
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    ok = sum(x > 0 for x in xs) / len(xs) >= 0.70 and (neg == 0 or pos / neg >= 2)
    return bool(ok and (sum(xs) > 0 if sum_pos else True))


def tst(xs):
    if len(xs) < 2 or not st.pstdev(xs):
        return None
    return round(st.mean(xs) / (st.pstdev(xs) / math.sqrt(len(xs))), 2)


def mean(xs):
    return round(st.mean(xs), 3) if xs else None


def day_means(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["t"] // DAY].append(r["a16"])
    return {d: st.mean(v) for d, v in by.items()}


def base(rows, mid):
    if not rows:
        return {"n": 0}
    dm = day_means(rows)
    return {"n": len(rows), "traders": len({r["who"] for r in rows}), "mean": mean([r["a16"] for r in rows]),
            "h1": mean([r["a16"] for r in rows if r["t"] < mid]), "h2": mean([r["a16"] for r in rows if r["t"] >= mid]),
            "t_days": tst(list(dm.values())), "plus": round(sum(r["a16"] > 0 for r in rows) / len(rows), 3),
            "stop_share": round(sum(r["stop16"] for r in rows) / len(rows), 4)}


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
    T = []
    for n, (tr, lst) in enumerate(by.items()):
        lst.sort(key=lambda r: r[5])
        own = [(1 if r[4] == "LONG" else -1) * (r[8] / r[7] - 1) * 100 - L.RT for r in lst if r[8]]
        full = len(lst) >= 20 and bool(passes(own, need=1, sum_pos=False))
        dec = sorted(((r[6], r[5], (1 if r[4] == "LONG" else -1) * (r[8] / r[7] - 1) * 100 - L.RT) for r in lst if r[8]))
        ct = [d[0] for d in dec]
        for r in lst:
            if r[6] > now - 10 * L.MIN or not r[8]:
                continue
            k = bisect.bisect_left(ct, r[5])
            prior = [d for d in dec[:k] if d[1] >= r[5] - WIN]
            pit = passes([d[2] for d in prior])
            if not pit:
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
            ok_a = tt is not None and tt >= 2
            gp = sum(x for x in xs if x > 0)
            top3 = sum(sorted((x for x in xs if x > 0), reverse=True)[:3])
            ok_b = gp > 0 and top3 / gp <= 0.50
            T.append(dict(t=r[5], who=tr, src=r[0], a16=c16[0], stop16=c16[1], full=full, stab=ok_a and ok_b,
                          ok_a=ok_a, ok_b=ok_b, tt=tt, top3=round(top3 / gp, 3) if gp else None))
        if n % 50 == 0:
            print(f"traders {n + 1}/{len(by)} rows {len(T)}", flush=True)
    T.sort(key=lambda r: r["t"])
    mid = T[len(T) // 2]["t"] if T else 0
    PIT = T
    STAB = [r for r in T if r["stab"]]
    DROP = [r for r in T if not r["stab"]]
    O = {"meta": {"trips": len(rows), "traders": len(by), "rows": len(T),
                  "mid": time.strftime("%Y-%m-%d", time.gmtime(mid / 1000)) if T else None,
                  "from": time.strftime("%Y-%m-%d", time.gmtime(T[0]["t"] / 1000)) if T else None,
                  "to": time.strftime("%Y-%m-%d", time.gmtime(T[-1]["t"] / 1000)) if T else None}}
    for name, rs in (("PIT", PIT), ("SKILL", STAB), ("DROP", DROP)):
        O[name] = base(rs, mid)
    ds, dd = day_means(STAB), day_means(DROP)
    diff = [ds[d] - dd[d] for d in ds if d in dd]
    O["diff_days"] = {"days": len(diff), "mean": mean(diff), "t": tst(diff)}
    # why dropped: which half failed (info)
    fa = [r for r in DROP if not r["ok_a"] and r["ok_b"]]
    fb = [r for r in DROP if r["ok_a"] and not r["ok_b"]]
    fab = [r for r in DROP if not r["ok_a"] and not r["ok_b"]]
    O["drop_why"] = {"fail_a_only": len(fa), "fail_a_only_mean": mean([r["a16"] for r in fa]),
                     "fail_b_only": len(fb), "fail_b_only_mean": mean([r["a16"] for r in fb]),
                     "fail_both": len(fab), "fail_both_mean": mean([r["a16"] for r in fab]),
                     "tt_median": st.median([r["tt"] for r in T if r["tt"] is not None]) if T else None,
                     "top3_median": st.median([r["top3"] for r in T if r["top3"] is not None]) if T else None}
    spoiled = [r for r in PIT if not r["full"]]
    O["spoiled282"] = {"n": len(spoiled), "mean": mean([r["a16"] for r in spoiled]),
                       "skipped_by_skill": sum(not r["stab"] for r in spoiled),
                       "skipped_mean": mean([r["a16"] for r in spoiled if not r["stab"]]),
                       "kept_mean": mean([r["a16"] for r in spoiled if r["stab"]])}
    P, S_ = O["PIT"], O["SKILL"]
    lost = round(len(DROP) / len(PIT), 3) if PIT else None
    c1 = bool(S_.get("h1") is not None and P.get("h1") is not None and S_["h1"] > P["h1"] and S_["h2"] > P["h2"])
    c2 = bool(O["diff_days"]["t"] is not None and O["diff_days"]["t"] >= 2)
    c3 = bool(S_.get("t_days") is not None and S_["t_days"] >= 2)
    c4 = bool(lost is not None and lost <= 0.40)
    O["verdict"] = {"both_halves_better": c1, "diff_t_ge_2": c2, "stab_t_ge_2": c3, "lost": lost, "lost_le_40": c4,
                    "passed": c1 and c2 and c3 and c4}
    for name in ("PIT", "SKILL", "DROP"):
        b = O[name]
        print(f"::notice title=skill279 {name}::n {b['n']} traders {b.get('traders')} mean {b.get('mean')}% h1 {b.get('h1')} "
              f"h2 {b.get('h2')} t_days {b.get('t_days')} plus {b.get('plus')} stop16 {b.get('stop_share')} | "
              f"{O['meta']['from']}→{O['meta']['to']} mid {O['meta']['mid']}")
    sp = O["spoiled282"]
    print(f"::notice title=skill279 verdict::diff SKILL-DROP days {O['diff_days']['days']} mean {O['diff_days']['mean']} "
          f"t {O['diff_days']['t']} | lost {lost} | fail a {O['drop_why']['fail_a_only']} ({O['drop_why']['fail_a_only_mean']}) b {O['drop_why']['fail_b_only']} ({O['drop_why']['fail_b_only_mean']}) both {O['drop_why']['fail_both']} ({O['drop_why']['fail_both_mean']}) tt_med {O['drop_why']['tt_median']} top3_med {O['drop_why']['top3_median']} | "
          f"spoiled282 n {sp['n']} mean {sp['mean']} skipped {sp['skipped_by_skill']} ({sp['skipped_mean']}) kept {sp['kept_mean']} | "
          f"checks {c1}/{c2}/{c3}/{c4} passed {O['verdict']['passed']}")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
