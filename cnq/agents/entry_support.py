"""«No chase» entries of the CRYPTO NOVA AI spot portfolio (10.11, owner: «главное не поздно; искать лучшие точки входа»; option A).

A non-core coin the rules rate «можно брать» is bought ONLY when
  1. its price is at most CHASE (3 %) above a support:
       • the true 200-day average (web/terminal.json `daily_sma200`; owner_watch.json `sma200` from OKX daily candles), or
       • the largest PERSISTENT bid wall below the price (owner_watch.json `bid_walls` seen for ≥ WALL_MIN_MS);
     the nearest support below the price is the one checked (a price below every support has none — the level is broken);
  2. BTC is not breaking down: its price is above the 20-day low (decision.json BTC regime `ll20`) and the 28-day trend rule is
     not «SHORT».
No resting orders: the model re-checks every run, and the OKX stop (okx_spot_stops) is placed after the buy fills.
Missing data never passes: no support known → no buy (the reason says so)."""
from __future__ import annotations

import json
import os

CHASE = 0.03                     # at most 3 % above the support
WALL_MIN_MS = 60 * 60_000        # a bid wall counts once we have seen it for an hour (two 30-minute runs)
WALL_MIN_USD = 100_000           # smaller bands are noise


def _load(out: str, name: str) -> dict:
    try:
        d = json.load(open(os.path.join(out, name), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def load(out: str = "web") -> dict:
    """{"sma200": {SYM: float}, "walls": {SYM: [{"price", "usd", "venue", "since"}]}, "btc": {"price", "ll20", "mid"}, "asof"}"""
    term, ow, dec = _load(out, "terminal.json"), _load(out, "owner_watch.json"), _load(out, "decision.json")
    sma: dict[str, float] = {}
    for s, v in (term.get("daily_sma200") or {}).items():
        if isinstance(v, dict) and v.get("sma200") and (v.get("days") or 0) >= 200:
            sma[str(s).upper()] = float(v["sma200"])
    walls: dict[str, list] = {}
    asof = int(ow.get("asof") or 0)
    for c in ow.get("coins") or []:
        s = str(c.get("coin") or "").upper()
        if not s:
            continue
        if c.get("sma200") and s not in sma:
            sma[s] = float(c["sma200"])           # OKX 300 daily candles: a true 200-day average
        t = int(c.get("asof") or asof or 0)
        walls[s] = [w for w in c.get("bid_walls") or []
                    if w.get("price") and (w.get("usd") or 0) >= WALL_MIN_USD and w.get("since") and t - int(w["since"]) >= WALL_MIN_MS]
    btc = next((c for c in (dec.get("coins") or term.get("coins") or []) if str(c.get("symbol", "")).startswith("BTC")), None) or {}
    reg = btc.get("regime") or {}
    return {"sma200": sma, "walls": walls, "btc": {"price": btc.get("price"), "ll20": reg.get("ll20"), "mid": reg.get("mid")}}


def _px(p: float) -> str:
    return f"${p:,.0f}".replace(",", " ") if p >= 1000 else f"${p:.2f}" if p >= 1 else f"${p:.4g}"


def btc_breaking(sup: dict) -> str | None:
    """the reason BTC is breaking down, or None"""
    b = (sup or {}).get("btc") or {}
    px, ll = b.get("price"), b.get("ll20")
    if not px or not ll:
        return "нет данных о режиме BTC — не покупаем"
    if px <= ll:
        return f"BTC пробивает минимум 20 дней ({_px(px)} ≤ {_px(ll)}) — новые альты не покупаем"
    if b.get("mid") == "SHORT":
        return "BTC в 28-дневном нисходящем тренде у минимума 20 дней — новые альты не покупаем"
    return None


def check(sym: str, px: float, sup: dict | None) -> tuple[bool, str]:
    """(ok, reason_ru) for buying `sym` at `px` now"""
    if sup is None:
        return True, ""
    br = btc_breaking(sup)
    if br:
        return False, br
    s = sym.upper()
    lv = []
    if (sup.get("sma200") or {}).get(s):
        lv.append((sup["sma200"][s], f"200-дневной средней {_px(sup['sma200'][s])}"))
    ws = [w for w in (sup.get("walls") or {}).get(s) or [] if w["price"] <= px]
    if ws:
        w = max(ws, key=lambda x: x["usd"])
        lv.append((float(w["price"]), f"стены покупок ${w['usd'] / 1e6:.1f}M на {_px(float(w['price']))} ({w.get('venue') or 'стакан'})"))
    if not lv:
        return False, "нет данных о поддержке (ни 200-дневной средней, ни устойчивой стены покупок) — без поддержки не входим"
    below = [x for x in lv if x[0] <= px]
    if not below:
        lo = min(lv, key=lambda x: x[0])
        return False, f"цена {_px(px)} ниже {lo[1]} — поддержка пробита, ждём"
    lvl, name = max(below, key=lambda x: x[0])
    d = px / lvl - 1
    if d > CHASE:
        return False, (f"цена {_px(px)} на {d * 100:.1f}% выше {name} — не догоняем (вход не выше +{CHASE * 100:.0f}% над поддержкой), "
                       f"ждём отката к {_px(lvl * (1 + CHASE))}")
    return True, f"вход у поддержки: цена {_px(px)} на {d * 100:.1f}% выше {name}; BTC выше минимума 20 дней"
