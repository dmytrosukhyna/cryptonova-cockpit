"""SQLite storage. Raw market data, derived objects and the trade journal live in
one file so backtests, replays and the live collector see the same history.

Tables are deliberately simple; every row carries the exchange timestamp so that
no module can accidentally read the future (replays filter by time)."""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Iterable

from ..bingx.rest import Candle

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
  symbol TEXT NOT NULL, interval TEXT NOT NULL, open_time INTEGER NOT NULL,
  open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER,
  quote_volume REAL, trades INTEGER, taker_buy_volume REAL, taker_buy_quote REAL,
  PRIMARY KEY (symbol, interval, open_time));
CREATE TABLE IF NOT EXISTS trades (
  symbol TEXT NOT NULL, time INTEGER NOT NULL, price REAL, qty REAL, aggressor TEXT,
  recv_time INTEGER);
CREATE INDEX IF NOT EXISTS trades_idx ON trades(symbol, time);
CREATE TABLE IF NOT EXISTS depth_snapshots (
  symbol TEXT NOT NULL, time INTEGER NOT NULL, synced INTEGER, bids TEXT, asks TEXT,
  bid_1pct REAL, ask_1pct REAL, bid_02pct REAL, ask_02pct REAL, spread REAL,
  PRIMARY KEY (symbol, time));
CREATE TABLE IF NOT EXISTS open_interest (
  symbol TEXT NOT NULL, time INTEGER NOT NULL, open_interest REAL, PRIMARY KEY (symbol, time));
CREATE TABLE IF NOT EXISTS funding (
  symbol TEXT NOT NULL, time INTEGER NOT NULL, rate REAL, PRIMARY KEY (symbol, time));
CREATE TABLE IF NOT EXISTS quality_log (
  time INTEGER NOT NULL, symbol TEXT, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS setups (
  id TEXT PRIMARY KEY, symbol TEXT, family TEXT, direction TEXT, status TEXT,
  created_time INTEGER, updated_time INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS setup_transitions (
  setup_id TEXT, time INTEGER, from_status TEXT, to_status TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS paper_trades (
  id TEXT PRIMARY KEY, run_id TEXT, setup_id TEXT, symbol TEXT, direction TEXT,
  mode TEXT, entry_time INTEGER, exit_time INTEGER, entry REAL, stop REAL,
  qty REAL, risk_usdt REAL, result_r REAL, net_pnl REAL, fees REAL, payload TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, kind TEXT, started INTEGER, finished INTEGER, params TEXT, summary TEXT);
"""


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- candles
    def upsert_candles(self, symbol: str, interval: str, candles: Iterable[Candle]) -> int:
        rows = [(symbol, interval, c.open_time, c.open, c.high, c.low, c.close, c.volume,
                 c.close_time, c.quote_volume, c.trades, c.taker_buy_volume, c.taker_buy_quote)
                for c in candles]
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def candles(self, symbol: str, interval: str, start: int | None = None,
                end: int | None = None, limit: int | None = None) -> list[Candle]:
        q = "SELECT open_time,open,high,low,close,volume,close_time,quote_volume,trades," \
            "taker_buy_volume,taker_buy_quote FROM candles WHERE symbol=? AND interval=?"
        args: list = [symbol, interval]
        if start is not None:
            q += " AND open_time>=?"; args.append(start)
        if end is not None:
            q += " AND open_time<=?"; args.append(end)
        q += " ORDER BY open_time"
        if limit:
            q = q.replace("ORDER BY open_time", "ORDER BY open_time DESC LIMIT ?")
            args.append(limit)
        rows = self.conn.execute(q, args).fetchall()
        if limit:
            rows.reverse()
        return [Candle(*r) for r in rows]

    def candle_bounds(self, symbol: str, interval: str) -> tuple[int | None, int | None, int]:
        r = self.conn.execute("SELECT MIN(open_time),MAX(open_time),COUNT(*) FROM candles "
                              "WHERE symbol=? AND interval=?", (symbol, interval)).fetchone()
        return r[0], r[1], r[2]

    # ----------------------------------------------------------- stream data
    def insert_trades(self, symbol: str, rows: list[dict]) -> None:
        with self.conn:
            self.conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?)",
                                  [(symbol, r["time"], r["price"], r["qty"], r["aggressor"],
                                    r.get("recv_time")) for r in rows])

    def insert_depth(self, symbol: str, snap: dict, metrics: dict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO depth_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)",
                (symbol, snap["T"], int(snap["synced"]), json.dumps(snap["bids"]),
                 json.dumps(snap["asks"]), metrics.get("bid_1pct"), metrics.get("ask_1pct"),
                 metrics.get("bid_02pct"), metrics.get("ask_02pct"), metrics.get("spread")))

    def insert_oi(self, symbol: str, time_ms: int, oi: float) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO open_interest VALUES (?,?,?)",
                              (symbol, time_ms, oi))

    def upsert_funding(self, symbol: str, rows: list[dict]) -> None:
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO funding VALUES (?,?,?)",
                                  [(symbol, r["time"], r["rate"]) for r in rows])

    def funding(self, symbol: str, start: int = 0) -> list[list]:
        """Settled funding rows [[time_ms, rate], …] for one perpetual since start (oldest first)."""
        return [[int(t), float(r)] for t, r in self.conn.execute(
            "SELECT time, rate FROM funding WHERE symbol=? AND time>=? AND rate IS NOT NULL ORDER BY time", (symbol, start)).fetchall()]

    def log_quality(self, time_ms: int, symbol: str, kind: str, detail: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO quality_log VALUES (?,?,?,?)",
                              (time_ms, symbol, kind, detail))

    # ---------------------------------------------------------------- setups
    def save_setup(self, s: dict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO setups VALUES (?,?,?,?,?,?,?,?)",
                (s["id"], s["symbol"], s["family"], s["direction"], s["status"],
                 s["created_time"], s["updated_time"], json.dumps(s, default=str)))

    def save_transition(self, setup_id: str, time_ms: int, a: str, b: str, reason: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO setup_transitions VALUES (?,?,?,?,?)",
                              (setup_id, time_ms, a, b, reason))

    def save_paper_trade(self, t: dict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (t["id"], t["run_id"], t["setup_id"], t["symbol"], t["direction"], t["mode"],
                 t["entry_time"], t["exit_time"], t["entry"], t["stop"], t["qty"], t["risk_usdt"],
                 t["result_r"], t["net_pnl"], t["fees"], json.dumps(t, default=str)))

    def save_run(self, run_id: str, kind: str, started: int, finished: int,
                 params: dict, summary: dict) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
                              (run_id, kind, started, finished, json.dumps(params), json.dumps(summary)))
