"""Liquidation-cascade fade: parameter sweep on one symbol. Writes data/liq_<SYM>.json."""
import sys, os, json, time, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cnq.research.binance_master import load_master, to_candles
from cnq.research.strategies import Costs, simulate, random_trades, split_rows, stats
from cnq.research.derivatives import d2_liquidation_cascade_fade

sym = sys.argv[1]
D = "/tmp/claude-0/-home-claude/5491f1a8-ca5d-58c4-b6e0-e27b29449aba/scratchpad/pq/csv"
m = load_master(f"{D}/{sym}_15m.csv"); cs = to_candles(m)
cut = cs[int(len(cs) * 0.67)].open_time
costs = Costs()
out = {}
t0 = time.time()
grid = {"sigma_mult": (2.5, 3.0, 3.5), "liq_pct": (0.90, 0.95, 0.98), "wick_frac": (0.3, 0.5),
        "hold_bars": (8, 16, 32), "target_retrace": (0.5, 0.62, 1.0)}
keys = list(grid)
for vals in itertools.product(*grid.values()):
    kw = dict(zip(keys, vals))
    tr = d2_liquidation_cascade_fade(cs, m, **kw)
    rows = simulate(cs, tr, costs)
    ins, oos = split_rows(rows, cut)
    name = "|".join(f"{k}={v}" for k, v in kw.items())
    out[name] = {"IS": stats(ins), "OOS": stats(oos)}
# baseline random for the default set
tr = d2_liquidation_cascade_fade(cs, m)
rnd = simulate(cs, random_trades(cs, tr, reps=5), costs)
ri, ro = split_rows(rnd, cut)
out["_random_default"] = {"IS": stats(ri), "OOS": stats(ro)}
out["_seconds"] = round(time.time() - t0)
json.dump(out, open(f"data/liq_{sym}.json", "w"))
print(sym, "done", out["_seconds"], "s")
