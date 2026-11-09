"""«Утренний доклад» (07.11.2026, owner 16:25): a short daily report at 07:00 Kyiv — what the system made over the last 24 h, the
market in a line, the account, and one line for the day only when there is something new. Warm, honest, no promises; public
wording «система CRYPTO NOVA AI» (never the method).

Numbers: web/public_real.json (the real account: 24 h futures / spot / total from its curves, value, result since 22.10) and
web/markets.json (BTC, fear & greed); the day line from the macro calendar (cnq/agents/macro_calendar.py, events of today).
Variety: every part has several phrasings; the ones used on the last days are remembered (agents.sqlite morning_hist) and not
repeated; the day line is skipped when it is the same as yesterday's or empty.
Output: web/morning.json {day, asof, title, body (the push), owner {title, body}, text (spoken), audio} + web/morning.mp3
(the same neural voices as the spoken brief, cnq/agents/voice.py). The push itself goes out from cnq/agents/app_push.py.
Stdlib only (edge-tts optional for the mp3)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time

try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:  # noqa: BLE001
    KYIV = dt.timezone(dt.timedelta(hours=2))

BUILD_FROM = (6, 40)            # the report is written from 06:40 Kyiv (the push leaves at 07:00)
BUILD_TO = 11                   # … and not after 11:00 (a late report would describe the wrong night)
MEMORY_DAYS = 4                 # a phrasing used on any of the last 4 days is not used again
SMALL = 10.0                    # under $10 a day is «почти в ноль» (no «хорошие сутки» for a few dollars)
FNG_RU = {"extreme fear": "крайний страх", "fear": "страх", "neutral": "нейтрально", "greed": "жадность", "extreme greed": "крайняя жадность"}

TITLES = ["Доброе утро · система CRYPTO NOVA AI", "Утренний доклад CRYPTO NOVA AI", "CRYPTO NOVA AI · как прошла ночь",
          "С добрым утром от CRYPTO NOVA AI", "CRYPTO NOVA AI · коротко за сутки", "Новый день · система CRYPTO NOVA AI"]
UP = ["За сутки система заработала {tot}: фьючерсы {fut}, спот {spot}.",
      "Плюс за сутки — {tot} (фьючерсы {fut}, спот {spot}).",
      "Сутки в плюсе: {tot}. Фьючерсы {fut}, спот {spot}.",
      "Сутки закончились в плюсе: {tot} — фьючерсы {fut}, спот {spot}."]
DOWN = ["За сутки {tot}: фьючерсы {fut}, спот {spot}. Стопы на месте, система работает по плану.",
        "Сутки в минусе — {tot} (фьючерсы {fut}, спот {spot}). Риск каждой сделки ограничен стопом.",
        "Минус за сутки: {tot}. Фьючерсы {fut}, спот {spot} — система держит свои правила.",
        "Не лучшие сутки: {tot} (фьючерсы {fut}, спот {spot}). У каждой позиции стоит стоп."]
FLAT = ["Сутки почти в ноль: {tot} (фьючерсы {fut}, спот {spot}).",
        "Спокойные сутки: {tot} — фьючерсы {fut}, спот {spot}.",
        "Ночь без больших движений по счёту: {tot}."]
MARKET = ["Биткоин {btc} ({chg}), настроение рынка — {fng_ru} ({fng}).",
          "Рынок: биткоин {btc}, за сутки {chg}; индекс страха и жадности {fng} — {fng_ru}.",
          "Биткоин стоит {btc} ({chg} за сутки), на рынке {fng_ru}.",
          "BTC {btc}, {chg} за сутки. Настроение — {fng_ru}, {fng} из 100."]
ACCOUNT = ["Счёт {eq}, с 22.10 {since}.", "Реальный счёт — {eq} ({since} с 22.10).", "На счёте {eq}; результат с 22.10 — {since}.",
           "Реальный счёт CRYPTO NOVA AI: {eq}, с начала {since}."]
ACCOUNT_OWNER = ["Твой счёт {eq}, с 22.10 {since}; позиций открыто: {open}.", "На твоём счёте {eq} ({since} с 22.10), открыто позиций: {open}.",
                 "Счёт {eq}, с 22.10 {since}. В работе позиций: {open}."]
DAY = ["Сегодня: {ev}.", "Что важно сегодня: {ev}.", "На сегодня в календаре: {ev}."]


def _fng_en(label) -> str:
    """markets.json may give the label in Russian («крайний страх») or English («Extreme Fear») → the English key"""
    x = str(label or "").strip().lower()
    return next((k for k, v in FNG_RU.items() if v == x), x)


def _load(out: str, name: str) -> dict:
    try:
        d = json.load(open(os.path.join(out, name), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _usd(v: float, sign: bool = True) -> str:
    a = abs(float(v))
    if a < 0.005:
        return "$0"
    body = (f"{a:,.2f}" if a < 10 else f"{a:,.0f}").replace(",", " ").replace(".", ",")
    return ("−" if v < 0 else "+" if sign else "") + "$" + body


def _pct(v: float) -> str:
    return ("−" if v < 0 else "+") + f"{abs(float(v)):.1f}".replace(".", ",") + "%"


def _change(curve, now: float):
    pts = sorted((float(t), float(v)) for t, v in curve or [] if v is not None)
    if not pts:
        return None
    base = next((v for t, v in reversed(pts) if t <= now - 86_400_000), pts[0][1])
    return pts[-1][1] - base


def facts(out: str, now: int) -> dict | None:
    """the numbers of the report; None when the real account's file is missing or stale"""
    r = _load(out, "public_real.json")
    if not r or r.get("stale") or r.get("equity_usd") is None:
        return None
    tot = _change(r.get("curve"), now)
    fut = _change(r.get("fut_curve"), now)
    if tot is None:
        tot = float(r.get("today_usd") or 0)
    m = _load(out, "markets.json")
    btc = next((t for t in m.get("top") or [] if isinstance(t, dict) and t.get("symbol") == "BTC"), {})
    f = m.get("fng") or {}
    ev = []
    try:
        from . import macro_calendar
        k = dt.datetime.fromtimestamp(now / 1000, KYIV).date()
        for e in macro_calendar.upcoming(now, 1.2):
            loc = dt.datetime.fromtimestamp(e["time"] / 1000, KYIV)
            if (e.get("importance") or 0) >= 2 and loc.date() == k and e["time"] >= now:
                ev.append(f"{e['name']} в {loc:%H:%M} — возможны резкие движения")
    except Exception:  # noqa: BLE001
        ev = []
    return {"total": tot, "futures": fut, "spot": (tot - fut) if fut is not None else None, "equity": float(r["equity_usd"]),
            "since_usd": float(r.get("pnl_usd") or 0), "since_pct": float(r.get("pnl_pct") or 0),
            "open": len([o for o in r.get("futures") or [] if isinstance(o, dict)]),
            "btc": btc.get("price"), "btc_chg": btc.get("chg24"), "fng": f.get("value"),
            "fng_ru": FNG_RU.get(str(f.get("label") or "").lower(), f.get("label") or ""), "fng_label": _fng_en(f.get("label")), "event": ev[0] if ev else None}


