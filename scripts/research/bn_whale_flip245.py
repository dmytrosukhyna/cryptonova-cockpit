"""Backlog 245 (22.11.2026): a lead trader who keeps changing side on the same coin — copy as now, skip, or copy at half size?
Research only, calculation for the owner; real rules are not touched.

Why: 21–22.11 小富婆6688 turned SOL long→short→long→short within 22 h; all 3 copies lost (account −$12.07, desk −$149.64).
Not backlog 89 (flips within minutes) — here the flips come every few hours.

Data: ws_trips (src bn / okx / hl) from the whale study database (Actions cache), merged with data/whale_trades/*.jsonl «closed»
rows (dedup by src+trader+id). Decision = one trader's entries in the same coin + side within 4 h (as in bn_whale_stops.py).
Copy: first Binance USD-M 5m bar starting ≥ whale open + 2 min, exit at the first bar ≥ whale close + 2 min, stop −16 %,
costs 0.10 % a round trip + funding (B.copy_ret).

Pre-registered (written before any result was seen):
- flag of a NEW decision = the trader changed side on this coin ≥ 2 times among his decisions that opened in the 48 h before it
  (the new decision counted; only past information).  Info thresholds: ≥ 1 change in 24 h, ≥ 3 changes in 72 h.
- universe (primary) = traders with ≥ 20 decisions and, over their whole history, ≥ 70 % of decisions in plus and PF ≥ 2 on the
  trader's own result after costs (a proxy for «passed the bar» — in-sample for selection, but the flip filter is orthogonal to it);
  info: every trader with ≥ 20 decisions.
- variants: (a) as now; (b) skip flagged decisions; (c) flagged at half size. Equal weight per decision (% of the copy).
- pass: (b) or (c) better than (a) in BOTH halves of time (split by decision time) AND flagged decisions that were in plus
  ≤ 15 % of all plus decisions. 2 variants share one sign (both = −Σ flagged), so no further correction; t of the flagged mean shown.
Usage: bn_whale_flip245.py DB OUT.json"""
import glob, json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B

B.COST = 0.10
H = 3_600_000
DELAY = 2 * 60_000
STOP = 16.0
ALIAS = {"PEPE": "1000PEPE", "SHIB": "1000SHIB", "BONK": "1000BONK", "FLOKI": "1000FLOKI", "kPEPE": "1000PEPE", "kSHIB": "1000SHIB",
         "kBONK": "1000BONK", "kFLOKI": "1000FLOKI", "LUNC": "1000LUNC", "XEC": "1000XEC", "SATS": "1000SATS", "RATS": "1000RATS"}
RULES = {"f2_48": (2, 48), "f1_24": (1, 24), "f3_72": (3, 72)}


def trips(db):
    rows = {}
    try:
        c = sqlite3.connect(db)
        for r in c.execute("SELECT src, trader, id, coin, side, open_t, close_t, entry, exit, notional FROM ws_trips WHERE close_t > 0"):
            rows[(r[0], r[1], str(r[2]))] = r
    except Exception as e:  # noqa: BLE001
        print(f"::warning::ws_trips not read: {e}")
    n_db = len(rows)
    for f in sorted(glob.glob("data/whale_trades/*.jsonl")):
        for ln in open(f):
            try:
                d = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            if d.get("kind") != "closed" or not d.get("close_t"):
                continue
            k = (d["src"], d["trader"], str(d.get("id")))
            if k not in rows:
                rows[k] = (d["src"], d["trader"], k[2], d["coin"], d["side"], d["open_t"], d["close_t"], d.get("entry"), d.get("exit"), d.get("notional"))
    print(f"trips: ws_trips {n_db}, total after whale_trades merge {len(rows)}", flush=True)
    return list(rows.values())


def decisions(rows):
    by = defaultdict(list)
    for r in rows:
        if r[4] in ("LONG", "SHORT"):
            by[r[1]].append(r)
    out = {}
    for tr, lst in by.items():
        lst.sort(key=lambda r: r[5])
        cur, mine = {}, []
        for r in lst:
            k = (r[3], r[4]); e = cur.get(k)
            if e and r[5] - e["open_t"] <= 4 * H:
                e["g"].append(r)
            else:
                e = {"src": r[0], "trader": tr, "coin": r[3], "side": r[4], "open_t": r[5], "g": [r]}; cur[k] = e; mine.append(e)
        for e in mine:
            g = e["g"]; w = [max(x[9] or 0, 1e-9) for x in g]
            e["close_t"] = max(x[6] for x in g)
            s = 1 if e["side"] == "LONG" else -1
            ok = [(x, wi) for x, wi in zip(g, w) if x[7] and x[8]]
            e["wret"] = (sum(s * (x[8] / x[7] - 1) * 100 * wi for x, wi in ok) / sum(wi for _, wi in ok)) if ok else None
        out[tr] = mine
    return out


def flag(mine):
    """per coin: number of side changes among the trader's decisions opened in the window before (and including) each one"""
    per = defaultdict(list)
    for e in mine:
        per[e["coin"]].append(e)
    for lst in per.values():
        lst.sort(key=lambda e: e["open_t"])
        for i, e in enumerate(lst):
            for name, (k, hrs) in RULES.items():
                win = [x for x in lst[: i + 1] if e["open_t"] - x["open_t"] <= hrs * H]
                ch = sum(1 for a, b in zip(win, win[1:]) if a["side"] != b["side"])
                e[name] = ch >= k


