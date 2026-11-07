"""«Утренний доклад» (07.11.2026, owner 16:25): a short daily report at 07:00 Kyiv — what the system made over the last 24 h, the
market in a line, the account, and one line for the day only when there is something new. Warm, honest, no promises; public
wording «система CRYPTO NOVA» (never the method).

Numbers: web/public_real.json (the real account: 24 h futures / spot / total from its curves, value, result since 22.10) and
web/markets.json (BTC, fear & greed); the day line from the macro calendar (cnq/agents/macro_calendar.py, events of today).
Variety: every part has several phrasings; the ones used on the last days are remembered (agents.sqlite morning_hist) and not
repeated; the day line is skipped when it is the same as yesterday's or empty.
Output: web/morning.json {day, asof, title, body (the push), owner {title, body}, text (spoken), audio} + web/morning.mp3
(the same neural voices as the spoken brief, cnq/agents/voice.py). The push itself goes out from cnq/agents/app_push.py.
Stdlib only (edge-tts optional for the mp3)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time

try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:  # noqa: BLE001
    KYIV = dt.timezone(dt.timedelta(hours=2))

BUILD_FROM = (6, 40)            # the report is written from 06:40 Kyiv (the push leaves at 07:00)
BUILD_TO = 11                   # … and not after 11:00 (a late report would describe the wrong night)
MEMORY_DAYS = 4                 # a phrasing used on any of the last 4 days is not used again
SMALL = 10.0                    # under $10 a day is «почти в ноль» (no «хорошие сутки» for a few dollars)
FNG_RU = {"extreme fear": "крайний страх", "fear": "страх", "neutral": "нейтрально", "greed": "жадность", "extreme greed": "крайняя жадность"}

TITLES = ["Доброе утро · система CRYPTO NOVA", "Утренний доклад CRYPTO NOVA", "CRYPTO NOVA · как прошла ночь",
          "С добрым утром от CRYPTO NOVA", "CRYPTO NOVA · коротко за сутки", "Новый день · система CRYPTO NOVA"]
UP = ["За сутки система заработала {tot}: фьючерсы {fut}, спот {spot}.",
      "Плюс за сутки — {tot} (фьючерсы {fut}, спот {spot}).",
      "Сутки в плюсе: {tot}. Фьючерсы {fut}, спот {spot}.",
      "Сутки закончились в плюсе: {tot} — фьючерсы {fut}, спот {spot}."]
DOWN = ["За сутки {tot}: фьючерсы {fut}, спот {spot}. Стопы на месте, система работает по плану.",
        "Сутки в минусе — {tot} (фьючерсы {fut}, спот {spot}). Риск каждой сделки ограничен стопом.",
        "Минус за сутки: {tot}. Фьючерсы {fut}, спот {spot} — система держит свои правила.",
        "Не лучшие сутки: {tot} (фьючерсы {fut}, спот {spot}). У каждой позиции стоит стоп."]
FLAT = ["Сутки почти в ноль: {tot} (фьючерсы {fut}, спот {spot}).",
        "Спокойные сутки: {tot} — фьючерсы {fut}, спот {spot}.",
        "Ночь без больших движений по счёту: {tot}."]
MARKET = ["Биткоин {btc} ({chg}), настроение рынка — {fng_ru} ({fng}).",
          "Рынок: биткоин {btc}, за сутки {chg}; индекс страха и жадности {fng} — {fng_ru}.",
          "Биткоин стоит {btc} ({chg} за сутки), на рынке {fng_ru}.",
          "BTC {btc}, {chg} за сутки. Настроение — {fng_ru}, {fng} из 100."]
ACCOUNT = ["Счёт {eq}, с 22.10 {since}.", "Реальный счёт — {eq} ({since} с 22.10).", "На счёте {eq}; результат с 22.10 — {since}.",
           "Реальный счёт CRYPTO NOVA: {eq}, с начала {since}."]
ACCOUNT_OWNER = ["Твой счёт {eq}, с 22.10 {since}; позиций открыто: {open}.", "На твоём счёте {eq} ({since} с 22.10), открыто позиций: {open}.",
                 "Счёт {eq}, с 22.10 {since}. В работе позиций: {open}."]
DAY = ["Сегодня: {ev}.", "Что важно сегодня: {ev}.", "На сегодня в календаре: {ev}."]


def _load(out: str, name: str) -> dict:
    try:
        d = json.load(open(os.path.join(out, name), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _usd(v: float, sign: bool = True) -> str:
    a = abs(float(v))
    if a < 0.005:
        return "$0"
    body = (f"{a:,.2f}" if a < 10 else f"{a:,.0f}").replace(",", " ").replace(".", ",")
    return ("−" if v < 0 else "+" if sign else "") + "$" + body


def _pct(v: float) -> str:
    return ("−" if v < 0 else "+") + f"{abs(float(v)):.1f}".replace(".", ",") + "%"


def _change(curve, now: float):
    pts = sorted((float(t), float(v)) for t, v in curve or [] if v is not None)
    if not pts:
        return None
    base = next((v for t, v in reversed(pts) if t <= now - 86_400_000), pts[0][1])
    return pts[-1][1] - base


def facts(out: str, now: int) -> dict | None:
    """the numbers of the report; None when the real account's file is missing or stale"""
    r = _load(out, "public_real.json")
    if not r or r.get("stale") or r.get("equity_usd") is None:
        return None
    tot = _change(r.get("curve"), now)
    fut = _change(r.get("fut_curve"), now)
    if tot is None:
        tot = float(r.get("today_usd") or 0)
    m = _load(out, "markets.json")
    btc = next((t for t in m.get("top") or [] if isinstance(t, dict) and t.get("symbol") == "BTC"), {})
    f = m.get("fng") or {}
    ev = []
    try:
        from . import macro_calendar
        k = dt.datetime.fromtimestamp(now / 1000, KYIV).date()
        for e in macro_calendar.upcoming(now, 1.2):
            loc = dt.datetime.fromtimestamp(e["time"] / 1000, KYIV)
            if (e.get("importance") or 0) >= 2 and loc.date() == k and e["time"] >= now:
                ev.append(f"{e['name']} в {loc:%H:%M} — возможны резкие движения")
    except Exception:  # noqa: BLE001
        ev = []
    return {"total": tot, "futures": fut, "spot": (tot - fut) if fut is not None else None, "equity": float(r["equity_usd"]),
            "since_usd": float(r.get("pnl_usd") or 0), "since_pct": float(r.get("pnl_pct") or 0),
            "open": len([o for o in r.get("futures") or [] if isinstance(o, dict)]),
            "btc": btc.get("price"), "btc_chg": btc.get("chg24"), "fng": f.get("value"),
            "fng_ru": FNG_RU.get(str(f.get("label") or "").lower(), f.get("label") or ""), "event": ev[0] if ev else None}


