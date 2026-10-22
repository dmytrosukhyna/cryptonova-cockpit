"""Конвейер стратегий (20.10.2026): у каждой стратегии есть стадия LAB → PAPER → READY → RETIRED.

Owner (20.10, вечер): бесконечный цикл «исследование → проверка → решение → замер → улучшение», строгая проверка, без обещаний.

What it does (once an hour at most, as an engine step after the desks):
  * registry of every strategy:
      - every demo desk the engine runs (`web/paper.json` profiles; the full trade record is read from the engine's own
        data/agents.sqlite when the desk's table is there, else from the feed's last closed trades);
      - the nightly autolab's passed rules (`web/autolab.json`, stage LAB);
      - a short hand-kept list of research rules that passed a check in docs/ but run as no desk (stage LAB, `DOC_CANDIDATES`);
  * numeric gates (constants below):
      - LAB → PAPER is only a RECOMMENDATION; the conveyor never starts a desk;
      - PAPER → READY: enough trades, days, mean, profit factor, drawdown, live not worse than its history (one-sided t-test);
      - RETIRE is automatic (`paused: true` in data/conveyor_state.json — the risk gate then blocks the desk's NEW entries);
        a retired desk keeps its history; only the owner brings it back: `python -m cnq.cli conveyor reinstate <desk> --reason …`;
  * READY → real money is never automatic: it stays the owner's decision. Nothing here sends orders or touches cnq/execution.

Outputs:
  data/conveyor_state.json — the contract with the risk gate (exact shape, server only, never committed):
      {"updated_at": <ms>, "desks": {"<desk>": {"stage": "lab|paper|ready|retired", "paused": bool, "reason_ru": "…", "reason_en": "…"}}}
  web/conveyor.json        — public: every strategy with its stage, key numbers, the gate it waits on, counts per stage, change log.
State: tables conveyor_stage / conveyor_log / conveyor_meta in data/agents.sqlite (the engine's own database).

Honesty notes the numbers below carry (audit 20.10, docs/research/conveyor.md):
  * most desks' rules were tuned on the same history they are compared with — the «backtest mean» is an upper bound, so the
    t-test below is a decay alarm, not a proof of edge;
  * the only true time hold-out of the autopilot (docs/research/universe.md, 2025–26) shows ≈ +9 %/yr for B+E, not +50–70 %;
  * live samples are tiny: nothing is promoted or retired below the minimum sample.
Stdlib only."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sqlite3
import time

DAY = 86_400_000
WEEK = 7 * DAY
MONDAY0 = 4 * DAY                       # 1970-01-05 (Monday) 00:00 UTC — start of week 0

STAGES = ("lab", "paper", "ready", "retired")
STATE_PATH = os.path.join("data", "conveyor_state.json")
FEED = "conveyor.json"

# ─────────────────────────────────────────────── ворота PAPER → READY (нужны ВСЕ сразу) ───────────────────────────────────────────────
READY_MIN_TRADES = 30        # закрытых демо-сделок: меньше — шум; 30 — минимум, при котором t-тест хоть что-то значит
READY_MIN_DAYS = 21          # дней живой работы: хотя бы три разные недели рынка, а не один удачный обвал
READY_MIN_MEAN_PCT = 0.30    # средняя сделка после комиссий, % от объёма позиции: ≈ два круга издержек (0.14%) с запасом
READY_MIN_PF = 1.2           # профит-фактор (сумма плюсов / сумма минусов): ниже — край слишком тонкий для реальных проскальзываний
READY_MAX_DD_PCT = 15.0      # максимальная просадка демо-счёта, % (по кривой с открытыми позициями)
DD_VS_BACKTEST = 1.5         # и не глубже 1.5× просадки, которую показала история стола (если она известна)
READY_P_WORSE = 0.05         # односторонний t-тест «живая средняя хуже исторической»: p < 0.05 → не готов

# ─────────────────────────────────────────── автоматическое снятие RETIRE (paused: true) ───────────────────────────────────────────
RETIRE_MIN_TRADES = 20       # минимальная выборка: до 20 закрытых сделок стол не снимается ни по одному правилу
RETIRE_LAST_N = 20           # окно «последних сделок»
RETIRE_LAST_MEAN_PCT = -0.5  # последние 20 сделок в среднем хуже −0.5% …
RETIRE_DD_MULT = 1.5         # … И просадка глубже 1.5× ожидаемой — снимаем (одно из двух — ещё не повод)
RETIRE_LOSING_WEEKS = 4      # или 4 полные недели подряд в минусе (неделя без закрытых сделок серию прерывает)
RETIRE_P_WORSE = 0.01        # или живая средняя значимо хуже истории: p < 0.01 (строже, чем для READY)
DD_FALLBACK_EXPECTED_PCT = 10.0   # история просадки неизвестна → ожидаем 10%; 1.5 × 10% = 15% = потолок ворот READY

# ──────────────────────────────────────────── LAB → PAPER (только рекомендация владельцу) ────────────────────────────────────────────
LAB_MIN_TRADES = 100         # docs/approach-review.md: «paper на реальных данных ≥ 100 сделок на семью сетапов»
LAB_MIN_OOS_YEARS = 5        # autolab не публикует число сделок → вместо него годы честной проверки «вперёд» (≥ 20 кварталов)
LAB_MAX_AGE_YEARS = 2        # проверка вслепую должна доходить до последних 2 лет рынка (данные до 2022 — устарели)
BONF_ALPHA = 0.05            # поправка Бонферрони на число перебранных вариантов (autolab: «tried» за ночь)

RUN_EVERY_MS = 50 * 60_000   # шаг движка — не чаще раза в час (движок зовёт раз в 60 циклов; запас на дрожание цикла)
LOG_KEEP = 200               # строк журнала в публичном файле

# ─────────────────────── история: средняя сделка после издержек, % от объёма (эталон для t-теста) — со ссылками ───────────────────────
# Большинство цифр получено на той же истории, на которой правило подбирали (in-sample): это верхняя граница, не ожидание.
RULE_REF = {
    "B": (0.95, "docs/research/orders.md: 729 сделок 2021–26, вход сразу после свечи"),
    "E": (1.07, "docs/research/panic_breadth.md: живой порог 0.4 лежит между ≥30% (+1.07%) и ≥50% (+1.14%) — берём нижнюю"),
    "F": (1.53, "docs/research/oi_flush.md: стоп −6%, 2023–26"),
    "D": (1.77, "docs/research/new_signals.md: 1 из 9 прошедших среди 439 вариантов; без 10 лучших дней +0.36%"),
    "T": (0.75, "описание стола brk: 2025–26; scripts/lab/results/trend.json по текущему правилу лаборатории НЕ проходит"),
    "K": (0.22, "scripts/lab/results/trend_wide50.json: TEST 2025–26 при BTC +2% (живой порог 2.5% в сетке не хранится)"),
    "S": (0.31, "docs/research/swing.md: в портфеле ≈ 0"),
    "G": (0.41, "описание стола exp; отдельного исследования нет"),
    "P": (3.1, "docs/research/own_signals.md раунды 9–10: топ-40 на дату, P и C вместе"),
    "C": (3.1, "docs/research/own_signals.md раунды 9–10: топ-40 на дату, P и C вместе"),
    "LST": (2.6, "docs/research/listings_more.md: 729 листингов Binance; MEXC −2.1%, BingX не проверялся"),
    "J": (0.76, "docs/research/macro_factors.md: 2023–26, в 2021–22 ≈ 0"),
    "L": (0.30, "описание стола lab; docs/research/range.md: ни один вариант не прошёл"),
    "SWP": (1.21, "docs/research/sweep.md: 217 сделок 2019–26, BTC + 10 альтов, «рост»; t по неделям 1.82 — на грани, альты выбраны задним числом"),
}
DESK_RULE_REF = {("imps", "K"): (0.16, "как K, но комиссия спота 0.1%/сторона: ≈ −0.06% на сделку")}

# ─────────────── стол → что обещала его история (просадка %), качество доказательства, оговорки (аудит 20.10, STRATEGY_MATRIX) ───────────────
# Просадки — scripts/autolab.py EXPECT («что обещала честная история каждого демо-счёта»).
DESKS = {
    "cons": {"dd": 9, "en": "Autopilot · conservative", "ev": "partial"},
    "bal": {"dd": 18, "en": "Autopilot · balanced", "ev": "partial"},
    "aggr": {"dd": 29, "en": "Autopilot · aggressive", "ev": "partial"},
    "lab": {"dd": None, "en": "New-idea test", "ev": "none"},
    "brk": {"dd": 49, "en": "Breakout", "ev": "failed"},
    "imp": {"dd": 12, "en": "BTC impulse", "ev": "partial"},
    "imps": {"dd": 12, "en": "Active spot · BTC impulse", "ev": "partial"},
    "exp": {"dd": None, "en": "Swing", "ev": "in_sample"},
    "lst": {"dd": 30, "en": "Listing short", "ev": "in_sample"},
    "own": {"dd": 23, "en": "CRYPTO NOVA signals", "ev": "partial"},
    "lsn": {"dd": 25, "en": "Neutral basket", "ev": "in_sample"},
    "lstx": {"dd": 56, "en": "Turbo · listing short ×4", "ev": "in_sample", "mirror_of": "lst"},
    "btcx": {"dd": 69, "en": "Turbo · BTC ×2 in uptrend", "ev": "in_sample", "holding": True},
    "swp": {"dd": None, "en": "False breakdown · long", "ev": "in_sample"},     # 22.10, paper only (cnq/agents/sweep_desk.py)
}
EVIDENCE = {
    "oos": ("есть честная проверка вслепую", "true out-of-sample check"),
    "partial": ("проверка вслепую частичная: живые параметры подобраны на всей истории",
                "partial out-of-sample: live parameters were chosen on the full history"),
    "in_sample": ("только на той же истории (in-sample)", "in-sample only"),
    "failed": ("по текущему правилу лаборатории НЕ проходит", "fails the lab's current pass rule"),
    "none": ("эксперимент без доказательства на истории", "experiment without historical evidence"),
}
CAVEATS = {
    "cons": ("Единственная честная проверка B+E (2025–26 вслепую) — около +9% в год, не +19%; F и D подобраны на всей истории. "
             "До сброса 17.10 правила R/C потеряли −$299/−$324 (в архиве).",
             "The only true hold-out of B+E (2025–26) shows about +9%/yr; F and D were tuned on the full history. "
             "Before the 17.10 reset rules R/C lost −$299/−$324 (archived)."),
    "brk": ("Стол запущен, хотя правило T не проходит текущую проверку лаборатории (счёт 2025 −6.8%).",
            "The desk runs although rule T fails the lab's current check (2025 account −6.8%)."),
    "imp": ("Единственный прошедший из 4 200 вариантов; без 3 лучших дней проверки в минусе; порог 2.5% в сохранённой сетке нет.",
            "The only pass out of 4,200 variants; negative without the 3 best test days; the live 2.5% threshold is not in the stored grid."),
    "lst": ("Край найден на листингах Binance; на MEXC −2.1% на сделку; листинги BingX не проверялись.",
            "Edge found on Binance listings; MEXC −2.1%/trade; BingX listings never tested."),
    "own": ("Первые сделки открыты версией правил без фильтра объёма; пороги выбраны на всей истории 2022–26; фандинг не учтён.",
            "The first trades were opened by the rules before the volume filter; thresholds chosen on all of 2022–26; no funding."),
    "lsn": ("Знаки анкеты качества выбраны на той же истории; итог сильно зависит от дня ребаланса.",
            "The quality-card signs were chosen on the same history; the result depends on the rebalance day."),
    "lab": ("Эксперимент: правила L/W/X/Y/J на истории не доказаны (W — по новостям LLM, проверить на истории нельзя).",
            "Experiment: rules L/W/X/Y/J are not proven on history (W follows LLM news and cannot be backtested)."),
    "exp": ("S на истории ≈ 0 в портфеле; у G нет отдельного исследования.",
            "S is ≈ 0 at portfolio level on history; G has no dedicated study."),
    "btcx": ("Держит BTC ×2, сделок почти не бывает: ворота по сделкам к нему почти не применимы — решает владелец по кривой счёта.",
             "Holds BTC ×2 and almost never trades: trade-count gates hardly apply — the owner judges it by the equity curve."),
    "lstx": ("Не самостоятельная стратегия: зеркало «Шорта листингов» ×4, стадия следует за ним.",
             "Not an independent strategy: the listing short ×4; its stage follows `lst`."),
}
CAVEATS["swp"] = ("Только демо. Край на грани (t по неделям 1.82), на одном BTC ноль; 10 альтов выбраны по сегодняшней ликвидности.",
                  "Paper only. Borderline edge (weekly t 1.82), zero on BTC alone; the 10 alts were chosen by today's liquidity.")
CAVEATS["bal"] = CAVEATS["aggr"] = CAVEATS["cons"]
CAVEATS["imps"] = CAVEATS["imp"]

# ──────────────── исследования, которые прошли проверку в docs/, но не работают ни в одном демо-столе (ведётся вручную) ────────────────
# Разбирать markdown автоматически ненадёжно: список короткий и правится руками при каждом новом исследовании.
DOC_CANDIDATES = [
    {"id": "doc:fx_gap_A", "doc": "docs/research-fx-2026-10-13.md",
     "name_ru": "Гэп FX пятница→понедельник, откат к закрытию пятницы (класс A)", "name_en": "FX weekend gap fade to Friday's close (class A)",
     "oos": True, "holdout_end": "2022-12-31", "trades": 2547, "venue_ok": False, "refuted_by": None,
     "metric_ru": "вне выборки 2019–22: +0.37R на сделку, 74% в плюсе", "metric_en": "out-of-sample 2019–22: +0.37R/trade, 74% wins",
     "running_ru": "только Telegram-сигналы по воскресеньям, без демо-счёта", "running_en": "Telegram signals on Sundays only, no demo account"},
    {"id": "doc:unlocks_big_short", "doc": "docs/research/unlocks.md",
     "name_ru": "Шорт перед крупным разлоком (≥ 20% монет) с хеджем BTC", "name_en": "Short before a large unlock (≥ 20% of supply), BTC-hedged",
     "oos": False, "holdout_end": None, "trades": 34, "venue_ok": True, "refuted_by": None,
     "metric_ru": "+14.8% на сделку (n 34), 2026: +2.8% (n 9) — угасает", "metric_en": "+14.8%/trade (n 34); 2026 +2.8% (n 9) — fading",
     "running_ru": "только как фильтр покупок в «Идеях спота»", "running_en": "only as a buy filter in the spot ideas"},
    {"id": "doc:b_extra_alts", "doc": "docs/research/universe.md",
     "name_ru": "Откуп каскада (B) ещё на 18 альткоинах", "name_en": "Cascade buyback (B) on 18 more alts",
     "oos": True, "holdout_end": "2026-10-16", "trades": None, "venue_ok": True,
     "refuted_by": "docs/research/alt100.md: при честном выборе монет на 01.01.2025 B ничего не добавляет; выключено 17.10",
     "metric_ru": "вслепую 2025–26: +12.5% в год против +8.9% без них", "metric_en": "blind 2025–26: +12.5%/yr vs +8.9% without",
     "running_ru": "выключено 17.10", "running_en": "switched off on 17.10"},
]

NOT_COVERED_RU = [
    "Поправка на множественные проверки сделана только для autolab (порог везения по числу перебранных вариантов и Бонферрони); "
    "для правил столов (D — 439 вариантов, K — 4 200, сетки E/F) эталон берётся как есть — это верхняя граница.",
    "Столы GitHub-конвейера (спот-портфели, фандинг, базис, доходность, мемы, киты, распределитель капитала) пока не в конвейере.",
    "Стадии считаются по столу целиком, а не по отдельному правилу внутри стола (например, Y в «Тесте новых идей»).",
    "Сделки демо-зеркала Bybit (реальные демо-исполнения) в оценку не входят: только бумажный учёт стола.",
    "Фандинг в столах автопилота и «Сигналах» не учтён, проскальзывание в обвалы занижено — живая средняя на реальном счёте будет хуже.",
]
NOT_COVERED_EN = [
    "Multiple-testing correction exists only for autolab (luck hurdle by the number of variants tried, and Bonferroni); for desk rules "
    "(D — 439 variants, K — 4,200, E/F grids) the reference mean is used as is — it is an upper bound.",
    "Desks run by the GitHub pipeline (spot portfolios, funding, basis, yield, memes, whales, the capital allocator) are not covered yet.",
    "Stages are per desk, not per rule inside a desk (e.g. Y in the new-idea test desk).",
    "Bybit demo mirror fills are not used: only the desk's own paper accounting.",
    "Funding is not charged in the autopilot and signals desks and crash slippage is understated — a real account would do worse.",
]


# ─────────────────────────────────────────────────────────────── statistics ───────────────────────────────────────────────────────────────
def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _betacf(a: float, b: float, x: float) -> float:
    """continued fraction of the incomplete beta function (modified Lentz)"""
    tiny, qab, qap, qam = 1e-300, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-12:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """regularized incomplete beta I_x(a, b)"""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    ln = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    front = math.exp(ln)
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def t_cdf(t: float, df: float) -> float:
    """P(T ≤ t) for Student's t with df degrees of freedom"""
    if not _finite(t):
        return 0.0 if t < 0 else 1.0
    p = 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))
    return p if t < 0 else 1.0 - p


