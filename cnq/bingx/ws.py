"""BingX swap market WebSocket client.

Facts from the official reference: all messages are GZIP-compressed; the server
sends the text "Ping" and expects "Pong"; no auth for market streams; BTC/ETH
incrDepth pushes every 200ms. Requires the `websockets` package (pip install websockets).
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import time
import uuid
from typing import Awaitable, Callable

log = logging.getLogger("cnq.ws")

Handler = Callable[[str, dict, int], Awaitable[None]]  # (dataType, data, recv_ms)


class BingXMarketWS:
    def __init__(self, url: str, channels: list[str], handler: Handler,
                 reconnect_delay: float = 2.0, max_delay: float = 60.0):
        self.url = url
        self.channels = channels
        self.handler = handler
        self.reconnect_delay = reconnect_delay
        self.max_delay = max_delay
        self.messages = 0
        self.reconnects = 0
        self.last_message_ms = 0
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        try:
            import websockets  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise SystemExit("pip install websockets") from e
        delay = self.reconnect_delay
        while not self._stop.is_set():
            try:
                await self._session()
                delay = self.reconnect_delay
            except Exception as e:  # noqa: BLE001
                self.reconnects += 1
                log.warning("ws disconnected (%s); reconnect in %.0fs", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.max_delay)

    async def _session(self) -> None:
        import websockets
        async with websockets.connect(self.url, max_size=None, ping_interval=None) as ws:
            for ch in self.channels:
                await ws.send(json.dumps({"id": str(uuid.uuid4()), "reqType": "sub", "dataType": ch}))
            log.info("subscribed: %s", ", ".join(self.channels))
            async for raw in ws:
                recv_ms = int(time.time() * 1000)
                text = gzip.decompress(raw).decode() if isinstance(raw, (bytes, bytearray)) else raw
                if text == "Ping":
                    await ws.send("Pong")
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                data_type = msg.get("dataType")
                if not data_type or "data" not in msg:
                    continue  # subscription ack
                self.messages += 1
                self.last_message_ms = recv_ms
                await self.handler(data_type, msg["data"], recv_ms)


def parse_trade(d: dict) -> dict:
    """`{symbol}@trade` payload → normalized row. m=True means buyer was maker,
    i.e. the aggressor was a SELLER."""
    return {"time": int(d["T"]), "price": float(d["p"]), "qty": float(d["q"]),
            "aggressor": "sell" if d.get("m") else "buy"}
