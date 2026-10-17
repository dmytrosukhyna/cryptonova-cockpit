"""Funding desk — paper "funding carry" account ($10 000), docs/research/funding_carry.md, scripts/funding_carry_test.py.

Strategy (delta-neutral): for a selected coin we hold LONG spot + SHORT perpetual of equal notional. Price moves cancel,
and while funding is positive the short side receives notional × funding at every settlement (00/08/16 UTC).
  • entry: average funding over the last 7 days = the 21 settlements (rate recorded last before each one) ≥ 0.01% per 8 h,
    and at least 18 of those 21 settlements must be known — a coin is never entered on a few hours of data. Missing history
    is filled once from the venue's settled-funding endpoint (`history=`). The ≥ is explicit with a tolerance: a coin that
    sits at the 0.01% floor for 7 days qualifies (backtest: better than "strictly above" in every year 2023–2026);
  • keep while that average stays > 0 (hysteresis, otherwise fees eat everything); fewer than 3 known settlements → keep;
  • up to 6 coins, equal capital: capital/coin = equity/6, notional = capital/1.2 (spot leg + perp margin at 5×);
  • costs: 0.15% of notional on entry and 0.15% on exit (both legs together) — 0.30% round trip = 30 settlements (10 days)
    of the 0.01% floor rate. Notional size does not change the % result: funding and fees both scale with it.
  • price leg: long spot / short perp do not cancel exactly. We mark it with the venue's index price (≈ spot) and mark price
    (perp): result = notional × (index/index₀ − mark/mark₀); shown open, realised into equity on exit.
Every run records the current funding rate of each venue (Binance, Bybit, OKX, BingX, Hyperliquid; normalised to a fraction
per 8 h) into funding_hist; the account uses ONE reference venue (Binance, else Bybit, else BingX — chosen once, recorded).
A settlement is credited with the rate recorded closest before it (no record within 8 h → skipped, and said so).
Ledger identity (meta): equity = 10 000 + funding − fees + realised price leg.

Honesty caveats: index ≠ the exact spot fill and mark ≠ the exact perp fill; borrow, transfers and liquidation of the perp leg
on a spike are NOT modelled. The recorded rate is the venue's live estimate shortly before settlement, not the settled print
(except history filled from the settled endpoint). Some venues settle some coins every 4 h or 1 h; rates are normalised per 8 h
but crediting assumes 8-hour settlements. The research found ≈ +5% a year on capital in 2023–2026, but only ≈ +1% in 2025 and
≈ 0 in 2026. Stdlib only; all network calls are injectable (`fetch=`, `history=`, `prices=`)."""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 cryptonova/1.0", "Content-Type": "application/json"}
START_EQUITY = 10_000.0
MAX_COINS, LEVER_K, COST = 6, 1.2, 0.0015
ENTRY_THR, EXIT_THR = 0.0001, 0.0
EPS = 1e-9                              # rates are compared with a tolerance: an average of 0.0001 prints is 0.00010000000000000002
LOOK = 21                               # settlements in the look-back (7 days × 3)
MIN_SETTLED, MIN_EXIT_SETTLED = 18, 3   # known settlements needed to enter / to judge an exit
BOOT_RETRY = 12 * 3_600_000             # history back-fill per coin at most every 12 h
H8, DAY = 8 * 3_600_000, 86_400_000
BUCKET = 15 * 60_000                    # funding_hist dedupe: one record per venue/coin per 15 minutes
VENUES = ("binance", "bybit", "okx", "bingx", "hyperliquid")
REF_ORDER = ("binance", "bybit", "bingx")
NAME = "Фандинг · без направления"
ABOUT = ("Покупаем монету на споте и одновременно продаём такой же объём фьючерса. Цена монеты на результат почти не влияет: "
         "что теряем на одной ноге, получаем на другой. Зарабатываем на фандинге — каждые 8 часов покупатели фьючерса платят "
         "продавцам, когда рынок перегрет в лонг. Берём до 6 монет, где средний фандинг за 7 дней (21 выплата) не ниже 0.01% "
         "за 8 ч, и держим, пока он остаётся положительным. История 2023–2026: ≈ +5% в год при очень маленькой просадке, но в "
         "2025–2026 только ≈ +1% в год — фандинг на крупных монетах сжался. Это учебный счёт на $10 000, без реальных денег.")
