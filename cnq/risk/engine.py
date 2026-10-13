"""CRYPTO NOVΛ Risk Engine — deterministic position sizing and the TP ladder.

Official scheme (STRICT_CRYPTO_NOVA):
  TP1 0.5R 40% | TP2 1R 30% | TP3 1.5R 10% | TP4 2R 10% | TP5 2.5R 10%
  stop: initial until TP2 is hit → break-even after TP2 → TP1 after TP3 →
        TP2 after TP4 → TP3 after TP5 (+ATR trailing if anything remains).
CRYPTO_NOVA_RUNNER: 40/30/10/10/5, 5% left for ATR trailing after TP5.

Position size comes from money risk and stop distance. Leverage only sets
margin; it never changes the allowed loss.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskMode:
    name: str
    tp_r: tuple[float, ...]            # R-multiples of each take-profit
    tp_frac: tuple[float, ...]         # fraction of ORIGINAL size closed at each TP
    # stop level (in R from entry) that applies once TP[i] is hit; None = unchanged
    stop_after_tp: tuple[float | None, ...]
    runner_frac: float                 # fraction left after the last TP for trailing


STRICT = RiskMode("STRICT_CRYPTO_NOVA", (0.5, 1.0, 1.5, 2.0, 2.5), (0.40, 0.30, 0.10, 0.10, 0.10),
                  (None, 0.0, 0.5, 1.0, 1.5), 0.0)
RUNNER = RiskMode("CRYPTO_NOVA_RUNNER", (0.5, 1.0, 1.5, 2.0, 2.5), (0.40, 0.30, 0.10, 0.10, 0.05),
                  (None, 0.0, 0.5, 1.0, 1.5), 0.05)
# Comparison scheme, NOT part of the official strategy: classic level trading —
# 30% at 1R (→ break-even), 30% at 2R (→ stop to 1R), 40% at 3R.
LEVEL_3R = RiskMode("LEVEL_3R", (1.0, 2.0, 3.0), (0.30, 0.30, 0.40), (0.0, 1.0, None), 0.0)
# Research baselines: one target, full exit, no stop moves.
SINGLE_1R = RiskMode("SINGLE_1R", (1.0,), (1.0,), (None,), 0.0)
SINGLE_1_5R = RiskMode("SINGLE_1_5R", (1.5,), (1.0,), (None,), 0.0)
SINGLE_2R = RiskMode("SINGLE_2R", (2.0,), (1.0,), (None,), 0.0)
MODES = {m.name: m for m in (STRICT, RUNNER, LEVEL_3R, SINGLE_1R, SINGLE_1_5R, SINGLE_2R)}


@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    qty_step: float = 0.0001      # tradeMinQuantity / quantityPrecision
    price_step: float = 0.1
    min_qty: float = 0.0001
    min_notional_usdt: float = 2.0
    taker_fee: float = 0.0005
    maker_fee: float = 0.0002
    max_leverage: int = 125


@dataclass
class TradePlan:
    symbol: str
    direction: str                 # LONG | SHORT
    mode: str
    entry: float
    stop: float
    qty: float
    notional: float
    risk_usdt: float
    risk_pct: float
    r_distance: float              # |entry - stop| in price
    leverage: int
    margin: float
    tps: list[float]
    tp_qty: list[float]
    fees_round_trip_est: float
    pnl_at_tp: list[float]         # cumulative net PnL if the trade ends exactly at TP[i]
    liquidation_est: float | None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.qty > 0 and not any(w.startswith("FAIL") for w in self.warnings)


def _round_step(x: float, step: float, down: bool = True) -> float:
    if step <= 0:
        return x
    k = int(x / step + 1e-9) if down else round(x / step)
    return round(k * step, 10)


def build_plan(symbol: str, direction: str, entry: float, stop: float, balance: float,
               risk_pct: float, spec: ContractSpec, leverage: int = 5,
               mode: str = "STRICT_CRYPTO_NOVA", slippage_pct: float = 0.0003,
               open_exposure_notional: float = 0.0, max_total_exposure_x: float = 3.0,
               daily_loss_used_pct: float = 0.0, daily_loss_limit_pct: float = 3.0) -> TradePlan:
    m = MODES[mode]
    direction = direction.upper()
    sign = 1 if direction == "LONG" else -1
    warnings: list[str] = []
    r_dist = abs(entry - stop)
    if r_dist <= 0 or (sign == 1 and stop >= entry) or (sign == -1 and stop <= entry):
        warnings.append("FAIL stop must be on the losing side of entry")
        r_dist = max(r_dist, 1e-9)
    risk_usdt = balance * risk_pct / 100
    # fees + slippage are paid from the risk budget so a stop-out loses exactly risk_usdt
    cost_per_unit = entry * (spec.taker_fee * 2 + slippage_pct)
    qty_raw = risk_usdt / (r_dist + cost_per_unit)
    qty = _round_step(qty_raw, spec.qty_step)
    if qty < spec.min_qty:
        warnings.append(f"FAIL qty {qty} below exchange minimum {spec.min_qty}")
    notional = qty * entry
    if notional < spec.min_notional_usdt:
        warnings.append(f"FAIL notional {notional:.2f} below minimum {spec.min_notional_usdt}")
    leverage = max(1, min(leverage, spec.max_leverage))
    margin = notional / leverage
    if margin > balance:
        warnings.append(f"FAIL margin {margin:.2f} exceeds balance {balance:.2f}")
    if (open_exposure_notional + notional) > balance * max_total_exposure_x:
        warnings.append("FAIL total exposure limit")
    if daily_loss_used_pct + risk_pct > daily_loss_limit_pct:
        warnings.append("FAIL daily loss limit would be exceeded")
    # liquidation estimate (isolated, ignoring maintenance tiers): entry ∓ entry/leverage
    liq = entry * (1 - sign / leverage * 0.98)
    if (sign == 1 and liq >= stop) or (sign == -1 and liq <= stop):
        warnings.append("FAIL liquidation price inside stop distance — lower leverage")
    tps = [_round_step(entry + sign * r * r_dist, spec.price_step, down=False) for r in m.tp_r]
    tp_qty = [_round_step(qty * f, spec.qty_step) for f in m.tp_frac]
    # remainder after rounding goes to the last TP so the sum equals qty
    tp_qty[-1] = round(qty - sum(tp_qty[:-1]), 10)
    fee_rt = notional * spec.taker_fee * 2
    pnl_at_tp = []
    for i in range(len(tps)):
        realized = sum(sign * (tps[j] - entry) * tp_qty[j] for j in range(i + 1))
        remaining = qty - sum(tp_qty[: i + 1])
        stop_r = m.stop_after_tp[i]
        exit_px = stop if stop_r is None else entry + sign * stop_r * r_dist
        realized += sign * (exit_px - entry) * remaining
        pnl_at_tp.append(round(realized - fee_rt, 4))
    return TradePlan(symbol, direction, mode, entry, stop, qty, round(notional, 4),
                     round(risk_usdt, 4), risk_pct, r_dist, leverage, round(margin, 4), tps,
                     tp_qty, round(fee_rt, 4), pnl_at_tp, round(liq, 2), warnings)


def ladder_outcome_r(mode: str, tps_hit: int) -> float:
    """Net result in R when the trade hits exactly `tps_hit` TPs, then the current stop.
    0 → full loss (-1R). Fees are excluded here (pure scheme arithmetic)."""
    m = MODES[mode]
    if tps_hit == 0:
        return -1.0
    realized = sum(m.tp_r[i] * m.tp_frac[i] for i in range(tps_hit))
    remaining = 1 - sum(m.tp_frac[:tps_hit])
    stop_r = m.stop_after_tp[tps_hit - 1]
    exit_r = -1.0 if stop_r is None else stop_r
    return round(realized + remaining * exit_r, 4)
