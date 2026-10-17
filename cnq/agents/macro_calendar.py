"""Scheduled US macro events that move crypto: FOMC rate decisions (+ press conference), CPI inflation, NFP jobs report.
Official sources: federalreserve.gov FOMC calendar; bls.gov release schedules (CPI, Employment Situation).
Times are UTC (US daylight time ends 01.11.2026, starts 14.03.2027 → 14:00 ET = 18:00 UTC in summer, 19:00 UTC in winter;
08:30 ET = 12:30 / 13:30 UTC). 2027 CPI/NFP dates are not published yet → not listed (never present unknown data as data).
What the events usually do to crypto is measured in docs/research/macro_events.md; until then the text only says what is certain."""
from __future__ import annotations

import datetime as dt
import json
import os

KYIV = dt.timezone(dt.timedelta(hours=2))   # display only; panel uses the browser's Europe/Kyiv zone

EVENTS = [   # (UTC "YYYY-MM-DD HH:MM", type, note)
    ("2026-10-28 18:00", "FOMC", ""), ("2026-11-06 13:30", "NFP", ""), ("2026-11-10 13:30", "CPI", ""),
    ("2026-12-04 13:30", "NFP", ""), ("2026-12-09 19:00", "FOMC", "с прогнозами ФРС"), ("2026-12-10 13:30", "CPI", ""),
    ("2027-01-27 19:00", "FOMC", ""), ("2027-03-17 18:00", "FOMC", "с прогнозами ФРС"), ("2027-04-28 18:00", "FOMC", ""),
    ("2027-06-09 18:00", "FOMC", "с прогнозами ФРС"), ("2027-07-28 18:00", "FOMC", ""), ("2027-09-15 18:00", "FOMC", "с прогнозами ФРС"),
    ("2027-10-27 18:00", "FOMC", ""), ("2027-12-08 19:00", "FOMC", "с прогнозами ФРС"),
]
INFO = {
    "FOMC": {"name": "ФРС: решение по ставке", "importance": 3,
             "what": "Решение по ставке и пресс-конференция главы ФРС через 30 минут. Самое сильное событие для рынка: цена может резко пойти в любую сторону."},
    "CPI": {"name": "Инфляция в США (CPI)", "importance": 2,
            "what": "Инфляция выше ожиданий — повод бояться высоких ставок, ниже — наоборот. Первые минуты после выхода обычно самые резкие."},
    "NFP": {"name": "Рынок труда США (NFP)", "importance": 2,
            "what": "Число новых рабочих мест. Сильный отчёт может отодвинуть снижение ставки, слабый — приблизить."},
}


def _ts(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def upcoming(now_ms: int, days: float = 60) -> list[dict]:
    out = []
    for t, typ, note in EVENTS:
        ms = _ts(t)
        if now_ms - 3 * 3_600_000 <= ms <= now_ms + days * 86_400_000:
            i = INFO[typ]
            out.append({"time": ms, "type": typ, "name": i["name"], "importance": i["importance"], "what": i["what"], "note": note,
                        "in_hours": round((ms - now_ms) / 3_600_000, 1)})
    return out


def post_lines(now_ms: int, days: float = 7) -> list[str]:
    """Plain lines for channel posts: «— ФРС: решение по ставке — 28.10 в 20:00 по Киеву (через 3 дня)»."""
    out = []
    for e in upcoming(now_ms, days):
        if e["in_hours"] < -1:
            continue
        local = dt.datetime.fromtimestamp(e["time"] / 1000, dt.timezone.utc).astimezone(_kyiv_tz(e["time"]))
        h = e["in_hours"]
        when = "сегодня" if h < 24 and local.date() == dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc).astimezone(_kyiv_tz(now_ms)).date() \
            else (f"через {int(round(h / 24))} {'день' if int(round(h / 24)) == 1 else 'дня' if int(round(h / 24)) in (2, 3, 4) else 'дней'}")
        out.append(f"— {e['name']}{' (' + e['note'] + ')' if e['note'] else ''} — {local:%d.%m} в {local:%H:%M} по Киеву ({when})")
    return out


def _kyiv_tz(ms: int) -> dt.timezone:
    """Kyiv: UTC+3 from the last Sunday of March to the last Sunday of October, otherwise UTC+2."""
    d = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    def last_sun(y, m):
        x = dt.datetime(y, m, 31, 1, tzinfo=dt.timezone.utc)
        return x - dt.timedelta(days=(x.weekday() + 1) % 7)
    summer = last_sun(d.year, 3) <= d < last_sun(d.year, 10)
    return dt.timezone(dt.timedelta(hours=3 if summer else 2))


def write(out: str = "web", now_ms: int | None = None) -> dict:
    now_ms = now_ms or int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    d = {"asof": now_ms, "events": upcoming(now_ms, 120),
         "note": "Официальные даты ФРС и BLS. Время — момент публикации. Даты CPI и NFP на 2027 год ещё не объявлены.",
         "sources": ["https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", "https://www.bls.gov/schedule/news_release/cpi.htm",
                     "https://www.bls.gov/ces/publications/news-release-schedule.htm"]}
    os.makedirs(out, exist_ok=True)
    json.dump(d, open(os.path.join(out, "calendar.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return d