def _hist(conn) -> list[dict]:
    if conn is None:
        return []
    conn.execute("CREATE TABLE IF NOT EXISTS morning_hist(day TEXT PRIMARY KEY, picks TEXT)")
    return [json.loads(p) for (p,) in conn.execute("SELECT picks FROM morning_hist ORDER BY day DESC LIMIT ?", (MEMORY_DAYS,))]


def _pick(pool: list, key: str, day: str, hist: list[dict]) -> int:
    """the day's phrasing of one part: deterministic for the day, never one used on the last MEMORY_DAYS days"""
    used = {h.get(key) for h in hist[:max(1, len(pool) - 1)]}     # a small pool remembers fewer days, so a choice always remains
    free = [i for i in range(len(pool)) if i not in used] or list(range(len(pool)))
    return free[int(hashlib.sha1((day + key).encode()).hexdigest(), 16) % len(free)]


LANGS = ("ru", "en", "uk", "el", "es")
# 07.11 (build 57): the same report in the app's other languages — the same picks (every pool has the same length in every language)
L = {"ru": {"titles": TITLES, "up": UP, "down": DOWN, "flat": FLAT, "market": MARKET, "account": ACCOUNT, "owner": ACCOUNT_OWNER,
            "day": DAY, "flat0": "За сутки {tot}.", "nofng": "без оценки", "close": "Это не обещание дохода: система держит свои правила и стопы. Хорошего дня!",
            "fng": FNG_RU},
     "en": {"titles": ["Good morning · CRYPTO NOVA AI system", "CRYPTO NOVA AI morning report", "CRYPTO NOVA AI · how the night went",
                       "Good morning from CRYPTO NOVA AI", "CRYPTO NOVA AI · the last 24 hours", "A new day · CRYPTO NOVA AI system"],
            "up": ["Over 24 hours the system made {tot}: futures {fut}, spot {spot}.", "Up {tot} over the day (futures {fut}, spot {spot}).",
                   "A day in profit: {tot}. Futures {fut}, spot {spot}.", "The day closed in profit: {tot} — futures {fut}, spot {spot}."],
            "down": ["Over 24 hours {tot}: futures {fut}, spot {spot}. The stops are in place, the system follows its plan.",
                     "A day in the red — {tot} (futures {fut}, spot {spot}). Every trade's risk is capped by its stop.",
                     "Down {tot} over the day. Futures {fut}, spot {spot} — the system keeps to its rules.",
                     "Not the best day: {tot} (futures {fut}, spot {spot}). Every position has its stop."],
            "flat": ["An almost flat day: {tot} (futures {fut}, spot {spot}).", "A calm day: {tot} — futures {fut}, spot {spot}.",
                     "No big moves on the account overnight: {tot}."],
            "market": ["Bitcoin {btc} ({chg}), market mood — {fng_ru} ({fng}).", "Market: bitcoin {btc}, {chg} in 24 h; fear & greed {fng} — {fng_ru}.",
                       "Bitcoin is at {btc} ({chg} in 24 h), the market shows {fng_ru}.", "BTC {btc}, {chg} in 24 h. Mood — {fng_ru}, {fng} of 100."],
            "account": ["Account {eq}, {since} since 22 Oct.", "Real account — {eq} ({since} since 22 Oct).", "Account at {eq}; result since 22 Oct — {since}.",
                        "CRYPTO NOVA AI real account: {eq}, {since} since the start."],
            "owner": ["Your account {eq}, {since} since 22 Oct; open positions: {open}.", "Your account is at {eq} ({since} since 22 Oct), open positions: {open}.",
                      "Account {eq}, {since} since 22 Oct. Positions open: {open}."],
            "day": ["Today: {ev}.", "What matters today: {ev}.", "On today's calendar: {ev}."], "flat0": "Over 24 hours: {tot}.", "nofng": "no reading",
            "close": "This is not a promise of income: the system keeps its rules and its stops. Have a good day!",
            "fng": {"extreme fear": "extreme fear", "fear": "fear", "neutral": "neutral", "greed": "greed", "extreme greed": "extreme greed"}},
     "uk": {"titles": ["Доброго ранку · система CRYPTO NOVA AI", "Ранкова доповідь CRYPTO NOVA AI", "CRYPTO NOVA AI · як минула ніч",
                       "Доброго ранку від CRYPTO NOVA AI", "CRYPTO NOVA AI · коротко за добу", "Новий день · система CRYPTO NOVA AI"],
            "up": ["За добу система заробила {tot}: ф'ючерси {fut}, спот {spot}.", "Плюс за добу — {tot} (ф'ючерси {fut}, спот {spot}).",
                   "Доба в плюсі: {tot}. Ф'ючерси {fut}, спот {spot}.", "Доба завершилася в плюсі: {tot} — ф'ючерси {fut}, спот {spot}."],
            "down": ["За добу {tot}: ф'ючерси {fut}, спот {spot}. Стопи на місці, система працює за планом.",
                     "Доба в мінусі — {tot} (ф'ючерси {fut}, спот {spot}). Ризик кожної угоди обмежений стопом.",
                     "Мінус за добу: {tot}. Ф'ючерси {fut}, спот {spot} — система тримається своїх правил.",
                     "Не найкраща доба: {tot} (ф'ючерси {fut}, спот {spot}). У кожної позиції стоїть стоп."],
            "flat": ["Доба майже в нуль: {tot} (ф'ючерси {fut}, спот {spot}).", "Спокійна доба: {tot} — ф'ючерси {fut}, спот {spot}.",
                     "Ніч без великих рухів по рахунку: {tot}."],
            "market": ["Біткоїн {btc} ({chg}), настрій ринку — {fng_ru} ({fng}).", "Ринок: біткоїн {btc}, за добу {chg}; індекс страху й жадібності {fng} — {fng_ru}.",
                       "Біткоїн коштує {btc} ({chg} за добу), на ринку {fng_ru}.", "BTC {btc}, {chg} за добу. Настрій — {fng_ru}, {fng} зі 100."],
            "account": ["Рахунок {eq}, з 22.10 {since}.", "Реальний рахунок — {eq} ({since} з 22.10).", "На рахунку {eq}; результат з 22.10 — {since}.",
                        "Реальний рахунок CRYPTO NOVA AI: {eq}, від початку {since}."],
            "owner": ["Твій рахунок {eq}, з 22.10 {since}; відкритих позицій: {open}.", "На твоєму рахунку {eq} ({since} з 22.10), відкритих позицій: {open}.",
                      "Рахунок {eq}, з 22.10 {since}. У роботі позицій: {open}."],
            "day": ["Сьогодні: {ev}.", "Що важливо сьогодні: {ev}.", "На сьогодні в календарі: {ev}."], "flat0": "За добу {tot}.", "nofng": "без оцінки",
            "close": "Це не обіцянка доходу: система тримається своїх правил і стопів. Гарного дня!",
            "fng": {"extreme fear": "крайній страх", "fear": "страх", "neutral": "нейтрально", "greed": "жадібність", "extreme greed": "крайня жадібність"}},
     "el": {"titles": ["Καλημέρα · σύστημα CRYPTO NOVA AI", "Πρωινή αναφορά CRYPTO NOVA AI", "CRYPTO NOVA AI · πώς πήγε η νύχτα",
                       "Καλημέρα από το CRYPTO NOVA AI", "CRYPTO NOVA AI · το 24ωρο με λίγα λόγια", "Νέα μέρα · σύστημα CRYPTO NOVA AI"],
            "up": ["Σε 24 ώρες το σύστημα κέρδισε {tot}: futures {fut}, spot {spot}.", "Άνοδος {tot} στο 24ωρο (futures {fut}, spot {spot}).",
                   "Κερδοφόρο 24ωρο: {tot}. Futures {fut}, spot {spot}.", "Το 24ωρο έκλεισε θετικά: {tot} — futures {fut}, spot {spot}."],
            "down": ["Σε 24 ώρες {tot}: futures {fut}, spot {spot}. Τα stop είναι στη θέση τους, το σύστημα ακολουθεί το σχέδιο.",
                     "Αρνητικό 24ωρο — {tot} (futures {fut}, spot {spot}). Ο κίνδυνος κάθε συναλλαγής περιορίζεται από το stop.",
                     "Πτώση {tot} στο 24ωρο. Futures {fut}, spot {spot} — το σύστημα τηρεί τους κανόνες του.",
                     "Όχι το καλύτερο 24ωρο: {tot} (futures {fut}, spot {spot}). Κάθε θέση έχει stop."],
            "flat": ["Σχεδόν μηδενικό 24ωρο: {tot} (futures {fut}, spot {spot}).", "Ήρεμο 24ωρο: {tot} — futures {fut}, spot {spot}.",
                     "Χωρίς μεγάλες κινήσεις στον λογαριασμό τη νύχτα: {tot}."],
            "market": ["Bitcoin {btc} ({chg}), διάθεση αγοράς — {fng_ru} ({fng}).", "Αγορά: bitcoin {btc}, {chg} σε 24 ώρες· δείκτης φόβου και απληστίας {fng} — {fng_ru}.",
                       "Το bitcoin είναι στα {btc} ({chg} σε 24 ώρες), στην αγορά επικρατεί {fng_ru}.", "BTC {btc}, {chg} σε 24 ώρες. Διάθεση — {fng_ru}, {fng} στα 100."],
            "account": ["Λογαριασμός {eq}, από 22.10 {since}.", "Πραγματικός λογαριασμός — {eq} ({since} από 22.10).", "Στον λογαριασμό {eq}· αποτέλεσμα από 22.10 — {since}.",
                        "Πραγματικός λογαριασμός CRYPTO NOVA AI: {eq}, από την αρχή {since}."],
            "owner": ["Ο λογαριασμός σου {eq}, από 22.10 {since}· ανοιχτές θέσεις: {open}.", "Στον λογαριασμό σου {eq} ({since} από 22.10), ανοιχτές θέσεις: {open}.",
                      "Λογαριασμός {eq}, από 22.10 {since}. Ανοιχτές θέσεις: {open}."],
            "day": ["Σήμερα: {ev}.", "Τι μετράει σήμερα: {ev}.", "Στο σημερινό ημερολόγιο: {ev}."], "flat0": "Σε 24 ώρες: {tot}.", "nofng": "χωρίς ένδειξη",
            "close": "Δεν είναι υπόσχεση κέρδους: το σύστημα τηρεί τους κανόνες και τα stop του. Καλή σου μέρα!",
            "fng": {"extreme fear": "ακραίος φόβος", "fear": "φόβος", "neutral": "ουδέτερη", "greed": "απληστία", "extreme greed": "ακραία απληστία"}},
     "es": {"titles": ["Buenos días · sistema CRYPTO NOVA AI", "Informe matinal de CRYPTO NOVA AI", "CRYPTO NOVA AI · cómo fue la noche",
                       "Buenos días de parte de CRYPTO NOVA AI", "CRYPTO NOVA AI · las últimas 24 horas", "Un nuevo día · sistema CRYPTO NOVA AI"],
            "up": ["En 24 horas el sistema ganó {tot}: futuros {fut}, spot {spot}.", "Ganancia de {tot} en el día (futuros {fut}, spot {spot}).",
                   "Un día en positivo: {tot}. Futuros {fut}, spot {spot}.", "El día cerró en positivo: {tot} — futuros {fut}, spot {spot}."],
            "down": ["En 24 horas {tot}: futuros {fut}, spot {spot}. Los stops están en su sitio y el sistema sigue su plan.",
                     "Un día en negativo — {tot} (futuros {fut}, spot {spot}). El riesgo de cada operación lo limita su stop.",
                     "Pérdida de {tot} en el día. Futuros {fut}, spot {spot} — el sistema mantiene sus reglas.",
                     "No es el mejor día: {tot} (futuros {fut}, spot {spot}). Cada posición tiene su stop."],
            "flat": ["Un día casi plano: {tot} (futuros {fut}, spot {spot}).", "Un día tranquilo: {tot} — futuros {fut}, spot {spot}.",
                     "Sin grandes movimientos en la cuenta durante la noche: {tot}."],
            "market": ["Bitcoin {btc} ({chg}), ánimo del mercado — {fng_ru} ({fng}).", "Mercado: bitcoin {btc}, {chg} en 24 h; índice de miedo y codicia {fng} — {fng_ru}.",
                       "Bitcoin está en {btc} ({chg} en 24 h), en el mercado hay {fng_ru}.", "BTC {btc}, {chg} en 24 h. Ánimo — {fng_ru}, {fng} de 100."],
            "account": ["Cuenta {eq}, {since} desde el 22.10.", "Cuenta real — {eq} ({since} desde el 22.10).", "En la cuenta {eq}; resultado desde el 22.10 — {since}.",
                        "Cuenta real de CRYPTO NOVA AI: {eq}, {since} desde el inicio."],
            "owner": ["Tu cuenta {eq}, {since} desde el 22.10; posiciones abiertas: {open}.", "Tu cuenta está en {eq} ({since} desde el 22.10), posiciones abiertas: {open}.",
                      "Cuenta {eq}, {since} desde el 22.10. Posiciones abiertas: {open}."],
            "day": ["Hoy: {ev}.", "Lo importante hoy: {ev}.", "En el calendario de hoy: {ev}."], "flat0": "En 24 horas: {tot}.", "nofng": "sin lectura",
            "close": "No es una promesa de ganancias: el sistema mantiene sus reglas y sus stops. ¡Que tengas un buen día!",
            "fng": {"extreme fear": "miedo extremo", "fear": "miedo", "neutral": "neutral", "greed": "codicia", "extreme greed": "codicia extrema"}}}


