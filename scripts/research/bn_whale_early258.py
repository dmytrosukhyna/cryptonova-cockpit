"""Backlog 258 (24.11.2026): the lead trader closed and did NOT re-open the same coin + side within 60 min —
does the price then keep going his way (he closed «early», our exit at close + 2 min too), and what would holding
our copy 1/2/4 h longer have given?  Reference for items 198/200 only; real rules are not touched.

Why: item 256 showed that a close followed by a quick re-open carries information (re-entering beats holding by
≈ 1.3 % a pair).  This checks the other side: the closes that are NOT followed by a re-open.

Data: ws_trips (Actions cache) + data/whale_trades «closed» rows (as bn_whale_reopen256.py). Unit = one base copy
(a trader position; add-ons while open merged).  Copy: first Binance USD-M 5m bar ≥ time, stop −16 % from our entry,
costs 0.10 % a round trip + funding (B.copy_ret).

Pre-registered (written before any result was seen):
- «final close» = the trader's next position in the same coin + side opens > 60 min after this close, or never
  (closes followed by a re-open ≤ 60 min are item 256 and are excluded here).
- a = copy as now (in open + 2 min, out close + 2 min); hX = same copy, out at close + X, X ∈ {1, 2, 4} h.
- follow(X) = the trader-side move from our exit bar (close + 2 min) to close + X, in %, no costs, no stop.
- «early» = follow(1 h) ≥ +2 %.  Reported: share of early closes, mean follow(X), and hX − a by halves of time.
- universe (primary): ≥ 20 decisions and the proxy bar (≥ 70 % plus, PF ≥ 2 after costs); info: all ≥ 20 decisions;
  by source (bn / okx / hl) inside the primary universe.
- pass (only as an argument, the conditioning on «no re-open» is known only 60 min after the close, so hX for
  X ≥ 1 h IS implementable as «hold X unless he re-opens», but item 256 already covers the re-open part):
  hX − a > 0 in BOTH halves, t of the per-day difference ≥ 2.4 (Bonferroni over 3 X), ≥ 200 final closes.
Usage: bn_whale_early258.py DB OUT.json"""
import json, math, os, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import bn_whale_flip245 as F
import bn_whale_reopen256 as R

B.COST = 0.10
H = 3_600_000
MIN = 60_000
DELAY = 2 * MIN
STOP = 16.0
XS = (1, 2, 4)
GAP = 60 * MIN


def px(S, t):
    i = B.bar_at(S, t)
    return None if i is None else S["k"][S["t"][i]][0]


