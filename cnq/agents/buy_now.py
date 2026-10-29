"""«Если покупать сегодня» for the app's Home (22.10.2026, owner on build 43: «можно сюда прогноз вставить, как у нас в системе…
если покупать сегодня — наши рекомендации»).

The same features and the same points as the owner's panel (web/app.html → buyNow), computed here on the server from BingX
candles of the core coins (own_desk.COINS40), so the app never fetches candles itself:
  +20  the daily close is above its 200-day average («выше 200-дневной средней»);
  +30  the price sits on a 4-hour support with ≥ 3 touches: 0.2 ATR under its top … 1 ATR above it;
  +15  4-hour RSI(14) ≤ 40 — not overheated;
  +rating/4  the coin's research card (web/research.json) rates ≥ 60/100;
  +10  the largest traders lean long on it (web/whales.json).
A coin with ≥ 45 points is a candidate; the five best are published, strongest first, with grade «strong» (≥ 85), «good» (≥ 65)
or «possible». The panel's sixth point (an active long setup) is computed only in the panel's browser and is not used here, so a
score can be lower than the panel's — never higher. Nothing is invented: a feature without data gives no points.
web/buy_now.json — numbers and reason keys only; cnq/agents/app_home.py turns them into words in the app's five languages.
It is a list of candidates by data, not a recommendation to buy, and every app screen that shows it says so. Stdlib only."""
from __future__ import annotations

import json
import os
import time

from .own_desk import COINS40

# build 51 (owner 29.10): up to 10 candidates — the app shows 5 and «Показать ещё» for the rest
MIN_SCORE, TOP = 45, 10
STRONG, GOOD = 85, 65


def atr(b: list, n: int = 14) -> list[float]:
    """the panel's atr(): true range, simple mean for the first n bars, then Wilder smoothing"""
    tr = [b[0][2] - b[0][3]] + [max(x[2] - x[3], abs(x[2] - b[i][4]), abs(x[3] - b[i][4])) for i, x in enumerate(b[1:])]
    out, a = [], 0.0
    for i, v in enumerate(tr):
        a = sum(tr[:i + 1]) / (i + 1) if i < n else (a * (n - 1) + v) / n
        out.append(a)
    return out


def rsi(c: list[float], n: int = 14) -> list[float]:
    """the panel's rsi()"""
    out, g, l = [], 0.0, 0.0
    for i in range(len(c)):
        if not i:
            out.append(50.0)
            continue
        d = c[i] - c[i - 1]
        up, dn = max(d, 0.0), max(-d, 0.0)
        if i <= n:
            g += up / n
            l += dn / n
        else:
            g = (g * (n - 1) + up) / n
            l = (l * (n - 1) + dn) / n
        out.append(100.0 if l == 0 else 100 - 100 / (1 + g / l))
    return out


def levels(b: list, left: int = 3, right: int = 3, tol_atr: float = 0.6, max_age: int = 400, min_touches: int = 2) -> list[dict]:
    """the panel's levels(): swing highs/lows clustered within 0.6 ATR, ≥ 2 touches, not broken by two closes; nearest first.
    Bars are [open ms, o, h, l, c, …]."""
    n = len(b)
    if n < left + right + 10:
        return []
    atr_now = atr(b)[n - 1]
    sw = []
    for i in range(max(left, n - max_age), n - right):
        w = b[i - left:i + right + 1]
        hi, lo = max(x[2] for x in w), min(x[3] for x in w)
        if b[i][2] == hi and sum(1 for x in w if x[2] == hi) == 1:
            sw.append((i, b[i][2], "high"))
        if b[i][3] == lo and sum(1 for x in w if x[3] == lo) == 1:
            sw.append((i, b[i][3], "low"))
    sw.sort(key=lambda s: s[1])
    cl: list[list] = []
    for s in sw:
        if cl and abs(s[1] - sum(x[1] for x in cl[-1]) / len(cl[-1])) <= tol_atr * atr_now:
            cl[-1].append(s)
        else:
            cl.append([s])
    price = b[n - 1][4]
    out = []
    for c in cl:
        c.sort(key=lambda s: s[0])
        m = []
        for s in c:
            if m and s[0] - m[-1][0] < 3:
                continue
            m.append(s)
        if len(m) < min_touches:
            continue
        ps = [x[1] for x in m]
        lo, hi = min(ps), max(ps)
        kinds = {x[2] for x in m}
        kind = "both" if len(kinds) == 2 else ("resistance" if "high" in kinds else "support")
        broken = False
        for i in range(m[-1][0] + 1, n - 1):
            up = b[i][4] > hi + 0.5 * atr_now and b[i + 1][4] > hi + 0.5 * atr_now
            dn = b[i][4] < lo - 0.5 * atr_now and b[i + 1][4] < lo - 0.5 * atr_now
            if (up and kind != "support") or (dn and kind != "resistance"):
                broken = True
            if broken and lo <= b[i][4] <= hi and lo <= b[i + 1][4] <= hi:
                broken = False
        if broken:
            continue
        center = (lo + hi) / 2
        out.append({"lo": lo, "hi": hi, "center": center, "touches": len(m), "kind": kind,
                    "dist_atr": abs(center - price) / atr_now if atr_now else 0.0})
    out.sort(key=lambda L: L["dist_atr"])
    return out


