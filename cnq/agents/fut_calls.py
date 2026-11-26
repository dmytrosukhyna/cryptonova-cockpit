"""Futures calls of public Telegram signal channels (owner 26.11.2026 18:24: «public communities with futures calls — entry / stop /
target — parse their history, rank by honest hit rate / PF, the best as PAPER desks»). Stdlib only. PAPER / RESEARCH.

A call = one post that names a coin, a side (long / short, buy / sell, лонг / шорт), an entry (a price, a zone, or «market»), a stop
and at least one target, in the usual layouts:
    #BTC/USDT LONG  Leverage 10x  Entry: 95000 - 94000  Targets: 1) 96000 2) 97000  Stop: 92000
    ETH SHORT · Entry zone 3050-3100 · TP1 2980 · TP2 2900 · SL 3180
    ЛОНГ SOL вход 140-138, тейки 145 / 150, стоп 133
A call whose numbers do not line up (LONG: stop < entry < first target; SHORT the other way) is dropped, never «fixed».
Channel handles never enter this module or the repository: the server reads them from the sealed file (web/sealed/), research runs
from a masked run input."""
from __future__ import annotations

import re

COIN = re.compile(r"(?:#|\$)?\b([A-Z][A-Z0-9]{1,11})\s*(?:/|-|_)?\s*(?:USDT|USDC|USD|PERP)\b|[#$]([A-Z][A-Z0-9]{1,11})\b|"
                  r"(?i:coin|pair|монета|пара)\s*[:\-–]?\s*[#$]?([A-Z][A-Z0-9]{1,11})\b|"
                  r"\b([A-Z][A-Z0-9]{1,11})\s+(?i:long|short|лонг|шорт)\b|\b(?i:long|short|лонг|шорт)\s+[#$]?([A-Z][A-Z0-9]{1,11})\b")
LONG = re.compile(r"\b(long|buy|лонг|покупка|bullish)\b", re.I)
SHORT = re.compile(r"\b(short|sell|шорт|продажа|bearish)\b", re.I)
KEYS = [("entry", r"entry\s*(?:zone|price|point|area)?|buy\s*(?:zone|area|between|price)|sell\s*(?:zone|area|between)|"
                  r"open\s*(?:price|at)|вход\w*|точк\w*\s*вход\w*|зона\s*вход\w*|enter"),
        ("tp", r"take[\s-]?profits?(?:\s*targets?)?|targets?|tp\d?|тейк\w*|цел[ьи]\w*|тп\d?"),
        ("sl", r"stop[\s-]?loss(?:\s*targets?)?|stop|sl|стоп\w*|invalidation"),
        ("lev", r"leverage|lev|плечо|cross|isolated|изолир\w*|кросс")]
KW = re.compile("|".join(f"(?P<{k}>\\b(?:{p})\\b)" for k, p in KEYS), re.I)
NUM = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)(?![\w.]*\s*[xх×%])(?!\))")
NOT = {"USDT", "USDC", "USD", "PERP", "LONG", "SHORT", "BUY", "SELL", "TP", "SL", "VIP", "ENTRY", "STOP", "TARGET", "TARGETS",
       "LEVERAGE", "CROSS", "BINANCE", "BYBIT", "OKX", "FUTURES", "SPOT", "SIGNAL", "SIGNALS", "FREE", "PREMIUM", "UPDATE", "ALERT",
       "PROFIT", "CLOSED", "DONE", "HIT", "NEW", "TOP", "PUMP", "MEXC", "BINGX", "BITGET", "CORNIX", "ROI"}
RESULT = re.compile(r"(target\s*\d?\s*(?:hit|done|reached|achieved)|all\s*targets|profit\s*[:\-]?\s*\+?\d|closed|stop(?:ped)?\s*(?:hit|out)|"
                    r"выбило|достигнут|отработал|закрыт|✅\s*tp|tp\s*\d?\s*✅|period\s*:\s*\d)", re.I)


def _nums(s: str) -> list:
    s = re.sub(r"(?:^|\s)\d{1,2}\s*[).:]\s", " ", s)            # list markers «1) » «2. »
    s = re.sub(r"\b(?:tp|тп|target|цель)\s*\d\b", " ", s, flags=re.I)
    s = re.sub(r"\d+(?:[.,]\d+)?\s*[xх×]\b|\d+(?:[.,]\d+)?\s*%", " ", s)
    out = []
    for m in NUM.finditer(s.replace("$", " ")):
        try:
            v = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        if v > 0:
            out.append(v)
    return out