NOTE = ("Ценовую ногу (спот против фьючерса) считаем по индексной цене биржи (≈ спот) и маркировочной цене фьючерса — это "
        "приближение, точные цены исполнения могут отличаться. Комиссия 0.15% от объёма при входе и столько же при выходе: "
        "круг 0.30% окупается 30 выплатами по 0.01% (10 дней). Начисляем ставку, записанную последней перед выплатой (оценка "
        "биржи, не итоговая ставка). Межбиржевой спред — только идея: шорт фьючерса там, где фандинг выше, лонг там, где "
        "ниже; комиссии и переводы в нём не учтены.")


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


_RAW: dict[str, tuple[float, object]] = {}     # venue → (fetched at, payload): price marks reuse the funding response


def _cached(venue: str, url: str):
    d = _get(url); _RAW[venue] = (time.time(), d)
    return d


def _binance(bases):
    try:
        want = {b + "USDT": b for b in bases}
        return {want[d["symbol"]]: float(d["lastFundingRate"]) for d in _cached("binance", "https://fapi.binance.com/fapi/v1/premiumIndex")
                if d.get("symbol") in want and d.get("lastFundingRate") not in (None, "") and _ok(float(d["lastFundingRate"]))}
    except Exception:  # noqa: BLE001
        return {}


def _bybit(bases):
    try:
        want = {b + "USDT": b for b in bases}; out = {}
        for d in _cached("bybit", "https://api.bybit.com/v5/market/tickers?category=linear")["result"]["list"]:
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
        d = _cached("bingx", f"https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex?timestamp={int(time.time() * 1000)}")
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


def fetch_history(venue: str, sym: str, start: int, end: int) -> list[tuple[int, float]]:
    """Settled funding prints [(settlement time ms, rate per the coin's own interval)] of the reference venue; [] on failure."""
    try:
        if venue == "binance":
            d = _get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}USDT&startTime={start}&endTime={end}&limit=200")
            return [(int(x["fundingTime"]), float(x["fundingRate"])) for x in d]
        if venue == "bybit":
            d = _get(f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}USDT&startTime={start}&endTime={end}&limit=200")
            return [(int(x["fundingRateTimestamp"]), float(x["fundingRate"])) for x in d["result"]["list"]]
        if venue == "bingx":
            time.sleep(1.1)                 # BingX public market endpoints: 1 request per second per IP
            d = _get(f"https://open-api.bingx.com/openApi/swap/v2/quote/fundingRate?symbol={sym}-USDT&startTime={start}&endTime={end}"
                     f"&limit=200&timestamp={int(time.time() * 1000)}")
            return [(int(x["fundingTime"]), float(x["fundingRate"])) for x in d.get("data") or []]
    except Exception:  # noqa: BLE001
        pass
    return []


def fetch_prices(venue: str, bases: list[str]) -> dict[str, tuple[float, float]]:
    """{base: (index price ≈ spot, mark price ≈ perp)} from the reference venue — reuses the funding response when fresh."""
    urls = {"binance": "https://fapi.binance.com/fapi/v1/premiumIndex", "bybit": "https://api.bybit.com/v5/market/tickers?category=linear",
            "bingx": "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex"}
    if venue not in urls:
        return {}
    try:
        t, d = _RAW.get(venue, (0.0, None))
        if d is None or time.time() - t > 120:
            d = _get(urls[venue] + (f"?timestamp={int(time.time() * 1000)}" if venue == "bingx" else ""))
        rows = d["result"]["list"] if venue == "bybit" else (d.get("data") or [] if venue == "bingx" else d)
        sep = "-" if venue == "bingx" else ""
        want = {b + sep + "USDT": b for b in bases}; out = {}
        for x in rows:
            if x.get("symbol") in want:
                try:
                    i, m = float(x["indexPrice"]), float(x["markPrice"])
                    if i > 0 and m > 0 and abs(m / i - 1) < 0.05: out[want[x["symbol"]]] = (i, m)
                except Exception:  # noqa: BLE001
                    pass
        return out
    except Exception:  # noqa: BLE001
        return {}


