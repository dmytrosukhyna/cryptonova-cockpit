"""«Сигналы CRYPTO NOVA» — our own daily signals with a steady flow, a $10 000 demo desk (19.10.2026, owner: «нам нужны
собственные сигналы … нужно движение, сигналы должны быть»; «почему мы не будем открывать ничего без резкого пролива?»).

Evidence (scripts/own_signals_study.py, Binance USDT-M daily candles 2022-01 → 2026-10, costs 0.14 % a trade + 0.02 % a day):
  • 42 rule variants (breakouts, pullbacks, weekly trend; both sides; four gates). No single family is in plus every year:
    the pullback lost in 2024, the trailing breakouts lost in 2025. Together they cover each other.
  • The chosen pair — P (pullback in a trend) + C (20-day breakout with a 3-ATR trailing exit), both only in the direction of
    our BTC regime — is in plus per trade in EVERY year 2022–2026 on the 40 coins it was picked on AND on 30 other coins never
    used to pick it (FET, RENDER, PEPE, WIF, …): +0.76 % and +0.75 % a trade, about 4 and 2.5 signals a day, 61 % winners,
    ≈ +0.4 % a trade better than a random entry of the same coin, side and holding time.
  • The edge is in the longs (+1.9…+2.3 % a trade); the shorts are about zero (+0.06 / −0.14 %) — they keep the flow going in a
    falling market and are sized the same, but they are the weak part.
  • Account (fixed risk per trade, at most 20 open): risk 0.5 % → 2022–2026 +31 / +28 / +1 / +18 / +36 % (40 coins) and
    +26 / +17 / +11 / +8 / +23 % (30 other coins), worst drawdown −46 % / −43 %, about +2 % a month on average.
    Risk 0.25 % → every year in plus on both sets, drawdown −26 %, about +1 % a month. Trades fall together in sharp market turns
    — that is where the drawdown comes from.

Rules (checked once a day after the daily close, 00:00 UTC; entries and rule exits at the new day's open):
  P — long: close above a RISING 50-day average (vs 5 days ago) and RSI(2) < 10, BTC regime «рост» (BTC above its 200-day average
      and up over 60 days); out when the close is back above the 5-day average or after 10 days. Short: the mirror, regime not «рост».
  C — long: close above the high of the previous 20 days and above the 20-day average, regime «рост»; out when the close falls
      3 ATR(14) below the best close since entry, or after 14 days. Short: the mirror, regime not «рост».
  Stop for both: 2.5 ATR(14) from the entry, at least 3 % and at most 15 % — checked every run (≈ 30 min) against the live price.
  Size: 0.5 % of the account is lost at the stop (notional at most 25 % of the account), at most 20 open, one position per coin and
  rule. Fees 0.05 % + slippage 0.02 % per side. Writes profiles["own"] into web/paper.json. Stdlib only; fetchers injectable."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.request

COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "BCH", "TRX", "NEAR", "ATOM", "ETC", "FIL",
         "APT", "ARB", "OP", "INJ", "SUI", "UNI", "AAVE", "XLM", "HBAR", "ICP", "RUNE", "STX", "GRT", "LDO", "SAND", "AXS", "CRV",
         "DYDX", "GALA", "TIA", "SEI", "WLD", "ENA"]
START = 10_000.0
RISK, NOTIONAL_CAP, MAX_OPEN = 0.005, 0.25, 20
FEE = 0.0007                      # 0.05 % taker + 0.02 % slippage, per side
HOLD = {"P": 10, "C": 14}
DAY = 86_400_000
NAME = "Сигналы CRYPTO NOVA"
RULE_RU = {"P": "откат в тренде", "C": "пробой 20 дней"}
ABOUT = ("Наши собственные сигналы раз в день на 40 монетах, только по направлению рынка: в росте — покупки, в падении — шорты. "
         "Два правила: «откат в тренде» (монета в росте сделала короткий откат) и «пробой 20 дней» (цена вышла из 20-дневного "
         "диапазона, держим, пока тренд не сломается). Стоп 3–15% от входа, риск 0.5% счёта на сделку, до 20 сделок. История "
         "2022–2026: плюс на сделку в каждом году и на 40 монетах, и на 30 других; в среднем около +2% в месяц, худшая просадка −46%. "
         "Сильнее покупки, шорты около нуля. Демо $10 000.")


def _get(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 cryptonova/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_daily(sym: str) -> list:
    """[[open ms, o, h, l, c], …] ascending, the last row is today's forming candle. BingX perp first, Binance spot mirror next."""
    try:
        from ..bingx.rest import BingXRest
        rows = [[c.open_time, c.open, c.high, c.low, c.close] for c in BingXRest().klines(f"{sym}-USDT", "1d", limit=300)]
        if len(rows) >= 120:
            return rows
    except Exception:  # noqa: BLE001
        pass
    d = _get(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}USDT&interval=1d&limit=300")
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in d]


