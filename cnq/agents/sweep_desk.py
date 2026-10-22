"""«Ложный пробой · лонг» (key `swp`, 22.10.2026) — a PAPER-ONLY test desk on the conveyor (docs/research/sweep.md).

Owner's question 22.10: when BTC comes to its 20-day low — a real breakdown (short) or a stop hunt that reverses (long)?
scripts/sweep_study.py (run 39782222341, Binance daily 2017→2026, BTC + 10 alts, 0.1 % a side, 7-day hold): in «рост» a long after
a false breakdown across the 11 coins made +1.21 % a trade (n 217, 43 % wins, 6 of 7 years in plus, t by weeks 1.82 — borderline);
BTC alone +0.08 % (nothing); shorting a true breakdown in «рост» −3.76 % a trade. So: a demo account only, the conveyor judges it.

Rule (UTC daily bars built from the engine's 15m candle store, the study's definitions):
  level L = the lowest low of the previous 20 days, fixed on the break day (the first day whose low or close is under L);
  sweep  = the low goes ≥ 0.5 % under L and, within 3 closes counting the break day, a daily close back above L;
  regime = «рост» at the reclaim: BTC above its 200-day average and up over 60 days (turbo_desk.btc_state — the same definition);
  entry  = the next day's open (00:00 UTC); a run later than ENTRY_GRACE_MS into that day enters at the latest fresh 15m close;
  stop   = the sweep's lowest low − 0.2 %; exit = the daily close 7 days after the entry day's open, or the stop (stop first inside
           a 15m bar; a gap through the stop fills at the bar's open);
  size   = 1 % of the closed equity at risk, a position never above 1/3 of the account (no leverage); at most 3 positions;
           coins BTC, ETH, SOL, XRP, BNB, DOGE, ADA, TRX, LINK, AVAX, LTC — this order is the priority when slots are short;
  fees   = 0.1 % a side (as in the study); no funding (no leverage — spot-equivalent);
  one episode per coin, 5 days of pause after a resolved one (as the study).

PAPER ONLY: $10 000 of demo dollars in data/agents.sqlite (swp_trades / swp_equity / swp_seen). Nothing here imports or calls
cnq/execution; the real-money executors follow fixed desk keys (okx_futures: bal + imp (+ own after its gate), okx_active: imps,
mirror: CNQ_MIRROR_PROFILE = bal) and never read `swp`. Every entry still asks the risk layer (kill switch, conveyor pause, daily
and drawdown stops). Run by `python -m cnq.cli turbo` right after the turbo desks (engine FAST step). Stdlib only."""
from __future__ import annotations

import json
import os
import sqlite3
import time

from ..risk import gate as risk_gate
from . import turbo_desk
from .history import thin

KEY = "swp"
NAME = "Ложный пробой · лонг"
GRADE = "SWP"
COINS = ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "TRX", "LINK", "AVAX", "LTC")
START = 10_000.0
DAY = 86_400_000
M15 = 900_000
LOOKBACK_D = 20          # the level: lowest low of the previous 20 days
BREAK = 0.005            # the sweep pierces the level by ≥ 0.5 %
WIN = 3                  # … and closes back above it within 3 closes (the break day counts)
BD_N = 3                 # 3 closes below = a true breakdown (no trade: shorting it in «рост» lost −3.76 % a trade)
COOL_D = 5               # days of pause after a resolved episode
STOP_PAD = 0.002         # stop = sweep low − 0.2 %
HOLD_D = 7
RISK = 0.01
MAX_OPEN = 3
MAX_SHARE = 1 / 3        # one position ≤ 1/3 of the account → 3 positions ≤ the account: no leverage
FEE = 0.001
LOAD_D = 75              # days of 15m bars read per coin: 20-day lookback + episodes + pauses
MIN_BARS_DAY = 48        # a UTC day with fewer stored 15m bars is skipped (store gap)
ENTRY_GRACE_MS = 60 * 60_000
FRESH_MS = 30 * 60_000
EQ_EVERY_MS = M15        # one equity point per 15 minutes

