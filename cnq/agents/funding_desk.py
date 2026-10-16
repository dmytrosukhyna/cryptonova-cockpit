"""Funding desk — paper "funding carry" account ($10 000), docs/research/funding_carry.md, scripts/funding_carry_test.py.

Strategy (delta-neutral): for a selected coin we hold LONG spot + SHORT perpetual of equal notional. Price moves cancel,
and while funding is positive the short side receives notional × funding at every settlement (00/08/16 UTC).
  • entry: trailing 7-day average funding of the coin > 0.01% per 8 h (before 3 records exist: the current rate);
  • keep while the trailing average stays > 0 (hysteresis, otherwise fees eat everything);
  • up to 6 coins, equal capital: capital/coin = equity/6, notional = capital/1.2 (spot leg + perp margin at 5×);
  • costs: 0.15% of notional on entry and 0.15% on exit (both legs together).
Every run records the current funding rate of each venue (Binance, Bybit, OKX, BingX, Hyperliquid; normalised to a fraction
per 8 h) into funding_hist; the account uses ONE reference venue (Binance, else Bybit, else BingX — chosen once, recorded).
A settlement is credited with the rate recorded closest before it (no record within 8 h → skipped, and said so).

Honesty caveats: spot and perp prices are treated as identical — basis/divergence risk, borrow, transfers and liquidation of
the perp leg on a spike are NOT modelled. The recorded rate is the venue's live estimate shortly before settlement, not the
settled print. Some venues settle some coins every 4 h or 1 h; rates are normalised per 8 h but crediting assumes 8-hour
settlements. The research found ≈ +5% a year on capital in 2023–2026, but only ≈ +1% in 2025 and ≈ 0 in 2026.
Stdlib only; all network calls are injectable (`fetch=`)."""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 cryptonova/1.0", "Content-Type": "application/json"}
START_EQUITY = 10_000.0
MAX_COINS, LEVER_K, COST = 6, 1.2, 0.0015
ENTRY_THR, EXIT_THR = 0.0001, 0.0
H8, DAY = 8 * 3_600_000, 86_400_000
BUCKET = 15 * 60_000                    # funding_hist dedupe: one record per venue/coin per 15 minutes
VENUES = ("binance", "bybit", "okx", "bingx", "hyperliquid")
REF_ORDER = ("binance", "bybit", "bingx")
NAME = "Фандинг · без направления"
ABOUT = ("Покупаем монету на споте и одновременно продаём такой же объём фьючерса. Цена монеты на результат почти не влияет: "
         "что теряем на одной ноге, получаем на другой. Зарабатываем на фандинге — каждые 8 часов покупатели фьючерса платят "
         "продавцам, когда рынок перегрет в лонг. Берём до 6 монет, где средний фандинг за 7 дней выше 0.01% за 8 ч, и держим, "
         "пока он остаётся положительным. История 2023–2026: ≈ +5% в год при очень маленькой просадке, но в 2025–2026 только ≈ +1% "
         "в год — фандинг на крупных монетах сжался. Это учебный счёт на $10 000, без реальных денег.")
NOTE = ("Цены спота и фьючерса считаются одинаковыми; риск расхождения не учтён. Комиссия 0.15% от объёма при входе и столько же "
        "при выходе. Начисляем ставку, записанную последней перед выплатой (оценка биржи, не итоговая ставка). Межбиржевой "
        "спред — только идея: шорт фьючерса там, где фандинг выше, лонг там, где ниже; комиссии и переводы в нём не учтены.")


def default_bases() -> list[str]:
    from cnq.config import Config
    return [bx.split("-")[0] for bx, _ in Config().universe]


# ── public endpoints: each returns {base: fraction per 8 h} or {} on any failure ─────────────────────────────────────────

