"""One small feed for the CRYPTO NOVA 2.0 app (19.10.2026, owner: «приложение — быстрая сводка, как Apple Watch; всё должно
работать по-настоящему»).

web/app_home.json (https://cryptonova.vip/app_home.json, on the server every minute) — everything the 2.0 home, futures and
spot tabs show, already in plain numbers so the app never recomputes the engine:
  total: earned since the demo start, split by source (futures = the average CNPTS $10 000 account — cons/bal/aggr trade the
         same signals at three sizes; spot = the CRYPTO NOVA portfolio per $10 000);
  curve: both sources over time (≤ 60 points); futures: result, today, trades, worst dip, open trades with stop/entry/now/target
         and a plain «why», last closed trades; spot: allocation groups, per-coin change since purchase, last action;
  market: bitcoin price and 24 h change, fear & greed, regime (BTC above its 200-day average = «рост»).
web/app_btc.json — bitcoin daily closes since 2021 for the free piggy bank («копилка»): the app replays the user's weekly or
monthly demo buys at real prices. Demo only, not advice. Stdlib only."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time

START = 10000.0
PROFILES = ("cons", "bal", "aggr")
GROUPS = (("Биткоин", {"BTC"}), ("Эфир", {"ETH"}), ("Солана", {"SOL"}))
KIND_RU = {"мем": "мем-монеты", "крупный альт": "крупные альты", "ядро": "ядро"}
WHY_RU = {
    "CLIMAX_LONG": "Рынок резко продали: свеча упала на большом объёме. После таких провалов цена чаще отскакивает за сутки.",
    "CLIMAX_SHORT": "Цену резко разогнали на большом объёме. После таких всплесков цена чаще откатывает за сутки.",
}
# 20.10 (owner: «нету активных сигналов в системе криптонова»): the app shows the trades of EVERY source of the CRYPTO NOVA mix
# (signal_feed.SOURCES), not only the main CNPTS desk — the own daily signals were open but invisible in the app.
GRADE_WHY_RU = {
    "P": "Монета в растущем тренде на день откатилась вниз. Покупаем откат: на истории 2023–2026 такие входы в среднем "
         "давали плюс.",
    "C": "Цена пробила максимум 20 дней. На истории после таких пробоев тренд чаще продолжается.",
    "LST": "Новая монета на бирже: после листинга цена чаще снижается. Шорт на 14 дней, небольшой размер.",
    "K": "Биткоин резко вырос, а эта монета ещё нет — на истории альты чаще догоняют рывок BTC.",
}
SRC_RU = {"bal": "основная стратегия", "imp": "импульс BTC", "own": "сигналы CRYPTO NOVA", "lst": "шорт листингов"}


def _load(out: str, name: str) -> dict:
    try:
        with open(os.path.join(out, name), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _r(x, n: int = 2):
    return None if x is None else round(float(x), n)


def _avg_curve(profiles: dict) -> list[tuple[int, float]]:
    """The average CNPTS account over time: the three profiles share timestamps (same engine cycle)."""
    hist = [(p, (profiles.get(p) or {}).get("equity_history") or []) for p in PROFILES]
    hist = [(p, h) for p, h in hist if h]
    if not hist:
        return []
    base = min(hist, key=lambda x: len(x[1]))[1]
    idx = [{int(e["time"]): float(e["equity"]) for e in h} for _, h in hist]
    out = []
    for e in base:
        t = int(e["time"])
        vals = [m[t] for m in idx if t in m]
        if len(vals) == len(idx):
            out.append((t, sum(vals) / len(vals) - START))
    return out


def _sample(points: list, n: int = 60) -> list:
    if len(points) <= n:
        return points
    step = (len(points) - 1) / (n - 1)
    return [points[round(i * step)] for i in range(n)]


def _mix_keys() -> list[str]:
    try:
        from .signal_feed import SOURCES
        return [k for k, _n, _w in SOURCES]
    except Exception:  # noqa: BLE001
        return ["bal"]


def _open_row(o: dict, src: str) -> dict:
    """one open trade of a mix desk in the app's words; the target: the desk's last take-profit or, for the rules that exit by
    rule / time, the same +1R/+2R/+3R ladder the Telegram message shows (its 3rd step)"""
    entry, price, stop = o.get("entry"), o.get("price"), o.get("stop_now") or o.get("stop")
    side = str(o.get("side") or "").upper()
    tps = o.get("tps") or []
    tgt = tps[-1] if tps else None
    grade = str(o.get("grade") or "")
    if (tgt is None or grade in ("P", "C", "LST")) and entry and stop:
        try:
            from .signal_feed import targets
            tgt = targets({"entry": entry, "stop": stop, "side": side, "grade": grade, "tps": tps})[-1]
        except Exception:  # noqa: BLE001
            pass
    pct = None
    if entry and price:
        pct = (price / entry - 1) * 100 * (1 if side == "LONG" else -1)
    why = WHY_RU.get(str(o.get("kind") or "")) or GRADE_WHY_RU.get(grade) or \
        "Сделка по правилам CRYPTO NOVA: вход, стоп и цель заданы заранее."
    if o.get("vol"):
        why += f" Объём дня сигнала — ×{float(o['vol']):.1f} к обычному."
    try:
        from .signal_feed import RULE_RU
        rule = RULE_RU.get(grade) or None
    except Exception:  # noqa: BLE001
        rule = None
    return {"sym": o.get("symbol"), "side": side, "entry": entry, "price": price, "stop": stop, "target": tgt,
            "pnl_usd": _r(float(o.get("open_pnl_usd") or 0)), "pct": _r(pct, 1), "opened": o.get("opened"),
            "expires": o.get("expires"), "why": why, "src": src, "src_ru": rule or SRC_RU.get(src, "CRYPTO NOVA")}


def _status(bal_open: int, closed: list, opened: list) -> str | None:
    """what the main strategy is doing, in one calm line (shown when it has nothing open)"""
    if bal_open:
        return None
    last = closed[0] if closed else None
    tail = ""
    if last and last.get("pnl_usd") is not None:
        d = dt.datetime.fromtimestamp((last.get("closed") or 0) / 1000, dt.timezone(dt.timedelta(hours=3)))
        tail = f" Последняя сделка: {last['sym']}, {'+' if last['pnl_usd'] >= 0 else '−'}${abs(last['pnl_usd']):.0f}, {d:%d.%m}."
    other = len(opened)
    word = "сделка" if other % 10 == 1 and other % 100 != 11 else \
        "сделки" if 2 <= other % 10 <= 4 and not 12 <= other % 100 <= 14 else "сделок"
    return ("Основная стратегия сейчас без сделок: она входит только после резкой распродажи на большом объёме — так дешевле "
            "и на истории выгоднее." + tail + (f" В работе {other} {word} других сигналов CRYPTO NOVA." if other else ""))


def _futures(paper: dict, now: int) -> dict:
    prof = paper.get("profiles") or {}
    curve = _avg_curve(prof)
    eq = [(prof.get(p) or {}) for p in PROFILES if prof.get(p)]
    usd = sum(float(p.get("equity", START)) + float(p.get("open_pnl_usd") or 0) - START for p in eq) / max(1, len(eq))
    day0 = int(dt.datetime.fromtimestamp(now / 1000, dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
               .timestamp() * 1000)
    before = [v for t, v in curve if t < day0]
    today = usd - (before[-1] if before else 0.0)
    peak, dd = START, 0.0
    for _, v in curve:
        peak = max(peak, START + v)
        dd = min(dd, (START + v) / peak - 1)
    bal = prof.get("bal") or paper
    opened = []
    for src in _mix_keys():
        desk = bal if src == "bal" else prof.get(src) or {}
        for o in desk.get("open") or []:
            opened.append(_open_row(o, src))
    opened.sort(key=lambda o: -(o.get("opened") or 0))
    bal_open = sum(1 for o in opened if o["src"] == "bal")
    closed = []
    for c in sorted(bal.get("closed") or [], key=lambda c: c.get("closed") or 0, reverse=True)[:8]:
        closed.append({"sym": c.get("symbol"), "side": str(c.get("side") or "").upper(), "pnl_usd": _r(float(c.get("pnl_usd") or 0)),
                       "closed": c.get("closed")})
    st = bal.get("stats") or {}
    n_closed = int(st.get("trades") or len(bal.get("closed") or []))
    return {"usd": _r(usd), "pct": _r(usd / START * 100), "today_usd": _r(today), "trades": n_closed + bal_open,
            "status_ru": _status(bal_open, closed, opened),
            "wins": round(float(st.get("winrate") or 0) * n_closed), "closed_n": n_closed, "worst_dd_pct": _r(dd * 100, 1),
            "open": opened, "closed": closed, "curve": [[t, _r(v)] for t, v in _sample(curve)],
            "about_ru": "Средний счёт $10 000: те же сделки CRYPTO NOVA в 3 размерах (осторожный, сбалансированный, смелый)."}


def _spot(pf: dict) -> dict:
    hist = pf.get("history") or []
    idx = pf.get("index")
    usd = None if idx is None else (float(idx) - 100.0) * START / 100.0
    pos = pf.get("positions") or []
    groups = []
    used = set()
    for name, syms in GROUPS:
        w = sum(float(p.get("weight") or 0) for p in pos if p.get("symbol") in syms)
        used |= syms
        groups.append({"name": name, "weight": _r(w * 100, 1)})
    rest: dict[str, float] = {}
    for p in pos:
        if p.get("symbol") in used:
            continue
        g = KIND_RU.get(str(p.get("kind_ru") or ""), "другие")
        rest[g] = rest.get(g, 0.0) + float(p.get("weight") or 0)
    for g, w in sorted(rest.items(), key=lambda x: -x[1]):
        groups.append({"name": g, "weight": _r(w * 100, 1)})
    groups.append({"name": "доллары", "weight": _r(float(pf.get("cash_pct") or 0), 1)})
    movers = []
    for p in pos:
        if p.get("entry") and p.get("price"):
            movers.append({"sym": p["symbol"], "pct": _r((p["price"] / p["entry"] - 1) * 100, 1)})
    movers.sort(key=lambda m: -m["pct"])
    acts = pf.get("recent_actions") or []
    last = max(acts, key=lambda a: a.get("time") or 0) if acts else None
    return {"usd": _r(usd), "pct": _r(None if idx is None else float(idx) - 100.0), "index": idx, "n_coins": len(pos),
            "cash_pct": pf.get("cash_pct"), "alloc": groups, "movers": movers,
            "curve": [[h["t"], _r((float(h["v"]) - 100.0) * START / 100.0)] for h in _sample(hist) if h.get("v") is not None],
            "last_action": None if not last else {"t": last.get("time"), "text": last.get("why_ru"), "sym": last.get("symbol"),
                                                  "action": last.get("action")},
            "started": pf.get("started")}


def _market(mk: dict) -> dict:
    top = {str(r.get("symbol") or "").upper(): r for r in mk.get("top") or []}
    btc = top.get("BTC") or {}
    fng = mk.get("fng") or {}
    return {"btc": {"price": btc.get("price"), "chg24": _r(btc.get("chg24"), 2)},
            "eth": {"price": (top.get("ETH") or {}).get("price"), "chg24": _r((top.get("ETH") or {}).get("chg24"), 2)},
            "fng": {"value": fng.get("value"), "label": fng.get("label")},
            "regime": None if mk.get("btc_bull") is None else ("рост" if mk.get("btc_bull") else "спад")}


def _engine_facts(out: str) -> dict:
    """Real numbers for the welcome screen (never invented): the nightly lab and the signals checked by price."""
    al, sig, ac = _load(out, "autolab.json"), _load(out, "app_signals.json"), _load(out, "academy.json")
    return {"lab_tried": al.get("tried"), "lab_tested": al.get("tested"), "lab_passed": al.get("passed"),
            "lab_reliable": al.get("reliable"), "lab_markets": len(al.get("coverage") or {}) or None,
            "signals_checked": (sig.get("counts") or {}).get("resolved"),
            "lessons": sum(len(lv.get("lessons") or []) for lv in ac.get("levels") or []) or None,
            "cycle_sec": 60}


def btc_daily(conn: sqlite3.Connection | None, out: str) -> dict:
    from . import outlook
    closes = outlook.merge(outlook.seed_closes(), outlook.local_closes(), outlook.db_closes(conn),
                           outlook.terminal_closes(_load(out, "terminal.json") or None)).get("BTC") or {}
    start = (dt.date(2021, 1, 1) - dt.date(1970, 1, 1)).days
    days = sorted(d for d in closes if d >= start)
    if not days:
        return {}
    first, last = days[0], days[-1]
    return {"start_day": first, "closes": [_r(closes.get(d), 2) for d in range(first, last + 1)],
            "about_ru": "Дневные закрытия биткоина (UTC). day = номер дня от 1970-01-01; closes[i] — закрытие дня start_day + i."}


def run(conn: sqlite3.Connection | None = None, out: str = "web", now: int | None = None) -> dict:
    now = now or int(time.time() * 1000)
    paper, pf, mk = _load(out, "paper.json"), _load(out, "app_portfolio.json"), _load(out, "markets.json")
    fut, spot = _futures(paper, now), _spot(pf)
    total = round((fut["usd"] or 0) + (spot["usd"] or 0), 2)
    doc = {"v": 1, "asof": now, "mode": "demo", "since": paper.get("since") or "2026-10-17",
           "total": {"usd": total, "futures_usd": fut["usd"], "spot_usd": spot["usd"]},
           "sources": [{"key": "futures", "usd": fut["usd"]}, {"key": "spot", "usd": spot["usd"]},
                       {"key": "piggy", "usd": None}, {"key": "soon", "label": "арбитраж, рынки прогнозов"}],
           "market": _market(mk), "futures": fut, "spot": spot, "engine": _engine_facts(out),
           "note_ru": "Демо-счета CRYPTO NOVA. Прошлые результаты не гарантируют будущих. Не финансовый совет."}
    with open(os.path.join(out, "app_home.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    if int(now / 60000) % 60 == 0 or not os.path.exists(os.path.join(out, "app_btc.json")):
        b = btc_daily(conn, out)
        if b:
            with open(os.path.join(out, "app_btc.json"), "w", encoding="utf-8") as f:
                json.dump(b, f, ensure_ascii=False, separators=(",", ":"))
    return doc
