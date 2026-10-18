"""CRYPTO NOVA Портфолио — one sum, the system splits it between our desks (18.10.2026, owner: «человек пополняет $1000, система сама
распределяет: спот, фьючерсы, фандинг… весь месяц работает, в конце подводит итоги: заработано столько-то, оплата 10%»; later the
same day: «участвовать в разных источниках, в разных секторах — он сам распределит, поймёт, адаптирует; нам нужен результат по профиту»).

A DEMO account of $10 000 (owner: «как будто я вложил настоящие свои 10 тысяч долларов» — his main screen). Six sleeves, each follows
a desk the server already runs:
  • spot     — the CRYPTO NOVA portfolio (portfolio.json → portfolios.ideas, equity_usd)                        [directional]
  • futures  — CRYPTO NOVA signals: «Сбалансированная» (paper.json profiles.bal) + «Импульс BTC» (profiles.imp)  [directional]
  • funding  — the funding-carry desk (funding.json equity_mtm)                                                  [no direction]
  • basis    — fixed-rate carry: spot + short dated futures on Deribit, rate locked at entry (basis.json)         [no direction]
  • yield    — stablecoin lending: Aave v3 USDC/USDT + Sky savings rate (yield.json)                             [no direction]
  • susde    — yield-bearing dollars, Ethena sUSDe (yield.json accounts.susde)                                    [no direction]
Arbitrage and the triangle are NOT given money: virtual estimates a client cannot run — shown as «наблюдение».

THE ALLOCATOR (v2). It decides two things and explains both in Russian («почему так»):
  1. How much goes to the directional part (spot + futures) — by the BTC regime: «рост» (BTC above its 200-day average and up over
     90 days — the same flag as the rest of the system) 60 %, otherwise 20 % (profile «Сбалансированная»; «Спокойная» 30/10 without
     futures, «Смелая» 80/35). Tested 18.10 on 2023-06 → 2026-10 daily data (BTC/ETH/SOL basket + stables at 4 %): fixed 40 % spot
     +100 % (max drawdown −31 %), 60/35 by regime +162 % (−30 %), 60/20 +168 % (−23 %) and better in every year incl. 2025 (+11 %)
     and 2026 (+3 %); the lower the share outside «рост», the better 2025–26 and the drawdown, for every share inside it. A short-
     term «broad sell-off» flag and «buy the dip with the stables» were NOT used: the dip rule won 2023–24 and lost 2025–26 (most
     of 96 variants). Spot:futures 2:1, and a
     sleeve whose desk lost more than 5 % over 30 days gets half, one that gained more than 5 % ×1.25 (the difference goes to /
     comes from the carry part).
  2. How the carry part (no bet on the price) is split — by what each source PAYS NOW minus a discount for its risk: lending and
     the fixed-rate futures carry are risk 2 (no discount), funding and sUSDe risk 3 (−1.5 points). Weights ∝ that score; lending
     keeps at least 30 % of the carry part (the liquid reserve); caps: funding 20 %, fixed rate 30 %, sUSDe 15 % of the whole sum.
  When: every Monday; at once when a new source appears; at once when the regime flips (not more often than once a day).
  Each re-split costs 0.1 % of the money moved (fees). A benchmark «без перераспределения» — the first split held as is — is
  published next to it: the allocator has to beat it to be worth anything.
Month summary: start of the calendar month (Kyiv) → now, and 10 % of a positive result as the fee («к оплате»).
A demo, not a promise: the result can be a minus. Stdlib only."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time

START = 10_000.0
VERSION = 2               # 18.10 v2: six sleeves and the allocator (v1: four sleeves, fixed base ± 30-day tilt) — restarts the demo once
KEYS = ["spot", "futures", "funding", "basis", "yield", "susde"]
DIRECTIONAL = ("spot", "futures")
CARRY = ("funding", "basis", "yield", "susde")
# 18.10 backtest (scripts/lab/portfolio_rules.py): regime shares 0.5–0.7 × 0.10–0.35 — lower «off» better in 2025–26 every time
RISK = {"spot": 4, "futures": 4, "funding": 3, "basis": 2, "yield": 2, "susde": 3}
DISCOUNT = {2: 0.0, 3: 1.5}                      # percentage points taken off a source's rate for its risk
CAP = {"funding": 0.20, "basis": 0.30, "susde": 0.15}
YIELD_FLOOR = 0.30                               # share of the carry part that always stays in lending (liquid reserve)
DEFAULT_RATE = 2.0                               # a source whose rate is unknown yet
FEE = 0.10
COST = 0.001                                     # a re-split costs 0.1 % of the money moved
REGIME_GAP_MS = 24 * 3_600_000
# 18.10 (owner): a user picks one of three models (and a couple of options) — the same allocator with a different appetite for risk.
PROFILES = {"calm": {"on": 0.30, "off": 0.10, "fut": 0.0}, "balanced": {"on": 0.60, "off": 0.20, "fut": 1 / 3},
            "bold": {"on": 0.80, "off": 0.35, "fut": 0.40}}
MODELS = {
    "calm": {"name_ru": "Спокойная", "about_ru": "Больше доходных источников без ставки на цену (кредитование, фикс-ставка) и ядра BTC/ETH/SOL; без фьючерсов."},
    "balanced": {"name_ru": "Сбалансированная", "about_ru": "Наша основная модель: понемногу везде; система сама двигает доли по рынку и ставкам."},
    "bold": {"name_ru": "Смелая", "about_ru": "Больше спота и фьючерсов — выше и доход, и риск; доходные источники как подушка."},
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
         "basis": "Фикс-ставка на фьючерсах (BTC/ETH)", "yield": "Кредитование стейблкоинов (Aave, Sky)",
         "susde": "Доходные доллары (Ethena sUSDe)"}
SHORT = {"spot": "спот", "futures": "фьючерсы", "funding": "фандинг", "basis": "фикс-ставка", "yield": "кредитование", "susde": "sUSDe"}
TITLE = {"spot": "Спот", "futures": "Фьючерсы", "funding": "Фандинг", "basis": "Фикс-ставка", "yield": "Кредитование", "susde": "sUSDe"}
KYIV = dt.timezone(dt.timedelta(hours=3))


def _load(out: str, name: str) -> dict:
    try:
        return json.load(open(os.path.join(out, name), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _susde(out: str) -> dict:
    return ((_load(out, "yield.json").get("accounts") or {}).get("susde")) or {}


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
    bd = _load(out, "basis.json")
    if bd.get("equity") is not None:
        v["basis"] = float(bd["equity"])
    yd = _load(out, "yield.json")
    if yd.get("equity") is not None:
        v["yield"] = float(yd["equity"])
    if _susde(out).get("equity") is not None:
        v["susde"] = float(_susde(out)["equity"])
    return v


def _ret(h: list, since: int) -> float | None:
    h = [x for x in h if x[0] >= since and x[1]]
    return h[-1][1] / h[0][1] - 1 if len(h) >= 2 else None


def desk_30d(out: str, now: int) -> dict:
    """Return of each desk over the last 30 days (from the desks' own histories), for the tilt of the directional part."""
    since = now - 30 * 86_400_000
    r = {}
    pf = (_load(out, "portfolio.json").get("portfolios") or {}).get("ideas") or {}
    x = _ret([[p.get("time", 0), p.get("v")] for p in pf.get("history") or []], since)
    if x is not None:
        r["spot"] = x
    prof = _load(out, "paper.json").get("profiles") or {}
    xs = [y for k in ("bal", "imp") if (y := _ret([[p.get("time", 0), p.get("equity")] for p in (prof.get(k) or {}).get("equity_history") or []], since)) is not None]
    if xs:
        r["futures"] = sum(xs) / len(xs)
    for k, h in (("funding", _load(out, "funding.json").get("history")), ("basis", _load(out, "basis.json").get("history")),
                 ("yield", _load(out, "yield.json").get("history")), ("susde", _susde(out).get("history"))):
        x = _ret(h or [], since)
        if x is not None:
            r[k] = x
    return r


def carry_rates(out: str, now: int) -> dict:
    """What each no-direction source pays NOW, % a year."""
    r = {}
    yd = _load(out, "yield.json")
    if yd.get("apy_now") is not None:
        r["yield"] = float(yd["apy_now"])
    if _susde(out).get("apy_now") is not None:
        r["susde"] = float(_susde(out)["apy_now"])
    bd = _load(out, "basis.json")
    if bd:
        pos = bd.get("position")
        rn = bd.get("rate_now")
        r["basis"] = float(pos["rate"]) if pos else (float(rn) if rn is not None and rn >= (bd.get("min_rate") or 0) else 0.0)
    h = [x for x in _load(out, "funding.json").get("history") or [] if x[0] >= now - 7 * 86_400_000 and x[1]]
    if len(h) >= 2 and h[-1][0] - h[0][0] >= 3 * 86_400_000:          # the funding desk's own last week, annualised
        r["funding"] = (h[-1][1] / h[0][1] - 1) * 365 * 86_400_000 / (h[-1][0] - h[0][0]) * 100
    return r


def regime(out: str) -> tuple[str, str]:
    """BTC above its 200-day average and up over 90 days → «рост». A short panic does not switch it (selling into a panic was not
    supported by the test); an unknown flag keeps «рост»."""
    bull = ((_load(out, "decision.json").get("risk") or {}).get("btc_bull"))
    if bull is False:
        return "off", "BTC ниже своей 200-дневной средней или за 90 дней в минусе"
    return "on", "BTC выше своей 200-дневной средней и за 90 дней в плюсе"


def allocate(profile: dict, reg: str, r30: dict, rates: dict, available: set, reason: str | None = None) -> tuple[dict, list]:
    """Weights of the sleeves (sum 1) and the reasons in Russian."""
    why = []
    d = profile[reg]
    w = {"spot": d * (1 - profile["fut"]), "futures": d * profile["fut"]}
    why.append(f"Рынок: {'рост' if reg == 'on' else 'осторожность'} — {reason or regime_text(reg)}: на спот и фьючерсы {d*100:.0f}%.")
    for k in DIRECTIONAL:
        x = r30.get(k)
        if k not in available:
            w[k] = 0.0
        elif w[k] and x is not None and x < -0.05:
            w[k] *= 0.5
            why.append(f"{TITLE[k]} за 30 дней {x*100:+.1f}% — доля вдвое меньше.")
        elif w[k] and x is not None and x > 0.05:
            w[k] *= 1.25
            why.append(f"{TITLE[k]} за 30 дней {x*100:+.1f}% — доля больше в 1.25 раза.")
    if w["spot"] + w["futures"] > d + 0.10:          # a good month may add, but not more than 10 points over the regime's share
        f = (d + 0.10) / (w["spot"] + w["futures"])
        w = {k: v * f for k, v in w.items()}
    c = max(0.0, 1.0 - w["spot"] - w["futures"])
    keys = [k for k in CARRY if k in available]
    score = {k: max(0.0, (rates.get(k) if rates.get(k) is not None else DEFAULT_RATE) - DISCOUNT[RISK[k]]) for k in keys}
    for k in keys:
        rt = rates.get(k)
        why.append(f"{TITLE[k]}: " + (f"платит {rt:.1f}% годовых" if rt is not None else "ставка ещё неизвестна")
                   + (f", за риск −{DISCOUNT[RISK[k]]:.1f}" if DISCOUNT[RISK[k]] else "") + f" → оценка {score[k]:.1f}.")
    cw = {k: 0.0 for k in keys}
    if "yield" in cw:
        cw["yield"] = YIELD_FLOOR * c
    rest, free = c - sum(cw.values()), [k for k in keys]
    for _ in range(len(keys) + 1):                   # ∝ score, caps fixed one by one, the rest to the others
        tot = sum(score[k] for k in free)
        if rest <= 1e-12 or tot <= 0:
            break
        over = [k for k in free if k in CAP and cw[k] + rest * score[k] / tot > CAP[k] + 1e-12]
        if not over:
            for k in free:
                cw[k] += rest * score[k] / tot
            rest = 0.0
            break
        for k in over:
            rest -= CAP[k] - cw[k]
            cw[k] = CAP[k]
            free.remove(k)
    if rest > 1e-12:                                  # nothing pays → the reserve
        if "yield" in cw:
            cw["yield"] += rest
        else:
            w["spot"] = w.get("spot", 0) + rest
    w.update(cw)
    w = {k: v for k, v in w.items() if k in available or v > 0}
    s = sum(w.values()) or 1.0
    return {k: round(v / s, 4) for k in KEYS if (v := w.get(k)) is not None and k in available}, why


def regime_text(reg: str) -> str:
    return "BTC выше 200-дневной средней" if reg == "on" else "BTC ниже 200-дневной средней"


def target_weights(r30: dict, available: set, reg: str = "on", rates: dict | None = None, profile: str = "balanced") -> dict:
    return allocate(PROFILES[profile], reg, r30, rates or {}, available)[0]


def _month_start(now: int) -> int:
    d = dt.datetime.fromtimestamp(now / 1000, KYIV)
    return int(dt.datetime(d.year, d.month, 1, tzinfo=KYIV).timestamp() * 1000)


def _monday(now: int) -> str:
    d = dt.datetime.fromtimestamp(now / 1000, KYIV)
    return (d - dt.timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def _follow(sleeves: dict, last: dict, desks: dict, spot_bonus: float = 1.0) -> dict:
    """Each sleeve moves by its desk's return since the previous run; the spot sleeve also earns the lending rate on its cash."""
    out = {k: (v * desks[k] / last[k] if last.get(k) and desks.get(k) else v) for k, v in sleeves.items()}
    if "spot" in out:
        out["spot"] *= spot_bonus
    return out


def spot_cash_bonus(out: str, rates: dict, dt_ms: int) -> float:
    """18.10 (owner: «докупать за счёт свободных стейблов»): the spot portfolio's own cash (≥ 10 %, waiting for the next buy) is not
    idle — it sits in lending like the rest of the dollars. Multiplier for the spot sleeve over dt_ms."""
    pf = (_load(out, "portfolio.json").get("portfolios") or {}).get("ideas") or {}
    cash = (pf.get("cash_pct") or 0) / 100
    apy = rates.get("yield")
    if not cash or apy is None or dt_ms <= 0:
        return 1.0
    return 1 + cash * apy / 100 * dt_ms / (365 * 86_400_000)


def _resplit(sleeves: dict, w: dict) -> tuple[dict, float]:
    total = sum(sleeves.values())
    moved = sum(abs(total * w.get(k, 0) - sleeves.get(k, 0)) for k in set(w) | set(sleeves)) / 2
    cost = moved * COST
    return {k: (total - cost) * x for k, x in w.items()}, cost


def step(conn: sqlite3.Connection, out: str = "web", now: int | None = None) -> dict:
    now = now or int(time.time() * 1000)
    conn.execute("CREATE TABLE IF NOT EXISTS capital_state(k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS capital_hist(time INTEGER PRIMARY KEY, value REAL)")
    st = {k: json.loads(v) for k, v in conn.execute("SELECT k, v FROM capital_state")}
    desks = desk_values(out)
    reg, reason = regime(out)
    rates = carry_rates(out, now)
    events = []
    if (st.get("sleeves") or st.get("models")) and st.get("version") != VERSION:   # the allocator changed → one clean restart
        old = st.get("sleeves") or st["models"]["balanced"]["sleeves"]
        prev = {"version": st.get("version", 1), "started": st.get("started"), "ended": now, "value": round(sum(old.values()), 2)}
        conn.execute("DELETE FROM capital_state")
        conn.execute("DELETE FROM capital_hist")
        st = {"previous": prev}
        events.append(f"Новая версия распределения: 6 источников и умный распределитель — демо-счёт начат заново с ${START:,.0f} "
                      f"(прошлый запуск закончился на ${prev['value']:,.2f}).")
    avail = set(desks)
    if not st.get("models"):
        r30 = desk_30d(out, now)
        st = {**st, "version": VERSION, "started": now, "last": desks, "week": _monday(now), "month_start": _month_start(now),
              "month_value": START, "regime": reg, "split_at": now, "models": {}}
        for m, prof in PROFILES.items():
            w, why = allocate(prof, reg, r30, rates, avail, reason)
            st["models"][m] = {"sleeves": {k: START * x for k, x in w.items()}, "weights": w, "why": why, "hist": [[now, START]]}
        st["bench"] = {k: START * x for k, x in st["models"]["balanced"]["weights"].items()}
        events.append(f"Старт: ${START:,.0f} разделены — " + ", ".join(f"{SHORT[k]} {x*100:.0f}%" for k, x in st["models"]["balanced"]["weights"].items()))
    else:
        last = st["last"]
        bonus = spot_cash_bonus(out, rates, now - st.get("t_last", now))
        for m in st["models"].values():
            m["sleeves"] = _follow(m["sleeves"], last, desks, bonus)
        st["bench"] = _follow(st["bench"], last, desks, bonus)
        st["last"] = {**last, **desks}
        main = st["models"]["balanced"]
        if _month_start(now) != st["month_start"]:          # a new calendar month: the previous one is closed
            total = sum(main["sleeves"].values())
            prof = total - st["month_value"]
            st.setdefault("months", []).append({"month_start": st["month_start"], "start": round(st["month_value"], 2), "end": round(total, 2),
                                                "profit": round(prof, 2), "fee": round(max(0.0, prof) * FEE, 2)})
            st["month_start"], st["month_value"] = _month_start(now), total
        new = sorted(avail - set(main["sleeves"]))
        why_now = None
        if _monday(now) != st["week"]:
            why_now = "недельный пересчёт"
        elif new:
            why_now = "появился новый источник: " + ", ".join(SHORT[k] for k in new)
        elif reg != st.get("regime") and now - st.get("split_at", 0) >= REGIME_GAP_MS:
            why_now = "рынок сменил режим: " + reason
        if why_now:
            r30 = desk_30d(out, now)
            for mk, m in st["models"].items():
                w, why = allocate(PROFILES[mk], reg, r30, rates, avail | set(m["sleeves"]), reason)
                m["sleeves"], cost = _resplit(m["sleeves"], w)
                m["weights"], m["why"] = w, why
                if mk == "balanced":
                    events.append(f"Перераспределение ({why_now}): " + ", ".join(f"{SHORT[k]} {x*100:.0f}%" for k, x in w.items())
                                  + f"; комиссии ≈ ${cost:,.2f}.")
            st["week"], st["regime"], st["split_at"] = _monday(now), reg, now
    st["t_last"] = now
    for m in st["models"].values():
        m["hist"] = (m.get("hist") or [])[-399:] + [[now, round(sum(m["sleeves"].values()), 2)]]
    main = st["models"]["balanced"]
    total = sum(main["sleeves"].values())
    bench = sum(st["bench"].values())
    conn.execute("INSERT OR REPLACE INTO capital_hist VALUES(?, ?)", (now, round(total, 2)))
    st.setdefault("base_desks", {**desks})                   # each desk as an index (100 at the portfolio's start)
    for k, v in desks.items():
        st["base_desks"].setdefault(k, v)
    st["idx"] = (st.get("idx") or [])[-399:] + [[now] + [round(desks[k] / st["base_desks"][k] * 100, 4) if k in desks and st["base_desks"].get(k) else None
                                                          for k in KEYS]]
    st.setdefault("ev", [])
    st["ev"] = (st["ev"] + [{"t": now, "ru": e} for e in events])[-40:]
    for k, v in st.items():
        conn.execute("INSERT OR REPLACE INTO capital_state VALUES(?, ?)", (k, json.dumps(v)))
    conn.commit()
    hist = [[t, v] for t, v in conn.execute("SELECT time, value FROM capital_hist ORDER BY time")][-400:]
    month_profit = total - st["month_value"]
    arb = (_load(out, "arb.json").get("week") or {}).get("virtual_pnl_usd")
    tri = (_load(out, "tri.json").get("week") or {}).get("virtual_pnl_usd")
    w_now = main["weights"]
    return {"asof": now, "name": "CRYPTO NOVA Портфолио", "version": VERSION, "start_usd": START, "started": st["started"],
            "value_usd": round(total, 2), "pnl_usd": round(total - START, 2), "pnl_pct": round((total / START - 1) * 100, 2),
            "sleeves": [{"key": k, "name_ru": NAMES[k], "kind": "directional" if k in DIRECTIONAL else "carry", "risk": RISK[k],
                         "weight": w_now.get(k), "rate_now": round(rates[k], 2) if k in CARRY and rates.get(k) is not None else None,
                         "value_usd": round(v, 2), "share_now": round(v / total, 4) if total else None}
                        for k, v in sorted(main["sleeves"].items(), key=lambda kv: KEYS.index(kv[0]))],
            "regime": {"key": reg, "ru": ("рост" if reg == "on" else "осторожность"), "why_ru": reason},
            "why_ru": main.get("why", []), "split_at": st.get("split_at"),
            "benchmark": {"name_ru": "Без перераспределения (первое распределение, оставленное как есть)", "value_usd": round(bench, 2),
                          "pnl_usd": round(bench - START, 2), "pnl_pct": round((bench / START - 1) * 100, 2),
                          "allocator_edge_usd": round(total - bench, 2)},
            "month": {"start": st["month_start"], "start_value": round(st["month_value"], 2), "profit_usd": round(month_profit, 2),
                      "fee_usd": round(max(0.0, month_profit) * FEE, 2), "fee_pct": FEE * 100},
            "months": st.get("months", []), "history": hist, "events": events, "event_log": st["ev"][-20:], "previous": st.get("previous"),
            "index_keys": KEYS, "index_history": st.get("idx", []),
            "models": {k: {**MODELS[k], "w": m["weights"], "why_ru": m.get("why", []), "value_usd": round(sum(m["sleeves"].values()), 2),
                           "pnl_pct": round((sum(m["sleeves"].values()) / START - 1) * 100, 2), "history": m["hist"][-200:]}
                       for k, m in st["models"].items()},
            "options": OPTIONS,
            "watch_only": [{"key": "arb", "name_ru": "Арбитраж между биржами", "week_virtual_usd": arb},
                           {"key": "tri", "name_ru": "Треугольный арбитраж", "week_virtual_usd": tri}],
            "rules_ru": [
                f"Демо-счёт ${START:,.0f}: система сама делит сумму между шестью источниками и ведёт её месяц.",
                "Активная часть (спот и фьючерсы) зависит от рынка: когда BTC выше своей 200-дневной средней и за 90 дней в плюсе — 60%, "
                "иначе — 20%. Проверено на 2023–2026: так +168% против +100% при постоянных 40%, просадка −23% вместо −31%, плюс в каждом году. "
                "Направление, которое за 30 дней потеряло больше 5%, получает половину доли, заработавшее больше 5% — в 1.25 раза больше.",
                "Свободные доллары внутри спота (его кэш) тоже работают — под ставку кредитования.",
                "Остальное — источники без ставки на цену: кредитование стейблкоинов (Aave, Sky), фикс-ставка на фьючерсах BTC/ETH, "
                "фандинг и доходные доллары sUSDe. Делим по тому, сколько каждый платит СЕЙЧАС, минус скидка за риск (фандинг и sUSDe −1.5). "
                "В кредитовании всегда не меньше 30% этой части — резерв; потолки: фандинг 20%, фикс-ставка 30%, sUSDe 15%.",
                "Пересчёт — каждый понедельник, сразу при новом источнике и при смене режима рынка (не чаще раза в сутки). "
                "Каждый пересчёт стоит 0.1% от переложенной суммы — это комиссии.",
                "Рядом показан вариант «без перераспределения»: распределитель полезен, только если обгоняет его.",
                "Арбитраж и треугольник денег не получают: их цифры — виртуальная оценка, клиент не может их исполнить.",
                "В конце месяца — итог; 10% от прибыли — оплата (месяц без прибыли бесплатно).",
                "Это демо, а не обещание: результат может быть и минусом."]}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None) -> dict:
    doc = step(conn, out, now)
    with open(os.path.join(out, "capital.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc
