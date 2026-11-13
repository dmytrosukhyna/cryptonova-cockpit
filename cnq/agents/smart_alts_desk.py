"""«Альты от китов (тест)» — a PAPER test desk (13.11.2026, owner 22:03). Owner-only, demo money: run by the API on the server
(cnq/api/smart_alts.py), state and file in <run>/data/smart_alts, served only to the signed-in owner. No «whales:» / «ivan:» signal
id, not in paper.json — the real account can never open it.

Source: web/smart_money.json «watch.tokens» (any chain; the price is the one that file carries — DexScreener for DEX tokens).
  buy   — a token whose status turns «накопление» with ≥ MIN_BUYERS labelled buyers (market maker / fund / known or top trader)
          and pool liquidity ≥ MIN_LIQ: 10 % of the paper account, no leverage, at most MAX_OPEN positions;
  sell  — the status turns «продают», or the buyers drop below MIN_BUYERS, or −25 % from the entry, or 30 days;
  costs — 0.3 % per side.
Benchmarks with the SAME timing and size: every entry also «buys» BTC and an equal-weight basket of the top-20 alts by market cap
(web/markets.json: no stablecoins / wrapped, BTC and ETH excluded) and closes them when the desk's position closes.
Prices are checked when this runs (every 10 minutes) on a file the pipeline refreshes about every 30 minutes — a stop can fill worse."""
from __future__ import annotations

import json
import os
import time

DAY = 86_400_000
START = 10_000.0
SLOT = 0.10
MAX_OPEN = 8
COST = 0.003
MIN_BUYERS = 2
MIN_LIQ = 500_000.0
STOP = 0.25
HOLD_DAYS = 30
TOP_ALTS = 20
VERSION = "smart-alts-2026-11-13"


def _f(x, d=None):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def tokens(sm: dict) -> dict:
    """{key: token row} of web/smart_money.json watch"""
    return {t["key"]: t for t in ((sm.get("watch") or {}).get("tokens") or []) if isinstance(t, dict) and t.get("key")}


def market(mk: dict) -> tuple[float | None, dict]:
    """(BTC price, {top-20 alt: price})"""
    rows = [r for r in mk.get("top") or [] if isinstance(r, dict) and _f(r.get("price"))]
    btc = next((_f(r["price"]) for r in rows if r.get("symbol") == "BTC"), None)
    alts = [r for r in sorted(rows, key=lambda r: r.get("rank") or 9999)
            if not r.get("stable") and not r.get("wrapped") and r.get("symbol") not in ("BTC", "ETH")][:TOP_ALTS]
    return btc, {r["symbol"]: _f(r["price"]) for r in alts}


def buyers_ok(t: dict) -> int:
    return int(_f(t.get("n"), 0) or 0)


def _bench_value(b: dict, btc: float | None, alts: dict) -> tuple[float | None, float | None]:
    """current value of the benchmark legs of one position"""
    vb = b["usd"] * btc / b["btc"] if btc and b.get("btc") else None
    parts = [b["usd"] / len(b["alts"]) * alts[s] / p for s, p in b["alts"].items() if alts.get(s)] if b.get("alts") else []
    va = sum(parts) * len(b["alts"]) / len(parts) if parts else None          # a coin that left the top-20: the others carry it
    return vb, va


