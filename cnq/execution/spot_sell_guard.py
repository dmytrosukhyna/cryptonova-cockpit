"""23.11.2026 08:56 (owner, explicit): «вообще не продаём ничего по споту» — refined the same minute: a spot sell is allowed ONLY as a
sound swap into a better coin. config/owner_overrides.json «nospotsell_1123» {"on": true, "rating_gap": 15, "day_cap_pct": 0.03}.
BLOCKED: every system-initiated spot sell (weekly review, model trims / exits, core → cash, sleeve exits, rotations) — the order is
dropped from the plan and noted «продажа заблокирована владельцем».
ALLOWED, only when ALL: in the SAME rebalance pass there is a buy of a DIFFERENT coin that takes the proceeds; that coin's research
rating (web/research.json) is ≥ rating_gap points above the sold coin's (core BTC / ETH / SOL among themselves: no rating test);
the bought coin is not falling (BTC in its bull regime, or the coin's daily close above its 20-day EMA); the swap sells ≤ day_cap_pct
of the account per UTC day. The allowed part is the smallest of the sell, the paired buys and the day's room — noted
«замена: X → Y, причина». Exchange stop-losses on OKX stay as they are (crash protection, okx_spot_stops). Buys are not touched."""
from __future__ import annotations

import json
import os
import time

KEY = "nospotsell_1123"
CORE = ("BTC", "ETH", "SOL")


def cfg(ov: dict | None) -> dict | None:
    c = (ov or {}).get(KEY)
    return c if isinstance(c, dict) and c.get("on") else None


# 26.11 09:40 Kyiv (owner: «если есть проблемы — продавать; запретов нет»), «problem_sells»: true in the override — a FULL exit of a
# non-core coin is allowed when the model leaves it because of a problem; the model marks such a sale «Продажа при проблеме», the
# real account repeats only those (its order «why» carries the model's reason). Trims, time / trailing / weekly exits, take-profits
# and every sale of the core BTC / ETH / SOL stay blocked (swaps as before).
PROBLEM_MARK = "Продажа при проблеме"
_PROBLEM = ("стоп", "кошельки", "красный флаг", "«не брать»", "не брать", "спот-пар")
_NOT_PROBLEM = ("трейлинг", "дней прошло", "недельный пересмотр", "фиксац", "откат 35%")


def problem(sym: str, reason: str) -> bool:
    """is this model exit of `sym` a problem exit (not core, reason names a problem, not a time / trailing / profit exit)"""
    r = str(reason or "").lower()
    if str(sym or "").upper() in CORE or not r:
        return False
    if any(w in r for w in _NOT_PROBLEM):
        return False
    return any(w in r for w in _PROBLEM)


def ratings(web: str) -> dict:
    try:
        with open(os.path.join(web, "research.json"), encoding="utf-8") as f:
            d = json.load(f)
        return {str(x.get("symbol") or "").upper(): float(x.get("rating") or 0) for x in d.get("cards") or [] if x.get("rating") is not None}
    except Exception:  # noqa: BLE001
        return {}


def sold_today(log_path: str, now: int) -> float:
    """$ of spot sells filled today (UTC) by the rebalancer — from its own log"""
    d0 = now - now % 86_400_000
    tot = 0.0
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 400_000))
            for ln in f.read().decode("utf-8", "replace").splitlines()[1:]:
                try:
                    ev = json.loads(ln)
                except ValueError:
                    continue
                if ev.get("ev") == "order" and ev.get("side") == "sell" and int(ev.get("t") or 0) >= d0 and (ev.get("filled") or 0):
                    tot += float(ev.get("usd") or 0)
    except OSError:
        pass
    return tot


def ema_ok(closes: list, n: int = 20) -> bool:
    if len(closes) < n + 1:
        return False
    e = closes[0]
    for c in closes[1:]:
        e = e + (c - e) * 2 / (n + 1)
    return closes[-1] > e


def apply(sells: list, buys: list, total_usd: float, c: dict, rate: dict, trend_ok, day_sold: float, btc_bull) -> tuple[list, list]:
    """→ (sells kept, notes). trend_ok(coin) → bool; the paired buy budget is consumed in order"""
    notes, kept = [], []
    room = max(0.0, float(c.get("day_cap_pct") or 0.03) * float(total_usd or 0) - float(day_sold or 0))
    gap = float(c.get("rating_gap") or 15)
    budget = {}
    for b in buys:
        budget[b["ccy"]] = budget.get(b["ccy"], 0.0) + float(b["usd"])
    for o in sells:
        x = o["ccy"]
        if c.get("problem_sells") and o.get("act") == "sell" and x not in CORE and PROBLEM_MARK in str(o.get("why") or ""):
            kept.append(o)                                   # 26.11 (owner): the model left the coin because of a problem
            notes.append(f"{x}: продажа при проблеме (решение владельца 26.11) — {str(o.get('why') or '')[:160]}")
            continue
        best, why = None, None
        for y, u in budget.items():
            if y == x or u <= 1.0:
                continue
            core = x in CORE and y in CORE
            if not core and rate.get(y, 0) - rate.get(x, 0) < gap:
                continue
            if not (btc_bull is True or trend_ok(y)):
                continue
            best = y
            why = ("ребаланс ядра" if core else f"рейтинг {rate.get(y, 0):.0f} против {rate.get(x, 0):.0f}") + \
                  (", BTC в росте" if btc_bull is True else ", цена выше 20-дневной EMA")
            break
        if not best:
            notes.append(f"{x}: продажа заблокирована владельцем ({o.get('act', 'sell')} ${o['usd']:.2f}) — нет замены на лучшую монету в этом проходе")
            continue
        allow = min(float(o["usd"]), budget[best], room)
        if allow < 10.0:
            notes.append(f"{x}: продажа заблокирована владельцем — дневной предел замен {float(c.get('day_cap_pct') or 0.03) * 100:g}% счёта исчерпан")
            continue
        k = allow / float(o["usd"])
        o2 = dict(o)
        if k < 0.999:
            o2["_scale"] = k
            o2["usd"] = round(allow, 2)
        o2["swap_to"] = best
        budget[best] -= allow
        room -= allow
        kept.append(o2)
        notes.append(f"замена: {x} → {best}, {why} (${allow:.2f})")
    return kept, notes
