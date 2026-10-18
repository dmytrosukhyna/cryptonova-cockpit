"""Our own signals in Cornix format (18.10.2026, owner: «стоит ли вообще брать у них эту базу … или мы сможем генерировать
собственные?»).

The CRYPTO NOVA app shows signals that come from a Telegram channel («Ai Golden Crypto (Auto Bot)») through Cornix → TradeReader.
Those signals lose money over their whole history (web/app_signals.json). This module turns the trades of OUR strategy
(CNPTS «Сбалансированная», the `bal` paper desk) into Cornix-format messages:
  • a new trade on the desk → an «open» message (pair, side, leverage, entry, take-profit targets, stop);
  • the trade closes on the desk (stop, target or the time exit) → a «close» message as a reply to the open message,
    so Cornix and the app close it the same way the desk did (our panic / flush buybacks exit by TIME after 24 h — Cornix has
    no time exit, the reply does it).
Messages are always written to web/signals_feed.json (the panel and the owner can read them). They are POSTED to Telegram only
when the secret CNQ_SIGNALS_CHAT is set (a separate signals channel where the bot is an admin), never to the news channel.

On the first run the trades already open are recorded as «seeded» and NOT posted — a signal that is hours old must not reach
users as new. Stdlib only."""
from __future__ import annotations

import json
import os
import sqlite3
import time

PROFILE = "bal"
LEV = {"B": 5, "E": 3, "F": 3, "D": 3}          # isolated leverage per rule: wide catastrophe stops (−6…−12 %) → 3×, cascade (tight stop) → 5×
RULE_RU = {"B": "откуп каскада ликвидаций", "E": "откуп паники", "F": "откуп сброса позиций", "D": "откуп дисконта фьючерса",
           "A": "откуп гэпа"}
TIME_EXIT = {"E", "F", "D"}


def _fmt(p: float) -> str:
    if p >= 1000:
        return f"{p:.1f}"
    if p >= 1:
        return f"{p:.4g}" if p < 10 else f"{p:.2f}"
    return f"{p:.6g}"


def targets(t: dict) -> list[float]:
    """Take-profit targets for the message. Cascade (B): the desk's own targets. Time-exit rules: +1R/+2R/+3R from the stop
    distance — they are rarely reached inside 24 h; the close message after 24 h is the real exit (as on the desk)."""
    e, s = float(t["entry"]), float(t["stop"])
    r = abs(e - s)
    sg = 1 if t["side"] == "LONG" else -1
    if t.get("grade") in TIME_EXIT or not t.get("tps"):
        return [e + sg * k * r for k in (1, 2, 3)]
    tps = sorted({float(x) for x in json.loads(t["tps"]) if x} if isinstance(t["tps"], str) else {float(x) for x in t["tps"] if x})
    return tps if sg > 0 else sorted(tps, reverse=True)


def open_text(t: dict) -> str:
    sym = t["symbol"].replace("-USDT", "").replace("USDT", "")
    g = t.get("grade", "")
    lines = [f"⚡ CRYPTO NOVA · {RULE_RU.get(g, 'сигнал')}", f"#{sym}/USDT", "Long" if t["side"] == "LONG" else "Short",
             f"Leverage: Isolated {LEV.get(g, 3)}x", "Entry Targets:", f"1) {_fmt(float(t['entry']))}", "Take-Profit Targets:"]
    lines += [f"{i}) {_fmt(p)}" for i, p in enumerate(targets(t), 1)]
    lines += ["Stop Targets:", f"1) {_fmt(float(t['stop']))}"]
    if g in TIME_EXIT:
        lines.append("Выход по времени через 24 ч — пришлём «Close».")
    return "\n".join(lines)


def close_text(t: dict) -> str:
    sym = t["symbol"].replace("-USDT", "").replace("USDT", "")
    pct = t.get("pnl_pct")
    why = {"STOP": "стоп", "TARGET": "цель", "TIME": "время вышло", "BREAKEVEN": "безубыток", "TRAIL": "трейлинг"}.get(t.get("exit_kind") or "", t.get("exit_kind") or "")
    return f"Close #{sym}/USDT" + (f" — {why}" if why else "") + (f", {pct:+.2f}%" if pct is not None else "")


def _pct(t: dict) -> float | None:
    """Price move of the trade in % (side-adjusted), from the desk's realised P&L and size."""
    try:
        q, e = float(t.get("qty") or 0), float(t["entry"])
        return round(float(t.get("pnl_usd") or 0) / (q * e) * 100, 2) if q and e else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _send(text: str, reply_to: int | None = None) -> int | None:
    chat, token = os.environ.get("CNQ_SIGNALS_CHAT"), os.environ.get("CNQ_TG_TOKEN")
    if not chat or not token:
        return None
    import urllib.request
    body = {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}
    if reply_to:
        body["reply_to_message_id"] = reply_to
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode())
    return (d.get("result") or {}).get("message_id") if d.get("ok") else None


