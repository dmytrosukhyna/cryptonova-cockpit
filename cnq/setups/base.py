"""Setup entity and its status machine.

Forecast and signal are different things: a setup lives as FORECAST /
WAIT_CONFIRMATION long before it becomes a CALL, and most never do."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

STATUSES = ("DISCOVERED", "ATTENTION", "FORECAST", "WAIT_CONFIRMATION", "VALIDATING",
            "READY", "CALL", "POSITION_OPEN", "COMPLETED", "REJECTED", "EXPIRED", "CANCELLED")
TERMINAL = {"COMPLETED", "REJECTED", "EXPIRED", "CANCELLED"}


@dataclass
class Setup:
    symbol: str
    family: str
    direction: str                      # LONG | SHORT
    level_id: str
    level_lo: float
    level_hi: float
    created_index: int
    created_time: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "DISCOVERED"
    updated_time: int = 0
    # planned prices — filled progressively, entry only becomes real on CALL
    entry_type: str = "MARKET"           # MARKET (next open) | LIMIT (at entry price on the touch candle)
    entry: float | None = None
    stop: float | None = None
    invalidation: float | None = None
    entry_zone: tuple[float, float] | None = None
    confirm_rule: str = ""
    touch_low: float | None = None
    touch_high: float | None = None
    touch_index: int | None = None
    expires_index: int | None = None
    checks: dict = field(default_factory=dict)
    score: float | None = None
    evidence: dict = field(default_factory=dict)
    transitions: list[tuple[int, str, str, str]] = field(default_factory=list)

    def move(self, status: str, time_ms: int, reason: str) -> None:
        assert status in STATUSES, status
        self.transitions.append((time_ms, self.status, status, reason))
        self.status = status
        self.updated_time = time_ms

    @property
    def active(self) -> bool:
        return self.status not in TERMINAL

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["transitions"] = [list(t) for t in self.transitions]
        return d
