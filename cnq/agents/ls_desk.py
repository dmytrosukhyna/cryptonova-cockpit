"""Нейтральная корзина: long the 10 best alts by the quality card, short the 10 worst — a paper desk that earns from the gap
between good and weak alts, not from the market's direction (19.10.2026, owner: «продолжай выполнять тесты, искать лучшие
возможности … усовершенствовать систему»).

Evidence (scripts/own_signals_study.py --ls --iv, round 15; every Binance USDT-M perp 2022–2026, delisted coins included, the
point-in-time top 100 alts by 30-day dollar volume with ≥ 1 year of history; monthly rebalance; DAILY marks; real Binance funding;
0.2 % of the replaced notional for costs):
  long the top 10 / short the bottom 10 of the quality composite (spot_quality.py), weights ∝ 1 ÷ 30-day volatility (no coin above
  2.5× an equal share): ×3.36 at gross 1 (half long, half short), worst drawdown −27 %, +2.55 % a month, every year 2022–2026 in
  plus (+10 / +15 / +39 / +55 / +23 %), first half ×1.28, second ×2.62; funding cost −0.6 % a month is inside.
  At gross 0.75 (the drawdown-25 % setting): ×2.57, +1.9 % a month. Rebalanced on the 15th instead (robustness): ×1.84 at gross
  0.75, +1.25 % a month, 2023 −11 % at gross 1 — so the honest range is +1.3…+1.9 % a month. Equal weights instead: drawdown −47 %
  (the short leg's squeezes); that is why the weights are by volatility. The composite's signs were chosen on the same history
  (round 13).

Live: the ranking = web/quality.json (spot_quality.py, daily); rebalance on the first run of each UTC month (and on the first run
ever); marks every run from the BingX ticker; funding accrues every run from BingX premiumIndex (rate per 8 h × hours ÷ 8; longs
pay a positive rate, shorts receive it). Demo $10 000, no orders anywhere. Stdlib only; every fetch injectable."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time

from .history import thin

from .own_desk import _get, fetch_daily, fetch_prices

START = 10_000.0
N = 10
GROSS = 0.75              # half long, half short; the setting where the history's worst drawdown was 25 %
CAP_MULT = 2.5            # no coin above 2.5× an equal share
FEE = 0.0007              # 0.05 % taker + 0.02 % slippage, per side
DAY = 86_400_000
NAME = "Нейтральная корзина"
ABOUT = ("Зарабатывает на разнице между сильными и слабыми альтами, а не на росте или падении рынка. Раз в месяц покупаем 10 "
         "лучших альтов по анкете качества (есть выручка, старше, без хайпа) и шортим 10 худших. Доли — по спокойствию монеты: "
         "чем сильнее её качает, тем меньше доля. Честная история 2022–2026 (все монеты Binance, включая снятые с торгов, "
         "фандинг и комиссии учтены): в среднем от +1.3% до +1.9% в месяц в зависимости от дня ребаланса, худшая просадка до −25%; "
         "при ребалансе 1-го числа каждый год в плюсе, при ребалансе 15-го 2023 год в минусе. Демо $10 000.")
RULES = ("раз в месяц: лонг 10 лучших и шорт 10 худших альтов по анкете качества (топ-100 по объёму, от года торгов); доли обратно "
         "колебаниям за 30 дней, не больше 2.5 равных долей; общий объём 75% счёта (по 37.5% в каждую сторону); без стопов")


def fetch_funding() -> dict:
    """{SYM: last funding rate} for the BingX USDT perps (one request)."""
    d = _get(f"https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex?timestamp={int(time.time() * 1000)}")
    out = {}
    for x in d.get("data") or []:
        s = str(x.get("symbol") or "")
        if s.endswith("-USDT") and x.get("lastFundingRate") not in (None, ""):
            try:
                out[s[:-5]] = float(x["lastFundingRate"])
            except (TypeError, ValueError):
                continue
    return out


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS lsn_state(k TEXT PRIMARY KEY, v TEXT);
      CREATE TABLE IF NOT EXISTS lsn_pos(symbol TEXT PRIMARY KEY, side TEXT, qty REAL, entry REAL, opened INTEGER, rank INTEGER,
        weight REAL, funding_usd REAL);
      CREATE TABLE IF NOT EXISTS lsn_closed(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, qty REAL, entry REAL,
        opened INTEGER, closed INTEGER, exit REAL, pnl_usd REAL, kind TEXT);
      CREATE TABLE IF NOT EXISTS lsn_equity(time INTEGER PRIMARY KEY, equity REAL);""")
    conn.commit()


