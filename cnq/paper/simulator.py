"""Internal Paper Trading Engine — candle-by-candle, no look-ahead.

Fill model
  entry  : market at the OPEN of the candle after the CALL, with slippage + taker fee
  stops  : market, slippage + taker fee
  TPs    : limit at the TP price, maker fee
  intrabar ambiguity: if the stop and a TP are both inside one candle, we assume
  the STOP was hit first (conservative). Use 1m candles for replay to reduce this.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..bingx.rest import Candle
from ..risk.engine import MODES, ContractSpec, TradePlan


@dataclass
class Fill:
    time: int
    kind: str          # ENTRY | TP1..TP5 | STOP | TRAIL | FORCED
    price: float
    qty: float
    fee: float


@dataclass
class Position:
    id: str
    setup_id: str
    plan: TradePlan
    open_time: int
    qty_left: float
    stop: float
    tps_hit: int = 0
    fills: list[Fill] = field(default_factory=list)
    closed: bool = False
    close_time: int | None = None
    trail_peak: float | None = None
    max_bars: int | None = None   # time exit (decision-bar count), None = no limit
    bars_open: int = 0
    mfe: float = 0.0   # max favourable excursion in R (while open)
    mae: float = 0.0   # max adverse excursion in R

    @property
    def sign(self) -> int:
        return 1 if self.plan.direction == "LONG" else -1

    def realized(self) -> float:
        pnl = 0.0
        for f in self.fills:
            if f.kind != "ENTRY":
                pnl += self.sign * (f.price - self.plan.entry) * f.qty
            pnl -= f.fee
        return pnl


class PaperEngine:
    def __init__(self, spec: ContractSpec, slippage_pct: float = 0.0003, atr_trail_mult: float = 2.0):
        self.spec = spec
        self.slippage = slippage_pct
        self.atr_trail_mult = atr_trail_mult
        self.open: list[Position] = []
        self.closed: list[Position] = []
        self.stepped_this_bar: set[str] = set()

    # --------------------------------------------------------------- opening
    def open_position(self, setup_id: str, plan: TradePlan, candle: Candle) -> Position:
        """Fill at this candle's open (the candle after the CALL)."""
        sign = 1 if plan.direction == "LONG" else -1
        px = candle.open * (1 + sign * self.slippage)
        fee = px * plan.qty * self.spec.taker_fee
        # keep the stop distance as planned relative to the real fill
        plan.entry = px
        r = plan.r_distance
        plan.stop = px - sign * r
        m = MODES[plan.mode]
        plan.tps = [px + sign * k * r for k in m.tp_r]
        pos = Position(uuid.uuid4().hex[:12], setup_id, plan, candle.open_time, plan.qty, plan.stop)
        pos.fills.append(Fill(candle.open_time, "ENTRY", px, plan.qty, fee))
        self.open.append(pos)
        return pos

    def open_limit(self, setup_id: str, plan: TradePlan, candle: Candle, atr_now: float) -> Position | None:
        """Limit fill at plan.entry on this candle (price must trade through it). Maker fee.
        Conservative: if the same candle also reaches the stop, the trade is stopped out."""
        sign = 1 if plan.direction == "LONG" else -1
        traded = candle.low <= plan.entry if sign == 1 else candle.high >= plan.entry
        if not traded:
            return None
        px = plan.entry
        fee = px * plan.qty * self.spec.maker_fee
        r = plan.r_distance
        plan.stop = px - sign * r
        m = MODES[plan.mode]
        plan.tps = [px + sign * k * r for k in m.tp_r]
        pos = Position(uuid.uuid4().hex[:12], setup_id, plan, candle.open_time, plan.qty, plan.stop)
        pos.fills.append(Fill(candle.open_time, "ENTRY", px, plan.qty, fee))
        self.open.append(pos)
        stop_hit = candle.low <= pos.stop if sign == 1 else candle.high >= pos.stop
        if stop_hit:
            self._exit_market(pos, candle, pos.stop, "STOP")
            self.open.remove(pos); self.closed.append(pos)
            return pos
        # TPs reachable on the touch candle are NOT credited (order unknown) — conservative
        return pos

    # --------------------------------------------------------------- stepping
    def step_one(self, pos: Position, candle: Candle, atr_now: float) -> list[Position]:
        """Step a single open position through one exec candle."""
        self._step_one(pos, candle, atr_now)
        if pos.closed and pos in self.open:
            self.open.remove(pos); self.closed.append(pos)
            return [pos]
        return []

    def step(self, candle: Candle, atr_now: float, exclude: set[str] | None = None) -> list[Position]:
        """Process one candle for all open positions. Returns positions closed on it."""
        done = []
        for pos in list(self.open):
            if exclude and pos.id in exclude:
                continue
            self._step_one(pos, candle, atr_now)
            if pos.closed:
                self.open.remove(pos)
                self.closed.append(pos)
                done.append(pos)
        return done

    def _step_one(self, pos: Position, c: Candle, atr_now: float) -> None:
        plan, sign, m = pos.plan, pos.sign, MODES[pos.plan.mode]
        if pos.max_bars is not None and pos.bars_open >= pos.max_bars:
            self._exit_market(pos, c, c.open, "TIME")
            return
        r = plan.r_distance
        pos.mfe = max(pos.mfe, sign * ((c.high if sign == 1 else c.low) - plan.entry) / r)
        pos.mae = max(pos.mae, -sign * ((c.low if sign == 1 else c.high) - plan.entry) / r)
        # 1. stop first (conservative intrabar assumption)
        stop_hit = c.low <= pos.stop if sign == 1 else c.high >= pos.stop
        if stop_hit and c.open_time > pos.open_time:
            self._exit_market(pos, c, pos.stop, "STOP")
            return
        if stop_hit and c.open_time == pos.open_time:
            # entry candle: if it opened beyond the stop we are already out
            if (sign == 1 and c.open <= pos.stop) or (sign == -1 and c.open >= pos.stop):
                self._exit_market(pos, c, c.open, "STOP")
                return
        # 2. take-profits reachable in this candle, in order
        while pos.tps_hit < len(plan.tps) and pos.qty_left > 0:
            tp = plan.tps[pos.tps_hit]
            reached = c.high >= tp if sign == 1 else c.low <= tp
            if not reached:
                break
            qty = min(plan.tp_qty[pos.tps_hit], pos.qty_left)
            pos.fills.append(Fill(c.open_time, f"TP{pos.tps_hit + 1}", tp, qty, tp * qty * self.spec.maker_fee))
            pos.qty_left = round(pos.qty_left - qty, 10)
            pos.tps_hit += 1
            new_stop_r = m.stop_after_tp[pos.tps_hit - 1]
            if new_stop_r is not None:
                pos.stop = plan.entry + sign * new_stop_r * plan.r_distance
            # same-candle stop check at the NEW stop (conservative: price can come back)
            if pos.qty_left > 0 and pos.tps_hit >= 2:
                back = c.low <= pos.stop if sign == 1 else c.high >= pos.stop
                if back and c.close != c.high and c.close != c.low:
                    self._exit_market(pos, c, pos.stop, "STOP")
                    return
        if pos.qty_left <= 1e-12:
            pos.closed, pos.close_time = True, c.open_time
            return
        # 3. ATR trailing for the runner after the last TP
        if pos.tps_hit >= len(plan.tps) and m.runner_frac > 0:
            extreme = c.high if sign == 1 else c.low
            pos.trail_peak = extreme if pos.trail_peak is None else (max(pos.trail_peak, extreme) if sign == 1 else min(pos.trail_peak, extreme))
            trail = pos.trail_peak - sign * self.atr_trail_mult * atr_now
            if (sign == 1 and trail > pos.stop) or (sign == -1 and trail < pos.stop):
                pos.stop = trail

    def _exit_market(self, pos: Position, c: Candle, px: float, kind: str) -> None:
        px = px * (1 - pos.sign * self.slippage)
        pos.fills.append(Fill(c.open_time, kind, px, pos.qty_left, px * pos.qty_left * self.spec.taker_fee))
        pos.qty_left = 0.0
        pos.closed, pos.close_time = True, c.open_time

    def force_close(self, c: Candle) -> None:
        for pos in list(self.open):
            self._exit_market(pos, c, c.close, "FORCED")
            self.open.remove(pos)
            self.closed.append(pos)

    # ---------------------------------------------------------------- report
    @staticmethod
    def trade_record(pos: Position, run_id: str, symbol: str) -> dict:
        pnl = pos.realized()
        fees = sum(f.fee for f in pos.fills)
        return {"id": pos.id, "run_id": run_id, "setup_id": pos.setup_id, "symbol": symbol,
                "direction": pos.plan.direction, "mode": pos.plan.mode, "entry_time": pos.open_time,
                "exit_time": pos.close_time, "entry": pos.plan.entry, "stop": pos.plan.stop,
                "qty": pos.plan.qty, "risk_usdt": pos.plan.risk_usdt,
                "result_r": round(pnl / pos.plan.risk_usdt, 4) if pos.plan.risk_usdt else 0.0,
                "net_pnl": round(pnl, 4), "fees": round(fees, 4), "tps_hit": pos.tps_hit,
                "mfe_r": round(pos.mfe, 3), "mae_r": round(pos.mae, 3), "exit_kind": pos.fills[-1].kind,
                "fills": [f.__dict__ for f in pos.fills]}
