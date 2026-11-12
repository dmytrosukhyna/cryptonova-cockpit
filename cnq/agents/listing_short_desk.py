"""«Шорт листингов (тест)» — a PAPER test desk (12.11.2026, owner 14:00: «шортить новые токены на листинге», trigger: Extended XTEN).
Owner-only, demo money: run by the API on the server (cnq/api/listing_short.py), state and file in <run>/data/listing_short, served
only to the signed-in owner. It has no «whales:» / «ivan:» signal id and is not in paper.json — the real account cannot open it.

The variant (docs/research/listing-short-2026-11-12.md, chosen by the rule registered before the run: the best mean among the
variants that passed both halves AND beat random entry dates in ≥ 95 % — Binance 2023–2026, 688 new perps, delisted included):
  a NEW OKX USDT perpetual (instruments listTime after the desk started; XTEN as soon as OKX lists it) is shorted at the close of its
  first 24 hours (the 24th hourly bar), 10 % of the paper account, 1× (no leverage), at most 5 open; out after 14 days, or at a stop
  +35 % above the entry (an hourly high; a gap fills at that bar's open — worse than the stop); 0.20 % costs per round trip; the
  real OKX funding of the hold (a short receives positive funding, pays negative). Public OKX data only, no keys."""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

H = 3_600_000
DAY = 24 * H
START = 10_000.0
SLOT = 0.10
MAX_OPEN = 5
COST = 0.002
VARIANT = {"entry_bar": 23, "hold_days": 14, "stop": 0.35, "id": "listing-short-2026-11-12-1d-14d-stop35"}
WATCH = ("XTEN",)                 # the owner's trigger coin — named in the file until OKX lists it
OKX = "https://www.okx.com"


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _http(path: str, **q):
    req = urllib.request.Request(f"{OKX}{path}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "cnq-listing-short/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    if str(d.get("code")) != "0":
        raise RuntimeError(f"OKX {path}: {d.get('msg') or d.get('code')}")
    return d.get("data") or []


def listings(http=_http) -> dict:
    """{instId: listTime ms} of the live OKX USDT swaps"""
    return {x["instId"]: int(_f(x.get("listTime"))) for x in http("/api/v5/public/instruments", instType="SWAP")
            if str(x.get("instId", "")).endswith("-USDT-SWAP") and _f(x.get("listTime"))}


def bars(inst: str, t0: int, n: int, http=_http) -> list:
    """the first n hourly bars from t0: [(open ms, o, h, l, c)]"""
    out, before = {}, str(t0 - 1)
    for _ in range(max(1, n // 100 + 2)):
        d = http("/api/v5/market/history-candles", instId=inst, bar="1H", limit=100, before=before)
        if not d:
            break
        for r in d:
            out[int(r[0])] = (int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]))
        before = str(max(int(r[0]) for r in d))
        if len(out) >= n:
            break
    return [out[t] for t in sorted(out)][:n]


def last_px(inst: str, http=_http) -> float | None:
    try:
        return _f((http("/api/v5/market/ticker", instId=inst) or [{}])[0].get("last")) or None
    except Exception:  # noqa: BLE001
        return None


def funding(inst: str, t0: int, t1: int, http=_http) -> float:
    try:
        d = http("/api/v5/public/funding-rate-history", instId=inst, limit=100)
        return sum(_f(r.get("realizedRate") or r.get("fundingRate")) for r in d if t0 < int(r["fundingTime"]) <= t1)
    except Exception:  # noqa: BLE001
        return 0.0


def step(st: dict | None, now: int, lists=listings, get_bars=bars, px_of=last_px, fund=funding) -> dict:
    v = VARIANT
    d = st or {"start": START, "cash": START, "open": [], "closed": [], "watch": {}, "events": [], "started": now}
    ev = d.setdefault("events", [])
    # 1. new listings since the desk started
    try:
        L = lists()
    except Exception as e:  # noqa: BLE001
        L = {}
        ev.append({"t": now, "kind": "error", "why": f"список инструментов OKX не прочитался: {str(e)[:100]}"})
    for inst, lt in L.items():
        if lt >= int(d["started"]) and inst not in d["watch"]:
            d["watch"][inst] = {"list_t": lt, "state": "wait"}
            ev.append({"t": now, "kind": "listing", "coin": inst.split("-")[0],
                       "why": f"новый перпетуал на OKX: {inst}, листинг {time.strftime('%d.%m %H:%M', time.gmtime(lt / 1000))} UTC"})
    # 2. open shorts: stop (hourly high since entry; a gap fills at the bar's open) or the time exit
    for o in list(d["open"]):
        hb = [b for b in get_bars(o["inst"], o["opened_t"], 24 * v["hold_days"] + 2) if b[0] >= o["opened_t"]]
        stop_px = o["entry"] * (1 + v["stop"])
        why, exit_px = None, None
        for b in hb:
            if b[2] >= stop_px:
                exit_px, why = max(stop_px, b[1]), f"стоп +{v['stop'] * 100:g}% (цена {max(stop_px, b[1]):g})"
                break
        if why is None and now >= o["opened_t"] + v["hold_days"] * DAY:
            exit_px, why = px_of(o["inst"]), f"выход по времени ({v['hold_days']} дн.)"
        if why and exit_px:
            fr = fund(o["inst"], o["opened_t"], now)
            net = -(exit_px / o["entry"] - 1) - COST + fr
            pnl = round(o["notional"] * net, 2)
            d["cash"] += pnl
            d["open"].remove(o)
            d["closed"].append({**o, "exit": exit_px, "closed_t": now, "pnl": pnl, "pnl_pct": round(net * 100, 2),
                                "funding_pct": round(fr * 100, 3), "why": why})
            ev.append({"t": now, "kind": "close", "coin": o["coin"], "pnl": pnl, "why": why})
            if o["inst"] in d["watch"]:
                d["watch"][o["inst"]]["state"] = "closed"
        else:
            p = px_of(o["inst"])
            if p:
                o["price"], o["upnl"] = p, round(o["notional"] * -(p / o["entry"] - 1), 2)
    # 3. entries: at the close of the 24th hourly bar, only when we see it within 2 hours (never at an old price)
    for inst, w in d["watch"].items():
        if w["state"] != "wait":
            continue
        t_entry = w["list_t"] + (v["entry_bar"] + 1) * H
        if now < t_entry:
            continue
        coin = inst.split("-")[0]
        if now > t_entry + 2 * H:
            w["state"] = "missed"
            ev.append({"t": now, "kind": "skip", "coin": coin, "why": "конец первых суток прошёл больше 2 часов назад — вход пропущен"})
            continue
        if len(d["open"]) >= MAX_OPEN:
            w["state"] = "skipped"
            ev.append({"t": now, "kind": "skip", "coin": coin, "why": f"уже {MAX_OPEN} бумажных шортов — предел"})
            continue
        b = get_bars(inst, w["list_t"], v["entry_bar"] + 1)
        px = px_of(inst)
        if len(b) < v["entry_bar"] + 1 or not px:
            continue                                          # data not there yet — try on the next run
        eq = d["cash"] + sum(_f(o.get("upnl")) for o in d["open"])
        o = {"inst": inst, "coin": coin, "side": "SHORT", "entry": px, "opened_t": now, "notional": round(SLOT * eq, 2),
             "stop": round(px * (1 + v["stop"]), 10), "exit_by": now + v["hold_days"] * DAY, "first_day_close": b[-1][4],
             "first_day_move_pct": round((b[-1][4] / b[0][1] - 1) * 100, 1), "price": px, "upnl": 0.0}
        d["open"].append(o)
        w["state"] = "open"
        ev.append({"t": now, "kind": "open", "coin": coin, "why": f"шорт после первых суток листинга по {px:g}, стоп {o['stop']:g}"})
    d["events"] = ev[-200:]
    d["equity"] = round(d["cash"] + sum(_f(o.get("upnl")) for o in d["open"]), 2)
    d["t"] = now
    return d


def public(d: dict, now: int) -> dict:
    cl = d.get("closed") or []
    listed = {i.split("-")[0] for i in d.get("watch") or {}}
    return {"asof": now, "name": "Шорт листингов (тест)", "test": True, "owner_only": True, "paper": True, "real_account": False,
            "label_ru": "ТЕСТ · бумажный стол, только для владельца. На реальный счёт сделки этого стола не попадают.",
            "variant": {k: VARIANT[k] for k in ("entry_bar", "hold_days", "stop")},
            "rule_ru": ("Новый перпетуал на OKX шортим на бумаге после первых суток торгов (закрытие 24-го часа), 10% бумажного счёта без "
                        f"плеча, не больше {MAX_OPEN} позиций. Выход через {VARIANT['hold_days']} дней или по стопу +{VARIANT['stop'] * 100:g}% "
                        "(при гэпе — хуже стопа). Издержки 0.20% и фандинг учтены."),
            "research": "docs/research/listing-short-2026-11-12.md",
            "watch_ru": {c: ("уже на OKX — в работе" if c in listed else "ждём листинга на OKX") for c in WATCH},
            "start": d["start"], "started": d.get("started"), "equity": d.get("equity", d["start"]),
            "pnl_usd": round(d.get("equity", d["start"]) - d["start"], 2), "trades": len(cl), "wins": sum(1 for c in cl if c["pnl"] > 0),
            "open": d.get("open") or [], "closed": sorted(cl, key=lambda c: -c["closed_t"])[:50],
            "waiting": [{"inst": i, **w} for i, w in (d.get("watch") or {}).items() if w.get("state") == "wait"],
            "events": (d.get("events") or [])[-40:]}


def run(out: str, now: int | None = None, kv_get=None, kv_set=None, **kw) -> dict:
    now = int(now or time.time() * 1000)
    st = kv_get("listing_short")
    if st and st.get("variant_id") != VARIANT["id"]:
        st = None
    d = step(st, now, **kw)
    d["variant_id"] = VARIANT["id"]
    kv_set("listing_short", d)
    doc = public(d, now)
    tmp = os.path.join(out, "listing_short.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(out, "listing_short.json"))
    return doc
