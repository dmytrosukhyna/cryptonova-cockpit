"""Loader for the open 'Backtesting_Data' Binance USDT-perp 15m research dataset
(github.com/kbsingh1399/Backtesting_Data): 18 symbols, Sep 2020 → Sep 2026, each bar
carrying funding, open interest, liquidations, long/short ratios, taker buy/sell
volume, CVD, VWAP; plus a per-candle footprint ladder (volume by price bin).

Parquet is converted to CSV with hyparquet (node, pure JS); this module reads the CSV
into numpy arrays and into the Candle store. Fields flagged `is_imputed_metrics`
(mostly 2022) are kept but marked: OI/liquidation studies must exclude them.
"""
from __future__ import annotations

import csv
import math

import numpy as np

from ..bingx.rest import Candle

MASTER_COLS = ["open_time_ms", "close_time_ms", "open", "high", "low", "close", "volume_base", "volume_quote",
               "trade_count", "taker_buy_vol_btc", "taker_sell_vol_btc", "funding_rate_pct", "basis_usd",
               "open_interest_k", "open_interest_usd", "oi_change_pct", "long_liq_usd", "short_liq_usd",
               "ls_ratio_global", "ls_ratio_top", "top_account_ratio", "taker_volume_ratio", "session_vwap",
               "vwap_zscore", "volume_ratio", "spot_close", "index_close", "basis_index_bps", "is_imputed_metrics",
               "future_cvd_15m", "spot_cvd_15m", "session_vah", "session_val", "prev_day_vah", "prev_day_val"]


def _f(x: str) -> float:
    try:
        return float(x)
    except ValueError:
        return math.nan


def load_master(path: str) -> dict[str, np.ndarray]:
    cols: dict[str, list] = {c: [] for c in MASTER_COLS}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for c in MASTER_COLS:
                cols[c].append(_f(r.get(c, "")))
    out = {c: np.array(v, dtype=float) for c, v in cols.items()}
    out["t"] = out.pop("open_time_ms").astype(np.int64)
    return out


def to_candles(m: dict[str, np.ndarray]) -> list[Candle]:
    tb = m["taker_buy_vol_btc"]; v = m["volume_base"]
    tb = np.where(np.isnan(tb), v / 2, tb)
    return [Candle(int(m["t"][i]), m["open"][i], m["high"][i], m["low"][i], m["close"][i], v[i],
                   int(m["close_time_ms"][i]), m["volume_quote"][i], int(m["trade_count"][i]),
                   float(tb[i]), float(tb[i] * m["close"][i])) for i in range(len(m["t"]))]


def load_ladder(path: str) -> dict[int, list[tuple]]:
    """open_time → [(price_bin, bid_vol, ask_vol, delta, total, trades, is_poc, buy_imb, sell_imb), ...]"""
    out: dict[int, list[tuple]] = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out.setdefault(int(r["open_time_ms"]), []).append(
                (float(r["price_bin"]), float(r["bid_vol_coin"]), float(r["ask_vol_coin"]), float(r["net_delta_coin"]),
                 float(r["total_vol_coin"]), int(r["trade_count"]), int(r["is_poc"]), int(r["is_buy_imbalance"]),
                 int(r["is_sell_imbalance"])))
    return out
