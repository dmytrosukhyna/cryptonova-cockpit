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
    kline_max_limit: int = 1440


@dataclass(frozen=True)
class Config:
    symbol: str = _env("SYMBOL", "BTC-USDT")
    # Starter universe (BingX symbol → Binance symbol). BTC first; the rest after BTC passes stage 2.
    universe: tuple[tuple[str, str], ...] = (
        ("BTC-USDT", "BTCUSDT"), ("ETH-USDT", "ETHUSDT"), ("SOL-USDT", "SOLUSDT"),
        ("BNB-USDT", "BNBUSDT"), ("XRP-USDT", "XRPUSDT"), ("DOGE-USDT", "DOGEUSDT"),
        ("ADA-USDT", "ADAUSDT"), ("AVAX-USDT", "AVAXUSDT"), ("LINK-USDT", "LINKUSDT"),
        ("LTC-USDT", "LTCUSDT"))
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
