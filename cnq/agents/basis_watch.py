"""Basis watch for rule D («откуп дисконта фьючерса», docs/research/new_signals.md).
Every server run records, per coin, how far the perpetual trades from its spot index: basis = last price / index price − 1.
Both numbers are taken in the same moment (BingX ticker lastPrice + premiumIndex indexPrice; fallback Hyperliquid midPx vs
oraclePx). The desk asks for the average over the last hour (≥ 2 samples spanning ≥ 25 min — a single snapshot was worse on
history). Changes are compared within one source only. Stdlib only, fetchers injectable for tests."""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 cryptonova/1.0", "Content-Type": "application/json"}


def _get(url: str, body: dict | None = None, timeout: int = 10):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_bingx(bases: list[str]) -> dict[str, float]:
    try:
        ts = int(time.time() * 1000)
        idx = {x["symbol"]: float(x["indexPrice"]) for x in (_get(f"https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex?timestamp={ts}").get("data") or [])
               if x.get("indexPrice") not in (None, "", "0")}
        last = {x["symbol"]: float(x["lastPrice"]) for x in (_get(f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?timestamp={ts}").get("data") or [])
                if x.get("lastPrice") not in (None, "", "0")}
        out = {}
        for b in bases:
            s = b + "-USDT"
            if s in idx and s in last and idx[s] > 0:
                v = last[s] / idx[s] - 1
                if abs(v) < 0.05:
                    out[b] = v
        return out
    except Exception:  # noqa: BLE001
        return {}


def fetch_hl(bases: list[str]) -> dict[str, float]:
    try:
        meta, ctxs = _get("https://api.hyperliquid.xyz/info", body={"type": "metaAndAssetCtxs"}, timeout=15)
        out = {}
        for u, c in zip(meta["universe"], ctxs):
            if u["name"] in bases and c.get("oraclePx") and (c.get("midPx") or c.get("markPx")):
                v = float(c.get("midPx") or c["markPx"]) / float(c["oraclePx"]) - 1
                if abs(v) < 0.05:
                    out[u["name"]] = v
        return out
    except Exception:  # noqa: BLE001
        return {}


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS basis_hist(symbol TEXT, time INTEGER, basis REAL, src TEXT, PRIMARY KEY(symbol, time, src))")


def record(conn: sqlite3.Connection, bases: list[str], now: int | None = None, fetchers=(("bingx", fetch_bingx), ("hl", fetch_hl))) -> dict:
    now = now or int(time.time() * 1000)
    _ensure(conn)
    n = {}
    for src, f in fetchers:
        got = f(bases)
        for b, v in got.items():
            conn.execute("INSERT OR REPLACE INTO basis_hist VALUES(?,?,?,?)", (b, now, v, src))
        n[src] = len(got)
    conn.execute("DELETE FROM basis_hist WHERE time < ?", (now - 3 * 86400_000,))
    conn.commit()
    return n


def avg_1h(conn: sqlite3.Connection, base: str, now: int, window_min: int = 70, min_span_min: int = 25) -> float | None:
    """Average basis over the last hour from one source (BingX first); None without ≥ 2 samples spanning ≥ 25 min."""
    _ensure(conn)
    for src in ("bingx", "hl"):
        rows = conn.execute("SELECT time, basis FROM basis_hist WHERE symbol=? AND src=? AND time BETWEEN ? AND ? ORDER BY time",
                            (base, src, now - window_min * 60_000, now)).fetchall()
        if len(rows) >= 2 and rows[-1][0] - rows[0][0] >= min_span_min * 60_000:
            return sum(v for _, v in rows) / len(rows)
    return None
