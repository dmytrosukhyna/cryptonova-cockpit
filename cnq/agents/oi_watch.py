"""Open-interest watch for rule F ("откуп сброса позиций", docs/research/oi_flush.md).
Every server run records open interest per coin (USD) into agents.sqlite; the desk then asks for the 4-hour change.
Source: BingX swap openInterest (the venue whose candles the desk uses); fallback Hyperliquid metaAndAssetCtxs
(openInterest × markPx). Changes are only compared within the same source. Stdlib only."""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 cryptonova/1.0", "Content-Type": "application/json"}


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS oi_hist(symbol TEXT, time INTEGER, oi_usd REAL, src TEXT, PRIMARY KEY(symbol, time, src))")


def _bingx(base: str, timeout: int = 10) -> float | None:
    url = f"https://open-api.bingx.com/openApi/swap/v2/quote/openInterest?symbol={base}-USDT&timestamp={int(time.time() * 1000)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            d = json.loads(r.read().decode())
        v = float((d.get("data") or {}).get("openInterest"))
        return v if v > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _hyperliquid(timeout: int = 15) -> dict[str, float]:
    try:
        req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps({"type": "metaAndAssetCtxs"}).encode(), headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            meta, ctxs = json.loads(r.read().decode())
        return {u["name"]: float(c["openInterest"]) * float(c["markPx"]) for u, c in zip(meta["universe"], ctxs) if c.get("markPx")}
    except Exception:  # noqa: BLE001
        return {}


def record(conn: sqlite3.Connection, bases: list[str], now: int | None = None, fetch_bingx=_bingx, fetch_hl=_hyperliquid) -> dict:
    now = now or int(time.time() * 1000)
    _ensure(conn)
    hl = None; n = {"bingx": 0, "hl": 0}
    for b in bases:
        v = fetch_bingx(b)
        if v:
            conn.execute("INSERT OR REPLACE INTO oi_hist VALUES(?,?,?,?)", (b, now, v, "bingx")); n["bingx"] += 1
        if hl is None:
            hl = fetch_hl()
        if hl and b in hl:
            conn.execute("INSERT OR REPLACE INTO oi_hist VALUES(?,?,?,?)", (b, now, hl[b], "hl")); n["hl"] += 1
    conn.execute("DELETE FROM oi_hist WHERE time < ?", (now - 3 * 86400_000,))
    conn.commit()
    return n


def change(conn: sqlite3.Connection, base: str, now: int, window_h: float = 4.0, tol_min: int = 50) -> float | None:
    """OI change over the last `window_h` hours (fraction), BingX first, else Hyperliquid; None if no matching history."""
    _ensure(conn)
    for src in ("bingx", "hl"):
        cur = conn.execute("SELECT time, oi_usd FROM oi_hist WHERE symbol=? AND src=? ORDER BY time DESC LIMIT 1", (base, src)).fetchone()
        if not cur or now - cur[0] > 45 * 60_000:
            continue
        target = cur[0] - int(window_h * 3_600_000)
        old = conn.execute("SELECT time, oi_usd FROM oi_hist WHERE symbol=? AND src=? AND time BETWEEN ? AND ? ORDER BY abs(time-?) LIMIT 1",
                           (base, src, target - tol_min * 60_000, target + tol_min * 60_000, target)).fetchone()
        if old and old[1] > 0:
            return cur[1] / old[1] - 1
    return None
