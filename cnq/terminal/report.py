"""Terminal report builder — turns stored candles (+ optional derivatives columns and
footprint ladder) into one JSON the web terminal renders, plus a Russian daily brief.

Everything here is deterministic. Numbers come from data; text comes from rules.
Scenario probabilities are NOT invented: where no calibrated statistic exists the
brief says so and gives the invalidation level instead.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np

from ..bingx.rest import Candle
from ..features.candles import arrays, atr, resample
from ..features.levels import LevelContext, build_levels
from ..setups.discovery import Discovery, DiscoveryParams
from ..research.derivatives import _pct_rank_trailing
from .profiles import profile


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.0f}".replace(",", " ")
    if p >= 1:
        return f"{p:.2f}"
    return f"{p:.4f}"


# ------------------------------------------------------------------ regime
def regime(cs15: list[Candle]) -> dict:
    """Three horizons from daily bars: short (3d), mid (28d trend rule), long (90d / 200d SMA)."""
    d = resample(cs15, 96)
    a = arrays(d)
    c = a["c"]; n = len(c)
    if n < 60:
        return {"short": "UNAVAILABLE", "mid": "UNAVAILABLE", "long": "UNAVAILABLE"}
    atr_d = atr(a["h"], a["l"], a["c"], 14)
    last = c[-1]
    r3 = last / c[-4] - 1; r7 = last / c[-8] - 1; r28 = last / c[-29] - 1
    r90 = last / c[-91] - 1 if n > 91 else float("nan")
    r60 = last / c[-61] - 1 if n > 61 else float("nan")       # 18.10: the allocator's regime uses 60 days (robustness test)
    hh20 = a["h"][-21:-1].max(); ll20 = a["l"][-21:-1].min()
    sma200 = c[-200:].mean() if n >= 200 else c.mean()
    rv30 = float(np.std(np.diff(np.log(c[-31:]))) * math.sqrt(365))
    short = "UP" if r3 > 0.5 * atr_d[-1] / last else ("DOWN" if r3 < -0.5 * atr_d[-1] / last else "FLAT")
    if r28 > 0 and last >= hh20:
        mid, mid_note = "LONG", "28-дневный тренд вверх и цена выше максимума 20 дней — наблюдение тренда (не проверенное правило)"
    elif r28 < 0 and last <= ll20:
        mid, mid_note = "SHORT", "28-дневный тренд вниз и цена ниже минимума 20 дней"
    elif r28 > 0:
        mid, mid_note = "UP_NO_BREAKOUT", "тренд вверх, но без обновления максимума 20 дней — ждать"
    else:
        mid, mid_note = "DOWN_NO_BREAKOUT", "тренд вниз, но без обновления минимума 20 дней — ждать"
    long_ = "BULL" if last > sma200 and (math.isnan(r90) or r90 > 0) else ("BEAR" if last < sma200 else "MIXED")
    return {"short": short, "mid": mid, "mid_note": mid_note, "long": long_,
            "r3": r3, "r7": r7, "r28": r28, "r60": r60, "r90": r90, "hh20": float(hh20), "ll20": float(ll20),
            "sma200": float(sma200), "atr_d": float(atr_d[-1]), "atr_pct": float(atr_d[-1] / last * 100),
            "rv30": rv30, "vol_weight": min(1.0, 0.30 / rv30) if rv30 > 0 else 0.0}


# ------------------------------------------------------------------ levels
def level_map(cs15: list[Candle], max_levels: int = 8) -> list[dict]:
    ctx = LevelContext(cs15, 16, every=1, lookback=300, left=3, right=3, min_score=0.0)
    i = len(cs15) - 1
    a = arrays(cs15); atr15 = float(atr(a["h"], a["l"], a["c"])[-1])
    price = cs15[-1].close
    out = []
    for L in ctx.levels_at(i):
        dist = (L.center - price) / price * 100
        dist_atr = abs(L.center - price) / atr15 if atr15 else 99
        state = "IN_ZONE" if L.lo - 0.1 * atr15 <= price <= L.hi + 0.1 * atr15 else (
            "APPROACHING" if dist_atr <= 2.0 else ("NEAR" if dist_atr <= 6.0 else "FAR"))
        out.append({"id": L.id, "kind": L.kind, "lo": L.lo, "hi": L.hi, "center": L.center,
                    "touches": L.touch_count, "status": L.status, "score": L.score,
                    "dist_pct": round(dist, 2), "dist_atr": round(dist_atr, 1), "state": state})
    out.sort(key=lambda x: x["dist_atr"])
    active = [x for x in out if x["status"] == "ACTIVE" and x["touches"] >= 2]
    return active[:max_levels]


# ------------------------------------------------------------------ setups
def setups_now(cs15: list[Candle], lookback_bars: int = 96 * 7) -> list[dict]:
    """Run discovery over the last week so the terminal shows live statuses."""
    start = max(0, len(cs15) - 96 * 60)
    window = cs15[start:]
    disc = Discovery("", window, DiscoveryParams())
    for i in range(len(window)):
        disc.step(i)
    recent = [s for s in disc.setups if s.created_index >= len(window) - lookback_bars]
    out = []
    for s in recent:
        ci = s.evidence.get("call_index")
        if s.status == "CALL" and ci is not None and len(window) - 1 - ci > 8:
            s.move("EXPIRED", window[-1].open_time, "limit not filled within 2h; order cancelled")
        out.append({"id": s.id, "family": s.family, "direction": s.direction, "status": s.status,
                    "time": s.updated_time, "entry": s.entry, "stop": s.stop, "zone": s.entry_zone,
                    "level_lo": s.level_lo, "level_hi": s.level_hi, "rule": s.confirm_rule,
                    "reason": s.transitions[-1][3] if s.transitions else ""})
    out.sort(key=lambda x: -x["time"])
    return out[:12]


# ------------------------------------------------------------------ derivatives
def derivatives_snapshot(m: dict | None) -> dict:
    if not m:
        return {"available": False}
    n = len(m["t"])
    f = m["funding_rate_pct"]; oi = m["open_interest_usd"]; ll = m["long_liq_usd"]; sl = m["short_liq_usd"]
    rank_f = _pct_rank_trailing(f, 96 * 90)
    last_ok = n - 1
    while last_ok > 0 and (np.isnan(oi[last_ok]) or m["is_imputed_metrics"][last_ok] or f[last_ok] == 0):
        last_ok -= 1
    oi24 = oi[last_ok] / oi[max(0, last_ok - 96)] - 1 if oi[max(0, last_ok - 96)] else float("nan")
    liq_long_24 = float(np.nansum(np.abs(ll[-96:]))); liq_short_24 = float(np.nansum(np.abs(sl[-96:])))
    tb = m["taker_buy_vol_btc"][-96:]; ts = m["taker_sell_vol_btc"][-96:]
    taker_ratio = float(np.nansum(tb) / np.nansum(ts)) if np.nansum(ts) > 0 else float("nan")
    return {"available": True, "funding_pct": float(f[last_ok]), "funding_rank90d": float(rank_f[last_ok]) if not np.isnan(rank_f[last_ok]) else None,
            "metrics_time": int(m["t"][last_ok]),
            "oi_usd": float(oi[last_ok]), "oi_change_24h": float(oi24), "liq_long_24h_usd": liq_long_24,
            "liq_short_24h_usd": liq_short_24, "taker_buy_sell_24h": taker_ratio,
            "ls_ratio_global": float(m["ls_ratio_global"][last_ok]), "ls_ratio_top": float(m["ls_ratio_top"][last_ok])}


def liquidation_events(cs15: list[Candle], m: dict | None, days: int = 7) -> list[dict]:
    """Recent 3σ bars with extreme same-side liquidations (the LIQ_CASCADE family's trigger)."""
    if not m:
        return []
    a = arrays(cs15); n = len(cs15)
    ret = np.concatenate([[0.0], np.diff(a["c"]) / a["c"][:-1]])
    ll, sl = np.abs(m["long_liq_usd"]), np.abs(m["short_liq_usd"])
    rl = _pct_rank_trailing(ll, 96 * 90); rs = _pct_rank_trailing(sl, 96 * 90)
    out = []
    for i in range(max(96 * 90, n - 96 * days), n):
        sig = ret[i - 96 * 30: i].std()
        if sig == 0 or m["is_imputed_metrics"][i]:
            continue
        if ret[i] <= -3 * sig and rl[i] >= 0.95:
            out.append({"time": int(a["t"][i]), "side": "LONG_LIQUIDATIONS", "ret_pct": round(ret[i] * 100, 2),
                        "liq_usd": float(ll[i]), "low": float(a["l"][i]), "high": float(a["h"][i]),
                        "wick_ok": (min(a["o"][i], a["c"][i]) - a["l"][i]) >= 0.5 * (a["h"][i] - a["l"][i])})
        elif ret[i] >= 3 * sig and rs[i] >= 0.95:
            out.append({"time": int(a["t"][i]), "side": "SHORT_LIQUIDATIONS", "ret_pct": round(ret[i] * 100, 2),
                        "liq_usd": float(sl[i]), "low": float(a["l"][i]), "high": float(a["h"][i]),
                        "wick_ok": (a["h"][i] - max(a["o"][i], a["c"][i])) >= 0.5 * (a["h"][i] - a["l"][i])})
    return out[-10:]


# ------------------------------------------------------------------ footprint
def footprint(ladder: dict[int, list[tuple]] | None, cs15: list[Candle], bars: int = 48) -> dict:
    if not ladder:
        return {"available": False}
    rows = []
    for c in cs15[-bars:]:
        rungs = ladder.get(c.open_time, [])
        rows.append({"t": c.open_time, "o": c.open, "h": c.high, "l": c.low, "c": c.close,
                     "rungs": [{"p": r[0], "bid": r[1], "ask": r[2], "delta": r[3], "vol": r[4], "poc": r[6],
                                "bimb": r[7], "simb": r[8]} for r in rungs]})
    return {"available": True, "bars": rows}


# ------------------------------------------------------------------ brief
def coin_brief(sym: str, price: float, reg: dict, levels: list[dict], setups: list[dict], deriv: dict) -> dict:
    sup = [L for L in levels if L["center"] < price]
    res = [L for L in levels if L["center"] > price]
    s0 = sup[0] if sup else None; r0 = res[0] if res else None
    lines = []
    # short term
    st = {"UP": "импульс вверх", "DOWN": "импульс вниз", "FLAT": "без направления"}[reg["short"]]
    s_txt = f"Краткосрок (1–3 дня): {st}, за 3 дня {reg['r3']*100:+.1f}%. Дневной ATR {reg['atr_pct']:.1f}%."
    if r0:
        s_txt += f" Ближайшее сопротивление {fmt_price(r0['lo'])}–{fmt_price(r0['hi'])} ({r0['touches']} касаний, {r0['dist_pct']:+.1f}%)."
    if s0:
        s_txt += f" Ближайшая поддержка {fmt_price(s0['lo'])}–{fmt_price(s0['hi'])} ({s0['touches']} касаний, {s0['dist_pct']:+.1f}%)."
    lines.append(s_txt)
    # mid term
    mid_map = {"LONG": "лонг-режим", "SHORT": "шорт-режим", "UP_NO_BREAKOUT": "рост без пробоя", "DOWN_NO_BREAKOUT": "снижение без пробоя"}
    m_txt = f"Среднесрок (1–4 недели): {mid_map.get(reg['mid'], reg['mid'])} — {reg['mid_note']}. За 28 дней {reg['r28']*100:+.1f}%."
    if reg["mid"] == "LONG":
        m_txt += f" Сценарий: продолжение, пока цена выше {fmt_price(reg['ll20'])} (минимум 20 дней); ориентир стопа 2 ATR = {fmt_price(price - 2*reg['atr_d'])}."
    elif reg["mid"] == "SHORT":
        m_txt += f" Сценарий: продолжение вниз, пока цена ниже {fmt_price(reg['hh20'])}; стоп 2 ATR = {fmt_price(price + 2*reg['atr_d'])}."
    else:
        trig = reg["hh20"] if reg["r28"] > 0 else reg["ll20"]
        m_txt += f" Триггер режима: закрытие дня {'выше' if reg['r28'] > 0 else 'ниже'} {fmt_price(trig)}."
    lines.append(m_txt)
    # long term
    l_map = {"BULL": "бычий (цена выше 200-дневной средней)", "BEAR": "медвежий (ниже 200-дневной средней)", "MIXED": "смешанный"}
    r90 = f", за 90 дней {reg['r90']*100:+.1f}%" if not (isinstance(reg["r90"], float) and math.isnan(reg["r90"])) else ""
    lines.append(f"Долгосрок (1–3 месяца): {l_map[reg['long']]}{r90}. SMA200 = {fmt_price(reg['sma200'])}. "
                 f"Размер по волатильности: {reg['vol_weight']*100:.0f}% от базового (годовая вола {reg['rv30']*100:.0f}%).")
    # derivatives
    if deriv.get("available"):
        f = deriv["funding_pct"]; rk = deriv.get("funding_rank90d")
        warn = ""
        if rk is not None and rk >= 0.95:
            warn = " Funding у верхней границы за 90 дней — лонги переполнены, риск сквиза вниз."
        elif rk is not None and rk <= 0.05:
            warn = " Funding у нижней границы — шорты переполнены, риск сквиза вверх."
        lines.append(f"Деривативы: funding {f:+.3f}%, OI за 24 ч {deriv['oi_change_24h']*100:+.1f}%, "
                     f"ликвидации 24 ч: лонги ${deriv['liq_long_24h_usd']/1e6:.1f}M / шорты ${deriv['liq_short_24h_usd']/1e6:.1f}M, "
                     f"тейкеры buy/sell {deriv['taker_buy_sell_24h']:.2f}.{warn}")
    # setups
    live = [s for s in setups if s["status"] in ("ATTENTION", "WAIT_CONFIRMATION", "READY", "CALL", "POSITION_OPEN")]
    if live:
        s = live[0]
        lines.append(f"Сетап: {s['family']} {s['direction']} — статус {s['status']}; вход {fmt_price(s['entry'])}, стоп {fmt_price(s['stop'])} "
                     f"(риск {abs(s['entry']-s['stop'])/s['entry']*100:.2f}%). Правило: {s['rule']}.")
    else:
        near = [L for L in levels if L["state"] in ("APPROACHING", "IN_ZONE")]
        if near:
            L = near[0]
            side = "лонг от поддержки" if L["center"] < price else "шорт от сопротивления"
            lines.append(f"Подготовка: цена в {L['dist_atr']} ATR от зоны {fmt_price(L['lo'])}–{fmt_price(L['hi'])} ({L['touches']} касаний). "
                         f"План: лимитка на краю зоны ({side}), стоп за зоной + 0.25 ATR, одна цель 1–1.5R.")
        else:
            lines.append("Сетапов нет: цена между зонами, ближайшая дальше 2 ATR. Ждём.")
    return {"symbol": sym, "lines": lines}


def market_brief(coins: list[dict], asof: int, portfolio: dict | None = None) -> str:
    dt = datetime.fromtimestamp(asof / 1000, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    bull = [c["symbol"] for c in coins if c["regime"]["mid"] == "LONG"]
    bear = [c["symbol"] for c in coins if c["regime"]["mid"] == "SHORT"]
    long_bull = sum(c["regime"]["long"] == "BULL" for c in coins)
    head = [f"CRYPTO NOVΛ — рынок на {dt}",
            f"Режим: {long_bull} из {len(coins)} монет выше 200-дневной. Лонг-режим по тренду: {', '.join(bull) or 'нет'}. "
            f"Шорт-режим: {', '.join(bear) or 'нет'}."]
    if portfolio:
        p = portfolio
        head.append(f"BTC {'выше' if p['btc_bull'] else 'ниже'} SMA200 → трендовые лонги по альтам {'разрешены' if p['btc_bull'] else 'заблокированы'}. "
                    f"Сигналы тренда сейчас: {', '.join(p['trend_longs']) or 'нет'}"
                    + (f" (заблокированы фильтром BTC: {', '.join(p['trend_longs_blocked'])})" if p['trend_longs_blocked'] else "") + ".")
        head.append("Лидеры 21 дня: " + ", ".join(f"{s.replace('USDT','')} {r*100:+.0f}%" for s, r in p["xs_top"]) +
                    ". Аутсайдеры: " + ", ".join(f"{s.replace('USDT','')} {r*100:+.0f}%" for s, r in p["xs_bottom"]) + ".")
    body = []
    for c in coins:
        body.append(f"\n{c['symbol']} {fmt_price(c['price'])} ({c['chg24']*100:+.1f}% за 24 ч)\n" + "\n".join("• " + l for l in c["brief"]["lines"]))
    tail = ["\nНе финансовый совет. Вероятности сценариев не калиброваны — указаны уровни отмены.",
            "Сетапы по правилу: лимитка на краю зоны 4h-уровня (≥2 касания), стоп за зоной + 0.25 ATR, одна цель 1–1.5R, только мейкерское исполнение."]
    return "\n".join(head + body + tail)


# ------------------------------------------------------------------ main
def build_report(universe: dict[str, list[Candle]], masters: dict[str, dict] | None = None,
                 ladders: dict[str, dict] | None = None, chart_bars: int = 96 * 10) -> dict:
    masters = masters or {}; ladders = ladders or {}
    coins = []
    asof = 0
    for sym, cs in universe.items():
        if len(cs) < 96 * 60:
            continue
        price = cs[-1].close
        asof = max(asof, cs[-1].close_time)
        reg = regime(cs)
        lv = level_map(cs)
        st = setups_now(cs)
        dv = derivatives_snapshot(masters.get(sym))
        liq = liquidation_events(cs, masters.get(sym))
        chg24 = price / cs[-97].close - 1
        d1 = resample(cs, 96)
        lv_d = []
        for L in build_levels(d1, left=3, right=3, max_age=400)[:10]:
            if L.status in ("ACTIVE", "RECLAIMED") and L.touch_count >= 2:
                lv_d.append({"kind": L.kind, "lo": L.lo, "hi": L.hi, "center": L.center, "touches": L.touch_count,
                             "status": L.status, "score": L.score, "dist_pct": round((L.center - price) / price * 100, 2)})
        coin = {"symbol": sym, "price": price, "chg24": chg24, "regime": reg, "levels": lv, "setups": st,
                "derivatives": dv, "liquidation_events": liq,
                "candles": [[c.open_time, c.open, c.high, c.low, c.close, c.volume] for c in cs[-chart_bars:]],
                "candles_1d": [[c.open_time, c.open, c.high, c.low, c.close, c.volume] for c in d1[-365:]],
                "levels_1d": sorted(lv_d, key=lambda x: abs(x["dist_pct"])),
                "chg7": price / cs[-96 * 7 - 1].close - 1, "chg30": price / cs[-96 * 30 - 1].close - 1,
                "footprint": footprint(ladders.get(sym), cs)}
        coin["brief"] = coin_brief(sym, price, reg, lv, st, dv)
        coin["profile"] = profile(sym)
        coins.append(coin)
    order = {"LONG": 0, "SHORT": 1, "UP_NO_BREAKOUT": 2, "DOWN_NO_BREAKOUT": 3}
    coins.sort(key=lambda c: (order.get(c["regime"]["mid"], 9), c["symbol"]))
    # ---- portfolio layer: BTC regime filter + cross-sectional momentum ranking (21d)
    btc = next((c for c in coins if c["symbol"].startswith("BTC")), None)
    btc_bull = bool(btc and btc["regime"]["long"] == "BULL")
    ranked = sorted(coins, key=lambda c: -c["regime"]["r28"])
    r21 = {}
    for c in coins:
        cs = universe[c["symbol"]]
        r21[c["symbol"]] = cs[-1].close / cs[-96 * 21 - 1].close - 1 if len(cs) > 96 * 21 + 1 else None
    ranked21 = sorted([c["symbol"] for c in coins if r21[c["symbol"]] is not None], key=lambda s: -r21[s])
    portfolio = {"btc_bull": btc_bull,
                 "trend_longs": [c["symbol"] for c in coins if c["regime"]["mid"] == "LONG" and btc_bull],
                 "trend_longs_blocked": [c["symbol"] for c in coins if c["regime"]["mid"] == "LONG" and not btc_bull],
                 "xs_top": [(s, r21[s]) for s in ranked21[:3]], "xs_bottom": [(s, r21[s]) for s in ranked21[-3:]],
                 "note": ("Правило тренда: дневное закрытие, 28 дней > 0 и максимум 20 дней, держать 5 дней, стоп 2 ATR; "
                          "только лонг и только когда BTC выше SMA200. Лидеры/аутсайдеры 21 дня — для недельной ротации "
                          "(лонг лидеры / шорт аутсайдеры, равными долями).")}
    for c in coins:
        c["r21"] = r21[c["symbol"]]
    events = []
    for c in coins:
        for e in c["liquidation_events"]:
            events.append({**e, "symbol": c["symbol"], "kind": "LIQUIDATION_CASCADE"})
        for s in c["setups"]:
            if s["status"] in ("CALL", "READY", "POSITION_OPEN", "WAIT_CONFIRMATION", "ATTENTION"):
                events.append({"time": s["time"], "symbol": c["symbol"], "kind": "SETUP", **{k: s[k] for k in ("family", "direction", "status", "entry", "stop")}})
        if c["derivatives"].get("available") and c["derivatives"].get("funding_rank90d") is not None:
            rk = c["derivatives"]["funding_rank90d"]
            if rk >= 0.95 or rk <= 0.05:
                events.append({"time": asof, "symbol": c["symbol"], "kind": "FUNDING_EXTREME", "rank": rk,
                               "funding_pct": c["derivatives"]["funding_pct"]})
    events.sort(key=lambda e: -e["time"])
    return {"generated": int(datetime.now(tz=timezone.utc).timestamp() * 1000), "asof": asof,
            "coins": coins, "events": events[:40], "brief": market_brief(coins, asof, portfolio), "portfolio": portfolio,
            "news": {"available": False, "note": "Новости и токеномика: нужен сервер с интернетом и ключ API языковой модели. Слот зарезервирован."}}


def write_report(rep: dict, path: str) -> None:
    def default(o):
        if isinstance(o, (np.floating, np.integer, np.bool_)):
            return o.item()
        if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
            return None
        raise TypeError(str(type(o)))
    txt = json.dumps(rep, default=default, ensure_ascii=False)
    txt = txt.replace("NaN", "null").replace("Infinity", "null")
    with open(path, "w") as f:
        f.write(txt)
