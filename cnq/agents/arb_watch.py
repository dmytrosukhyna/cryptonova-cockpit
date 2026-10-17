"""Arbitrage watch (server side, every run): best-bid / best-ask of ~30 coins on several spot venues, the best cross-venue
spread per coin net of both taker fees, recorded into agents.sqlite → web/arb.json with honest statistics:
how often a window that is positive after fees exists at the moment of a sample, how big, how deep the top of the book is,
how long it lives, and what a virtual account would have made taking up to $1 000 per window (capped by the top-of-book
size; assumes money pre-placed on both venues, fill at top of book, no latency, no transfers — still optimistic).

Fresh vs stuck: a price gap that persists is almost always one venue with closed deposits/withdrawals for the coin or a
dead book (e.g. INJ and OP cheap on HTX for hours). It shows up as several routes (HTX→MEXC, HTX→OKX, …), so a window is
«fresh» only if neither its buy side (coin, buy venue) nor its sell side (coin, sell venue) was part of any window in the
previous 24 h. The flag is recomputed over all stored rows each run (also fixes rows written by older rules).

Each run takes SAMPLES snapshots a few seconds apart. Venues that refuse the server (geo-blocks) are listed as unavailable;
Binance falls back to its market-data mirror. Stdlib only; every venue fetch is injectable for tests."""
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
DAY_MS = 86_400_000
NEXT_RUN_MS = 120_000     # an episode longer than this survived into a later run (samples inside one run are ~7 s apart)


def _get(url: str, timeout: int = 8):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _row(bid, ask, bid_qty=None, ask_qty=None) -> tuple:
    """(bid, ask, bid size, ask size) — sizes in coins at the best price, None when the venue does not publish them."""
    return float(bid or 0), float(ask or 0), _f(bid_qty), _f(ask_qty)


VENUES = {   # name: (taker fee, url, parser → {COIN: (bid, ask, bid_qty, ask_qty)})
    "Binance": (0.001, "https://api.binance.com/api/v3/ticker/bookTicker",
                lambda d: {x["symbol"][:-4]: _row(x["bidPrice"], x["askPrice"], x.get("bidQty"), x.get("askQty")) for x in d if x["symbol"].endswith("USDT")}),
    "Bybit": (0.001, "https://api.bybit.com/v5/market/tickers?category=spot",
              lambda d: {x["symbol"][:-4]: _row(x["bid1Price"], x["ask1Price"], x.get("bid1Size"), x.get("ask1Size")) for x in d["result"]["list"] if x["symbol"].endswith("USDT")}),
    "OKX": (0.001, "https://www.okx.com/api/v5/market/tickers?instType=SPOT",
            lambda d: {x["instId"][:-5]: _row(x["bidPx"], x["askPx"], x.get("bidSz"), x.get("askSz")) for x in d["data"] if x["instId"].endswith("-USDT")}),
    "Gate": (0.002, "https://api.gateio.ws/api/v4/spot/tickers",
             lambda d: {x["currency_pair"][:-5]: _row(x["highest_bid"], x["lowest_ask"], x.get("highest_size"), x.get("lowest_size")) for x in d if x["currency_pair"].endswith("_USDT")}),
    "MEXC": (0.0005, "https://api.mexc.com/api/v3/ticker/bookTicker",
             lambda d: {x["symbol"][:-4]: _row(x["bidPrice"], x["askPrice"], x.get("bidQty"), x.get("askQty")) for x in d if x["symbol"].endswith("USDT")}),
    "Bitget": (0.001, "https://api.bitget.com/api/v2/spot/market/tickers",
               lambda d: {x["symbol"][:-4]: _row(x["bidPr"], x["askPr"], x.get("bidSz"), x.get("askSz")) for x in d["data"] if x["symbol"].endswith("USDT")}),
    "KuCoin": (0.001, "https://api.kucoin.com/api/v1/market/allTickers",
               lambda d: {x["symbol"][:-5]: _row(x["buy"], x["sell"], x.get("bestBidSize"), x.get("bestAskSize")) for x in d["data"]["ticker"] if x["symbol"].endswith("-USDT")}),
    "HTX": (0.002, "https://api.huobi.pro/market/tickers",
            lambda d: {x["symbol"][:-4].upper(): _row(x["bid"], x["ask"], x.get("bidSize"), x.get("askSize")) for x in d["data"] if x["symbol"].endswith("usdt")}),
}
ALT_URLS = {"Binance": "https://data-api.binance.vision/api/v3/ticker/bookTicker"}   # market-data mirror (api.binance.com geo-blocks US servers)


