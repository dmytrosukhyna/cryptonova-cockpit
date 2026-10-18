"""Доход держателям — which tokens get real money from their protocol (18.10.2026; sector research: the strongest tokens of
2025–26 — HYPE, SKY, UNI, TRX, PUMP — are the ones whose protocol spends revenue on buybacks / burns / payouts to holders; ONDO
and most «narrative» tokens get nothing). Owner: «недооценённые монеты учитывать в перспективу; перегретые не подбирать».

Source: DefiLlama's free API — holders revenue (the part of fees that goes to token holders: buybacks, burns, staking payouts)
for every protocol, 30 days and 1 year, joined to the token symbol through the protocols list (symbol / gecko_id). Output
web/tokenomics.json: {symbol: {name, holders_rev_30d_usd, holders_rev_1y_usd, slug}} + the top list. spot_ideas.py reads it:
a coin whose holders receive money gets a score bonus and a line in its «i». Missing data = no bonus (never a guess).

Also (18.10, research: token unlocks are the best-documented edge — about 90 % of 16 000+ unlocks pushed the price down, from
about 30 days before; Keyrock): REALISED dilution. Unlock calendars are paid (DefiLlama API $300/month, Tokenomist), so we
measure what already happened: once a day the implied circulating supply (market cap ÷ price, CoinGecko top-150 from
markets.json) is stored; `supply_30d_pct` = its change over 30 days (needs ≥ 25 days of our own history). spot_ideas.py turns a
growth of 2 % or more into a fact line and a score penalty (never a gate).

Also (18.10, our own study scripts/unlocks_study.py, 287 unlocks 2024–2026 on Binance perps): UPCOMING unlocks from a calendar
we keep in the repo (scripts/lab/data/unlocks_upcoming.json, public sources, each row with its URL; refreshed by hand / by the
weekly review — `calendar_built` shows its age). Result of the study: the coin lagged BTC from 30 days before an unlock to the
unlock day in 2024–2025, the effect faded in 2026 for the average unlock, and held only for unlocks of ≥ 20 % of the
circulating supply (34 events: −15 % vs BTC on average, 3 of 4 cases). So `unlocks_next` {SYM: next unlock within 30 days} →
spot_ideas.py: ≥ 20 % = do not buy / add before the unlock (non-core coins); 5–20 % = a fact line only.
`unlock_watch` = a forward test with no money: for every calendar unlock ≥ 5 % we store the coin and BTC price when first seen
inside the 30-day window and on the unlock day; the summary shows how the coin did against BTC — is the effect still alive."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.request

DAY = 86_400_000
CALENDAR = os.path.join("scripts", "lab", "data", "unlocks_upcoming.json")
WINDOW_D = 30            # the study: the coin lags BTC from ~30 days before the unlock
BIG_PCT, WATCH_PCT = 20.0, 5.0

OVERVIEW = ("https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
            "&dataType=dailyHoldersRevenue")
PROTOCOLS = "https://api.llama.fi/protocols"


def _get(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "cnq-tokenomics/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def build(overview: dict, protocols: list) -> dict:
    """Join holders revenue (per protocol or parent) to token symbols; the biggest protocol wins a shared symbol."""
    sym_of = {}
    for p in protocols or []:
        s = (p.get("symbol") or "").upper()
        if not s or s == "-":
            continue
        for k in ("slug", "name", "parentProtocol"):
            v = p.get(k)
            if v:
                sym_of.setdefault(str(v).lower(), s)
    out = {}
    for e in (overview or {}).get("protocols") or []:
        r30, r1y = e.get("total30d"), e.get("total1y")
        if not r30 and not r1y:
            continue
        s = None
        for k in ("slug", "name", "displayName", "parentProtocol", "defillamaId"):
            v = e.get(k)
            if v and str(v).lower() in sym_of:
                s = sym_of[str(v).lower()]
                break
        if not s:
            continue
        cur = out.get(s)
        if cur and (cur["holders_rev_30d_usd"] or 0) >= (r30 or 0):
            continue
        out[s] = {"name": e.get("displayName") or e.get("name"), "slug": e.get("slug"), "holders_rev_30d_usd": round(r30 or 0),
                  "holders_rev_1y_usd": round(r1y or 0)}
    return out


def supply_growth(conn: sqlite3.Connection, markets: dict, now: int) -> dict:
    """Store today's implied circulating supply per coin; return {SYM: % change over ~30 days} where history allows."""
    conn.execute("CREATE TABLE IF NOT EXISTS tok_supply(day INTEGER, symbol TEXT, supply REAL, PRIMARY KEY(day, symbol))")
    day = now // DAY
    for r in (markets or {}).get("top") or []:
        s, mc, px = r.get("symbol"), r.get("mcap"), r.get("price")
        if s and mc and px and not r.get("stable"):
            conn.execute("INSERT OR IGNORE INTO tok_supply VALUES(?,?,?)", (day, s, float(mc) / float(px)))
    conn.commit()
    out = {}
    for s, sup in conn.execute("SELECT symbol, supply FROM tok_supply WHERE day = ?", (day,)).fetchall():
        old = conn.execute("SELECT day, supply FROM tok_supply WHERE symbol = ? AND day BETWEEN ? AND ? ORDER BY day LIMIT 1",
                           (s, day - 32, day - 25)).fetchone()
        if old and old[1]:
            g = sup / old[1] - 1
            if abs(g) < 2:                      # ignore data glitches (a redenomination, a wrong price)
                out[s] = round(g * 100, 2)
    return out


def _day_ms(date: str) -> int:
    import datetime as dt
    d = dt.date.fromisoformat(date)
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