RULES_RU = ("только в режиме «рост» (BTC выше 200-дневной средней и за 60 дней в плюсе); монеты: BTC, ETH, SOL, XRP, BNB, DOGE, ADA, "
            "TRX, LINK, AVAX, LTC; вход на открытии следующего дня, когда дневная свеча закрылась снова выше минимума прошлых 20 дней "
            "не позже 3-го закрытия после пробоя на 0.5% и больше; стоп — минимум пробоя −0.2%; выход через 7 дней или по стопу; "
            "риск 1% счёта, позиция не больше трети счёта, до 3 позиций; комиссия 0.1% на сторону; только демо, реальные деньги не торгует")
ABOUT_RU = ("Покупаем монету, когда цена проткнула минимум 20 дней и быстро вернулась выше — ловушка для продавцов. Только в режиме "
            "«рост». История 2019–2026 (BTC и 10 крупных альтов, дневные свечи): +1.2% на сделку, 43% сделок в плюсе, 6 лет из 7 в плюсе, "
            "но это на грани случайности; на одном BTC — ноль. Альты выбраны по сегодняшней ликвидности — история приукрашена. "
            "Только демо $10 000, реальные деньги не торгует.")
OUTCOME_RU = {"opened": "открыт", "regime": "пропущен — режим не «рост»", "slots": "пропущен — заняты 3 места",
              "risk": "пропущен — риск-слой", "missed": "пропущен — окно входа прошло", "gap": "пропущен — открытие ниже стопа",
              "stale": "пропущен — нет свежей цены"}


# ─────────────────────────────────────────────────────────────── data ───────────────────────────────────────────────────────────────
def load_15m(candles_db: str | None, coin: str, now: int, days: int = LOAD_D) -> list[tuple]:
    """[(open_time, o, h, l, c)] of `coin`-USDT 15m bars from the engine's store, oldest first ([] when the store is missing)."""
    if not candles_db or not os.path.exists(candles_db):
        return []
    try:
        c = sqlite3.connect(f"file:{candles_db}?mode=ro", uri=True, timeout=5)
        try:
            return [tuple(r) for r in c.execute(
                "SELECT open_time, open, high, low, close FROM candles WHERE symbol=? AND interval='15m' AND open_time>=? AND open_time<=? "
                "ORDER BY open_time", (f"{coin}-USDT", now - days * DAY, now))]
        finally:
            c.close()
    except sqlite3.Error:
        return []


def daily_bars(rows: list, now: int) -> list[list]:
    """15m rows → complete UTC daily bars [t, o, h, l, c] (a day is complete once its 24 h are over; < MIN_BARS_DAY bars → skipped)."""
    out, cur = [], None
    for t, o, h, l, c in rows:
        d = int(t) - int(t) % DAY
        if cur is None or cur[0] != d:
            if cur:
                out.append(cur)
            cur = [d, float(o), float(h), float(l), float(c), 1]
        else:
            cur[2] = max(cur[2], float(h))
            cur[3] = min(cur[3], float(l))
            cur[4] = float(c)
            cur[5] += 1
    if cur:
        out.append(cur)
    return [b[:5] for b in out if b[0] + DAY <= now and b[5] >= MIN_BARS_DAY]


def episodes(b: list[list]) -> list[dict]:
    """scripts/sweep_study.events() on daily bars (W = 3, breakdown = 3 closes), live variant: a sweep confirmed on the LAST closed
    day counts (its entry is the running day), and an unresolved break at the end is returned as kind «open»."""
    n, ev, i = len(b), [], LOOKBACK_D
    while i < n:
        L = min(x[3] for x in b[i - LOOKBACK_D:i])
        if not (b[i][3] < L or b[i][4] < L):
            i += 1
            continue
        low, res = b[i][3], None
        for j in range(i, min(n, i + max(WIN, BD_N))):
            low = min(low, b[j][3])
            if b[j][4] > L and j - i < WIN and low <= L * (1 - BREAK):
                res = ("sweep", j)
                break
            if b[j][4] > L:                       # back above before a valid sweep / breakdown → neither
                res = ("none", j)
                break
            if j - i + 1 >= BD_N and all(x[4] < L for x in b[i:j + 1]):
                res = ("breakdown", j)
                break
        if res is None:
            if i + max(WIN, BD_N) >= n:           # still unresolved on the last closed day
                ev.append({"kind": "open", "i": i, "j": n - 1, "level": L, "low": min(x[3] for x in b[i:n]), "t_break": b[i][0]})
                break
            res = ("none", i)
        kind, j = res
        ev.append({"kind": kind, "i": i, "j": j, "level": L, "low": min(x[3] for x in b[i:j + 1]), "t_break": b[i][0], "t_conf": b[j][0]})
        i = j + 1 + (COOL_D if kind != "none" else 0)
    return ev