def _get(url: str, timeout: int = 10, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


def _ok(r: float) -> bool:
    return abs(r) < 0.05               # drop garbage (>5% per 8 h is not a real funding print)


def _binance(bases):
    try:
        want = {b + "USDT": b for b in bases}
        return {want[d["symbol"]]: float(d["lastFundingRate"]) for d in _get("https://fapi.binance.com/fapi/v1/premiumIndex")
                if d.get("symbol") in want and d.get("lastFundingRate") not in (None, "") and _ok(float(d["lastFundingRate"]))}
    except Exception:  # noqa: BLE001
        return {}


def _bybit(bases):
    try:
        want = {b + "USDT": b for b in bases}; out = {}
        for d in _get("https://api.bybit.com/v5/market/tickers?category=linear")["result"]["list"]:
            if d.get("symbol") in want and d.get("fundingRate") not in (None, ""):
                r = float(d["fundingRate"]) * 8 / float(d.get("fundingIntervalHour") or 8)
                if _ok(r): out[want[d["symbol"]]] = r
        return out
    except Exception:  # noqa: BLE001
        return {}


def _okx(bases):
    out = {}
    for b in bases:
        try:
            d = _get(f"https://www.okx.com/api/v5/public/funding-rate?instId={b}-USDT-SWAP")["data"][0]
            r = float(d["fundingRate"])
            try:   # interval = gap between the next two settlements (normally 8 h)
                hrs = (int(d["nextFundingTime"]) - int(d["fundingTime"])) / 3_600_000
                r = r * 8 / hrs if 0.5 <= hrs <= 8 else r
            except Exception:  # noqa: BLE001
                pass
            if _ok(r): out[b] = r
        except Exception:  # noqa: BLE001
            if not out and b == bases[min(2, len(bases) - 1)]:
                break                       # OKX unreachable: stop after 3 failures instead of 18 timeouts
    return out


def _bingx(bases):
    try:
        want = {b + "-USDT": b for b in bases}; out = {}
        d = _get(f"https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex?timestamp={int(time.time() * 1000)}")
        for x in d.get("data") or []:
            if x.get("symbol") in want and x.get("lastFundingRate") not in (None, ""):
                r = float(x["lastFundingRate"])
                if _ok(r): out[want[x["symbol"]]] = r
        return out
    except Exception:  # noqa: BLE001
        return {}


def _hyperliquid(bases):
    try:
        meta, ctxs = _get("https://api.hyperliquid.xyz/info", timeout=15, body={"type": "metaAndAssetCtxs"})
        want = set(bases)
        return {u["name"]: float(c["funding"]) * 8 for u, c in zip(meta["universe"], ctxs)       # hourly → per 8 h
                if u["name"] in want and c.get("funding") is not None and _ok(float(c["funding"]) * 8)}
    except Exception:  # noqa: BLE001
        return {}


def fetch_funding(bases: list[str] | None = None) -> dict[str, dict[str, float]]:
    """{venue: {base: funding fraction per 8 h}} — venues that failed are simply missing/empty."""
    bases = bases or default_bases()
    return {v: f(bases) for v, f in (("binance", _binance), ("bybit", _bybit), ("okx", _okx), ("bingx", _bingx), ("hyperliquid", _hyperliquid))}


# ── storage ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS funding_hist(venue TEXT, symbol TEXT, time INTEGER, rate REAL, PRIMARY KEY(venue, symbol, time));
        CREATE TABLE IF NOT EXISTS funding_desk_pos(symbol TEXT PRIMARY KEY, notional REAL, entry_time INTEGER, earned_usd REAL);
        CREATE TABLE IF NOT EXISTS funding_desk_equity(time INTEGER PRIMARY KEY, equity REAL);
        CREATE TABLE IF NOT EXISTS funding_desk_events(time INTEGER, text TEXT);
        CREATE TABLE IF NOT EXISTS funding_desk_meta(k TEXT PRIMARY KEY, v TEXT);""")


def _meta(conn, k, default=None):
    r = conn.execute("SELECT v FROM funding_desk_meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def _set(conn, k, v) -> None:
    conn.execute("INSERT OR REPLACE INTO funding_desk_meta VALUES(?,?)", (k, str(v)))


def record(conn: sqlite3.Connection, rates: dict, now: int) -> int:
    t = now - now % BUCKET; n = 0
    for venue, m in rates.items():
        for sym, r in (m or {}).items():
            conn.execute("INSERT OR REPLACE INTO funding_hist VALUES(?,?,?,?)", (venue, sym, t, float(r))); n += 1
    conn.execute("DELETE FROM funding_hist WHERE time < ?", (now - 30 * DAY,))
    return n


def avg7(conn, venue: str, sym: str, now: int, current: float | None) -> float | None:
    rows = conn.execute("SELECT rate FROM funding_hist WHERE venue=? AND symbol=? AND time>? AND time<=?", (venue, sym, now - 7 * DAY, now)).fetchall()
    return sum(r[0] for r in rows) / len(rows) if len(rows) >= 3 else current


def _rate_before(conn, venue: str, sym: str, t: int) -> float | None:
    r = conn.execute("SELECT rate FROM funding_hist WHERE venue=? AND symbol=? AND time<? AND time>=? ORDER BY time DESC LIMIT 1",
                     (venue, sym, t, t - H8)).fetchone()
    return r[0] if r else None


def _pct(r: float) -> str:
    return f"{r * 100:.3f}%"


# ── the account ────────────────────────────────────────────────────────────────────────────────────────────────────────

def run(conn: sqlite3.Connection, now: int | None = None, fetch=fetch_funding, bases: list[str] | None = None) -> dict:
    now = now or int(time.time() * 1000)
    bases = bases or default_bases()
    _ensure(conn)
    rates = fetch(bases) or {}
    record(conn, rates, now)
    ev: list[tuple[int, str]] = []
    equity = float(_meta(conn, "equity", START_EQUITY))
    pos = {s: {"notional": n, "entry_time": t, "earned_usd": e}
           for s, n, t, e in conn.execute("SELECT symbol, notional, entry_time, earned_usd FROM funding_desk_pos")}

    venue = _meta(conn, "venue")
    if venue is None or (not rates.get(venue) and not pos):          # sticky once positions exist
        venue = next((v for v in REF_ORDER if rates.get(v)), venue)
        if venue: _set(conn, "venue", venue)
    cur = rates.get(venue, {}) if venue else {}

    # 1) credit settlements (00/08/16 UTC) not yet credited
    last = int(_meta(conn, "last_settled", now - now % H8))
    s = last + H8
    while s <= now:
        tot = 0.0; miss = []
        for sym, p in pos.items():
            if p["entry_time"] >= s: continue
            r = _rate_before(conn, venue, sym, s)
            if r is None: miss.append(sym); continue
            usd = p["notional"] * r; p["earned_usd"] += usd; equity += usd; tot += usd
        if any(p["entry_time"] < s for p in pos.values()):
            ev.append((s, f"Начислен фандинг {'+' if tot >= 0 else '−'}${abs(tot):.2f}" + (f" (нет ставки для {', '.join(miss)} — пропущено)" if miss else "")))
        last = s; s += H8
    _set(conn, "last_settled", last)

    # 2) re-select coins (only with live data from the reference venue)
    avgs = {b: avg7(conn, venue, b, now, cur.get(b)) for b in bases} if venue else {}
    if cur:
        keep = {sym for sym in pos if avgs.get(sym) is None or avgs[sym] > EXIT_THR}
        for sym in [x for x in pos if x not in keep]:
            p = pos.pop(sym); c = p["notional"] * COST; equity -= c
            conn.execute("DELETE FROM funding_desk_pos WHERE symbol=?", (sym,))
            ev.append((now, f"Закрыли: {sym} — средний фандинг {_pct(avgs[sym])} за 8 ч, уже не платит. Комиссия −${c:.2f}, заработали за всё время {'+' if p['earned_usd'] >= 0 else '−'}${abs(p['earned_usd']):.2f}"))
        size = equity / MAX_COINS / LEVER_K                           # equal capital per coin, from equity before this run's entries
        for sym in sorted((b for b in bases if avgs.get(b) is not None and cur.get(b) is not None), key=lambda b: -avgs[b]):
            if len(pos) >= MAX_COINS: break
            if sym in pos or avgs[sym] <= ENTRY_THR: continue
            n = size; c = n * COST; equity -= c
            pos[sym] = {"notional": n, "entry_time": now, "earned_usd": 0.0}
            ev.append((now, f"Открыли: {sym} — спот куплен, фьючерс продан на ${n:,.0f}, получаем фандинг {_pct(avgs[sym])} каждые 8 ч. Комиссия −${c:.2f}"))
    elif venue is None and (conn.execute("SELECT text FROM funding_desk_events ORDER BY time DESC LIMIT 1").fetchone() or [""])[0][:12] != "Биржи не отв":
        ev.append((now, "Биржи не ответили — ставки фандинга не получены, счёт без изменений"))

    for sym, p in pos.items():
        conn.execute("INSERT OR REPLACE INTO funding_desk_pos VALUES(?,?,?,?)", (sym, p["notional"], p["entry_time"], p["earned_usd"]))
    _set(conn, "equity", equity)
    conn.execute("INSERT OR REPLACE INTO funding_desk_equity VALUES(?,?)", (now, round(equity, 4)))
    conn.executemany("INSERT INTO funding_desk_events VALUES(?,?)", ev)
    conn.execute("DELETE FROM funding_desk_events WHERE time < ?", (now - 30 * DAY,))
    conn.commit()
    return snapshot(conn, now, rates, venue, avgs, bases)


def snapshot(conn, now, rates, venue, avgs, bases) -> dict:
    equity = float(_meta(conn, "equity", START_EQUITY))
    eqh = conn.execute("SELECT time, equity FROM funding_desk_equity ORDER BY time").fetchall()
    step = max(1, -(-len(eqh) // 500))
    hist = [[t, round(e, 2)] for t, e in eqh[::step]]
    if eqh and hist[-1][0] != eqh[-1][0]: hist = (hist + [[eqh[-1][0], round(eqh[-1][1], 2)]])[-500:]
    cur = rates.get(venue, {}) if venue else {}
    positions = [{"symbol": s, "notional": round(n, 2), "entry_time": t, "earned_usd": round(e, 2), "rate_now": cur.get(s), "avg7": avgs.get(s)}
                 for s, n, t, e in conn.execute("SELECT symbol, notional, entry_time, earned_usd FROM funding_desk_pos ORDER BY notional DESC, symbol")]
    table = []
    for b in bases:
        per = {v: rates[v][b] for v in VENUES if b in (rates.get(v) or {})}
        row = {"symbol": b, "rates": per, "avg7": avgs.get(b), "held": any(p["symbol"] == b for p in positions)}
        if len(per) >= 2:
            hi, lo = max(per, key=per.get), min(per, key=per.get); sp = per[hi] - per[lo]
            row["spread"] = {"short_on": hi, "long_on": lo, "per8h": round(sp, 8), "apr_pct": round(sp * 3 * 365 * 100, 2)}
        table.append(row)
    table.sort(key=lambda r: -(r.get("spread") or {}).get("per8h", -1))
    events = [{"time": t, "text": x} for t, x in conn.execute("SELECT time, text FROM funding_desk_events ORDER BY time DESC, rowid DESC LIMIT 40")]
    return {"asof": now, "name": NAME, "about": ABOUT, "note": NOTE, "venue": venue,
            "venues_ok": [v for v in VENUES if rates.get(v)],
            "equity": round(equity, 2), "start_equity": START_EQUITY, "pnl_usd": round(equity - START_EQUITY, 2),
            "pnl_pct": round((equity / START_EQUITY - 1) * 100, 3), "positions": positions, "history": hist, "events": events,
            "next_settlement": now - now % H8 + H8, "table": table,
            "rules": f"вход: средний фандинг за 7 дней > {ENTRY_THR*100:.2f}% за 8 ч; держим, пока > 0; до {MAX_COINS} монет; "
                     f"объём = капитал/{MAX_COINS}/{LEVER_K}; комиссия {COST*100:.2f}% при входе и выходе"}
