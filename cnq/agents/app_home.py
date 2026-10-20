"""One small feed for the CRYPTO NOVA 2.0 app (19.10.2026, owner: «приложение — быстрая сводка, как Apple Watch; всё должно
работать по-настоящему»).

web/app_home.json (https://cryptonova.vip/app_home.json, on the server every minute) — everything the 2.0 home, futures and
spot tabs show, already in plain numbers so the app never recomputes the engine:
  total: earned since the demo start, split by source (futures = the average CNPTS $10 000 account — cons/bal/aggr trade the
         same signals at three sizes; spot = the CRYPTO NOVA portfolio per $10 000);
  curve: both sources over time (≤ 60 points); futures: result, today, trades, worst dip, open trades with stop/entry/now/target
         and a plain «why», last closed trades; spot: allocation groups, per-coin change since purchase, last action;
  market: bitcoin price and 24 h change, fear & greed, regime (BTC above its 200-day average = «рост»).
20.10: every open trade also explains itself (see _explain): why_title, checks [{t, v, ok}] with the numbers the desk stored at
entry, history (the rule's backtest record from docs/research + its live record), risk (stop / target / R:R / leverage / $ at risk /
holding time), work (the real scan counters behind the signal), note; futures.scan = the own desk's last daily scan.
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

# 20.10 (owner: «хотелось бы видеть более серьёзное объяснение, почему мы вошли в сделку. Мы проделываем огромную работу, и люди
# должны это видеть»): every open trade explains itself — the setup in a few words, the conditions checked at entry with THIS
# trade's numbers (the desks store them: own_desk.entry_facts, paper_desk signals()["facts"], listing_desk.entry_facts), the
# rule's record on history and live, the risk, and the scale of the scan behind it. Nothing is filled in when the data is missing.
TITLE_RU = {"P": "Откат в растущем тренде", "C": "Пробой максимума 20 дней", "E": "Откуп паники на большом объёме",
            "B": "Откуп каскада ликвидаций", "F": "Откуп сброса позиций", "D": "Откуп дисконта фьючерса",
            "K": "Альт догоняет рывок биткоина", "LST": "Шорт нового листинга"}
# The rules' record on history — numbers only from the research write-ups / backtest outputs named in `src`; a number the
# research does not give is None, never estimated.
HISTORY = {
    "P": {"n": None, "win_pct": None, "avg_pct": 3.1, "period": "2023–2026", "years_plus": "каждый год в плюсе",
          "text": "Честная проверка обоих правил «Сигналов» вместе (каждый день топ-40 монет Binance по обороту, включая снятые с "
                  "торгов): в среднем +3.1% на сделку, около +2% в месяц к счёту; отдельной статистики «отката» в отчёте нет.",
          "src": "docs/research/own_signals.md, раунды 9–10"},
    "C": {"n": 403, "win_pct": 53, "avg_pct": 6.3, "period": "2021–2026", "years_plus": "каждый год в плюсе",
          "text": "18 крупных монет Binance, вход после закрытия дня с объёмом от ×1.5: 403 сделки, 53% в плюсе, в среднем +6.3% — "
                  "без снятых с торгов монет это завышено; честная проверка обоих правил вместе даёт +3.1% на сделку.",
          "src": "docs/research/orders.md §3; own_signals.md, раунды 9–10"},
    "E": {"n": 3414, "win_pct": 60, "avg_pct": 1.26, "period": "2023–2026", "years_plus": "каждый год в плюсе",
          "text": "18 монет Binance: 3 414 сделок, 60% в плюсе, в среднем +1.26% за сутки — при пороге «падает 70% рынка»; с 16.10 "
                  "порог 40%, при 30–50% в среднем +1.07…+1.14%, тоже каждый год в плюсе.",
          "src": "docs/research/oi_flush.md (дополнение 16.10), panic_breadth.md"},
    "B": {"n": 729, "win_pct": 59, "avg_pct": 0.95, "period": "2021–2026", "years_plus": "5 из 6 лет в плюсе",
          "text": "18 монет Binance, вход сразу после свечи с длинным хвостом снизу: 729 сделок, 59% в плюсе, в среднем +0.95%, "
                  "за 2025–2026 +1.19%.",
          "src": "docs/research/orders.md §1"},
    "F": {"n": 650, "win_pct": 56, "avg_pct": 1.53, "period": "2023–2026", "years_plus": "каждый год в плюсе",
          "text": "18 монет Binance с настоящим открытым интересом: 650 сделок, 56% в плюсе, в среднем +1.53% за сутки при стопе "
                  "−6%; случайная покупка в те же годы −0.08…−0.14%.",
          "src": "docs/research/oi_flush.md"},
    "D": {"n": None, "win_pct": 55, "avg_pct": 1.77, "period": "2023–2026", "years_plus": "каждый год в плюсе",
          "text": "18 монет Binance: около 0.4 сделки в день, 55% в плюсе, в среднем +1.77% за сутки; случайный вход на тот же срок "
                  "−0.12%. Прибыль держится на редких сильных перекосах.",
          "src": "docs/research/new_signals.md"},
    "K": {"n": None, "win_pct": None, "avg_pct": None, "period": "2025–2026", "years_plus": "2025 и 2026 в плюсе",
          "text": "Проверка «Лаборатории» на топ-50 монет по обороту (с 2023): счёт из 5 позиций +34% за 2025–2026, просадка −12%; "
                  "в обвальном 2022 правило было в минусе.",
          "src": "paper_desk.PROFILES['imp'], коммит 17139d5f; scripts/lab/results/trend_wide50.json"},
    "LST": {"n": 729, "win_pct": 55, "avg_pct": 2.6, "period": "2023–2026", "years_plus": "каждый год в плюсе",
            "text": "Все 729 листингов Binance 2023–2026, включая снятые с торгов, с фандингом и комиссиями: 55% сделок в плюсе, в "
                    "среднем +2.6%; худшая сделка −43%, поэтому стоп обязателен.",
            "src": "cnq/agents/listing_desk.py (scripts/listings_study.py)"},
}
RULE_NAME_RU = {"E": "откуп паники", "B": "откуп каскада", "F": "откуп сброса позиций", "D": "откуп дисконта фьючерса",
                "K": "альт догоняет рывок BTC"}


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


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _pc(x: float, d: int = 1, sign: bool = True) -> str:
    """a fraction as a percent in the app's style: 0.0399 → «+4.0%», −0.018 → «−1.8%» (sign=False → «4.0%»)"""
    v = round(x * 100, d)
    s = f"{abs(v):.{d}f}%"
    return (("+" if v > 0 else "−" if v < 0 else "") if sign else "") + s


