"""Historical backfill of candles (all intervals) and funding history.

Idempotent: re-running only fetches what is missing at the tail (and fills gaps)."""
from __future__ import annotations

import logging
import time

from ..bingx.rest import BingXRest
from ..config import Config, INTERVAL_MS
from ..store.db import Store
from .quality import coverage, find_gaps, sanity

log = logging.getLogger("cnq.backfill")


def backfill(cfg: Config, store: Store, rest: BingXRest | None = None,
             days: int | None = None, intervals: tuple[str, ...] | None = None) -> dict:
    rest = rest or BingXRest(cfg.bingx)
    now = int(time.time() * 1000)
    start_wanted = now - (days or cfg.backfill_days) * 86_400_000
    report: dict = {}
    for interval in intervals or cfg.intervals:
        first, last, count = store.candle_bounds(cfg.symbol, interval)
        ranges = []
        if last is None:
            ranges.append((start_wanted, now))
        else:
            if first > start_wanted + INTERVAL_MS[interval]:
                ranges.append((start_wanted, first - 1))
            ranges.append((last, now))  # re-fetch last candle (it may have been open)
        fetched = 0
        for a, b in ranges:
            candles = rest.klines_range(cfg.symbol, interval, a, b)
            fetched += store.upsert_candles(cfg.symbol, interval, candles)
        stored = store.candles(cfg.symbol, interval, start=start_wanted)
        for gap_start, gap_end in find_gaps(stored, interval):
            # BingX occasionally skips candles with zero trades; refetch once, then log.
            fill = rest.klines_range(cfg.symbol, interval, gap_start, gap_end - 1)
            if fill:
                store.upsert_candles(cfg.symbol, interval, fill)
            else:
                store.log_quality(now, cfg.symbol, "candle_gap", f"{interval} {gap_start}-{gap_end}")
        stored = store.candles(cfg.symbol, interval, start=start_wanted)
        for p in sanity(stored):
            store.log_quality(now, cfg.symbol, "candle_sanity", f"{interval} {p}")
        report[interval] = {"fetched": fetched, **coverage(stored, interval)}
        log.info("%s %s: %s", cfg.symbol, interval, report[interval])
    # funding history (8h settlements → ~3/day, one request covers months)
    funding = rest.funding_history(cfg.symbol, start_ms=start_wanted, end_ms=now)
    store.upsert_funding(cfg.symbol, funding)
    report["funding"] = {"rows": len(funding)}
    return report
