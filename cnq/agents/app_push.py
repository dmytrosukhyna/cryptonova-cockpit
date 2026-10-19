"""Push messages for the 2.0 app — useful, calm, never spammy (owner 19.10: «чтобы это не раздражало, было понятно, приятно»).

Rules:
- only what the user switched on (Кабинет → Уведомления); default: trades on, news on, piggy off, weekly summary on;
- quiet hours 22:00–09:00 Kyiv time: nothing is sent (trade news is not queued — it would be stale);
- trades: a CRYPTO NOVA demo trade opened or closed, at most 6 messages a day per person, several at once are bundled;
- piggy: one reminder on the buy day at 10:00 Kyiv; weekly summary: Friday 18:00 Kyiv.
Every message says «демо». Runs every engine cycle; reads data/api.sqlite (users, prefs, push tokens) and web/paper.json."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time

KYIV = dt.timezone(dt.timedelta(hours=3))
QUIET = (22, 9)
TRADE_CAP = 6


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


def trade_events(paper: dict) -> list[dict]:
    bal = (paper.get("profiles") or {}).get("bal") or paper
    ev = []
    for o in bal.get("open") or []:
        side = "рост" if str(o.get("side")).upper() == "LONG" else "падение"
        tps = o.get("tps") or []
        ev.append({"key": f"open:{o.get('id') or o.get('symbol')}:{o.get('opened')}", "title": "Новая сделка CRYPTO NOVA · демо",
                   "body": f"{o.get('symbol')} · {side} · вход {_fmt_price(o.get('entry'))}, стоп {_fmt_price(o.get('stop'))}"
                           + (f", цель {_fmt_price(tps[-1])}" if tps else ""), "sym": o.get("symbol")})
    for c in bal.get("closed") or []:
        pnl = float(c.get("pnl_usd") or 0)
        ev.append({"key": f"close:{c.get('id') or c.get('symbol')}:{c.get('closed')}", "title": "Сделка закрыта · демо",
                   "body": f"{c.get('symbol')}: {_fmt_usd(pnl)} на счёте $10 000", "sym": c.get("symbol")})
    return ev


def run(conn: sqlite3.Connection, api_db: str, out: str = "web", now_ms: int | None = None, fcm=None) -> dict:
    now_ms = now_ms or int(time.time() * 1000)
    _db(conn)
    if fcm is None:
        from cnq.api.push import Fcm
        fcm = Fcm()
    try:
        with open(os.path.join(out, "paper.json"), encoding="utf-8") as f:
            paper = json.load(f)
    except Exception:  # noqa: BLE001
        paper = {}
    first_run = conn.execute("SELECT 1 FROM push_seen WHERE key='__init__'").fetchone() is None
    if first_run:
        conn.execute("INSERT OR IGNORE INTO push_seen VALUES('__init__', ?)", (now_ms,))
    fresh = []
    for e in trade_events(paper):
        if conn.execute("SELECT 1 FROM push_seen WHERE key=?", (e["key"],)).fetchone():
            continue
        conn.execute("INSERT INTO push_seen VALUES(?,?)", (e["key"], now_ms))
        fresh.append(e)
    conn.commit()
    if first_run:                 # the very first run only remembers what already exists — no flood of old trades
        return {"sent": 0, "first_run": True, "events": len(fresh)}
    if not os.path.exists(api_db):
        return {"sent": 0, "reason": "no api db"}
    from cnq.api.store import Store
    st = Store(api_db)
    day = _now_kyiv(now_ms).strftime("%Y-%m-%d")
    sent, dropped, results = 0, 0, {}
    msgs: list[tuple[str, str, str, dict]] = []      # (pref, title, body, data)
    if fresh and not quiet(now_ms):
        if len(fresh) == 1:
            msgs.append(("trades", fresh[0]["title"], fresh[0]["body"], {"open": "futures"}))
        else:
            msgs.append(("trades", f"CRYPTO NOVA · {len(fresh)} события по сделкам · демо",
                         "; ".join(e["body"] for e in fresh[:3]) + (" …" if len(fresh) > 3 else ""), {"open": "futures"}))
    k = _now_kyiv(now_ms)
    if k.weekday() == 4 and k.hour == 18 and k.minute < 2:
        prof = (paper.get("profiles") or {}).get("bal") or {}
        hist = prof.get("equity_history") or []
        week_ago = now_ms - 7 * 86_400_000
        base = next((h["equity"] for h in hist if h["time"] >= week_ago), None)
        if base and hist:
            msgs.append(("brief", "Итоги недели · CRYPTO NOVA", f"Система за неделю: {_fmt_usd(hist[-1]['equity'] - base)} на демо-счёте $10 000. Подробности — в приложении.",
                         {"open": "home"}))
    for pref, title, body, data in msgs:
        for uid, tok in st.push_tokens(pref=pref):
            if pref == "trades":
                n = conn.execute("SELECT COUNT(*) FROM push_log WHERE uid=? AND kind='trades' AND day=?", (uid, day)).fetchone()[0]
                if n >= TRADE_CAP:
                    continue
            r = fcm.send(tok, title, body, data)
            results[r] = results.get(r, 0) + 1
            if r == "gone":
                st.drop_push(tok)
                dropped += 1
            elif r in ("ok", "dry"):
                conn.execute("INSERT INTO push_log VALUES(?,?,?,?)", (uid, pref, day, now_ms))
                sent += 1
    # piggy reminders: once on the buy day at 10:00 Kyiv
    if k.hour == 10 and k.minute < 2:
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
    return {"sent": sent, "dropped": dropped, "results": results, "events": len(fresh), "quiet": quiet(now_ms)}