def upcoming(cal: dict, now: int) -> dict:
    """{SYM: the biggest unlock in the next 30 days} — {date, days, pct_circ, event_class, recipients, usd_m, source}."""
    out = {}
    for e in (cal or {}).get("events") or []:
        s, date = (e.get("symbol") or "").upper(), e.get("date")
        if not s or not date:
            continue
        try:
            days = (_day_ms(date) - (now - now % DAY)) // DAY
        except ValueError:
            continue
        if not 0 <= days <= WINDOW_D:
            continue
        row = {"date": date, "days": int(days), "pct_circ": e.get("pct_circ"), "event_class": e.get("event_class"),
               "recipients": e.get("recipients"), "usd_m": e.get("usd_m"), "source": e.get("source")}
        cur = out.get(s)
        if cur is None or (row["pct_circ"] or 0) > (cur["pct_circ"] or 0):
            out[s] = row
    return out


def unlock_watch(conn: sqlite3.Connection, markets: dict, cal: dict, now: int) -> dict:
    """Forward test: coin vs BTC from first sight (≤ 30 days before) to the unlock day, for calendar unlocks ≥ 5 %."""
    conn.execute("CREATE TABLE IF NOT EXISTS unl_watch(symbol TEXT, date TEXT, pct REAL, seen INTEGER, px0 REAL, btc0 REAL, "
                 "px1 REAL, btc1 REAL, PRIMARY KEY(symbol, date))")
    px = {r.get("symbol"): r.get("price") for r in (markets or {}).get("top") or [] if r.get("price")}
    btc = px.get("BTC")
    today = now - now % DAY
    for e in (cal or {}).get("events") or []:
        s, date, pct = (e.get("symbol") or "").upper(), e.get("date"), e.get("pct_circ")
        if not s or not date or not pct or pct < WATCH_PCT or not btc or not px.get(s):
            continue
        try:
            t0 = _day_ms(date)
        except ValueError:
            continue
        if t0 - WINDOW_D * DAY <= today < t0:
            conn.execute("INSERT OR IGNORE INTO unl_watch(symbol, date, pct, seen, px0, btc0) VALUES(?,?,?,?,?,?)",
                         (s, date, pct, now, px[s], btc))
        elif t0 <= today < t0 + 3 * DAY:          # the unlock day (a late run still counts for 2 more days)
            conn.execute("UPDATE unl_watch SET px1 = ?, btc1 = ? WHERE symbol = ? AND date = ? AND px1 IS NULL", (px[s], btc, s, date))
    conn.commit()
    rows = conn.execute("SELECT symbol, date, pct, seen, px0, btc0, px1, btc1 FROM unl_watch ORDER BY date").fetchall()
    done, open_ = [], []
    for s, date, pct, seen, p0, b0, p1, b1 in rows:
        r = {"symbol": s, "date": date, "pct_circ": pct, "days_before_seen": max(0, (_day_ms(date) - (seen - seen % DAY)) // DAY)}
        if p1:
            r["vs_btc_pct"] = round(((p1 / p0) - (b1 / b0)) * 100, 1)
            done.append(r)
        else:
            open_.append(r)
    v = [r["vs_btc_pct"] for r in done]
    return {"resolved": len(done), "open": open_[-20:], "last": done[-20:],
            "avg_vs_btc_pct": round(sum(v) / len(v), 1) if v else None,
            "lagged_share_pct": round(sum(1 for x in v if x < 0) / len(v) * 100) if v else None}


def run(out: str = "web", fetch=_get, conn: sqlite3.Connection | None = None, now: int | None = None,
        calendar: str = CALENDAR) -> dict:
    now = now or int(time.time() * 1000)
    err = None
    try:
        tok = build(fetch(OVERVIEW), fetch(PROTOCOLS))
    except Exception as e:  # noqa: BLE001
        tok, err = {}, f"{type(e).__name__}: {str(e)[:120]}"
    growth, watch = {}, None
    try:
        cal = json.load(open(calendar, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cal = {}
    nxt = upcoming(cal, now)
    if conn is not None:
        try:
            markets = json.load(open(os.path.join(out, "markets.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            markets = {}
        growth = supply_growth(conn, markets, now)
        watch = unlock_watch(conn, markets, cal, now)
        for s, g in growth.items():
            tok.setdefault(s, {}).update({"supply_30d_pct": g})
    top = sorted([kv for kv in tok.items() if kv[1].get("holders_rev_30d_usd")], key=lambda kv: -kv[1]["holders_rev_30d_usd"])[:25]
    diluting = sorted([(s, v["supply_30d_pct"]) for s, v in tok.items() if (v.get("supply_30d_pct") or 0) >= 2], key=lambda x: -x[1])[:25]
    doc = {"asof": now, "error": err, "tokens": tok, "top": [{"symbol": s, **v} for s, v in top],
           "diluting": [{"symbol": s, "supply_30d_pct": g} for s, g in diluting], "supply_history_coins": len(growth),
           "unlocks_next": nxt, "calendar_built": cal.get("built"), "calendar_events": len(cal.get("events") or []),
           "unlock_watch": watch,
           "about_ru": "Доход держателям — часть комиссий протокола, которая идёт держателям токена: выкуп, сжигание или выплаты "
                       "стейкерам (DefiLlama, holders revenue). Токены, которым протокол реально платит, в 2025–2026 держались лучше "
                       "остальных. Нет данных — значит, держатели ничего не получают или DefiLlama это не считает."}
    if tok or nxt or not os.path.exists(os.path.join(out, "tokenomics.json")):    # a failed fetch keeps the last good file (supply rows are kept in the DB)
        with open(os.path.join(out, "tokenomics.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
    return doc