# ── storage ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS funding_hist(venue TEXT, symbol TEXT, time INTEGER, rate REAL, PRIMARY KEY(venue, symbol, time));
        CREATE TABLE IF NOT EXISTS funding_desk_pos(symbol TEXT PRIMARY KEY, notional REAL, entry_time INTEGER, earned_usd REAL);
        CREATE TABLE IF NOT EXISTS funding_desk_equity(time INTEGER PRIMARY KEY, equity REAL);
        CREATE TABLE IF NOT EXISTS funding_desk_events(time INTEGER, text TEXT);
        CREATE TABLE IF NOT EXISTS funding_desk_meta(k TEXT PRIMARY KEY, v TEXT);""")
    have = {r[1] for r in conn.execute("PRAGMA table_info(funding_desk_pos)")}
    for col in ("entry_index REAL", "entry_mark REAL", "basis_from INTEGER", "price_usd REAL"):
        if col.split()[0] not in have:
            conn.execute(f"ALTER TABLE funding_desk_pos ADD COLUMN {col}")


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


def _rate_before(conn, venue: str, sym: str, t: int) -> float | None:
    r = conn.execute("SELECT rate FROM funding_hist WHERE venue=? AND symbol=? AND time<? AND time>=? ORDER BY time DESC LIMIT 1",
                     (venue, sym, t, t - H8)).fetchone()
    return r[0] if r else None


def settled_avg(conn, venue: str, sym: str, now: int) -> tuple[float | None, int]:
    """Average of the rate in force at each of the last 21 settlements (7 days) and how many of them are known."""
    last = now - now % H8
    rs = [r for r in (_rate_before(conn, venue, sym, last - k * H8) for k in range(LOOK)) if r is not None]
    return (sum(rs) / len(rs) if rs else None), len(rs)


def avg7(conn, venue: str, sym: str, now: int, current: float | None = None) -> float | None:
    return settled_avg(conn, venue, sym, now)[0]


def _normalise(prints) -> list[tuple[int, float]]:
    """Settled prints per the coin's own interval → per 8 h (interval = median gap between prints, 0.5…8 h)."""
    pr = sorted(set((int(t), float(r)) for t, r in prints))
    gaps = sorted(b[0] - a[0] for a, b in zip(pr, pr[1:]) if b[0] > a[0])
    hrs = gaps[len(gaps) // 2] / 3_600_000 if gaps else 8.0
    k = 8.0 / hrs if 0.5 <= hrs <= 8 else 1.0
    return [(t, r * k) for t, r in pr if _ok(r * k)]


def backfill(conn, venue: str, syms, now: int, history) -> int:
    """Fill missing settlements from the venue's settled-funding history (stored 1 ms before each settlement, so the
    settlement average and crediting pick the settled print). Each coin is tried at most every 12 h."""
    n = 0
    for sym in sorted(syms):
        key = f"boot:{venue}:{sym}"
        if now - int(_meta(conn, key, 0)) < BOOT_RETRY:
            continue
        _set(conn, key, now)
        for t, r in _normalise(history(venue, sym, now - 7 * DAY - H8, now) or []):
            if now - 7 * DAY - H8 <= t <= now:
                conn.execute("INSERT OR IGNORE INTO funding_hist VALUES(?,?,?,?)", (venue, sym, t - 1, r)); n += 1
    return n


def _pct(r: float) -> str:
    return f"{r * 100:.3f}%"


def _usd(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}${abs(x):,.2f}"


def _pl(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n)); w = one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many
    return f"{n} {w}"