def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def p_worse(diffs: list[float]) -> float | None:
    """one-sided one-sample t-test of «live is worse than its reference»: diffs = live trade % − reference %.
    → p (small = live significantly worse), None when fewer than 2 values."""
    xs = [x for x in diffs if _finite(x)]
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    if sd < 1e-12:
        return 0.0 if m < 0 else 1.0
    return t_cdf(m / (sd / math.sqrt(n)), n - 1)


def _max_dd(values) -> float:
    """max drawdown in % of the running peak (values: an iterable of equity numbers, oldest first)"""
    peak, dd = None, 0.0
    for v in values:
        if not _finite(v) or v <= 0:
            continue
        peak = v if peak is None or v > peak else peak
        dd = max(dd, (peak - v) / peak)
    return dd * 100


def week_of(t: int) -> int:
    return (int(t) - MONDAY0) // WEEK


def losing_week_streak(trades: list[dict], start: int, now: int) -> int:
    """complete UTC weeks (Monday start) in a row, newest first, whose closed trades summed below 0. A week with no closed trade ends
    the streak. The running week is not counted."""
    pnl, cnt = {}, {}
    for t in trades:
        w = week_of(t["closed"])
        pnl[w] = pnl.get(w, 0.0) + t["net_usd"]
        cnt[w] = cnt.get(w, 0) + 1
    streak, w = 0, week_of(now) - 1
    while w >= week_of(start):
        if cnt.get(w) and pnl[w] < 0:
            streak += 1
            w -= 1
        else:
            break
    return streak