def _vs(a: float, b: float) -> str:
    """«на 4.0% выше» / «на 1.2% ниже» — a against b"""
    return f"на {_pc(a / b - 1, sign=False)} {'выше' if a >= b else 'ниже'}"


def _num(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _checks(grade: str, f: dict, sym: str = "") -> list[dict]:
    """the conditions the rule checked at entry, with THIS trade's numbers (only those the stored facts back)"""
    out: list[dict] = []

    def add(t, v, ok):
        out.append({"t": t, "v": v, "ok": bool(ok)})
    c = f.get("close")
    if grade in ("P", "C"):
        if grade == "P":
            s50, s5, r2, ch2 = f.get("sma50"), f.get("sma50_5"), f.get("rsi2"), f.get("chg2")
            if c and s50:
                add("Тренд", f"цена {_vs(c, s50)} средней за 50 дней", c > s50)
            if s50 and s5:
                add("Тренд растёт", f"средняя за 50 дней {_pc(s50 / s5 - 1)} за 5 дней", s50 > s5)
            if r2 is not None:
                add("Откат", (f"{_pc(ch2)} за 2 дня, " if ch2 is not None else "") + f"RSI(2) = {_num(r2)} (вход ниже 10)", r2 < 10)
        else:
            hh, s20 = f.get("hh20"), f.get("sma20")
            if c and hh:
                add("Пробой", f"закрытие {_vs(c, hh)} максимума 20 дней", c > hh)
            if c and s20:
                add("Тренд", f"цена {_vs(c, s20)} средней за 20 дней", c > s20)
        b = f.get("btc") or {}
        if b.get("close") and b.get("sma200") and b.get("c60"):
            bull = b["close"] > b["sma200"] and b["close"] > b["c60"]
            add("Рынок", f"BTC {_vs(b['close'], b['sma200'])} средней за 200 дней и {_pc(b['close'] / b['c60'] - 1)} за 60 дней — "
                         + ("режим роста" if bull else "не рост"), bull)
        if f.get("vol") is not None:
            add("Объём", f"×{f['vol']:.1f} к среднему за 30 дней (нужно от ×1.5)", f["vol"] >= 1.5)
        if f.get("rank") and f.get("of"):
            add("Ликвидность", f"№{f['rank']} из {f['of']} самых торгуемых монет за 30 дней", f["rank"] <= f["of"])
    elif grade == "E":
        if f.get("breadth") is not None and f.get("need_breadth") is not None:
            add("Распродажа", f"за сутки упали больше чем на 3%: {_pc(f['breadth'], 0, False)} монет (нужно от "
                              f"{_pc(f['need_breadth'], 0, False)})", f["breadth"] >= f["need_breadth"])
        if f.get("drop") is not None and f.get("need_drop") is not None:
            add("Свеча", f"{_pc(f['drop'])} за 15 минут (нужно {_pc(f['need_drop'])} и ниже)", f["drop"] <= f["need_drop"])
        if f.get("vol_x") is not None and f.get("need_vol") is not None:
            add("Объём", f"×{f['vol_x']:.1f} к среднему за сутки (нужно от ×{f['need_vol']:g})", f["vol_x"] >= f["need_vol"])
    elif grade == "B":
        if f.get("ret") is not None and f.get("sigma_x") is not None:
            add("Обвал свечи", f"{_pc(f['ret'])} за 15 минут — в {abs(f['sigma_x']):.1f} раза больше обычного колебания (нужно от "
                               f"{abs(f.get('need_sigma') or 3.5):g})", f["sigma_x"] <= (f.get("need_sigma") or -3.5))
        if f.get("wick") is not None:
            add("Хвост снизу", f"{_pc(f['wick'], 0, False)} свечи — падение выкупили (нужно от {_pc(f.get('need_wick') or 0.5, 0, False)})",
                f["wick"] >= (f.get("need_wick") or 0.5))
    elif grade == "F":
        if f.get("oi4") is not None and f.get("need_oi") is not None:
            add("Открытый интерес", f"{_pc(f['oi4'], 0)} за 4 часа (нужно {_pc(f['need_oi'], 0)} и ниже)", f["oi4"] <= f["need_oi"])
        if f.get("px4") is not None and f.get("need_px") is not None:
            add("Цена", f"{_pc(f['px4'])} за 4 часа (нужно {_pc(f['need_px'], 0)} и ниже)", f["px4"] <= f["need_px"])
    elif grade == "D":
        if f.get("basis") is not None and f.get("need_basis") is not None:
            add("Дисконт фьючерса", f"фьючерс {_pc(abs(f['basis']), 2, False)} {'ниже' if f['basis'] < 0 else 'выше'} спота в среднем "
                                    f"за час (нужно от {_pc(abs(f['need_basis']), 2, False)} ниже)", f["basis"] <= f["need_basis"])
    elif grade == "K":
        if f.get("btc_ret") is not None and f.get("need_btc") is not None:
            add("Рывок BTC", f"{_pc(f['btc_ret'])} за час (нужно от {_pc(f['need_btc'])})", f["btc_ret"] >= f["need_btc"])
        sc = f.get("scan") or {}
        if f.get("rank") and sc.get("coins"):
            add("Ликвидность", f"№{f['rank']} из {sc['coins']} альткоинов по обороту за сутки — места получают самые торгуемые", True)
    elif grade == "LST":
        if f.get("age_h") is not None:
            add("Новый листинг", f"фьючерс появился {_num(f['age_h'])} ч назад (вход через 4–24 ч)", 4 <= f["age_h"] <= 24)
        if f.get("move") is not None and f.get("skip_pump") is not None:
            add("Без пампа", f"{_pc(f['move'], 0)} от первой цены (пропускаем от {_pc(f['skip_pump'], 0)})", f["move"] < f["skip_pump"])
        if f.get("qvol") is not None and f.get("min_qvol") is not None:
            add("Оборот", f"${f['qvol'] / 1e6:.1f} млн с листинга (нужно от ${f['min_qvol'] / 1e6:g} млн)", f["qvol"] >= f["min_qvol"])
        if out:
            try:
                from .listing_desk import not_crypto
                add("Токен", "настоящая монета, не синтетическая пара (валюта, товар, акция)", not not_crypto(f"{sym}-USDT"))
            except Exception:  # noqa: BLE001
                pass
    if grade in ("E", "B", "F", "D") and out and (f.get("scan") or {}).get("coins"):
        add("Монета", f"одна из {f['scan']['coins']} основных монет, на которых правило проверено на истории", True)
    return out


def _hold_ru(grade: str) -> str | None:
    try:
        from . import listing_desk, own_desk, paper_desk as pd
    except Exception:  # noqa: BLE001
        return None
    h = {"E": pd.HOLD_E_H, "F": pd.HOLD_F_H, "D": pd.HOLD_D_H, "K": pd.HOLD_K_H}
    if grade == "P":
        return f"до {own_desk.HOLD['P']} дней; раньше — когда закрытие вернётся выше средней за 5 дней"
    if grade == "C":
        return f"до {own_desk.HOLD['C']} дней; раньше — если закрытие уйдёт на 3 ATR ниже лучшего"
    if grade in h:
        return f"{h[grade]} {_plural(h[grade], 'час', 'часа', 'часов')} — выход по времени, цель только ориентир"
    if grade == "B":
        return f"до {pd.HOLD_B_H} часов или до цели +1R"
    if grade == "LST":
        return f"{listing_desk.HOLD_D} дней — выход по времени"
    return None


def _risk(o: dict, grade: str, entry, stop, tgt) -> dict | None:
    if not entry or not stop:
        return None
    try:
        from .signal_feed import LEV
        lev = LEV.get(grade, 3)
    except Exception:  # noqa: BLE001
        lev = None
    stop_pct = abs(stop / entry - 1) * 100
    tgt_pct = abs(tgt / entry - 1) * 100 if tgt else None
    qty = o.get("qty_left") if o.get("qty_left") is not None else o.get("qty")
    risk_usd = round(float(qty) * abs(entry - stop)) if qty else None
    return {"stop_pct": _r(-stop_pct, 1), "target_pct": _r(tgt_pct, 1),
            "rr": _r(tgt_pct / stop_pct, 1) if tgt_pct and stop_pct else None, "lev": lev, "risk_usd": risk_usd,
            "risk_pct": None if risk_usd is None else _r(risk_usd / START * 100, 2), "hold": _hold_ru(grade)}


def _live(desk: dict, src: str, grade: str) -> dict | None:
    """the rule's live record on its demo desk so far (closed trades, winners)"""
    if not desk:
        return None
    st = desk.get("stats") or {}
    if src == "own":
        r = (desk.get("live_by_rule") or {}).get(grade) or {"n": 0, "wins": 0, "avg_pct": None}
        return {"n": r.get("n", 0), "wins": r.get("wins", 0), "avg_pct": r.get("avg_pct"), "since": desk.get("since")}
    if src == "lst":
        n = int(st.get("trades") or 0)
        return {"n": n, "wins": round(float(st.get("winrate") or 0) * n), "avg_pct": st.get("avg_trade_pct"), "since": desk.get("since")}
    g = (st.get("by_grade") or {}).get(grade) or {}
    n = int(g.get("n") or 0)
    return {"n": n, "wins": round(float(g.get("winrate") or 0) * n), "avg_pct": None, "since": desk.get("since")}


def _scan_text(sc: dict, now: int | None) -> str | None:
    """the own desk's daily scan in one sentence: «Сегодня система проверила 40 монет по 8 условиям: …»"""
    if not sc.get("coins") or not sc.get("checks"):
        return None
    day = int(sc.get("day") or 0)
    today = (now or 0) // 86_400_000 * 86_400_000
    when = "Сегодня" if day == today else dt.datetime.fromtimestamp(day / 1000, dt.timezone.utc).strftime("%d.%m")
    n, s = int(sc["coins"]), int(sc.get("signals") or 0)
    head = f"{when} система проверила {n} {_plural(n, 'монету', 'монеты', 'монет')} по {sc['checks']} условиям"
    if sc.get("bull") is False:
        return head + ": рынок не в режиме роста — покупок нет, деньги ждут в долларах."
    return (head + f": правила дали {s} {_plural(s, 'сигнал', 'сигнала', 'сигналов')}, все условия прошли "
            f"{int(sc.get('passed') or 0)}.")


def _scan(sc: dict | None, now: int | None) -> dict | None:
    """futures.scan — today's (the last) daily scan of «Сигналы CRYPTO NOVA», real counters only"""
    if not isinstance(sc, dict) or not sc.get("coins"):
        return None
    return {"coins": sc.get("coins"), "checks": sc.get("checks"), "passed": sc.get("passed"), "signals": sc.get("signals"),
            "opened": sc.get("opened"), "asof": sc.get("asof"), "day": sc.get("day"), "src": "own", "text": _scan_text(sc, now)}


def _work(grade: str, f: dict, now: int | None) -> str | None:
    """one sentence with the real counters of the scan behind the signal; None when the counters were not recorded"""
    sc = f.get("scan") or {}
    if grade in ("P", "C"):
        return _scan_text(sc, now)
    if grade == "K" and sc.get("coins"):
        n = sc["coins"]
        slots = f.get("slots")
        return (f"В момент входа система проверяла {n} {_plural(n, 'альткоин', 'альткоина', 'альткоинов')}: рывок BTC даёт сигнал всем, "
                + (f"места получают {slots} самых торгуемых." if slots else "места получают самые торгуемые."))
    if grade in RULE_NAME_RU and sc.get("coins") and sc.get("rules"):
        n, r, k = sc["coins"], sc["rules"], int(sc.get("fired") or 0)
        return (f"В момент входа система проверяла {n} {_plural(n, 'монету', 'монеты', 'монет')} по {r} "
                f"{_plural(r, 'правилу', 'правилам', 'правилам')} — «{RULE_NAME_RU[grade]}» сработал у {k} из них.")
    if grade == "LST" and f.get("watching"):
        w = int(f["watching"])
        return (f"Система следит за {w} {_plural(w, 'фьючерсом', 'фьючерсами', 'фьючерсами')} BingX: шорт получает только новый "
                f"листинг, прошедший все фильтры.")
    return None


def _explain(o: dict, src: str, grade: str, entry, stop, tgt, desk: dict | None, now: int | None) -> dict:
    f = o.get("facts") if isinstance(o.get("facts"), dict) else {}
    checks = _checks(grade, f, str(o.get("symbol") or "")) if f else []
    hist = HISTORY.get(grade)
    note = None
    if src == "own" and grade in ("P", "C") and o.get("vol") is None and o.get("opened"):
        # the volume mark and the daily top 40 came on 19.10 — a trade without a stored volume was opened before them
        d = dt.datetime.fromtimestamp(o["opened"] / 1000, _kyiv())
        note = f"Сделка открыта {d:%d.%m} по первой версии правил: объём дня и место в топ-40 по обороту тогда ещё не проверялись."
    return {"why_title": TITLE_RU.get(grade), "checks": checks or None,
            "history": None if not hist else {**hist, "live": _live(desk or {}, src, grade)},
            "risk": _risk(o, grade, entry, stop, tgt), "work": _work(grade, f, now) if f else None, "note": note}


def _open_row(o: dict, src: str, desk: dict | None = None, now: int | None = None) -> dict:
    """one open trade of a mix desk in the app's words; the target: the desk's last take-profit or, for the rules that exit by
    rule / time, the same +1R/+2R/+3R ladder the Telegram message shows (its 3rd step)"""
    entry, price, stop = o.get("entry"), o.get("price"), o.get("stop_now") or o.get("stop")
    side = str(o.get("side") or "").upper()
    tps = o.get("tps") or []
    tgt = tps[-1] if tps else None
    grade = str(o.get("grade") or "")
    try:
        from .signal_feed import TIME_EXIT
    except Exception:  # noqa: BLE001
        TIME_EXIT = {"P", "C", "LST"}
    if (tgt is None or grade in TIME_EXIT) and entry and stop:
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
            "expires": o.get("expires"), "why": why, "src": src, "src_ru": rule or SRC_RU.get(src, "CRYPTO NOVA"),
            **_explain(o, src, grade, entry, stop, tgt, desk, now)}