# ── the account ────────────────────────────────────────────────────────────────────────────────────────────────────────

LEDGER = ("funding_total", "fees_total", "price_total", "settlements", "entries", "exits")


def run(conn: sqlite3.Connection, now: int | None = None, fetch=fetch_funding, bases: list[str] | None = None,
        history=None, prices=None) -> dict:
    now = now or int(time.time() * 1000)
    bases = bases or default_bases()
    live = fetch is fetch_funding                       # injected fetch (tests) → no other network either, unless given
    history = history or (fetch_history if live else (lambda *a: []))
    prices = prices or (fetch_prices if live else (lambda *a: {}))
    _ensure(conn)
    rates = fetch(bases) or {}
    record(conn, rates, now)
    ev: list[tuple[int, str]] = []
    equity = float(_meta(conn, "equity", START_EQUITY))
    pos = {s: {"notional": n, "entry_time": t, "earned_usd": e, "entry_index": i0, "entry_mark": m0, "basis_from": bf, "price_usd": pu or 0.0}
           for s, n, t, e, i0, m0, bf, pu in conn.execute(
               "SELECT symbol, notional, entry_time, earned_usd, entry_index, entry_mark, basis_from, price_usd FROM funding_desk_pos")}

    # ledger: equity = start + funding − fees + realised price leg. An account opened before the ledger existed had no exits,
    # so everything that is not funding was fees.
    if _meta(conn, "funding_total") is None:
        f0 = sum(p["earned_usd"] for p in pos.values())
        cnt = lambda pfx: conn.execute("SELECT COUNT(*) FROM funding_desk_events WHERE text LIKE ?", (pfx + "%",)).fetchone()[0]
        for k, v in (("funding_total", f0), ("fees_total", START_EQUITY + f0 - equity), ("price_total", 0.0),
                     ("settlements", cnt("Начислен фандинг")), ("entries", cnt("Открыли:")), ("exits", cnt("Закрыли:"))):
            _set(conn, k, v)
    led = {k: float(_meta(conn, k, 0)) for k in LEDGER}
    if _meta(conn, "rules_v") != "2":
        if conn.execute("SELECT 1 FROM funding_desk_equity LIMIT 1").fetchone():
            ev.append((now, "Правила уточнены: входим, только если фандинг не ниже 0.01% за 8 ч в среднем по последним 21 выплате "
                            "(7 дней) и известны хотя бы 18 из них. Раньше «средний за 7 дней» считался по тем часам, что успели "
                            "записаться, поэтому счёт мог войти через несколько часов после запуска. Открытые позиции оставлены — "
                            "комиссия за вход уже уплачена, выход стоил бы ещё столько же. Ценовая нога теперь считается."))
        _set(conn, "rules_v", "2")

    venue = _meta(conn, "venue")
    if venue is None or (not rates.get(venue) and not pos):          # sticky once positions exist
        venue = next((v for v in REF_ORDER if rates.get(v)), venue)
        if venue: _set(conn, "venue", venue)
    cur = rates.get(venue, {}) if venue else {}
    universe = sorted(set(bases) | set(pos))

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
            led["funding_total"] += tot; led["settlements"] += 1
            ev.append((s, f"Начислен фандинг {'+' if tot >= 0 else '−'}${abs(tot):.2f}" + (f" (нет ставки для {', '.join(miss)} — пропущено)" if miss else "")))
        last = s; s += H8
    _set(conn, "last_settled", last)

    # 2) price leg of open positions: long spot (index) against short perp (mark)
    px = (prices(venue, universe) or {}) if venue else {}
    for sym, p in pos.items():
        q = px.get(sym)
        if not q: continue
        if not p["entry_index"] or not p["entry_mark"]:          # opened before the price leg was tracked → counted from now
            p["entry_index"], p["entry_mark"], p["basis_from"] = q[0], q[1], now
        p["price_usd"] = p["notional"] * (q[0] / p["entry_index"] - q[1] / p["entry_mark"])

    # 3) re-select coins (only with live data from the reference venue)
    if venue and cur:
        need = [b for b in universe if settled_avg(conn, venue, b, now)[1] < MIN_SETTLED]
        if need: backfill(conn, venue, need, now, history)
    avg = {b: settled_avg(conn, venue, b, now) for b in universe} if venue else {}
    if cur:
        def keep(sym):
            a, n = avg.get(sym, (None, 0))
            return a is None or n < MIN_EXIT_SETTLED or a > EXIT_THR + EPS
        for sym in [x for x in pos if not keep(x)]:
            p = pos.pop(sym); c = p["notional"] * COST; pr = p["price_usd"] or 0.0
            equity += pr - c; led["fees_total"] += c; led["price_total"] += pr; led["exits"] += 1
            conn.execute("DELETE FROM funding_desk_pos WHERE symbol=?", (sym,))
            ev.append((now, f"Закрыли: {sym} — средний фандинг {_pct(avg[sym][0])} за 8 ч, уже не платит. Комиссия −${c:.2f}, "
                            f"ценовая нога {_usd(pr)}, фандинг за всё время {_usd(p['earned_usd'])}"))
        size = equity / MAX_COINS / LEVER_K                           # equal capital per coin, from equity before this run's entries
        cands = [b for b in bases if avg.get(b, (None, 0))[0] is not None and avg[b][1] >= MIN_SETTLED and cur.get(b) is not None]
        for sym in sorted(cands, key=lambda b: -avg[b][0]):
            if len(pos) >= MAX_COINS: break
            if sym in pos or avg[sym][0] < ENTRY_THR - EPS: continue
            n = size; c = n * COST; equity -= c; led["fees_total"] += c; led["entries"] += 1
            q = px.get(sym)
            pos[sym] = {"notional": n, "entry_time": now, "earned_usd": 0.0, "entry_index": q[0] if q else None,
                        "entry_mark": q[1] if q else None, "basis_from": now if q else None, "price_usd": 0.0}
            ev.append((now, f"Открыли: {sym} — спот куплен, фьючерс продан на ${n:,.0f}, фандинг в среднем {_pct(avg[sym][0])} за 8 ч "
                            f"по {avg[sym][1]} последним выплатам. Комиссия −${c:.2f}"))
    elif venue is None and (conn.execute("SELECT text FROM funding_desk_events ORDER BY time DESC LIMIT 1").fetchone() or [""])[0][:12] != "Биржи не отв":
        ev.append((now, "Биржи не ответили — ставки фандинга не получены, счёт без изменений"))

    for sym, p in pos.items():
        conn.execute("INSERT OR REPLACE INTO funding_desk_pos(symbol, notional, entry_time, earned_usd, entry_index, entry_mark, basis_from, price_usd) "
                     "VALUES(?,?,?,?,?,?,?,?)", (sym, p["notional"], p["entry_time"], p["earned_usd"], p["entry_index"], p["entry_mark"],
                                                 p["basis_from"], p["price_usd"]))
    for k, v in led.items():
        _set(conn, k, v)
    _set(conn, "equity", equity)
    conn.execute("INSERT OR REPLACE INTO funding_desk_equity VALUES(?,?)", (now, round(equity, 4)))
    conn.executemany("INSERT INTO funding_desk_events VALUES(?,?)", ev)
    conn.execute("DELETE FROM funding_desk_events WHERE time < ?", (now - 30 * DAY,))
    conn.commit()
    return snapshot(conn, now, rates, venue, avg, bases)