def _num(v: float, d: int, lang: str) -> str:
    s = f"{abs(float(v)):,.{d}f}"
    return s if lang == "en" else s.replace(",", " ").replace(".", ",")


def _usd_l(v: float, lang: str, sign: bool = True) -> str:
    a = abs(float(v))
    if a < 0.005:
        return "$0"
    return ("−" if v < 0 else "+" if sign else "") + "$" + _num(a, 2 if a < 10 else 0, lang)


def _pct_l(v: float, lang: str) -> str:
    return ("−" if v < 0 else "+") + _num(v, 1, lang) + "%"


def plan(F: dict, day: str, hist: list[dict]) -> dict:
    """the day's choices, the same for every language"""
    picks = {}
    tot = F["total"]
    kind = "up" if tot >= SMALL else "down" if tot <= -SMALL else "flat"
    picks["t"] = _pick(TITLES, "t", day, hist)
    picks["r"] = _pick({"up": UP, "down": DOWN, "flat": FLAT}[kind], "r" + kind, day, hist)
    picks["rk"] = kind
    if F.get("btc"):
        picks["m"] = _pick(MARKET, "m", day, hist)
    picks["a"] = _pick(ACCOUNT, "a", day, hist)
    if F.get("event") and all(h.get("ev") != F["event"] for h in hist[:1]):
        picks["d"] = _pick(DAY, "d", day, hist)
        picks["ev"] = F["event"]
    return picks


