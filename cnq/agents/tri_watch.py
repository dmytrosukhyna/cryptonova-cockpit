"""Triangle watch (server side, every run): three trades inside ONE exchange, start → A → B → start, e.g.
USDT → BTC → XRP → USDT. Every spot pair quoted in USDT / USDC / FDUSD / BTC / ETH / BNB is read from the public
ticker book of each venue, every 3-cycle of that graph is scanned in both directions, and the result is net of the taker
fee on each of the three legs (0.1% on Binance/Bybit/OKX/Bitget/KuCoin, venue fee otherwise — see arb_watch.VENUES).

A cycle that stays ≥ +0.05% after three fees is a «window»; it is recorded in agents.sqlite (tri_samples / tri_windows)
→ web/tri.json with the same honesty rules as arb_watch: the virtual ticket is at most $1 000 and never more than the
smallest top-of-book size of the three legs; a window whose triangle — or whose non-major coin on the same venue — already
gave a window in the previous 24 h is «stuck» (stale quote on a halted/illiquid pair) and is counted once. Legs with a
crossed book or a spread > 2% are ignored, and cycles > +3% are reported as suspicious, never as profit.

Why the panel only ever shows USDT/BNB/BTC on Binance: after 3 × 0.1% fees every cycle is negative, so «the best» is
simply the least negative one — the triangle made of the three most liquid books. Stdlib only; fetch is injectable."""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request

from .arb_watch import VENUES as ARB_VENUES, _f

UA = {"User-Agent": "Mozilla/5.0 cryptonova/1.0"}
HOME = ("USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB")          # quote currencies kept, and start-node priority
STABLE = {"USDT", "USDC", "FDUSD"}
QUOTES_BY_LEN = sorted(HOME, key=len, reverse=True)
MIN_NET = 0.0005
MAX_NET = 0.03            # > 3 % on one exchange is a stale/halted book, not money
MAX_LEG_SPREAD = 0.02
TICKET = 1000.0
SAMPLES = 2
DAY_MS = 86_400_000


def _get(url: str, timeout: int = 10):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


TRI_VENUES = {   # name: (urls (first that answers wins), symbol separator or None for «ETHBTC» style, rows → (symbol, bid, ask, bid qty, ask qty))
    "Binance": (("https://api.binance.com/api/v3/ticker/bookTicker", "https://data-api.binance.vision/api/v3/ticker/bookTicker"), None,
                lambda d: ((x["symbol"], x["bidPrice"], x["askPrice"], x.get("bidQty"), x.get("askQty")) for x in d)),
    "Bybit": (("https://api.bybit.com/v5/market/tickers?category=spot",), None,
              lambda d: ((x["symbol"], x["bid1Price"], x["ask1Price"], x.get("bid1Size"), x.get("ask1Size")) for x in d["result"]["list"])),
    "OKX": (("https://www.okx.com/api/v5/market/tickers?instType=SPOT",), "-",
            lambda d: ((x["instId"], x["bidPx"], x["askPx"], x.get("bidSz"), x.get("askSz")) for x in d["data"])),
    "Bitget": (("https://api.bitget.com/api/v2/spot/market/tickers",), None,
               lambda d: ((x["symbol"], x["bidPr"], x["askPr"], x.get("bidSz"), x.get("askSz")) for x in d["data"])),
    "KuCoin": (("https://api.kucoin.com/api/v1/market/allTickers",), "-",
               lambda d: ((x["symbol"], x["buy"], x["sell"], x.get("bestBidSize"), x.get("bestAskSize")) for x in d["data"]["ticker"])),
    "Gate": (("https://api.gateio.ws/api/v4/spot/tickers",), "_",
             lambda d: ((x["currency_pair"], x["highest_bid"], x["lowest_ask"], x.get("highest_size"), x.get("lowest_size")) for x in d)),
    "MEXC": (("https://api.mexc.com/api/v3/ticker/bookTicker",), None,
             lambda d: ((x["symbol"], x["bidPrice"], x["askPrice"], x.get("bidQty"), x.get("askQty")) for x in d)),
    "HTX": (("https://api.huobi.pro/market/tickers",), None,
            lambda d: ((x["symbol"], x["bid"], x["ask"], x.get("bidSize"), x.get("askSize")) for x in d["data"])),
}
FEES = {v: ARB_VENUES[v][0] for v in TRI_VENUES}


