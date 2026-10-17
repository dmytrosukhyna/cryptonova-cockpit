"""Macro watch — current values of the outside markets that move crypto, for the panel (web/macro.json, every server run).

Items: dollar index (DXY), US 10-year and 2-year yields, USD/JPY, oil (WTI, Brent), gold, S&P 500 and Nasdaq-100 futures, VIX,
Nikkei 225. Each item: latest value, change vs the previous daily close and vs the close 5 sessions back (yields in basis
points, everything else in %).

Sources, as checked from GitHub Actions on 17.10.2026 (scripts/macro_factors_fetch.py --probe):
  • Yahoo Finance chart API — every item. From Actions it answers only to a browser-like TLS client: if the optional package
    curl_cffi is installed it is used (site.yml installs it for this step); plain urllib gets HTTP 429 there.
  • fallbacks that answer to plain urllib: US Treasury daily par yields (10Y, 2Y; end of day), CBOE delayed quotes (VIX,
    S&P 500 and Nasdaq-100 cash indices, 15 min delay), ECB reference rate via api.frankfurter.app (USD/JPY, once a day).
    FRED and Stooq do not answer from Actions. DXY, oil, gold and the Nikkei have no free fallback → shown empty.

What the study found (docs/research/macro_factors.md, 2023–2026; scripts/macro_factors_test.py): the solid link is the SAME
day — when US stocks move hard, crypto moves the same way that day, and after a hard fall the next 24 h swing more than usual.
Nothing here predicts tomorrow's direction, except a reversal after big Nikkei days seen only in 2024–2026 (not in 2021–2022) —
shown as an observation. `signal_ru` follows these findings and never promises a direction. Stdlib only (curl_cffi optional);
network calls are injectable (`fetch=`)."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# key, name_ru, unit shown, Yahoo symbol, fallback (kind:id) or None, change kind ('bp' | 'pct')
ITEMS = [
    ("DXY", "Индекс доллара (DXY)", "пт", "DX-Y.NYB", None, "pct"),
    ("US10Y", "Доходность 10-летних облигаций США", "%", "^TNX", "treasury:10 Yr", "bp"),
    ("US2Y", "Доходность 2-летних облигаций США", "%", "2YY=F", "treasury:2 Yr", "bp"),
    ("USDJPY", "Доллар / иена", "¥", "JPY=X", "ecb:JPY", "pct"),
    ("WTI", "Нефть WTI", "$", "CL=F", None, "pct"),
    ("BRENT", "Нефть Brent", "$", "BZ=F", None, "pct"),
    ("GOLD", "Золото", "$", "GC=F", None, "pct"),
    ("SPX_FUT", "S&P 500 (фьючерс)", "пт", "ES=F", "cboe:_SPX", "pct"),
    ("NDX_FUT", "Nasdaq-100 (фьючерс)", "пт", "NQ=F", "cboe:_NDX", "pct"),
    ("VIX", "VIX — индекс страха на рынке акций", "пт", "^VIX", "cboe:_VIX", "pct"),
    ("NIKKEI", "Nikkei 225 (Япония)", "пт", "^N225", None, "pct"),
]

NOTES = [
    "Акции США и биткоин в один и тот же день чаще всего движутся вместе: в 2023–2026 корреляция с Nasdaq около 0.4 "
    "и растёт (2023 — 0.23, 2025–2026 — 0.49). На 1% движения Nasdaq биткоин в среднем проходит около 0.9%. "
    "Но движение акций сегодня не подсказывает, куда пойдёт биткоин завтра.",
    "После резкого падения акций (Nasdaq −2% и больше, S&P −2%, VIX +20% за день) следующие сутки биткоин колеблется "
    "в 1.4–1.8 раза сильнее обычного. Направление на завтра по истории не угадывается.",
    "Доллар, иена, доходности облигаций, нефть и золото: в обычные дни связь с биткоином слабая и меняет знак от года к году. "
    "Резкие движения иены (как 5 августа 2024), облигаций и нефти дальнейшее направление биткоина тоже не предсказывали.",
    "Наблюдение 2024–2026: после сильного дня в Японии (Nikkei ±1.5% и больше к закрытию Токио) биткоин в следующие сутки "
    "чаще шёл в обратную сторону (в среднем 0.8% за сделку). В 2021–2022 такого не было, поэтому это не сигнал автопилота.",
]


def _get(url: str, timeout: float = 12.0) -> bytes:
    """urllib for everything; curl_cffi (browser TLS) for Yahoo when installed — Yahoo answers 429 to urllib from Actions."""
    if "finance.yahoo.com" in url:
        try:
            from curl_cffi import requests as cr   # optional
            r = cr.get(url, impersonate="chrome", timeout=timeout)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r.content
        except ImportError:
            pass
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def yahoo_daily(sym: str, fetch=None) -> list[tuple[int, float]]:
    """[(unix seconds, close)] for the last month of daily bars; the last bar carries the latest price."""
    fetch = fetch or _get
    q = urllib.parse.quote(sym, safe="")
    last = None
    for host in ("query1", "query2"):
        try:
            d = json.loads(fetch(f"https://{host}.finance.yahoo.com/v8/finance/chart/{q}?interval=1d&range=1mo"))
            res = d["chart"]["result"][0]
            ts = res.get("timestamp") or []
            cl = res["indicators"]["quote"][0].get("close") or []
            out = [(int(t), float(c)) for t, c in zip(ts, cl) if c is not None]
            px = (res.get("meta") or {}).get("regularMarketPrice")
            if out and px is not None:
                out[-1] = (out[-1][0], float(px))
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"yahoo {sym}: {last}")


def treasury_daily(col: str, fetch=None, now: float | None = None) -> list[tuple[int, float]]:
    """US Treasury daily par yield curve (end of day), this month and the previous one, oldest first."""
    fetch = fetch or _get
    d = dt.datetime.fromtimestamp(now or time.time(), dt.timezone.utc).date()
    prev = (d.replace(day=1) - dt.timedelta(days=1))
    rows = {}
    for m in (prev, d):
        url = (f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/all/{m:%Y%m}"
               f"?type=daily_treasury_yield_curve&field_tdr_date_value_month={m:%Y%m}&page&_format=csv")
        try:
            for r in csv.DictReader(io.StringIO(fetch(url).decode())):
                if r.get(col):
                    t = int(dt.datetime.strptime(r["Date"], "%m/%d/%Y").replace(tzinfo=dt.timezone.utc).timestamp())
                    rows[t] = float(r[col])
        except Exception:  # noqa: BLE001
            if m == d and not rows:
                raise
    if not rows:
        raise RuntimeError(f"treasury {col}: no rows")
    return sorted(rows.items())


def cboe_quote(sym: str, fetch=None) -> list[tuple[int, float]]:
    """CBOE delayed quote (15 min): [(prev close), (current)] — enough for the 1-day change only."""
    fetch = fetch or _get
    d = json.loads(fetch(f"https://cdn.cboe.com/api/global/delayed_quotes/quotes/{sym}.json"))["data"]
    cur = float(d["current_price"])
    prev = d.get("prev_day_close")
    if prev in (None, 0):
        prev = cur - float(d.get("price_change") or 0)
    t = int(time.time())
    return [(t - 86_400, float(prev)), (t, cur)]


def ecb_usd(ccy: str, fetch=None, now: float | None = None) -> list[tuple[int, float]]:
    """ECB reference rate (via api.frankfurter.app), USD base, last ~2 weeks of business days."""
    fetch = fetch or _get
    d = dt.datetime.fromtimestamp(now or time.time(), dt.timezone.utc).date()
    j = json.loads(fetch(f"https://api.frankfurter.app/{(d - dt.timedelta(days=16)).isoformat()}..?from=USD&to={ccy}"))
    out = []
    for day, r in sorted(j.get("rates", {}).items()):
        if ccy in r:
            out.append((int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()), float(r[ccy])))
    if not out:
        raise RuntimeError(f"ecb {ccy}: no rates")
    return out


def _fallback(spec: str, fetch, now) -> tuple[list[tuple[int, float]], str]:
    kind, x = spec.split(":", 1)
    if kind == "treasury":
        return treasury_daily(x, fetch, now), "US Treasury (конец дня)"
    if kind == "cboe":
        return cboe_quote(x, fetch), "CBOE (задержка 15 мин)"
    if kind == "ecb":
        return ecb_usd(x, fetch, now), "ЕЦБ (курс дня)"
    raise ValueError(spec)


def _change(series: list[tuple[int, float]], kind: str, back: int) -> float | None:
    if len(series) <= back:
        return None
    a, b = series[-1 - back][1], series[-1][1]
    if kind == "bp":
        return round((b - a) * 100, 1)
    return round((b / a - 1) * 100, 2) if a else None


def signal(key: str, value: float | None, c1: float | None) -> str:
    """Plain-language note only where the study found something solid (docs/research/macro_factors.md)."""
    if c1 is None or value is None:
        return ""
    if key in ("NDX_FUT", "SPX_FUT") and c1 <= -2:
        return ("Акции США резко падают. В такие дни биткоин в среднем тоже теряет 2–3%, а следующие сутки колеблется "
                "примерно в 1.4 раза сильнее обычного. Куда пойдёт дальше — по истории не угадать.")
    if key in ("NDX_FUT", "SPX_FUT") and c1 >= 2:
        return "Акции США резко растут — в такие дни биткоин в среднем тоже растёт (около +2.8%); на завтра это ничего не обещает."
    if key == "VIX" and c1 >= 20:
        return "Страх на рынке акций резко вырос. В такие дни биткоин в среднем падает на 4%, следующие сутки — повышенные колебания."
    if key == "VIX" and value >= 25:
        return "VIX выше 25: в такие периоды биткоин сильнее привязан к акциям, чем обычно."
    if key == "NIKKEI" and abs(c1) >= 1.5:
        side = "рос" if c1 < 0 else "снижался"
        return (f"Сильный день в Японии ({c1:+.1f}%). В 2024–2026 после таких дней биткоин в следующие сутки чаще {side} — "
                "наблюдение, а не сигнал: в 2021–2022 этого не было.")
    return ""


def run(fetch=None, now: float | None = None) -> dict:
    now = now or time.time()
    items, errors = [], []
    for key, name, unit, ysym, fb, kind in ITEMS:
        ser, src, err = None, None, None
        try:
            ser = yahoo_daily(ysym, fetch); src = "Yahoo Finance"
        except Exception as e:  # noqa: BLE001
            err = str(e)[:120]
        if ser is None and fb:
            try:
                ser, src = _fallback(fb, fetch, now)
            except Exception as e:  # noqa: BLE001
                err = (err or "") + f" | {fb}: {str(e)[:120]}"
        if not ser:
            items.append({"key": key, "name_ru": name, "value": None, "chg_1d": None, "chg_5d": None, "unit": unit,
                          "chg_unit": "бп" if kind == "bp" else "%", "source": None, "asof": None, "signal_ru": ""})
            errors.append(f"{key}: {err}")
            continue
        if key == "US10Y" and ser[-1][1] > 20:                       # old ^TNX convention: yield × 10
            ser = [(t, v / 10) for t, v in ser]
        if fb and fb.startswith("cboe:") and src.startswith("CBOE"):
            name = name.replace("(фьючерс)", "(индекс)")
        v = round(ser[-1][1], 3 if kind == "bp" else 2)
        c1, c5 = _change(ser, kind, 1), _change(ser, kind, 5)
        items.append({"key": key, "name_ru": name, "value": v, "chg_1d": c1, "chg_5d": c5, "unit": unit,
                      "chg_unit": "бп" if kind == "bp" else "%", "source": src,
                      "asof": dt.datetime.fromtimestamp(ser[-1][0], dt.timezone.utc).strftime("%Y-%m-%d"),
                      "signal_ru": signal(key, v, c1)})
    return {"asof": int(now * 1000), "asof_iso": dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "items": items, "notes": NOTES, "errors": errors,
            "about": "Изменение за день — к закрытию предыдущей торговой сессии; за 5 дней — к закрытию 5 сессий назад. "
                     "Доходности — в базисных пунктах (1 бп = 0.01%). Источник: Yahoo Finance; запасные — US Treasury, CBOE, ЕЦБ.",
            "research": "docs/research/macro_factors.md"}


def write(out: str = "web", fetch=None, now: float | None = None) -> dict:
    d = run(fetch, now)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "macro.json"), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    return d