def _hist(conn) -> list[dict]:
    if conn is None:
        return []
    conn.execute("CREATE TABLE IF NOT EXISTS morning_hist(day TEXT PRIMARY KEY, picks TEXT)")
    return [json.loads(p) for (p,) in conn.execute("SELECT picks FROM morning_hist ORDER BY day DESC LIMIT ?", (MEMORY_DAYS,))]


def _pick(pool: list, key: str, day: str, hist: list[dict]) -> int:
    """the day's phrasing of one part: deterministic for the day, never one used on the last MEMORY_DAYS days"""
    used = {h.get(key) for h in hist[:max(1, len(pool) - 1)]}     # a small pool remembers fewer days, so a choice always remains
    free = [i for i in range(len(pool)) if i not in used] or list(range(len(pool)))
    return free[int(hashlib.sha1((day + key).encode()).hexdigest(), 16) % len(free)]


def compose(F: dict, day: str, hist: list[dict]) -> dict:
    picks = {}
    tot, fut, spot = F["total"], F["futures"], F["spot"]
    kind = "up" if tot >= SMALL else "down" if tot <= -SMALL else "flat"
    pool = {"up": UP, "down": DOWN, "flat": FLAT}[kind]
    picks["t"] = _pick(TITLES, "t", day, hist)
    picks["r"] = _pick(pool, "r" + kind, day, hist)
    picks["rk"] = kind
    res = pool[picks["r"]].format(tot=_usd(tot), fut=_usd(fut) if fut is not None else "—", spot=_usd(spot) if spot is not None else "—")
    if fut is None and "{fut}" in pool[picks["r"]]:
        res = f"За сутки {_usd(tot)}."
    mk = ""
    if F.get("btc"):
        picks["m"] = _pick(MARKET, "m", day, hist)
        mk = MARKET[picks["m"]].format(btc="$" + f"{float(F['btc']):,.0f}".replace(",", " "), chg=_pct(F.get("btc_chg") or 0),
                                       fng=F.get("fng") if F.get("fng") is not None else "—", fng_ru=F.get("fng_ru") or "без оценки")
    picks["a"] = _pick(ACCOUNT, "a", day, hist)
    acc = ACCOUNT[picks["a"]].format(eq=_usd(F["equity"], False), since=_pct(F["since_pct"]))
    acc_o = ACCOUNT_OWNER[picks["a"] % len(ACCOUNT_OWNER)].format(eq=_usd(F["equity"], False), since=_usd(F["since_usd"]), open=F["open"])
    day_line = ""
    if F.get("event") and all(h.get("ev") != F["event"] for h in hist[:1]):
        picks["d"] = _pick(DAY, "d", day, hist)
        day_line = DAY[picks["d"]].format(ev=F["event"])
        picks["ev"] = F["event"]
    body = " ".join(x for x in (res, mk, acc) if x)
    owner = " ".join(x for x in (res, mk, acc_o) if x)
    if day_line and len(body) + len(day_line) < 230:
        body += " " + day_line
        owner += " " + day_line
    head = ". ".join(p[:1].upper() + p[1:] for p in TITLES[picks["t"]].split(" · ")) + "."
    text = " ".join(x for x in (head, res, mk, acc, day_line,
                                "Это не обещание дохода: система держит свои правила и стопы. Хорошего дня!") if x)
    return {"title": TITLES[picks["t"]], "body": body, "owner": {"title": TITLES[picks["t"]], "body": owner}, "text": text, "picks": picks}