def _date(t: int) -> str:
    return time.strftime("%d.%m %H:%M UTC", time.gmtime(t / 1000))


def explain(b: dict, positions: list[dict]) -> str:
    """Plain-Russian account of where the result comes from, built only from the ledger numbers."""
    if not b["entries"] and not positions:
        return ("Счёт пока не входил в позиции: ни одна монета не держала фандинг не ниже 0.01% за 8 ч все последние 7 дней. "
                "Денег не потрачено — ждём. Больший объём процент не меняет: фандинг и комиссия растут вместе с объёмом.")
    exits = f", {_pl(b['exits'], 'выход', 'выхода', 'выходов')}" if b["exits"] else ""
    parts = [f"С {_date(b['since'])} счёт получил фандинг {_usd(b['funding_usd'])} за {_pl(b['settlements'], 'выплату', 'выплаты', 'выплат')} "
             f"и заплатил комиссий −${b['fees_usd']:,.2f} ({_pl(b['entries'], 'вход', 'входа', 'входов')}{exits}, по 0.15% от объёма)."]
    pl = b["price_realized_usd"] + b["price_open_usd"]
    parts.append(f"Ценовая нога (спот против фьючерса): {_usd(pl)}" +
                 (f" — считаем с {_date(b['price_from'])}, раньше она не учитывалась." if b.get("price_from") else "."))
    parts.append(f"Итого {_usd(b['pnl_usd'])} к $10 000 (из них фандинг {_usd(b['funding_usd'])}, комиссии −${b['fees_usd']:,.2f}, "
                 f"цена {_usd(pl)}).")
    if positions:
        parts.append(f"Если закрыть всё сейчас, добавится комиссия за выход −${b['exit_fees_usd']:,.2f}: было бы {_usd(b['if_closed_usd'])}.")
        if b["daily_funding_usd"] > 0:
            be = b["breakeven_days"]
            parts.append(f"Сейчас {_pl(len(positions), 'позиция', 'позиции', 'позиций')} по $" + f"{positions[0]['notional']:,.0f}".replace(",", " ") + f" приносят ≈ ${b['daily_funding_usd']:.2f} в день "
                         f"(≈ {b['avg_rate_8h'] * 100:.4f}% за 8 ч)" +
                         (f" — минус вместе с выходом окупится примерно за {be:.0f} дн., если ставки не упадут." if be else " — счёт уже в плюсе с учётом выхода."))
        else:
            parts.append("Сейчас фандинг по открытым позициям не положительный — окупаемости нет, ждём выхода по правилу.")
    parts.append("Больший объём процент не меняет: фандинг и комиссия пропорциональны объёму — ×10 денег дало бы ×10 и плюса, и минуса.")
    return " ".join(parts)


