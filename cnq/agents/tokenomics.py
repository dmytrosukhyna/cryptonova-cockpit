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
growth of 2 % or more into a fact line and a score penalty (never a gate). Stdlib only."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.request

DAY = 86_400_000

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


def run(out: str = "web", fetch=_get, conn: sqlite3.Connection | None = None, now: int | None = None) -> dict:
    now = now or int(time.time() * 1000)
    err = None
    try:
        tok = build(fetch(OVERVIEW), fetch(PROTOCOLS))
    except Exception as e:  # noqa: BLE001
        tok, err = {}, f"{type(e).__name__}: {str(e)[:120]}"
    growth = {}
    if conn is not None:
        try:
            markets = json.load(open(os.path.join(out, "markets.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            markets = {}
        growth = supply_growth(conn, markets, now)
        for s, g in growth.items():
            tok.setdefault(s, {}).update({"supply_30d_pct": g})
    top = sorted([kv for kv in tok.items() if kv[1].get("holders_rev_30d_usd")], key=lambda kv: -kv[1]["holders_rev_30d_usd"])[:25]
    diluting = sorted([(s, v["supply_30d_pct"]) for s, v in tok.items() if (v.get("supply_30d_pct") or 0) >= 2], key=lambda x: -x[1])[:25]
    doc = {"asof": now, "error": err, "tokens": tok, "top": [{"symbol": s, **v} for s, v in top],
           "diluting": [{"symbol": s, "supply_30d_pct": g} for s, g in diluting], "supply_history_coins": len(growth),
           "about_ru": "Доход держателям — часть комиссий протокола, которая идёт держателям токена: выкуп, сжигание или выплаты "
                       "стейкерам (DefiLlama, holders revenue). Токены, которым протокол реально платит, в 2025–2026 держались лучше "
                       "остальных. Нет данных — значит, держатели ничего не получают или DefiLlama это не считает."}
    if tok or not os.path.exists(os.path.join(out, "tokenomics.json")):    # a failed fetch keeps the last good file (supply rows are kept in the DB)
        with open(os.path.join(out, "tokenomics.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
    return doc