def stats(trades: list[dict], desk: str, start: int, now: int, dd_pct: float | None) -> dict:
    """the numbers the gates read; trades = [{id, rule, closed, net_usd, pct}] in the evaluation window, oldest first"""
    n = len(trades)
    pcts = [t["pct"] for t in trades if _finite(t.get("pct"))]
    wins = [t["net_usd"] for t in trades if t["net_usd"] > 0]
    losses = [-t["net_usd"] for t in trades if t["net_usd"] < 0]
    pf = None
    if losses and sum(losses) > 0:
        pf = sum(wins) / sum(losses)
    elif wins:
        pf = 99.0                                                      # no losing trade yet (shown as ≥ 99)
    last = [t["pct"] for t in trades[-RETIRE_LAST_N:] if _finite(t.get("pct"))]
    diffs, ref_vals, missing, by_rule = [], [], set(), {}
    for t in trades:
        r = rule_ref(desk, t["rule"])
        br = by_rule.setdefault(t["rule"] or "?", {"n": 0, "sum": 0.0, "wins": 0, "ref_pct": r[0] if r else None})
        br["n"] += 1
        br["wins"] += 1 if t["net_usd"] > 0 else 0
        if _finite(t.get("pct")):
            br["sum"] += t["pct"]
            if r:
                diffs.append(t["pct"] - r[0])
                ref_vals.append(r[0])
            else:
                missing.add(t["rule"] or "?")
    for v in by_rule.values():
        v["mean_pct"] = round(v.pop("sum") / v["n"], 3) if v["n"] else None
    return {"n": n, "days": round(max(0.0, (now - start) / DAY), 1),
            "mean_pct": round(sum(pcts) / len(pcts), 3) if pcts else None,
            "winrate_pct": round(len(wins) / n * 100, 1) if n else None,
            "pf": round(pf, 2) if pf is not None else None,
            "dd_pct": round(dd_pct, 2) if _finite(dd_pct) else None,
            "last_mean_pct": round(sum(last) / len(last), 3) if last else None, "last_n": len(last),
            "losing_weeks": losing_week_streak(trades, start, now),
            "ref_mean_pct": round(sum(ref_vals) / len(ref_vals), 3) if ref_vals else None, "ref_n": len(diffs),
            "ref_missing": sorted(missing), "p_worse": None if len(diffs) < 2 else round(p_worse(diffs), 4),
            "by_rule": by_rule}


