"""Backlog 129 (10.11: BTC杰师傅 held a LTC long and a LTC short at once — the desk «flipped» twice in 15 min). Binance lead traders.

Research only — reads ws_trips from the whale study's database (Actions cache), writes nothing. Trades are built exactly as
bn_whale_side.py / bn_whale_same2.py: decisions = ws_trips merged within 4 h per coin+side, live bar walk-forward (prior 60 d),
copy +2 min, exit +2 min after the whale, stop −16 %, costs 0.11 % a round trip + funding, 5m Binance candles.

Part 1 — how common: a «hedge decision» = a decision of trader W in coin C on side S that opens while W still has an open
position (any trip, bar or not) on C on the OTHER side. Share among all decisions and among bar-passing / top-10 decisions;
how often the new leg is the larger one (maxOpenInterest × avgCost; this is the leg the live desk would follow).

Part 2 — book, pre-registered before any result was seen (1 comparison, top-10 is primary, all-bar secondary):
 LIVE (a) = what the live desk does today (cnq/agents/whale_study.py dominant_sides: one side per coin = the larger notional):
     when W's opposite leg arrives while we hold W's copy on C and the new leg is LARGER than the old one, our copy is closed at the
     5m mark (0.11 % cost, funding pro-rated as in book_sim._cut) and the new leg is copied (if it passes the book checks); when that
     hedge leg later closes while W's old leg is still open, the dominant side returns → the old leg is re-joined at that moment
     only if the price is within −3 % … +1 % of W's entry (the live join window), exit with W's old leg (+2 min) or the −16 % stop.
     A smaller new leg is ignored (live keeps the larger side).
 HOLD (b) = proposed: the same trader's opposite leg is never copied while we hold his copy on that coin; we keep the first leg.
 Both: risk 3 % at the 16 % stop, daily stop 3 %, ≤ 15 open, one copy per coin, another trader's opposite decision skipped.
 Decision rule (from the backlog line, fixed in advance): an engineering change is proposed to the owner if hedge decisions are
 > 2 % of top-10 decisions OR HOLD is not worse than LIVE on the top-10 total (HOLD − LIVE ≥ 0). Reported too: paired daily t in
 both halves, per-case delta, worst day and drawdown. Approximations: maxOpenInterest is the leg's peak size, not its size at that
 minute; the 2-poll confirmation (~1 min) is ignored.
Usage: bn_whale_hedge.py DB OUT.json"""
import bisect, heapq, json, math, os, sqlite3, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import daystop_sim as D
from bn_whale_book import copy2

DAY, H, MIN = 86_400_000, 3_600_000, 60_000
NOT = D.NOTIONAL


