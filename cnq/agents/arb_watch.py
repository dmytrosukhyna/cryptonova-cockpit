"""Arbitrage watch (server side, every run): best-bid / best-ask of ~30 coins on several spot venues, the best cross-venue
spread per coin net of both taker fees, recorded into agents.sqlite → web/arb.json with honest statistics:
how often a window that is positive after fees exists at the moment of a sample, how big, and what a virtual account would
have made taking $1 000 per window (assumes money pre-placed on both venues, fill at top of book, no latency — optimistic).

Each run takes SAMPLES snapshots a few seconds apart. Venues that refuse the server (geo-blocks) are listed as unavailable.
Stdlib only; every venue fetch is injectable for tests."""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 cryptonova/1.0"}
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "BCH", "TRX", "NEAR", "SUI", "APT", "ARB", "OP", "BNB",
         "TON", "PEPE", "SHIB", "WIF", "ATOM", "FIL", "INJ", "SEI", "TIA", "UNI", "AAVE", "ETC"]
MIN_NET = 0.0005          # a window counts when ≥ 0.05 % after fees (same rule as the panel's virtual account)
MAX_GROSS = 0.05          # > 5 % gross is almost always a different token or closed withdrawals — ignored
TICKET = 1000.0
SAMPLES = 3


def _get(url: str, timeout: int = 8):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


VENUES = {   # name: (taker fee, url, parser → {COIN: (bid, ask)})
    "Binance": (0.001, "https://api.binance.com/api/v3/ticker/bookTicker",
                lambda d: {x["symbol"][:-4]: (float(x["bidPrice"]), float(x["askPrice"])) for x in d if x["symbol"].endswith("USDT")}),
    "Bybit": (0.001, "https://api.bybit.com/v5/market/tickers?category=spot",
              lambda d: {x["symbol"][:-4]: (float(x["bid1Price"] or 0), float(x["ask1Price"] or 0)) for x in d["result"]["list"] if x["symbol"].endswith("USDT")}),
    "OKX": (0.001, "https://www.okx.com/api/v5/market/tickers?instType=SPOT",
            lambda d: {x["instId"][:-5]: (float(x["bidPx"] or 0), float(x["askPx"] or 0)) for x in d["data"] if x["instId"].endswith("-USDT")}),
    "Gate": (0.002, "https://api.gateio.ws/api/v4/spot/tickers",
             lambda d: {x["currency_pair"][:-5]: (float(x["highest_bid"] or 0), float(x["lowest_ask"] or 0)) for x in d if x["currency_pair"].endswith("_USDT")}),
    "MEXC": (0.0005, "https://api.mexc.com/api/v3/ticker/bookTicker",
             lambda d: {x["symbol"][:-4]: (float(x["bidPrice"] or 0), float(x["askPrice"] or 0)) for x in d if x["symbol"].endswith("USDT")}),
    "Bitget": (0.001, "https://api.bitget.com/api/v2/spot/market/tickers",
               lambda d: {x["symbol"][:-4]: (float(x["bidPr"] or 0), float(x["askPr"] or 0)) for x in d["data"] if x["symbol"].endswith("USDT")}),
    "KuCoin": (0.001, "https://api.kucoin.com/api/v1/market/allTickers",
               lambda d: {x["symbol"][:-5]: (float(x["buy"] or 0), float(x["sell"] or 0)) for x in d["data"]["ticker"] if x["symbol"].endswith("-USDT")}),
    "HTX": (0.002, "https://api.huobi.pro/market/tickers",
            lambda d: {x["symbol"][:-4].upper(): (float(x["bid"] or 0), float(x["ask"] or 0)) for x in d["data"] if x["symbol"].endswith("usdt")}),
}


def fetch_books(get=_get) -> dict[str, dict]:
    out = {}
    for name, (_, url, parse) in VENUES.items():
        try:
            m = {c: ba for c, ba in parse(get(url)).items() if c in COINS and ba[0] > 0 and ba[1] > 0}
            if m:
                out[name] = m
        except Exception:  # noqa: BLE001
            pass
    return out


