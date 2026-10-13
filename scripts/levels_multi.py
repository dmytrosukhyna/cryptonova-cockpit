import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys, json, time
from cnq.store.db import Store
from cnq.research.hypotheses import LevelContext, h_limit_at_level, h_confirmed_bounce_next_open, evaluate, random_like, split as hsplit, K
sym = sys.argv[1]
st = Store("data/binance_research.sqlite")
cs = st.candles(f"BINANCE:{sym}", "15m")
ctx = LevelContext(cs, 16, every=1, lookback=300, left=3, right=3)
out = {}
t0 = time.time()
for name, gen in (("limit_t2", lambda: h_limit_at_level(cs, ctx)),
                  ("limit_t3", lambda: h_limit_at_level(cs, ctx, min_touches=3)),
                  ("confirmed", lambda: h_confirmed_bounce_next_open(cs, ctx))):
    es = gen(); ins, oos = hsplit(es, len(cs))
    out[name] = {}
    for lab, e in (("IS", ins), ("OOS", oos)):
        out[name][lab] = {"sig": evaluate(cs, e, 96), "rnd": evaluate(cs, random_like(cs, e, reps=3), 96)}
out["seconds"] = round(time.time() - t0)
json.dump(out, open(f"data/levels_{sym}.json", "w"))
print(sym, "done", out["seconds"], "s")
