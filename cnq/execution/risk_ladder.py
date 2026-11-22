"""Risk ladder for NEW real copies (owner 22.11.2026 18:07, part of «halfrisk_1122»: «линия побед… наращивать успех с умом»).
Levels 1.0 % → 1.5 % → 2.0 % of the futures budget per new copy — never above 2.0 % (the level the owner had accepted before
22.11); the day stop scales with it (2 % at 1.0–1.5 %, 3 % at 2.0 %). Open positions keep their size and stops.
  UP one level only if ALL: ≥ 15 closed real copies since the last level change, PF of the last 15 ≥ 1.5, win rate of the last 15
     ≥ 55 %, the futures equity (closed P/L since «since» + the open copies' P/L now) within 1 % of the budget from its high, and
     ≥ 3 days since the last step up;
  DOWN one level at once on: 3 losing closes in a row, PF of the last 10 < 1.0 (both counted on closes since the last change),
     or a drawdown > 3 % of the budget from the high since the last change; straight to 1.0 % when the drawdown > 5 %.
Every change is logged («risk_level») with the reason. Pure functions + a tiny state {"i", "changed_t", "up_t", "high", "log"}.
Stdlib only."""
from __future__ import annotations

DAY = 86_400_000
LEVELS = (0.01, 0.015, 0.02)


def day_loss(level: float) -> float:
    return 0.03 if level >= 0.02 - 1e-9 else 0.02


def _pf(xs):
    p, n = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return p / n if n else (99.0 if p else 0.0)


def decide(S: dict, closes: list, open_upl: float, budget: float, now: int, cfg: dict | None = None) -> tuple[dict, str | None]:
    """S = the ladder state (changed in place); closes = [(t, $)] of real copies since «since», oldest first. → (S, reason or None)"""
    c = cfg or {}
    lv = tuple(float(x) for x in (c.get("levels") or LEVELS) if 0 < float(x) <= 0.02) or LEVELS
    S.setdefault("i", 0)
    S.setdefault("changed_t", 0)
    S.setdefault("up_t", 0)
    S["i"] = max(0, min(len(lv) - 1, int(S["i"])))
    eq = sum(u for _t, u in closes) + float(open_upl or 0)
    since = [u for t, u in closes if t > S["changed_t"]]
    S["high_c"] = max(float(S.get("high_c", eq)), eq) if S.get("high_c") is not None else eq      # high since the last change
    S["high"] = max(float(S.get("high", eq)), eq) if S.get("high") is not None else eq            # high since «since»
    B = float(budget or 0) or 1.0
    dd = (S["high_c"] - eq) / B
    why, new = None, S["i"]
    if dd > 0.05 and S["i"] > 0:
        new, why = 0, f"просадка {dd * 100:.1f}% бюджета от максимума > 5% → сразу {lv[0] * 100:g}%"
    elif S["i"] > 0 and len(since) >= 3 and all(u < 0 for u in since[-3:]):
        new, why = S["i"] - 1, "3 убыточных закрытия подряд"
    elif S["i"] > 0 and len(since) >= 10 and _pf(since[-10:]) < 1.0:
        new, why = S["i"] - 1, f"PF последних 10 {_pf(since[-10:]):.2f} < 1,0"
    elif S["i"] > 0 and dd > 0.03:
        new, why = S["i"] - 1, f"просадка {dd * 100:.1f}% бюджета от максимума > 3%"
    elif (S["i"] < len(lv) - 1 and len(since) >= 15 and _pf(since[-15:]) >= 1.5 and sum(u > 0 for u in since[-15:]) / 15 >= 0.55
          and (S["high"] - eq) / B <= 0.01 and now - S["up_t"] >= 3 * DAY):
        new, why = S["i"] + 1, (f"15 закрытий: PF {_pf(since[-15:]):.2f}, прибыльных {sum(u > 0 for u in since[-15:]) / 15 * 100:.0f}%, "
                                "капитал у максимума")
    if new != S["i"]:
        S.setdefault("log", []).append({"t": now, "from": lv[S["i"]], "to": lv[new], "why": why})
        S["log"] = S["log"][-50:]
        if new > S["i"]:
            S["up_t"] = now
        S["i"], S["changed_t"], S["high_c"] = new, now, eq
        return S, why
    return S, None


def level(S: dict | None, cfg: dict | None = None) -> float:
    lv = tuple(float(x) for x in ((cfg or {}).get("levels") or LEVELS) if 0 < float(x) <= 0.02) or LEVELS
    return lv[max(0, min(len(lv) - 1, int((S or {}).get("i", 0))))]


def backtest(trades: list, budget: float = 1000.0, mode: str = "ladder", stop_pct: float = 16.0) -> dict:
    """trades = [(t_close, result % of notional)]; a copy risks level × budget at a stop of stop_pct → $ = result / stop × risk"""
    S, eq, peak, dd, hist = {}, 0.0, 0.0, 0.0, []
    for t, r in trades:
        lvl = 0.01 if mode == "fixed1" else 0.02 if mode == "fixed2" else level(S)
        u = r / stop_pct * lvl * budget
        hist.append((t, u))
        eq += u
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        if mode == "ladder":
            decide(S, hist, 0.0, budget, t)
    return {"mode": mode, "n": len(trades), "pnl_usd": round(eq, 2), "ret_pct": round(eq / budget * 100, 2),
            "max_dd_pct": round(dd / budget * 100, 2), "changes": len(S.get("log") or []), "final_level": level(S) if mode == "ladder" else None}
