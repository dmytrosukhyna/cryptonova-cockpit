"""Import plain OHLCV CSV (timestamp,open,high,low,close,volume) into the store.

Used for open datasets (e.g. Bitstamp BTC/USD 1m on GitHub) when exchange APIs
are unreachable. Venue is kept in the symbol (e.g. "BITSTAMP:BTCUSD") and such
candles carry NO taker-buy volume: delta/CVD are set to NaN-equivalent (taker_buy
= volume/2 → delta 0) and the validation reports them as UNAVAILABLE."""
from __future__ import annotations

import csv
import gzip
import io

from ..bingx.rest import Candle
from ..config import INTERVAL_MS
from ..store.db import Store


def read_ohlcv_csv(path: str, ts_unit: str = "s") -> list[Candle]:
    opener = gzip.open if path.endswith(".gz") else open
    mult = 1000 if ts_unit == "s" else 1
    out = []
    with opener(path, "rt") as f:
        for r in csv.DictReader(f):
            t = int(float(r["timestamp"])) * mult
            v = float(r["volume"])
            out.append(Candle(t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                              v, t + 59_999, v * float(r["close"]), 0, v / 2, v / 2 * float(r["close"])))
    out.sort(key=lambda c: c.open_time)
    return out


def fill_gaps_flat(candles: list[Candle], interval: str = "1m") -> tuple[list[Candle], int]:
    """Bitstamp omits minutes with no trades. Fill them with flat zero-volume candles
    so resampling and ATR stay aligned. Returns (candles, filled_count)."""
    step = INTERVAL_MS[interval]
    out, filled = [], 0
    for c in candles:
        if out and c.open_time - out[-1].open_time > step:
            prev = out[-1]
            t = prev.open_time + step
            while t < c.open_time:
                out.append(Candle(t, prev.close, prev.close, prev.close, prev.close, 0.0, t + step - 1, 0.0, 0, 0.0, 0.0))
                filled += 1
                t += step
        out.append(c)
    return out, filled


def import_csv(store: Store, symbol: str, path: str, start_ms: int | None = None) -> dict:
    candles = read_ohlcv_csv(path)
    if start_ms:
        candles = [c for c in candles if c.open_time >= start_ms]
    candles, filled = fill_gaps_flat(candles)
    n = store.upsert_candles(symbol, "1m", candles)
    return {"rows": n, "filled_flat": filled, "first": candles[0].open_time if candles else None,
            "last": candles[-1].open_time if candles else None}