def score(sym: str, b4: list, d1: list, card: dict | None, whale: dict | None) -> dict | None:
    """one coin: its points and the reasons behind them (keys + numbers), or None without enough candles"""
    if len(b4) < 60:
        return None
    last = b4[-1][4]
    c24 = last / b4[-7][4] - 1 if len(b4) > 7 and b4[-7][4] else 0.0
    pts, why = 0, []
    if len(d1) >= 60:
        closes = [x[4] for x in d1]
        sma200 = sum(closes[-200:]) / min(200, len(closes))
        if closes[-1] > sma200:
            pts += 20
            why.append({"k": "sma200"})
        else:                                     # build 51: said honestly too (no points)
            why.append({"k": "trend_down"})
    lv = levels(b4)
    sup = next((L for L in lv if L["center"] < last and L["touches"] >= 3), None)
    a = atr(b4)[-1] or last * 0.01
    if sup:
        dist = (last - sup["hi"]) / a
        if -0.2 <= dist <= 1:
            pts += 30
            why.append({"k": "support", "p": sup["lo"], "n": sup["touches"]})
    rs = rsi([x[4] for x in b4])[-1]
    if rs <= 40:
        pts += 15
        why.append({"k": "rsi", "v": round(rs)})
    rating = (card or {}).get("rating")
    if isinstance(rating, (int, float)) and rating >= 60:
        pts += round(rating / 4)
        why.append({"k": "quality", "v": int(rating)})
    if (whale or {}).get("lean") == "LONG":
        pts += 10
        why.append({"k": "whales"})
    # build 51 (owner: «почему» in plain words — trend, levels, volume): the last full day's volume against the 20 days before it
    # (context only, no points)
    if len(d1) >= 23:
        vols = [x[5] for x in d1[-22:-2]]
        avg = sum(vols) / len(vols) if vols else 0
        if avg > 0 and d1[-2][5] / avg >= 1.5:
            why.append({"k": "volume", "v": round(d1[-2][5] / avg, 1)})
    if pts < MIN_SCORE:
        return None
    grade = "strong" if pts >= STRONG else "good" if pts >= GOOD else "possible"
    return {"sym": sym, "price": last, "chg24": round(c24 * 100, 2), "score": pts, "grade": grade, "why": why}


def _load(out: str, name: str) -> dict:
    try:
        with open(os.path.join(out, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def fetch(sym: str, interval: str, limit: int) -> list:
    """[[open ms, o, h, l, c, volume], …] ascending, BingX perpetual (the engine's exchange)"""
    from ..bingx.rest import BingXRest
    return [[c.open_time, c.open, c.high, c.low, c.close, c.volume] for c in BingXRest().klines(f"{sym}-USDT", interval, limit=limit)]


def build(out: str = "web", now: int | None = None, candles=fetch, coins: list[str] | None = None) -> dict:
    now = now or int(time.time() * 1000)
    cards = {str(c.get("symbol") or "").upper(): c for c in _load(out, "research.json").get("cards") or [] if isinstance(c, dict)}
    whales = {str(c.get("coin") or "").upper(): c for c in _load(out, "whales.json").get("coins") or [] if isinstance(c, dict)}
    items, checked = [], 0
    for sym in coins or COINS40:
        try:
            b4, d1 = candles(sym, "4h", 300), candles(sym, "1d", 220)
        except Exception:  # noqa: BLE001
            continue
        if len(b4) < 60:
            continue
        checked += 1
        name = sym[4:] if sym.startswith("1000") else sym
        r = score(name, b4, d1, cards.get(name), whales.get(name))
        if r:
            items.append(r)
    items.sort(key=lambda x: -x["score"])
    return {"asof": now, "available": checked > 0, "checked": checked, "items": items[:TOP],
            "about_ru": "Кандидаты по данным (как «Если покупать сейчас» в панели), не рекомендация."}


def run(out: str = "web", now: int | None = None, candles=fetch) -> dict:
    doc = build(out, now, candles)
    path = os.path.join(out, "buy_now.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return doc
