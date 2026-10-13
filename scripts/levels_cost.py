"""Level-limit family with explicit costs: limit entry (maker), target (maker), stop (taker).
Variants: target 1R / 1.5R; retail vs VIP maker fees. Writes data/lvcost_<SYM>.json."""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cnq.store.db import Store
from cnq.features.levels import LevelContext
from cnq.research.hypotheses import h_limit_at_level
from cnq.research.strategies import Costs, Trade, simulate, split_rows, stats

sym = sys.argv[1]
st = Store("data/binance_research.sqlite")
cs = st.candles(f"BINANCE:{sym}", "15m")
cut = cs[int(len(cs) * 0.67)].open_time
ctx = LevelContext(cs, 16, every=1, lookback=300, left=3, right=3)
out = {}
t0 = time.time()
entries = h_limit_at_level(cs, ctx)
for tr_name, trend in (("all", 0), ("trend28", 96 * 28)):
    es = entries if trend == 0 else h_limit_at_level(cs, ctx, trend_bars=trend)
    for tgt in (1.0, 1.5, 2.0):
        trades = []
        for e in es:
            sign = 1 if e.direction == "LONG" else -1
            r = abs(e.entry - e.stop)
            trades.append(Trade(e.index, e.direction, e.entry, e.stop, e.entry + sign * tgt * r, 96 * 2 * 15, True, "lvl"))
        for fee_name, costs in (("retail", Costs(taker=0.0005, maker=0.0002, slippage=0.0002)),
                                ("vip", Costs(taker=0.0003, maker=0.0000, slippage=0.0001)),
                                ("zero", Costs(0, 0, 0))):
            rows = simulate(cs, trades, costs)
            ins, oos = split_rows(rows, cut)
            out[f"{tr_name}|tgt={tgt}|{fee_name}"] = {"IS": stats(ins), "OOS": stats(oos)}
out["_seconds"] = round(time.time() - t0)
json.dump(out, open(f"data/lvcost_{sym}.json", "w"))
print(sym, "done", out["_seconds"], "s")
