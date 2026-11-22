"""Risk ladder backtest on the stored whale stream (owner 22.11.2026 18:07). RESEARCH ONLY. Pre-registered: the same 60-day base
stream as exp_variants.py (lock traders, copy at +2 min, stop −16 %, costs + funding), copies in order of their CLOSE time; a copy
risks level × $1 000 at its 16 % stop; compare fixed 1 %, fixed 2 % and the ladder 1 → 1.5 → 2 % (cnq/execution/risk_ladder.py,
the same rules as the real account). Report: P/L % of the budget, max drawdown %, level changes, both halves.
Usage: exp_ladder.py DB OUT.json"""
import json, os, sqlite3, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import bn_whale_delay as D  # noqa: E402
import exp_variants as EV  # noqa: E402
from cnq.execution import risk_ladder as RL  # noqa: E402

H, DAY, M1 = D.H, D.DAY, D.M1


def main():
    db, out = sys.argv[1], sys.argv[2]
    lock = EV.load_lock()
    rows = sqlite3.connect(db).execute("SELECT src, trader, coin, side, open_t, close_t FROM ws_trips WHERE src IN ('bn','okx') AND close_t > 0 "
                                       "AND coin IS NOT NULL AND coin != '' ORDER BY src, trader, open_t").fetchall()
    t_end = max(r[5] for r in rows); since = t_end - 60 * DAY
    eps, cur = [], {}
    for r in rows:
        tid = r[1] if str(r[1]).startswith(r[0] + ":") else f"{r[0]}:{r[1]}"
        if tid not in lock:
            continue
        k = (tid, r[2].upper(), r[3]); e = cur.get(k)
        if e and r[4] - e["open_t"] <= 4 * H:
            e["close_t"] = max(e["close_t"], r[5])
        else:
            e = {"coin": r[2].upper(), "side": r[3], "open_t": r[4], "close_t": r[5]}; cur[k] = e
            if r[4] >= since:
                eps.append(e)
    need = defaultdict(list)
    for e in eps:
        need[e["coin"] + "USDT"].append((e["open_t"] - H, e["close_t"] + H))
    data = {s: D.load_sym(s, sp) for s, sp in need.items()}
    tr = []
    for e in eps:
        S = data.get(e["coin"] + "USDT")
        v = EV.sim(S, e["side"], e["open_t"] + 2 * M1, e["close_t"] + 2 * M1) if S else None
        if v:
            tr.append((e["close_t"], v[0]))
    tr.sort()
    h = len(tr) // 2
    O = {"n": len(tr), "all": [RL.backtest(tr, 1000, m) for m in ("fixed1", "fixed2", "ladder")],
         "h1": [RL.backtest(tr[:h], 1000, m) for m in ("fixed1", "fixed2", "ladder")],
         "h2": [RL.backtest(tr[h:], 1000, m) for m in ("fixed1", "fixed2", "ladder")]}
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    for k in ("all", "h1", "h2"):
        print(f"::notice title=ladder {k}::" + json.dumps(O[k]))


if __name__ == "__main__":
    main()