def _kyiv():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Kyiv")
    except Exception:  # noqa: BLE001
        return dt.timezone(dt.timedelta(hours=3))


def _status(bal_open: int, closed: list, opened: list) -> str | None:
    """what the main strategy is doing, in one calm line (shown when it has nothing open)"""
    if bal_open:
        return None
    last = closed[0] if closed else None
    tail = ""
    if last and last.get("pnl_usd") is not None:
        d = dt.datetime.fromtimestamp((last.get("closed") or 0) / 1000, _kyiv())
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
            opened.append(_open_row(o, src, desk, now))
    opened.sort(key=lambda o: -(o.get("opened") or 0))
    bal_open = sum(1 for o in opened if o["src"] == "bal")
    closed = []
    for c in sorted(bal.get("closed") or [], key=lambda c: c.get("closed") or 0, reverse=True)[:8]:
        closed.append({"sym": c.get("symbol"), "side": str(c.get("side") or "").upper(), "pnl_usd": _r(float(c.get("pnl_usd") or 0)),
                       "closed": c.get("closed")})
    st = bal.get("stats") or {}
    n_closed = int(st.get("trades") or len(bal.get("closed") or []))
    return {"usd": _r(usd), "pct": _r(usd / START * 100), "today_usd": _r(today), "trades": n_closed + bal_open,
            "status_ru": _status(bal_open, closed, opened), "scan": _scan((prof.get("own") or {}).get("scan"), now),
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


def _config(out: str) -> dict:
    """web/app_config.json (in git): what the app may change without a new App Store version — the exchange partner link"""
    c = _load(out, "app_config.json")
    ref = c.get("exchange_ref")
    return {"exchange_name": c.get("exchange_name") or "BingX",
            "exchange_ref": ref if isinstance(ref, str) and ref.startswith("https://") else None,
            "exchange_trade_url": c.get("exchange_trade_url")}


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
           "market": _market(mk), "futures": fut, "spot": spot, "engine": _engine_facts(out), "config": _config(out),
           "note_ru": "Демо-счета CRYPTO NOVA. Прошлые результаты не гарантируют будущих. Не финансовый совет."}
    with open(os.path.join(out, "app_home.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    if int(now / 60000) % 60 == 0 or not os.path.exists(os.path.join(out, "app_btc.json")):
        b = btc_daily(conn, out)
        if b:
            with open(os.path.join(out, "app_btc.json"), "w", encoding="utf-8") as f:
                json.dump(b, f, ensure_ascii=False, separators=(",", ":"))
    return doc