def rule_ref(desk: str, rule: str | None):
    return DESK_RULE_REF.get((desk, rule)) or RULE_REF.get(rule or "")


def expected_dd(desk: str) -> float:
    v = (DESKS.get(desk) or {}).get("dd")
    return float(v) if _finite(v) else DD_FALLBACK_EXPECTED_PCT


def _f(x, nd=2, sign=True) -> str:
    if not _finite(x):
        return "—"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


# ──────────────────────────────────────────────────────────────── the decision ────────────────────────────────────────────────────────────────
def decide(desk: str, s: dict, prev: str | None) -> dict:
    """→ {stage, paused, reason_ru, reason_en, gate} from the window statistics `s` (see stats()) and the previous stage.
    RETIRED is sticky (only the owner's reinstate clears it). READY is not: it is re-earned on every run."""
    if prev == "retired":
        return {"stage": "retired", "paused": True, "gate": "owner",
                "reason_ru": "снят; вернуть может только владелец: python -m cnq.cli conveyor reinstate " + desk + " --reason …",
                "reason_en": "retired; only the owner can bring it back: python -m cnq.cli conveyor reinstate " + desk + " --reason …"}
    n = s["n"]
    exp_dd = expected_dd(desk)
    # ---- automatic retirement (only from the minimum sample on) ----
    if n >= RETIRE_MIN_TRADES:
        why_ru, why_en = [], []
        lm, dd = s.get("last_mean_pct"), s.get("dd_pct")
        if (s.get("last_n") or 0) >= RETIRE_LAST_N and _finite(lm) and lm < RETIRE_LAST_MEAN_PCT and _finite(dd) and dd > RETIRE_DD_MULT * exp_dd:
            why_ru.append(f"последние {RETIRE_LAST_N} сделок в среднем {_f(lm)}% (< {RETIRE_LAST_MEAN_PCT}%) и просадка {dd:.1f}% "
                          f"глубже {RETIRE_DD_MULT}× ожидаемой ({RETIRE_DD_MULT * exp_dd:.1f}%)")
            why_en.append(f"the last {RETIRE_LAST_N} trades average {_f(lm)}% (< {RETIRE_LAST_MEAN_PCT}%) and the drawdown {dd:.1f}% "
                          f"is deeper than {RETIRE_DD_MULT}× the expected ({RETIRE_DD_MULT * exp_dd:.1f}%)")
        if s.get("losing_weeks", 0) >= RETIRE_LOSING_WEEKS:
            why_ru.append(f"{s['losing_weeks']} убыточных недели подряд")
            why_en.append(f"{s['losing_weeks']} losing weeks in a row")
        p = s.get("p_worse")
        if p is not None and s.get("ref_n", 0) >= RETIRE_MIN_TRADES and p < RETIRE_P_WORSE:
            why_ru.append(f"живая средняя {_f(s['mean_pct'])}% значимо хуже истории {_f(s['ref_mean_pct'])}% (p = {p:.4f} < {RETIRE_P_WORSE})")
            why_en.append(f"live mean {_f(s['mean_pct'])}% is significantly worse than history {_f(s['ref_mean_pct'])}% (p = {p:.4f} < {RETIRE_P_WORSE})")
        if why_ru:
            return {"stage": "retired", "paused": True, "gate": "retired",
                    "reason_ru": "снят автоматически: " + "; ".join(why_ru) + ". История сохранена; вернуть — решение владельца.",
                    "reason_en": "retired automatically: " + "; ".join(why_en) + ". History kept; bringing it back is the owner's decision."}
    # ---- PAPER → READY: the first gate not met is what the desk waits on ----
    dd_lim = min(READY_MAX_DD_PCT, DD_VS_BACKTEST * exp_dd) if (DESKS.get(desk) or {}).get("dd") else READY_MAX_DD_PCT
    checks = [
        ("trades", n >= READY_MIN_TRADES, f"мало сделок: {n} из {READY_MIN_TRADES}", f"too few trades: {n} of {READY_MIN_TRADES}"),
        ("days", s["days"] >= READY_MIN_DAYS, f"мало дней: {s['days']:.0f} из {READY_MIN_DAYS}", f"too few days live: {s['days']:.0f} of {READY_MIN_DAYS}"),
        ("mean", _finite(s.get("mean_pct")) and s["mean_pct"] >= READY_MIN_MEAN_PCT,
         f"средняя сделка {_f(s.get('mean_pct'))}% ниже {READY_MIN_MEAN_PCT:+.2f}%", f"mean trade {_f(s.get('mean_pct'))}% below {READY_MIN_MEAN_PCT:+.2f}%"),
        ("pf", _finite(s.get("pf")) and s["pf"] >= READY_MIN_PF,
         f"профит-фактор {_f(s.get('pf'), sign=False)} ниже {READY_MIN_PF}", f"profit factor {_f(s.get('pf'), sign=False)} below {READY_MIN_PF}"),
        ("dd", _finite(s.get("dd_pct")) and s["dd_pct"] <= dd_lim,
         f"просадка {_f(s.get('dd_pct'), 1, False)}% глубже предела {dd_lim:.1f}%", f"drawdown {_f(s.get('dd_pct'), 1, False)}% deeper than the {dd_lim:.1f}% limit"),
        ("reference", not s.get("ref_missing") and s.get("ref_n", 0) >= READY_MIN_TRADES,
         "нет эталона на истории для правил: " + (", ".join(s.get("ref_missing") or []) or "—") + " — не с чем сравнить",
         "no historical reference for rules: " + (", ".join(s.get("ref_missing") or []) or "—") + " — nothing to compare with"),
        ("ttest", s.get("p_worse") is not None and s["p_worse"] >= READY_P_WORSE,
         f"живая средняя хуже истории {_f(s.get('ref_mean_pct'))}%: p = {_f(s.get('p_worse'), 3, False)} < {READY_P_WORSE}",
         f"live mean worse than history {_f(s.get('ref_mean_pct'))}%: p = {_f(s.get('p_worse'), 3, False)} < {READY_P_WORSE}"),
    ]
    for code, ok, ru, en in checks:
        if not ok:
            return {"stage": "paper", "paused": False, "gate": code, "reason_ru": ru, "reason_en": en}
    return {"stage": "ready", "paused": False, "gate": "owner",
            "reason_ru": (f"все ворота пройдены: {n} сделок за {s['days']:.0f} дн., средняя {_f(s['mean_pct'])}%, PF {s['pf']:.2f}, "
                          f"просадка {s['dd_pct']:.1f}%, p = {s['p_worse']:.3f}. Реальные деньги — только решение владельца."),
            "reason_en": (f"all gates passed: {n} trades in {s['days']:.0f} days, mean {_f(s['mean_pct'])}%, PF {s['pf']:.2f}, "
                          f"drawdown {s['dd_pct']:.1f}%, p = {s['p_worse']:.3f}. Real money is only the owner's decision.")}


