"""Import Binance daily datasets into the SQLite store.

Venue is encoded in the symbol: Binance rows are stored under "BINANCE:BTCUSDT",
BingX rows under "BTC-USDT". They are never mixed silently."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from ..store.db import Store
from . import public_data as pd_

log = logging.getLogger("cnq.binance.load")

EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS book_depth_pct (
  symbol TEXT NOT NULL, time INTEGER NOT NULL, percentage REAL NOT NULL, depth REAL, notional REAL,
  PRIMARY KEY (symbol, time, percentage));
CREATE TABLE IF NOT EXISTS metrics (
  symbol TEXT NOT NULL, time INTEGER NOT NULL, open_interest REAL, open_interest_value REAL,
  top_ls_accounts REAL, top_ls_positions REAL, global_ls_accounts REAL, taker_buy_sell_ratio REAL,
  PRIMARY KEY (symbol, time));
"""


def venue_symbol(symbol: str) -> str:
    return f"BINANCE:{symbol}"


def load_klines(store: Store, symbol: str, interval: str, start: date, end: date, cache_dir: str) -> int:
    n = 0
    for p in pd_.sync("klines", symbol, start, end, f"{cache_dir}/klines/{interval}", interval):
        candles = [pd_.kline_row_to_candle(r) for r in pd_.read_rows(p, "klines")]
        n += store.upsert_candles(venue_symbol(symbol), interval, candles)
    return n


def load_aggtrades(store: Store, symbol: str, start: date, end: date, cache_dir: str) -> int:
    n = 0
    for p in pd_.sync("aggTrades", symbol, start, end, f"{cache_dir}/aggTrades"):
        buf = []
        for r in pd_.read_rows(p, "aggTrades"):
            buf.append(pd_.aggtrade_row(r))
            if len(buf) >= 50_000:
                store.insert_trades(venue_symbol(symbol), buf); n += len(buf); buf = []
        if buf:
            store.insert_trades(venue_symbol(symbol), buf); n += len(buf)
        log.info("aggTrades %s loaded (%d rows so far)", p, n)
    return n


def load_book_depth(store: Store, symbol: str, start: date, end: date, cache_dir: str) -> int:
    store.conn.executescript(EXTRA_SCHEMA)
    n = 0
    for p in pd_.sync("bookDepth", symbol, start, end, f"{cache_dir}/bookDepth"):
        rows = []
        for r in pd_.read_rows(p, "bookDepth"):
            ts = r["timestamp"]
            t = int(ts) if ts.isdigit() else _parse_ts(ts)
            rows.append((venue_symbol(symbol), t, float(r["percentage"]), float(r["depth"]), float(r["notional"])))
        with store.conn:
            store.conn.executemany("INSERT OR REPLACE INTO book_depth_pct VALUES (?,?,?,?,?)", rows)
        n += len(rows)
    return n


def load_metrics(store: Store, symbol: str, start: date, end: date, cache_dir: str) -> int:
    store.conn.executescript(EXTRA_SCHEMA)
    n = 0
    for p in pd_.sync("metrics", symbol, start, end, f"{cache_dir}/metrics"):
        rows = []
        for r in pd_.read_rows(p, "metrics"):
            ts = r["create_time"]
            t = int(ts) if ts.isdigit() else _parse_ts(ts)
            rows.append((venue_symbol(symbol), t, _f(r["sum_open_interest"]), _f(r["sum_open_interest_value"]),
                         _f(r["sum_toptrader_long_short_ratio"]), _f(r.get("count_toptrader_long_short_ratio")),
                         _f(r["count_long_short_ratio"]), _f(r["sum_taker_long_short_vol_ratio"])))
        with store.conn:
            store.conn.executemany("INSERT OR REPLACE INTO metrics VALUES (?,?,?,?,?,?,?,?)", rows)
        n += len(rows)
    return n


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _parse_ts(s: str) -> int:
    """'2023-01-01 00:00:00' → ms (UTC)."""
    from datetime import datetime, timezone
    return int(datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)


def sync_all(store: Store, symbol: str, days: int, cache_dir: str, with_aggtrades: bool = False) -> dict:
    end = date.today() - timedelta(days=1)       # yesterday is the newest daily file
    start = end - timedelta(days=days - 1)
    rep = {"klines_1m": load_klines(store, symbol, "1m", start, end, cache_dir),
           "klines_1h": load_klines(store, symbol, "1h", start, end, cache_dir),
           "metrics": load_metrics(store, symbol, start, end, cache_dir),
           "book_depth": load_book_depth(store, symbol, start, end, cache_dir)}
    if with_aggtrades:
        rep["agg_trades"] = load_aggtrades(store, symbol, start, end, cache_dir)
    return rep