# ─────────────────────────────────────────────────────────────── storage ───────────────────────────────────────────────────────────────
def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS swp_trades(id TEXT PRIMARY KEY, symbol TEXT, side TEXT, opened INTEGER, entry REAL, stop REAL, qty REAL,
        risk_usd REAL, expires INTEGER, level REAL, t_conf INTEGER, checked INTEGER, closed INTEGER, exit REAL, exit_kind TEXT,
        pnl_usd REAL, reason TEXT);
      CREATE TABLE IF NOT EXISTS swp_equity(time INTEGER PRIMARY KEY, equity REAL, mtm REAL);
      CREATE TABLE IF NOT EXISTS swp_seen(id TEXT PRIMARY KEY, t INTEGER, symbol TEXT, outcome TEXT, note TEXT);
      CREATE TABLE IF NOT EXISTS swp_meta(k TEXT PRIMARY KEY, v TEXT);""")


def _meta(conn, k, default=None):
    r = conn.execute("SELECT v FROM swp_meta WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def _set_meta(conn, k, v) -> None:
    conn.execute("INSERT OR REPLACE INTO swp_meta VALUES(?, ?)", (k, json.dumps(v)))


T_COLS = ("id", "symbol", "side", "opened", "entry", "stop", "qty", "risk_usd", "expires", "level", "t_conf", "checked", "closed", "exit",
          "exit_kind", "pnl_usd", "reason")


def _trades(conn, open_: bool) -> list[dict]:
    q = "closed IS NULL ORDER BY opened" if open_ else "closed IS NOT NULL ORDER BY closed DESC"
    return [dict(zip(T_COLS, r)) for r in conn.execute(f"SELECT {', '.join(T_COLS)} FROM swp_trades WHERE {q}")]


def equity(conn) -> float:
    return START + float(conn.execute("SELECT COALESCE(SUM(pnl_usd), 0) FROM swp_trades WHERE closed IS NOT NULL").fetchone()[0])


def net_pnl(entry: float, exit_: float, qty: float) -> float:
    return (exit_ - entry) * qty - FEE * (entry + exit_) * qty


# ─────────────────────────────────────────────────────────────── one step ───────────────────────────────────────────────────────────────
def manage(conn, t: dict, rows: list, now: int) -> str | None:
    """walk the closed 15m bars since the last check: stop first, then the 7-day time exit → an event line when the trade closed"""
    for bt, o, h, l, c in rows:
        if bt < t["opened"] or (t["checked"] is not None and bt <= t["checked"]):
            continue
        if bt + M15 > now:
            break                                  # the running bar: next time
        ex, kind = None, None
        if l <= t["stop"]:
            ex, kind = min(float(o), t["stop"]), "stop"
        elif bt + M15 >= t["expires"]:
            ex, kind = float(c), "time"
        if ex is not None:
            pnl = net_pnl(t["entry"], ex, t["qty"])
            conn.execute("UPDATE swp_trades SET closed=?, exit=?, exit_kind=?, pnl_usd=?, checked=? WHERE id=?",
                         (bt + M15, ex, kind, round(pnl, 4), bt, t["id"]))
            return (f"{t['symbol']}: {'стоп' if kind == 'stop' else '7 дней прошли'} — выход {ex:.6g}, "
                    f"{'+' if pnl >= 0 else '−'}${abs(pnl):.2f}")
        conn.execute("UPDATE swp_trades SET checked=? WHERE id=?", (bt, t["id"]))
    return None


def _seen(conn, sid: str, now: int, sym: str, outcome: str, note: str = "") -> None:
    conn.execute("INSERT OR IGNORE INTO swp_seen VALUES(?,?,?,?,?)", (sid, now, sym, outcome, note))


def step(conn: sqlite3.Connection, out: str = "web", now: int | None = None, candles_db: str | None = None) -> dict:
    now = now or int(time.time() * 1000)
    ensure(conn)
    since = _meta(conn, "since")
    if since is None:
        since = now
        _set_meta(conn, "since", since)
    _, bull = turbo_desk.btc_state(out, candles_db)
    events: list[str] = []
    rows = {c: load_15m(candles_db, c, now) for c in COINS}
    # 1. open trades: stop / time exit on closed 15m bars
    for t in _trades(conn, True):
        e = manage(conn, t, rows.get(t["symbol"]) or [], now)
        if e:
            events.append(e)
    # 2. new sweeps confirmed on the last closed day → entry at the running day's open
    watch_near, pending = [], []
    for coin in COINS:
        r = rows.get(coin) or []
        b = daily_bars(r, now)
        if len(b) < LOOKBACK_D + 1:
            continue
        ev = episodes(b)
        last_close = r[-1][4] if r else b[-1][4]
        lvl_now = min(x[3] for x in b[-LOOKBACK_D:])                  # the level today's bar is measured against
        watch_near.append({"symbol": coin, "level": lvl_now, "dist_pct": round((lvl_now / float(last_close) - 1) * 100, 2)})
        e = ev[-1] if ev else None
        if e and e["kind"] == "open":
            pending.append({"symbol": coin, "level": round(e["level"], 8), "low": round(e["low"], 8),
                            "pierced_pct": round((e["low"] / e["level"] - 1) * 100, 2), "deadline": e["t_break"] + WIN * DAY})
        if not e or e["kind"] != "sweep":
            continue
        sid = f"SWP-{coin}-{e['t_conf']}"
        if conn.execute("SELECT 1 FROM swp_seen WHERE id=?", (sid,)).fetchone():
            continue
        day0 = e["t_conf"] + DAY                                           # the entry day
        stop = e["low"] * (1 - STOP_PAD)
        if b[-1][0] != e["t_conf"]:                                        # the entry day is already over
            if since < day0 + DAY:                                         # … and the desk was running then → a missed window
                _seen(conn, sid, now, coin, "missed")
            continue                                                       # older than the desk itself → not ours to count
        if bull is not True:
            _seen(conn, sid, now, coin, "regime", "режим BTC неизвестен" if bull is None else "")
            continue
        if any(t["symbol"] == coin for t in _trades(conn, True)):
            continue                                                       # one position per coin (the next episode needs a pause anyway)
        if len(_trades(conn, True)) >= MAX_OPEN:
            _seen(conn, sid, now, coin, "slots")
            continue
        first = next((x for x in r if x[0] == day0), None)
        if now - day0 <= ENTRY_GRACE_MS:
            if not first:
                continue                                                   # the store has not got the day's first bar yet — next minute
            entry, t_open, how = float(first[1]), day0, "на открытии дня"
        else:
            fresh = r[-1] if r and now - r[-1][0] <= FRESH_MS else None
            if not fresh:
                _seen(conn, sid, now, coin, "stale")
                continue
            entry, t_open, how = float(fresh[4]), int(fresh[0]) + M15, "по последней цене (запуск позже открытия дня)"
        if entry <= stop:
            _seen(conn, sid, now, coin, "gap", f"вход {entry:.6g} ≤ стоп {stop:.6g}")
            continue
        eq = equity(conn)
        qty = min(RISK * eq / (entry - stop), MAX_SHARE * eq / entry)
        ok, why = risk_gate.allow_entry(KEY, coin, "LONG", qty * entry, now)
        if not ok:
            _seen(conn, sid, now, coin, "risk", why)
            risk_gate.record_skip(conn, KEY, sid, coin, "LONG", qty * entry, why, now)
            events.append(f"{coin}: ложный пробой есть, вход отложен — риск-слой: {why}")
            continue
        reason = (f"Ложный пробой: минимум 20 дней {e['level']:.6g} проткнут до {e['low']:.6g} ({(e['low'] / e['level'] - 1) * 100:.1f}%), "
                  f"дневное закрытие снова выше. Режим «рост». Вход {how}, стоп {stop:.6g}, выход через 7 дней.")
        conn.execute(f"INSERT INTO swp_trades({', '.join(T_COLS)}) VALUES({', '.join('?' * len(T_COLS))})",
                     (sid, coin, "LONG", t_open, entry, stop, qty, round(qty * (entry - stop), 4), day0 + HOLD_D * DAY, e["level"],
                      e["t_conf"], None, None, None, None, None, reason))
        _seen(conn, sid, now, coin, "opened")
        events.append(f"{coin}: покупка {entry:.6g} — ложный пробой минимума 20 дней, стоп {stop:.6g}")
        t = dict(zip(T_COLS, conn.execute(f"SELECT {', '.join(T_COLS)} FROM swp_trades WHERE id=?", (sid,)).fetchone()))
        e2 = manage(conn, t, r, now)                                       # a late entry: bars since the open may already hit the stop
        if e2:
            events.append(e2)
    # 3. equity and the document
    price = {c: float(rows[c][-1][4]) for c in COINS if rows.get(c)}
    opens = []
    for t in _trades(conn, True):
        px = price.get(t["symbol"], t["entry"])
        r_ = (t["entry"] - t["stop"]) or 1
        opens.append({**t, "grade": GRADE, "kind": "SWEEP_LONG", "qty_left": t["qty"], "stop_now": t["stop"], "tps": [], "parts": [],
                      "price": px, "pnl_usd": 0.0, "open_pnl_usd": round((px - t["entry"]) * t["qty"] - FEE * t["entry"] * t["qty"], 2),
                      "moved_r": round((px - t["entry"]) / r_, 2)})
    eq = equity(conn)
    mtm = eq + sum(o["open_pnl_usd"] for o in opens)
    conn.execute("INSERT OR REPLACE INTO swp_equity VALUES(?,?,?)", (now - now % EQ_EVERY_MS, round(eq, 2), round(mtm, 2)))
    conn.commit()
    closed = []
    for t in _trades(conn, False)[:50]:
        closed.append({**t, "grade": GRADE, "kind": "SWEEP_LONG", "net_usd": round(t["pnl_usd"], 2), "pnl_usd": round(t["pnl_usd"], 2),
                       "pnl_r": round(t["pnl_usd"] / t["risk_usd"], 2) if t["risk_usd"] else None,
                       "pct": round(t["pnl_usd"] / (t["entry"] * t["qty"]) * 100, 3) if t["entry"] and t["qty"] else None})
    n = conn.execute("SELECT COUNT(*) FROM swp_trades WHERE closed IS NOT NULL").fetchone()[0]
    agg = conn.execute("SELECT COALESCE(SUM(pnl_usd > 0), 0), COALESCE(MAX(pnl_usd), 0), COALESCE(MIN(pnl_usd), 0), "
                       "AVG(pnl_usd / (entry * qty) * 100) FROM swp_trades WHERE closed IS NOT NULL").fetchone()
    hist = [{"time": t_, "equity": m if m is not None else e_} for t_, e_, m in conn.execute("SELECT time, equity, mtm FROM swp_equity ORDER BY time")]
    peak, dd = START, 0.0
    for h in hist:
        peak = max(peak, h["equity"])
        dd = max(dd, (peak - h["equity"]) / peak if peak else 0.0)
    seen = {o: c for o, c in conn.execute("SELECT outcome, COUNT(*) FROM swp_seen GROUP BY outcome")}
    lr = conn.execute("SELECT id, t, symbol, outcome, note FROM swp_seen ORDER BY t DESC, id DESC LIMIT 1").fetchone()
    watch = {"regime_bull": bull, "since": since, "signals": sum(seen.values()), "opened": seen.get("opened", 0),
             "skipped": {k: v for k, v in seen.items() if k != "opened"},
             "nearest": sorted(watch_near, key=lambda x: -x["dist_pct"])[:3], "pending": pending,
             "last": {"symbol": lr[2], "t": lr[1], "t_conf": int(lr[0].rsplit("-", 1)[1]), "outcome": lr[3],
                      "outcome_ru": OUTCOME_RU.get(lr[3], lr[3]), "note": lr[4]} if lr else None}
    doc = {"asof": now, "profile": KEY, "name": NAME, "about": ABOUT_RU, "rules": RULES_RU, "paper_only": True, "start_equity": START,
           "since": since, "equity": round(eq, 2), "equity_mtm": round(mtm, 2), "pnl_usd": round(eq - START, 2),
           "pnl_pct": round((eq / START - 1) * 100, 2), "open_pnl_usd": round(mtm - eq, 2), "open": opens, "pending": [], "closed": closed,
           "stats": {"trades": n, "winrate": round(agg[0] / n, 3) if n else None, "best": round(agg[1], 2), "worst": round(agg[2], 2),
                     "avg_trade_pct": round(agg[3], 3) if agg[3] is not None else None, "max_dd_pct": round(dd * 100, 2)},
           "equity_history": thin(hist, now), "events": events, "error": None if rows.get("BTC") else "нет 15-минутных свечей BTC в хранилище",
           "watch": watch}
    doc["status_ru"] = status_ru(doc)
    return doc


# ─────────────────────────────────────────────────────────────── status ───────────────────────────────────────────────────────────────
def _ddmm(ms: int) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%d.%m")


def _px(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ") if x >= 1000 else f"{x:.4g}"


def status_ru(doc: dict) -> str:
    """one plain line: what the desk is doing or waiting for (the comparison table and the «Что будет делать система» block)"""
    w = doc.get("watch") or {}
    o = doc.get("open") or []
    if o:
        return "в работе · " + ", ".join(f"{x['symbol']} стоп {_px(x['stop'])} $, выход {_ddmm(x['expires'])}" for x in o)
    if doc.get("error"):
        return "нет данных: " + doc["error"]
    parts = []
    if w.get("regime_bull") is not True:
        parts.append("режим не «рост» — новых входов нет" if w.get("regime_bull") is False else "режим BTC неизвестен — новых входов нет")
    else:
        parts.append("ждёт ложный пробой минимума 20 дней")
    pe = w.get("pending") or []
    if pe:
        parts.append("; ".join(f"{p['symbol']} проткнул {_px(p['level'])} $ на {abs(p['pierced_pct']):.1f}% — нужен возврат выше до "
                               f"закрытия {_ddmm(p['deadline'] - DAY)}" for p in pe[:2]))
    pend = {p["symbol"] for p in pe}
    under = [x["symbol"] for x in w.get("nearest") or [] if x["dist_pct"] >= 0 and x["symbol"] not in pend]
    if under:
        parts.append(", ".join(under) + " сейчас под минимумом 20 дней — ждём, вернётся ли выше")
    nr = [x for x in w.get("nearest") or [] if x["dist_pct"] < 0][:1]
    if nr and not pe and not under:
        parts.append(f"ближе всего {nr[0]['symbol']}: до минимума {_px(nr[0]['level'])} $ {nr[0]['dist_pct']:+.1f}%".replace("-", "−"))
    la = w.get("last")
    if la:
        parts.append(f"последний сигнал {la['symbol']} {_ddmm(la['t_conf'])}: {la['outcome_ru']}")
    return " · ".join(parts)


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, candles_db: str | None = None) -> dict:
    doc = step(conn, out, now, candles_db)
    p = os.path.join(out, "paper.json")
    try:
        paper = json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return doc
    paper.setdefault("profiles", {})[KEY] = doc
    pl = [x for x in paper.get("profile_list") or [] if x.get("key") != KEY]
    pl.append({"key": KEY, "name": NAME, "about": ABOUT_RU, "risk": RISK * 100, "classes": [GRADE], "max_open": MAX_OPEN, "paper_only": True})
    paper["profile_list"] = pl
    with open(p, "w", encoding="utf-8") as f:
        json.dump(paper, f, ensure_ascii=False)
    return doc
