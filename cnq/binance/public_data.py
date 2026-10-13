"""Binance USDⓈ-M public datasets (data.binance.vision) — the research source.

Why Binance for research: it is the deepest BTC perp market and the only venue
that publishes, for free, years of daily files with:
  klines (with taker-buy volume)         → price, delta, CVD
  aggTrades (every aggregated trade)     → order flow, absorption, footprint
  bookDepth (depth at ±1..5% every min)  → liquidity history (what BingX cannot give)
  metrics (every 5 min)                  → open interest, top-trader & global
                                           long/short ratios, taker buy/sell ratio
Execution still happens on BingX; prices track Binance within basis points.

Files: https://data.binance.vision/data/futures/um/daily/<type>/<SYMBOL>/<SYMBOL>-<type>-YYYY-MM-DD.zip
(klines add the interval: .../klines/BTCUSDT/1m/BTCUSDT-1m-YYYY-MM-DD.zip). Each has a .CHECKSUM (sha256).
Column layouts are read from the CSV header when present; otherwise the documented
layouts below are used. Verify on first run with `python -m cnq.cli binance-peek`.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from typing import Iterator

from ..bingx.rest import Candle

log = logging.getLogger("cnq.binance")
BASE = "https://data.binance.vision/data/futures/um/daily"

LAYOUTS = {
    "klines": ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
               "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"],
    "aggTrades": ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id",
                  "transact_time", "is_buyer_maker"],
    "bookDepth": ["timestamp", "percentage", "depth", "notional"],
    "metrics": ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
                "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
                "count_long_short_ratio", "sum_taker_long_short_vol_ratio"],
}


def file_url(kind: str, symbol: str, day: date, interval: str | None = None) -> str:
    if kind == "klines":
        return f"{BASE}/klines/{symbol}/{interval}/{symbol}-{interval}-{day:%Y-%m-%d}.zip"
    return f"{BASE}/{kind}/{symbol}/{symbol}-{kind}-{day:%Y-%m-%d}.zip"


def download(url: str, cache_dir: str, verify: bool = True, timeout: float = 60) -> str | None:
    """Download a daily zip (and verify its sha256 CHECKSUM). Returns the local path,
    or None when the file does not exist (404: not published yet / no data that day)."""
    os.makedirs(cache_dir, exist_ok=True)
    local = os.path.join(cache_dir, url.rsplit("/", 1)[1])
    if os.path.exists(local):
        return local
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            blob = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    if verify:
        with urllib.request.urlopen(url + ".CHECKSUM", timeout=timeout) as r:
            expected = r.read().decode().split()[0]
        if hashlib.sha256(blob).hexdigest() != expected:
            raise RuntimeError(f"checksum mismatch for {url}")
    with open(local, "wb") as f:
        f.write(blob)
    return local


def read_rows(zip_path: str, kind: str) -> Iterator[dict]:
    """Yield CSV rows as dicts. Uses the header line if present, else the known layout."""
    with zipfile.ZipFile(zip_path) as z:
        name = z.namelist()[0]
        with z.open(name) as f:
            text = io.TextIOWrapper(f, encoding="utf-8")
            reader = csv.reader(text)
            first = next(reader, None)
            if first is None:
                return
            if any(ch.isalpha() for ch in first[0]):  # header row
                cols = first
            else:
                cols = LAYOUTS[kind]
                yield dict(zip(cols, first))
            for row in reader:
                yield dict(zip(cols, row))


def days(start: date, end: date) -> Iterator[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------- converters
def kline_row_to_candle(r: dict) -> Candle:
    return Candle(int(r["open_time"]) // (1000 if int(r["open_time"]) > 10**14 else 1),  # µs-safe
                  float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                  float(r["volume"]), int(r["close_time"]) // (1000 if int(r["close_time"]) > 10**14 else 1),
                  float(r["quote_volume"]), int(r["count"]), float(r["taker_buy_volume"]),
                  float(r["taker_buy_quote_volume"]))


def aggtrade_row(r: dict) -> dict:
    t = int(r["transact_time"])
    return {"time": t // (1000 if t > 10**14 else 1), "price": float(r["price"]), "qty": float(r["quantity"]),
            "aggressor": "sell" if r["is_buyer_maker"].strip().lower() in ("true", "1") else "buy",
            "recv_time": None}


def sync(kind: str, symbol: str, start: date, end: date, cache_dir: str,
         interval: str | None = None) -> list[str]:
    """Download every missing daily file in [start, end]. Returns local paths (existing ones)."""
    paths = []
    for d in days(start, end):
        p = download(file_url(kind, symbol, d, interval), cache_dir)
        if p:
            paths.append(p)
        else:
            log.info("%s %s %s: not available", kind, symbol, d)
    return paths
