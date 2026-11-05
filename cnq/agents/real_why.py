"""«Почему эта сделка» for the real CRYPTO NOVA futures (04.11.2026, owner: «по каким правилам?», «по какой причине его шортишь?»).

web/real_why.json — a SIBLING of web/public_real.json (the autopilot writes that one with a strict whitelist; this file is built in the
site workflow from public files only, no keys):
  {"asof", "by_sig": {"<public_real futures[].sig>": why}, "closed": [{"inst", "opened", "closed", "sig", "why"}], "about_ru"}
The app joins open positions by futures[].sig and closed ones by inst + opened (± 10 min) — or reads closed[].why directly.
why = {
  "desk": "whales" | "aggr" | …, "desk_ru",
  "whale": {"name", "source": "okx_lead" | "hyperliquid", "source_ru", "addr", "entry", "lev", "notional_usd",
            "record_30d": {"pnl_usd", "trades", "win", "pf", "hold_med_h", "style_ru"} | null},
  "copy": {"entry", "stop", "stop_src_ru", "lev_cap": 5, "lev": <whale lev capped>, "exit_ru": "выход вместе с китом"},
  "target": {"ref_px": 2R from our entry, "r_pct", "ru": "ориентир 2R … — выход вместе с китом"},
  "market": {"btc_24h_pct", "funding_8h_pct", "support", "resistance", "oi_4h_pct": null, "src_ru", "at"},
  "text_ru": two short lines in plain Russian, "owner_ru": an owner override that applies, if any }
Only data we actually have: anything unknown is null. Market context is taken from Hyperliquid at the copy's entry time (hourly
candles before the entry → the levels engine; BTC 24 h; the funding rate of the hour); computed once per trade and kept. OI 4 h
change at the entry is not recorded publicly → null. Stdlib + the levels engine (numpy)."""
from __future__ import annotations

import json
import os
import time

from ..execution import owner_overrides as ov

H1 = 3_600_000
DAY = 86_400_000
OUT = "web/real_why.json"
DESK_RU = {"whales": "Киты · копирование топ-5", "aggr": "Агрессивная", "bal": "Сбалансированная", "cons": "Консервативная",
           "imp": "Импульс BTC", "own": "Сигналы CRYPTO NOVA"}


def _f(x, d=None):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def _num_ru(v, n=2) -> str:
    if v is None:
        return "—"
    s = f"{v:,.{n}f}".replace(",", " ").replace(".", ",")
    return s


def _px_ru(v) -> str:
    if v is None:
        return "—"
    a = abs(v)
    n = 2 if a >= 10 else 4 if a >= 0.1 else 8
    return _num_ru(v, n).rstrip("0").rstrip(",") if n > 2 else _num_ru(v, n)


def hl_coin(sym: str) -> str:
    """OKX base → Hyperliquid name (PEPE / 1000PEPE → kPEPE)"""
    b = ov.base(sym)
    return "k" + b if b in ("PEPE", "SHIB", "BONK", "FLOKI", "LUNC", "DOGS", "NEIRO", "SATS") else b


