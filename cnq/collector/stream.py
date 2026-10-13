"""Live recorder: trades + incremental order book + periodic OI, written to SQLite.

This is the part BingX cannot give us retroactively, so it must run 24/7 on the
server (pm2/systemd). Candles are NOT recorded here — the backfill refetches them
from REST, which is authoritative and includes taker-buy volume."""
from __future__ import annotations

import asyncio
import logging
import time

from ..bingx.orderbook import OrderBook
from ..bingx.rest import BingXRest
from ..bingx.ws import BingXMarketWS, parse_trade
from ..config import Config
from ..store.db import Store

log = logging.getLogger("cnq.stream")


class Recorder:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.book = OrderBook(cfg.symbol)
        self.trade_buf: list[dict] = []
        self.last_depth_write = 0.0
        self.last_trade_ms = 0
        self.stats = {"trades": 0, "depth_msgs": 0, "depth_gaps": 0, "snapshots": 0, "oi": 0}

    async def handle(self, data_type: str, data: dict, recv_ms: int) -> None:
        if data_type.endswith("@trade"):
            rows = data if isinstance(data, list) else [data]
            for d in rows:
                t = parse_trade(d)
                t["recv_time"] = recv_ms
                self.trade_buf.append(t)
                self.last_trade_ms = t["time"]
            self.stats["trades"] += len(rows)
            if len(self.trade_buf) >= 200:
                self.flush_trades()
        elif data_type.endswith("@incrDepth"):
            self.stats["depth_msgs"] += 1
            ok = self.book.apply(data)
            if not ok:
                self.stats["depth_gaps"] += 1
                self.store.log_quality(recv_ms, self.cfg.symbol, "depth_gap",
                                       f"lastUpdateId={data.get('lastUpdateId')}")
            now = time.monotonic()
            if now - self.last_depth_write >= self.cfg.depth_snapshot_every_s:
                self.last_depth_write = now
                self.write_depth()

    def flush_trades(self) -> None:
        if self.trade_buf:
            self.store.insert_trades(self.cfg.symbol, self.trade_buf)
            self.trade_buf.clear()

    def write_depth(self) -> None:
        b1, a1 = self.book.depth_within(0.01)
        b02, a02 = self.book.depth_within(0.002)
        bb, ba = self.book.best_bid(), self.book.best_ask()
        metrics = {"bid_1pct": b1, "ask_1pct": a1, "bid_02pct": b02, "ask_02pct": a02,
                   "spread": (ba - bb) if bb and ba else None}
        self.store.insert_depth(self.cfg.symbol, self.book.snapshot(50), metrics)
        self.stats["snapshots"] += 1

    async def poll_oi(self, rest: BingXRest) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                oi = await loop.run_in_executor(None, rest.open_interest, self.cfg.symbol)
                self.store.insert_oi(self.cfg.symbol, oi["time"], oi["open_interest"])
                self.stats["oi"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("OI poll failed: %s", e)
            await asyncio.sleep(self.cfg.oi_poll_every_s)

    async def heartbeat(self, ws: BingXMarketWS) -> None:
        while True:
            await asyncio.sleep(60)
            self.flush_trades()
            stale = (int(time.time() * 1000) - ws.last_message_ms) > 30_000
            log.info("stats=%s synced=%s stale=%s reconnects=%d",
                     self.stats, self.book.synced, stale, ws.reconnects)
            if stale:
                self.store.log_quality(int(time.time() * 1000), self.cfg.symbol, "stream_stale",
                                       f"no messages for >30s; reconnects={ws.reconnects}")


async def run_stream(cfg: Config, store: Store) -> None:
    rec = Recorder(cfg, store)
    ws = BingXMarketWS(cfg.bingx.ws_market,
                       [f"{cfg.symbol}@trade", f"{cfg.symbol}@incrDepth"], rec.handle)
    rest = BingXRest(cfg.bingx)
    await asyncio.gather(ws.run(), rec.poll_oi(rest), rec.heartbeat(ws))
