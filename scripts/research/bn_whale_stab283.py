"""Backlog 283 (27.11.2026): a «stability bar» - does requiring the bar to hold in BOTH 30-day halves of the 60-day window
before entry filter out the traders who later break?

Side result of 282: copies of traders who passed the point-in-time bar but failed it on their full history («later
spoiled») averaged -0.27 % (2 508 copies).  One bar is tested, written down before any result was seen (no variants):

 Book T exactly as bn_whale_pit282 (bn_whale_liq271.lev_trips, copy in +2 min, out +2 min, stop -16 %, 0.20 % a round
   trip; the same row filters as 282 so the PIT base must reproduce 282: ~12 973 copies, +0.63 %).
 PIT  = at the trip's open t: the trader's trips CLOSED before t and opened within 60 d before t: >= 20, win >= 70 %,
        PF >= 2, sum > 0 (the live selection, as 262/282).
 STAB = PIT and, separately in each 30-day half of that window (opened in [t-60d, t-30d) and [t-30d, t), closed
        before t): >= 8 trips (the 30-day floor of item 240), win >= 70 %, PF >= 2, sum > 0.
 DROP = PIT and not STAB (the copies the stability bar would skip).
 Pass (all four, else «not passed»):
   1. STAB mean copy > PIT mean copy in BOTH time halves (split at the PIT median entry time);
   2. t over days of (STAB day mean - DROP day mean), days with both, >= 2;
   3. t over days of the STAB copies >= 2;
   4. copies lost (DROP / PIT) <= 40 %.
 Info only: how many of the 282 «later spoiled» copies (PIT and not FULL) the stability bar skips, and their mean.
 A pass is only an argument for the owner (the live bar is a real-money rule); nothing here changes the engine.
Usage: bn_whale_stab283.py DB OUT.json"""
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
            old = [d[2] for d in prior if d[1] < r[5] - HALF]
            new = [d[2] for d in prior if d[1] >= r[5] - HALF]
            stab = bool(passes(old, need=8)) and bool(passes(new, need=8))
            T.append(dict(t=r[5], who=tr, src=r[0], a16=c16[0], stop16=c16[1], full=full, stab=stab,
                          n_old=len(old), n_new=len(new)))
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
    for name, rs in (("PIT", PIT), ("STAB", STAB), ("DROP", DROP)):
        O[name] = base(rs, mid)
    ds, dd = day_means(STAB), day_means(DROP)
    diff = [ds[d] - dd[d] for d in ds if d in dd]
    O["diff_days"] = {"days": len(diff), "mean": mean(diff), "t": tst(diff)}
    # why dropped: which half failed (info)
    O["drop_why"] = {"old_short": sum(r["n_old"] < 8 for r in DROP), "new_short": sum(r["n_new"] < 8 for r in DROP)}
    spoiled = [r for r in PIT if not r["full"]]
    O["spoiled282"] = {"n": len(spoiled), "mean": mean([r["a16"] for r in spoiled]),
                       "skipped_by_stab": sum(not r["stab"] for r in spoiled),
                       "skipped_mean": mean([r["a16"] for r in spoiled if not r["stab"]]),
                       "kept_mean": mean([r["a16"] for r in spoiled if r["stab"]])}
    P, S_ = O["PIT"], O["STAB"]
    lost = round(len(DROP) / len(PIT), 3) if PIT else None
    c1 = bool(S_.get("h1") is not None and P.get("h1") is not None and S_["h1"] > P["h1"] and S_["h2"] > P["h2"])
    c2 = bool(O["diff_days"]["t"] is not None and O["diff_days"]["t"] >= 2)
    c3 = bool(S_.get("t_days") is not None and S_["t_days"] >= 2)
    c4 = bool(lost is not None and lost <= 0.40)
    O["verdict"] = {"both_halves_better": c1, "diff_t_ge_2": c2, "stab_t_ge_2": c3, "lost": lost, "lost_le_40": c4,
                    "passed": c1 and c2 and c3 and c4}
    for name in ("PIT", "STAB", "DROP"):
        b = O[name]
        print(f"::notice title=stab283 {name}::n {b['n']} traders {b.get('traders')} mean {b.get('mean')}% h1 {b.get('h1')} "
              f"h2 {b.get('h2')} t_days {b.get('t_days')} plus {b.get('plus')} stop16 {b.get('stop_share')} | "
              f"{O['meta']['from']}→{O['meta']['to']} mid {O['meta']['mid']}")
    sp = O["spoiled282"]
    print(f"::notice title=stab283 verdict::diff STAB-DROP days {O['diff_days']['days']} mean {O['diff_days']['mean']} "
          f"t {O['diff_days']['t']} | lost {lost} | drop old<8 {O['drop_why']['old_short']} new<8 {O['drop_why']['new_short']} | "
          f"spoiled282 n {sp['n']} mean {sp['mean']} skipped {sp['skipped_by_stab']} ({sp['skipped_mean']}) kept {sp['kept_mean']} | "
          f"checks {c1}/{c2}/{c3}/{c4} passed {O['verdict']['passed']}")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