def market_at(coin: str, t: int, post) -> dict:
    """Hyperliquid at time t: BTC 24 h change, the coin's funding of that hour (per 8 h, %), the nearest support / resistance of the
    levels engine on hourly candles closed before t. Anything that fails → null."""
    out = {"btc_24h_pct": None, "funding_8h_pct": None, "support": None, "resistance": None, "oi_4h_pct": None, "at": t,
           "src_ru": "Hyperliquid на момент входа: свечи 1 ч (уровни), BTC за 24 ч, фандинг часа входа; изменение OI за 4 ч на момент "
                     "входа не записывалось — нет данных"}
    try:
        b = post({"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "1h", "startTime": t - DAY - 2 * H1, "endTime": t}}) or []
        b = [x for x in b if int(x["t"]) + H1 <= t]
        if len(b) >= 24:
            out["btc_24h_pct"] = round((_f(b[-1]["c"]) / _f(b[-25]["c"]) - 1) * 100, 2)
    except Exception:  # noqa: BLE001
        pass
    try:
        fh = post({"type": "fundingHistory", "coin": coin, "startTime": t - 2 * H1, "endTime": t}) or []
        if fh:
            out["funding_8h_pct"] = round(_f(fh[-1]["fundingRate"], 0.0) * 8 * 100, 4)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..bingx.rest import Candle
        from ..features.levels import build_levels
        cs = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h", "startTime": t - 30 * DAY, "endTime": t}}) or []
        cs = [x for x in cs if int(x["t"]) + H1 <= t]
        cand = [Candle(int(x["t"]), _f(x["o"]), _f(x["h"]), _f(x["l"]), _f(x["c"]), _f(x["v"], 0.0), int(x["t"]) + H1 - 1,
                       _f(x["v"], 0.0) * _f(x["c"]), int(_f(x.get("n"), 0)), 0.0, 0.0) for x in cs]
        if len(cand) > 50:
            px = cand[-1].close
            lv = [L for L in build_levels(cand) if L.status in ("ACTIVE", "RECLAIMED")]
            sup = [L.center for L in lv if L.center < px]
            res = [L.center for L in lv if L.center > px]
            out["support"] = max(sup) if sup else None
            out["resistance"] = min(res) if res else None
    except Exception:  # noqa: BLE001
        pass
    return out


def _whale_rec(addr: str, wtop: dict) -> dict | None:
    for p in (wtop.get("top") or []) + (wtop.get("others") or []):
        if p.get("addr") == addr:
            return {"pnl_usd": p.get("pnl_30d"), "trades": p.get("trips"), "win": p.get("win"), "pf": p.get("pf"),
                    "hold_med_h": p.get("hold_med_h"), "style_ru": p.get("style_ru"), "rank": p.get("rank")}
    return None


def _copy_index(wtop: dict) -> dict:
    """paper id (W-<SYM>-<S>-<opened>) → the desk's copy row (open or closed) with the whale fields"""
    d = wtop.get("desk") or {}
    from .whale_study import okx_symbol
    out = {}
    for o in (d.get("open") or []) + (d.get("closed") or []):
        try:
            out[f"W-{okx_symbol(o['coin'])}-{o['side'][0]}-{int(_f(o.get('opened_t'), 0))}"] = o
        except Exception:  # noqa: BLE001
            continue
    return out


def why_for(sig: str, side: str, entry: float | None, stop: float | None, inst: str, wtop: dict, idx: dict, ova: dict,
            market: dict | None, opened: int | None) -> dict:
    desk = (sig or "").split(":")[0] if sig else None
    w = {"desk": desk, "desk_ru": DESK_RU.get(desk or "", desk), "whale": None, "copy": None, "target": None, "market": market,
         "text_ru": None, "owner_ru": None}
    long = str(side).lower() == "long"
    sym = ov.base(inst)
    r = abs(entry - stop) if entry and stop else None
    if r:
        tp = entry + 2 * r if long else entry - 2 * r
        w["target"] = {"ref_px": round(tp, 8), "r_pct": round(r / entry * 100, 2),
                       "ru": f"Ориентир {_px_ru(tp)} (2R: двойное расстояние до стопа) — но копия выходит вместе с китом"}
    if desk == "whales":
        o = idx.get(sig.split(":", 1)[1]) or {}
        addr = o.get("whale") or ""
        okx = addr.startswith("okx:")
        name = o.get("whale_ru") or addr[:10]
        rec = _whale_rec(addr, wtop) if addr else None
        we = _f(o.get("whale_entry"))
        if we and str(o.get("coin") or "")[:1] == "k" and not str(inst).upper().startswith("1000"):
            we = we / 1000.0            # Hyperliquid kPEPE is priced per 1000 coins, the OKX swap per coin
        bn = addr.startswith("bn:")             # 04.11: Binance lead portfolios (cnq.agents.whale_sources)
        w["whale"] = {"name": name, "source": "okx_lead" if okx else "binance_lead" if bn else "hyperliquid",
                      "source_ru": "лид-трейдер копитрейдинга OKX" if okx else "лид-трейдер копитрейдинга Binance" if bn
                      else "трейдер Hyperliquid (сделки видны в блокчейне)",
                      "addr": addr or None, "entry": we, "lev": _f(o.get("whale_lev")),
                      "notional_usd": _f(o.get("whale_notional")), "record_30d": rec}
        w["copy"] = {"entry": entry, "stop": stop, "stop_src_ru": o.get("stop_src") or "защитный стоп системы", "lev_cap": 5,
                     "lev": min(5.0, _f(o.get("whale_lev"), 1.0) or 1.0), "exit_ru": "выход вместе с китом (или по стопу)"}
        side_ru = "лонг" if long else "шорт"
        rec_ru = (f"за 30 дней {'+' if (rec.get('pnl_usd') or 0) >= 0 else ''}${_num_ru(rec.get('pnl_usd'), 0)}, {rec.get('trades')} сделок, "
                  f"{round((rec.get('win') or 0) * 100)}% в плюс") if rec else "результат за 30 дней не найден"
        wl = f"{_num_ru(w['whale']['lev'], 0)}×" if w["whale"]["lev"] else "плечо не видно"
        w["text_ru"] = (f"{side_ru.capitalize()} {sym} — копия кита {name} ({w['whale']['source_ru']}): у него {side_ru} от "
                        f"{_px_ru(w['whale']['entry'])}, {wl}, позиция ${_num_ru(w['whale']['notional_usd'], 0)}; {rec_ru}.\n"
                        f"Наша копия: вход {_px_ru(entry)}, стоп {_px_ru(stop)} ({w['copy']['stop_src_ru']}), плечо ≤ 5×; выходим вместе с китом.")
    else:
        w["text_ru"] = (f"{'Лонг' if long else 'Шорт'} {sym} — сделка стола «{w['desk_ru']}».\n"
                        f"Вход {_px_ru(entry)}, стоп {_px_ru(stop)}; выход по сигналу стола или по стопу.")
    bl = ov.blocked(ova, sym, "LONG" if long else "SHORT", opened)
    if bl:
        w["owner_ru"] = bl
    return w


def build(pub: dict, wtop: dict, prev: dict | None, post, now: int | None = None, ova: dict | None = None) -> dict:
    now = int(now or time.time() * 1000)
    ova = ova if ova is not None else ov.active(ov.load(), now)
    prev = prev or {}
    keep = dict(prev.get("by_sig") or {})
    idx = _copy_index(wtop or {})
    by_sig = {}
    acts = {a.get("sig"): a for a in (pub.get("actions") or []) if a.get("kind") == "fut_open" and a.get("sig")}
    for f in pub.get("futures") or []:
        sig = f.get("sig")
        if not sig:
            continue
        old = keep.get(sig) or {}
        mk = old.get("market")
        if mk is None:
            mk = market_at(hl_coin(f.get("inst")), int(f.get("opened") or now), post)
        by_sig[sig] = why_for(sig, f.get("side"), _f(f.get("entry")), _f(f.get("stop")), f.get("inst"), wtop or {}, idx, ova, mk,
                              f.get("opened"))
    # closed: OKX's own history rows; their sig comes from the matching fut_open action (inst + time)
    closed = []
    hist = {**keep, **by_sig}
    for c in (pub.get("closed") or [])[:30]:
        o = int(c.get("opened") or 0)
        sig = None
        for s, a in acts.items():
            if ov.base(a.get("sym")) == ov.base(c.get("inst")) and abs(int(a.get("t") or 0) - o) <= 10 * 60_000:
                sig = s
                break
        w = hist.get(sig) if sig else None
        if w is None and sig:
            w = why_for(sig, c.get("side"), _f(c.get("open_px")), None, c.get("inst"), wtop or {}, idx, ova, None, o)
        closed.append({"inst": c.get("inst"), "opened": c.get("opened"), "closed": c.get("closed"), "sig": sig, "why": w})
    # remember every open why (closed ones look it up by sig later)
    mem = {**{k: v for k, v in keep.items() if k not in by_sig}, **by_sig}
    mem = dict(list(mem.items())[-200:])
    return {"asof": now, "by_sig": by_sig, "closed": closed, "memory": mem,
            "about_ru": "Почему открыта каждая реальная сделка фьючерсов: какой стол или кит её дал, его вход, плечо и размер, его итог "
                        "за 30 дней, наш стоп и плечо, рынок на момент входа (Hyperliquid). Цель у копий — выход вместе с китом; "
                        "ориентир 2R показан только для понимания."}


def run(web: str = "web", post=None) -> dict:
    from .whale_study import _post
    post = post or _post

    def rd(p):
        try:
            with open(os.path.join(web, p), encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}
    pub, wtop, prev = rd("public_real.json"), rd("whales_top.json"), rd("real_why.json")
    if prev.get("memory"):
        prev = {**prev, "by_sig": prev["memory"]}
    return build(pub, wtop, prev, post)