def split(sym: str, sep: str | None) -> tuple[str, str] | None:
    s = str(sym).upper()
    if sep:
        b, _, q = s.partition(sep)
        return (b, q) if b and q in HOME else None
    for q in QUOTES_BY_LEN:
        if s.endswith(q) and len(s) > len(q):
            return s[:-len(q)], q
    return None


def parse(rows, sep: str | None) -> dict:
    """→ {(base, quote): (bid, ask, bid qty, ask qty)} keeping only sane books."""
    out = {}
    for sym, bid, ask, bq, aq in rows:
        p = split(sym, sep)
        b, a = _f(bid), _f(ask)
        if not p or not b or not a or p[0] == p[1] or b > a or a / b - 1 > MAX_LEG_SPREAD:
            continue
        out[p] = (b, a, _f(bq), _f(aq))
    return out


def fetch_books(get=_get) -> dict[str, dict]:
    out = {}
    for name, (urls, sep, rows) in TRI_VENUES.items():
        for u in urls:
            try:
                m = parse(rows(get(u)), sep)
                if m:
                    out[name] = m
                    break
            except Exception:  # noqa: BLE001
                pass
    return out


def _usd(book: dict) -> dict[str, float]:
    """Rough USD value of each currency (mid price via USDT, else via BTC) — only to size the legs."""
    px = {s: 1.0 for s in STABLE}
    for (b, q), (bid, ask, _, _) in book.items():
        if q == "USDT":
            px[b] = (bid + ask) / 2
    btc = px.get("BTC")
    for (b, q), (bid, ask, _, _) in book.items():
        if q == "BTC" and b not in px and btc:
            px[b] = (bid + ask) / 2 * btc
    return px


def _leg(book, fee, x, y):
    """Best way to turn 1 x into y on this venue: (amount of y, capacity of the leg in USD units of x or y, pair) or None."""
    best = None
    if (x, y) in book:                      # sell x for y at the bid
        bid, _, bq, _ = book[(x, y)]
        best = (bid * (1 - fee), ("base", x, bq), (x, y))
    if (y, x) in book:                      # buy y with x at the ask
        _, ask, _, aq = book[(y, x)]
        r = (1 / ask) * (1 - fee)
        if best is None or r > best[0]:
            best = (r, ("base", y, aq), (y, x))
    return best


def scan(book: dict, fee: float) -> tuple[list[dict], int]:
    """All triangles of one venue, best direction each, sorted by net. Returns (rows, number of triangles scanned)."""
    adj: dict[str, set] = {}
    for b, q in book:
        adj.setdefault(b, set()).add(q); adj.setdefault(q, set()).add(b)
    order = {c: i for i, c in enumerate(sorted(adj))}
    tris = set()
    for b, q in book:
        x, y = sorted((b, q), key=order.get)
        for z in adj[x] & adj[y]:
            if order[z] > order[y]:
                tris.add((x, y, z))
    usd = _usd(book)
    pri = {c: i for i, c in enumerate(HOME)}
    rows = []
    for tri in tris:
        best = None
        for cyc in (tri, (tri[0], tri[2], tri[1])):
            legs = [_leg(book, fee, cyc[i], cyc[(i + 1) % 3]) for i in range(3)]
            if any(lg is None for lg in legs):
                continue
            mult = legs[0][0] * legs[1][0] * legs[2][0]
            if best is None or mult > best[0]:
                best = (mult, cyc, legs)
        if best is None:
            continue
        mult, cyc, legs = best
        k = min(range(3), key=lambda i: pri.get(cyc[i], 99))                  # start at USDT if present, else the next home coin
        path = [cyc[(k + i) % 3] for i in range(3)]
        caps = [usd[c] * qty if qty and c in usd else None for _, (_, c, qty), _ in legs]
        rows.append({"path": " → ".join(path + [path[0]]), "nodes": ",".join(sorted(tri)), "net": round(mult - 1, 6),
                     "gross": round(mult / (1 - fee) ** 3 - 1, 6), "size_usd": round(min(caps), 2) if all(c is not None for c in caps) else None,
                     "pairs": ["/".join(legs[(k + i) % 3][2]) for i in range(3)]})
    rows.sort(key=lambda r: -r["net"])
    return rows, len(tris)


