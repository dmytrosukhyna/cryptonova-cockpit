"""Outlook department — BTC (and ETH, SOL) outlook for the day (24 h), week (7 d) and month (30 d) that the presenter can
explain on air: in what price band the outcome landed 2 times out of 3, how often such a period ended higher in the same
BTC regime, how our short A/B forecast has scored so far, and which scheduled / market facts matter for the horizon.

Numbers come only from data; the texts are templates (no LLM), no promises.

Range («коридор 2 из 3»): σ = standard deviation of daily log returns over the last 90 days; band = price × exp(±k·σ·√N).
k = the 2/3 quantile of |N-day log return| ÷ (σ90 on that day × √N) over the whole history — the width at which 2 of 3
historical outcomes landed inside. Check out of sample: k fitted on windows before the last 2 years, coverage measured on
the last 2 years (`check`).

Base rate: share of N-day windows (every day is a start, windows overlap) that ended higher while BTC was in the same
regime as now — the panel's rule (terminal.report.regime): daily close above the 200-day average and 90-day change > 0
= BULL. Current regime = terminal.json portfolio.btc_bull (what the panel shows). n_independent = n ÷ N (non-overlapping
equivalent); the 95 % interval uses n_independent. «Как монетка» when 50 % is inside that interval.

History: cnq/agents/outlook_seed.json (Binance daily closes 2020-09 → 2026-09; data/ is not in git), the local
data/daily_closes18.json when present, table outlook_daily in agents.sqlite (completed days from terminal.json, so the
history keeps growing on the server) and the completed daily bars of web/terminal.json. Output: web/outlook.json =
  {asof, symbol:"BTC", price, regime{btc_bull, label_ru, sma200_*, …}, history{from,to,days}, params_ru, method_ru{}, summary_ru,
   horizons{day|week|month: {days, range{lo,hi,pm_pct,lo_pct,hi_pct,k,check{…}}, base_rate_up{p,n,n_independent,ci95,regime,period_ru,
   verdict,verdict_ru}, forecast_ab (day only), drivers[{kind,text_ru}], text_ru}}, others{ETH|SOL: {price, horizons{…range, base_rate_up, text_ru}}}}
Stdlib only.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sqlite3
import time

try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:  # noqa: BLE001
    KYIV = dt.timezone(dt.timedelta(hours=3))

DAY_MS = 86_400_000
HORIZONS = (("day", 1), ("week", 7), ("month", 30))
H_NAME = {"day": "Сутки", "week": "Неделя", "month": "Месяц"}
H_FOR = {"day": "на сутки", "week": "на неделю", "month": "на месяц"}
H_OVER = {"day": "за сутки", "week": "за неделю", "month": "за месяц"}
H_UNIT = {"day": "суток", "week": "недель", "month": "месяцев"}
SYMBOLS = ("BTC", "ETH", "SOL")
SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outlook_seed.json")
LOCAL = os.path.join("data", "daily_closes18.json")
VOL_DAYS = 90
TARGET = 2 / 3
OOS_DAYS = 730
WD_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
PARAMS_RU = ("Что используем: волатильность последних 90 дней, история дневных закрытий {period}, режим BTC (выше или ниже "
             "200-дневной средней), наш прогноз A/B и его проверка, календарь событий, макро-панель, панель наблюдения "
             "автопилота, индекс страха и жадности.")


# ------------------------------------------------------------------ formatting
def fmt_price(p: float | None) -> str:
    if p is None:
        return "—"
    if p >= 1000:
        return f"{p:,.0f}".replace(",", " ")
    if p >= 1:
        return f"{p:.2f}"
    return f"{p:.4f}"


def pct(x: float | None, d: int = 1, sign: bool = True) -> str:
    """x is a fraction (0.0123 → «+1.2%»)."""
    if x is None:
        return "—"
    v = round(x * 100, d)
    s = f"{abs(v):.{d}f}%"
    if not sign or v == 0:
        return s
    return ("+" if v > 0 else "−") + s


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural: 1 проверка, 2 проверки, 5 проверок."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def kyiv(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, KYIV)


def kyiv_str(ms: int, weekday: bool = True) -> str:
    t = kyiv(ms)
    return (f"{WD_RU[t.weekday()]} " if weekday else "") + t.strftime("%d.%m %H:%M")


def _day_iso(day: int) -> str:
    return (dt.date(1970, 1, 1) + dt.timedelta(days=day)).isoformat()


def _year(day: int) -> int:
    return (dt.date(1970, 1, 1) + dt.timedelta(days=day)).year


def _load(out: str, name: str):
    try:
        return json.load(open(os.path.join(out, name), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ history
def seed_closes(path: str = SEED) -> dict[str, dict[int, float]]:
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for s, v in (d.get("symbols") or {}).items():
        out[s] = {int(v["start_day"]) + i: float(c) for i, c in enumerate(v.get("closes") or []) if c}
    return out


def local_closes(path: str = LOCAL) -> dict[str, dict[int, float]]:
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {s: {int(k): float(c) for k, c in d[s + "USDT"].items() if c} for s in SYMBOLS if s + "USDT" in d}


def terminal_closes(term: dict | None) -> dict[str, dict[int, float]]:
    """Completed UTC daily bars from web/terminal.json (the partial first bar and today's forming bar are skipped)."""
    out: dict[str, dict[int, float]] = {}
    if not term:
        return out
    asof = term.get("asof") or term.get("generated") or 0
    for c in term.get("coins") or []:
        base = str(c.get("symbol", "")).replace("-USDT", "")
        if base not in SYMBOLS:
            continue
        m = out.setdefault(base, {})
        for k in c.get("candles_1d") or []:
            if k[0] % DAY_MS or k[0] + DAY_MS > asof + 900_000:
                continue
            m[k[0] // DAY_MS] = float(k[4])
    return out


def _db(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS outlook_daily(symbol TEXT, day INTEGER, close REAL, PRIMARY KEY(symbol, day))")


def db_closes(conn: sqlite3.Connection | None) -> dict[str, dict[int, float]]:
    if conn is None:
        return {}
    _db(conn)
    out: dict[str, dict[int, float]] = {}
    for s, d, c in conn.execute("SELECT symbol, day, close FROM outlook_daily"):
        out.setdefault(s, {})[int(d)] = float(c)
    return out


def save_closes(conn: sqlite3.Connection | None, closes: dict[str, dict[int, float]]) -> int:
    if conn is None:
        return 0
    _db(conn)
    rows = [(s, d, c) for s, m in closes.items() for d, c in m.items()]
    conn.executemany("INSERT OR REPLACE INTO outlook_daily VALUES(?,?,?)", rows)
    conn.commit()
    return len(rows)


def merge(*layers: dict[str, dict[int, float]]) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    for layer in layers:
        for s, m in (layer or {}).items():
            out.setdefault(s, {}).update(m)
    return out


# ------------------------------------------------------------------ statistics
def regime_labels(btc: dict[int, float]) -> dict[int, str]:
    """Panel rule per day: BULL = close above the 200-day average and 90-day change > 0; BEAR = below; else MIXED."""
    days = sorted(btc)
    c = [btc[d] for d in days]
    out = {}
    run = 0.0
    for i, d in enumerate(days):
        run += c[i]
        if i >= 200:
            run -= c[i - 200]
        if i < 199:
            continue
        sma = run / 200
        r90 = c[i] / c[i - 90] - 1
        out[d] = "BULL" if c[i] > sma and r90 > 0 else ("BEAR" if c[i] < sma else "MIXED")
    return out


def sma200(btc: dict[int, float]) -> float | None:
    days = sorted(btc)
    return sum(btc[d] for d in days[-200:]) / 200 if len(days) >= 200 else None


def _returns(closes: dict[int, float]) -> dict[int, float]:
    return {d: math.log(c / closes[d - 1]) for d, c in closes.items() if d - 1 in closes and c > 0 and closes[d - 1] > 0}


def sigmas(closes: dict[int, float], n: int = VOL_DAYS) -> dict[int, float]:
    """σ of daily log returns over the n days ending at each day (needs ≥ 80 % of the days present)."""
    ret = _returns(closes)
    out = {}
    for d in sorted(closes):
        xs = [ret[j] for j in range(d - n + 1, d + 1) if j in ret]
        if len(xs) < 0.8 * n:
            continue
        m = sum(xs) / len(xs)
        out[d] = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))
    return out


def _quantile(sorted_xs: list[float], q: float) -> float:
    if not sorted_xs:
        return float("nan")
    i = q * (len(sorted_xs) - 1)
    lo = int(math.floor(i)); hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (i - lo)


def band_fit(closes: dict[int, float], N: int, sig: dict[int, float] | None = None, oos_days: int = OOS_DAYS) -> dict | None:
    """k for the 2-of-3 band and its out-of-sample check (fit before the last `oos_days`, measure after)."""
    sig = sig if sig is not None else sigmas(closes)
    zs = []
    for d in sorted(sig):
        e = d + N
        if e in closes and sig[d] > 0:
            zs.append((d, math.log(closes[e] / closes[d]) / (sig[d] * math.sqrt(N))))
    if len(zs) < 60:
        return None
    k = _quantile(sorted(abs(z) for _, z in zs), TARGET)
    cut = zs[-1][0] - oos_days
    train = sorted(abs(z) for d, z in zs if d < cut)
    test = [z for d, z in zs if d >= cut]
    check = None
    if len(train) >= 60 and len(test) >= 30:
        k_fit = _quantile(train, TARGET)
        check = {"k_fit": round(k_fit, 3), "coverage": round(sum(1 for z in test if abs(z) <= k_fit) / len(test), 3), "n": len(test),
                 "from": _day_iso(cut), "to": _day_iso(zs[-1][0] + N),
                 "note_ru": f"k подобран на истории до {_day_iso(cut)[:7]}, проверен на {_year(cut)}–{_year(zs[-1][0] + N)}"}
    return {"k": round(k, 3), "n": len(zs), "from": _day_iso(zs[0][0]), "to": _day_iso(zs[-1][0] + N),
            "above_share": round(sum(1 for _, z in zs if z > k) / len(zs), 3),
            "below_share": round(sum(1 for _, z in zs if z < -k) / len(zs), 3), "check": check}


def price_range(price: float, sigma_now: float, N: int, fit: dict) -> dict:
    w = fit["k"] * sigma_now * math.sqrt(N)
    lo, hi = price * math.exp(-w), price * math.exp(w)
    return {"lo": round(lo, 6 if price < 1 else 2), "hi": round(hi, 6 if price < 1 else 2),
            "pm_pct": round((hi - lo) / 2 / price * 100, 1), "lo_pct": round((lo / price - 1) * 100, 1), "hi_pct": round((hi / price - 1) * 100, 1),
            "k": fit["k"], "sigma_daily_pct": round(sigma_now * 100, 2), "n": fit["n"], "from": fit["from"], "to": fit["to"],
            "above_share": fit["above_share"], "below_share": fit["below_share"], "check": fit["check"],
            "method": "vol90_k",
            "method_ru": (f"волатильность последних 90 дней ({sigma_now * 100:.2f}% в день) × √{N} × {fit['k']:.2f}; множитель подобран по "
                          f"истории {fit['from'][:4]}–{fit['to'][:4]} так, чтобы внутри оказывались 2 из 3 исходов")}


def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def base_rate(closes: dict[int, float], labels: dict[int, str], N: int, bull: bool | None) -> dict | None:
    """Share of N-day windows that ended higher; bull=True → only BULL start days, False → not BULL, None → all."""
    up = n = up_all = n_all = 0
    first = last = None
    for d in sorted(closes):
        e = d + N
        if e not in closes or d not in labels:
            continue
        r = closes[e] > closes[d]
        n_all += 1; up_all += r
        if bull is None or (labels[d] == "BULL") == bull:
            n += 1; up += r
            first = d if first is None else first
            last = e
    if n < 30:
        return None
    p = up / n
    n_ind = max(1, n // N)
    lo, hi = wilson(p, n_ind)
    verdict = "coin" if lo <= 0.5 <= hi else ("up" if p > 0.5 else "down")
    verdict_ru = {"coin": "как монетка", "up": "есть перевес вверх", "down": "есть перевес вниз"}[verdict]
    return {"p": round(p, 3), "n": n, "n_independent": n_ind, "ci95": [round(lo, 3), round(hi, 3)],
            "regime": None if bull is None else ("BULL" if bull else "NOT_BULL"),
            "from": _day_iso(first), "to": _day_iso(last), "period_ru": f"{_year(first)}–{_year(last)}",
            "p_all": round(up_all / n_all, 3) if n_all else None, "n_all": n_all, "verdict": verdict, "verdict_ru": verdict_ru}


# ------------------------------------------------------------------ inputs from the panel
def panel_regime(term: dict | None, btc_hist: dict[int, float]) -> dict:
    btc = next((c for c in (term or {}).get("coins") or [] if str(c.get("symbol", "")).startswith("BTC")), None)
    bull = ((term or {}).get("portfolio") or {}).get("btc_bull")
    src = "terminal.json portfolio.btc_bull"
    if bull is None and btc:
        bull = (btc.get("regime") or {}).get("long") == "BULL"; src = "terminal.json BTC regime.long"
    reg = (btc or {}).get("regime") or {}
    hist_sma = sma200(btc_hist)
    lab = regime_labels(btc_hist)
    hist_label = lab[max(lab)] if lab else None
    price = (btc or {}).get("price")
    out = {"btc_bull": bull, "source": src, "long": reg.get("long"), "mid": reg.get("mid"), "mid_note": reg.get("mid_note"),
           "sma200_panel": reg.get("sma200"), "sma200_hist": round(hist_sma, 2) if hist_sma else None, "hist_label": hist_label,
           "hh20": reg.get("hh20"), "ll20": reg.get("ll20"), "price": price,
           "dist_sma200_pct": round((price / hist_sma - 1) * 100, 1) if price and hist_sma else None}
    if bull is None:
        out["label_ru"] = "режим BTC неизвестен"
    else:
        out["label_ru"] = "бычий: BTC выше 200-дневной средней" if bull else "не бычий: BTC ниже 200-дневной средней или 90 дней в минусе"
    if bull is not None and hist_label is not None and (hist_label == "BULL") != bool(bull):
        out["note_ru"] = ("Панель и полная история расходятся в режиме: на сервере меньше 200 дней свечей, средняя считается по тому, что есть. "
                          "Условие берём по панели.")
    return out


def forecast_ab(cal: dict | None, sym: str = "BTC", now_ms: int | None = None) -> dict | None:
    if not cal:
        return None
    st = cal.get("stats") or {}
    o = (cal.get("open") or {}).get(sym)
    if not st.get("n") and not o:
        return None
    out = {"n": st.get("n"), "hit_rate": st.get("hit_rate"), "brier": st.get("brier"), "brier_coin_flip": st.get("brier_coin_flip", 0.25),
           "coin_n": ((st.get("per_coin") or {}).get(sym) or {}).get("n"), "coin_hit_rate": ((st.get("per_coin") or {}).get(sym) or {}).get("hit_rate"),
           "since": st.get("since"), "about_ru": cal.get("note")}
    if o:
        p = o.get("p_up")
        side = "up" if p is not None and p >= 0.55 else "down" if p is not None and p <= 0.45 else "flat"
        out.update({"p_up": p, "up": o.get("up"), "dn": o.get("dn"), "time": o.get("time"), "side": side,
                    "age_h": round((now_ms - o["time"]) / 3_600_000, 1) if now_ms and o.get("time") else None})
        if side == "up":
            out["scenario_ru"] = f"сценарий A — сначала к {fmt_price(o.get('up'))} (вероятность {round(p * 100)}%)"
        elif side == "down":
            out["scenario_ru"] = f"сценарий A — сначала к {fmt_price(o.get('dn'))} (вероятность {round((1 - p) * 100)}%)"
        else:
            out["scenario_ru"] = f"перевеса нет ({round(p * 100)}% на рост), ближние цели {fmt_price(o.get('dn'))} и {fmt_price(o.get('up'))}"
    hr = out.get("hit_rate")
    if hr is not None and out.get("n"):
        out["verdict_ru"] = "пока как монетка" if 0.45 <= hr <= 0.55 else ("лучше монетки" if hr > 0.55 else "хуже монетки")
    return out


def _event_line(e: dict) -> str:
    return f"{kyiv_str(e['time'])} (Киев) — {e.get('name')}. {e.get('what') or ''}".strip()


def drivers(h: str, N: int, ctx: dict, now_ms: int, regime: dict) -> list[dict]:
    out: list[dict] = []
    cal = ctx.get("calendar")
    if cal is not None:
        evs = [e for e in cal.get("events") or [] if e.get("time") and now_ms < e["time"] <= now_ms + N * DAY_MS]
        if h == "month":
            evs = [e for e in evs if (e.get("importance") or 0) >= 2]
        evs = sorted(sorted(evs, key=lambda e: (-(e.get("importance") or 0), e["time"]))[:2], key=lambda e: e["time"])
        for e in evs:
            out.append({"kind": "calendar", "time": e["time"], "name": e.get("name"), "importance": e.get("importance"), "text_ru": _event_line(e)})
        if not evs:
            out.append({"kind": "calendar", "text_ru": f"В календаре {H_FOR[h]} " + ("важных событий нет." if h == "month" else "плановых событий нет.")})
    macro = ctx.get("macro")
    if macro and h in ("day", "week"):
        for i in macro.get("items") or []:
            if i.get("signal_ru") and i.get("value") is not None:
                ch = i.get("chg_1d")
                chs = "" if ch is None else (f"{ch:+.2f}%" if i.get("chg_unit") == "%" else f"{ch:+.1f} бп").replace("-", "−")
                out.append({"kind": "macro", "key": i.get("key"), "text_ru": f"{i.get('name_ru')}: {chs} за день ({i.get('asof')}). {i['signal_ru']}"})
    scan = (ctx.get("paper") or {}).get("scan")
    if scan and h in ("day", "week") and scan.get("breadth") is not None:
        br, nb = scan["breadth"], scan.get("need_breadth") or 0.4
        out.append({"kind": "autopilot", "breadth": br, "need": nb,
                    "text_ru": f"Панель наблюдения автопилота: за сутки больше чем на 3% упали {round(br * 100)}% монет, для сделки по панике нужно {round(nb * 100)}%."})
        rows = {r.get("coin"): r for r in scan.get("rows") or []}
        need_b = scan.get("d_need_basis")
        for s in SYMBOLS:
            r = rows.get(s)
            if r and need_b is not None and r.get("basis") is not None and r["basis"] <= need_b:
                out.append({"kind": "basis", "coin": s, "text_ru": f"Фьючерс {s} дешевле спота на {abs(r['basis']) * 100:.2f}% — это условие правила «откуп дисконта» (нужно {abs(need_b) * 100:.2f}%)."})
    fund = ctx.get("funding")
    if fund:   # only unusual funding is a driver: ≥ 0.03 % per 8 h (3× the usual 0.01 %) or negative beyond −0.01 %; the line states the value
        for r in fund.get("table") or []:
            a = r.get("avg7")
            if r.get("symbol") in SYMBOLS and a is not None and (a >= 0.0003 or a <= -0.0001):
                out.append({"kind": "funding", "coin": r["symbol"],
                            "text_ru": f"Фандинг {r['symbol']} за 7 дней в среднем {a * 100:+.3f}% за 8 часов — {'лонги платят много' if a > 0 else 'шорты платят лонгам'}.".replace("-", "−")})
    if h in ("week", "month") and regime.get("price"):
        sma = regime.get("sma200_hist") or regime.get("sma200_panel")
        if h == "month" and sma:
            d = regime["price"] / sma - 1
            out.append({"kind": "regime", "text_ru": f"BTC {'выше' if d > 0 else 'ниже'} 200-дневной средней ({fmt_price(sma)}) на {pct(abs(d), 1, False)}. "
                                                      f"Закрытие дня {'ниже' if d > 0 else 'выше'} неё меняет режим."})
        mid = regime.get("mid")
        if mid in ("UP_NO_BREAKOUT", "LONG") and regime.get("hh20"):
            out.append({"kind": "trend", "text_ru": f"Тренд 28 дней: {regime.get('mid_note') or ''}. Правило тренда включится при закрытии дня выше {fmt_price(regime['hh20'])}."})
        elif mid in ("DOWN_NO_BREAKOUT", "SHORT") and regime.get("ll20"):
            out.append({"kind": "trend", "text_ru": f"Тренд 28 дней: {regime.get('mid_note') or ''}. Минимум 20 дней — {fmt_price(regime['ll20'])}."})
    fng = (ctx.get("markets") or {}).get("fng") or {}
    if fng.get("value") is not None:
        wk = f", неделю назад {fng['week']}" if fng.get("week") is not None else ""
        out.append({"kind": "fng", "text_ru": f"Индекс страха и жадности {fng['value']} ({fng.get('label', '')}){wk}."})
    return out[:5]


def _horizon_text(sym: str, h: str, N: int, rng: dict | None, br: dict | None, regime: dict, ab: dict | None, drv: list[dict]) -> str:
    S = []
    if rng:
        S.append(f"{H_NAME[h]}: коридор {sym} {fmt_price(rng['lo'])} – {fmt_price(rng['hi'])} $ (±{rng['pm_pct']:.1f}%) — "
                 f"в такой коридор на истории попадали 2 из 3 исходов.")
    if br:
        reg = ("Когда BTC в бычьем режиме, как сейчас," if br.get("regime") == "BULL" else
               "Когда BTC не в бычьем режиме, как сейчас," if br.get("regime") == "NOT_BULL" else "На всей истории")
        tail = "направление — как монетка" if br["verdict"] == "coin" else br["verdict_ru"] + ", но это не гарантия"
        size = (f"{br['n']} {plural(br['n'], 'день', 'дня', 'дней')}" if N == 1 else
                f"{br['n']} {plural(br['n'], 'отрезок', 'отрезка', 'отрезков')}, независимых около {br['n_independent']}")
        S.append(f"{reg} {sym} {H_OVER[h]} рос в {round(br['p'] * 100)}% случаев ({size}, {br['period_ru']}): {tail}.")
    if ab and h == "day" and ab.get("n"):
        a = f"Наш прогноз A/B на сутки: {ab['scenario_ru']}. " if ab.get("scenario_ru") else ""
        S.append(f"{a}За {ab['n']} {plural(ab['n'], 'проверку', 'проверки', 'проверок')} он угадал {round(ab['hit_rate'] * 100)}% — {ab.get('verdict_ru', '')}.")
    elif h != "day":
        ev = next((d for d in drv if d["kind"] == "calendar" and d.get("time")), None)
        if ev:
            S.append(f"В этом окне: {ev['name']} — {kyiv_str(ev['time'])} по Киеву.")
    return " ".join(S[:4])


# ------------------------------------------------------------------ build
def _btc_daily(term: dict | None) -> list:
    """completed UTC daily bars of BTC from web/terminal.json [[t, o, h, l, c, v], …] (today's forming bar dropped)"""
    term = term or {}
    asof = term.get("asof") or term.get("generated") or 0
    btc = next((c for c in term.get("coins") or [] if str(c.get("symbol", "")).startswith("BTC")), None) or {}
    return [k for k in btc.get("candles_1d") or [] if len(k) >= 5 and not k[0] % DAY_MS and k[0] + DAY_MS <= asof + 900_000]


def system_plan(ctx: dict, regime: dict, price: float | None) -> dict | None:
    """«Что будет делать система» (22.10): the BTC key levels and, for each, the reactions that are ALREADY programmed — every line
    names the code it comes from; nothing here is a forecast. Numbers are read from the modules themselves, so they cannot drift."""
    from ..risk import limits as RL
    from . import sweep_desk as SW
    from .portfolio import TOPUP
    from .spot_profiles import IDEAS
    bars = _btc_daily(ctx.get("terminal"))
    low20 = min(k[3] for k in bars[-20:]) if len(bars) >= 20 else regime.get("ll20")
    high20 = max(k[2] for k in bars[-20:]) if len(bars) >= 20 else regime.get("hh20")
    sma, sma_full = regime.get("sma200_panel") or regime.get("sma200_hist"), regime.get("sma200_hist")
    price = price or regime.get("price")
    if not price or not (low20 or high20 or sma):
        return None
    sw = (((ctx.get("paper") or {}).get("profiles") or {}).get(SW.KEY)) or {}
    pc = lambda x: f"{x * 100:g}%".replace("-", "−")                                           # noqa: E731
    lv = lambda key, name, p, lines, note=None: {"key": key, "name_ru": name, "price": round(p, 2), "note_ru": note,  # noqa: E731
                                                 "dist_pct": round((p / price - 1) * 100, 2), "lines": lines}
    L = []
    if low20:
        L.append(lv("low20", "Минимум 20 дней", low20, [
            # cnq/agents/sweep_desk.py episodes() / step(): BREAK 0.5 %, WIN 3 closes, stop = sweep low − STOP_PAD, HOLD_D days, «рост» only
            {"text_ru": f"Проткнёт на {pc(SW.BREAK)}+ и за {SW.WIN} дня вернётся выше → «{SW.NAME}» покупает (демо, только «рост»): "
                        f"стоп −{pc(SW.STOP_PAD)} под минимумом, выход через {SW.HOLD_D} дней.",
             "src": "cnq/agents/sweep_desk.py step()"},
            # paper_desk.PROFILES: cons / bal / aggr trade A, B, E, F, D — longs only; own_desk.LONG_ONLY; no desk has a breakdown short
            {"text_ru": "Закрепится ниже 3 дня → шорта нет: ни один счёт не шортит пробой поддержки (в «рост» это −3.76% на сделку).",
             "src": "cnq/agents/paper_desk.py PROFILES; docs/research/sweep.md"},
            # spot_profiles.IDEAS["stop_buy"] (IdeasPortfolio sells, ideas_reco «У стопа −20%»); core BTC/ETH/SOL — no stop
            {"text_ru": f"Спот: альт — стоп {pc(IDEAS['stop_buy'])} от входа. Ядро BTC/ETH/SOL без стопа, докупка при −{pc(TOPUP['dip'])} "
                        f"от средней цены (до {TOPUP['max_n']} раз за {TOPUP['window_d']} дней).",
             "src": "cnq/agents/spot_profiles.py IDEAS stop_buy, ideas_reco, IdeasPortfolio._topups; cnq/agents/portfolio.py TOPUP"},
        ]))
    if high20:
        L.append(lv("high20", "Максимум 20 дней", high20, [
            # own_desk rule C: close above the previous 20-day high and the 20-day average, regime «рост», day volume ≥ 1.5× — demo
            {"text_ru": "Закрытие выше → «Сигналы CRYPTO NOVA» (демо) покупают пробой 20 дней, только в «рост» и при объёме дня от 1.5×.",
             "src": "cnq/agents/own_desk.py rule C"},
            # paper_desk.signals class C makes a SHORT at a resistance zone in any regime (stop zone + 0.25 ATR), but no PROFILES desk trades C
            {"text_ru": "Шорт от сопротивления в коде есть, но ни один счёт его не торгует — основной счёт только покупает.",
             "src": "cnq/agents/paper_desk.py signals() class C; PROFILES"},
        ]))
    if sma:
        note = (f"по полной истории {fmt_price(sma_full)} $" if sma_full and abs(sma / sma_full - 1) > 0.01 else None)
        L.append(lv("sma200", "200-дневная средняя", sma, [
            # spot_ideas.py: not bull → reason «BTC в медвежьем режиме — покупаем только ядро» (status changes in the table)
            {"text_ru": "Закрытие ниже → режим «осторожно»: в таблице спота только ядро BTC/ETH/SOL, альты — «ждём».",
             "src": "cnq/agents/spot_ideas.py (reason regime)"},
            # paper_desk.signals: `side == LONG and not btc_bull and base != BTC` skips alt longs of classes C and R; B/E/F/D have no regime check
            {"text_ru": "Фьючерсы: откупы паники и каскадов идут в любом режиме (запрет лонгов альтов — только у неторгуемых правил "
                        "«у уровня» и «ретест»).", "src": "cnq/agents/paper_desk.py signals()"},
            # turbo_desk.step (btcx → dollars), own_desk (buys only in «рост»), sweep_desk.step (bull is not True → no entry)
            {"text_ru": "«Турбо · BTC ×2» уходит в доллары; «Сигналы CRYPTO NOVA» и «Ложный пробой» не покупают.",
             "src": "cnq/agents/turbo_desk.py; cnq/agents/own_desk.py; cnq/agents/sweep_desk.py"},
        ], note))
    always = [
        # cnq/execution/okx_futures.py: the stop is attached to the order and re-checked; missing and not restorable → closed at once
        {"text_ru": "Реальная позиция на OKX всегда со стопом на бирже; стоп не ставится → закрываем сразу.",
         "src": "cnq/execution/okx_futures.py"},
        # cnq/risk/limits.py DAILY_LOSS_HALT, enforced in cnq/risk/gate.py _allow (latch «daily»); the real OKX sleeve has its own
        # cnq/execution/okx_futures.py DAY_LOSS (3 % of the futures budget per UTC day) — read as text, execution is not imported here
        {"text_ru": f"Дневной стоп {pc(RL.DAILY_LOSS_HALT)} (все демо-счета вместе; у OKX свой −3%) → без новых входов до 00:00 UTC.", "src": "cnq/risk/limits.py DAILY_LOSS_HALT; cnq/risk/gate.py; okx_futures DAY_LOSS"},
        # cnq/risk/limits.py DRAWDOWN_HALT, enforced in cnq/risk/gate.py _allow (latch «drawdown»)
        {"text_ru": f"Просадка {pc(RL.DRAWDOWN_HALT)} от пика (все демо-счета) → без новых входов до сброса владельцем.",
         "src": "cnq/risk/limits.py DRAWDOWN_HALT; cnq/risk/gate.py"},
    ]
    bull = regime.get("btc_bull")
    return {"price": price, "regime_ru": "рост" if bull else "осторожно" if bull is False else "неизвестен", "levels": L,
            "always": always, "desk": {"key": SW.KEY, "name_ru": SW.NAME, "paper_only": True,
                                       # cnq/agents/sweep_desk.status_ru (the live app prefers paper.json → profiles.swp.status_ru)
                                       "status_ru": sw.get("status_ru") or "демо-счёт ещё не запущен на сервере"}}


def build(ctx: dict, history: dict[str, dict[int, float]], now_ms: int | None = None) -> dict:
    now_ms = now_ms or int(time.time() * 1000)
    term = ctx.get("terminal") or {}
    btc_hist = history.get("BTC") or {}
    regime = panel_regime(term, btc_hist)
    labels = regime_labels(btc_hist)
    bull = regime.get("btc_bull")
    prices = {str(c.get("symbol", "")).replace("-USDT", ""): c.get("price") for c in term.get("coins") or []}
    out = {"asof": now_ms, "asof_kyiv": kyiv(now_ms).strftime("%d.%m.%Y %H:%M"), "symbol": "BTC", "regime": regime, "horizons": {}, "others": {}}
    period = None
    for sym in SYMBOLS:
        closes = history.get(sym) or {}
        if len(closes) < VOL_DAYS + 40:
            continue
        price = prices.get(sym) or closes[max(closes)]
        sig = sigmas(closes)
        last_day = max(sig) if sig else None
        sigma_now = sig.get(last_day) if last_day is not None else None
        hz = {}
        for h, N in HORIZONS:
            fit = band_fit(closes, N, sig)
            rng = price_range(price, sigma_now, N, fit) if fit and sigma_now else None
            br = base_rate(closes, labels, N, bull) if labels else None
            if sym == "BTC":
                ab = forecast_ab(ctx.get("calibration"), sym, now_ms) if h == "day" else None
                drv = drivers(h, N, ctx, now_ms, regime)
                hz[h] = {"days": N, "label_ru": H_NAME[h], "range": rng, "base_rate_up": br, "forecast_ab": ab, "drivers": drv,
                         "text_ru": _horizon_text(sym, h, N, rng, br, regime, ab, drv)}
                if br and not period:
                    period = br["period_ru"]
            else:
                hz[h] = {"days": N, "label_ru": H_NAME[h], "range": rng, "base_rate_up": br,
                         "text_ru": _horizon_text(sym, h, N, rng, br, regime, None, [])}
        block = {"price": price, "vol90_daily_pct": round(sigma_now * 100, 2) if sigma_now else None,
                 "vol90_annual_pct": round(sigma_now * math.sqrt(365) * 100, 1) if sigma_now else None,
                 "history": {"from": _day_iso(min(closes)), "to": _day_iso(max(closes)), "days": len(closes)}, "horizons": hz}
        if sym == "BTC":
            out["price"] = price
            out["vol90_daily_pct"] = block["vol90_daily_pct"]; out["vol90_annual_pct"] = block["vol90_annual_pct"]
            out["history"] = block["history"]
            out["horizons"] = hz
        else:
            out["others"][sym] = block
    period = period or (f"{out['history']['from'][:4]}–{out['history']['to'][:4]}" if out.get("history") else "—")
    out["params_ru"] = PARAMS_RU.format(period=period)
    out["method_ru"] = {
        "range": "Коридор: насколько в среднем гуляла цена последние 90 дней, умножено на корень из числа дней; ширина подобрана по истории так, "
                 "чтобы внутри оказывались 2 из 3 исходов. Значит, в 1 случае из 3 цена выходит за коридор.",
        "base_rate": "Доля прошлых отрезков той же длины, которые закончились выше начала, — только в том же режиме BTC, что сейчас. "
                     "Отрезки перекрываются, поэтому независимых случаев меньше: n ÷ число дней. Если 50% внутри погрешности — это монетка.",
        "forecast_ab": "Прогноз A/B: куда цена дойдёт сначала — к ближней зоне сверху или снизу; записывается каждые 4 часа и проверяется через сутки."}
    h = out.get("horizons") or {}
    if h:
        parts = [f"{H_FOR[k]} ±{h[k]['range']['pm_pct']:.1f}%" for k, _ in HORIZONS if h.get(k) and h[k].get("range")]
        coins = [h[k]["base_rate_up"]["verdict"] == "coin" for k, _ in HORIZONS if h.get(k) and h[k].get("base_rate_up")]
        out["summary_ru"] = (f"BTC {fmt_price(out.get('price'))} $: коридор " + ", ".join(parts) + ". "
                             + ("Направление на всех горизонтах — как монетка." if coins and all(coins) else
                                "Направление: " + ", ".join(f"{H_NAME[k].lower()} — {h[k]['base_rate_up']['verdict_ru']}" for k, _ in HORIZONS
                                                            if h.get(k) and h[k].get("base_rate_up")) + "."))
    try:   # 22.10: «Что будет делать система» — the key BTC levels and the reactions already programmed at each (system_plan)
        out["system_plan"] = system_plan(ctx, regime, out.get("price"))
    except Exception as e:  # noqa: BLE001 — never breaks the outlook
        out["system_plan"] = None
        out["system_plan_error"] = f"{type(e).__name__}: {str(e)[:160]}"
    out["disclaimer_ru"] = "Это статистика прошлого, а не обещание. Не финансовая рекомендация."
    return out


def load_ctx(out_dir: str) -> dict:
    return {n.split(".")[0]: _load(out_dir, n) for n in ("terminal.json", "calibration.json", "calendar.json", "macro.json", "paper.json",
                                                          "markets.json", "funding.json")}


def write(out_dir: str = "web", conn: sqlite3.Connection | None = None, now_ms: int | None = None, seed_path: str = SEED,
          local_path: str = LOCAL, in_dir: str | None = None) -> dict:
    """Reads the panel JSON from in_dir (default: out_dir) and writes out_dir/outlook.json."""
    ctx = load_ctx(in_dir or out_dir)
    term_c = terminal_closes(ctx.get("terminal"))
    try:
        save_closes(conn, term_c)
    except Exception:  # noqa: BLE001
        pass
    history = merge(seed_closes(seed_path), local_closes(local_path), db_closes(conn), term_c)
    doc = build(ctx, history, now_ms)
    doc["sources"] = {k: (v or {}).get("asof") if isinstance(v, dict) else None for k, v in ctx.items()}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "outlook.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return doc