# ─────────────────────────────────────────────────────────────── LAB candidates ───────────────────────────────────────────────────────────────
def _years_between(a: str | None, now: int) -> float | None:
    try:
        d = dt.datetime.strptime(str(a)[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        return (now / 1000 - d.timestamp()) / (365.25 * 86400)
    except (TypeError, ValueError):
        return None


def lab_gate(c: dict, now: int) -> tuple[bool, str, str, str]:
    """LAB → PAPER recommendation for one candidate → (ok, gate code, ru, en). Order = what it waits on first."""
    if c.get("refuted_by"):
        return False, "refuted", "опровергнуто: " + c["refuted_by"], "refuted: " + c["refuted_by"]
    if not c.get("oos"):
        return False, "oos", "нет проверки вне выборки", "no out-of-sample check"
    if c.get("source") == "autolab":
        if not c.get("reliable"):
            return False, "luck", f"Sharpe {c['sharpe']} ниже порога везения {c['hurdle']} (N = {c['n_tested']})", \
                f"Sharpe {c['sharpe']} below the luck hurdle {c['hurdle']} (N = {c['n_tested']})"
        if not c.get("deflated_ok"):
            return False, "deflated", (f"с поправкой на все {c['n_tried']} перебранных вариантов порог {c['hurdle_tried']} выше Sharpe {c['sharpe']}"),\
                (f"against all {c['n_tried']} variants tried the hurdle {c['hurdle_tried']} is above Sharpe {c['sharpe']}")
        if (c.get("oos_years") or 0) < LAB_MIN_OOS_YEARS:
            return False, "sample", f"мало лет проверки «вперёд»: {c.get('oos_years')} из {LAB_MIN_OOS_YEARS}", \
                f"too few walk-forward years: {c.get('oos_years')} of {LAB_MIN_OOS_YEARS}"
    else:
        if (c.get("trades") or 0) < LAB_MIN_TRADES:
            return False, "sample", f"мало сделок: {c.get('trades') or 'неизвестно'} из {LAB_MIN_TRADES}", \
                f"too few trades: {c.get('trades') or 'unknown'} of {LAB_MIN_TRADES}"
    age = _years_between(c.get("holdout_end"), now)
    if age is None or age > LAB_MAX_AGE_YEARS:
        return False, "stale", f"проверка кончается {c.get('holdout_end') or '—'}: старше {LAB_MAX_AGE_YEARS} лет", \
            f"the check ends {c.get('holdout_end') or '—'}: older than {LAB_MAX_AGE_YEARS} years"
    if not c.get("venue_ok"):
        return False, "venue", "рынок не торгуется нашим движком (только крипто-фьючерсы)", "market not traded by our engine (crypto perps only)"
    return True, "owner", "можно ставить в демо — решение владельца; сам конвейер стол не запускает", \
        "may go to a demo desk — the owner's decision; the conveyor never starts a desk itself"


def autolab_candidates(doc: dict | None, now: int) -> list[dict]:
    out = []
    if not isinstance(doc, dict):
        return out
    rows = [r for r in doc.get("rows") or [] if isinstance(r, dict) and r.get("passes")]
    n_tried = int(doc.get("tried") or 0) or len(doc.get("rows") or []) or 1
    n_tested = int(doc.get("tested") or 0) or len(doc.get("rows") or []) or 1
    for r in rows:
        try:
            sh = float(r.get("sharpe"))
            yrs = float(len(r["years"]) if isinstance(r.get("years"), dict) and r["years"] else (r.get("n_years") or 0))
            if not math.isfinite(sh) or yrs <= 0:
                continue
            hurdle_tried = math.sqrt(2 * math.log(max(n_tried, 2))) / math.sqrt(yrs)
            p_one = norm_sf(sh * math.sqrt(yrs))                 # annual Sharpe × √years ≈ t-statistic of the mean
            c = {"id": f"autolab:{r.get('market')}·{r.get('family')}·{r.get('mode')}", "source": "autolab", "doc": "web/autolab.json",
                 "name_ru": f"{r.get('market_ru') or r.get('market')} · {r.get('family_ru') or r.get('family')} · {r.get('mode_ru') or r.get('mode')}",
                 "name_en": f"{r.get('market')} · {r.get('family')} · {r.get('mode')}",
                 "market_class": r.get("class"), "param_now": r.get("param_now"), "sharpe": round(sh, 2), "cagr_pct": r.get("cagr"),
                 "dd_pct": r.get("dd"), "oos": True, "oos_years": int(yrs), "holdout_end": r.get("to"), "reliable": bool(r.get("reliable")),
                 "hurdle": r.get("hurdle"), "n_tested": n_tested, "n_tried": n_tried, "hurdle_tried": round(hurdle_tried, 2),
                 "deflated_ok": sh >= hurdle_tried, "p_bonferroni": round(min(1.0, p_one * n_tried), 4),
                 "bonferroni_ok": p_one * n_tried < BONF_ALPHA, "venue_ok": r.get("class") == "crypto", "refuted_by": None,
                 "metric_ru": f"Sharpe {sh:.2f}, {r.get('cagr')}% в год, просадка {r.get('dd')}%, {int(yrs)} лет вперёд",
                 "metric_en": f"Sharpe {sh:.2f}, {r.get('cagr')}%/yr, drawdown {r.get('dd')}%, {int(yrs)} walk-forward years",
                 "running_ru": "не работает ни в одном столе", "running_en": "runs in no desk"}
            if r.get("class") == "crypto":
                c["caveat_ru"] = "крипта autolab — только живые сегодня монеты (ошибка выжившего)"
                c["caveat_en"] = "autolab crypto uses today's surviving coins only (survivorship)"
            out.append(c)
        except (TypeError, ValueError, KeyError):
            continue
    return out


def lab_registry(autolab: dict | None, now: int) -> list[dict]:
    out = []
    for c in autolab_candidates(autolab, now) + [dict(x, source="docs") for x in DOC_CANDIDATES]:
        ok, gate, ru, en = lab_gate(c, now)
        c.update({"stage": "lab", "recommend_paper": ok, "gate": gate, "reason_ru": ru, "reason_en": en})
        out.append(c)
    return sorted(out, key=lambda c: (not c["recommend_paper"], c["source"] != "docs", -(c.get("sharpe") or 0)))


# ─────────────────────────────────────────────────────────────── desk records ───────────────────────────────────────────────────────────────
def _paper_profiles():
    try:
        from cnq.agents import paper_desk as PD
        return PD.PROFILES, PD.FEE
    except Exception:  # noqa: BLE001
        return {}, 0.0005


def _row(tid, rule, closed, entry, qty, net):
    if not (_finite(closed) and _finite(net)):
        return None
    notional = entry * qty if _finite(entry) and _finite(qty) else None
    pct = net / notional * 100 if notional and notional > 0 else None
    return {"id": str(tid), "rule": rule, "closed": int(closed), "net_usd": float(net), "pct": pct}


def db_trades(conn: sqlite3.Connection | None, desk: str) -> list[dict] | None:
    """every closed trade of the desk from the engine's database, oldest first; None when the desk has no table there"""
    if conn is None:
        return None
    profiles, fee0 = _paper_profiles()
    try:
        if desk in profiles:
            fee = profiles[desk].get("fee", fee0)
            cur = conn.execute(f"SELECT id, grade, closed, entry, qty, pnl_usd FROM {profiles[desk]['prefix']}trades "
                               "WHERE closed IS NOT NULL ORDER BY closed")
            rows = [_row(i, g, c, e, q, (p or 0) - (e or 0) * (q or 0) * fee) for i, g, c, e, q, p in cur]
        elif desk == "own":
            rows = [_row(*r) for r in conn.execute("SELECT id, rule, closed, entry, qty, pnl_usd FROM own_trades WHERE closed IS NOT NULL ORDER BY closed")]
        elif desk == "lst":
            rows = [_row(*r) for r in conn.execute("SELECT id, 'LST', closed, entry, qty, pnl_usd FROM lst_trades WHERE closed IS NOT NULL ORDER BY closed")]
        elif desk == "lsn":
            rows = [_row(*r) for r in conn.execute("SELECT id, 'LSN', closed, entry, qty, pnl_usd FROM lsn_closed WHERE closed IS NOT NULL ORDER BY closed")]
        elif desk == "swp":      # pnl_usd is net of fees (cnq/agents/sweep_desk.py)
            rows = [_row(*r) for r in conn.execute("SELECT id, 'SWP', closed, entry, qty, pnl_usd FROM swp_trades WHERE closed IS NOT NULL ORDER BY closed")]
        else:
            return None
    except sqlite3.Error:
        return None
    return [r for r in rows if r]


def feed_trades(profile: dict, desk: str) -> list[dict]:
    """fallback: the closed trades the desk publishes in paper.json (the last 50–60 only)"""
    out = []
    for t in profile.get("closed") or []:
        if not isinstance(t, dict):
            continue
        r = _row(t.get("id"), t.get("grade"), t.get("closed"), t.get("entry"), t.get("qty"), t.get("net_usd"))
        if r and r["pct"] is None and _finite(t.get("entry")) and _finite(t.get("exit")) and t["entry"] > 0:
            sg = -1 if (t.get("side") or "").upper() == "SHORT" else 1
            r["pct"] = (sg * (t["exit"] / t["entry"] - 1) - 0.001) * 100      # no size in the row: price move − 0.1 % fees (approx.)
        if r:
            out.append(r)
    return sorted(out, key=lambda r: r["closed"])


def db_dd(conn: sqlite3.Connection | None, desk: str, start: int) -> float | None:
    """max drawdown (%, open positions included where the desk stores them) since `start`, streamed from the equity table"""
    if conn is None:
        return None
    profiles, _ = _paper_profiles()
    qs = []
    if desk in profiles:
        t = profiles[desk]["prefix"] + "equity"
        qs = [(f"SELECT COALESCE(mtm, equity) FROM {t} WHERE time >= ? ORDER BY time", (start,)),
              (f"SELECT equity FROM {t} WHERE time >= ? ORDER BY time", (start,))]
    elif desk in ("own", "lst", "lsn"):
        qs = [(f"SELECT equity FROM {desk}_equity WHERE time >= ? ORDER BY time", (start,))]
    elif desk == "swp":
        qs = [("SELECT COALESCE(mtm, equity) FROM swp_equity WHERE time >= ? ORDER BY time", (start,))]
    elif desk in ("lstx", "btcx"):
        qs = [("SELECT equity FROM turbo_hist WHERE desk = ? AND time >= ? ORDER BY time", (desk, start))]
    for sql, args in qs:
        peak, dd, n = None, 0.0, 0
        try:
            for (v,) in conn.execute(sql, args):          # streamed: a year of minute points never sits in memory at once
                if not _finite(v) or v <= 0:
                    continue
                n += 1
                peak = v if peak is None or v > peak else peak
                dd = max(dd, (peak - v) / peak)
        except sqlite3.Error:
            continue
        return dd * 100 if n else None
    return None


def feed_dd(profile: dict, start: int, since: int) -> float | None:
    vals = [e.get("equity") for e in profile.get("equity_history") or [] if isinstance(e, dict) and _finite(e.get("time")) and e["time"] >= start]
    dd = _max_dd(vals) if vals else None
    st = (profile.get("stats") or {}).get("max_dd_pct")
    if start <= since and _finite(st):                      # the desk's own figure covers its whole life (= this window)
        dd = max(dd or 0.0, float(st))
    return dd


# ─────────────────────────────────────────────────────────────── storage ───────────────────────────────────────────────────────────────
def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS conveyor_stage(desk TEXT PRIMARY KEY, stage TEXT, since INTEGER, reason_ru TEXT, reason_en TEXT);
      CREATE TABLE IF NOT EXISTS conveyor_log(id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER, desk TEXT, from_stage TEXT, to_stage TEXT,
        reason_ru TEXT, reason_en TEXT, by TEXT, note TEXT);
      CREATE TABLE IF NOT EXISTS conveyor_meta(k TEXT PRIMARY KEY, v TEXT);""")
    conn.commit()


def _meta(conn, k, d=None):
    r = conn.execute("SELECT v FROM conveyor_meta WHERE k=?", (k,)).fetchone()
    try:
        return json.loads(r[0]) if r else d
    except (TypeError, ValueError):
        return d


def _set_meta(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO conveyor_meta VALUES(?, ?)", (k, json.dumps(v, ensure_ascii=False)))


def _stages(conn) -> dict:
    return {r[0]: {"stage": r[1], "since": r[2], "reason_ru": r[3], "reason_en": r[4]}
            for r in conn.execute("SELECT desk, stage, since, reason_ru, reason_en FROM conveyor_stage")}


def _log(conn, t, desk, a, b, ru, en, by="conveyor", note=None):
    conn.execute("INSERT INTO conveyor_log(t, desk, from_stage, to_stage, reason_ru, reason_en, by, note) VALUES(?,?,?,?,?,?,?,?)",
                 (t, desk, a, b, ru, en, by, note))


def _clean(o):
    """NaN / ±inf → None everywhere (inputs come from other feeds; JSON must stay strict)"""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _write_json(path: str, doc: dict) -> None:
    """atomic: a reader never sees half a file"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_clean(doc), f, ensure_ascii=False, allow_nan=False)
    os.replace(tmp, path)


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def contract(desks: dict, now: int) -> dict:
    """the exact shape the risk gate reads"""
    return {"updated_at": int(now), "desks": {k: {"stage": v["stage"] if v.get("stage") in STAGES else "paper", "paused": bool(v.get("paused")),
                                                  "reason_ru": str(v.get("reason_ru") or ""), "reason_en": str(v.get("reason_en") or "")}
                                              for k, v in desks.items()}}


# ─────────────────────────────────────────────────────────────── one run ───────────────────────────────────────────────────────────────
def evaluate(conn: sqlite3.Connection, paper: dict | None, autolab: dict | None, now: int) -> dict:
    """read every record, decide every stage, persist changes → {desks: [...], lab: [...], errors: [...]}"""
    ensure(conn)
    errors = []
    profiles = (paper or {}).get("profiles") if isinstance(paper, dict) else None
    if not isinstance(profiles, dict) or not profiles:
        errors.append("web/paper.json: нет профилей столов — стадии не пересчитаны")
        profiles = {}
    prev = _stages(conn)
    keys = list(profiles) + [k for k in prev if k not in profiles]
    out, results = [], {}
    order = sorted(keys, key=lambda k: 1 if (DESKS.get(k) or {}).get("mirror_of") else 0)      # mirrors after their base
    pd_profiles, _ = _paper_profiles()
    for k in order:
        p = profiles.get(k) if isinstance(profiles.get(k), dict) else None
        meta = DESKS.get(k) or {}
        pv = (prev.get(k) or {}).get("stage")
        row = {"key": k, "name_ru": (p or {}).get("name") or k, "name_en": meta.get("en") or (p or {}).get("name") or k,
               "evidence": meta.get("ev", "in_sample"), "expected_dd_pct": meta.get("dd")}
        ev = EVIDENCE.get(row["evidence"], EVIDENCE["in_sample"])
        row["evidence_ru"], row["evidence_en"] = ev
        cv = CAVEATS.get(k)
        if cv:
            row["caveat_ru"], row["caveat_en"] = cv
        try:
            if meta.get("mirror_of") and meta["mirror_of"] in results:
                base = results[meta["mirror_of"]]
                # a mirror is never judged on its own: its stage (and pause) is the base desk's; reinstating the base brings it back
                d = {"stage": base["stage"], "paused": base["paused"], "gate": base["gate"],
                     "reason_ru": f"зеркало «{base['name_ru']}» ×4: " + base["reason_ru"], "reason_en": f"mirror of `{meta['mirror_of']}` ×4: " + base["reason_en"]}
                row.update({"mirror_of": meta["mirror_of"], **{x: base.get(x) for x in ("trades", "days", "mean_pct", "pf", "winrate_pct",
                                                                                          "ref_mean_pct", "p_worse", "last_mean_pct", "losing_weeks")}})
            elif p is None:
                old = prev.get(k) or {}
                d = {"stage": old.get("stage") or "paper", "paused": old.get("stage") == "retired", "gate": "no_data",
                     "reason_ru": "стола нет в web/paper.json — стадия не пересчитана", "reason_en": "desk missing from web/paper.json — stage not recomputed"}
            else:
                since = int(p.get("since") or p.get("asof") or now)
                rein = _meta(conn, f"reinstated:{k}") or {}
                start = max(since, int(rein.get("t") or 0))
                active = set(pd_profiles[k]["classes"]) if k in pd_profiles else None
                dbt, fdt = db_trades(conn, k), feed_trades(p, k)
                src, all_tr = ("db", dbt) if dbt is not None and len(dbt) >= len(fdt) else ("paper.json", fdt)
                win = [t for t in all_tr if t["closed"] >= start]
                dropped = [t for t in win if active is not None and t["rule"] not in active]
                win = [t for t in win if t not in dropped]
                dds = [x for x in (db_dd(conn, k, start), feed_dd(p, start, since)) if _finite(x)]
                dd = max(dds) if dds else None                  # the deeper of the two views of the same account (conservative)
                s = stats(win, k, start, now, dd)
                d = decide(k, s, pv)
                row.update({"source": src, "since": since, "window_start": start, "trades": s["n"], "trades_total": len(all_tr),
                            "trades_removed_rules": len(dropped), "days": s["days"], "mean_pct": s["mean_pct"], "pf": s["pf"],
                            "winrate_pct": s["winrate_pct"], "max_dd_pct": s["dd_pct"], "ref_mean_pct": s["ref_mean_pct"],
                            "p_worse": s["p_worse"], "last_mean_pct": s["last_mean_pct"], "losing_weeks": s["losing_weeks"],
                            "by_rule": s["by_rule"], "dd_limit_pct": min(READY_MAX_DD_PCT, DD_VS_BACKTEST * expected_dd(k)) if meta.get("dd") else READY_MAX_DD_PCT})
                if (p.get("reset") or {}).get("archived_pnl_usd") is not None:
                    row["archived_pnl_usd"] = p["reset"]["archived_pnl_usd"]
                if rein:
                    row["reinstated_at"] = rein.get("t")
        except Exception as e:  # noqa: BLE001 — one broken desk never stops the others; it keeps its last stage
            old = prev.get(k) or {}
            d = {"stage": old.get("stage") or "paper", "paused": old.get("stage") == "retired", "gate": "error",
                 "reason_ru": "ошибка расчёта — стадия не изменена", "reason_en": "evaluation error — stage unchanged"}
            errors.append(f"{k}: {type(e).__name__}: {str(e)[:200]}")
        row.update(d)
        results[k] = row
        if pv != d["stage"]:
            since_t = now
            conn.execute("INSERT OR REPLACE INTO conveyor_stage VALUES(?,?,?,?,?)", (k, d["stage"], since_t, d["reason_ru"], d["reason_en"]))
            _log(conn, now, k, pv, d["stage"], d["reason_ru"], d["reason_en"])
        else:
            conn.execute("UPDATE conveyor_stage SET reason_ru=?, reason_en=? WHERE desk=?", (d["reason_ru"], d["reason_en"], k))
        row["stage_since"] = (_stages(conn).get(k) or {}).get("since")
        out.append(row)
    conn.commit()
    lab = []
    try:
        lab = lab_registry(autolab, now)
    except Exception as e:  # noqa: BLE001
        errors.append(f"lab: {type(e).__name__}: {str(e)[:200]}")
    if autolab is None:
        errors.append("web/autolab.json: не прочитан — кандидаты autolab не показаны")
    rank = {k: i for i, k in enumerate(profiles)}
    out.sort(key=lambda r: rank.get(r["key"], 999))
    return {"desks": out, "lab": lab, "errors": errors}


def feed(ev: dict, conn: sqlite3.Connection, now: int) -> dict:
    desks, lab = ev["desks"], ev["lab"]
    counts = {s: 0 for s in STAGES}
    for r in desks:
        counts[r["stage"]] = counts.get(r["stage"], 0) + 1
    counts["lab"] += len(lab)
    log = [{"t": t, "desk": k, "from": a, "to": b, "reason_ru": ru, "reason_en": en, "by": by}
           for t, k, a, b, ru, en, by in conn.execute("SELECT t, desk, from_stage, to_stage, reason_ru, reason_en, by FROM conveyor_log "
                                                      "ORDER BY id DESC LIMIT ?", (LOG_KEEP,))]
    return {
        "asof": now, "version": 1,
        "about_ru": ("Конвейер стратегий: LAB (исследование) → PAPER (демо-счёт) → READY (ворота пройдены) → RETIRED (снят). "
                     "Всё — демо и бумага; перевод в реальные деньги — только решение владельца. Статистика прошлого, не обещание."),
        "about_en": ("Strategy conveyor: LAB (research) → PAPER (demo account) → READY (gates passed) → RETIRED. "
                     "Everything is demo/paper; moving to real money is only the owner's decision. Past statistics, not a promise."),
        "counts": counts,
        "gates": {
            "paper_to_ready": {"min_trades": READY_MIN_TRADES, "min_days": READY_MIN_DAYS, "min_mean_pct": READY_MIN_MEAN_PCT,
                               "min_pf": READY_MIN_PF, "max_dd_pct": READY_MAX_DD_PCT, "dd_vs_backtest": DD_VS_BACKTEST, "p_worse": READY_P_WORSE},
            "retire": {"min_trades": RETIRE_MIN_TRADES, "last_n": RETIRE_LAST_N, "last_mean_pct": RETIRE_LAST_MEAN_PCT,
                       "dd_mult": RETIRE_DD_MULT, "losing_weeks": RETIRE_LOSING_WEEKS, "p_worse": RETIRE_P_WORSE,
                       "dd_fallback_expected_pct": DD_FALLBACK_EXPECTED_PCT},
            "lab_to_paper": {"min_trades": LAB_MIN_TRADES, "min_oos_years": LAB_MIN_OOS_YEARS, "max_age_years": LAB_MAX_AGE_YEARS,
                             "bonferroni_alpha": BONF_ALPHA, "automatic": False},
            "ru": [f"PAPER → READY: от {READY_MIN_TRADES} закрытых сделок и {READY_MIN_DAYS} дней; средняя сделка после комиссий от "
                   f"{READY_MIN_MEAN_PCT:+.2f}%; профит-фактор от {READY_MIN_PF}; просадка не глубже {READY_MAX_DD_PCT:.0f}% и "
                   f"{DD_VS_BACKTEST}× исторической; живая средняя не хуже истории (t-тест, p ≥ {READY_P_WORSE}).",
                   f"RETIRE (автоматически, от {RETIRE_MIN_TRADES} сделок): последние {RETIRE_LAST_N} в среднем ниже {RETIRE_LAST_MEAN_PCT}% "
                   f"и просадка глубже {RETIRE_DD_MULT}× ожидаемой; или {RETIRE_LOSING_WEEKS} убыточные недели подряд; или живая средняя "
                   f"значимо хуже истории (p < {RETIRE_P_WORSE}). Новые входы стола блокируются, история сохраняется.",
                   "LAB → PAPER: только рекомендация (проверка вне выборки, порог везения, достаточная выборка, свежие данные, наш рынок).",
                   "READY → реальные деньги: никогда не автоматически — только решение владельца."],
            "en": [f"PAPER → READY: at least {READY_MIN_TRADES} closed trades and {READY_MIN_DAYS} days; mean trade after fees at least "
                   f"{READY_MIN_MEAN_PCT:+.2f}%; profit factor at least {READY_MIN_PF}; drawdown within {READY_MAX_DD_PCT:.0f}% and "
                   f"{DD_VS_BACKTEST}× its history; live mean not worse than history (t-test, p ≥ {READY_P_WORSE}).",
                   f"RETIRE (automatic, from {RETIRE_MIN_TRADES} trades): the last {RETIRE_LAST_N} average below {RETIRE_LAST_MEAN_PCT}% and "
                   f"the drawdown deeper than {RETIRE_DD_MULT}× expected; or {RETIRE_LOSING_WEEKS} losing weeks in a row; or the live mean "
                   f"significantly worse than history (p < {RETIRE_P_WORSE}). New entries of the desk are blocked; history is kept.",
                   "LAB → PAPER: a recommendation only (out-of-sample check, luck hurdle, enough sample, fresh data, our market).",
                   "READY → real money: never automatic — only the owner's decision."]},
        "desks": desks, "lab": lab, "recommend_paper": [c["id"] for c in lab if c.get("recommend_paper")],
        "log": log, "not_covered_ru": NOT_COVERED_RU, "not_covered_en": NOT_COVERED_EN,
        "status": {"ok": not ev["errors"], "errors": ev["errors"]},
    }


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, state_path: str = STATE_PATH, force: bool = False) -> dict:
    """one engine step. Never raises on bad inputs: what cannot be read is reported in status.errors, stages stay as they were."""
    now = int(now or time.time() * 1000)
    ensure(conn)
    last = _meta(conn, "last_run")
    if not force and isinstance(last, (int, float)) and 0 <= now - last < RUN_EVERY_MS:
        return {"skipped": True, "minutes_since_last": round((now - last) / 60_000, 1)}
    paper = _load(os.path.join(out, "paper.json"))
    autolab = _load(os.path.join(out, "autolab.json"))
    ev = evaluate(conn, paper, autolab, now)
    doc = feed(ev, conn, now)
    _write_json(state_path, contract({r["key"]: r for r in ev["desks"]}, now))
    _write_json(os.path.join(out, FEED), doc)
    _set_meta(conn, "last_run", now)
    conn.commit()
    return doc


def write_error(out: str, err: str, now: int | None = None) -> None:
    """last resort after an unexpected failure: keep the previous public feed, mark its status"""
    now = int(now or time.time() * 1000)
    path = os.path.join(out, FEED)
    doc = _load(path) or {"asof": now, "desks": [], "lab": [], "counts": {}, "log": []}
    doc["status"] = {"ok": False, "errors": [str(err)[:300]], "failed_at": now}
    try:
        _write_json(path, doc)
    except Exception:  # noqa: BLE001
        pass


def reinstate(conn: sqlite3.Connection, desk: str, reason: str, out: str = "web", now: int | None = None,
              state_path: str = STATE_PATH) -> dict:
    """the owner brings a retired desk back to PAPER. Its history stays; the gates count again only from now (a fresh minimum
    sample is needed before the conveyor may retire it again). The reason text stays private (database + server state file)."""
    now = int(now or time.time() * 1000)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("нужна причина: --reason «…»")
    ensure(conn)
    cur = _stages(conn).get(desk)
    if not cur:
        raise ValueError(f"стол «{desk}» конвейеру неизвестен")
    base = (DESKS.get(desk) or {}).get("mirror_of")
    if base:
        raise ValueError(f"стол «{desk}» — зеркало «{base}» и следует за ним: восстановите «{base}»")
    if cur["stage"] != "retired":
        raise ValueError(f"стол «{desk}» не снят (стадия {cur['stage']}) — восстанавливать нечего")
    ru = "восстановлен владельцем; ворота считаются заново с этого момента"
    en = "reinstated by the owner; the gates count again from now"
    _set_meta(conn, f"reinstated:{desk}", {"t": now, "reason": reason[:500]})
    conn.execute("INSERT OR REPLACE INTO conveyor_stage VALUES(?,?,?,?,?)", (desk, "paper", now, ru, en))
    _log(conn, now, desk, "retired", "paper", ru, en, by="owner", note=reason[:500])
    conn.commit()
    st = _load(state_path)
    if isinstance(st, dict) and isinstance(st.get("desks"), dict):       # unblock right away, before the next hourly run
        st["desks"][desk] = {"stage": "paper", "paused": False, "reason_ru": ru + ": " + reason[:200], "reason_en": en + ": " + reason[:200]}
        st["updated_at"] = now
        _write_json(state_path, contract(st["desks"], now))
    return run(conn, out, now, state_path, force=True)


def status_table(conn: sqlite3.Connection) -> list[str]:
    ensure(conn)
    return [f"{k:6} {v['stage']:8} {v['reason_ru']}" for k, v in sorted(_stages(conn).items())]