def step(st: dict | None, sm: dict, mk: dict, now: int) -> dict:
    d = st or {"start": START, "cash": START, "open": [], "closed": [], "events": [], "started": now, "seen": {},
               "bench": {"btc_pnl": 0.0, "alts_pnl": 0.0}}
    ev = d.setdefault("events", [])
    T = tokens(sm)
    btc, alts = market(mk)
    # 1. exits
    for o in list(d["open"]):
        t = T.get(o["key"]) or {}
        px = _f(t.get("price"))
        why = None
        if px and px <= o["entry"] * (1 - STOP):
            why = f"стоп −{STOP * 100:g}%"
        elif t.get("status") == "продают":
            why = "умные кошельки продают"
        elif t and buyers_ok(t) < MIN_BUYERS:
            why = f"покупателей меньше {MIN_BUYERS}"
        elif not t:
            why = "токен выбыл из наблюдения" if o.get("missing_since") and now - o["missing_since"] > 2 * DAY else None
            o.setdefault("missing_since", now)
        elif now - o["opened_t"] >= HOLD_DAYS * DAY:
            why = f"{HOLD_DAYS} дней прошло"
        if t:
            o.pop("missing_since", None)
        exit_px = px or o.get("price")
        if why and exit_px:
            net = exit_px / o["entry"] - 1 - 2 * COST
            pnl = round(o["notional"] * net, 2)
            vb, va = _bench_value(o["bench"], btc, alts)
            bp = round((vb or o["bench"]["usd"]) * (1 - COST) - o["bench"]["usd"] * (1 + COST), 2)
            ap = round((va or o["bench"]["usd"]) * (1 - COST) - o["bench"]["usd"] * (1 + COST), 2)
            d["cash"] += pnl
            d["bench"]["btc_pnl"] = round(d["bench"]["btc_pnl"] + bp, 2)
            d["bench"]["alts_pnl"] = round(d["bench"]["alts_pnl"] + ap, 2)
            d["open"].remove(o)
            d["closed"].append({**{k: v for k, v in o.items() if k != "bench"}, "exit": exit_px, "closed_t": now, "pnl": pnl,
                                "pnl_pct": round(net * 100, 2), "why": why, "btc_same_time_usd": bp, "alts_same_time_usd": ap})
            ev.append({"t": now, "kind": "close", "symbol": o["symbol"], "pnl": pnl, "why": why})
        elif px:
            o["price"], o["upnl"] = px, round(o["notional"] * (px / o["entry"] - 1), 2)
    # 2. entries: a token that TURNS «накопление» (seen in another status before, or new since the desk started)
    held = {o["key"] for o in d["open"]}
    seen = d.setdefault("seen", {})
    for k, t in T.items():
        prev = seen.get(k)
        seen[k] = t.get("status")
        turned = t.get("status") == "накопление" and prev != "накопление" and (prev is not None or now - int(d["started"]) > 0)
        if not turned or k in held:
            continue
        px, liq, n = _f(t.get("price")), _f(t.get("liq"), 0.0), buyers_ok(t)
        sym = t.get("symbol") or k
        if n < MIN_BUYERS or liq < MIN_LIQ or not px:
            ev.append({"t": now, "kind": "skip", "symbol": sym,
                       "why": f"накопление, но {'покупателей ' + str(n) if n < MIN_BUYERS else 'ликвидность $' + format(liq or 0, ',.0f') if liq < MIN_LIQ else 'нет цены'}"})
            continue
        if len(d["open"]) >= MAX_OPEN:
            ev.append({"t": now, "kind": "skip", "symbol": sym, "why": f"уже {MAX_OPEN} бумажных позиций — предел"})
            continue
        eq = d["cash"] + sum(_f(o.get("upnl"), 0.0) for o in d["open"])
        usd = round(SLOT * eq, 2)
        o = {"key": k, "symbol": sym, "chain": t.get("chain"), "entry": px, "opened_t": now, "notional": usd, "price": px, "upnl": 0.0,
             "buyers": [b.get("label") for b in t.get("buyers") or []][:6], "liq": liq, "url": t.get("url"),
             "bench": {"usd": usd, "btc": btc, "alts": dict(alts)}}
        d["open"].append(o)
        held.add(k)
        ev.append({"t": now, "kind": "open", "symbol": sym, "why": f"накопление: {n} покупателя ({', '.join(o['buyers'][:3])}), ликвидность ${liq:,.0f}"})
    d["events"] = ev[-200:]
    d["equity"] = round(d["cash"] + sum(_f(o.get("upnl"), 0.0) for o in d["open"]), 2)
    # benchmarks: closed legs + the open legs at today's prices
    ob = oa = 0.0
    for o in d["open"]:
        vb, va = _bench_value(o["bench"], btc, alts)
        ob += (vb or o["bench"]["usd"]) - o["bench"]["usd"]
        oa += (va or o["bench"]["usd"]) - o["bench"]["usd"]
    d["bench_now"] = {"btc_usd": round(d["bench"]["btc_pnl"] + ob, 2), "alts_usd": round(d["bench"]["alts_pnl"] + oa, 2)}
    d["t"] = now
    return d


