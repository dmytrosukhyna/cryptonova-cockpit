"""Push messages for the 2.0 app — useful, calm, never spammy (owner 19.10: «чтобы это не раздражало, было понятно, приятно»).

Rules:
- only what the user switched on (Кабинет → Уведомления); default (28.10, spec §14 — no notifications without consent): all off;
- quiet hours 22:00–09:00 Kyiv time: nothing is sent (trade news is not queued — it would be stale);
- trades (05.11, owner: the system copies the top-5 verified traders since 04.11): ONE evening summary of the «Киты» demo desk
  (whales_top.json desk) at 20:00 Kyiv — copies closed today, their result, how many are open; only when a closed copy moved at
  least $5 (no pushes about $1–2 trades); no push per trade, the retired rule desks are no longer pushed;
- piggy: one reminder on the buy day at 10:00 Kyiv; weekly summary («Киты» desk over 7 days): Friday 18:00 Kyiv;
- «важное на рынке» (news switch): bitcoin moved ≥ 5% in 24 h — at most once a day, 10:00–21:00 Kyiv.
Every trade message says «демо». Runs every engine cycle; reads data/api.sqlite (users, prefs, push tokens), web/whales_top.json
and web/markets.json. Timed messages have a 10-minute window and are deduplicated per person and day (a slow cycle never
skips or doubles them)."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time

try:                                  # Kyiv time with summer / winter time (UTC+3 / UTC+2)
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:  # noqa: BLE001
    KYIV = dt.timezone(dt.timedelta(hours=3))
QUIET = (22, 9)
TRADE_CAP = 1           # 05.11: one «Киты» summary a day per person
MIN_USD = 5.0           # a copy that moved less than $5 is not news (owner 04.11: «сделки по $1–2 неинтересны»)
DAILY_HOUR = 20         # the evening summary, Kyiv time
DAY_MS = 86_400_000
BIG_MOVE = 5.0          # % in 24 h that counts as «важное на рынке»


def _now_kyiv(now_ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(now_ms / 1000, KYIV)


def quiet(now_ms: int) -> bool:
    h = _now_kyiv(now_ms).hour
    return h >= QUIET[0] or h < QUIET[1]


def _fmt_usd(v: float) -> str:
    s = f"{abs(v):,.0f}".replace(",", " ") if abs(v) >= 10 else f"{abs(v):.2f}"
    return ("+" if v >= 0 else "−") + "$" + s


def _fmt_price(p) -> str:
    if p is None:
        return "—"
    p = float(p)
    return f"{p:,.0f}".replace(",", " ") if p >= 1000 else f"{p:.2f}" if p >= 10 else f"{p:.4f}" if p >= 0.01 else f"{p:.6f}"


def _db(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS push_seen(key TEXT PRIMARY KEY, t INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS push_log(uid TEXT, kind TEXT, day TEXT, t INTEGER)")


def _desk(out: str) -> dict:
    try:
        with open(os.path.join(out, "whales_top.json"), encoding="utf-8") as f:
            d = (json.load(f) or {}).get("desk") or {}
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _closed_since(desk: dict, since_ms: int) -> list[dict]:
    return [c for c in desk.get("closed") or [] if isinstance(c, dict) and (c.get("closed_t") or 0) >= since_ms]


def day_summary(out: str, now_ms: int) -> tuple[str, str] | None:
    """(title, body) of the evening «Киты» summary: the copies the demo desk closed today (Kyiv) — None when nothing worth a push
    (no closed copy that moved at least MIN_USD)"""
    desk = _desk(out)
    k = _now_kyiv(now_ms)
    day0 = int(k.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    cl = _closed_since(desk, day0)
    if not any(abs(float(c.get("pnl") or 0)) >= MIN_USD for c in cl):
        return None
    tot = sum(float(c.get("pnl") or 0) for c in cl)
    wins = sum(1 for c in cl if float(c.get("pnl") or 0) > 0)
    best = max(cl, key=lambda c: float(c.get("pnl") or 0))
    n_open = len([o for o in desk.get("open") or [] if isinstance(o, dict)])
    body = (f"Закрыто сделок: {len(cl)}, в плюсе {wins}; итог {_fmt_usd(tot)} на демо-счёте $10 000"
            + (f". Лучшая: {best.get('coin')} {_fmt_usd(float(best.get('pnl') or 0))}" if float(best.get("pnl") or 0) > 0 else "")
            + f". В работе {n_open}.")
    return "Система CRYPTO NOVA за день · демо", body


def week_summary(out: str, now_ms: int) -> tuple[str, str] | None:
    """(title, body) of the Friday summary of the «Киты» demo desk over 7 days — None without closed copies"""
    desk = _desk(out)
    cl = _closed_since(desk, now_ms - 7 * DAY_MS)
    if not cl:
        return None
    tot = sum(float(c.get("pnl") or 0) for c in cl)
    return ("Итоги недели · система CRYPTO NOVA",
            f"Сигналы системы на демо-счёте $10 000: закрыто сделок {len(cl)}, итог {_fmt_usd(tot)}. Подробности — в приложении.")


def _big_move(out: str) -> tuple[str, str] | None:
    """bitcoin moved ≥ BIG_MOVE % in 24 h → (title, body) in calm words; None otherwise"""
    try:
        with open(os.path.join(out, "markets.json"), encoding="utf-8") as f:
            mk = json.load(f)
    except Exception:  # noqa: BLE001
        return None
    btc = next((r for r in mk.get("top") or [] if str(r.get("symbol") or "").upper() == "BTC"), None)
    if not btc or btc.get("chg24") is None:
        return None
    ch = float(btc["chg24"])
    if abs(ch) < BIG_MOVE:
        return None
    word = "вырос" if ch > 0 else "упал"
    return (f"Важное на рынке: биткоин {word} на {abs(ch):.0f}% за сутки",
            f"Сейчас {_fmt_price(btc.get('price'))} $. Такие движения бывают — что это значит, простыми словами объяснит NOVA.")


def run(conn: sqlite3.Connection, api_db: str, out: str = "web", now_ms: int | None = None, fcm=None) -> dict:
    now_ms = now_ms or int(time.time() * 1000)
    _db(conn)
    if fcm is None:
        from cnq.api.push import Fcm
        fcm = Fcm()
    first_run = conn.execute("SELECT 1 FROM push_seen WHERE key='__init__'").fetchone() is None
    if first_run:
        conn.execute("INSERT OR IGNORE INTO push_seen VALUES('__init__', ?)", (now_ms,))
    conn.commit()
    if not os.path.exists(api_db):
        return {"sent": 0, "reason": "no api db"}
    from cnq.api.store import Store
    st = Store(api_db)
    day = _now_kyiv(now_ms).strftime("%Y-%m-%d")
    sent, dropped, results = 0, 0, {}
    msgs: list[tuple[str, str, str, dict]] = []      # (pref, title, body, data)
    k = _now_kyiv(now_ms)
    summary = day_summary(out, now_ms) if k.hour == DAILY_HOUR and not quiet(now_ms) else None
    if summary:                                       # once a day per person (push_log); the whole hour so a slow cycle never skips it
        msgs.append(("trades", summary[0], summary[1], {"open": "futures"}))
    if k.weekday() == 4 and k.hour == 18:
        ws = week_summary(out, now_ms)
        if ws:
            # final QA 28.10: «Итоги … за неделю» — not «что заработала система» (a week can end in a loss)
            msgs.append(("brief", ws[0], ws[1], {"open": "home"}))
    move = _big_move(out)
    if move and 10 <= k.hour < 21:
        msgs.append(("news", move[0], move[1], {"open": "home"}))
    for pref, title, body, data in msgs:
        for uid, tok in st.push_tokens(pref=pref):
            if pref == "trades":
                n = conn.execute("SELECT COUNT(*) FROM push_log WHERE uid=? AND kind='trades' AND day=?", (uid, day)).fetchone()[0]
                if n >= TRADE_CAP:
                    continue
            elif conn.execute("SELECT 1 FROM push_log WHERE uid=? AND kind=? AND day=?", (uid, pref, day)).fetchone():
                continue          # brief and news: once a day per person
            r = fcm.send(tok, title, body, data)
            results[r] = results.get(r, 0) + 1
            if r == "gone":
                st.drop_push(tok)
                dropped += 1
            elif r in ("ok", "dry"):
                conn.execute("INSERT INTO push_log VALUES(?,?,?,?)", (uid, pref, day, now_ms))
                sent += 1
    # piggy reminders: once on the buy day at 10:00 Kyiv
    if k.hour == 10 and k.minute < 10:
        today = int(now_ms // 86_400_000)
        for uid, tok in st.push_tokens(pref="piggy"):
            p = st.piggy(uid) or {}
            if not p.get("on"):
                continue
            step = 30 if p.get("freq") == "month" else 7
            if (today - int(p.get("start_day") or today)) % step != 0:
                continue
            if conn.execute("SELECT 1 FROM push_log WHERE uid=? AND kind='piggy' AND day=?", (uid, day)).fetchone():
                continue
            amt = float(p.get("amount") or 50)
            r = fcm.send(tok, "Копилка · сегодня день покупки", f"+${amt:.0f} в биткоин — в демо, по сегодняшней цене. Загляните, как растёт ваш фонд.",
                         {"open": "piggy"})
            results[r] = results.get(r, 0) + 1
            if r in ("ok", "dry"):
                conn.execute("INSERT INTO push_log VALUES(?,?,?,?)", (uid, "piggy", day, now_ms))
                sent += 1
    conn.commit()
    return {"sent": sent, "dropped": dropped, "results": results, "summary": bool(summary), "quiet": quiet(now_ms)}
