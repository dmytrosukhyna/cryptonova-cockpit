"""BingX USDT-M perpetual public market data (REST). Stdlib only.

Endpoints and limits come from the official BingX api-ai-skills reference
(skills/swap-market/api-reference.md). Market endpoints need no API key.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..config import BingXConfig, INTERVAL_MS


class BingXError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float          # base asset
    close_time: int
    quote_volume: float
    trades: int
    taker_buy_volume: float  # base asset bought by aggressive buyers
    taker_buy_quote: float

    @property
    def taker_sell_volume(self) -> float:
        return self.volume - self.taker_buy_volume

    @property
    def delta(self) -> float:
        """Aggressive buy volume minus aggressive sell volume (base asset)."""
        return 2 * self.taker_buy_volume - self.volume


class BingXRest:
    """Minimal rate-limited client. One request per second per IP is the documented limit."""

    def __init__(self, cfg: BingXConfig | None = None, vst: bool = False):
        self.cfg = cfg or BingXConfig()
        self.bases = self.cfg.rest_vst if vst else self.cfg.rest_live
        self._last_request = 0.0

    # ------------------------------------------------------------------ core
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        params["timestamp"] = int(time.time() * 1000)
        query = urllib.parse.urlencode(params)
        wait = self.cfg.min_request_interval_s - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        last_err: Exception | None = None
        for i, base in enumerate(self.bases):
            url = f"{base}{path}?{query}"
            try:
                self._last_request = time.monotonic()
                with urllib.request.urlopen(url, timeout=self.cfg.timeout_s) as resp:
                    body = json.loads(resp.read().decode())
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                # Network-level failure: documented policy allows the .pro fallback.
                last_err = e
                continue
            if body.get("code", 0) != 0:
                code = body.get("code")
                if code == 100410:  # rate limit — back off and retry once
                    time.sleep(2.0)
                    return self._get(path, {k: v for k, v in params.items() if k != "timestamp"})
                raise BingXError(f"{path}: code={code} msg={body.get('msg')}")
            return body.get("data")
        raise BingXError(f"{path}: network failure on all hosts: {last_err}")

    # ------------------------------------------------------------- endpoints
    def server_time(self) -> int:
        return int(self._get("/openApi/swap/v2/server/time")["serverTime"])

    def contracts(self, symbol: str | None = None) -> list[dict]:
        return self._get("/openApi/swap/v2/quote/contracts", {"symbol": symbol})

    def klines(self, symbol: str, interval: str, start_ms: int | None = None,
               end_ms: int | None = None, limit: int = 1440) -> list[Candle]:
        raw = self._get("/openApi/swap/v3/quote/klines", {
            "symbol": symbol, "interval": interval, "startTime": start_ms,
            "endTime": end_ms, "limit": min(limit, self.cfg.kline_max_limit)})
        out = [self._parse_candle(r) for r in raw or []]
        out.sort(key=lambda c: c.open_time)
        return out

    @staticmethod
    def _parse_candle(r: Any) -> Candle:
        if isinstance(r, dict):  # some gateways return objects instead of arrays
            return Candle(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]),
                          float(r["close"]), float(r["volume"]), int(r.get("closeTime", r["time"])),
                          float(r.get("quoteVolume", 0)), int(r.get("count", 0)),
                          float(r.get("takerBuyVolume", 0)), float(r.get("takerBuyQuoteVolume", 0)))
        return Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
                      int(r[6]), float(r[7]), int(r[8]), float(r[9]), float(r[10]))

    def klines_range(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Candle]:
        """Page through [start_ms, end_ms] in 1440-candle windows (1 request/s)."""
        step = INTERVAL_MS[interval] * self.cfg.kline_max_limit
        seen: dict[int, Candle] = {}
        t = start_ms
        while t < end_ms:
            batch = self.klines(symbol, interval, start_ms=t, end_ms=min(t + step - 1, end_ms))
            for c in batch:
                seen[c.open_time] = c
            if not batch:
                t += step
            else:
                t = max(batch[-1].open_time + INTERVAL_MS[interval], t + 1)
        return [seen[k] for k in sorted(seen)]

    def depth(self, symbol: str, limit: int = 100) -> dict:
        d = self._get("/openApi/swap/v2/quote/depth", {"symbol": symbol, "limit": limit})
        return {"T": int(d["T"]), "bids": [(float(p), float(q)) for p, q in d["bids"]],
                "asks": [(float(p), float(q)) for p, q in d["asks"]]}

    def recent_trades(self, symbol: str, limit: int = 1000) -> list[dict]:
        return self._get("/openApi/swap/v2/quote/trades", {"symbol": symbol, "limit": limit})

    def historical_trades(self, symbol: str, from_id: int | None = None, limit: int = 100) -> list[dict]:
        return self._get("/openApi/swap/v1/market/historicalTrades",
                         {"symbol": symbol, "fromId": from_id, "limit": limit})

    def open_interest(self, symbol: str) -> dict:
        d = self._get("/openApi/swap/v2/quote/openInterest", {"symbol": symbol})
        return {"time": int(d["time"]), "open_interest": float(d["openInterest"])}

    def funding_history(self, symbol: str, start_ms: int | None = None,
                        end_ms: int | None = None, limit: int = 1000) -> list[dict]:
        rows = self._get("/openApi/swap/v2/quote/fundingRate",
                         {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": limit})
        return [{"time": int(r["fundingTime"]), "rate": float(r["fundingRate"])} for r in rows or []]

    def premium_index(self, symbol: str) -> dict:
        return self._get("/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol})
