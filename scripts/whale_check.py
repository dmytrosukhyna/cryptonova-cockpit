"""One full whale study on a FRESH database from a GitHub runner (04.11 22:35, owner: «проверь BestMax и Queenstown Traders»;
the pipeline's own state is stale after the 19:28 UTC cache loss, so its study step is skipped). Read-only: nothing is published
or committed — the result is printed in the run's log: the owner-named OKX traders' measured 60-day numbers, the whales that pass the
bar and the follow list the rules pick. Runs the study in budgeted passes until a pass is complete (≤ ~40 minutes)."""
import json
import sqlite3
import sys
import time

sys.path.insert(0, ".")
from cnq.agents import whale_study as W  # noqa: E402

conn = sqlite3.connect("/tmp/whale_check.sqlite")
t0, d = time.time(), None
for i in range(30):
    d = W.run(conn, max_requests=400, budget_s=150)
    f = d["funnel"]
    print(f"pass {i + 1}: complete={d['complete']} studied={f['studied']} eligible={f['eligible']} waiting={f['waiting']} "
          f"candles_waiting={f['candles_waiting']} named={(d.get('funnel') or {}).get('okx_dropped')}", flush=True)
    if d["complete"] or time.time() - t0 > 40 * 60:
        break
cand = W.kv_get(conn, "candidates") or {}
print("OKX named in the list:", (cand.get("okx_why") or {}).get("named"), flush=True)
rows = d["top"] + d["others"]


def line(p):
    c2 = (p.get("copy") or {}).get(str(W.COPY_DELAY)) or p.get("copy2") or {}
    return json.dumps({"src": p.get("src"), "name": p.get("name") or p.get("short"), "addr": p.get("addr"), "pnl_60d": p.get("pnl_60d"),
                       "decisions": p.get("decisions", p.get("trips")), "win": p.get("win"), "pf": p.get("pf"), "win_closed": p.get("win_closed"),
                       "pf_closed": p.get("pf_closed"), "hold_med_h": p.get("hold_med_h"), "per_day": p.get("per_day"), "open_trips": p.get("open_trips"),
                       "open_loss": p.get("open_loss"), "copy2": {k: c2.get(k) for k in ("n", "avg_pct", "win")}, "eligible": p.get("eligible"),
                       "excluded": p.get("excluded_ru")}, ensure_ascii=False)


norm = lambda x: "".join(ch for ch in str(x or "").lower() if ch.isalnum())  # noqa: E731
for p in rows:
    if norm(p.get("name")) in W.OKX_NAMED:
        print("NAMED", line(p), flush=True)
for p in d["top"]:
    print("PASSED", line(p), flush=True)
print("FOLLOW", json.dumps(d["following"], ensure_ascii=False), flush=True)
print("BY_SOURCE", json.dumps(d.get("by_source")), flush=True)
