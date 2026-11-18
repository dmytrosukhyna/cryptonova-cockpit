"""Backlog 198 (18.11.2026): two list whales hold the same coin on the same side — exit by OUR whale, by the FIRST exit, or by
the LAST exit? Binance lead traders, research only — reads ws_trips from the whale study's database (Actions cache), writes nothing.

Trades are built exactly as bn_whale_same2.py (backlog 131): decisions = ws_trips merged within 4 h per coin+side, live bar
walk-forward (prior 60 d), copy +2 min, exit +2 min after the whale, stop −16 % from OUR entry, costs 0.11 % a round trip +
funding, 5m Binance candles; book = the live book (risk at the 16 % stop, daily stop 3 %, ≤ 15 open copies, one copy per coin,
first come first served).

A CASE = a copy we hold (whale A) while a bar-passing decision of ANOTHER trader B on the same coin and side is skipped as
«coin busy» (B opens after A, before A's exit). For each case three exits of OUR copy (same entry, same stop, same costs):
 OWN   = A's exit + 2 min (the live rule),
 FIRST = min(A, all such B) exit + 2 min,
 LAST  = max(A, all such B) exit + 2 min (we keep holding while any of them still holds).
Pre-registered before any result was seen (2 alternatives, primary pool top-10, all-bar = sign check):
 LAST passes if n ≥ 20 cases, sum(LAST − OWN) > 0, LAST better than OWN in ≥ 55 % of the cases where they differ, and the
 sum is > 0 in BOTH halves (by entry day); FIRST is judged by the same bar. Otherwise → noted, nothing changes.
Usage: bn_whale_lastexit.py DB OUT.json"""
import bisect, json, math, os, sqlite3, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import daystop_sim as D
from bn_whale_book import copy2

DAY, H, MIN = 86_400_000, 3_600_000, 60_000
NOT = D.NOTIONAL


def book(trades, mark, max_pos=15, X=3.0):
    ev = sorted(trades, key=lambda x: x["t_in"])
    openp, closed_today, blocked, taken, cases = [], {}, set(), [], []

    def close_until(t):
        nonlocal openp
        keep = []
        for p in openp:
            if p["t_out"] <= t:
                d = p["t_out"] // DAY; closed_today[d] = closed_today.get(d, 0.0) + p["ret"] * NOT
            else: keep.append(p)
        openp = keep

    for tr in ev:
        t = tr["t_in"]; d = t // DAY
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
            for p in same:
                if p["side"] == tr["side"] and p["w"] != tr["w"]: p["mates"].append(tr)
            continue
        if max_pos is not None and len(openp) >= max_pos: continue
        p = dict(tr, mates=[]); openp.append(p); taken.append(p)
    return [p for p in taken if p["mates"]]


def report(trades, data, mark, tag):
    cases = book(trades, mark)
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY; mid = (d0 + d1) // 2
    rows = []
    for p in cases:
        S = data.get(p["coin"] + "USDT")
        ends = [p["w_close"]] + [m["w_close"] for m in p["mates"]]
        v = {}
        for k, te in (("own", p["w_close"]), ("first", min(ends)), ("last", max(ends))):
            r = copy2(S, p["side"], p["t_in"], te + 2 * MIN)
            v[k] = r[2] if r and math.isfinite(r[2]) else None
        if None in v.values(): continue
        rows.append({"coin": p["coin"], "side": p["side"], "w": p["w"], "day": p["t_in"] // DAY, "mates": len(p["mates"]), **v})
    R = {"cases": len(rows), "days": d1 - d0 + 1}
    for k in ("own", "first", "last"):
        xs = [r[k] for r in rows]
        R[k] = {"sum": round(sum(xs), 2), "mean": round(st.mean(xs), 3) if xs else None,
                "win": round(sum(1 for x in xs if x > 0) / len(xs), 3) if xs else None}
    for alt in ("first", "last"):
        dif = [(r[alt] - r["own"], r["day"]) for r in rows]
        nz = [x for x, _ in dif if abs(x) > 1e-9]
        h1 = sum(x for x, dd in dif if dd <= mid); h2 = sum(x for x, dd in dif if dd > mid)
        sd = st.pstdev(nz) if len(nz) > 1 else 0
        t = (st.mean(nz) / (sd / math.sqrt(len(nz)))) if nz and sd > 0 else None
        better = sum(1 for x in nz if x > 0) / len(nz) if nz else None
        A = {"n": len(rows), "n_diff": len(nz), "sum": round(sum(x for x, _ in dif), 2), "h1": round(h1, 2), "h2": round(h2, 2),
             "better_share": round(better, 3) if better is not None else None, "t": round(t, 2) if t is not None else None,
             "worst": round(min(nz), 2) if nz else None, "best": round(max(nz), 2) if nz else None}
        A["pass"] = bool(len(rows) >= 20 and A["sum"] > 0 and (better or 0) >= 0.55 and h1 > 0 and h2 > 0)
        R[alt + "_minus_own"] = A
        print(f"::notice title={tag} {alt}−own::{A}", flush=True)
    print(f"::notice title={tag} exits::cases {R['cases']} own {R['own']} first {R['first']} last {R['last']}", flush=True)
    R["sample"] = rows[-12:]
    return R


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, pnl FROM ws_trips WHERE src='bn' AND close_t > 0 ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows: by[r[0]].append(r)
    cand, status = [], {}
    for tr, lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H: e["g"].append(r)
            else:
                e = {"w": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        for e in mine:
            e["close_t"] = max(x[4] for x in e["g"]); e["pnl"] = sum(x[5] or 0 for x in e["g"])
        mine.sort(key=lambda e: e["open_t"])
        done = []
        for e in mine:
            t0 = e["open_t"]
            pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
            bar = False
            if len(pri) >= 20:
                wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
                bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
            done.append((t0, e["close_t"], e["pnl"]))
            status.setdefault(tr, []).append((t0, bar, sum(d[2] for d in pri)))
            if bar and e["close_t"] - t0 > 2 * MIN: cand.append(e)
    print(f"bn bar-passing decisions: {len(cand)}", flush=True)
    syms = defaultdict(list)
    for e in cand: syms[e["coin"] + "USDT"].append(e)
    data = {sym: B.load_sym(sym, min(x["open_t"] for x in es) - DAY, max(x["close_t"] for x in es) + DAY) for sym, es in syms.items()}
    trades = []
    for e in cand:
        S = data.get(e["coin"] + "USDT")
        if not S: continue
        v = copy2(S, e["side"], e["open_t"] + 2 * MIN, e["close_t"] + 2 * MIN)
        if v is None or not math.isfinite(v[2]): continue
        trades.append({"w": e["w"], "coin": e["coin"], "side": e["side"], "t_in": e["open_t"] + 2 * MIN, "t_out": v[1], "a": v[0],
                       "ret": v[2], "w_close": e["close_t"]})

    def mark(coin, t):
        S = data.get(coin + "USDT")
        if not S: return None
        i = bisect.bisect_right(S["t"], t) - 1
        return S["k"][S["t"][i]][3] if i >= 0 else None

    O = {"traders": len({t["w"] for t in trades})}
    O["top10"] = report(D.top_n(trades, status, 10), data, mark, "bn top10")
    O["all_bar"] = report(trades, data, mark, "bn all-bar")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
