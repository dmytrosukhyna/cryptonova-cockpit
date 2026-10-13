"""Data-quality checks: candle gaps, duplicates, stale streams."""
from __future__ import annotations

from ..bingx.rest import Candle
from ..config import INTERVAL_MS


def find_gaps(candles: list[Candle], interval: str) -> list[tuple[int, int]]:
    """Return (expected_open_time, actual_open_time) pairs where candles are missing."""
    step = INTERVAL_MS[interval]
    gaps = []
    for a, b in zip(candles, candles[1:]):
        if b.open_time - a.open_time != step:
            gaps.append((a.open_time + step, b.open_time))
    return gaps


def sanity(candles: list[Candle]) -> list[str]:
    problems = []
    for c in candles:
        if not (c.low <= min(c.open, c.close) and c.high >= max(c.open, c.close)):
            problems.append(f"ohlc inconsistent at {c.open_time}")
        if c.volume < 0 or c.taker_buy_volume < 0 or c.taker_buy_volume > c.volume * 1.0001:
            problems.append(f"taker volume inconsistent at {c.open_time}")
    return problems


def coverage(candles: list[Candle], interval: str) -> dict:
    if not candles:
        return {"count": 0, "gaps": 0, "coverage": 0.0}
    span = candles[-1].open_time - candles[0].open_time + INTERVAL_MS[interval]
    expected = span // INTERVAL_MS[interval]
    gaps = find_gaps(candles, interval)
    return {"count": len(candles), "expected": expected, "gaps": len(gaps),
            "coverage": round(len(candles) / expected, 4) if expected else 0.0,
            "first": candles[0].open_time, "last": candles[-1].open_time}