def public(d: dict, now: int, bt: dict | None = None) -> dict:
    cl = d.get("closed") or []
    return {"asof": now, "name": "Альты от китов (тест)", "test": True, "owner_only": True, "paper": True, "real_account": False,
            "label_ru": "ТЕСТ · бумажный стол, только для владельца. На реальный счёт сделки этого стола не попадают.",
            "rule_ru": (f"Покупаем на бумаге токен, у которого статус сменился на «накопление»: ≥ {MIN_BUYERS} помеченных покупателя "
                        f"(маркетмейкер, фонд, известный или топ-трейдер) и ликвидность ≥ ${MIN_LIQ:,.0f}. 10% бумажного счёта, без плеча, "
                        f"не больше {MAX_OPEN} позиций. Продаём, когда кошельки продают, покупателей меньше {MIN_BUYERS}, −{STOP * 100:g}% "
                        f"или через {HOLD_DAYS} дней. Издержки 0.3% на вход и на выход. Для сравнения на те же даты и суммы — BTC и "
                        "корзина из 20 крупнейших альтов."),
            "start": d["start"], "started": d.get("started"), "equity": d.get("equity", d["start"]),
            "pnl_usd": round(d.get("equity", d["start"]) - d["start"], 2), "trades": len(cl), "wins": sum(1 for c in cl if c["pnl"] > 0),
            "benchmarks": {"btc_same_timing_usd": (d.get("bench_now") or {}).get("btc_usd"),
                           "top20_alts_same_timing_usd": (d.get("bench_now") or {}).get("alts_usd")},
            "open": [{k: v for k, v in o.items() if k != "bench"} for o in d.get("open") or []],
            "closed": sorted(cl, key=lambda c: -c["closed_t"])[:50], "events": (d.get("events") or [])[-40:], "backtest": bt}


def backtest(snap_dir: str) -> dict | None:
    """the same rules replayed on the daily snapshots of the watch list (data/smart_money/watchlist/*.json, since 04.11) — one
    look a day, so stops and statuses act a day late at most. A first look, not evidence: the history is a few weeks long."""
    try:
        files = sorted(f for f in os.listdir(snap_dir) if f.endswith(".json"))
    except OSError:
        return None
    d, t_last = None, None
    for i, f in enumerate(files):
        sm = _load(os.path.join(snap_dir, f))
        t = int(_f(sm.get("asof"), 0) or 0) or (i + 1) * DAY
        if d is None:                                            # day one is the baseline: what was already «накопление» is not new
            d = step(None, {"watch": {"tokens": []}}, {}, t - 1)
            d["seen"] = {k: x.get("status") for k, x in tokens(sm).items()}
            t_last = t
            continue
        d = step(d, {"watch": sm}, {}, t)
        t_last = t
    if d is None:
        return None
    cl = d.get("closed") or []
    return {"days": len(files), "from": files[0][:10], "to": files[-1][:10], "trades_closed": len(cl), "open_now": len(d["open"]),
            "pnl_usd": round(d.get("equity", START) - START, 2), "closed": [{k: c[k] for k in ("symbol", "pnl_pct", "why")} for c in cl],
            "open": [{"symbol": o["symbol"], "upnl_pct": round((o.get("price", o["entry"]) / o["entry"] - 1) * 100, 1)} for o in d["open"]],
            "note_ru": "Короткая история (несколько недель, один снимок в день, без цен BTC и альтов на те же даты) — это не доказательство."}


def run(out: str, web: str, now: int | None = None, kv_get=None, kv_set=None, snap_dir: str | None = None) -> dict:
    now = int(now or time.time() * 1000)
    st = kv_get("smart_alts")
    if st and st.get("version") != VERSION:
        st = None
    sm, mk = _load(os.path.join(web, "smart_money.json")), _load(os.path.join(web, "markets.json"))
    if st is None:                                              # the first run is the baseline: only a CHANGE to «накопление» buys
        st = step(None, {"watch": {"tokens": []}}, mk, now)
        st["seen"] = {k: t.get("status") for k, t in tokens(sm).items()}
    d = step(st, sm, mk, now)
    d["version"] = VERSION
    kv_set("smart_alts", d)
    bt = backtest(snap_dir) if snap_dir else None
    doc = public(d, now, bt)
    tmp = os.path.join(out, "smart_alts.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(out, "smart_alts.json"))
    return doc
