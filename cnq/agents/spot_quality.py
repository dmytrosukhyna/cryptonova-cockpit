"""Спот: анкета качества — which alts to hold, picked by what ACTUALLY predicted the next month (19.10.2026, owner: «по споту
можно проанализировать максимально всё … спот мы можем сделать идеальный, подойди профессионально»).

Evidence (scripts/own_signals_study.py --factors / --quality; every Binance USDT-M perp 2022–2026, delisted coins included, the
point-in-time top 100 alts by 30-day dollar volume with ≥ 1 year of history, monthly):
  • tokens of projects WITH REVENUE (DefiLlama: protocol / chain revenue mapped to its token) beat the other alts by +2.0 % a
    month (t 3.1, 69 % of months, plus 2023–2026);
  • OLDER coins beat younger ones (+3.0 % a month top vs bottom third, t 1.9, plus every year);
  • a RISING volume trend (30-day vs 180-day) lost (−2.9 %, t −2.0); the 3-month winners against BTC lost (−2.4 %, minus every
    year) — the hype fades;
  • revenue growth (90 days vs the 90 before) slightly positive; «cheap» revenue yield, buybacks, distance from the high — nothing.
  Basket of the 10 best by that composite (Q10) vs the 10 largest by 180-day volume (VOL10), inside the BTC regime: Q10 ×1.22 vs
  ×0.99, better in both halves; in a portfolio of BTC 80 % + alts 20 %: ×2.07 vs ×1.95. Honest: alts as a class lost to BTC
  since 2022 (BTC with the regime ×2.19, drawdown −22 %); the composite picks better alts, it does not beat BTC.

Composite (the signs from that study; equal weights, no fitting): mean of the percentile ranks inside the universe of
  age ↑ · 3-month strength vs BTC ↓ · volume trend 30 d / 180 d ↓ · revenue growth ↑ (no data = middle)  + 0.5 if the project has
  revenue. The top 10 (a held coin stays while it ranks in the top 20) → own_state.alt_basket (alt_portfolio.py, the alt part of «ядро + альтсезон»), all scores → web/quality.json.

Inputs: own_desk.py `pool_features` (own_state.pool_feats: 30/180-day dollar volume, 3-month change vs BTC, candles we have),
age = the first daily candle on Binance spot (cached; else the BingX candles we have, ≤ 400 days), DefiLlama revenue. Once a day.
Stdlib only; every fetch injectable."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

LL = "https://api.llama.fi"
OVERVIEW = f"{LL}/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue"
DAY = 86_400_000
TOP_N, BASKET_N, MIN_AGE_D = 100, 10, 365
KEEP_RANK = 20          # a basket coin stays while it ranks in the top 20 — the study rebalanced monthly; no daily churn of 2 % lots
REV_BONUS = 0.5


def _get(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 cryptonova/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def revenue_slugs(overview: dict, protocols: list, chains: list) -> dict:
    """{TOKEN: [DefiLlama slugs with revenue]} — protocols by their token symbol (or the parent's), chains by their gas token."""
    by_id = {str(p.get("defillamaId") or p.get("id")): p for p in protocols or []}
    parent_sym = {}
    for p in protocols or []:
        if p.get("parentProtocol") and p.get("symbol") and p["symbol"] != "-":
            parent_sym.setdefault(p["parentProtocol"], p["symbol"].upper())
    chain_sym = {str(c.get("name") or "").lower(): str(c.get("tokenSymbol") or "").upper() for c in chains or [] if c.get("tokenSymbol")}
    out: dict[str, set] = {}
    for e in (overview or {}).get("protocols") or []:
        slug = e.get("slug") or e.get("module")
        if not slug or not (e.get("total30d") or 0) > 0:
            continue
        p = by_id.get(str(e.get("defillamaId") or e.get("id")))
        sym = None
        if p and p.get("symbol") and p["symbol"] != "-":
            sym = p["symbol"].upper()
        elif e.get("parentProtocol") and e["parentProtocol"] in parent_sym:
            sym = parent_sym[e["parentProtocol"]]
        elif str(e.get("protocolType") or e.get("category") or "").lower() == "chain":
            sym = chain_sym.get(str(e.get("name") or "").lower())
        if sym:
            out.setdefault(sym, set()).add(slug)
    return {k: sorted(v) for k, v in out.items()}


def revenue_series(slug: str, fetch=_get) -> dict:
    j = fetch(f"{LL}/summary/fees/{slug}?dataType=dailyRevenue") or {}
    out = {}
    for row in j.get("totalDataChart") or []:
        try:
            out[int(row[0]) * 1000 // DAY * DAY] = float(row[1] or 0)
        except (TypeError, ValueError, IndexError):
            continue
    return out


def binance_first_day(sym: str, fetch=_get) -> int | None:
    base = sym[4:] if sym.startswith("1000") else sym
    try:
        d = fetch(f"https://data-api.binance.vision/api/v3/klines?symbol={base}USDT&interval=1d&startTime=0&limit=1")
        return int(d[0][0]) if d else None
    except Exception:  # noqa: BLE001
        return None


def composite(rows: dict) -> dict:
    """rows {SYM: {age, m3, vt, rg, rev}} → {SYM: score}. Percentile ranks inside the set; missing = the middle."""
    syms = list(rows)

    def prank(key):
        xs = sorted((rows[s][key], s) for s in syms if rows[s].get(key) is not None)
        n = max(1, len(xs) - 1)
        r = {s: k / n for k, (_v, s) in enumerate(xs)}
        return {s: r.get(s, 0.5) for s in syms}
    ra, rm, rv, rg = prank("age"), prank("m3"), prank("vt"), prank("rg")
    return {s: round((ra[s] + (1 - rm[s]) + (1 - rv[s]) + rg[s]) / 4 + (REV_BONUS if rows[s].get("rev") else 0.0), 4) for s in syms}


def _state(conn, k):
    try:
        r = conn.execute("SELECT v FROM own_state WHERE k=?", (k,)).fetchone()
    except sqlite3.Error:
        return None
    return json.loads(r[0]) if r else None


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, fetch=_get, first_day=None, workers: int = 6) -> dict:
    now = now or int(time.time() * 1000)
    day = now // DAY * DAY
    conn.execute("CREATE TABLE IF NOT EXISTS sq_state(k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS sq_age(symbol TEXT PRIMARY KEY, first_ms INTEGER)")
    last = conn.execute("SELECT v FROM sq_state WHERE k='day'").fetchone()
    if last and int(last[0]) == day and os.path.exists(os.path.join(out, "quality.json")):
        return {"skipped": "done today"}
    feats = _state(conn, "pool_feats") or {}
    if not feats:
        return {"error": "no pool features yet (own_desk.py writes them at its daily step)"}
    # age: Binance spot first candle (cached), else the BingX candles we have
    have = {s: v for s, v in conn.execute("SELECT symbol, first_ms FROM sq_age")}
    miss = [s for s in feats if s not in have][:60]
    fd = first_day or (lambda s: binance_first_day(s, fetch))
    with ThreadPoolExecutor(workers) as ex:
        for s, f in zip(miss, ex.map(fd, miss)):
            have[s] = f or 0
            conn.execute("INSERT OR REPLACE INTO sq_age VALUES(?,?)", (s, f or 0))
    conn.commit()

    def age_days(s):
        f = have.get(s) or 0
        return max((now - f) / DAY if f else 0, feats[s].get("n") or 0)
    # the universe: the top 100 alts by 30-day dollar volume with ≥ 1 year of history
    alts = [s for s in feats if s not in ("BTC", "ETH") and age_days(s) >= MIN_AGE_D]
    uni = sorted(alts, key=lambda s: -(feats[s].get("dv30") or 0))[:TOP_N]
    # revenue
    err = None
    slugs = {}
    try:
        slugs = revenue_slugs(fetch(OVERVIEW), fetch(f"{LL}/protocols"), fetch(f"{LL}/v2/chains"))
    except Exception as e:  # noqa: BLE001
        err = f"DefiLlama: {str(e)[:100]}"

    def base(s):
        return s[4:] if s.startswith("1000") else s
    jobs = [(s, sl) for s in uni for sl in slugs.get(base(s), [])]
    ser = {}
    with ThreadPoolExecutor(workers) as ex:
        for (s, _sl), rs in zip(jobs, ex.map(lambda j: _safe(revenue_series, j[1], fetch), jobs)):
            tgt = ser.setdefault(s, {})
            for d, v in (rs or {}).items():
                tgt[d] = tgt.get(d, 0.0) + v

    def ssum(x, end, n):
        return sum(x.get(end - k * DAY, 0.0) for k in range(n))
    rows = {}
    for s in uni:
        x = ser.get(s) or {}
        r30, r90, r90p = ssum(x, day - DAY, 30), ssum(x, day - DAY, 90), ssum(x, day - 91 * DAY, 90)
        dv30, dv180 = feats[s].get("dv30") or 0, feats[s].get("dv180") or 0
        rows[s] = {"age": round(age_days(s)), "m3": feats[s].get("m3"), "vt": round(dv30 / dv180, 3) if dv180 else None,
                   "rg": round(r90 / r90p - 1, 3) if r90p > 0 and r90 > 0 else None, "rev": r30 > 0, "rev30": round(r30)}
    sc = composite(rows) if rows else {}
    order = sorted(sc, key=lambda s: -sc[s])
    prev = conn.execute("SELECT v FROM sq_state WHERE k='basket'").fetchone()
    prev = json.loads(prev[0]) if prev else []
    keep = [s for s in prev if s in order[:KEEP_RANK]]
    basket = (keep + [s for s in order if s not in keep])[:BASKET_N]
    if len(basket) == BASKET_N and not err:
        conn.execute("CREATE TABLE IF NOT EXISTS own_state(k TEXT PRIMARY KEY, v TEXT)")
        conn.execute("INSERT OR REPLACE INTO own_state VALUES('alt_basket', ?)", (json.dumps(basket),))
        conn.execute("INSERT OR REPLACE INTO sq_state VALUES('basket', ?)", (json.dumps(basket),))
    conn.execute("INSERT OR REPLACE INTO sq_state VALUES('day', ?)", (str(day),))
    conn.commit()
    doc = {"asof": now, "error": err, "universe": len(uni), "basket": basket,
           "rows": [{"symbol": s, "rank": k + 1, "score": sc[s], **rows[s]} for k, s in enumerate(order)],
           "method_ru": ("Из 100 самых торгуемых альтов (с историей от года) выше оценка у тех, у чьих проектов есть выручка, кто старше, "
                         "у кого объём торгов не раздут и кто не вырос резко за 3 месяца, плюс рост выручки. Так на истории 2022–2026 "
                         "выбор альтов был лучше: выручка дала +2% в месяц к остальным альтам, возраст +3%. Дешевизна к выручке, выкуп "
                         "токенов и «далеко от максимума» не помогали. Важно: альты в целом с 2022 года отставали от биткоина."),
           "evidence": {"revenue_vs_other_alts_month_pct": 2.0, "t": 3.1, "age_top_vs_bottom_month_pct": 3.0,
                        "q10_vs_vol10_in_regime_x": [1.22, 0.99], "btc_regime_x": 2.19}}
    with open(os.path.join(out, "quality.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return None
