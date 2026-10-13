"""Expected value of the CRYPTO NOVΛ ladder under simple path assumptions.

Model: a trade that reaches TP[i] goes on to reach TP[i+1] with probability p_next
(same for every rung; "continuation probability"). This is a crude but honest way
to see what win-rate the scheme needs. Real numbers must come from paper trading."""
from __future__ import annotations

from .engine import MODES, ladder_outcome_r


def ev_table(mode: str = "STRICT_CRYPTO_NOVA", p_tp1_list=(0.4, 0.5, 0.55, 0.6, 0.65, 0.7),
             p_next: float = 0.75) -> list[dict]:
    m = MODES[mode]
    k = len(m.tp_r)
    rows = []
    for p1 in p_tp1_list:
        ev = (1 - p1) * ladder_outcome_r(mode, 0)
        p_reach = p1
        for i in range(1, k + 1):
            p_stop_here = p_reach * (1 - p_next) if i < k else p_reach
            ev += p_stop_here * ladder_outcome_r(mode, i)
            p_reach *= p_next
        rows.append({"p_tp1": p1, "p_next": p_next, "ev_r": round(ev, 3)})
    return rows


def outcomes(mode: str = "STRICT_CRYPTO_NOVA") -> list[dict]:
    return [{"tps_hit": i, "result_r": ladder_outcome_r(mode, i)} for i in range(len(MODES[mode].tp_r) + 1)]


def breakeven_p_tp1(mode: str = "STRICT_CRYPTO_NOVA", p_next: float = 0.75) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if ev_table(mode, (mid,), p_next)[0]["ev_r"] < 0:
            lo = mid
        else:
            hi = mid
    return round(hi, 3)
