"""Шорт листингов — a $10 000 demo desk that shorts new perpetual listings (18.10.2026, owner: «после листинга новые монеты сразу
обваливаются, люди шортят листинги на MEXC, Bybit и зарабатывают»).

Evidence (scripts/listings_study.py, every Binance USDT-M perp listed 2023-01 → 2026-10, 729 listings, delisted included, 1h data,
funding paid/received, 0.14 % costs, stop filled 1 % worse):
  • the price after listing: median −2 % after 1 day, −6 % after 7, −12 % after 30, −28 % after 90; 73 % lower after 90 days —
    but 10 % are up more than 50 % (the best ×140): the tail that kills shorts without a stop;
  • short 4 h after listing, stop +30 %, exit after 14 days: +2.6 % a trade on average, 55 % winners, worst −43 %; positive in
    every year (2023 +0.4 %, 2024 +0.9 %, 2025 +4.6 %, 2026 +2.5 % a trade); 5 % of the account per listing → about +55 % (2025)
    and +33 % (2026) a year. Chosen from 80 variants — some optimism is built in; the live desk is the real test.

Second pass (18.10, the same 729 listings, the chosen rule split by what is known at entry):
  • the first 4 h move: fell ≥ 10 % → +11.1 % a trade (2025–26 +11.5 %); −10…+10 % → +1.6 % (+2.3 %); ROSE ≥ 10 % → −1.6 %
    (−0.1 %) — shorting a listing that is being pumped gets squeezed → those are SKIPPED;
  • the first 4 h turnover: lowest third −0.9 % (+0.2 %), middle +2.0 % (+3.5 %), top third +6.8 % (+8.2 %) → thin ones are
    skipped (on BingX: < $2M traded by the entry — an absolute floor, BingX volumes differ from Binance's);
  • funding at entry and the BTC regime: positive either way — not used.

Live rules (BingX perpetuals — the venue our server reaches): a contract not seen before is a listing (its launchTime if BingX gives
one, else the first time this server saw it; the first run only remembers the existing list). 4 h after the listing (and not later
than 24 h — a stale one is skipped): short 5 % of the account at the market price, no leverage beyond 1×; stop +30 % from the
entry; exit after 14 days. At most 20 open. Every run (≈ 30 min): price ≥ stop → closed at that price (worse than the stop when
it gapped); a 24 h high above the stop on a trade older than 24 h → closed at the stop + 1 %; funding: a short receives a positive
rate and pays a negative one, every 8 h. Fees 0.05 % per side. Writes profiles["lst"] into web/paper.json so the panel shows it
next to the other test desks. Stdlib only, fetchers injectable for tests."""
from __future__ import annotations

import json
import os
import sqlite3
import time

from .history import thin
import urllib.request

BASE = "https://open-api.bingx.com/openApi/swap/v2/quote"
START = 10_000.0
DELAY_H, MAX_AGE_H, HOLD_D = 4, 24, 14
STOP, SHARE, MAX_OPEN = 0.30, 0.05, 20
SKIP_PUMP = 0.10                 # rose ≥ 10 % from the first seen price by the entry → not shorted (study: −1.6 % a trade)
MIN_QVOL = 2_000_000             # USDT traded on BingX by the entry (24 h figure = since listing) — thinner ones are skipped
FEE = 0.0005
H = 3_600_000
NAME = "Шорт листингов"
ABOUT = ("Шортим новые фьючерсы через 4 часа после листинга: 5% счёта на монету, стоп +30%, выход через 14 дней. Пропускаем "
         "те, что за первые часы выросли на 10% и больше (их выносит вверх) и с тонким оборотом. Проверка на "
         "729 листингах Binance 2023–2026 (с фандингом и комиссиями): цена через 30 дней в середине −12%, через 90 — −28%; шорт по этим "
         "правилам +2.6% на сделку, плюс в каждом году. Но каждая десятая монета улетает вверх больше чем на 50% — поэтому стоп "
         "обязателен. Демо-счёт $10 000, BingX.")


