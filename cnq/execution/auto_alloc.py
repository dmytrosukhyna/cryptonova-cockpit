"""Автораспределение счёта раз в неделю (решение владельца 05.11.2026, явное «да»: docs/owner-decision-2026-11-05-auto-alloc.md).

Phase 1 (built 06.11, backlog item 87) — the futures («Киты») share of the real OKX account:
  * start 25 % (okx_alloc.SHARES["futures"]);
  * once a WEEK (the first review one week after this module first ran on the server) the share is reviewed on the account's own
    closed swaps of the last 30 days, whale-copy era only (opened ≥ WHALE_ERA_MS — 04.11.2026 21:00 UTC, when the real futures
    budget became 100 % «Киты»); a review needs ≥ MIN_TRADES closed swaps, otherwise the share stays;
  * PF ≥ 1.5 AND max drawdown of the closed-trade curve < 10 % of the futures budget → +10 p.p., ceiling 50 %;
    result in minus (realized P/L, fees and funding included) → −10 p.p., floor 10 %; anything else → unchanged;
  * the p.p. come from / go to the spot portfolio share (owner's point 5: money goes first where the live result is proven; the
    spot portfolio keeps its own cash and «кэш → ядро» rules); spot + core never below 30 % (owner's point 3).
Protections are unchanged (risk per trade, daily stop, the stop before liquidation — okx_futures); fut_budget() never squeezes
open margin. An explicit env OKX_FUT_SHARE / OKX_SPOT_SHARE still wins (okx_alloc.shares). Nothing here places or moves money:
it only keeps a state file that okx_alloc.shares() reads. Points 4 (early coins, needs backlog 86) and the Earn rule are not in
phase 1. Not financial advice. Stdlib only."""
from __future__ import annotations

import json
import os
import time

WHALE_ERA_MS = 1793826000000          # 2026-11-04 21:00 UTC — first real whale copies
WEEK_MS = 7 * 86_400_000
WINDOW_MS = 30 * 86_400_000
MIN_TRADES = 20
STEP = 0.10
FLOOR, CEIL = 0.10, 0.50
START = 0.25
PF_MIN, DD_MAX = 1.5, 0.10
SPOT_CORE_MIN = 0.30


def path(env=None) -> str:
    e = os.environ if env is None else env
    return os.path.expanduser(e.get("CNQ_AUTO_ALLOC_STATE") or "~/.cryptonova_auto_alloc.json")


def load(env=None) -> dict:
    try:
        with open(path(env), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save(st: dict, env=None) -> None:
    p = path(env)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(p + ".tmp", p)


def futures_share(st: dict) -> float | None:
    """the auto futures share, or None (no state yet → okx_alloc's default)"""
    v = st.get("futures") if isinstance(st, dict) else None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and FLOOR - 1e-9 <= v <= CEIL + 1e-9:
        return float(v)
    return None


def stats(closed: list, now: int, budget_usd: float) -> dict:
    """the window's numbers: n, pnl, pf, max drawdown share of the futures budget (closed-trade curve, in closing order)"""
    lo = max(WHALE_ERA_MS, now - WINDOW_MS)
    rows = [r for r in closed or [] if isinstance(r, dict) and isinstance(r.get("pnl_usd"), (int, float))
            and int(r.get("opened") or 0) >= lo and int(r.get("closed") or 0) <= now]
    rows.sort(key=lambda r: int(r.get("closed") or 0))
    pnl = [float(r["pnl_usd"]) for r in rows]
    win, loss = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p < 0)
    pf = (win / loss) if loss > 0 else (float("inf") if win > 0 else 0.0)
    cum = peak = dd = 0.0
    for p in pnl:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    b = float(budget_usd or 0.0)
    return {"n": len(pnl), "pnl": round(sum(pnl), 2), "pf": round(pf, 3) if pf != float("inf") else None,
            "dd": round(dd / b, 4) if b > 0 else None, "from": lo}


def decide(share: float, s: dict) -> tuple[float, str]:
    """(new share, why in Russian)"""
    if s["n"] < MIN_TRADES:
        return share, f"мало сделок за 30 дней ({s['n']} из {MIN_TRADES}) — доля не меняется"
    pf = s["pf"] if s["pf"] is not None else float("inf")
    if s["pnl"] < 0:
        new = max(FLOOR, round(share - STEP, 4))
        return new, f"фьючерсы в минусе за 30 дней (${s['pnl']:.2f}) — −10 п.п. (пол {FLOOR:.0%})"
    if pf >= PF_MIN and s["dd"] is not None and s["dd"] < DD_MAX:
        new = min(CEIL, round(share + STEP, 4))
        return new, f"PF {pf:.2f} ≥ {PF_MIN}, просадка {s['dd']:.1%} < {DD_MAX:.0%} — +10 п.п. (потолок {CEIL:.0%})"
    return share, "результат не в минусе, но PF/просадка не дотягивают — доля не меняется"


def update(closed: list, start_usd: float, now: int | None = None, st: dict | None = None, env=None, persist: bool = True) -> dict:
    """called by the server (public_real.run, every ~15 min, after an ok read). A state without «next» starts the clock: the first
    review is one week later. Returns the state."""
    now = int(now or time.time() * 1000)
    if st is None:
        st = load(env)
    changed = False
    if futures_share(st) is None:
        st = {"futures": START, "started": now, "next": now + WEEK_MS, "log": []}
        changed = True
    if now >= int(st.get("next") or 0):
        share = futures_share(st) or START
        s = stats(closed, now, share * float(start_usd or 0.0))
        new, why = decide(share, s)
        st["log"] = (list(st.get("log") or []) + [{"t": now, "from": share, "to": new, "why_ru": why, **s}])[-30:]
        st["futures"], st["next"], st["last"] = new, now + WEEK_MS, now
        changed = True
    if changed and persist:
        save(st, env)
    return st


def apply(w: dict, fut: float | None) -> dict:
    """okx_alloc shares with the auto futures share: the difference to/from spot, spot + core ≥ SPOT_CORE_MIN"""
    if fut is None:
        return w
    cur = float(w.get("futures") or 0.0)
    fut = min(fut, max(0.0, 1 - SPOT_CORE_MIN - float(w.get("active") or 0.0)))
    d = fut - cur
    spot = float(w.get("spot") or 0.0) - d
    core = float(w.get("core") or 0.0)
    if spot + core < SPOT_CORE_MIN:
        spot = max(0.0, SPOT_CORE_MIN - core)
        fut = cur + (float(w.get("spot") or 0.0) - spot)
    out = dict(w)
    out["spot"], out["futures"] = round(spot, 6), round(fut, 6)
    out["reserve"] = round(max(0.0, 1 - sum(out[k] for k in ("spot", "core", "futures", "active"))), 6)
    return out
