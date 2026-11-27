"""«Пилот» for a levels-family paper desk (owner 27.11.2026 07:51: a faster path to real IF the strategy works). OFF by default.
This module only DECIDES whether a pilot may start; it places nothing and is imported by no execution code. Going real still needs
the owner's explicit yes AND a separate change that routes the pilot's signals — none exists today.

A pilot may start only when ALL hold:
  1. config/levels_pilot.json names a variant whose pre-registered study PASSED on 30 coins (both halves > 0, OOS daily t ≥ the
     corrected threshold, ≥ 100 OOS trades, above random entries) AND had a positive mean on the 12 new coins («pilot_eligible»);
  2. config/owner_overrides.json has the key «levels_pilot_1127» with "armed": true (the owner's yes) — absent by default;
  3. that variant's forward PAPER desk has ≥ 10 closed trades with PF > 1.2 and no rule breaks (instead of the usual 30).
Pilot rules: risk 0.5 % per trade, ≤ 2 positions, a 1 % day stop, its own budget line. It moves to the full rules only after 30 real
trades meet the usual bar (PF and drawdown better than the source) and the owner's yes again."""
from __future__ import annotations

import json
import os

KEY = "levels_pilot_1127"
RULES = {"risk_pct": 0.5, "max_positions": 2, "day_stop_pct": 1.0, "min_fwd_trades": 10, "min_fwd_pf": 1.2, "full_after_real_trades": 30}
CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config", "levels_pilot.json")


def _pf(xs):
    p, n = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return p / n if n else (99.0 if p else 0.0)


def ready(spec: dict | None, overrides: dict | None, fwd_closed: list, breaks: int = 0) -> tuple[bool, list]:
    """→ (may start, reasons why not). fwd_closed = the forward paper desk's closed results (% or $), oldest first"""
    why = []
    s = spec or {}
    if not s.get("variant") or not s.get("study_passed") or not s.get("pilot_eligible"):
        why.append("нет варианта, прошедшего тест на 30 монетах и плюсового на 12 новых")
    if not ((overrides or {}).get(KEY) or {}).get("armed"):
        why.append("нет «да» владельца (ключ levels_pilot_1127 не включён)")
    if len(fwd_closed or []) < RULES["min_fwd_trades"]:
        why.append(f"вперёд на бумаге {len(fwd_closed or [])} сделок из {RULES['min_fwd_trades']}")
    elif _pf(fwd_closed) <= RULES["min_fwd_pf"]:
        why.append(f"PF вперёд {_pf(fwd_closed):.2f} ≤ {RULES['min_fwd_pf']}")
    if breaks:
        why.append(f"нарушений правил: {breaks}")
    return (not why), why


def spec(path: str | None = None) -> dict:
    try:
        with open(path or CFG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}