def _get(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 cryptonova/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def not_crypto(symbol: str) -> bool:
    """19.10: BingX lists synthetic pairs like NCCOXAU2TRY-USDT, NCFXEUR2NZD-USDT, NCSKCCI2USD-USDT (commodities, FX, stocks).
    The listing-short edge is about new TOKENS — those are skipped."""
    base = symbol.split("-")[0]
    return base.startswith("NC") and "2" in base and len(base) >= 7


def fetch_market() -> dict:
    """{symbol: {"launch": ms|None, "price": float|None, "high24": float|None, "funding": float|None}} for every BingX USDT perp."""
    ts = int(time.time() * 1000)
    out = {}
    for c in _get(f"{BASE}/contracts?timestamp={ts}").get("data") or []:
        s = c.get("symbol") or ""
        if s.endswith("-USDT") and str(c.get("status", 1)) in ("1", "True", "true"):
            lt = c.get("launchTime")
            out[s] = {"launch": int(lt) if lt not in (None, "", 0, "0") else None, "price": None, "high24": None, "funding": None, "qvol": None}
    for x in _get(f"{BASE}/ticker?timestamp={ts}").get("data") or []:
        if x.get("symbol") in out:
            out[x["symbol"]]["price"] = float(x["lastPrice"]) if x.get("lastPrice") not in (None, "", "0") else None
            out[x["symbol"]]["high24"] = float(x["highPrice"]) if x.get("highPrice") not in (None, "", "0") else None
            out[x["symbol"]]["qvol"] = float(x["quoteVolume"]) if x.get("quoteVolume") not in (None, "") else None
    for x in _get(f"{BASE}/premiumIndex?timestamp={ts}").get("data") or []:
        if x.get("symbol") in out and x.get("lastFundingRate") not in (None, ""):
            out[x["symbol"]]["funding"] = float(x["lastFundingRate"])
    return out


def _db(conn: sqlite3.Connection):
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS lst_known(symbol TEXT PRIMARY KEY, listed INTEGER, first_seen INTEGER, seeded INTEGER);
      CREATE TABLE IF NOT EXISTS lst_trades(id TEXT PRIMARY KEY, symbol TEXT, listed INTEGER, opened INTEGER, entry REAL, stop REAL,
        qty REAL, expires INTEGER, funding_usd REAL, last_t INTEGER, closed INTEGER, exit REAL, exit_kind TEXT, pnl_usd REAL);
      CREATE TABLE IF NOT EXISTS lst_equity(time INTEGER PRIMARY KEY, equity REAL);""")
    if "first_px" not in [r[1] for r in conn.execute("PRAGMA table_info(lst_known)")]:
        conn.execute("ALTER TABLE lst_known ADD COLUMN first_px REAL")


def step(conn: sqlite3.Connection, now: int | None = None, fetch=fetch_market) -> dict:
    now = now or int(time.time() * 1000)
    _db(conn)
    events, err = [], None
    try:
        mk = fetch()
    except Exception as e:  # noqa: BLE001
        mk, err = {}, f"{type(e).__name__}: {str(e)[:100]}"
    known = {r[0]: r for r in conn.execute("SELECT symbol, listed, first_seen, seeded FROM lst_known")}
    first_run = not known
    for s, m in mk.items():
        if s not in known:
            seeded = 1 if first_run else 0
            if not seeded and not_crypto(s):
                seeded = 4                                    # BingX's synthetic FX / commodity / stock pairs — not a token listing
            listed = m["launch"] if (m["launch"] and not seeded) else (None if seeded else now)
            if not seeded and m["launch"] and m["launch"] < now - 3 * 86_400_000:
                seeded = 1                                    # BingX says it launched long ago — not a fresh listing
            conn.execute("INSERT INTO lst_known(symbol, listed, first_seen, seeded, first_px) VALUES(?,?,?,?,?)",
                         (s, listed, now, seeded, m.get("price")))
            if not seeded:
                events.append(f"Новый фьючерс на BingX: {s.replace('-USDT', '')} — шорт через {DELAY_H} ч после листинга.")
    if first_run and mk:
        events.append(f"Запомнили {len(mk)} фьючерсов BingX; дальше каждый новый — кандидат на шорт.")
    realized = sum(r[0] or 0 for r in conn.execute("SELECT pnl_usd FROM lst_trades WHERE closed IS NOT NULL"))
    opens = [dict(zip(("id", "symbol", "listed", "opened", "entry", "stop", "qty", "expires", "funding_usd", "last_t"), r)) for r in
             conn.execute("SELECT id, symbol, listed, opened, entry, stop, qty, expires, funding_usd, last_t FROM lst_trades WHERE closed IS NULL")]
    # manage open shorts
    for t in opens:
        m = mk.get(t["symbol"]) or {}
        px, hi = m.get("price"), m.get("high24")
        if m.get("funding") is not None and t["last_t"]:
            f = t["entry"] * t["qty"] * m["funding"] * (now - t["last_t"]) / (8 * H)
            t["funding_usd"] = (t["funding_usd"] or 0) + f
        kind, exit_px = None, None
        if px and px >= t["stop"]:
            kind, exit_px = "стоп", px
        elif hi and hi >= t["stop"] and now - t["opened"] > 24 * H:
            kind, exit_px = "стоп", t["stop"] * 1.01
        elif now >= t["expires"] and px:
            kind, exit_px = "14 дней", px
        elif not m and now >= t["expires"]:
            kind, exit_px = "снят с торгов", t["entry"]          # delisted: no price — closed flat (logged)
        if kind:
            pnl = (t["entry"] - exit_px) * t["qty"] - (t["entry"] + exit_px) * t["qty"] * FEE + (t["funding_usd"] or 0)
            conn.execute("UPDATE lst_trades SET closed=?, exit=?, exit_kind=?, pnl_usd=?, funding_usd=? WHERE id=?",
                         (now, exit_px, kind, pnl, t["funding_usd"], t["id"]))
            realized += pnl
            events.append(f"Закрыт шорт {t['symbol'].replace('-USDT', '')}: {kind}, {pnl:+.2f}$.")
        else:
            conn.execute("UPDATE lst_trades SET funding_usd=?, last_t=? WHERE id=?", (t["funding_usd"], now, t["id"]))
    opens = [dict(zip(("id", "symbol", "listed", "opened", "entry", "stop", "qty", "expires", "funding_usd"), r)) for r in
             conn.execute("SELECT id, symbol, listed, opened, entry, stop, qty, expires, funding_usd FROM lst_trades WHERE closed IS NULL")]
    equity_closed = START + realized
    # open new shorts
    for (s,) in conn.execute("SELECT symbol FROM lst_known WHERE seeded = 0").fetchall():
        if not_crypto(s):
            conn.execute("UPDATE lst_known SET seeded = 4 WHERE symbol = ?", (s,))
    traded = {r[0] for r in conn.execute("SELECT symbol FROM lst_trades")}
    for s, listed, first_seen, seeded, first_px in conn.execute(
            "SELECT symbol, listed, first_seen, seeded, first_px FROM lst_known WHERE seeded = 0").fetchall():
        if s in traded or not listed or len(opens) >= MAX_OPEN:
            continue
        age = now - listed
        px = (mk.get(s) or {}).get("price")
        if age < DELAY_H * H or not px:
            continue
        if age > MAX_AGE_H * H:
            conn.execute("UPDATE lst_known SET seeded = 2 WHERE symbol = ?", (s,))      # missed the window (server was down) — skip
            events.append(f"{s.replace('-USDT', '')}: окно входа пропущено (листинг {age / H:.0f} ч назад) — не шортим.")
            continue
        if not first_px:                                # first price comes with the first run that sees a price
            conn.execute("UPDATE lst_known SET first_px = ? WHERE symbol = ?", (px, s))
            first_px = px
        mv = px / first_px - 1
        qv = (mk.get(s) or {}).get("qvol")
        why_skip = (f"выросла на {mv * 100:.0f}% с первой цены — такие листинги шортить невыгодно (на истории −1.6% на сделку)"
                    if mv >= SKIP_PUMP else
                    f"оборот ${qv / 1e6:.1f}M — тонкий рынок (на истории нижняя треть по обороту ≈ 0)" if qv is not None and qv < MIN_QVOL else None)
        if why_skip:
            conn.execute("UPDATE lst_known SET seeded = 3 WHERE symbol = ?", (s,))
            events.append(f"{s.replace('-USDT', '')}: не шортим — {why_skip}.")
            continue
        qty = equity_closed * SHARE / px
        tid = f"LST-{s}-{now // H}"
        conn.execute("INSERT INTO lst_trades VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL)",
                     (tid, s, listed, now, px, px * (1 + STOP), qty, now + HOLD_D * 86_400_000, 0.0, now))
        opens.append({"id": tid, "symbol": s, "listed": listed, "opened": now, "entry": px, "stop": px * (1 + STOP), "qty": qty,
                      "expires": now + HOLD_D * 86_400_000, "funding_usd": 0.0})
        events.append(f"Шорт {s.replace('-USDT', '')} по {px:g}: листинг {age / H:.1f} ч назад, стоп {px * (1 + STOP):g}, выход через {HOLD_D} дней.")
    open_pnl = 0.0
    out_open = []
    for t in opens:
        px = (mk.get(t["symbol"]) or {}).get("price") or t["entry"]
        u = (t["entry"] - px) * t["qty"] + (t["funding_usd"] or 0) - t["entry"] * t["qty"] * FEE
        open_pnl += u
        out_open.append({"id": t["id"], "symbol": t["symbol"].replace("-USDT", ""), "side": "SHORT", "grade": "LST", "kind": "LISTING_SHORT",
                         "entry": t["entry"], "stop": t["stop"], "qty": t["qty"], "qty_left": t["qty"], "opened": t["opened"], "pnl_usd": 0.0,
                         "expires": t["expires"], "price": px, "open_pnl_usd": round(u, 2), "funding_usd": round(t["funding_usd"] or 0, 2),
                         "listed": t["listed"], "reason": f"Шорт листинга: через {DELAY_H} ч после появления фьючерса; стоп +30%, выход через {HOLD_D} дней."})
    equity = equity_closed + open_pnl
    conn.execute("INSERT OR REPLACE INTO lst_equity VALUES(?, ?)", (now, round(equity, 2)))
    conn.commit()
    eqh = [{"time": a, "equity": b} for a, b in conn.execute("SELECT time, equity FROM lst_equity ORDER BY time")]
    peak, dd = START, 0.0
    for e in eqh:
        peak = max(peak, e["equity"]); dd = max(dd, (peak - e["equity"]) / peak)
    closed = [dict(zip(("id", "symbol", "opened", "entry", "closed", "exit", "exit_kind", "pnl_usd", "funding_usd"), r)) for r in
              conn.execute("SELECT id, symbol, opened, entry, closed, exit, exit_kind, pnl_usd, funding_usd FROM lst_trades WHERE closed IS NOT NULL ORDER BY closed DESC LIMIT 50")]
    for t in closed:
        t.update({"symbol": t["symbol"].replace("-USDT", ""), "side": "SHORT", "grade": "LST", "net_usd": round(t["pnl_usd"], 2)})
    n = conn.execute("SELECT COUNT(*) FROM lst_trades WHERE closed IS NOT NULL").fetchone()[0]
    wins = conn.execute("SELECT COUNT(*) FROM lst_trades WHERE closed IS NOT NULL AND pnl_usd > 0").fetchone()[0]
    avg_trade = conn.execute("SELECT AVG(pnl_usd / (qty * entry)) FROM lst_trades WHERE closed IS NOT NULL AND qty > 0 AND entry > 0").fetchone()[0]
    first = conn.execute("SELECT MIN(time) FROM lst_equity").fetchone()[0] or now
    # 19.10: the paper-desk convention — `equity` = closed trades only, open P&L apart (the panel adds open positions at live prices;
    # equity_mtm = both, for the history, the allocator and the turbo mirror)
    return {"asof": now, "profile": "lst", "name": NAME, "about": ABOUT, "start_equity": START, "since": first, "reset": None,
            "equity": round(equity_closed, 2), "equity_mtm": round(equity, 2),
            "pnl_usd": round(equity - START, 2), "pnl_pct": round((equity / START - 1) * 100, 2),
            "open_pnl_usd": round(open_pnl, 2), "open": out_open, "pending": [], "closed": closed,
            "stats": {"trades": n, "winrate": round(wins / n, 3) if n else None, "max_dd_pct": round(dd * 100, 2),
                      "best": max((t["net_usd"] for t in closed), default=0), "worst": min((t["net_usd"] for t in closed), default=0),
                      "avg_trade_pct": round(avg_trade * 100, 2) if avg_trade is not None else None},
            "equity_history": thin(eqh), "events": events, "error": err, "watching": len(mk),
            "rules": f"шорт через {DELAY_H} ч после листинга, {int(SHARE * 100)}% счёта, стоп +{int(STOP * 100)}%, выход через {HOLD_D} дней, "
                     f"до {MAX_OPEN} позиций, комиссия 0.05%/сторона, фандинг учитывается"}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, fetch=fetch_market) -> dict:
    """Runs the desk and puts it into web/paper.json → profiles["lst"] (+ profile_list) for the panel."""
    doc = step(conn, now, fetch)
    p = os.path.join(out, "paper.json")
    try:
        paper = json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        paper = None
    if paper is not None:
        paper.setdefault("profiles", {})["lst"] = doc
        pl = [x for x in paper.get("profile_list") or [] if x.get("key") != "lst"]
        pl.append({"key": "lst", "name": NAME, "about": ABOUT, "risk": SHARE, "classes": ["LST"], "max_open": MAX_OPEN})
        paper["profile_list"] = pl
        with open(p, "w", encoding="utf-8") as f:
            json.dump(paper, f, ensure_ascii=False)
    return doc