def parse(text: str, coins: set | None = None) -> dict | None:
    """→ {coin, side, entry_lo, entry_hi (None = market), stop, targets [ordered our way], lev} or None"""
    if not text or RESULT.search(text):
        return None
    coin = None
    for m in COIN.finditer(text):
        c = next((g for g in m.groups() if g), "").upper()
        if c and c not in NOT and (coins is None or c in coins):
            coin = c
            break
    if not coin:
        return None
    lo, sh = LONG.search(text), SHORT.search(text)
    if lo and sh:
        side = "LONG" if lo.start() < sh.start() else "SHORT"
    elif lo or sh:
        side = "LONG" if lo else "SHORT"
    else:
        return None
    marks = [(m.lastgroup, m.start(), m.end()) for m in KW.finditer(text)]
    sec = {"entry": [], "tp": [], "sl": [], "lev": []}
    market = False
    for i, (k, a, b) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        chunk = text[b:end]
        if k == "entry" and re.search(r"\b(market|cmp|now|current|по рынку|рынок)\b", chunk[:40], re.I):
            market = True
        if k != "lev":
            sec[k] += _nums(chunk)
    if not sec["entry"] and re.search(r"\b(market|cmp|по рынку)\b", text, re.I):
        market = True
    if not sec["sl"] or not sec["tp"] or not (sec["entry"] or market):
        return None
    ent = sec["entry"][:2]
    stop = sec["sl"][0]
    ref = sum(ent) / len(ent) if ent else None
    tps = sec["tp"][:8]
    s = 1 if side == "LONG" else -1
    if ref is not None:
        if max(ent) / min(ent) > 1.2:                                  # a «zone» that is not a zone (a date / a size in the section)
            return None
        tps = [t for t in tps if s * (t - ref) > 0 and 0.002 < abs(t / ref - 1) < 2.0]
        if not tps or s * (ref - stop) <= 0 or abs(stop / ref - 1) > 0.5:
            return None
    tps = sorted(set(tps), key=lambda t: s * t)
    return {"coin": coin, "side": side, "entry_lo": min(ent) if ent else None, "entry_hi": max(ent) if ent else None,
            "stop": stop, "targets": tps, "market": market or not ent}


def calls(posts: list, coins: set | None = None, dedupe_h: float = 24) -> list:
    """posts [{id, t, text}] → calls [{id, t, …parse}] oldest first; the same coin + side again within dedupe_h = the same call"""
    out, last = [], {}
    for p in sorted(posts or [], key=lambda x: (x.get("t") or 0, x.get("id") or 0)):
        if not p.get("t"):
            continue
        c = parse(p.get("text") or "", coins)
        if not c:
            continue
        k = (c["coin"], c["side"])
        if k in last and p["t"] - last[k] < dedupe_h * 3_600_000:
            continue
        last[k] = p["t"]
        out.append({"id": p.get("id"), "t": p["t"], **c})
    return out


def sim(call: dict, bars: list, cost: float = 0.11, wait_h: int = 48, max_h: int = 168, mode: str = "tp1") -> dict | None:
    """one call on 1h bars [[t, o, h, l, c], …] ascending, starting after the post. Entry: market at the first bar's open when no
    zone (or the open is inside / beyond the zone our way), else the zone's near edge when a bar touches it within wait_h. Exit: the
    stop or the first target (mode tp1), or the last target with the stop moved to the entry after the first (mode trail); a bar that
    touches both the stop and a target counts as the stop; otherwise the close after max_h. → {r (R), pct, kind, hold_h} or None"""
    if not bars:
        return None
    s = 1 if call["side"] == "LONG" else -1
    lo, hi = call.get("entry_lo"), call.get("entry_hi")
    i0, px = None, None
    o0 = bars[0][1]
    if lo is None or (s > 0 and o0 <= hi) or (s < 0 and o0 >= lo):
        if lo is None or abs(o0 / ((lo + hi) / 2) - 1) <= 0.03:
            i0, px = 0, o0
    if i0 is None and lo is not None:
        edge = hi if s > 0 else lo
        for i, b in enumerate(bars[:wait_h]):
            if (s > 0 and b[3] <= edge) or (s < 0 and b[2] >= edge):
                i0, px = i, edge
                break
    if i0 is None:
        return {"kind": "no_fill"}
    stop = call["stop"]
    if s * (px - stop) <= 0:
        return {"kind": "no_fill"}
    risk = abs(px / stop - 1) * 100
    tps = [t for t in call["targets"] if s * (t - px) > 0]
    if not tps:
        return {"kind": "no_fill"}
    goal = tps[0] if mode == "tp1" else tps[-1]
    lvl, got1 = stop, False
    for j in range(i0, min(len(bars), i0 + max_h)):
        t, o, h, l, c = bars[j][:5]
        hit_s = (s > 0 and l <= lvl) or (s < 0 and h >= lvl)
        touch = j == i0 and px != o                            # filled by a touch inside this bar: its stop counts, its target does not
        if touch and not hit_s:
            continue
        if hit_s:
            ex = o if not touch and ((s > 0 and o < lvl) or (s < 0 and o > lvl)) else lvl
            pct = s * (ex / px - 1) * 100 - cost
            return {"r": round(pct / risk, 3), "pct": round(pct, 3), "kind": "be" if got1 else "stop", "hold_h": j - i0}
        if mode == "trail" and not got1 and ((s > 0 and h >= tps[0]) or (s < 0 and l <= tps[0])):
            got1, lvl = True, px
        if (s > 0 and h >= goal) or (s < 0 and l <= goal):
            pct = s * (goal / px - 1) * 100 - cost
            return {"r": round(pct / risk, 3), "pct": round(pct, 3), "kind": "tp", "hold_h": j - i0}
    last = bars[min(len(bars), i0 + max_h) - 1]
    pct = s * (last[4] / px - 1) * 100 - cost
    return {"r": round(pct / risk, 3), "pct": round(pct, 3), "kind": "time", "hold_h": min(len(bars), i0 + max_h) - 1 - i0}
