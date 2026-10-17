"""Scheduled US macro events that move crypto: FOMC rate decisions (+ press conference), CPI inflation, NFP jobs report.
Official sources: federalreserve.gov FOMC calendar; bls.gov release schedules (CPI, Employment Situation).
Times are UTC (US daylight time ends 01.11.2026, starts 14.03.2027 → 14:00 ET = 18:00 UTC in summer, 19:00 UTC in winter;
08:30 ET = 12:30 / 13:30 UTC). 2027 CPI/NFP dates come from scripts/macro_events.json (BLS schedules, status «scheduled»).
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
    # Bank of Japan (decision ≈ midday Tokyo on day 2 → ≈ 03:00 UTC; boj.or.jp schedule)
    ("2026-10-30 03:00", "BOJ", ""), ("2026-12-18 03:00", "BOJ", ""), ("2027-01-22 03:00", "BOJ", ""), ("2027-03-18 03:00", "BOJ", ""),
    ("2027-04-28 03:00", "BOJ", ""), ("2027-06-11 03:00", "BOJ", ""), ("2027-07-22 03:00", "BOJ", ""), ("2027-09-22 03:00", "BOJ", ""),
    ("2027-10-29 03:00", "BOJ", ""), ("2027-12-17 03:00", "BOJ", ""),
    # ECB (14:15 CET/CEST; ecb.europa.eu calendar)
    ("2026-10-29 13:15", "ECB", ""), ("2026-12-17 13:15", "ECB", "с прогнозами"), ("2027-02-04 13:15", "ECB", ""), ("2027-03-18 13:15", "ECB", "с прогнозами"),
    ("2027-04-29 12:15", "ECB", ""), ("2027-06-10 12:15", "ECB", "с прогнозами"), ("2027-07-22 12:15", "ECB", ""), ("2027-09-09 12:15", "ECB", "с прогнозами"),
    ("2027-10-28 12:15", "ECB", ""), ("2027-12-16 13:15", "ECB", "с прогнозами"),
    # US politics / crypto regulation (CLARITY Act failed cloture 49–50 on 15.09.2026; no re-vote date set)
    ("2026-11-03 23:00", "ELECTION", ""), ("2026-10-20 21:00", "SEC", "приём комментариев к правилам для крипто-активов"),
]
INFO = {   # texts measured on 2023–2026 (docs/research/macro_events.md): only the volatility spike is robust, no tradable direction
    "FOMC": {"name": "ФРС: решение по ставке", "importance": 3,
             "what": "Решение по ставке и через 30 минут пресс-конференция. В первый час биткоин обычно проходит около 0.7% (в 2.7 раза больше обычного), "
                     "в каждом третьем случае — больше 1%. Направление заранее не угадать."},
    "CPI": {"name": "Инфляция в США (CPI)", "importance": 2,
            "what": "В первый час биткоин обычно проходит около 0.5%, в каждом третьем случае — больше 1%. В 2026 году реакция заметно слабее. "
                    "Перед публикацией цена чаще немного растёт, чем падает."},
    "NFP": {"name": "Рынок труда США (NFP)", "importance": 2,
            "what": "В первый час биткоин обычно проходит около 0.4%, больше 1% — примерно 1 раз из 5. Через час открывается фондовый рынок США — движение может продолжиться."},
}
INFO.update({
    "BOJ": {"name": "Банк Японии: решение по ставке", "importance": 3,
            "what": "Обычное решение Банка Японии биткоин заметно не двигает. Повышение ставки — другое дело: после 4 из 5 повышений (2024–2026) "
                    "биткоин следующие 3 дня был слабее обычного, в среднем на 2%; за повышением 31.07.2024 последовал обвал 5 августа. Случаев мало — это предупреждение, не правило."},
    "ECB": {"name": "ЕЦБ: решение по ставке", "importance": 1,
            "what": "На биткоин заметно не влияет: ни направления, ни всплеска колебаний (29 решений 2023–2026)."},
    "OPEX": {"name": "Экспирация опционов Deribit", "importance": 1,
             "what": "Последняя пятница месяца, 08:00 UTC. Заметного движения после экспирации на истории 2023–2026 нет."},
    "ELECTION": {"name": "Выборы в Конгресс США", "importance": 3,
                 "what": "Промежуточные выборы. От нового состава Сената зависит судьба закона о крипторынке (CLARITY Act провалил голосование 15.09.2026, 49 против 50)."},
    "PCE": {"name": "Инфляция PCE (США)", "importance": 2,
            "what": "Любимый показатель инфляции ФРС. В первый час биткоин колеблется в 1.3 раза сильнее обычного; направление не угадать."},
    "PPI": {"name": "Цены производителей PPI (США)", "importance": 1,
            "what": "В первый час колебания в 1.3 раза сильнее обычного; направления нет."},
    "GDP": {"name": "ВВП США", "importance": 1, "what": "Заметной реакции биткоина на истории нет."},
    "JACKSON_HOLE": {"name": "Джексон-Хоул: речь главы ФРС", "importance": 3,
                     "what": "Все 4 раза (2023–2026) в первый час биткоин колебался в 2–5 раз сильнее обычного, направление каждый раз разное."},
    "SEC": {"name": "SEC: регулирование крипты", "importance": 1,
            "what": "Срок приёма комментариев к правилам SEC для крипто-активов. Само по себе цену обычно не двигает — важно, что будет дальше."},
})


def _opex(now_ms: int, months: int = 6) -> list[tuple[str, str, str]]:
    """Deribit monthly options expiry: last Friday of the month, 08:00 UTC (quarterly in Mar/Jun/Sep/Dec)."""
    d = dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc); out = []
    y, m = d.year, d.month
    for _ in range(months + 1):
        nm = dt.date(y + (m == 12), m % 12 + 1, 1)
        last = nm - dt.timedelta(days=1)
        fri = last - dt.timedelta(days=(last.weekday() - 4) % 7)
        out.append((f"{fri:%Y-%m-%d} 08:00", "OPEX", "квартальная" if m in (3, 6, 9, 12) else ""))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


VERDICT = ("Проверено на 2023–2026: направления цены ни одно плановое событие не предсказывает — ни ФРС, ни инфляция, ни Банк Японии. "
           "Устойчив только всплеск колебаний в первый час после ФРС, CPI, NFP (и слабее — PCE, PPI). Фильтры автопилота вокруг событий ухудшали результат — позиции держим как обычно. "
           "Из рынков сильнее всего с биткоином связан фондовый рынок США (в тот же день), доллар, иена, нефть и доходности — слабо.")


def _from_research() -> list[tuple[str, str, str]]:
    """Future events from scripts/macro_events.json (official Fed/BLS schedules, built by the macro study) — extends the list to 2027."""
    out = []
    evs = []
    for name in ("macro_events.json", "macro_events_ext.json"):   # FOMC/CPI/NFP + BoJ/ECB/PCE/PPI/GDP/Jackson Hole (docs/research/macro_*.md)
        try:
            evs += json.load(open(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", name), encoding="utf-8")).get("events", [])
        except Exception:  # noqa: BLE001
            pass
    for e in evs:
        if e.get("type") in INFO and e.get("status") == "scheduled" and e.get("utc"):
            note = "с прогнозами ФРС" if e.get("sep") else ""
            out.append((e["utc"].replace("T", " ").rstrip("Z"), e["type"], note))
    return out


def _ts(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def upcoming(now_ms: int, days: float = 60) -> list[dict]:
    out = []
    seen = set()
    for t, typ, note in sorted(set(EVENTS) | set(_from_research()) | set(_opex(now_ms)), key=lambda x: x[0]):
        if (t, typ) in seen:
            continue
        seen.add((t, typ))
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
        if e["in_hours"] < -1 or e["importance"] < 2:   # posts mention only events that matter (ФРС, Банк Японии, инфляция, рынок труда, выборы)
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
         "note": "Официальные даты ФРС и BLS. Время — момент публикации. Даты ФРС на 2027 год — предварительные.", "verdict": VERDICT,
         "sources": ["https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", "https://www.bls.gov/schedule/news_release/cpi.htm",
                     "https://www.bls.gov/ces/publications/news-release-schedule.htm"]}
    os.makedirs(out, exist_ok=True)
    json.dump(d, open(os.path.join(out, "calendar.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return d
