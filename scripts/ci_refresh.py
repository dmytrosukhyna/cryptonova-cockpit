"""GitHub Actions refresh: 15m candles for the starter universe (BingX), ~400 days, into the SQLite store.
Only 15m is needed: the terminal resamples 1h/4h/1d itself. Failures per symbol are logged, not fatal."""
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from cnq.collector.backfill import backfill  # noqa: E402
from cnq.config import Config  # noqa: E402
from cnq.store.db import Store  # noqa: E402

cfg = Config()
store = Store(cfg.db_path)
days = int(os.environ.get("CNQ_CI_DAYS", "400"))
ok = 0
t0 = time.time()
for bx, _ in cfg.universe:
    try:
        r = backfill(Config(symbol=bx), store, days=days, intervals=("15m",))
        ok += 1
        print(bx, "15m", r.get("15m"))
    except Exception as e:  # noqa: BLE001
        print("backfill failed", bx, repr(e))
print(f"backfilled {ok}/{len(cfg.universe)} symbols in {time.time()-t0:.0f}s")
for bx in cfg.b_extra:   # lab-only alt coins: 75 days is enough (σ over 30 days, 14-day range, store check needs 60)
    try:
        r = backfill(Config(symbol=bx), store, days=75, intervals=("15m",))
        print(bx, "15m (lab)", r.get("15m"))
    except Exception as e:  # noqa: BLE001
        print("backfill failed", bx, repr(e))
sys.exit(0 if ok else 1)