def fetch_books(get=_get) -> dict[str, dict]:
    out = {}
    for name, (_, url, parse) in VENUES.items():
        for u in (url, ALT_URLS.get(name)):
            if not u:
                continue
            try:
                m = {c: ba for c, ba in parse(get(u)).items() if c in COINS and ba[0] > 0 and ba[1] > 0}
                if m:
                    out[name] = m
                    break
            except Exception:  # noqa: BLE001
                pass
    return out


def _ba(x) -> tuple:
    x = tuple(x) + (None, None)
    return x[0], x[1], x[2], x[3]


def best_spreads(books: dict[str, dict]) -> list[dict]:
    rows = []
    for c in COINS:
        q = [(v, _ba(books[v][c])) for v in books if c in books[v]]
        if any(qb[0] / qa[1] - 1 > MAX_GROSS for _, qa in q for _, qb in q):
            continue   # prices too far apart → different token with the same ticker or closed withdrawals
        best = None
        for va, (_, ask, _, ask_qty) in q:
            for vb, (bid, _, bid_qty, _) in q:
                if va == vb:
                    continue
                gross = bid / ask - 1
                net = gross - VENUES[va][0] - VENUES[vb][0]
                if best is None or net > best["net"]:
                    size = round(min(ask * ask_qty, bid * bid_qty), 2) if ask_qty and bid_qty else None
                    best = {"coin": c, "buy": va, "sell": vb, "ask": ask, "bid": bid, "gross": round(gross, 6), "net": round(net, 6),
                            "size_usd": size}
        if best:
            rows.append(best)
    return sorted(rows, key=lambda r: -r["net"])


def _ensure(conn):
    conn.executescript("""CREATE TABLE IF NOT EXISTS arb_samples(time INTEGER PRIMARY KEY, venues TEXT, best_net REAL, n_pos INTEGER);
    CREATE TABLE IF NOT EXISTS arb_windows(time INTEGER, coin TEXT, buy TEXT, sell TEXT, net REAL, PRIMARY KEY(time, coin));""")
    have = {r[1] for r in conn.execute("PRAGMA table_info(arb_windows)")}
    if "fresh" not in have:
        conn.execute("ALTER TABLE arb_windows ADD COLUMN fresh INTEGER DEFAULT 1")
    if "size_usd" not in have:
        conn.execute("ALTER TABLE arb_windows ADD COLUMN size_usd REAL")


def classify(conn) -> list[dict]:
    """All stored windows in time order with two flags: `fresh` (neither the buy side nor the sell side of this coin was in
    any window during the previous 24 h — the honest rule, written back to the table) and `route_fresh` (the older,
    looser rule: the same coin/buy/sell route not seen in 24 h — kept only to show the difference)."""
    rows = [dict(zip(("rowid", "time", "coin", "buy", "sell", "net", "fresh_db", "size_usd"), r)) for r in conn.execute(
        "SELECT rowid, time, coin, buy, sell, net, fresh, size_usd FROM arb_windows ORDER BY time, coin")]
    side, route, upd = {}, {}, []
    for r in rows:
        kb, ks, kr = (r["coin"], "b", r["buy"]), (r["coin"], "s", r["sell"]), (r["coin"], r["buy"], r["sell"])
        r["fresh"] = int(all(r["time"] - side.get(k, -10 ** 15) >= DAY_MS for k in (kb, ks)))
        r["route_fresh"] = int(r["time"] - route.get(kr, -10 ** 15) >= DAY_MS)
        side[kb] = side[ks] = route[kr] = r["time"]
        if r["fresh"] != r["fresh_db"]:
            upd.append((r["fresh"], r["rowid"]))
    if upd:
        conn.executemany("UPDATE arb_windows SET fresh=? WHERE rowid=?", upd)
    return rows


