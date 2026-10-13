import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys, json, time
from cnq.research.binance_master import load_master, to_candles
from cnq.research.strategies import Costs, simulate, random_trades, split_rows, stats
from cnq.research.derivatives import d1_funding_extreme_fade, d2_liquidation_cascade_fade, d3_oi_spike_continuation, d4_taker_ratio_extreme

sym = sys.argv[1]
D = "/tmp/claude-0/-home-claude/5491f1a8-ca5d-58c4-b6e0-e27b29449aba/scratchpad/pq/csv"
m = load_master(f"{D}/{sym}_15m.csv"); cs = to_candles(m)
cut = cs[int(len(cs) * 0.67)].open_time
costs = Costs()
out = {}
t0 = time.time()
for name, gen in (("funding_fade", lambda: d1_funding_extreme_fade(cs, m)),
                  ("liq_cascade_fade", lambda: d2_liquidation_cascade_fade(cs, m)),
                  ("oi_spike_cont", lambda: d3_oi_spike_continuation(cs, m)),
                  ("taker_absorption", lambda: d4_taker_ratio_extreme(cs, m))):
    tr = gen()
    rows = simulate(cs, tr, costs); rnd = simulate(cs, random_trades(cs, tr, reps=5), costs)
    out[name] = {}
    for lab, (r, rr) in {"IS": split_rows(rows, cut)[0:1] + split_rows(rnd, cut)[0:1],
                         "OOS": split_rows(rows, cut)[1:2] + split_rows(rnd, cut)[1:2]}.items():
        out[name][lab] = {"sig": stats(r), "rnd": stats(rr)}
out["seconds"] = round(time.time() - t0)
json.dump(out, open(f"data/deriv_{sym}.json", "w"))
print(sym, "done", out["seconds"], "s")
