"""Long-only liquidation fade variants per symbol → data/liql_<SYM>.json"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cnq.research.binance_master import load_master, to_candles
from cnq.research.strategies import Costs, simulate, split_rows, stats
from cnq.research.derivatives import d2_liquidation_cascade_fade
sym = sys.argv[1]
D = "/tmp/claude-0/-home-claude/5491f1a8-ca5d-58c4-b6e0-e27b29449aba/scratchpad/pq/csv"
m = load_master(f"{D}/{sym}_15m.csv"); cs = to_candles(m)
cut = cs[int(len(cs) * 0.67)].open_time
base = dict(sigma_mult=3.5, liq_pct=0.98, wick_frac=0.5, side="LONG")
variants = {
 "strict_L_hold32": dict(base, hold_bars=32),
 "strict_L_hold64": dict(base, hold_bars=64),
 "strict_L_hold96": dict(base, hold_bars=96),
 "strict_L_hold32_tgt1.5R": dict(base, hold_bars=32, target_r=1.5),
 "strict_L_hold32_tgt2R": dict(base, hold_bars=32, target_r=2.0),
 "strict_L_hold64_tgt2R": dict(base, hold_bars=64, target_r=2.0),
 "strict_L_hold32_stop0.5": dict(base, hold_bars=32, stop_atr=0.5),
 "strict_L_hold32_vol2x": dict(base, hold_bars=32, vol_mult=2.0),
 "sigma3_L_hold32": dict(base, sigma_mult=3.0, hold_bars=32),
 "sigma4_L_hold32": dict(base, sigma_mult=4.0, hold_bars=32),
 "pct95_L_hold32": dict(base, liq_pct=0.95, hold_bars=32),
 "wick0.6_L_hold32": dict(base, wick_frac=0.6, hold_bars=32),
}
out = {}
rows_keep = {}
for name, kw in variants.items():
    rows = simulate(cs, d2_liquidation_cascade_fade(cs, m, **kw), Costs())
    ins, oos = split_rows(rows, cut)
    out[name] = {"IS": stats(ins), "OOS": stats(oos)}
    rows_keep[name] = [{k: r[k] for k in ("time","net_pct","net_r","kind","minutes")} for r in rows]
json.dump({"stats": out, "rows": rows_keep}, open(f"data/liql_{sym}.json", "w"))
print(sym, "done")
