"""Local order book maintained from BingX `@incrDepth` messages.

Rules (official reference): first message action="all" is a full snapshot with
lastUpdateId; each following action="update" must carry lastUpdateId == previous + 1.
A gap means the book is unreliable until the next snapshot. Qty 0 removes a level.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OrderBook:
    symbol: str
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    last_update_id: int | None = None
    last_ts: int = 0
    synced: bool = False
    gaps: int = 0

    def apply(self, msg: dict) -> bool:
        """Apply one incrDepth payload. Returns True when the book is consistent."""
        action = msg.get("action")
        uid = int(msg.get("lastUpdateId", -1))
        self.last_ts = int(msg.get("T", self.last_ts))
        if action == "all":
            self.bids = {float(p): float(q) for p, q in msg.get("bids", [])}
            self.asks = {float(p): float(q) for p, q in msg.get("asks", [])}
            self.last_update_id = uid
            self.synced = True
            return True
        if not self.synced or self.last_update_id is None or uid != self.last_update_id + 1:
            self.synced = False
            self.gaps += 1
            return False
        for side, levels in ((self.bids, msg.get("bids", [])), (self.asks, msg.get("asks", []))):
            for p, q in levels:
                p, q = float(p), float(q)
                if q == 0:
                    side.pop(p, None)
                else:
                    side[p] = q
        self.last_update_id = uid
        return True

    # --------------------------------------------------------------- queries
    def best_bid(self) -> float | None:
        return max(self.bids) if self.bids else None

    def best_ask(self) -> float | None:
        return min(self.asks) if self.asks else None

    def mid(self) -> float | None:
        b, a = self.best_bid(), self.best_ask()
        return (a + b) / 2 if a and b else None

    def depth_within(self, pct: float) -> tuple[float, float]:
        """Base-asset quantity resting within ±pct of mid on each side."""
        m = self.mid()
        if not m:
            return 0.0, 0.0
        lo, hi = m * (1 - pct), m * (1 + pct)
        bid_qty = sum(q for p, q in self.bids.items() if p >= lo)
        ask_qty = sum(q for p, q in self.asks.items() if p <= hi)
        return bid_qty, ask_qty

    def imbalance(self, pct: float) -> float | None:
        """(bid - ask) / (bid + ask) within ±pct of mid. +1 = only bids, -1 = only asks."""
        b, a = self.depth_within(pct)
        return (b - a) / (b + a) if (b + a) > 0 else None

    def snapshot(self, levels: int = 50) -> dict:
        bids = sorted(self.bids.items(), reverse=True)[:levels]
        asks = sorted(self.asks.items())[:levels]
        return {"T": self.last_ts, "bids": bids, "asks": asks, "synced": self.synced}
