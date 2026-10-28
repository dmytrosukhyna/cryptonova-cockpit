"""CRYPTO NOVA Портфолио — one sum, the system splits it between our desks (18.10.2026, owner: «человек пополняет $1000, система сама
распределяет: спот, фьючерсы, фандинг… весь месяц работает, в конце подводит итоги: заработано столько-то, оплата 10%»; later the
same day: «участвовать в разных источниках, в разных секторах — он сам распределит, поймёт, адаптирует; нам нужен результат по профиту»).

A DEMO account of $10 000 (owner: «как будто я вложил настоящие свои 10 тысяч долларов» — his main screen). Seven sleeves, each
follows a desk the server already runs:
  • spot     — the model's spot portfolio: CRYPTO NOVA портфель (portfolios.ideas) or, for «Спокойная», «Консервативный»
               (portfolios.cons: BTC/ETH/SOL held)                                                              [directional]
  • active   — active spot trading: «Импульс BTC» on spot, no leverage, spot fees (paper.json profiles.imps)     [directional]
  • futures  — CRYPTO NOVA signals: «Сбалансированная» (paper.json profiles.bal) + «Импульс BTC» (profiles.imp)  [directional]
  • funding  — the funding-carry desk (funding.json equity_mtm)                                                  [no direction]
  • basis    — fixed-rate carry: spot + short dated futures on Deribit, rate locked at entry (basis.json)         [no direction]
  • yield    — stablecoin lending: Sky savings rate (sUSDS) + Aave v3 USDC + Aave savings GHO (yield.json)      [no direction]
  • susde    — yield-bearing dollars, Ethena sUSDe (yield.json accounts.susde)                                    [no direction]
  • listings — short of new listings (paper.json profiles.lst) — 19.10 (owner: «если свободные стейблы дадут лучше результат в
               других сферах — думать как и что»): the best use of free dollars we found — on history (729 Binance listings,
               5 % of an account per listing) 2025 +55 %, 2026 +33 % a year (2023 +2 %, 2024 +6 %) against ≈ 4 % in lending.
               It gets money ONLY after its own live test passes (LST_GATE: ≥ 20 closed demo trades, on average ≥ +1 % a trade
               after fees and funding); then a fixed part of the dollars (PROFILES lst: calm 0, balanced 8 %, bold 12 %, never
               more than half the dollar part), halved after a 30-day loss over 5 %; the gate failing later takes the money back
                                                                                                           [short, risk 4]
Arbitrage and the triangle are NOT given money: virtual estimates a client cannot run — shown as «наблюдение».

THE ALLOCATOR (v2). It decides two things and explains both in Russian («почему так»):
  1. How much goes to the directional part (spot + futures) — by the BTC regime: «рост» (BTC above its 200-day average and up over
     90 days — the same flag as the rest of the system) 70 %, otherwise 20 % (profile «Сбалансированная»; «Спокойная» 35/10 without
     futures and active trades, «Смелая» 85/35; see PROFILES). Tested 18.10 on 2023-06 → 2026-10 daily data (BTC/ETH/SOL basket + stables at 4 %): fixed 40 % spot
     +100 % (max drawdown −31 %), 60/35 by regime +162 % (−30 %), 60/20 +168 % (−23 %) and better in every year incl. 2025 (+11 %)
     and 2026 (+3 %); 70/20 +203 % (−26 %), better again in every year; the lower the share outside «рост», the better 2025–26 and
     the drawdown, for every share inside it. A short-
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
KEYS = ["spot", "active", "futures", "funding", "basis", "yield", "susde", "listings"]
IDX_KEYS = ["spot", "futures", "funding", "basis", "yield", "susde", "active", "listings"]   # index_history columns (new desks go last)
DIRECTIONAL = ("spot", "active", "futures")
CARRY = ("funding", "basis", "yield", "susde")
# 18.10 backtest (scripts/lab/portfolio_rules.py): regime shares 0.5–0.7 × 0.10–0.35 — lower «off» better in 2025–26 every time
RISK = {"spot": 4, "active": 4, "futures": 4, "funding": 3, "basis": 2, "yield": 2, "susde": 3, "listings": 4}
LST_GATE = {"trades": 20, "avg_trade_pct": 1.0}
OWN_GATE = {"trades": 30, "avg_trade_pct": 0.3}   # «Сигналы CRYPTO NOVA» join the futures sleeve after this live record (history: +0.76 % a trade)  # the listing short gets dollars only after this live record (criteria of «Концепция»)
DISCOUNT = {2: 0.0, 3: 1.5}                      # percentage points taken off a source's rate for its risk
CAP = {"funding": 0.20, "basis": 0.30, "susde": 0.15}
YIELD_FLOOR = 0.30                               # share of the carry part that always stays in lending (liquid reserve)
DEFAULT_RATE = 2.0                               # a source whose rate is unknown yet
FEE = 0.10
COST = 0.001                                     # a re-split costs 0.1 % of the money moved
REGIME_GAP_MS = 24 * 3_600_000
# 18.10 (owner): a user picks one of three models (and a couple of options) — the same allocator with a different appetite for risk.
# Later the same evening (owner: «консервативная — больше холда, меньше торговли; умеренная — монеты динамичнее и немного спотовой
# торговли; агрессивная — больше покупать-продавать, переставлять; и всё грамотно застраховано»):
#   on / off — the directional share when BTC is above / below its 200-day average (tested: 0.7/0.2 → +203 %, drawdown −26 %,
#              better than 0.6/0.2 in every year 2023–2026; the owner: «сейчас бычий рынок — кредитование важнее в медвежьем»);
#   fut / act — parts of the directional share in futures signals and in ACTIVE SPOT trading (imps: «Импульс BTC» on spot — the
#              only active rule that passed the lab on 2025–26); the rest is the spot portfolio;
#   spot — which spot portfolio the model holds: calm = «Консервативный» (BTC 50 / ETH 25 / SOL 15, almost no trades),
#          the others = CRYPTO NOVA портфель (core + table alts + small high-risk).
# Protection for every model: the BTC-regime switch (tested: drawdown −23…−26 % vs −31 % fixed), stops on the alts and on the
# active trades, max 5 active positions, the reserve in lending.
PROFILES = {"calm": {"on": 0.35, "off": 0.10, "fut": 0.0, "act": 0.0, "lst": 0.0, "spot": {"spot_cons": 1.0}},
            "balanced": {"on": 0.70, "off": 0.20, "fut": 0.25, "act": 0.15, "lst": 0.08, "spot": {"spot": 1.0}},
            "bold": {"on": 0.85, "off": 0.35, "fut": 0.35, "act": 0.30, "lst": 0.12, "spot": {"spot": 1.0}}}
MODELS = {
    "calm": {"name_ru": "Спокойная", "about_ru": "Держим ядро BTC 60% / ETH 20% / SOL 10% почти без сделок; когда BTC ниже своей 200-дневной "
                                                 "средней — большая часть уходит в доходные доллары. Без фьючерсов и активной торговли."},
    "balanced": {"name_ru": "Сбалансированная", "about_ru": "CRYPTO NOVA портфель (ядро и отобранные альты), немного активной торговли на споте "
                                                         "и фьючерсов; доли система двигает сама — по рынку и по ставкам."},
    "bold": {"name_ru": "Смелая", "about_ru": "Больше активной торговли на споте (Импульс BTC) и фьючерсов — выше и доход, и риск. Защита та же: "
                                             "режим BTC, стопы, не больше 5 активных позиций, резерв в кредитовании."},
}
OPTIONS = {   # toggles that shift a model (applied in order, then normalised)
    "risky_coins": {"name_ru": "Добавить рискованные монеты", "about_ru": "Больше спота с мемами и высоким риском: +10% к споту за счёт кредитования.",
                    "shift": {"spot": +0.10, "yield": -0.10}},
    "more_defi": {"name_ru": "Больше DeFi-кредитования", "about_ru": "+10% в кредитование стейблкоинов (малый риск) за счёт фьючерсов и спота.",
                  "shift": {"yield": +0.10, "futures": -0.05, "spot": -0.05}},
    "no_futures": {"name_ru": "Без фьючерсов", "about_ru": "Фьючерсная часть уходит в спот и кредитование поровну.",
                   "zero": "futures"},
}
NAMES = {"spot": "Спот · портфель", "active": "Активный спот · Импульс BTC", "futures": "Фьючерсы · CRYPTO NOVA", "funding": "Фандинг (без направления)",
         "basis": "Фикс-ставка на фьючерсах (BTC/ETH)", "yield": "Кредитование стейблкоинов (Aave, Sky)",
         "susde": "Доходные доллары (Ethena sUSDe)", "listings": "Шорт новых листингов"}
SHORT = {"spot": "спот", "active": "активный спот", "futures": "фьючерсы", "funding": "фандинг", "basis": "фикс-ставка", "yield": "кредитование",
         "susde": "sUSDe", "listings": "шорт листингов"}
TITLE = {"spot": "Спот", "active": "Активный спот", "futures": "Фьючерсы", "funding": "Фандинг", "basis": "Фикс-ставка", "yield": "Кредитование",
         "susde": "sUSDe", "listings": "Шорт листингов"}
KYIV = dt.timezone(dt.timedelta(hours=3))


def _load(out: str, name: str) -> dict:
    try:
        return json.load(open(os.path.join(out, name), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _susde(out: str) -> dict:
    return ((_load(out, "yield.json").get("accounts") or {}).get("susde")) or {}


def own_gate(out: str) -> tuple[bool, dict]:
    """Has the own-signals demo desk earned a place in the futures result? (≥ 30 closed trades, ≥ +0.3 % a trade on average)."""
    s = (((_load(out, "paper.json").get("profiles") or {}).get("own") or {}).get("stats")) or {}
    n, avg = s.get("trades") or 0, s.get("avg_trade_pct")
    return bool(n >= OWN_GATE["trades"] and avg is not None and avg >= OWN_GATE["avg_trade_pct"]), {"trades": n, "avg_trade_pct": avg}


def fut_keys(out: str) -> tuple:
    """The desks of FUTURES · CRYPTO NOVA as the panel header counts them (28.10): the main desk the selector chose
    (web/desk_select.json → panel_header.nova_base; none → «Агрессивная») + «Импульс BTC», + the own signals after their gate."""
    from . import panel_header as ph
    base = ph.nova_base(ph.load_sel(out))
    return base + (("own",) if own_gate(out)[0] and "own" not in base else ())


def lst_gate(out: str) -> tuple[bool, dict]:
    """Has the listing-short demo desk earned real dollars? (≥ 20 closed trades, ≥ +1 % a trade on average)."""
    s = (((_load(out, "paper.json").get("profiles") or {}).get("lst") or {}).get("stats")) or {}
    n, avg = s.get("trades") or 0, s.get("avg_trade_pct")
    return bool(n >= LST_GATE["trades"] and avg is not None and avg >= LST_GATE["avg_trade_pct"]), {"trades": n, "avg_trade_pct": avg}


def desk_values(out: str) -> dict:
    """Current equity of each desk (any unit — only the ratio between runs matters). Missing → not in the dict."""
    v = {}
    pfs = _load(out, "portfolio.json").get("portfolios") or {}
    for key, name in (("spot", "ideas"), ("spot_cons", "cons")):
        pf = pfs.get(name) or {}
        val = pf.get("equity_usd")
        if val is None:                                  # some spot desks publish only their index (100 at start)
            val = pf.get("index") if pf.get("index") is not None else ((pf.get("history") or [{}])[-1].get("v"))
        if val:
            v[key] = float(val)
    prof = _load(out, "paper.json").get("profiles") or {}
    d = prof.get("imps") or {}
    if d.get("equity") is not None:
        v["active"] = float(d["equity"]) + sum(float(o.get("open_pnl_usd") or 0) for o in d.get("open") or [])
    fut = [float(d["equity"]) + sum(float(o.get("open_pnl_usd") or 0) for o in d.get("open") or [])
           for k in fut_keys(out) if (d := prof.get(k)) and d.get("equity") is not None]
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
    d = prof.get("lst") or {}
    if d.get("equity") is not None:                      # tracked always (index), given money only after the gate
        v["listings"] = float(d["equity_mtm"] if d.get("equity_mtm") is not None else d["equity"])
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
    x = _ret([[p.get("time", 0), p.get("equity")] for p in (prof.get("imps") or {}).get("equity_history") or []], since)
    if x is not None:
        r["active"] = x
    x = _ret([[p.get("time", 0), p.get("equity")] for p in (prof.get("lst") or {}).get("equity_history") or []], since)
    if x is not None:
        r["listings"] = x
    from . import panel_header as ph
    xs = [y for k in ph.nova_base(ph.load_sel(out)) if (y := _ret([[p.get("time", 0), p.get("equity")] for p in (prof.get(k) or {}).get("equity_history") or []], since)) is not None]
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
    ok, s = lst_gate(out)
    if ok:
        r.update({"listings_ok": True, "listings_n": s["trades"], "listings_avg": s["avg_trade_pct"]})
    h = [x for x in _load(out, "funding.json").get("history") or [] if x[0] >= now - 7 * 86_400_000 and x[1]]
    if len(h) >= 2 and h[-1][0] - h[0][0] >= 3 * 86_400_000:          # the funding desk's own last week, annualised
        r["funding"] = (h[-1][1] / h[0][1] - 1) * 365 * 86_400_000 / (h[-1][0] - h[0][0]) * 100
    return r


def regime(out: str) -> tuple[str, str]:
    """BTC above its 200-day average and up over 60 days → «рост» (18.10 robustness test, scripts/lab/regime_robust.py: 20 variants
    of SMA 100–250 × lookback none/30/60/90/120 all beat fixed 40 % (+88 %); 60 days was best or near best for every SMA —
    SMA200 + 60 d: +197 %, drawdown −25 %, 2025 +15 %, 2026 +9 % vs SMA200 + 90 d: +183 %, −26 %, +13 %, +3 %). Uses the terminal's
    daily BTC figures (decision.json coins → BTC regime: sma200, r60, price); without them — the system-wide btc_bull flag
    (200-day average + 90 days). A short panic does not switch it; an unknown state keeps «рост»."""
    dec = _load(out, "decision.json")
    btc = next((c for c in dec.get("coins") or [] if str(c.get("symbol", "")).startswith("BTC")), None) or {}
    rg = btc.get("regime") or {}
    px, sma, r60 = btc.get("price"), rg.get("sma200"), rg.get("r60")
    if px and sma and r60 is not None and r60 == r60:
        if px > sma and r60 > 0:
            return "on", f"BTC выше своей 200-дневной средней и за 60 дней в плюсе ({r60 * 100:+.0f}%)"
        return "off", ("BTC ниже своей 200-дневной средней" if px <= sma else f"BTC за 60 дней в минусе ({r60 * 100:+.0f}%)")
    bull = (dec.get("risk") or {}).get("btc_bull")
    if bull is False:
        return "off", "BTC ниже своей 200-дневной средней или за 90 дней в минусе"
    return "on", "BTC выше своей 200-дневной средней и за 90 дней в плюсе"


def allocate(profile: dict, reg: str, r30: dict, rates: dict, available: set, reason: str | None = None) -> tuple[dict, list]:
    """Weights of the sleeves (sum 1) and the reasons in Russian."""
    why = []
    d = profile[reg]
    act = profile.get("act", 0.0)
    w = {"spot": d * (1 - profile["fut"] - act), "active": d * act, "futures": d * profile["fut"]}
    why.append(f"Рынок: {'рост' if reg == 'on' else 'осторожность'} — {reason or regime_text(reg)}: на рост (спот, активный спот, фьючерсы) {d*100:.0f}%.")
    for k in ("active", "futures"):                  # a desk that isn't running yet: its share stays in the spot portfolio
        if k not in available and "spot" in available:
            w["spot"] += w[k]
            w[k] = 0.0
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
    if sum(w.values()) > d + 0.10:                   # a good month may add, but not more than 10 points over the regime's share
        f = (d + 0.10) / sum(w.values())
        w = {k: v * f for k, v in w.items()}
    c = max(0.0, 1.0 - sum(w.values()))
    if "listings" in available:
        wl = 0.0
        if rates.get("listings_ok") and profile.get("lst"):
            wl = min(profile["lst"], c * 0.5)
            x = r30.get("listings")
            why.append(f"Шорт листингов прошёл живую проверку ({rates.get('listings_n', 0)} сделок, в среднем "
                       f"{rates.get('listings_avg', 0):+.1f}% на сделку) — из долларов {wl*100:.0f}% на шорт новых монет.")
            if x is not None and x < -0.05:
                wl *= 0.5
                why.append(f"Шорт листингов за 30 дней {x*100:+.1f}% — доля вдвое меньше.")
        w["listings"] = wl
        c -= wl
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


def _follow(sleeves: dict, last: dict, desks: dict, spot_bonus: float = 1.0, spot_mix: dict | None = None) -> dict:
    """Each sleeve moves by its desk's return since the previous run; the spot sleeve follows the model's spot portfolio(s)
    (spot_mix: {desk: share}) and also earns the lending rate on its cash."""
    out = {k: (v * desks[k] / last[k] if last.get(k) and desks.get(k) else v) for k, v in sleeves.items() if k != "spot"}
    if "spot" in sleeves:
        mix = {k: x for k, x in (spot_mix or {"spot": 1.0}).items() if last.get(k) and desks.get(k)}
        r = sum(x * desks[k] / last[k] for k, x in mix.items()) / sum(mix.values()) if mix else 1.0
        out["spot"] = sleeves["spot"] * r * spot_bonus
    return {k: out[k] for k in sleeves}


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


def rank_models(models: dict, now: int, started: int) -> dict:
    """18.10 (owner: «самая эффективная среди стратегий будет нашей»): models by result per unit of drawdown. A verdict only after
    28 days — a week or two of numbers decides nothing."""
    rows = []
    for k, m in models.items():
        h = [v for _, v in m.get("hist") or []]
        if not h:
            continue
        peak, dd = h[0], 0.0
        for v in h:
            peak = max(peak, v)
            dd = min(dd, v / peak - 1)
        pnl = h[-1] / START - 1
        rows.append({"key": k, "name_ru": MODELS[k]["name_ru"], "pnl_pct": round(pnl * 100, 2), "max_dd_pct": round(dd * 100, 2),
                     "score": round(pnl / max(0.02, -dd), 3)})
    rows.sort(key=lambda r: -r["score"])
    days = (now - started) / 86_400_000
    ready = days >= 28 and len(rows) >= 2
    return {"rows": rows, "days": round(days, 1), "ready": ready, "leader": rows[0]["key"] if ready else None,
            "ru": (f"Лидер за {days:.0f} дн. по доходу с учётом просадки: «{rows[0]['name_ru']}»." if ready else
                   f"Сравнение моделей — через 28 дней (прошло {days:.0f}); раньше цифры ничего не решают.")}


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
    avail = set(desks) & set(KEYS)
    if not rates.get("listings_ok"):                     # tracked, but not a source of its own until its live test passes
        avail.discard("listings")
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
        for mk, m in st["models"].items():
            m["sleeves"] = _follow(m["sleeves"], last, desks, bonus, PROFILES[mk]["spot"])
        st["bench"] = _follow(st["bench"], last, desks, bonus, PROFILES["balanced"]["spot"])
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
                                                          for k in IDX_KEYS]]
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
            "sleeves": [{"key": k, "name_ru": NAMES[k], "kind": "directional" if k in DIRECTIONAL else ("short" if k == "listings" else "carry"), "risk": RISK[k],
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
            "index_keys": IDX_KEYS, "index_history": st.get("idx", []),
            "models": {k: {**MODELS[k], "w": m["weights"], "why_ru": m.get("why", []), "value_usd": round(sum(m["sleeves"].values()), 2),
                           "pnl_pct": round((sum(m["sleeves"].values()) / START - 1) * 100, 2), "history": m["hist"][-200:]}
                       for k, m in st["models"].items()},
            "models_rank": rank_models(st["models"], now, st["started"]), "options": OPTIONS,
            "watch_only": [{"key": "arb", "name_ru": "Арбитраж между биржами", "week_virtual_usd": arb},
                           {"key": "tri", "name_ru": "Треугольный арбитраж", "week_virtual_usd": tri}],
            "rules_ru": [
                f"Демо-счёт ${START:,.0f}: система сама делит сумму между шестью источниками и ведёт её месяц.",
                "Часть со ставкой на рост (спот, активный спот, фьючерсы) зависит от рынка: когда BTC выше своей 200-дневной средней и "
                "за 60 дней в плюсе — 70%, иначе — 20% (из 20 вариантов настройки 60 дней — лучший или близкий к лучшему при любой средней). Проверено на 2023–2026: так +203% против +100% при постоянных 40%, просадка −26% "
                "вместо −31%, плюс в каждом году. В росте доходные доллары — подушка, в падении — основное место для денег. "
                "Направление, которое за 30 дней потеряло больше 5%, получает половину доли, заработавшее больше 5% — в 1.25 раза больше.",
                "Свободные доллары внутри спота (его кэш) тоже работают — под ставку кредитования.",
                "Остальное — источники без ставки на цену: кредитование стейблкоинов (Aave, Sky), фикс-ставка на фьючерсах BTC/ETH, "
                "фандинг и доходные доллары sUSDe. Делим по тому, сколько каждый платит СЕЙЧАС, минус скидка за риск (фандинг и sUSDe −1.5). "
                "В кредитовании всегда не меньше 30% этой части — резерв; потолки: фандинг 20%, фикс-ставка 30%, sUSDe 15%.",
                "Пересчёт — каждый понедельник, сразу при новом источнике и при смене режима рынка (не чаще раза в сутки). "
                "Каждый пересчёт стоит 0.1% от переложенной суммы — это комиссии.",
                "Рядом показан вариант «без перераспределения»: распределитель полезен, только если обгоняет его.",
                "Три модели: Спокойная — держим ядро BTC/ETH/SOL почти без сделок; Сбалансированная — CRYPTO NOVA портфель, немного "
                "активной торговли и фьючерсов; Смелая — больше активной торговли и фьючерсов. Активная торговля — «Импульс BTC» на споте "
                "(единственное активное правило, прошедшее проверку на 2025–2026). Через 4 недели сравниваем модели по доходу с учётом "
                "просадки — лучшая становится основной.",
                "Шорт новых листингов — лучшее применение свободных долларов на истории (2025 +55%, 2026 +33% в год против ≈4% в "
                "кредитовании), но рискованное: деньги он получает только после живой проверки — 20 закрытых демо-сделок и в среднем "
                "не меньше +1% на сделку. Тогда — 8% суммы в Сбалансированной и 12% в Смелой (Спокойная без него).",
                "Арбитраж и треугольник денег не получают: их цифры — виртуальная оценка, клиент не может их исполнить.",
                "В конце месяца — итог; 10% от прибыли — оплата (месяц без прибыли бесплатно).",
                "Это демо, а не обещание: результат может быть и минусом."]}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None) -> dict:
    doc = step(conn, out, now)
    with open(os.path.join(out, "capital.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc
