"""Candle refresh: 15m candles for the starter universe (BingX) into the SQLite store.
Only 15m is needed: the terminal resamples 1h/4h/1d itself. Failures per symbol are logged, not fatal.

19.10.2026 (owner: «сделки должны моментально открываться»): this step took 11 minutes of every 18-minute GitHub run because it
re-checked 400 days of gaps and funding for 54 coins each time. Now:
  CNQ_REFRESH=quick — only the bars after the last stored one (the 18 core coins + the 36 lab coins unless CNQ_REFRESH_EXTRA=0);
                      a coin with no history still gets its full backfill;
  CNQ_REFRESH=full  — the old full check (gaps, funding, lab coins).
Default (GitHub): full once every 6 hours (UTC hour % 6 == 0, first 20 minutes), quick otherwise. The server engine
(cnq/engine.py) runs quick every minute and full every 15 minutes."""
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from cnq.bingx.rest import BingXRest  # noqa: E402
from cnq.collector.backfill import backfill  # noqa: E402
from cnq.config import Config  # noqa: E402
from cnq.store.db import Store  # noqa: E402


def mode() -> str:
    m = os.environ.get("CNQ_REFRESH", "").strip().lower()
    if m in ("quick", "full", "funding"):
        return m
    now = dt.datetime.now(dt.timezone.utc)
    return "full" if now.hour % 6 == 0 and now.minute < 20 else "quick"


def quick(store: Store, symbols: list[str], rest: BingXRest) -> int:
    """Only the bars after the last stored one (the last one is re-fetched: it may have been open)."""
    ok, now = 0, int(time.time() * 1000)
    for bx in symbols:
        try:
            _first, last, _n = store.candle_bounds(bx, "15m")
            if last is None:
                backfill(Config(symbol=bx), store, days=int(os.environ.get("CNQ_CI_DAYS", "400")), intervals=("15m",))
            else:
                store.upsert_candles(bx, "15m", rest.klines_range(bx, "15m", last, now))
            ok += 1
        except Exception as e:  # noqa: BLE001
            print("quick refresh failed", bx, repr(e))
    return ok


def main() -> int:
    cfg = Config()
    store = Store(cfg.db_path)
    days = int(os.environ.get("CNQ_CI_DAYS", "400"))
    m = mode()
    t0 = time.time()
    core = [bx for bx, _ in cfg.universe]
    if m == "funding":                       # the server engine, once an hour: the last 2 days of funding for the core coins
        rest, now, n = BingXRest(cfg.bingx), int(time.time() * 1000), 0
        for bx in core:
            try:
                store.upsert_funding(bx, rest.funding_history(bx, start_ms=now - 2 * 86_400_000, end_ms=now))
                n += 1
            except Exception as e:  # noqa: BLE001
                print("funding failed", bx, repr(e))
        print(f"funding {n}/{len(core)} in {time.time() - t0:.0f}s")
        return 0
    if m == "quick":
        rest = BingXRest(cfg.bingx)          # the documented limit is 1 request a second per IP — kept (18 coins ≈ 20 s)
        ok = quick(store, core, rest)
        extra = os.environ.get("CNQ_REFRESH_EXTRA", "1")
        if extra == "1":
            quick(store, list(cfg.b_extra), rest)
        elif "/" in extra:                   # "k/n": the k-th of n slices of the lab coins (the engine spreads them over cycles)
            k, n_ = (int(x) for x in extra.split("/"))
            quick(store, list(cfg.b_extra)[k::n_], rest)
        print(f"quick refresh {ok}/{len(core)} symbols in {time.time() - t0:.0f}s")
        return 0 if ok else 1
    ok = 0
    for bx in core:
        try:
            r = backfill(Config(symbol=bx), store, days=days, intervals=("15m",))
            ok += 1
            print(bx, "15m", r.get("15m"))
        except Exception as e:  # noqa: BLE001
            print("backfill failed", bx, repr(e))
    print(f"backfilled {ok}/{len(core)} symbols in {time.time() - t0:.0f}s")
    for bx in cfg.b_extra:   # lab-only alt coins: 75 days is enough (σ over 30 days, 14-day range, store check needs 60)
        try:
            r = backfill(Config(symbol=bx), store, days=75, intervals=("15m",))
            print(bx, "15m (lab)", r.get("15m"))
        except Exception as e:  # noqa: BLE001
            print("backfill failed", bx, repr(e))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