def _ensure(conn):
    conn.executescript("""CREATE TABLE IF NOT EXISTS tri_samples(time INTEGER PRIMARY KEY, venues TEXT, n_tri INTEGER, best_net REAL, n_pos INTEGER);
    CREATE TABLE IF NOT EXISTS tri_windows(time INTEGER, venue TEXT, nodes TEXT, path TEXT, net REAL, size_usd REAL, fresh INTEGER DEFAULT 1,
                                           PRIMARY KEY(time, venue, nodes));""")


def classify(conn) -> list[dict]:
    """Fresh = neither this triangle nor any of its non-major coins on this venue was in a window in the previous 24 h."""
    rows = [dict(zip(("rowid", "time", "venue", "nodes", "path", "net", "size_usd", "fresh_db"), r)) for r in conn.execute(
        "SELECT rowid, time, venue, nodes, path, net, size_usd, fresh FROM tri_windows ORDER BY time, venue, nodes")]
    seen, upd = {}, []
    for r in rows:
        keys = [(r["venue"], "t", r["nodes"])] + [(r["venue"], "c", c) for c in r["nodes"].split(",") if c not in HOME]
        r["fresh"] = int(all(r["time"] - seen.get(k, -10 ** 15) >= DAY_MS for k in keys))
        for k in keys:
            seen[k] = r["time"]
        if r["fresh"] != r["fresh_db"]:
            upd.append((r["fresh"], r["rowid"]))
    if upd:
        conn.executemany("UPDATE tri_windows SET fresh=? WHERE rowid=?", upd)
    return rows