def _med(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def _value(r) -> float:
    return r["net"] * min(TICKET, r["size_usd"]) if r["size_usd"] is not None else r["net"] * TICKET


def episodes(sample_times: list[int], rows: list[dict]) -> dict:
    """A coin's episode = consecutive samples in which it had a window. Shows how long gaps live between samples."""
    by_t: dict[int, dict] = {}
    for r in rows:
        by_t.setdefault(r["time"], {})[r["coin"]] = r
    open_: dict[str, dict] = {}; done = []
    for t in sorted(set(sample_times) | set(by_t)):
        here = by_t.get(t, {})
        for c in [c for c in open_ if c not in here]:
            done.append(open_.pop(c))
        for c, r in here.items():
            e = open_.setdefault(c, {"coin": c, "buy": r["buy"], "sell": r["sell"], "start": t, "end": t, "samples": 0, "max_net": r["net"]})
            e["end"] = t; e["samples"] += 1; e["max_net"] = max(e["max_net"], r["net"])
    done += open_.values()
    if not done:
        return {"count": 0}
    dur = [(e["end"] - e["start"]) / 60_000 for e in done]
    longest = max(done, key=lambda e: e["end"] - e["start"])
    return {"count": len(done), "one_sample": sum(e["samples"] == 1 for e in done),
            "within_run": sum(e["samples"] > 1 and e["end"] - e["start"] < NEXT_RUN_MS for e in done),
            "next_run": sum(e["end"] - e["start"] >= NEXT_RUN_MS for e in done),
            "hour_plus": sum(e["end"] - e["start"] >= 3_600_000 for e in done),
            "median_min": round(_med(dur), 1), "still_open": len(open_),
            "longest": {"coin": longest["coin"], "buy": longest["buy"], "sell": longest["sell"],
                        "hours": round((longest["end"] - longest["start"]) / 3_600_000, 1), "max_net": round(longest["max_net"], 5)}}


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
                conn.execute("INSERT OR REPLACE INTO arb_windows(time, coin, buy, sell, net, fresh, size_usd) VALUES(?,?,?,?,?,1,?)",
                             (t, r["coin"], r["buy"], r["sell"], r["net"], r["size_usd"]))
        if i < samples - 1 and pause:
            time.sleep(pause)
    t_now = now or int(time.time() * 1000)
    conn.execute("DELETE FROM arb_samples WHERE time < ?", (t_now - 30 * DAY_MS,))
    conn.execute("DELETE FROM arb_windows WHERE time < ?", (t_now - 30 * DAY_MS,))
    allw = classify(conn)
    conn.commit()

    def stats(since):
        n, npos = conn.execute("SELECT COUNT(*), SUM(n_pos > 0) FROM arb_samples WHERE time >= ?", (since,)).fetchone()
        w = [r for r in allw if r["time"] >= since]
        fr = [r for r in w if r["fresh"]]
        stuck_keys = {(r["coin"], r["buy"], r["sell"]) for r in w if not r["fresh"]}
        return {"samples": n or 0, "samples_with_window": npos or 0, "share": round((npos or 0) / n, 3) if n else None,
                "windows": len(fr), "observations": len(w), "stuck_observations": len(w) - len(fr),
                "virtual_pnl_usd": round(sum(_value(r) for r in fr), 2),
                "virtual_pnl_naive_usd": round(sum(r["net"] * TICKET for r in w if r["route_fresh"]), 2),
                "best_net": round(max((r["net"] for r in fr), default=0), 5), "median_net": _med([r["net"] for r in fr]),
                "median_size_usd": _med([r["size_usd"] for r in fr]), "windows_no_depth": sum(r["size_usd"] is None for r in fr),
                "stuck_routes": len(stuck_keys)}

    day = [r for r in allw if r["time"] >= t_now - DAY_MS]
    week = [r for r in allw if r["time"] >= t_now - 7 * DAY_MS]
    top = [{"time": r["time"], "coin": r["coin"], "buy": r["buy"], "sell": r["sell"], "net": r["net"], "size_usd": r["size_usd"],
            "usd": round(_value(r), 2)} for r in reversed([r for r in allw if r["fresh"]][-15:])]
    stuck = [{"coin": b, "buy": c, "sell": d, "net": e, "since": a, "seen": k} for b, c, d, e, a, k in conn.execute(
        "SELECT coin, buy, sell, MAX(net), MIN(time), COUNT(*) FROM arb_windows WHERE time >= ? GROUP BY coin, buy, sell HAVING COUNT(*) >= 3 "
        "ORDER BY MAX(net) DESC LIMIT 10", (t_now - DAY_MS,)).fetchall()]
    n_week = conn.execute("SELECT COUNT(*) FROM arb_samples WHERE time >= ?", (t_now - 7 * DAY_MS,)).fetchone()[0] or 0
    routes = {}
    for r in week:
        g = routes.setdefault((r["coin"], r["buy"], r["sell"]), {"coin": r["coin"], "buy": r["buy"], "sell": r["sell"], "seen": 0, "fresh": 0,
                                                                  "first": r["time"], "last": r["time"], "nets": [], "sizes": []})
        g["seen"] += 1; g["fresh"] += r["fresh"]; g["last"] = r["time"]; g["nets"].append(r["net"]); g["sizes"].append(r["size_usd"])
    route_list = []
    for g in sorted(routes.values(), key=lambda g: -g["seen"])[:15]:
        nets, sizes = g.pop("nets"), g.pop("sizes")
        g.update({"share_of_samples": round(g["seen"] / n_week, 3) if n_week else None, "median_net": _med(nets), "max_net": max(nets),
                  "median_size_usd": _med(sizes), "status": "застывший" if g["seen"] > g["fresh"] and g["last"] - g["first"] >= NEXT_RUN_MS else "новый"})
        route_list.append(g)
    by_venue: dict[str, int] = {}
    for r in day:
        for v in {r["buy"], r["sell"]}:
            by_venue[v] = by_venue.get(v, 0) + 1
    st_day = conn.execute("SELECT time FROM arb_samples WHERE time >= ?", (t_now - DAY_MS,)).fetchall()
    first = conn.execute("SELECT MIN(time) FROM arb_samples").fetchone()[0]
    vl = conn.execute("SELECT venues FROM arb_samples ORDER BY time DESC LIMIT 1").fetchone()
    d = {"asof": t_now, "venues_ok": sorted(venues_ok), "venues_last": vl[0].split(",") if vl and vl[0] else [],
         "venues_all": list(VENUES), "now": last_rows[:12],
         "day": stats(t_now - DAY_MS), "week": stats(t_now - 7 * DAY_MS), "since": first, "recent_windows": top, "stuck": stuck,
         "episodes": episodes([t for (t,) in st_day], day), "routes": route_list,
         "by_venue": dict(sorted(by_venue.items(), key=lambda kv: -kv[1])),
         "rules": f"окно = ≥{MIN_NET * 100:.2f}% после комиссий обеих бирж; виртуально до ${TICKET:,.0f} на окно, но не больше, чем стоит "
                  f"в лучшей цене стакана; монета, которая на той же бирже уже давала окно за последние сутки, считается застывшей; "
                  f"разница >5% не считается (другой токен/закрыт вывод)",
         "note": "Сервер смотрит цены 3 раза за запуск (раз в 10–30 минут) — это выборка, а не непрерывное наблюдение. "
                 "Окно, которое держится часами, почти всегда «застывшее»: на бирже закрыт ввод/вывод монеты или стакан пустой — такое считаем один раз в сутки. "
                 "Оценка оптимистичная: деньги заранее на обеих биржах, исполнение по лучшей цене без задержки, перевод не нужен."}
    d["explain_ru"] = explain(d)
    return d


def _p(x: float) -> str:
    return f"{x * 100:.2f}%"


def explain(d: dict) -> str:
    s, e = d["day"], d.get("episodes") or {}
    if not s["samples"]:
        return "Данные ещё копятся: сервер пока ни разу не получил цены бирж."
    ok = d["venues_ok"] or d.get("venues_last") or []
    off = [v for v in d["venues_all"] if v not in ok]
    parts = [f"Это виртуальный счёт, не деньги. Сервер раз в 10–30 минут трижды смотрит цены {len(ok)} бирж"
             + (f" ({', '.join(off)} серверу недоступны)" if off else "") +
             f" и «засчитывает» сделку, если монета на одной бирже дешевле, чем на другой, больше чем на 0.05% после комиссий."]
    if s["observations"]:
        parts.append(f"За сутки {s['samples']} проверок, окно было в {round((s['share'] or 0) * 100)}% из них, но {s['stuck_observations']} "
                     f"из {s['observations']} наблюдений — застывшие: та же монета на той же бирже держит разницу часами.")
        if d["stuck"]:
            parts.append("Главные: " + ", ".join(f"{x['coin']} {x['buy']}→{x['sell']} до {_p(x['net'])}" for x in d["stuck"][:3]) +
                         ". Так бывает, когда на бирже закрыт ввод/вывод монеты или стакан пустой — забрать такую разницу нельзя.")
        if d["by_venue"]:
            v, k = next(iter(d["by_venue"].items()))
            parts.append(f"Чаще всего в окнах участвует {v} ({k} из {s['observations']} наблюдений).")
    parts.append(f"Новых окон за сутки {s['windows']}" + (f", обычно {_p(s['median_net'])} чистыми" if s["median_net"] is not None else "") +
                 (f", в лучшей цене стакана в среднем ${s['median_size_usd']:,.0f}".replace(",", " ") if s["median_size_usd"] is not None else "") +
                 f". Честная оценка — {'+' if s['virtual_pnl_usd'] >= 0 else '−'}${abs(s['virtual_pnl_usd']):,.2f} за сутки "
                 f"(старый подсчёт по маршрутам по $1 000 дал бы ${s['virtual_pnl_naive_usd']:,.2f}).")
    if e.get("count"):
        parts.append(f"Из {e['count']} эпизодов {e['one_sample'] + e['within_run']} исчезли в пределах одного запуска, "
                     f"{e['next_run']} дожили до следующего, {e['hour_plus']} держались час и дольше.")
    parts.append("Чтобы зарабатывать на этом вживую, нужны деньги заранее на обеих биржах (USDT там, где покупаем, монета там, "
                 "где продаём), периодический перевод для выравнивания (≈ $1–2 и от минут до часа) и робот, который смотрит "
                 "цены каждую секунду, а не раз в полчаса.")
    return " ".join(parts)
