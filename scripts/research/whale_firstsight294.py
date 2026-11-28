"""Item 294 (28.11): desk «Киты» copies opened as kind=new with entry outside the join window −3…+1% of the trader's entry.
Reads web/whales_live.json desk.closed/open; prints counts and the desk total with / without them. Read-only."""
import json, statistics as S, collections as C, sys
d = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "web/whales_live.json"))["desk"]
out = lambda c: c.get("gap_pct") is not None and (c["gap_pct"] > 1.0 or c["gap_pct"] < -3.0)
g = C.defaultdict(list)
for c in d["closed"]:
    g[("join" if c.get("join") else ("new" if c.get("gap_pct") is not None else "no_gap"), "out" if out(c) else "in")].append(c)
for k, v in sorted(g.items()):
    p = [c["pnl"] for c in v]
    print(k, len(v), "sum$", round(sum(p), 1), "median$", round(S.median(p), 2), "win", sum(x > 0 for x in p))
tot = sum(c["pnl"] for c in d["closed"]); bad = sum(c["pnl"] for c in g[("new", "out")])
print("closed", len(d["closed"]), "total$", round(tot, 1), "without new/out$", round(tot - bad, 1))
print("open new/out:", [(o["coin"], o.get("gap_pct"), round(o.get("upnl") or 0, 1)) for o in d["open"] if not o.get("join") and out(o)])
