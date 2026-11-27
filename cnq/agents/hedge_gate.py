"""«Хедж»-трейдеры вне реального копирования (owner 27.11.2026 08:59, explicit «да»: «исключить, если явно лучше» — the backtest of
scripts/research/hedge_pool.py, run 45860166004: the locked list's copies PF 2.12 → 2.63, max drawdown 370 → 128 pp without them).

ON only while config/owner_overrides.json has «nohedge_real_1127» with "armed": true. Then:
  • cnq/agents/whale_study: a flagged trader is not eligible and a locked one leaves the follow list at once («хедж: …»);
  • cnq/execution/okx_futures: no NEW real copy of a flagged trader's signal (checked every pass, never final).
Open copies are never force-closed: they run to their own stop or the trader's exit (the usual «кит ушёл» path).
The flags: config/hedge_flags.json {"flags": {trader id: [why…]}}. Stdlib only."""
from __future__ import annotations

import json
import os

KEY = "nohedge_real_1127"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FLAGS = os.path.join(ROOT, "config", "hedge_flags.json")
WHY = {"carry": "фандинг-карри", "carry2": "карри (держит сторону, которой платят фандинг)", "offset": "двусторонняя книга",
       "bigmiss": "крупные входы не по направлению", "bigbook": "большая книга без ценового края"}


def flags(path: str | None = None) -> dict:
    try:
        with open(path or FLAGS, encoding="utf-8") as f:
            d = json.load(f)
        return {str(k).lower(): list(v or []) for k, v in (d.get("flags") or {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def armed(overrides: dict | None) -> bool:
    return bool(((overrides or {}).get(KEY) or {}).get("armed"))


def reason(addr, overrides: dict | None, fl: dict | None = None) -> str | None:
    """the «хедж» reason for this trader when the owner's key is on, else None"""
    if not armed(overrides) or not addr:
        return None
    f = (flags() if fl is None else fl).get(str(addr).lower())
    if not f:
        return None
    return "хедж: " + ", ".join(WHY.get(x, x) for x in f) + " — не копируем на реальный счёт (решение владельца 27.11)"
