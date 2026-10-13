"""Setup Validation Engine — independent checks, each PASS / FAIL / PENDING / UNAVAILABLE,
plus an explainable score. Critical FAILs cannot be compensated by the score."""
from __future__ import annotations

import numpy as np

from .base import Setup

CRITICAL = {"stop_distance", "data_quality", "risk_reward_tp1"}


def validate(s: Setup, atr_now: float, candles_coverage: float, higher_tf_trend: str | None,
             book_imbalance: float | None, funding_rate: float | None,
             family_stats: dict | None) -> tuple[dict, float]:
    checks: dict[str, tuple[str, str]] = {}
    # 1. stop distance: too tight = noise, too wide = poor R
    if s.entry is None or s.stop is None:
        checks["stop_distance"] = ("PENDING", "no entry/stop yet")
    else:
        d = abs(s.entry - s.stop) / atr_now
        max_d = 12.0 if s.family == "LIQ_CASCADE_LONG" else 3.0   # a cascade bar is many ATRs by definition
        if d < 0.3:
            checks["stop_distance"] = ("FAIL", f"stop {d:.2f} ATR: inside noise")
        elif d > max_d:
            checks["stop_distance"] = ("FAIL", f"stop {d:.2f} ATR: too wide")
        else:
            checks["stop_distance"] = ("PASS", f"stop {d:.2f} ATR")
    # 2. data quality
    checks["data_quality"] = (("PASS" if candles_coverage >= 0.99 else "FAIL"),
                              f"candle coverage {candles_coverage:.3f}")
    # 3. level quality
    ls = s.evidence.get("level_score", 0)
    checks["level_score"] = (("PASS" if ls >= 50 else "FAIL"), f"level score {ls}")
    # 4. volume evidence at confirmation
    rv = s.evidence.get("rel_volume")
    if rv is None:
        checks["volume"] = ("PENDING", "no confirmation candle yet")
    else:
        checks["volume"] = (("PASS" if rv >= 1.2 else "FAIL"), f"rel. volume {rv}")
    # 5. delta agrees with direction
    dl = s.evidence.get("delta")
    if dl is None:
        checks["delta"] = ("PENDING", "")
    else:
        agree = dl > 0 if s.direction == "LONG" else dl < 0
        checks["delta"] = (("PASS" if agree else "FAIL"), f"delta {dl:+.3f}")
    # 6. higher-timeframe context
    if higher_tf_trend is None:
        checks["higher_tf"] = ("UNAVAILABLE", "")
    else:
        against = (higher_tf_trend == "down" and s.direction == "LONG") or \
                  (higher_tf_trend == "up" and s.direction == "SHORT")
        checks["higher_tf"] = (("FAIL" if against else "PASS"), f"1h trend {higher_tf_trend}")
    # 7. order book (only live / with recorded depth)
    if book_imbalance is None:
        checks["order_book"] = ("UNAVAILABLE", "no synced depth for this time")
    else:
        agree = book_imbalance > 0.1 if s.direction == "LONG" else book_imbalance < -0.1
        checks["order_book"] = (("PASS" if agree else "FAIL"), f"imbalance {book_imbalance:+.2f}")
    # 8. funding extremes add risk
    if funding_rate is None:
        checks["funding"] = ("UNAVAILABLE", "")
    else:
        crowded = (funding_rate > 0.0005 and s.direction == "LONG") or \
                  (funding_rate < -0.0005 and s.direction == "SHORT")
        checks["funding"] = (("FAIL" if crowded else "PASS"), f"funding {funding_rate:+.5f}")
    # 9. historical performance of this family (needs ≥30 trades to say anything)
    if not family_stats or family_stats.get("n", 0) < 30:
        checks["family_stats"] = ("UNAVAILABLE", "fewer than 30 completed trades")
    else:
        ev = family_stats.get("ev_r", 0)
        checks["family_stats"] = (("PASS" if ev > 0 else "FAIL"), f"EV {ev:+.2f}R over {family_stats['n']}")
    # 10. TP1 reachable before the opposite side of the zone (0.5R must clear the zone)
    if s.entry is not None and s.stop is not None and s.family != "LIQ_CASCADE_LONG":
        r = abs(s.entry - s.stop)
        zone_w = s.level_hi - s.level_lo
        checks["risk_reward_tp1"] = (("PASS" if 0.5 * r > zone_w * 0.5 else "FAIL"),
                                     f"TP1 {0.5*r:.2f} vs half zone {zone_w*0.5:.2f}")
    score = _score(checks)
    return {k: {"status": v[0], "detail": v[1]} for k, v in checks.items()}, score


def _score(checks: dict) -> float:
    weights = {"stop_distance": 15, "data_quality": 10, "level_score": 20, "volume": 15, "delta": 10,
               "higher_tf": 10, "order_book": 5, "funding": 5, "family_stats": 5, "risk_reward_tp1": 5}
    got, total = 0.0, 0.0
    for k, (st, _) in checks.items():
        if st in ("UNAVAILABLE", "PENDING"):
            continue
        total += weights[k]
        if st == "PASS":
            got += weights[k]
    return round(100 * got / total, 1) if total else 0.0


def critical_failed(checks: dict) -> list[str]:
    return [k for k, v in checks.items() if k in CRITICAL and v["status"] == "FAIL"]