def fetch_prices() -> dict:
    """{SYM: last price} for the BingX perps (one request)."""
    d = _get(f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?timestamp={int(time.time() * 1000)}")
    out = {}
    for x in d.get("data") or []:
        s = str(x.get("symbol") or "")
        if s.endswith("-USDT") and x.get("lastPrice") not in (None, "", "0"):
            out[s[:-5]] = float(x["lastPrice"])
    return out


# ------------------------------------------------------------------------------------------------ indicators (closed candles)
def _sma(xs: list, n: int, end: int) -> float | None:
    return sum(xs[end - n + 1:end + 1]) / n if end - n + 1 >= 0 else None


def _atr(K: list, end: int, n: int = 14) -> float | None:
    if end < n:
        return None
    trs = [max(K[i][2] - K[i][3], abs(K[i][2] - K[i - 1][4]), abs(K[i][3] - K[i - 1][4])) for i in range(end - n + 1, end + 1)]
    return sum(trs) / n


def _rsi2(c: list, end: int) -> float | None:
    if end < 2:
        return None
    d = [c[i] - c[i - 1] for i in (end - 1, end)]
    up = sum(x for x in d if x > 0) / 2
    dn = sum(-x for x in d if x < 0) / 2
    return 100.0 if dn == 0 else 100 - 100 / (1 + up / dn)


def regime(btc: list) -> bool | None:
    """BTC above its 200-day average and up over 60 days, on closed candles."""
    c = [k[4] for k in btc]
    e = len(c) - 1
    s200 = _sma(c, 200, e)
    if s200 is None or e < 60:
        return None
    return c[e] > s200 and c[e] > c[e - 60]


def signals(K: list, bull: bool) -> list[tuple[str, int]]:
    """Signals at the last CLOSED candle of K → [(rule, side)], side 1 long / −1 short."""
    c = [k[4] for k in K]
    e = len(c) - 1
    out = []
    s50, s50_5, s20 = _sma(c, 50, e), _sma(c, 50, e - 5), _sma(c, 20, e)
    r2 = _rsi2(c, e)
    if s50 is not None and s50_5 is not None and r2 is not None:
        if bull and c[e] > s50 and s50 > s50_5 and r2 < 10:
            out.append(("P", 1))
        if not bull and c[e] < s50 and s50 < s50_5 and r2 > 90:
            out.append(("P", -1))
    if s20 is not None and e >= 21:
        hh = max(k[2] for k in K[e - 20:e])
        ll = min(k[3] for k in K[e - 20:e])
        if bull and c[e] > hh and c[e] > s20:
            out.append(("C", 1))
        if not bull and c[e] < ll and c[e] < s20:
            out.append(("C", -1))
    return out


def rule_exit(t: dict, K: list) -> bool:
    """The rule's own exit at the last closed candle (the stop is checked separately, every run)."""
    c = [k[4] for k in K]
    e = len(c) - 1
    sg = 1 if t["side"] == "LONG" else -1
    days = (K[e][0] - t["opened_day"]) // DAY + 1
    if t["rule"] == "P":
        s5 = _sma(c, 5, e)
        if s5 is not None and ((sg == 1 and c[e] > s5) or (sg == -1 and c[e] < s5)):
            return True
    else:
        a = _atr(K, e)
        best = t["best"]
        if a is not None and ((sg == 1 and c[e] < best - 3 * a) or (sg == -1 and c[e] > best + 3 * a)):
            return True
    return days >= HOLD[t["rule"]]


# ------------------------------------------------------------------------------------------------ storage
def ensure(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS own_state(k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("""CREATE TABLE IF NOT EXISTS own_trades(id TEXT PRIMARY KEY, symbol TEXT, rule TEXT, side TEXT, opened INTEGER,
                    opened_day INTEGER, entry REAL, stop REAL, qty REAL, best REAL, closed INTEGER, exit REAL, exit_kind TEXT,
                    pnl_usd REAL)""")
    conn.execute("CREATE TABLE IF NOT EXISTS own_equity(time INTEGER PRIMARY KEY, equity REAL)")
    conn.commit()


def _state(conn, k, d=None):
    r = conn.execute("SELECT v FROM own_state WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else d


def _set(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO own_state VALUES(?, ?)", (k, json.dumps(v)))


def _close(conn, t: dict, px: float, kind: str, now: int) -> float:
    sg = 1 if t["side"] == "LONG" else -1
    pnl = sg * (px - t["entry"]) * t["qty"] - (t["entry"] + px) * t["qty"] * FEE
    conn.execute("UPDATE own_trades SET closed=?, exit=?, exit_kind=?, pnl_usd=? WHERE id=?", (now, px, kind, pnl, t["id"]))
    return pnl


def _open_rows(conn) -> list[dict]:
    keys = ("id", "symbol", "rule", "side", "opened", "opened_day", "entry", "stop", "qty", "best")
    return [dict(zip(keys, r)) for r in conn.execute(f"SELECT {', '.join(keys)} FROM own_trades WHERE closed IS NULL ORDER BY opened")]


def equity_closed(conn) -> float:
    return START + (conn.execute("SELECT COALESCE(SUM(pnl_usd), 0) FROM own_trades WHERE closed IS NOT NULL").fetchone()[0] or 0.0)


def step(conn: sqlite3.Connection, now: int | None = None, daily=fetch_daily, prices=fetch_prices) -> dict:
    now = now or int(time.time() * 1000)
    ensure(conn)
    events = []
    day = now // DAY * DAY
    # 1. stops against the live price, every run
    try:
        px = prices() or {}
    except Exception as e:  # noqa: BLE001
        px, events = {}, [f"цены: {str(e)[:80]}"]
    for t in _open_rows(conn):
        p = px.get(t["symbol"])
        if p and ((t["side"] == "LONG" and p <= t["stop"]) or (t["side"] == "SHORT" and p >= t["stop"])):
            fill = min(p, t["stop"]) if t["side"] == "LONG" else max(p, t["stop"])        # a gap through the stop fills worse
            pnl = _close(conn, t, fill, "стоп", now)
            events.append(f"{t['symbol']} {RULE_RU[t['rule']]}: стоп, {pnl:+.0f}$")
    # 2. once a day after the daily close: rule exits, then new signals at the new day's open
    if _state(conn, "day") != day and now - day >= 5 * 60_000:
        K = {}
        for s in COINS:
            try:
                rows = daily(s)
            except Exception:  # noqa: BLE001
                continue
            if rows and rows[-1][0] == day and len(rows) >= 120:
                K[s] = rows
        if "BTC" in K:
            bull = regime(K["BTC"][:-1])
            for t in _open_rows(conn):
                k = K.get(t["symbol"])
                if not k:
                    continue
                closed = k[:-1]
                c = closed[-1][4]
                best = max(t["best"], c) if t["side"] == "LONG" else min(t["best"], c)
                conn.execute("UPDATE own_trades SET best=? WHERE id=?", (best, t["id"]))
                t["best"] = best
                if rule_exit(t, closed):
                    pnl = _close(conn, t, k[-1][1], "правило", now)
                    events.append(f"{t['symbol']} {RULE_RU[t['rule']]}: выход по правилу, {pnl:+.0f}$")
            if bull is not None:
                eq = equity_closed(conn)
                open_now = _open_rows(conn)
                have = {(t["symbol"], t["rule"]) for t in open_now}
                n_open = len(open_now)
                for s, k in K.items():
                    closed = k[:-1]
                    for rule, sg in signals(closed, bull):
                        if n_open >= MAX_OPEN or (s, rule) in have:
                            continue
                        e = k[-1][1]
                        a = _atr(closed, len(closed) - 1)
                        if not a or e <= 0:
                            continue
                        dist = min(max(2.5 * a, 0.03 * e), 0.15 * e)
                        qty = min(eq * RISK / dist, eq * NOTIONAL_CAP / e)
                        tid = f"OWN-{rule}-{s}-{day // DAY}"
                        conn.execute("INSERT OR IGNORE INTO own_trades(id, symbol, rule, side, opened, opened_day, entry, stop, qty, best) "
                                     "VALUES(?,?,?,?,?,?,?,?,?,?)",
                                     (tid, s, rule, "LONG" if sg == 1 else "SHORT", now, day, e, e - sg * dist, qty, closed[-1][4]))
                        have.add((s, rule))
                        n_open += 1
                        events.append(f"{s} {RULE_RU[rule]}: {'покупка' if sg == 1 else 'шорт'} по {e:g}")
            _set(conn, "regime", bull)
            _set(conn, "day", day)
            _set(conn, "coins_seen", len(K))
    conn.commit()
    return {"events": events, "prices": px}


def doc(conn: sqlite3.Connection, now: int, px: dict, events: list) -> dict:
    eqc = equity_closed(conn)
    out_open, open_pnl = [], 0.0
    for t in _open_rows(conn):
        p = px.get(t["symbol"]) or t["entry"]
        sg = 1 if t["side"] == "LONG" else -1
        u = sg * (p - t["entry"]) * t["qty"] - t["entry"] * t["qty"] * FEE
        open_pnl += u
        out_open.append({"id": t["id"], "symbol": t["symbol"], "side": t["side"], "grade": t["rule"], "kind": "OWN_" + t["rule"],
                         "entry": t["entry"], "stop": t["stop"], "qty": t["qty"], "qty_left": t["qty"], "opened": t["opened"], "pnl_usd": 0.0,
                         "price": p, "open_pnl_usd": round(u, 2),
                         "reason": f"{RULE_RU[t['rule']]}: стоп {abs(t['stop'] / t['entry'] - 1) * 100:.1f}%, выход по правилу или через {HOLD[t['rule']]} дней"})
    eq = eqc + open_pnl
    conn.execute("INSERT OR REPLACE INTO own_equity VALUES(?, ?)", (now, round(eq, 2)))
    conn.commit()
    eqh = [{"time": a, "equity": b} for a, b in conn.execute("SELECT time, equity FROM own_equity ORDER BY time")][-500:]
    peak, dd = START, 0.0
    for e in eqh:
        peak = max(peak, e["equity"])
        dd = max(dd, (peak - e["equity"]) / peak)
    keys = ("id", "symbol", "rule", "side", "opened", "entry", "closed", "exit", "exit_kind", "pnl_usd", "qty")
    closed = [dict(zip(keys, r)) for r in conn.execute(f"SELECT {', '.join(keys)} FROM own_trades WHERE closed IS NOT NULL ORDER BY closed DESC LIMIT 60")]
    for t in closed:
        t.update({"grade": t.pop("rule"), "net_usd": round(t["pnl_usd"], 2)})
    n = conn.execute("SELECT COUNT(*) FROM own_trades WHERE closed IS NOT NULL").fetchone()[0]
    wins = conn.execute("SELECT COUNT(*) FROM own_trades WHERE closed IS NOT NULL AND pnl_usd > 0").fetchone()[0]
    avg = conn.execute("SELECT AVG(pnl_usd / (qty * entry)) FROM own_trades WHERE closed IS NOT NULL AND qty > 0").fetchone()[0]
    first = conn.execute("SELECT MIN(time) FROM own_equity").fetchone()[0] or now
    bull = _state(conn, "regime")
    return {"asof": now, "profile": "own", "name": NAME, "about": ABOUT, "start_equity": START, "since": first, "reset": None,
            "equity": round(eqc, 2), "equity_mtm": round(eq, 2), "pnl_usd": round(eq - START, 2), "pnl_pct": round((eq / START - 1) * 100, 2),
            "open_pnl_usd": round(open_pnl, 2), "open": out_open, "pending": [], "closed": closed,
            "stats": {"trades": n, "winrate": round(wins / n, 3) if n else None, "avg_trade_pct": round(avg * 100, 2) if avg is not None else None,
                      "max_dd_pct": round(dd * 100, 2), "best": 0, "worst": 0},
            "equity_history": eqh, "events": events[:20], "error": None,
            "regime_ru": None if bull is None else ("рост: только покупки" if bull else "не рост: только шорты"),
            "rules": "откат в тренде и пробой 20 дней, по направлению режима BTC; стоп 2.5 ATR (3–15%), риск 0.5% счёта, до 20 сделок"}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, daily=fetch_daily, prices=fetch_prices) -> dict:
    now = now or int(time.time() * 1000)
    r = step(conn, now, daily, prices)
    d = doc(conn, now, r["prices"], r["events"])
    p = os.path.join(out, "paper.json")
    try:
        paper = json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return d
    paper.setdefault("profiles", {})["own"] = d
    pl = [x for x in paper.get("profile_list") or [] if x.get("key") != "own"]
    pl.append({"key": "own", "name": NAME, "about": ABOUT, "risk": 2, "classes": ["P", "C"], "max_open": MAX_OPEN})
    paper["profile_list"] = pl
    with open(p, "w", encoding="utf-8") as f:
        json.dump(paper, f, ensure_ascii=False)
    return d
