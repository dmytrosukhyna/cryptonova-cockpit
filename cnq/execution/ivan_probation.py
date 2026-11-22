"""Ivan under probation on the real account (owner 22.11.2026 17:55: «аккуратненько, соблюдать систему, если ошибок много — следи»).
RISK-REDUCING ONLY: it can only pause NEW copies of Ivan's accounts (ivan / ivan2 / ivan3 / ivan4); open positions keep their stops
and exits; nothing is ever opened or resized by it. Ivan's copies already follow every system rule the other whales follow (the
−16 % stop, the day stop, the careful 2 % risk and long cap, the one-trader margin cap) — no exemption exists in okx_futures.

config/owner_overrides.json «ivan_probation_1122»: {"id", "since" (ms or ISO), "losses_in_row": 3, "pf10_min": 0.8, "dd_pct": 0.04,
"resume_after": null}. From «since», on Ivan's REAL copies only (web/public_real.json closed swaps matched to their desk-trade id in
web/real_why.json; open ones from public_real futures):
  • 3 losing closes in a row (a loss is OKX's realized P/L < 0; with the −16 % stop and his own exits a loss is a stop-out or a
    losing exit — both count), OR
  • the rolling PF of the last 10 closes < 0.8 (only with ≥ 10 closes), OR
  • drawdown of his copies (closed P/L in order + the open copies' P/L now, from the peak) > 4 % of the futures budget
→ PAUSED: the futures step skips every new Ivan copy (not final, so it is re-checked every pass) and logs «ivan_probation_pause» once.
Resume only with the owner's yes: «resume_after» set to a time after the pause. Stdlib only."""
from __future__ import annotations

import json
import os

KEY = "ivan_probation_1122"
PREFIXES = ("ivan:", "ivan2:", "ivan3:", "ivan4:")


def cfg(ov: dict | None) -> dict | None:
    c = (ov or {}).get(KEY)
    return c if isinstance(c, dict) and c.get("id") else None


def _ms(v) -> int:
    from .owner_overrides import _ms as m
    return m(v)


def _load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def copies(web: str, since: int) -> tuple[list, float]:
    """(Ivan's closed real copies since «since», oldest first: [(t, pnl $)], the open copies' P/L now)"""
    pr = _load(os.path.join(web, "public_real.json")) or {}
    why = _load(os.path.join(web, "real_why.json")) or {}
    mine = {(x.get("inst"), x.get("closed")) for x in why.get("closed") or [] if isinstance(x, dict) and str(x.get("sig") or "").startswith(PREFIXES)}
    cl = sorted((int(x["closed"]), float(x.get("pnl_usd") or 0)) for x in pr.get("closed") or []
                if isinstance(x, dict) and x.get("closed") and int(x["closed"]) >= since and (x.get("inst"), x.get("closed")) in mine)
    upl = sum(float(x.get("upnl_usd") or 0) for x in pr.get("futures") or [] if str(x.get("sig") or "").startswith(PREFIXES))
    return cl, upl


def check(closed: list, open_upl: float, budget: float, c: dict) -> str | None:
    """the reason to pause, or None"""
    pnl = [u for _t, u in closed]
    n = int(c.get("losses_in_row") or 3)
    if len(pnl) >= n and all(u < 0 for u in pnl[-n:]):
        return f"{n} убыточных закрытия подряд"
    if len(pnl) >= 10:
        last = pnl[-10:]
        pos, neg = sum(u for u in last if u > 0), -sum(u for u in last if u < 0)
        pf = pos / neg if neg else 99.0
        if pf < float(c.get("pf10_min") or 0.8):
            return f"PF последних 10 закрытий {pf:.2f} < {float(c.get('pf10_min') or 0.8):g}"
    cum, peak, dd = 0.0, 0.0, 0.0
    for u in pnl + [open_upl]:
        cum += u
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    lim = float(c.get("dd_pct") or 0.04) * float(budget or 0)
    if budget and dd > lim:
        return f"просадка копий ${dd:.2f} > {float(c.get('dd_pct') or 0.04) * 100:g}% бюджета фьючерсов (${lim:.2f})"
    return None


def gate(st: dict, ov: dict | None, web: str, budget: float, now: int, log=None) -> str | None:
    """called by the futures step before an Ivan copy: the pause reason (new Ivan copies are skipped) or None"""
    c = cfg(ov)
    if not c:
        return None
    P = st.setdefault("ivan_probation", {})
    if P.get("paused"):
        ra = _ms(c.get("resume_after"))
        if ra and ra > int(P.get("t") or 0) and now >= ra:
            P.clear()
            if log:
                log({"ev": "ivan_probation_resume", "t": now})
            return None
        return P.get("why")
    closed, upl = copies(web, _ms(c.get("since")) or 0)
    why = check(closed, upl, budget, c)
    if why:
        P.update({"paused": True, "t": now, "why": why, "closed_n": len(closed)})
        if log:
            log({"ev": "ivan_probation_pause", "t": now, "why": why, "closed_n": len(closed), "open_upl": round(upl, 2)})
    return why