def best_spreads(books: dict[str, dict]) -> list[dict]:
    rows = []
    for c in COINS:
        q = [(v, books[v][c]) for v in books if c in books[v]]
        if any(bid / ask - 1 > MAX_GROSS for _, (_, ask) in q for _, (bid, _) in q):
            continue   # prices too far apart → different token with the same ticker or closed withdrawals
        best = None
        for va, (_, ask) in q:
            for vb, (bid, _) in q:
                if va == vb:
                    continue
                gross = bid / ask - 1
                net = gross - VENUES[va][0] - VENUES[vb][0]
                if best is None or net > best["net"]:
                    best = {"coin": c, "buy": va, "sell": vb, "ask": ask, "bid": bid, "gross": round(gross, 6), "net": round(net, 6)}
        if best:
            rows.append(best)
    return sorted(rows, key=lambda r: -r["net"])


def _ensure(conn):
    conn.executescript("""CREATE TABLE IF NOT EXISTS arb_samples(time INTEGER PRIMARY KEY, venues TEXT, best_net REAL, n_pos INTEGER);
    CREATE TABLE IF NOT EXISTS arb_windows(time INTEGER, coin TEXT, buy TEXT, sell TEXT, net REAL, PRIMARY KEY(time, coin));""")


def run(conn: sqlite3.Connection, now: int | None = None, fetch=fetch_books, samples: int = SAMPLES, pause: float = 4.0) -> dict:
    _ensure(conn)
    venues_ok: set[str] = set()
    last_rows: list[dict] = []
    for i in range(samples):
        t = (now or int(time.time() * 1000)) + i
        books = fetch()
        venues_ok |= set(books)
        rows = best_spreads(books)
        if rows:
            last_rows = rows
            pos = [r for r in rows if r["net"] >= MIN_NET]
            conn.execute("INSERT OR REPLACE INTO arb_samples VALUES(?,?,?,?)", (t, ",".join(sorted(books)), rows[0]["net"], len(pos)))
            for r in pos:
                conn.execute("INSERT OR REPLACE INTO arb_windows VALUES(?,?,?,?,?)", (t, r["coin"], r["buy"], r["sell"], r["net"]))
        if i < samples - 1 and pause:
            time.sleep(pause)
    t_now = now or int(time.time() * 1000)
    conn.execute("DELETE FROM arb_samples WHERE time < ?", (t_now - 30 * 86400_000,))
    conn.execute("DELETE FROM arb_windows WHERE time < ?", (t_now - 30 * 86400_000,))
    conn.commit()

    def stats(since):
        n, npos = conn.execute("SELECT COUNT(*), SUM(n_pos > 0) FROM arb_samples WHERE time >= ?", (since,)).fetchone()
        w = conn.execute("SELECT COUNT(*), COALESCE(SUM(net),0), COALESCE(MAX(net),0) FROM arb_windows WHERE time >= ?", (since,)).fetchone()
        return {"samples": n or 0, "samples_with_window": npos or 0, "share": round((npos or 0) / n, 3) if n else None,
                "windows": w[0], "virtual_pnl_usd": round(w[1] * TICKET, 2), "best_net": round(w[2], 5)}
    top = [{"time": a, "coin": b, "buy": c, "sell": d, "net": e} for a, b, c, d, e in
           conn.execute("SELECT time, coin, buy, sell, net FROM arb_windows ORDER BY time DESC LIMIT 15").fetchall()]
    first = conn.execute("SELECT MIN(time) FROM arb_samples").fetchone()[0]
    return {"asof": t_now, "venues_ok": sorted(venues_ok), "venues_all": list(VENUES), "now": last_rows[:12],
            "day": stats(t_now - 86400_000), "week": stats(t_now - 7 * 86400_000), "since": first, "recent_windows": top,
            "rules": f"окно = ≥{MIN_NET * 100:.2f}% после комиссий обеих бирж; виртуально ${TICKET:,.0f} на окно; разница >5% не считается (другой токен/закрыт вывод)",
            "note": "Сервер смотрит цены 3 раза за запуск (раз в 30 минут) — это выборка, а не непрерывное наблюдение. "
                    "Оценка оптимистичная: деньги заранее на обеих биржах, исполнение по лучшей цене без задержки, перевод не нужен."}