def ensure(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS signal_feed(id TEXT PRIMARY KEY, symbol TEXT, side TEXT, grade TEXT, entry REAL, stop REAL,
                    opened INTEGER, open_text TEXT, msg_id INTEGER, seeded INTEGER, closed INTEGER, exit_kind TEXT, pnl_pct REAL,
                    close_text TEXT, close_msg_id INTEGER)""")
    conn.commit()


def step(conn: sqlite3.Connection, desk: dict, now: int | None = None, send=_send) -> dict:
    """desk = paper.json profiles[PROFILE]. Returns {'opened': [...], 'closed': [...]} for this run."""
    now = now or int(time.time() * 1000)
    ensure(conn)
    known = {r[0]: r for r in conn.execute("SELECT id, msg_id, closed FROM signal_feed")}
    first = not known and not conn.execute("SELECT 1 FROM signal_feed LIMIT 1").fetchone()
    out = {"opened": [], "closed": []}
    for t in desk.get("open") or []:
        if t["id"] in known:
            continue
        txt = open_text(t)
        seeded = 1 if first else 0
        mid = None
        if not seeded:
            try:
                mid = send(txt)
            except Exception as e:  # noqa: BLE001
                print("signal feed send:", e)
        conn.execute("INSERT INTO signal_feed VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (t["id"], t["symbol"], t["side"], t.get("grade"), t["entry"], t["stop"], t.get("opened") or now, txt, mid, seeded,
                      None, None, None, None, None))
        out["opened"].append(t["id"])
    for t in desk.get("closed") or []:
        row = known.get(t["id"])
        if not row or row[2]:
            continue
        t = dict(t, pnl_pct=_pct(t))
        txt = close_text(t)
        cmid = None
        if row[1]:
            try:
                cmid = send(txt, reply_to=row[1])
            except Exception as e:  # noqa: BLE001
                print("signal feed close:", e)
        conn.execute("UPDATE signal_feed SET closed=?, exit_kind=?, pnl_pct=?, close_text=?, close_msg_id=? WHERE id=?",
                     (t.get("closed") or now, t.get("exit_kind"), t["pnl_pct"], txt, cmid, t["id"]))
        out["closed"].append(t["id"])
    conn.commit()
    return out


def public(conn: sqlite3.Connection, now: int | None = None) -> dict:
    now = now or int(time.time() * 1000)
    keys = ("id", "symbol", "side", "grade", "entry", "stop", "opened", "open_text", "msg_id", "seeded", "closed", "exit_kind", "pnl_pct", "close_text")
    rows = [dict(zip(keys, r)) for r in conn.execute(f"SELECT {', '.join(keys)} FROM signal_feed ORDER BY opened DESC LIMIT 200")]
    done = [r for r in rows if r["closed"] and r["pnl_pct"] is not None and not r["seeded"]]
    wins = sum(1 for r in done if r["pnl_pct"] > 0)
    for r in rows:
        r["rule_ru"] = RULE_RU.get(r["grade"] or "", r["grade"])
        r["posted"] = bool(r.pop("msg_id"))
    return {"asof": now, "profile": PROFILE, "channel_on": bool(os.environ.get("CNQ_SIGNALS_CHAT")),
            "stats": {"signals": sum(1 for r in rows if not r["seeded"]), "closed": len(done), "wins": wins,
                      "avg_pct": round(sum(r["pnl_pct"] for r in done) / len(done), 2) if done else None},
            "items": rows,
            "about_ru": ("Наши собственные сигналы: каждая сделка стратегии CNPTS «Сбалансированная» в формате Cornix (пара, сторона, "
                         "плечо, вход, цели, стоп). Сделки с выходом по времени закрываются сообщением «Close» через 24 ч, как на демо-счёте. "
                         "Канал для Cornix включается, когда задан CNQ_SIGNALS_CHAT; до этого сигналы только здесь.")}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None) -> dict:
    try:
        paper = json.load(open(os.path.join(out, "paper.json"), encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"error": f"paper.json: {e}"}
    desk = (paper.get("profiles") or {}).get(PROFILE) or {}
    res = step(conn, desk, now)
    doc = public(conn, now)
    doc["this_run"] = res
    with open(os.path.join(out, "signals_feed.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc
