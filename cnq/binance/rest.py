"""Binance USDⓈ-M public REST (no key) — live complements to the datasets:
open-interest history, long/short ratios, taker ratio, funding history, and the
liquidation stream (WebSocket `<symbol>@forceOrder`, which BingX does not offer)."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

FAPI = "https://fapi.binance.com"
WS = "wss://fstream.binance.com/ws"


def _get(path: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> Any:
    q = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    with urllib.request.urlopen(f"{FAPI}{path}?{q}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def open_interest_hist(symbol: str, period: str = "5m", limit: int = 500,
                       start_ms: int | None = None, end_ms: int | None = None) -> list[dict]:
    """period: 5m 15m 30m 1h 2h 4h 6h 12h 1d; Binance keeps only ~30 days here —
    the daily `metrics` files are the long-term source."""
    rows = _get("/futures/data/openInterestHist", {"symbol": symbol, "period": period, "limit": limit,
                                                  "startTime": start_ms, "endTime": end_ms})
    return [{"time": int(r["timestamp"]), "open_interest": float(r["sumOpenInterest"]),
             "open_interest_value": float(r["sumOpenInterestValue"])} for r in rows]


def long_short_ratio(symbol: str, kind: str = "global", period: str = "5m", limit: int = 500) -> list[dict]:
    path = {"global": "/futures/data/globalLongShortAccountRatio",
            "top_accounts": "/futures/data/topLongShortAccountRatio",
            "top_positions": "/futures/data/topLongShortPositionRatio",
            "taker": "/futures/data/takerlongshortRatio"}[kind]
    rows = _get(path, {"symbol": symbol, "period": period, "limit": limit})
    out = []
    for r in rows:
        ratio = r.get("longShortRatio") or r.get("buySellRatio")
        out.append({"time": int(r["timestamp"]), "ratio": float(ratio)})
    return out


def funding_history(symbol: str, start_ms: int | None = None, end_ms: int | None = None,
                    limit: int = 1000) -> list[dict]:
    rows = _get("/fapi/v1/fundingRate", {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": limit})
    return [{"time": int(r["fundingTime"]), "rate": float(r["fundingRate"])} for r in rows]


def liquidation_stream_url(symbol: str) -> str:
    """Subscribe with a websocket client; each message is one forced liquidation order:
    o.s symbol, o.S side (SELL = long liquidated), o.p price, o.q qty, o.T time."""
    return f"{WS}/{symbol.lower()}@forceOrder"


def server_time() -> int:
    return int(_get("/fapi/v1/time")["serverTime"])


if __name__ == "__main__":  # quick connectivity check
    t0 = time.time()
    print("server time", server_time(), "latency ms", round((time.time() - t0) * 1000))
    print(open_interest_hist("BTCUSDT", limit=2))