def _med(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def _value(r) -> float:
    return r["net"] * (min(TICKET, r["size_usd"]) if r["size_usd"] is not None else TICKET)


def run(conn: sqlite3.Connection, now: int | None = None, fetch=fetch_books, samples: int = SAMPLES, pause: float = 3.0) -> dict:
    _ensure(conn)
    venues_ok: set[str] = set()
    scanned: dict[str, dict] = {}; best: dict[str, list] = {}; suspicious: list[dict] = []
    for i in range(samples):
        t = (now or int(time.time() * 1000)) + i
        books = fetch()
        venues_ok |= set(books)
        allrows, n_tri, pos = [], 0, []
        for v, book in books.items():
            rows, n = scan(book, FEES.get(v, 0.001))
            n_tri += n
            scanned[v] = {"pairs": len(book), "triangles": n, "fee_per_leg": FEES.get(v, 0.001)}
            ok = [r for r in rows if r["net"] <= MAX_NET]
            suspicious += [dict(r, venue=v) for r in rows if r["net"] > MAX_NET][:5]
            best[v] = ok[:5]
            allrows += [dict(r, venue=v) for r in ok]
            pos += [dict(r, venue=v) for r in ok if r["net"] >= MIN_NET]
        if allrows:
            allrows.sort(key=lambda r: -r["net"])
            conn.execute("INSERT OR REPLACE INTO tri_samples VALUES(?,?,?,?,?)", (t, ",".join(sorted(books)), n_tri, allrows[0]["net"], len(pos)))
            for r in pos:
                conn.execute("INSERT OR REPLACE INTO tri_windows(time, venue, nodes, path, net, size_usd, fresh) VALUES(?,?,?,?,?,?,1)",
                             (t, r["venue"], r["nodes"], r["path"], r["net"], r["size_usd"]))
        if i < samples - 1 and pause:
            time.sleep(pause)
    t_now = now or int(time.time() * 1000)
    conn.execute("DELETE FROM tri_samples WHERE time < ?", (t_now - 30 * DAY_MS,))
    conn.execute("DELETE FROM tri_windows WHERE time < ?", (t_now - 30 * DAY_MS,))
    allw = classify(conn)
    conn.commit()

    def stats(since):
        n, npos = conn.execute("SELECT COUNT(*), SUM(n_pos > 0) FROM tri_samples WHERE time >= ?", (since,)).fetchone()
        w = [r for r in allw if r["time"] >= since]
        fr = [r for r in w if r["fresh"]]
        return {"samples": n or 0, "samples_with_window": npos or 0, "share": round((npos or 0) / n, 3) if n else None,
                "windows": len(fr), "observations": len(w), "stuck_observations": len(w) - len(fr),
                "virtual_pnl_usd": round(sum(_value(r) for r in fr), 2), "best_net": round(max((r["net"] for r in fr), default=0), 5),
                "median_net": _med([r["net"] for r in fr]), "median_size_usd": _med([r["size_usd"] for r in fr]),
                "stuck_triangles": len({(r["venue"], r["nodes"]) for r in w if not r["fresh"]})}

    top_now = sorted((dict(r, venue=v) for v, rows in best.items() for r in rows), key=lambda r: -r["net"])[:12]
    recent = [{"time": r["time"], "venue": r["venue"], "path": r["path"], "net": r["net"], "size_usd": r["size_usd"], "usd": round(_value(r), 2)}
              for r in reversed([r for r in allw if r["fresh"]][-15:])]
    stuck = [{"venue": a, "path": b, "net": c, "since": d, "seen": e} for a, b, c, d, e in conn.execute(
        "SELECT venue, MAX(path), MAX(net), MIN(time), COUNT(*) FROM tri_windows WHERE time >= ? GROUP BY venue, nodes HAVING COUNT(*) >= 3 "
        "ORDER BY MAX(net) DESC LIMIT 10", (t_now - DAY_MS,)).fetchall()]
    first = conn.execute("SELECT MIN(time) FROM tri_samples").fetchone()[0]
    d = {"asof": t_now, "venues_ok": sorted(venues_ok), "venues_all": list(TRI_VENUES), "scanned": scanned,
         "scanned_total": sum(s["triangles"] for s in scanned.values()), "best": best, "now": top_now,
         "suspicious": sorted({(r["venue"], r["path"]): r for r in suspicious}.values(), key=lambda r: -r["net"])[:5], "day": stats(t_now - DAY_MS), "week": stats(t_now - 7 * DAY_MS),
         "since": first, "recent_windows": recent, "stuck": stuck,
         "rules": f"треугольник = 3 обмена внутри одной биржи через пары к {', '.join(HOME)}; комиссия тейкера на каждом шаге "
                  f"(0.1% на Binance/Bybit/OKX); окно = ≥{MIN_NET * 100:.2f}% после трёх комиссий; виртуально до ${TICKET:,.0f}, но не больше "
                  f"самого тонкого из трёх стаканов; >{MAX_NET * 100:.0f}% — подозрительно (застывшая цена), не считается",
         "note": "Сервер смотрит цены 2 раза за запуск (раз в 10–30 минут) — это выборка. Настоящие треугольные окна на крупных "
                 "биржах живут миллисекунды и достаются роботам маркет-мейкеров с нулевой комиссией."}
    d["explain_ru"] = explain(d)
    return d


def explain(d: dict) -> str:
    if not d["scanned"]:
        return "Данные ещё копятся: биржи пока не ответили серверу."
    parts = [f"Треугольник — три обмена внутри одной биржи (например, USDT → BTC → XRP → USDT). Сервер проверяет все "
             f"треугольники из пар к USDT, USDC, FDUSD, BTC, ETH и BNB, в обе стороны, на биржах {', '.join(d['scanned'])} — "
             f"всего {d['scanned_total']}."]
    lead = [(v, rows[0]) for v, rows in d["best"].items() if rows]
    if lead:
        v, r = max(lead, key=lambda x: x[1]["net"])
        parts.append(f"Лучший круг сейчас: {r['path']} на {v}, {r['net'] * 100:+.3f}% после трёх комиссий.")
        if r["net"] < 0:
            parts.append("Это минус: три комиссии по 0.1% = 0.3%, а цены самых ликвидных пар (BTC/USDT, BNB/BTC, ETH/BTC) роботы "
                         "выравнивают до сотых долей процента. Поэтому «лучший» — просто наименее убыточный круг из самых "
                         "ликвидных пар, а не возможность.")
    s = d["day"]
    if s["samples"]:
        parts.append(f"За сутки проверок: {s['samples']}, новых окон ≥ 0.05% чистыми: {s['windows']}" +
                     (f" (обычно {s['median_net'] * 100:.2f}%, в стакане ≈ $" + f"{s['median_size_usd']:,.0f}".replace(",", " ") + ")" if s["median_net"] is not None and s["median_size_usd"] is not None else "") +
                     f", застывших треугольников {s['stuck_triangles']}; виртуально {'+' if s['virtual_pnl_usd'] >= 0 else '−'}${abs(s['virtual_pnl_usd']):,.2f}.")
    off = [v for v in d["venues_all"] if v not in d["venues_ok"]]
    if off:
        parts.append(f"Серверу недоступны: {', '.join(off)} — их считает только браузер.")
    return " ".join(parts)