def stat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "sum": round(sum(xs), 2)}
    m, sd = st.mean(xs), st.pstdev(xs)
    return {"n": len(xs), "sum": round(sum(xs), 2), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "win": round(sum(x > 0 for x in xs) / len(xs), 3)}


def compare(res, name):
    mid = res[len(res) // 2]["t"] if res else 0
    O = {}
    for part, f in (("all", lambda r: True), ("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid)):
        rs = [r for r in res if f(r)]
        fl = [r["cp"] for r in rs if r[name]]
        a = sum(r["cp"] for r in rs)
        O[part] = {"n": len(rs), "flagged": stat(fl), "unflagged": stat([r["cp"] for r in rs if not r[name]]), "a": round(a, 2), "b": round(a - sum(fl), 2), "c": round(a - 0.5 * sum(fl), 2),
                   "whale_flagged": stat([r["wret"] for r in rs if r[name] and r["wret"] is not None])}
    plus = [r for r in res if r["cp"] > 0]
    O["plus_cut_share"] = round(sum(1 for r in plus if r[name]) / len(plus), 3) if plus else None
    O["pass_b"] = O["h1"]["b"] > O["h1"]["a"] and O["h2"]["b"] > O["h2"]["a"] and (O["plus_cut_share"] or 1) <= 0.15
    O["pass_c"] = O["h1"]["c"] > O["h1"]["a"] and O["h2"]["c"] > O["h2"]["a"] and (O["plus_cut_share"] or 1) <= 0.15
    return O


def main():
    db, out = sys.argv[1], sys.argv[2]
    D = decisions(trips(db))
    allx = []
    good = set()
    for tr, mine in D.items():
        if len(mine) < 20:
            continue
        flag(mine)
        xs = [e["wret"] - B.COST for e in mine if e["wret"] is not None]
        if xs:
            pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
            if sum(x > 0 for x in xs) / len(xs) >= 0.70 and (neg == 0 or pos / neg >= 2):
                good.add(tr)
        allx += mine
    print(f"decisions (traders ≥ 20): {len(allx)}, traders {len({e['trader'] for e in allx})}, pass-bar proxy {len(good)}", flush=True)
    syms = defaultdict(list)
    for e in allx:
        syms[ALIAS.get(e["coin"], e["coin"]) + "USDT"].append(e)
    res, miss = [], 0
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        S = B.load_sym(sym, min(e["open_t"] for e in es) - H, max(e["close_t"] for e in es) + H)
        if n % 20 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if S else 'none'}", flush=True)
        if not S:
            miss += len(es); continue
        for e in es:
            v = B.copy_ret(S, e["side"], e["open_t"] + DELAY, e["close_t"] + DELAY, STOP)
            if v is None:
                miss += 1; continue
            r = {"trader": e["trader"], "src": e["src"], "coin": e["coin"], "side": e["side"], "t": e["open_t"], "cp": v,
                 "wret": (e["wret"] - B.COST) if e["wret"] is not None else None, "good": e["trader"] in good}
            for name in RULES:
                r[name] = e[name]
            res.append(r)
    res.sort(key=lambda r: r["t"])
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    O = {"n": len(res), "missing": miss, "traders": len({r["trader"] for r in res}), "good_traders": len(good),
         "from": time.strftime("%Y-%m-%d", time.gmtime(res[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(res[-1]["t"] / 1000)),
         "by_src": {s: sum(1 for r in res if r["src"] == s) for s in {r["src"] for r in res}}}
    for uni, f in (("good", lambda r: r["good"]), ("all20", lambda r: True), ("good_bn_okx", lambda r: r["good"] and r["src"] in ("bn", "okx"))):
        rs = [r for r in res if f(r)]
        O[uni] = {name: compare(rs, name) for name in RULES}
    top = defaultdict(lambda: [0, 0.0])
    for r in res:
        if r["good"] and r["f2_48"]:
            top[r["trader"]][0] += 1; top[r["trader"]][1] += r["cp"]
    O["flagged_by_trader"] = sorted(([k, v[0], round(v[1], 2)] for k, v in top.items()), key=lambda x: -x[1])[:15]
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=flip245 n::{O['n']} copies, {O['traders']} traders (good {len(good)}), {O['from']} → {O['to']}, missing {miss}, src {O['by_src']}")
    for uni in ("good", "all20", "good_bn_okx"):
        for name in RULES:
            q = O[uni][name]
            print(f"::notice title=flip245 {uni} {name}::flagged {q['all']['flagged']} | whale {q['all']['whale_flagged'].get('mean')} | "
                  f"a/b/c all {q['all']['a']}/{q['all']['b']}/{q['all']['c']} h1 {q['h1']['a']}/{q['h1']['b']}/{q['h1']['c']} "
                  f"h2 {q['h2']['a']}/{q['h2']['b']}/{q['h2']['c']} | h1fl {q['h1']['flagged'].get('mean')}/{q['h1']['flagged']['n']} "
                  f"h2fl {q['h2']['flagged'].get('mean')}/{q['h2']['flagged']['n']} | unfl {q['all']['unflagged'].get('mean')}/{q['all']['unflagged']['n']} | plus cut {q['plus_cut_share']} | pass b {q['pass_b']} c {q['pass_c']}")
    print("::notice title=flip245 by trader::" + json.dumps(O["flagged_by_trader"], ensure_ascii=False))


if __name__ == "__main__":
    main()