def summ(rs):
    if not rs:
        return None
    rs = sorted(rs, key=lambda r: r["t"])
    mid = rs[len(rs) // 2]["t"]
    q = {"n": len(rs), "traders": len({r["trader"] for r in rs}),
         "from": time.strftime("%Y-%m-%d", time.gmtime(rs[0]["t"] / 1000)),
         "to": time.strftime("%Y-%m-%d", time.gmtime(rs[-1]["t"] / 1000)),
         "a_sum": round(sum(r["a"] for r in rs), 2), "a_mean": round(st.mean(r["a"] for r in rs), 3),
         "early_share": round(sum(r["f"][1] >= 2 for r in rs) / len(rs), 3),
         "against_share": round(sum(r["f"][1] <= -2 for r in rs) / len(rs), 3)}
    for X in XS:
        f = [r["f"][X] for r in rs]
        q[f"follow{X}h_mean"] = round(st.mean(f), 3)
        q[f"follow{X}h_median"] = round(st.median(f), 3)
        d = [r["h"][X] - r["a"] for r in rs]
        day = defaultdict(float)
        for r in rs:
            day[int(r["t"] // (24 * H))] += r["h"][X] - r["a"]
        dv = list(day.values())
        t = None
        if len(dv) > 1 and st.pstdev(dv):
            t = round(st.mean(dv) / (st.pstdev(dv) / math.sqrt(len(dv))), 2)
        h1 = round(sum(r["h"][X] - r["a"] for r in rs if r["t"] < mid), 2)
        h2 = round(sum(r["h"][X] - r["a"] for r in rs if r["t"] >= mid), 2)
        q[f"hold{X}h"] = {"diff_sum": round(sum(d), 2), "diff_mean": round(st.mean(d), 3), "h1": h1, "h2": h2,
                          "t_day": t, "days": len(dv),
                          "pass": h1 > 0 and h2 > 0 and (t or 0) >= 2.4 and len(rs) >= 200}
    # «early» closes only (hindsight split, description only)
    e = [r for r in rs if r["f"][1] >= 2]
    if e:
        q["early_hold4h_mean"] = round(st.mean(r["h"][4] - r["a"] for r in e), 3)
    return q


def main():
    db, out = sys.argv[1], sys.argv[2]
    rows = F.trips(db)
    D = F.decisions(rows)
    good, n20 = set(), set()
    for tr, mine in D.items():
        if len(mine) < 20:
            continue
        n20.add(tr)
        xs = [e["wret"] - B.COST for e in mine if e["wret"] is not None]
        if xs:
            pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
            if sum(x > 0 for x in xs) / len(xs) >= 0.70 and (neg == 0 or pos / neg >= 2):
                good.add(tr)
    grp = defaultdict(list)
    src_of = {}
    for r in rows:
        if r[1] in n20 and r[4] in ("LONG", "SHORT") and r[6] and r[6] > r[5]:
            grp[(r[1], r[3], r[4])].append(r)
            src_of[r[1]] = r[0]
    for v in grp.values():
        v.sort(key=lambda r: r[5])
    print(f"trips of ≥20-decision traders: {sum(len(v) for v in grp.values())}, groups {len(grp)}, good {len(good)}", flush=True)
    bysym = defaultdict(list)
    for k, v in grp.items():
        bysym[F.ALIAS.get(k[1], k[1]) + "USDT"].append(k)
    res, miss, reopened = [], 0, 0
    for n, (sym, keys) in enumerate(sorted(bysym.items(), key=lambda kv: -sum(len(grp[k]) for k in kv[1]))):
        t0 = min(grp[k][0][5] for k in keys) - H
        t1 = max(max(r[6] for r in grp[k]) for k in keys) + 6 * H
        S = B.load_sym(sym, t0, t1)
        if n % 25 == 0:
            print(f"candles {n + 1}/{len(bysym)} {sym} {'ok' if S else 'none'}", flush=True)
        if not S:
            miss += sum(len(grp[k]) for k in keys)
            continue
        for k in keys:
            base = R.chains(grp[k], -1)
            s = 1 if k[2] == "LONG" else -1
            for i, c in enumerate(base):
                nxt = base[i + 1]["open"] if i + 1 < len(base) else None
                if nxt is not None and nxt - c["close"] <= GAP:
                    reopened += 1
                    continue
                a = B.copy_ret(S, k[2], c["open"] + DELAY, c["close"] + DELAY, STOP)
                h = {X: B.copy_ret(S, k[2], c["open"] + DELAY, c["close"] + X * H, STOP) for X in XS}
                p0 = px(S, c["close"] + DELAY)
                fp = {X: px(S, c["close"] + X * H) for X in XS}
                if a is None or any(v is None for v in h.values()) or not p0 or any(v is None for v in fp.values()):
                    miss += 1
                    continue
                res.append({"t": c["open"], "trader": k[0], "src": src_of.get(k[0], "?"), "good": k[0] in good,
                            "a": a, "h": h, "f": {X: s * (fp[X] / p0 - 1) * 100 for X in XS}})
    if not res:
        json.dump({"error": "no rows"}, open(out, "w"))
        print("::error::no rows")
        return
    O = {"missing": miss, "reopened_excluded": reopened, "good_traders": len(good), "traders20": len(n20)}
    groups = [("good", lambda r: r["good"]), ("all20", lambda r: True)]
    for src in sorted({r["src"] for r in res}):
        groups.append((f"good_{src}", lambda r, s=src: r["good"] and r["src"] == s))
    for name, f in groups:
        q = summ([r for r in res if f(r)])
        if not q:
            continue
        O[name] = q
        hs = " | ".join(f"{X}h: f {q[f'follow{X}h_mean']}/{q[f'follow{X}h_median']} d {q[f'hold{X}h']['diff_mean']} "
                        f"h1 {q[f'hold{X}h']['h1']} h2 {q[f'hold{X}h']['h2']} t {q[f'hold{X}h']['t_day']} pass {q[f'hold{X}h']['pass']}"
                        for X in XS)
        print(f"::notice title=early258 {name}::n {q['n']} ({q['traders']} tr) {q['from']}→{q['to']} | a {q['a_mean']} | "
              f"early {q['early_share']} against {q['against_share']} | {hs} | early-only hold4h {q.get('early_hold4h_mean')}")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=early258 meta::missing {miss}, reopened excluded {reopened}, good {len(good)}, traders20 {len(n20)}")


if __name__ == "__main__":
    main()