def render(F: dict, picks: dict, lang: str = "ru") -> dict:
    P = L[lang]
    tot, fut, spot = F["total"], F["futures"], F["spot"]
    pool = P[picks["rk"]]
    res = pool[picks["r"]].format(tot=_usd_l(tot, lang), fut=_usd_l(fut, lang) if fut is not None else "—",
                                  spot=_usd_l(spot, lang) if spot is not None else "—")
    if fut is None and "{fut}" in pool[picks["r"]]:
        res = P["flat0"].format(tot=_usd_l(tot, lang))
    mk = ""
    if "m" in picks and F.get("btc"):
        lab = P["fng"].get(str(F.get("fng_label") or "").lower()) or (F.get("fng_ru") if lang == "ru" else None) or P["nofng"]
        mk = P["market"][picks["m"]].format(btc="$" + _num(F["btc"], 0, lang), chg=_pct_l(F.get("btc_chg") or 0, lang),
                                            fng=F.get("fng") if F.get("fng") is not None else "—", fng_ru=lab)
    acc = P["account"][picks["a"]].format(eq=_usd_l(F["equity"], lang, False), since=_pct_l(F["since_pct"], lang))
    acc_o = P["owner"][picks["a"] % len(P["owner"])].format(eq=_usd_l(F["equity"], lang, False), since=_usd_l(F["since_usd"], lang), open=F["open"])
    day_line = P["day"][picks["d"]].format(ev=picks["ev"]) if "d" in picks and lang == "ru" else ""   # the calendar's names are Russian
    body = " ".join(x for x in (res, mk, acc) if x)
    owner = " ".join(x for x in (res, mk, acc_o) if x)
    if day_line and len(body) + len(day_line) < 230:
        body += " " + day_line
        owner += " " + day_line
    title = P["titles"][picks["t"]]
    head = ". ".join(p[:1].upper() + p[1:] for p in title.split(" · ")) + "."
    text = " ".join(x for x in (head, res, mk, acc, day_line, P["close"]) if x)
    return {"title": title, "body": body, "owner": {"title": title, "body": owner}, "text": text}