def synth(text: str, path: str) -> tuple[str | None, list[str]]:
    """the spoken report into path (mp3) with the brief's voices; (voice or None, errors). A failure keeps the old file."""
    errs, tmp = [], path + ".tmp"
    try:
        import asyncio
        import edge_tts
        from .voice import RATE, brief_voices, spoken
        for v in brief_voices():
            try:
                async def go(v=v):
                    await edge_tts.Communicate(spoken(text), v, rate=RATE).save(tmp)
                asyncio.run(go())
                if os.path.getsize(tmp) > 2000:
                    os.replace(tmp, path)
                    return v, errs
                errs.append(f"{v}: short audio")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{v}: {str(e)[:60]}")
    except Exception as e:  # noqa: BLE001
        errs.append(str(e)[:80])
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return None, errs


def build(out: str = "web", conn=None, now: int | None = None, audio: bool = True) -> dict | None:
    now = now or int(time.time() * 1000)
    F = facts(out, now)
    if not F:
        return None
    day = dt.datetime.fromtimestamp(now / 1000, KYIV).strftime("%Y-%m-%d")
    hist = [h for h in _hist(conn)]
    doc = {"day": day, "asof": now, **compose(F, day, hist), "facts": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in F.items()},
           "audio": None}
    if audio:
        v, errs = synth(doc["text"], os.path.join(out, "morning.mp3"))
        doc["audio"], doc["voice"] = ("morning.mp3" if v else None), v
        if errs:
            doc["audio_error"] = " | ".join(errs)[:300]
    if conn is not None:
        conn.execute("INSERT OR REPLACE INTO morning_hist VALUES(?,?)", (day, json.dumps(doc["picks"], ensure_ascii=False)))
        conn.commit()
    with open(os.path.join(out, "morning.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc


def ensure(out: str, conn, now: int | None = None, audio: bool = True) -> dict | None:
    """today's report: built once in the morning window (06:40–11:00 Kyiv); the saved one otherwise"""
    now = now or int(time.time() * 1000)
    k = dt.datetime.fromtimestamp(now / 1000, KYIV)
    cur = _load(out, "morning.json")
    if cur.get("day") == k.strftime("%Y-%m-%d"):
        return cur
    if (k.hour, k.minute) < BUILD_FROM or k.hour >= BUILD_TO:
        return None
    return build(out, conn, now, audio)
