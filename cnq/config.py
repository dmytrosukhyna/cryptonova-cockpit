"""Central configuration. Everything here can be overridden with environment variables
(CNQ_*). No secrets are needed for phase 1: only public market data is used."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(f"CNQ_{name}", default)


@dataclass(frozen=True)
class BingXConfig:
    # Public REST, no auth needed for market data. `.pro` is a network-failure fallback only.
    rest_live: tuple[str, ...] = ("https://open-api.bingx.com", "https://open-api.bingx.pro")
    rest_vst: tuple[str, ...] = ("https://open-api-vst.bingx.com", "https://open-api-vst.bingx.pro")
    ws_market: str = "wss://open-api-swap.bingx.com/swap-market"
    timeout_s: float = 10.0
    # Documented: market endpoints are limited to 1 request/s per IP.
    min_request_interval_s: float = 1.05
    # BingX returns at most 1000 candles per request and keeps the NEWEST ones: asking for 1440 silently leaves a hole of 440
    # candles at the start of every window (found 19.10.2026 — it cut 110 h out of every app-signal price path).
    kline_max_limit: int = 1000


@dataclass(frozen=True)
class Config:
    symbol: str = _env("SYMBOL", "BTC-USDT")
    # Starter universe (BingX symbol → Binance symbol). BTC first; the rest after BTC passes stage 2.
    universe: tuple[tuple[str, str], ...] = (
        ("BTC-USDT", "BTCUSDT"), ("ETH-USDT", "ETHUSDT"), ("SOL-USDT", "SOLUSDT"),
        ("BNB-USDT", "BNBUSDT"), ("XRP-USDT", "XRPUSDT"), ("DOGE-USDT", "DOGEUSDT"),
        ("ADA-USDT", "ADAUSDT"), ("AVAX-USDT", "AVAXUSDT"), ("LINK-USDT", "LINKUSDT"),
        ("LTC-USDT", "LTCUSDT"),
        # 15.10.2026: +8 coins from the 18-coin research set (rules B/E/F tested on all 18, docs/research/oi_flush.md)
        ("DOT-USDT", "DOTUSDT"), ("NEAR-USDT", "NEARUSDT"), ("BCH-USDT", "BCHUSDT"), ("TRX-USDT", "TRXUSDT"),
        ("SUI-USDT", "SUIUSDT"), ("APT-USDT", "APTUSDT"), ("ARB-USDT", "ARBUSDT"), ("OP-USDT", "OPUSDT"))
    # 17.10.2026: extra alt coins for the LAB account only (docs/research/alt100.md): the 40 alts with the highest Binance futures volume
    # over 365 days, minus 1000×-unit meme contracts. Rule B on 13 extras was switched off for the main accounts — no gain in 2025–26
    # without hindsight. These coins never feed breadth, forecasts or the panel.
    b_extra: tuple[str, ...] = ("ZEC-USDT", "WLD-USDT", "UNI-USDT", "FIL-USDT", "AAVE-USDT", "DASH-USDT", "XLM-USDT", "ONDO-USDT", "ICP-USDT",
                                "WIF-USDT", "HBAR-USDT", "FET-USDT", "AXS-USDT", "INJ-USDT", "CRV-USDT", "TIA-USDT", "QNT-USDT", "ETC-USDT",
                                "SEI-USDT", "LDO-USDT", "SAND-USDT", "JTO-USDT", "ATOM-USDT", "PENDLE-USDT", "ALGO-USDT", "CHZ-USDT", "JUP-USDT",
                                "APE-USDT", "AR-USDT", "PYTH-USDT", "STX-USDT", "BAT-USDT", "CFX-USDT", "COMP-USDT", "ENS-USDT", "MANA-USDT")
    db_path: str = _env("DB", "data/cnq.sqlite")
    # Candle intervals we keep. 1m is the base: delta/CVD are derived from taker-buy volume.
    intervals: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")
    backfill_days: int = int(_env("BACKFILL_DAYS", "365"))
    depth_snapshot_every_s: int = int(_env("DEPTH_EVERY_S", "5"))
    oi_poll_every_s: int = int(_env("OI_EVERY_S", "60"))
    bingx: BingXConfig = field(default_factory=BingXConfig)
    # Trading is OFF. There is no code path that places real orders in this phase.
    live_trading_enabled: bool = False


INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}