def compose(F: dict, day: str, hist: list[dict], lang: str = "ru") -> dict:
    picks = plan(F, day, hist)
    return {**render(F, picks, lang), "picks": picks}


def synth(text: str, path: str) -> tuple[str | None, list[str]]:
    """the spoken report into path (mp3) with the brief's voices; (voice or None, errors). A failure keeps the old file."""
    errs, tmp = [], path + ".tmp"
    from .voice import azure_to
    v = azure_to(text, path, errs)                     # 10.11: Azure Speech first when its key is set
    if v:
        return v, errs
    try:
        import asyncio
        import edge_tts
        from .voice import RATE, brief_voices, spoken
        for v in brief_voices():
            for attempt in range(2):                   # 10.11: Dmitry / Svetlana are refused now and then — a second try first
                try:
                    async def go(v=v):
                        await edge_tts.Communicate(spoken(text), v, rate=RATE).save(tmp)
                    asyncio.run(go())
                    if os.path.getsize(tmp) > 2000:
                        os.replace(tmp, path)
                        return v, errs
                    errs.append(f"{v}#{attempt + 1}: short audio")
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{v}#{attempt + 1}: {str(e)[:60]}")
                    time.sleep(2)
    except Exception as e:  # noqa: BLE001
        errs.append(str(e)[:80])
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return None, errs


