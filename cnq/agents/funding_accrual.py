"""Funding on the demo perpetual desks (22.10.2026 — audit 20.10, «simulation defects flatter the demo record»: funding was omitted).

Perpetual futures settle funding every 8 hours (00:00, 08:00, 16:00 UTC). At a settlement a LONG pays rate × notional and a SHORT
receives it (a negative rate reverses the direction). The demo desks hold for hours to weeks, so leaving it out made longs in a
positive-funding market look better than they were.

How a rate is chosen for a settlement (the honest order):
  1. the exchange's settled history for that coin and time (the market store's `funding` table — BingX, refreshed hourly by
     the engine; passed in as coin["funding"] = [[time_ms, rate], …]);
  2. otherwise, only while the settlement is recent (≤ 2 h), the live last-settled rate (BingX premiumIndex lastFundingRate,
     coin["funding_now"]);
  A settlement younger than 70 min waits for the stored number (the next run charges it).
  3. otherwise nothing is invented: the settlement is recorded as «missing» with 0 $ and counted, so the desk can say so.

Every settlement is written once to `funding_ledger` (desk, trade, time) — append-only, so a re-run never charges twice and the
record can be audited. Stdlib only.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request

PERIOD_MS = 8 * 3_600_000
MATCH_MS = 5 * 60_000            # a stored settlement counts for a boundary within ±5 min (exchange timestamps vary by a few s)
LIVE_MAX_AGE_MS = 2 * 3_600_000  # the live rate stands in for a settlement at most 2 h old
WAIT_MS = 70 * 60_000            # the store refreshes settled funding hourly: wait that long for the real number before the live one


def ensure(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS funding_ledger(desk TEXT, trade_id TEXT, ts INTEGER, symbol TEXT, side TEXT,
                    rate REAL, notional REAL, usd REAL, src TEXT, PRIMARY KEY(desk, trade_id, ts))""")


def boundaries(start_ms: int, end_ms: int) -> list[int]:
    """Settlement times strictly after start_ms and up to end_ms (UTC multiples of 8 h)."""
    if end_ms <= start_ms:
        return []
    first = (start_ms // PERIOD_MS + 1) * PERIOD_MS
    return list(range(first, end_ms + 1, PERIOD_MS))


def rate_at(coin: dict | None, ts: int, now: int, wait: bool = True) -> tuple[float | None, str]:
    """(rate, source) for the settlement at ts; (None, 'missing') when no honest number exists."""
    best = None
    for row in (coin or {}).get("funding") or []:
        try:
            t, r = int(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if abs(t - ts) <= MATCH_MS and (best is None or abs(t - ts) < abs(best[0] - ts)):
            best = (t, r)
    if best is not None:
        return best[1], "history"
    if wait and 0 <= now - ts < WAIT_MS:
        return None, "wait"
    live = (coin or {}).get("funding_now")
    if live is not None and 0 <= now - ts <= LIVE_MAX_AGE_MS:
        try:
            return float(live), "live"
        except (TypeError, ValueError):
            pass
    return None, "missing"


def price_at(coin: dict | None, ts: int, fallback: float) -> float:
    """The open of the 15m bar that starts at the settlement (its first traded price); the latest close before it otherwise."""
    px = None
    for c in (coin or {}).get("candles") or []:
        if c[0] <= ts:
            px = c[1] if c[0] == ts else c[4]
        else:
            break
    if not px:
        px = (coin or {}).get("price_now")
    return float(px) if px else float(fallback)


def accrue(conn: sqlite3.Connection, desk: str, trades: list[dict], coins: dict, now: int, wait: bool = True) -> dict[str, float]:
    """Charge every settlement each open trade has crossed since it opened (or since its last ledger row). Returns
    {trade_id: usd added now}. Trades need id, symbol, side, opened, qty_left (or qty), entry."""
    ensure(conn)
    out: dict[str, float] = {}
    for t in trades:
        last = conn.execute("SELECT MAX(ts) FROM funding_ledger WHERE desk=? AND trade_id=?", (desk, t["id"])).fetchone()[0]
        start = max(int(t["opened"]), int(last or 0))
        end = min(now, int(t["closed"])) if t.get("closed") else now        # a closed trade: only the settlements it was open for
        coin = coins.get(t["symbol"])
        qty = t.get("qty") if t.get("closed") else (t.get("qty_left") if t.get("qty_left") is not None else t.get("qty"))
        if not qty:
            continue
        sgn = 1 if t["side"] == "LONG" else -1
        for ts in boundaries(start, end):
            rate, src = rate_at(coin, ts, now, wait)
            if src == "wait":                     # not settled in the store yet — the next run charges it
                break
            notional = abs(qty) * price_at(coin, ts, t["entry"])
            usd = 0.0 if rate is None else -sgn * rate * notional
            cur = conn.execute("INSERT OR IGNORE INTO funding_ledger VALUES(?,?,?,?,?,?,?,?,?)",
                               (desk, t["id"], ts, t["symbol"], t["side"], rate, round(notional, 4), round(usd, 6), src))
            if cur.rowcount:
                out[t["id"]] = out.get(t["id"], 0.0) + usd
    return out


def summary(conn: sqlite3.Connection, desk: str) -> dict:
    """Totals for the desk report: dollars paid (−) or received (+), settlements charged, settlements without data."""
    ensure(conn)
    usd, n_ok, n_miss = conn.execute(
        "SELECT COALESCE(SUM(usd), 0), SUM(src != 'missing'), SUM(src = 'missing') FROM funding_ledger WHERE desk=?", (desk,)).fetchone()
    return {"usd": round(usd or 0.0, 2), "settlements": int(n_ok or 0), "missing": int(n_miss or 0)}


def fetch_live(timeout: int = 15) -> dict[str, float]:
    """BingX premiumIndex for all perpetuals → {base: lastFundingRate}. Empty on any error (then only stored history counts)."""
    try:
        req = urllib.request.Request("https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex",
                                     headers={"User-Agent": "cnq-funding/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode()).get("data") or []
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for x in data if isinstance(data, list) else []:
        s = str(x.get("symbol") or "")
        try:
            if s.endswith("-USDT") and x.get("lastFundingRate") not in (None, ""):
                out[s[:-5]] = float(x["lastFundingRate"])
        except (TypeError, ValueError):
            continue
    return out