def merge(lst):
    cur, mine = {}, []
    for r in lst:                       # r = trader, coin, side, open_t, close_t, pnl, entry, notional
        k = (r[1], r[2]); e = cur.get(k)
        if e and r[3] - e["open_t"] <= 4 * H: e["g"].append(r)
        else:
            e = {"w": r[0], "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
    for e in mine:
        e["close_t"] = max(x[4] for x in e["g"]); e["pnl"] = sum(x[5] or 0 for x in e["g"])
        e["notional"] = max((x[7] or 0) for x in e["g"])
        q = sum((x[7] or 0) for x in e["g"])
        e["entry"] = sum((x[6] or 0) * (x[7] or 0) for x in e["g"]) / q if q > 0 else (e["g"][0][6] or 0)
    mine.sort(key=lambda e: e["open_t"])
    return mine


def simulate(trades, mark, S_of, mode, max_pos=15, X=3.0):
    """trades: dicts with w, coin, side, t_in, t_out, a, ret, hedge (the opposite open decision dict of the same trader or None),
    nb (new leg larger), old (that opposite decision)."""
    heap, seq = [], 0
    for x in trades:
        heapq.heappush(heap, (x["t_in"], seq, x)); seq += 1
    openp, closed_today, day_pnl = [], {}, {}
    blocked, taken, busy, flips, rejoins, cases = set(), 0, 0, 0, 0, []

    def book(d, v):
        day_pnl[d] = day_pnl.get(d, 0.0) + v; closed_today[d] = closed_today.get(d, 0.0) + v

    def close_until(t):
        nonlocal openp
        keep = []
        for p in sorted(openp, key=lambda p: p["t_out"]):
            if p["t_out"] <= t: book(p["t_out"] // DAY, p["ret"] * NOT)
            else: keep.append(p)
        openp = keep

    def cut(p, t):
        px, pe = mark(p["coin"], t), mark(p["coin"], p["t_out"])
        if not px or not pe: return None
        s = 1 if p["side"] == "LONG" else -1
        fund_full = s * (pe / p["a"] - 1) * 100 - B.COST - p["ret"]
        frac = (t - p["t_in"]) / max(1, p["t_out"] - p["t_in"])
        return s * (px / p["a"] - 1) * 100 - B.COST - fund_full * frac

    while heap:
        t, _, tr = heapq.heappop(heap)
        d = t // DAY
        close_until(t)
        if X is not None and d not in blocked:
            unl = 0.0
            for p in openp:
                px = mark(p["coin"], t)
                if px:
                    s = 1 if p["side"] == "LONG" else -1
                    u = s * (px / p["a"] - 1) * 100 * NOT
                    if u < 0: unl += u
            if closed_today.get(d, 0.0) + unl <= -X: blocked.add(d)
        if X is not None and d in blocked: continue
        same = [p for p in openp if p["coin"] == tr["coin"]]
        if same:
            p = same[0]
            if p["side"] == tr["side"] or p["w"] != tr["w"] or mode == "hold" or not tr.get("nb"):
                busy += 1; continue
            # LIVE: the same trader's larger opposite leg → close ours, copy the new leg
            r = cut(p, t)
            if r is None or not math.isfinite(r):
                busy += 1; continue
            book(d, r * NOT); flips += 1
            openp = [q for q in openp if q is not p]
            cases.append({"t": t, "old_final": p["ret"], "early": r, "new": tr["ret"]})
            old = tr.get("old")
            if old and old["close_t"] > tr["t_out"]:        # the hedge leg ends first → dominant side returns to the old leg
                S = S_of(tr["coin"])
                px = mark(tr["coin"], tr["t_out"])
                e = old["entry"]
                if S and px and e > 0:
                    gap = (px / e - 1) if old["side"] == "LONG" else (1 - px / e)   # >0 = we pay worse than the whale
                    if -0.03 <= gap <= 0.01:
                        v = copy2(S, old["side"], tr["t_out"], old["close_t"] + 2 * MIN)
                        if v and math.isfinite(v[2]):
                            rj = {"w": tr["w"], "coin": tr["coin"], "side": old["side"], "t_in": tr["t_out"], "t_out": v[1],
                                  "a": v[0], "ret": v[2], "rejoin": True}
                            heapq.heappush(heap, (rj["t_in"] + 1, seq, rj)); seq += 1
        if max_pos is not None and len(openp) >= max_pos:
            busy += 1; continue
        openp.append(tr); taken += 1
        if tr.get("rejoin"): rejoins += 1
    close_until(float("inf"))
    return {"days": day_pnl, "taken": taken, "busy": busy, "flips": flips, "rejoins": rejoins, "cases": cases}


def report(trades, mark, S_of, tag):
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    hd = [t for t in trades if t.get("hedge")]
    R = {"n": len(trades), "days": d1 - d0 + 1, "hedge_n": len(hd), "hedge_share": round(len(hd) / len(trades), 4) if trades else None,
         "hedge_larger_n": sum(1 for t in hd if t.get("nb")),
         "hedge_mean_ret": round(st.mean(t["ret"] for t in hd), 3) if hd else None,
         "other_mean_ret": round(st.mean(t["ret"] for t in trades if not t.get("hedge")), 3)}
    print(f"::notice title={tag} share::{ {k: v for k, v in R.items()} }", flush=True)
    sims = {k: simulate(trades, mark, S_of, k) for k in ("live", "hold")}
    for k, S in sims.items():
        cell = {"taken": S["taken"], "busy": S["busy"], "flips": S["flips"], "rejoins": S["rejoins"]}
        for p, (a, b) in parts: cell[p] = D.curve_stats(S["days"], a, b)
        cs = S["cases"]
        if cs:
            cell["cases"] = {"n": len(cs), "old_final": round(st.mean(x["old_final"] for x in cs), 3),
                             "early": round(st.mean(x["early"] for x in cs), 3), "new": round(st.mean(x["new"] for x in cs), 3),
                             "delta_mean": round(st.mean(x["new"] + x["early"] - x["old_final"] for x in cs), 3),
                             "h1": sum(1 for x in cs if x["t"] // DAY <= mid), "h2": sum(1 for x in cs if x["t"] // DAY > mid)}
        R[k] = cell
        print(f"::notice title={tag} {k}::{cell}", flush=True)
    P = {p: D.paired(sims["hold"]["days"], sims["live"]["days"], a, b) for p, (a, b) in parts}
    P["share_gt_2pct"] = bool(R["hedge_share"] and R["hedge_share"] > 0.02)
    P["hold_not_worse"] = bool(P["all"]["d"] >= 0)
    P["propose"] = P["share_gt_2pct"] or P["hold_not_worse"]
    R["hold_minus_live"] = P
    print(f"::notice title={tag} hold−live::{P}", flush=True)
    return R


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, pnl, entry, notional FROM ws_trips "
                     "WHERE src='bn' AND close_t > 0 ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows: by[r[0]].append(r)
    cand, status, alld, allh = [], {}, 0, 0
    for tr, lst in by.items():
        mine = merge(lst)
        done = []
        for e in mine:
            t0 = e["open_t"]
            opp = [o for o in mine if o is not e and o["coin"] == e["coin"] and o["side"] != e["side"] and o["open_t"] < t0 < o["close_t"]]
            e["hedge"] = bool(opp)
            e["old"] = max(opp, key=lambda o: o["notional"]) if opp else None
            e["nb"] = bool(opp and e["notional"] > e["old"]["notional"])
            alld += 1; allh += e["hedge"]
            pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
            bar = False
            if len(pri) >= 20:
                wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
                bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
            done.append((t0, e["close_t"], e["pnl"]))
            status.setdefault(tr, []).append((t0, bar, sum(d[2] for d in pri)))
            if bar and e["close_t"] - t0 > 2 * MIN: cand.append(e)
    print(f"::notice title=bn hedge all decisions::{alld} decisions of {len(by)} traders, hedge {allh} ({allh / max(1, alld):.2%}); "
          f"bar-passing {len(cand)}, hedge {sum(e['hedge'] for e in cand)}", flush=True)
    syms = defaultdict(list)
    for e in cand:
        syms[e["coin"] + "USDT"].append(e)
        if e["old"]: syms[e["coin"] + "USDT"].append(e["old"])
    data = {sym: B.load_sym(sym, min(x["open_t"] for x in es) - DAY, max(x["close_t"] for x in es) + DAY) for sym, es in syms.items()}
    trades = []
    for e in cand:
        S = data.get(e["coin"] + "USDT")
        if not S: continue
        v = copy2(S, e["side"], e["open_t"] + 2 * MIN, e["close_t"] + 2 * MIN)
        if v is None or not math.isfinite(v[2]): continue
        trades.append({"w": e["w"], "coin": e["coin"], "side": e["side"], "t_in": e["open_t"] + 2 * MIN, "t_out": v[1], "a": v[0],
                       "ret": v[2], "hedge": e["hedge"], "nb": e["nb"], "old": e["old"]})

    def mark(coin, t):
        S = data.get(coin + "USDT")
        if not S: return None
        i = bisect.bisect_right(S["t"], t) - 1
        return S["k"][S["t"][i]][3] if i >= 0 else None

    S_of = lambda coin: data.get(coin + "USDT")
    O = {"traders": len({t["w"] for t in trades}), "all_decisions": alld, "all_hedge": allh}
    O["top10"] = report(D.top_n(trades, status, 10), mark, S_of, "bn top10")
    O["all_bar"] = report(trades, mark, S_of, "bn all-bar")
    for k in ("top10", "all_bar"):
        for m in ("live", "hold"): O[k][m].pop("cases_raw", None)
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