def _synth_lang(text: str, lang: str, path: str) -> tuple[str | None, list[str]]:
    if lang == "ru":
        return synth(text, path)
    try:                                              # the app's multilingual voices (cnq/api/tts.py VOICES + FALLBACK)
        from cnq.api import tts
        data = tts.synth(text, lang)
        if data and len(data) > 2000:
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(path + ".tmp", path)
            return tts.VOICES.get(lang), []
        return None, ["short audio"]
    except Exception as e:  # noqa: BLE001
        return None, [str(e)[:120]]


def file_name(lang: str, ext: str) -> str:
    """morning.json / morning.mp3 for Russian, morning.<lang>.json / .mp3 for the others (the app's own naming)"""
    return f"morning.{ext}" if lang == "ru" else f"morning.{lang}.{ext}"


def build(out: str = "web", conn=None, now: int | None = None, audio: bool = True) -> dict | None:
    now = now or int(time.time() * 1000)
    F = facts(out, now)
    if not F:
        return None
    day = dt.datetime.fromtimestamp(now / 1000, KYIV).strftime("%Y-%m-%d")
    picks = plan(F, day, _hist(conn))
    fx = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in F.items()}
    docs = {}
    for lang in [x for x in LANGS if x != "ru"] + ["ru"]:              # Russian last: its file says «all languages written»
        doc = {"day": day, "asof": now, "lang": lang, **render(F, picks, lang), "picks": picks, "facts": fx, "audio": None}
        if lang == "ru":                   # 09.11 (owner 21:40): the owner's version names what changed on ADA / AVAX / LIT
            try:
                from . import owner_watch
                ow = owner_watch.owner_line(out, now)
            except Exception:  # noqa: BLE001
                ow = None
            if ow:
                doc["owner"] = {**doc["owner"], "body": doc["owner"]["body"] + " " + ow}
        if audio:
            v, errs = _synth_lang(doc["text"], lang, os.path.join(out, file_name(lang, "mp3")))
            doc["audio"], doc["voice"] = (file_name(lang, "mp3") if v else None), v
            if errs:
                doc["audio_error"] = " | ".join(errs)[:300]
        with open(os.path.join(out, file_name(lang, "json")), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        docs[lang] = doc
    if conn is not None:
        conn.execute("INSERT OR REPLACE INTO morning_hist VALUES(?,?)", (day, json.dumps(picks, ensure_ascii=False)))
        conn.commit()
    return docs["ru"]


def ensure(out: str, conn, now: int | None = None, audio: bool = True) -> dict | None:
    """today's report: built once in the morning window (06:40–11:00 Kyiv); the saved one otherwise"""
    now = now or int(time.time() * 1000)
    k = dt.datetime.fromtimestamp(now / 1000, KYIV)
    cur = _load(out, "morning.json")
    if cur.get("day") == k.strftime("%Y-%m-%d"):
        return cur
    if (k.hour, k.minute) < BUILD_FROM or k.hour >= BUILD_TO:
        return None
    return build(out, conn, now, audio)