def snapshot(conn, now, rates, venue, avg, bases) -> dict:
    equity = float(_meta(conn, "equity", START_EQUITY))
    eqh = conn.execute("SELECT time, equity FROM funding_desk_equity ORDER BY time").fetchall()
    step = max(1, -(-len(eqh) // 500))
    hist = [[t, round(e, 2)] for t, e in eqh[::step]]
    if eqh and hist[-1][0] != eqh[-1][0]: hist = (hist + [[eqh[-1][0], round(eqh[-1][1], 2)]])[-500:]
    cur = rates.get(venue, {}) if venue else {}
    avgs = {b: a for b, (a, n) in avg.items()}
    positions = []
    for s, n, t, e, bf, pu in conn.execute("SELECT symbol, notional, entry_time, earned_usd, basis_from, price_usd FROM funding_desk_pos ORDER BY notional DESC, symbol"):
        a, k = avg.get(s, (None, 0))
        positions.append({"symbol": s, "notional": round(n, 2), "entry_time": t, "earned_usd": round(e, 2), "rate_now": cur.get(s), "avg7": a,
                          "avg7_n": k, "price_usd": round(pu or 0.0, 2), "price_from": bf, "entry_fee_usd": round(n * COST, 2),
                          "days_held": round((now - t) / DAY, 2)})
    table = []
    for b in bases:
        per = {v: rates[v][b] for v in VENUES if b in (rates.get(v) or {})}
        row = {"symbol": b, "rates": per, "avg7": avgs.get(b), "avg7_n": (avg.get(b) or (None, 0))[1], "held": any(p["symbol"] == b for p in positions)}
        if len(per) >= 2:
            hi, lo = max(per, key=per.get), min(per, key=per.get); sp = per[hi] - per[lo]
            row["spread"] = {"short_on": hi, "long_on": lo, "per8h": round(sp, 8), "apr_pct": round(sp * 3 * 365 * 100, 2)}
        table.append(row)
    table.sort(key=lambda r: -(r.get("spread") or {}).get("per8h", -1))
    events = [{"time": t, "text": x} for t, x in conn.execute("SELECT time, text FROM funding_desk_events ORDER BY time DESC, rowid DESC LIMIT 40")]

    led = {k: float(_meta(conn, k, 0)) for k in LEDGER}
    open_px = sum(p["price_usd"] for p in positions)
    exit_fees = sum(p["notional"] * COST for p in positions)
    rate8 = [(p["notional"], p["avg7"] if p["avg7"] is not None else (p["rate_now"] or 0.0)) for p in positions]
    per_day = sum(n * r * 3 for n, r in rate8)
    if_closed = equity + open_px - exit_fees
    hole = START_EQUITY - if_closed
    froms = [p["price_from"] for p in positions if p["price_from"] and p["price_from"] > p["entry_time"]]
    breakdown = {"since": eqh[0][0] if eqh else now, "days": round((now - (eqh[0][0] if eqh else now)) / DAY, 2),
                 "funding_usd": round(led["funding_total"], 2), "fees_usd": round(led["fees_total"], 2),
                 "price_realized_usd": round(led["price_total"], 2), "price_open_usd": round(open_px, 2),
                 "price_from": max(froms) if froms else None, "pnl_usd": round(equity + open_px - START_EQUITY, 2),
                 "exit_fees_usd": round(exit_fees, 2), "if_closed_usd": round(if_closed - START_EQUITY, 2),
                 "settlements": int(led["settlements"]), "entries": int(led["entries"]), "exits": int(led["exits"]),
                 "daily_funding_usd": round(per_day, 2), "avg_rate_8h": (sum(n * r for n, r in rate8) / sum(n for n, _ in rate8)) if rate8 else None,
                 "breakeven_days": round(hole / per_day, 1) if hole > 0 and per_day > 0 else None,
                 "roundtrip_cost_pct": COST * 2 * 100, "floor_breakeven_days": round(COST * 2 / ENTRY_THR / 3, 1)}
    return {"asof": now, "name": NAME, "about": ABOUT, "note": NOTE, "venue": venue,
            "venues_ok": [v for v in VENUES if rates.get(v)],
            "equity": round(equity, 2), "equity_mtm": round(equity + open_px, 2), "start_equity": START_EQUITY, "pnl_usd": round(equity - START_EQUITY, 2),
            "pnl_pct": round((equity / START_EQUITY - 1) * 100, 3), "positions": positions, "history": hist, "events": events,
            "next_settlement": now - now % H8 + H8, "table": table, "breakdown": breakdown, "explain_ru": explain(breakdown, positions),
            "rules": f"вход: фандинг не ниже {ENTRY_THR*100:.2f}% за 8 ч в среднем по последним {LOOK} выплатам (7 дней, нужно ≥{MIN_SETTLED} известных); "
                     f"держим, пока > 0; до {MAX_COINS} монет; объём = капитал/{MAX_COINS}/{LEVER_K}; комиссия {COST*100:.2f}% при входе и выходе "
                     f"(круг {COST*200:.2f}% = {COST*2/ENTRY_THR:.0f} выплат по 0.01%)"}