def _st(conn, k, d=None):
    r = conn.execute("SELECT v FROM lsn_state WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else d


def _set(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO lsn_state VALUES(?, ?)", (k, json.dumps(v)))


def vol30(K: list) -> float | None:
    """The daily σ of log returns over the last 30 closed candles (K: [[t, o, h, l, c, v], …], the forming one last)."""
    cs = [k[4] for k in K[:-1][-31:] if k[4] and k[4] > 0]
    if len(cs) < 15:
        return None
    r = [math.log(b / a) for a, b in zip(cs, cs[1:])]
    m = sum(r) / len(r)
    return (sum((x - m) ** 2 for x in r) / len(r)) ** 0.5 or None


def weights(syms: list, sig: dict) -> dict:
    """Inverse-volatility weights, a missing σ = the median, capped at CAP_MULT equal shares, summing to 1."""
    iv = {s: 1 / sig[s] for s in syms if sig.get(s)}
    med = sorted(iv.values())[len(iv) // 2] if iv else 1.0
    raw = {s: iv.get(s, med) for s in syms}
    cap = CAP_MULT / max(1, len(syms))
    fixed: set = set()
    while True:                              # water-filling: capped coins stay at the cap, the rest share what is left
        free = {s: v for s, v in raw.items() if s not in fixed}
        if not free:
            return {s: 1 / len(syms) for s in syms}
        rem = 1 - cap * len(fixed)
        tot = sum(free.values()) or 1.0
        w = {s: rem * v / tot for s, v in free.items()}
        over = {s for s, v in w.items() if v > cap + 1e-12}
        if not over:
            return {**{s: cap for s in fixed}, **w}
        fixed |= over


def pick(quality: dict, prices: dict) -> tuple[list, list, dict]:
    """(longs, shorts, {sym: rank}) from quality.json rows (best first); only coins with a BingX price."""
    rows = [r for r in (quality or {}).get("rows") or [] if r.get("symbol") in prices]
    rank = {r["symbol"]: int(r.get("rank") or k + 1) for k, r in enumerate(rows)}
    if len(rows) < 2 * N + 5:
        return [], [], rank
    return [r["symbol"] for r in rows[:N]], [r["symbol"] for r in rows[-N:]], rank


def _pos(conn) -> dict:
    keys = ("symbol", "side", "qty", "entry", "opened", "rank", "weight", "funding_usd")
    return {r[0]: dict(zip(keys, r)) for r in conn.execute(f"SELECT {', '.join(keys)} FROM lsn_pos")}


def _cash(conn) -> float:
    return float(_st(conn, "cash", START))


def equity(conn, px: dict) -> float:
    eq = _cash(conn)
    for p in _pos(conn).values():
        sg = 1 if p["side"] == "LONG" else -1
        eq += sg * ((px.get(p["symbol"]) or p["entry"]) - p["entry"]) * p["qty"]
    return eq


def _trade(conn, p: dict | None, sym: str, side: str, qty_new: float, price: float, now: int, rank: int | None, w: float | None,
           events: list) -> None:
    """Move the position in sym to (side, qty_new) at price: realise what is reduced or reversed, fees on what changes."""
    cash = _cash(conn)
    if p and (p["side"] != side or qty_new <= 0):
        sg = 1 if p["side"] == "LONG" else -1
        pnl = sg * (price - p["entry"]) * p["qty"] - FEE * price * p["qty"]
        cash += pnl
        conn.execute("INSERT INTO lsn_closed(symbol, side, qty, entry, opened, closed, exit, pnl_usd, kind) VALUES(?,?,?,?,?,?,?,?,?)",
                     (sym, p["side"], p["qty"], p["entry"], p["opened"], now, price, round(pnl + (p.get("funding_usd") or 0), 2), "rebalance"))
        conn.execute("DELETE FROM lsn_pos WHERE symbol=?", (sym,))
        events.append(f"{'лонг' if p['side'] == 'LONG' else 'шорт'} {sym} закрыт: {'+' if pnl >= 0 else '−'}${abs(pnl):.0f}")
        p = None
    if qty_new <= 0:
        _set(conn, "cash", cash)
        return
    if p is None:
        cash -= FEE * price * qty_new
        conn.execute("INSERT OR REPLACE INTO lsn_pos VALUES(?,?,?,?,?,?,?,?)", (sym, side, qty_new, price, now, rank, w, 0.0))
        events.append(f"{'лонг' if side == 'LONG' else 'шорт'} {sym}: ${price * qty_new:,.0f}".replace(",", " "))
    else:
        sg = 1 if side == "LONG" else -1
        dq = qty_new - p["qty"]
        if dq < 0:                                   # reduce: realise the part
            cash += sg * (price - p["entry"]) * (-dq) - FEE * price * (-dq)
            conn.execute("UPDATE lsn_pos SET qty=?, rank=?, weight=? WHERE symbol=?", (qty_new, rank, w, sym))
        elif dq > 0:                                 # add: the average entry
            cash -= FEE * price * dq
            e = (p["entry"] * p["qty"] + price * dq) / qty_new
            conn.execute("UPDATE lsn_pos SET qty=?, entry=?, rank=?, weight=? WHERE symbol=?", (qty_new, e, rank, w, sym))
    _set(conn, "cash", cash)


def rebalance(conn, now: int, px: dict, quality: dict, daily=fetch_daily) -> list:
    events: list = []
    longs, shorts, rank = pick(quality, px)
    if not longs:
        return ["анкета качества ещё не готова — корзина ждёт"]
    sig = {}
    for s in longs + shorts:
        try:
            sig[s] = vol30(daily(s))
        except Exception:  # noqa: BLE001
            sig[s] = None
    eq = equity(conn, px)
    tgt = {}
    for leg, side in ((longs, "LONG"), (shorts, "SHORT")):
        w = weights(leg, sig)
        for s in leg:
            tgt[s] = (side, eq * GROSS * 0.5 * w[s] / px[s], w[s])
    cur = _pos(conn)
    for s, p in cur.items():                         # out of the basket first
        if s not in tgt:
            _trade(conn, p, s, p["side"], 0.0, px.get(s) or p["entry"], now, None, None, events)
    cur = _pos(conn)
    for s, (side, q, w) in tgt.items():
        _trade(conn, cur.get(s), s, side, q, px[s], now, rank.get(s), w, events)
    _set(conn, "month", time.strftime("%Y-%m", time.gmtime(now / 1000)))
    _set(conn, "last_rebalance", now)
    conn.commit()
    return [f"ребаланс: лонг {', '.join(longs)}; шорт {', '.join(shorts)}"] + events


def accrue_funding(conn, now: int, px: dict, rates: dict) -> float:
    last = _st(conn, "funding_t")
    _set(conn, "funding_t", now)
    if not last or not rates:
        return 0.0
    hrs = min(12.0, max(0.0, (now - last) / 3_600_000))
    tot = 0.0
    for p in _pos(conn).values():
        r = rates.get(p["symbol"])
        if r is None:
            continue
        sg = 1 if p["side"] == "LONG" else -1
        f = -sg * r * (hrs / 8) * (px.get(p["symbol"]) or p["entry"]) * p["qty"]
        tot += f
        conn.execute("UPDATE lsn_pos SET funding_usd=COALESCE(funding_usd, 0) + ? WHERE symbol=?", (f, p["symbol"]))
    _set(conn, "cash", _cash(conn) + tot)
    return tot


def doc(conn, now: int, px: dict, events: list) -> dict:
    eq = equity(conn, px)
    conn.execute("INSERT OR REPLACE INTO lsn_equity VALUES(?, ?)", (now, round(eq, 2)))
    conn.commit()
    eqh = [{"time": a, "equity": b} for a, b in conn.execute("SELECT time, equity FROM lsn_equity ORDER BY time")]
    peak, dd = START, 0.0
    for e in eqh:
        peak = max(peak, e["equity"])
        dd = max(dd, (peak - e["equity"]) / peak)
    out_open, open_pnl = [], 0.0
    for p in sorted(_pos(conn).values(), key=lambda p: (p["side"], p.get("rank") or 0)):
        price = px.get(p["symbol"]) or p["entry"]
        sg = 1 if p["side"] == "LONG" else -1
        u = sg * (price - p["entry"]) * p["qty"]
        open_pnl += u
        out_open.append({"id": f"lsn_{p['symbol']}", "symbol": p["symbol"], "side": p["side"], "grade": "LSN", "kind": "LSN",
                         "entry": p["entry"], "stop": None, "qty": p["qty"], "qty_left": p["qty"], "opened": p["opened"],
                         "pnl_usd": round(p.get("funding_usd") or 0, 2), "price": price, "open_pnl_usd": round(u, 2),
                         "reason": (f"анкета качества: место {p['rank']} — " if p.get("rank") else "")
                                   + ("в лучшей десятке, лонг" if p["side"] == "LONG" else "в худшей десятке, шорт")
                                   + (f"; доля {p['weight'] * 100:.0f}% стороны" if p.get("weight") else "")
                                   + "; держим до ребаланса в начале месяца"})
    keys = ("id", "symbol", "side", "qty", "entry", "opened", "closed", "exit", "pnl_usd", "kind")
    closed = [dict(zip(keys, r)) for r in conn.execute(f"SELECT {', '.join(keys)} FROM lsn_closed ORDER BY closed DESC LIMIT 60")]
    for t in closed:
        t.update({"grade": "LSN", "exit_kind": t.pop("kind"), "net_usd": t["pnl_usd"]})
    n = conn.execute("SELECT COUNT(*) FROM lsn_closed").fetchone()[0]
    wins = conn.execute("SELECT COUNT(*) FROM lsn_closed WHERE pnl_usd > 0").fetchone()[0]
    first = conn.execute("SELECT MIN(time) FROM lsn_equity").fetchone()[0] or now
    return {"asof": now, "profile": "lsn", "name": NAME, "about": ABOUT, "start_equity": START, "since": first, "reset": None,
            "equity": round(eq - open_pnl, 2), "equity_mtm": round(eq, 2), "pnl_usd": round(eq - START, 2),
            "pnl_pct": round((eq / START - 1) * 100, 2), "open_pnl_usd": round(open_pnl, 2), "open": out_open, "pending": [],
            "closed": closed, "stats": {"trades": n, "winrate": round(wins / n, 3) if n else None, "avg_trade_pct": None,
                                        "max_dd_pct": round(dd * 100, 2), "best": 0, "worst": 0},
            "equity_history": thin(eqh), "events": events[:20], "error": None, "regime_ru": "рыночно-нейтрально: половина лонг, половина шорт",
            "rules": RULES, "last_rebalance": _st(conn, "last_rebalance"),
            "evidence": {"x_gross_075": [2.57, 1.84], "month_pct": [1.9, 1.25], "rebalance_day": [1, 15], "max_dd_pct": 25, "years_pct_gross1": [10, 15, 39, 55, 23],
                         "halves_x_gross1": [1.28, 2.62], "funding_month_pct": -0.58}}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, prices=fetch_prices, funding=fetch_funding,
        daily=fetch_daily) -> dict:
    now = now or int(time.time() * 1000)
    ensure(conn)
    px = prices()
    events: list = []
    try:
        quality = json.load(open(os.path.join(out, "quality.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        quality = None
    try:
        rates = funding()
    except Exception:  # noqa: BLE001
        rates = {}
    f = accrue_funding(conn, now, px, rates)
    if abs(f) >= 1:
        events.append(f"фандинг {'+' if f >= 0 else '−'}${abs(f):.2f}")
    month = time.strftime("%Y-%m", time.gmtime(now / 1000))
    if _st(conn, "month") != month:
        events = rebalance(conn, now, px, quality, daily) + events
    conn.commit()
    d = doc(conn, now, px, events)
    p = os.path.join(out, "paper.json")
    try:
        paper = json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return d
    paper.setdefault("profiles", {})["lsn"] = d
    pl = [x for x in paper.get("profile_list") or [] if x.get("key") != "lsn"]
    pl.append({"key": "lsn", "name": NAME, "about": ABOUT, "risk": 0, "classes": ["LSN"], "max_open": 2 * N})
    paper["profile_list"] = pl
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(paper, fh, ensure_ascii=False)
    return d
