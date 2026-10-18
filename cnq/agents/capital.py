"""CRYPTO NOVA Портфолио — one sum, the system splits it between our desks (18.10.2026, owner: «человек пополняет $1000, система сама
распределяет: спот, фьючерсы, фандинг… весь месяц работает, в конце подводит итоги: заработано столько-то, оплата 10%»).

A DEMO account of $10 000 (owner: «как будто я вложил настоящие свои 10 тысяч долларов» — his main screen) that starts on the first run and follows the desks the server already runs:
  • spot     — the CRYPTO NOVA portfolio (portfolio.json → portfolios.ideas, equity_usd);
  • futures  — CRYPTO NOVA = the desks in the mix: CNPTS «Сбалансированная» (paper.json profiles.bal) and «Импульс BTC»
               (profiles.imp), equal parts, equity incl. open P&L;
  • funding  — the funding-carry desk (funding.json equity_mtm);
  • yield    — stablecoin lending on Aave v3 at DefiLlama's current supply APY (yield_desk.py → yield.json).
Arbitrage and the triangle are NOT given money: their numbers are virtual estimates from windows the server saw, and a client
cannot run them (money on several exchanges, robots win those windows in milliseconds). They are shown as «наблюдение».

Each run every sleeve moves by its desk's return since the previous run. Weights are reset once a week (Monday, Kyiv):
base spot 40 % · futures 25 % · funding 15 % · lending 20 %; a sleeve whose desk lost more than 5 % over the last 30 days gets half its base
weight, one that gained more than 5 % gets ×1.25, then everything is scaled back to 100 % within the bounds
(spot 25–55 %, futures 10–40 %, funding 0–25 %, lending 10–40 %). Month summary: start of the calendar month (Kyiv) → now, and 10 % of a
positive result as the fee («к оплате»). A demo, not a promise: the result can be a minus. Stdlib only."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time

START = 10_000.0
# 18.10 (owner): + a conservative lending sleeve («кредитование», yield_desk.py) → spot 40 · futures 25 · funding 15 · lending 20
BASE = {"spot": 0.40, "futures": 0.25, "funding": 0.15, "yield": 0.20}
BOUNDS = {"spot": (0.25, 0.55), "futures": (0.10, 0.40), "funding": (0.0, 0.25), "yield": (0.10, 0.40)}
FEE = 0.10
# 18.10 (owner): a user picks one of three models (and a couple of options) — the system builds HIS mix from the same sleeves.
MODELS = {
    "calm": {"name_ru": "Спокойная", "about_ru": "Больше кредитования стейблкоинов и ядра BTC/ETH/SOL, без фьючерсов.",
             "w": {"spot": 0.35, "futures": 0.0, "funding": 0.15, "yield": 0.50}},
    "balanced": {"name_ru": "Сбалансированная", "about_ru": "Наша основная модель: понемногу везде, доли подстраиваются раз в неделю.",
                 "w": dict(BASE)},
    "bold": {"name_ru": "Смелая", "about_ru": "Больше спота и фьючерсов, меньше кредитования — выше и доход, и риск.",
             "w": {"spot": 0.45, "futures": 0.35, "funding": 0.10, "yield": 0.10}},
}
OPTIONS = {   # toggles that shift a model (applied in order, then normalised)
    "risky_coins": {"name_ru": "Добавить рискованные монеты", "about_ru": "Больше спота с мемами и высоким риском: +10% к споту за счёт кредитования.",
                    "shift": {"spot": +0.10, "yield": -0.10}},
    "more_defi": {"name_ru": "Больше DeFi-кредитования", "about_ru": "+10% в кредитование стейблкоинов (малый риск) за счёт фьючерсов и спота.",
                  "shift": {"yield": +0.10, "futures": -0.05, "spot": -0.05}},
    "no_futures": {"name_ru": "Без фьючерсов", "about_ru": "Фьючерсная часть уходит в спот и кредитование поровну.",
                   "zero": "futures"},
}
NAMES = {"spot": "Спот · CRYPTO NOVA портфель", "futures": "Фьючерсы · CRYPTO NOVA", "funding": "Фандинг (без направления)",
         "yield": "Кредитование стейблкоинов (Aave)"}
KYIV = dt.timezone(dt.timedelta(hours=3))


def _load(out: str, name: str) -> dict:
    try:
        return json.load(open(os.path.join(out, name), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def desk_values(out: str) -> dict:
    """Current equity of each desk (any unit — only the ratio between runs matters). Missing → not in the dict."""
    v = {}
    pf = (_load(out, "portfolio.json").get("portfolios") or {}).get("ideas") or {}
    if pf.get("equity_usd") is not None:
        v["spot"] = float(pf["equity_usd"])
    prof = _load(out, "paper.json").get("profiles") or {}
    fut = [float(d["equity"]) + sum(float(o.get("open_pnl_usd") or 0) for o in d.get("open") or [])
           for k in ("bal", "imp") if (d := prof.get(k)) and d.get("equity") is not None]
    if fut:
        v["futures"] = sum(fut) / len(fut)
    fd = _load(out, "funding.json")
    if fd.get("start_equity"):
        v["funding"] = float(fd["equity_mtm"] if fd.get("equity_mtm") is not None else fd.get("equity", fd["start_equity"]))
    yd = _load(out, "yield.json")
    if yd.get("equity") is not None:
        v["yield"] = float(yd["equity"])
    return v


def desk_30d(out: str, now: int) -> dict:
    """Return of each desk over the last 30 days (from the desks' own histories), for the weekly tilt."""
    since = now - 30 * 86_400_000
    r = {}
    pf = (_load(out, "portfolio.json").get("portfolios") or {}).get("ideas") or {}
    h = [x for x in pf.get("history") or [] if x.get("time", 0) >= since and x.get("v")]
    if h:
        r["spot"] = h[-1]["v"] / h[0]["v"] - 1
    prof = _load(out, "paper.json").get("profiles") or {}
    xs = []
    for k in ("bal", "imp"):
        hh = [x for x in (prof.get(k) or {}).get("equity_history") or [] if x.get("time", 0) >= since and x.get("equity")]
        if hh:
            xs.append(hh[-1]["equity"] / hh[0]["equity"] - 1)
    if xs:
        r["futures"] = sum(xs) / len(xs)
    fh = [x for x in _load(out, "funding.json").get("history") or [] if x[0] >= since and x[1]]
    if fh:
        r["funding"] = fh[-1][1] / fh[0][1] - 1
    yh = [x for x in _load(out, "yield.json").get("history") or [] if x[0] >= since and x[1]]
    if yh:
        r["yield"] = yh[-1][1] / yh[0][1] - 1
    return r


def target_weights(r30: dict, available: set) -> dict:
    w = {}
    for k, b in BASE.items():
        if k not in available:
            continue
        x = r30.get(k)
        w[k] = b * (0.5 if x is not None and x < -0.05 else 1.25 if x is not None and x > 0.05 else 1.0)
    s = sum(w.values()) or 1.0
    w = {k: v / s for k, v in w.items()}
    fixed: dict = {}
    for _ in range(len(w) + 1):                          # scale to 100 % inside the bounds: clip, give the rest to the free sleeves
        free = {k: v for k, v in w.items() if k not in fixed}
        room = 1.0 - sum(fixed.values())
        fs = sum(free.values()) or 1.0
        w = {**fixed, **{k: v / fs * room for k, v in free.items()}}
        out = {k: v for k, v in w.items() if k not in fixed and not (BOUNDS[k][0] - 1e-9 <= v <= BOUNDS[k][1] + 1e-9)}
        if not out:
            break
        for k, v in out.items():
            fixed[k] = min(max(v, BOUNDS[k][0]), BOUNDS[k][1])
    return {k: round(v, 4) for k, v in w.items()}


def _month_start(now: int) -> int:
    d = dt.datetime.fromtimestamp(now / 1000, KYIV)
    return int(dt.datetime(d.year, d.month, 1, tzinfo=KYIV).timestamp() * 1000)


def _monday(now: int) -> str:
    d = dt.datetime.fromtimestamp(now / 1000, KYIV)
    return (d - dt.timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def step(conn: sqlite3.Connection, out: str = "web", now: int | None = None) -> dict:
    now = now or int(time.time() * 1000)
    conn.execute("CREATE TABLE IF NOT EXISTS capital_state(k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS capital_hist(time INTEGER PRIMARY KEY, value REAL)")
    st = {k: json.loads(v) for k, v in conn.execute("SELECT k, v FROM capital_state")}
    desks = desk_values(out)
    events = []
    if not st.get("sleeves"):
        w = target_weights({}, set(desks))
        st = {"started": now, "sleeves": {k: START * x for k, x in w.items()}, "weights": w, "last": desks, "week": _monday(now),
              "month_start": _month_start(now), "month_value": START}
        events.append(f"Старт: ${START:,.0f} разделены — " + ", ".join(f"{NAMES[k]} {x*100:.0f}%" for k, x in w.items()))
    else:
        for k, val in list(st["sleeves"].items()):          # each sleeve follows its desk since the previous run
            a, b = st["last"].get(k), desks.get(k)
            if a and b:
                st["sleeves"][k] = val * b / a
        st["last"] = {**st["last"], **desks}
        if _month_start(now) != st["month_start"]:          # a new calendar month: the previous one is closed
            total = sum(st["sleeves"].values())
            prof = total - st["month_value"]
            st.setdefault("months", []).append({"month_start": st["month_start"], "start": round(st["month_value"], 2), "end": round(total, 2),
                                                "profit": round(prof, 2), "fee": round(max(0.0, prof) * FEE, 2)})
            st["month_start"], st["month_value"] = _month_start(now), total
        if _monday(now) != st["week"]:                      # weekly re-split by the rule
            total = sum(st["sleeves"].values())
            w = target_weights(desk_30d(out, now), set(desks) | set(st["sleeves"]))
            st["sleeves"] = {k: total * x for k, x in w.items()}
            st["weights"], st["week"] = w, _monday(now)
            events.append("Недельное перераспределение: " + ", ".join(f"{NAMES[k]} {x*100:.0f}%" for k, x in w.items()))
    total = sum(st["sleeves"].values())
    conn.execute("INSERT OR REPLACE INTO capital_hist VALUES(?, ?)", (now, round(total, 2)))
    # each desk as an index (100 at the portfolio's start) — the panel builds any user model from these
    st.setdefault("base_desks", {**desks})
    for k, v in desks.items():
        st["base_desks"].setdefault(k, v)
    st["idx"] = (st.get("idx") or [])[-399:] + [[now] + [round(desks[k] / st["base_desks"][k] * 100, 4) if k in desks and st["base_desks"].get(k) else None
                                                          for k in ("spot", "futures", "funding", "yield")]]
    for k, v in st.items():
        conn.execute("INSERT OR REPLACE INTO capital_state VALUES(?, ?)", (k, json.dumps(v)))
    conn.commit()
    hist = [[t, v] for t, v in conn.execute("SELECT time, value FROM capital_hist ORDER BY time")][-400:]
    month_profit = total - st["month_value"]
    arb = (_load(out, "arb.json").get("week") or {}).get("virtual_pnl_usd")
    tri = (_load(out, "tri.json").get("week") or {}).get("virtual_pnl_usd")
    return {"asof": now, "name": "CRYPTO NOVA Портфолио", "start_usd": START, "started": st["started"], "value_usd": round(total, 2),
            "pnl_usd": round(total - START, 2), "pnl_pct": round((total / START - 1) * 100, 2),
            "sleeves": [{"key": k, "name_ru": NAMES[k], "weight": st["weights"].get(k), "value_usd": round(v, 2),
                         "share_now": round(v / total, 4) if total else None} for k, v in st["sleeves"].items()],
            "month": {"start": st["month_start"], "start_value": round(st["month_value"], 2), "profit_usd": round(month_profit, 2),
                      "fee_usd": round(max(0.0, month_profit) * FEE, 2), "fee_pct": FEE * 100},
            "months": st.get("months", []), "history": hist, "events": events,
            "index_keys": ["spot", "futures", "funding", "yield"], "index_history": st.get("idx", []),
            "models": {k: {**m, "w": {kk: round(vv, 4) for kk, vv in m["w"].items()}} for k, m in MODELS.items()}, "options": OPTIONS,
            "watch_only": [{"key": "arb", "name_ru": "Арбитраж между биржами", "week_virtual_usd": arb},
                           {"key": "tri", "name_ru": "Треугольный арбитраж", "week_virtual_usd": tri}],
            "rules_ru": [
                f"Демо-счёт ${START:,.0f}: система сама делит сумму между нашими направлениями и ведёт её месяц.",
                "Старт: спот 40%, фьючерсы 25%, фандинг 15%, кредитование стейблкоинов 20%. Раз в неделю (понедельник) доли "
                "пересчитываются: направление, которое за 30 дней потеряло больше 5%, получает половину своей доли, заработавшее "
                "больше 5% — в 1.25 раза больше; границы: спот 25–55%, фьючерсы 10–40%, фандинг 0–25%, кредитование 10–40%.",
                "Кредитование — консервативная часть: USDC и USDT отданы в кредит на Aave под плавающий процент (сейчас — в файле "
                "yield.json); без займов, поэтому нет ликвидаций, но остаются риски смарт-контракта и отвязки стейблкоина.",
                "Арбитраж и треугольник денег не получают: их цифры — виртуальная оценка, клиент не может их исполнить.",
                "В конце месяца — итог; 10% от прибыли — оплата (месяц без прибыли бесплатно).",
                "Это демо, а не обещание: результат может быть и минусом."]}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None) -> dict:
    doc = step(conn, out, now)
    with open(os.path.join(out, "capital.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc
