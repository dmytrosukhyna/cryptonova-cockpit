"""Weekly review department (server side, Fridays).

Builds the Friday review from JSON only — numbers come from the candle store and the department tables, never from
the model. Output: web/weekly.json = {week, market, paper, spot, said_last_time, next_week, text, post, history}.
`history` keeps the last 12 reviews so the panel can show "что говорили в прошлый раз → что вышло".
Stdlib only.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3

WEEK_MS = 7 * 86400000
RU_DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _fmt(p: float) -> str:
    return f"{p:,.0f}".replace(",", " ") if p >= 1000 else (f"{p:.2f}" if p >= 1 else f"{p:.4f}")


def _pct(x: float) -> str:
    return f"{x*100:+.1f}%"


def _d(ms: int) -> str:
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%d.%m")


def market_block(rep: dict, now_ms: int) -> dict:
    coins = rep.get("coins", [])
    rows = []
    for c in coins:
        cs = c.get("candles") or []
        if len(cs) < 96 * 7 + 1:
            continue
        # weekly return from the first candle ≥ 7 days ago
        past = [k for k in cs if k[0] <= now_ms - WEEK_MS]
        ref = past[-1][4] if past else cs[0][4]
        lo = min(k[3] for k in cs[-96 * 7:]); hi = max(k[2] for k in cs[-96 * 7:])
        rows.append({"symbol": c["symbol"].replace("-USDT", ""), "price": c["price"], "ret7": c["price"] / ref - 1,
                     "range_pct": (hi - lo) / c["price"], "above200": (c.get("regime") or {}).get("long") == "BULL", "mid": (c.get("regime") or {}).get("mid")})
    rows.sort(key=lambda r: r["ret7"], reverse=True)
    # stress days: share of coins down >3 % per calendar day
    stress_days = []
    if coins:
        base = coins[0].get("candles") or []
        days = sorted({dt.datetime.utcfromtimestamp(k[0] / 1000).date() for k in base[-96 * 7:]})
        for day in days:
            downs = 0; tot = 0
            for c in coins:
                cs = [k for k in (c.get("candles") or []) if dt.datetime.utcfromtimestamp(k[0] / 1000).date() == day]
                if len(cs) < 8:
                    continue
                tot += 1
                if cs[-1][4] / cs[0][1] - 1 < -0.03:
                    downs += 1
            if tot and downs / tot >= 0.7:
                stress_days.append(day.strftime("%d.%m"))
    btc = next((r for r in rows if r["symbol"] == "BTC"), None)
    up = sum(1 for r in rows if r["ret7"] > 0)
    return {"rows": rows, "btc": btc, "up": up, "total": len(rows), "stress_days": stress_days,
            "best": rows[:3], "worst": rows[-3:][::-1] if rows else []}


def paper_block(conn: sqlite3.Connection, now_ms: int) -> dict:
    since = now_ms - WEEK_MS
    closed = conn.execute("select symbol, side, grade, kind, entry, stop, exit_kind, pnl_r, pnl_usd, opened, closed, reason from paper_trades "
                          "where closed is not null and closed >= ? order by closed", (since,)).fetchall()
    openp = conn.execute("select symbol, side, grade, entry, stop_now, opened from paper_trades where closed is null").fetchall()
    eq = conn.execute("select time, equity from paper_equity order by time").fetchall()
    eq_start = next((e for t, e in eq if t >= since), eq[0][1] if eq else None)
    eq_end = eq[-1][1] if eq else None
    by = {}
    for r in closed:
        g = by.setdefault(r[2], {"n": 0, "wins": 0, "r": 0.0, "usd": 0.0})
        g["n"] += 1; g["wins"] += 1 if r[7] > 0 else 0; g["r"] += r[7]; g["usd"] += r[8]
    trades = [{"symbol": r[0], "side": r[1], "grade": r[2], "entry": r[4], "stop": r[5], "exit": r[6], "r": round(r[7], 2), "usd": round(r[8], 2),
               "opened": r[9], "closed": r[10], "reason": r[11]} for r in closed]
    return {"trades": trades, "n": len(closed), "sum_r": round(sum(r[7] for r in closed), 2), "usd": round(sum(r[8] for r in closed), 2),
            "wins": sum(1 for r in closed if r[7] > 0), "by_grade": by, "open": [{"symbol": o[0], "side": o[1], "grade": o[2], "entry": o[3], "stop": o[4]} for o in openp],
            "equity_start": eq_start, "equity_end": eq_end,
            }


def _has_col(conn, table, col) -> bool:
    try:
        return col in [d[1] for d in conn.execute(f"pragma table_info({table})")]
    except Exception:
        return False


def spot_block(conn: sqlite3.Connection, now_ms: int) -> dict:
    since = now_ms - WEEK_MS
    vals = conn.execute("select time, index_value from spot_value order by time").fetchall()
    v0 = next((v for t, v in vals if t >= since), vals[0][1] if vals else None)
    v1 = vals[-1][1] if vals else None
    acts = conn.execute("select time, symbol, action, weight, price, reason from spot_actions where time >= ? order by time", (since,)).fetchall()
    pos = conn.execute("select symbol, weight, entry, bucket from spot_portfolio where weight > 0 and status='OPEN' order by weight desc").fetchall() if _has_col(conn, "spot_portfolio", "weight") else []
    return {"value_start": v0, "value_end": v1, "ret": (v1 / v0 - 1) if v0 and v1 else None,
            "actions": [{"time": a[0], "symbol": a[1], "action": a[2], "weight": a[3], "price": a[4], "reason": a[5]} for a in acts],
            "positions": [{"symbol": p[0], "weight": p[1], "entry": p[2], "horizon": p[3]} for p in pos]}


def next_week(rep: dict) -> list:
    out = []
    for c in rep.get("coins", [])[:6]:
        p = c["price"]
        sup = [l for l in c.get("levels", []) if l["kind"] == "support" and l["status"] == "ACTIVE" and l["lo"] <= p]
        res = [l for l in c.get("levels", []) if l["kind"] == "resistance" and l["status"] == "ACTIVE" and l["hi"] >= p]
        sup.sort(key=lambda l: -l["center"]); res.sort(key=lambda l: l["center"])
        out.append({"symbol": c["symbol"].replace("-USDT", ""), "price": c["price"],
                    "support": {"lo": sup[0]["lo"], "hi": sup[0]["hi"], "touches": sup[0]["touches"]} if sup else None,
                    "resistance": {"lo": res[0]["lo"], "hi": res[0]["hi"], "touches": res[0]["touches"]} if res else None,
                    "bull": (c.get("regime") or {}).get("long") == "BULL", "mid": (c.get("regime") or {}).get("mid"), "mid_note": (c.get("regime") or {}).get("mid_note")})
    return out


def verify_last(prev: dict | None, market: dict) -> dict | None:
    """What we said last Friday vs what happened: compare last review's next_week levels to this week's moves."""
    if not prev:
        return None
    rows = {r["symbol"]: r for r in market["rows"]}
    checks = []
    for n in prev.get("next_week", []):
        r = rows.get(n["symbol"])
        if not r:
            continue
        said = []
        if n.get("support"):
            held = r["price"] >= n["support"]["lo"]
            said.append(f"поддержка {_fmt(n['support']['lo'])}–{_fmt(n['support']['hi'])} {'удержала' if held else 'пробита'}")
        if n.get("resistance"):
            broke = r["price"] > n["resistance"]["hi"]
            said.append(f"сопротивление {_fmt(n['resistance']['lo'])}–{_fmt(n['resistance']['hi'])} {'пробито' if broke else 'держит'}")
        checks.append({"symbol": n["symbol"], "then": n["price"], "now": r["price"], "ret": r["price"] / n["price"] - 1, "said": said})
    return {"week": prev.get("week"), "checks": checks, "paper_said": prev.get("paper", {}).get("sum_r"), "btc_then": (prev.get("market") or {}).get("btc")}


def compose(week: dict, market: dict, paper: dict, spot: dict, nxt: list, last: dict | None) -> tuple[str, str]:
    L = []
    L.append(f"# CRYPTO NOVΛ — итоги недели {week['from']}–{week['to']}")
    L.append("")
    L.append("## Рынок")
    if market["btc"]:
        L.append(f"- BTC за неделю {_pct(market['btc']['ret7'])}, размах {market['btc']['range_pct']*100:.1f}%. "
                 f"В плюсе {market['up']} из {market['total']} монет.")
    if market["best"]:
        L.append("- Лучшие: " + ", ".join(f"{r['symbol']} {_pct(r['ret7'])}" for r in market["best"]) +
                 "; худшие: " + ", ".join(f"{r['symbol']} {_pct(r['ret7'])}" for r in market["worst"]) + ".")
    if market["stress_days"]:
        L.append(f"- Дни широкого падения (≥70% монет −3%): {', '.join(market['stress_days'])}. В такие дни система лонги не открывала — "
                 f"на истории покупка дна со стопом даёт −0.20R, шорт вдогонку −0.04R.")
    else:
        L.append("- Дней широкого падения не было.")
    L.append("")
    L.append("## Демо-счёт фьючерсы ($10 000, сигналы системы)")
    if paper["n"]:
        L.append(f"- Закрыто сделок: {paper['n']}, в плюс {paper['wins']}, итог {paper['sum_r']:+.2f}R ({paper['usd']:+.0f} $). "
                 f"Счёт {_fmt(paper['equity_start'])} → {_fmt(paper['equity_end'])} $." if paper["equity_start"] and paper["equity_end"] else
                 f"- Закрыто сделок: {paper['n']}, итог {paper['sum_r']:+.2f}R.")
        for t in paper["trades"]:
            L.append(f"  - {t['symbol']} {'лонг' if t['side']=='LONG' else 'шорт'} ({t['grade']}) {_d(t['opened'])}→{_d(t['closed'])}: "
                     f"{'стоп' if t['exit']=='STOP' else 'цель' if str(t['exit']).startswith('TP') else t['exit']} {t['r']:+.2f}R")
    else:
        L.append("- Сделок не было — система не нашла входа от зоны. Это нормально: сделка только у стенки.")
    if paper["open"]:
        L.append("- Открыто сейчас: " + ", ".join(f"{o['symbol']} {'лонг' if o['side']=='LONG' else 'шорт'} от {_fmt(o['entry'])}" for o in paper["open"]) + ".")
    L.append("")
    L.append("## Спотовый портфель ($10 000)")
    if spot["ret"] is not None:
        L.append(f"- Стоимость {_pct(spot['ret'])} за неделю ({_fmt(spot['value_start'])} → {_fmt(spot['value_end'])}).")
    if spot["actions"]:
        L.append("- Действия: " + "; ".join(f"{a['action']} {a['symbol']}" for a in spot["actions"][:8]) + ".")
    else:
        L.append("- Изменений в портфеле не было.")
    if spot["positions"]:
        L.append("- Состав: " + ", ".join(f"{p['symbol']} {p['weight']*100:.0f}%" for p in spot["positions"][:8]) + ".")
    L.append("")
    if last and last["checks"]:
        L.append(f"## Что говорили в прошлый раз ({last['week']}) → что вышло")
        for c in last["checks"]:
            L.append(f"- {c['symbol']}: {_fmt(c['then'])} → {_fmt(c['now'])} ({_pct(c['ret'])}); " + "; ".join(c["said"]) + ".")
        L.append("")
    L.append("## На следующую неделю")
    for n in nxt:
        parts = []
        if n["support"]:
            parts.append(f"поддержка {_fmt(n['support']['lo'])}–{_fmt(n['support']['hi'])} ({n['support']['touches']}× касаний) — там ждём покупателей")
        if n["resistance"]:
            parts.append(f"сопротивление {_fmt(n['resistance']['lo'])}–{_fmt(n['resistance']['hi'])} ({n['resistance']['touches']}× касаний) — там продавцы")
        mode = ("Выше средней 200 дней — лонг от поддержки" if n["bull"] else "Ниже средней 200 дней — шорт от сопротивления") + (f"; {n['mid_note']}" if n.get("mid_note") else "")
        L.append(f"- {n['symbol']} {_fmt(n['price'])}: " + "; ".join(parts) + f". {mode}.")
    L.append("")
    L.append("Правило недели: вход только от стенки с ретестом, стоп за зоной, риск 1% на сделку. Падение вдогонку не торгуем.")
    text = "\n".join(L)
    # telegram post (short)
    P = [f"📊 CRYPTO NOVΛ — неделя {week['from']}–{week['to']}"]
    if market["btc"]:
        P.append(f"BTC {_pct(market['btc']['ret7'])}, в плюсе {market['up']}/{market['total']} монет" + (f", дни паники: {', '.join(market['stress_days'])}" if market["stress_days"] else ""))
    P.append(f"Демо-фьючерсы: {paper['n']} сделок, {paper['sum_r']:+.2f}R" + (f", счёт {_fmt(paper['equity_end'])} $" if paper["equity_end"] else ""))
    if spot["ret"] is not None:
        P.append(f"Спот-портфель: {_pct(spot['ret'])} за неделю")
    P.append("На неделю:")
    for n in nxt[:4]:
        if n["support"] and n["resistance"]:
            P.append(f"• {n['symbol']} {_fmt(n['price'])}: {_fmt(n['support']['lo'])} ⇄ {_fmt(n['resistance']['hi'])}")
    P.append("Вход только от стенки. Падение вдогонку не торгуем.")
    return text, "\n".join(P)


def should_run(out_path: str, now_ms: int | None = None) -> str | None:
    """'first' when there is no review yet (so the panel is never empty), 'friday' on Fridays from 17:00 UTC (once per Friday), else None."""
    now_ms = now_ms or int(dt.datetime.utcnow().timestamp() * 1000)
    if not os.path.exists(out_path):
        return "first"
    try:
        gen = json.load(open(out_path, encoding="utf-8")).get("generated", 0)
    except Exception:
        return "first"
    now = dt.datetime.utcfromtimestamp(now_ms / 1000)
    if now.weekday() == 4 and now.hour >= 17 and dt.datetime.utcfromtimestamp(gen / 1000).date() != now.date():
        return "friday"
    return None


def run(conn: sqlite3.Connection, rep: dict, out_path: str, now_ms: int | None = None) -> dict:
    now_ms = now_ms or int(dt.datetime.utcnow().timestamp() * 1000)
    prev_doc = None
    if os.path.exists(out_path):
        try:
            prev_doc = json.load(open(out_path, encoding="utf-8"))
        except Exception:
            prev_doc = None
    week = {"from": _d(now_ms - WEEK_MS), "to": _d(now_ms), "end_ms": now_ms}
    market = market_block(rep, now_ms)
    paper = paper_block(conn, now_ms)
    spot = spot_block(conn, now_ms)
    nxt = next_week(rep)
    last = verify_last(prev_doc, market) if prev_doc and prev_doc.get("week", {}).get("end_ms", 0) < now_ms - 3 * 86400000 else (prev_doc.get("said_last_time") if prev_doc else None)
    text, post = compose(week, market, paper, spot, nxt, last)
    hist = (prev_doc or {}).get("history", [])
    if prev_doc and prev_doc.get("week") and prev_doc["week"].get("end_ms", 0) < now_ms - 3 * 86400000:
        hist = ([{"week": prev_doc["week"], "btc": (prev_doc.get("market") or {}).get("btc"), "paper_sum_r": (prev_doc.get("paper") or {}).get("sum_r"),
                  "spot_ret": (prev_doc.get("spot") or {}).get("ret"), "next_week": prev_doc.get("next_week")}] + hist)[:12]
    doc = {"generated": now_ms, "week": week, "market": {k: v for k, v in market.items() if k != "rows"} | {"rows": market["rows"]},
           "paper": paper, "spot": spot, "next_week": nxt, "said_last_time": last, "text": text, "post": post, "history": hist}
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    json.dump(doc, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    return doc
