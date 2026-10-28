"""«Разбор портфеля» (build 45, owner 22.10): the user's coins checked by CRYPTO NOVA's own rules and data. Deterministic, stdlib.

  review(holdings, data, lang)  → total, a row per coin (share, status, one plain reason), 3–5 recommendations each citing the
                                  rule it comes from, «как выглядел бы этот портфель по модели CRYPTO NOVA», the disclaimer
  Data(web_dir)                 the four files of the system, re-read when they change:
      web/ideas.json      «Что брать на спот»: status (можно брать / добрать / небольшой суммой / ждём / не брать), its reason
                          (dilution, overheating, ATH, volume, meme desk …) and today's price
      web/quality.json    the altcoin quality check (place among ~89 alts)
      web/altseason.json  the «ядро + альтсезон» model (target shares)
      web/portfolio.json  «CRYPTO NOVA портфель» (its rules and today's shares)
      web/markets.json    prices of the top coins (a price for a coin the table does not rate)
  message(code, lang)           the sentence the app shows for an error code
  Limiter                       per-user request limits, in memory

No price predictions, no «купите X», no promises; a recommendation is a rule of the system applied to the user's numbers.
Nothing is stored: the holdings live only in this call."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time

from cnq.agents.i18n import DEFAULT, norm_lang

CORE = ("BTC", "ETH", "SOL")
CORE_PLAN = {"BTC": 30.0, "ETH": 10.0, "SOL": 5.0}        # web/portfolio.json ideas rules_ru: «Ядро — BTC 30%, ETH 10%, SOL 5%»
CORE_PLAN_SUM = 45.0
ALT_MAX, SMALL_MAX, SMALL_COUNT, CASH_MIN, COINS_MAX = 7.5, 2.5, 3, 10.0, 8
STABLES = {"USDT", "USDC", "FDUSD", "DAI", "USDE", "TUSD", "PYUSD", "USD1", "RLUSD", "BUSD", "USDD", "USDP", "USDS", "GUSD", "USD",
           "LUSD", "FRAX", "USDG", "USDQ"}
STATUSES = ("buy", "add", "small", "wait", "avoid", "cash", "unknown")


# ------------------------------------------------------------------------------------------------ texts (5 languages)
def _L(ru, en, uk, el, es):
    return {"ru": ru, "en": en, "uk": uk, "el": el, "es": es}


SRC = {
    "ideas": _L("таблица «Что брать на спот»", "the “What to buy on spot” table", "таблиця «Що брати на спот»",
                "πίνακας «Τι να αγοράσω στο spot»", "la tabla «Qué comprar en spot»"),
    "pf": _L("CRYPTO NOVA портфель", "CRYPTO NOVA portfolio", "Портфель CRYPTO NOVA", "Χαρτοφυλάκιο CRYPTO NOVA", "Cartera CRYPTO NOVA"),
    "real": _L("спот реального счёта CRYPTO NOVA", "the spot part of CRYPTO NOVA's real account", "спот реального рахунку CRYPTO NOVA",
               "το spot του πραγματικού λογαριασμού του CRYPTO NOVA", "el spot de la cuenta real de CRYPTO NOVA"),
    "alt": _L("модель «ядро + альтсезон»", "the “core + altseason” model", "модель «ядро + альтсезон»",
              "μοντέλο «πυρήνας + altseason»", "modelo «núcleo + altseason»"),
    "q": _L("оценка качества альткоинов", "the altcoin quality check", "анкета якості альтів", "έλεγχος ποιότητας altcoins",
            "la ficha de calidad de altcoins"),
}

STATUS_LABEL = {
    "buy": _L("подходит для покупки", "worth buying", "можна брати", "για αγορά", "para comprar"),
    "add": _L("можно докупить", "worth adding to", "можна докупити", "για συμπλήρωση", "para ampliar"),
    "small": _L("только небольшой суммой", "small amount only", "невеликою сумою", "μόνο μικρό ποσό", "solo poca cantidad"),
    "wait": _L("пока ждём", "wait", "чекаємо", "αναμονή", "esperar"),
    "avoid": _L("не покупаем", "avoid", "не брати", "αποφυγή", "evitar"),
    "cash": _L("доллары", "dollars", "долари", "δολάρια", "dólares"),
    "unknown": _L("нет оценки", "not rated", "немає оцінки", "χωρίς αξιολόγηση", "sin evaluar"),
}

# the table's reason (web/ideas.json why_ru, written by cnq/agents/spot_ideas.py) → its key, its numbers and the same sentence in
# the other languages; the Russian text is shown as it is. The keys also tell which check decided (flag).
REASONS: list[tuple[str, str, str | None, dict]] = [
    ("vol_low", r"^Оборот (?P<v>\$[\d.,]+\s?[KMB]?) в сутки — цену легко сдвинуть$", "vol", _L(
        None, "Trades only {v} a day — the price is easy to push", "Оборот лише {v} на добу — ціну легко зсунути",
        "Όγκος μόλις {v} την ημέρα — η τιμή μετακινείται εύκολα", "Solo {v} de volumen al día: el precio se mueve fácilmente")),
    ("vol_buy", r"^Оборот (?P<v>\$[\d.,]+\s?[KMB]?) в сутки — для покупки мало$", "vol", _L(
        None, "Trades {v} a day — too little to buy", "Оборот {v} на добу — для купівлі замало",
        "Όγκος {v} την ημέρα — λίγος για αγορά", "Volumen de {v} al día: poco para comprar")),
    ("buy_mid", r"^Анкета (?P<r>\d+), консультант (?P<c>\d+), тренд вверх, BTC бычий — можно брать$", None, _L(
        None, "Rating {r}, advisor {c}, uptrend, BTC bullish — worth buying", "Анкета {r}, консультант {c}, тренд угору, BTC бичачий — можна брати",
        "Βαθμολογία {r}, σύμβουλος {c}, ανοδική τάση, BTC ανοδικό — για αγορά",
        "Puntuación {r}, asesor {c}, tendencia al alza, BTC alcista: para comprar")),
    ("ath", r"^(?P<p>[−-]?\d+%) от исторического максимума — монета «мёртвая», не берём$", "ath", _L(
        None, "{p} from its all-time high — a “dead” coin, we don't buy", "{p} від історичного максимуму — монета «мертва», не беремо",
        "{p} από το ιστορικό υψηλό — «νεκρό» νόμισμα, δεν αγοράζουμε", "{p} desde su máximo histórico: moneda «muerta», no compramos")),
    ("dilution", r"^В обращении ~(?P<p>\d+)% токенов — впереди разлоки, ждём$", "dilution", _L(
        None, "Only ~{p}% of tokens in circulation — unlocks ahead, we wait", "В обігу ~{p}% токенів — попереду розлоки, чекаємо",
        "Σε κυκλοφορία μόνο ~{p}% των tokens — έρχονται ξεκλειδώματα, περιμένουμε",
        "Solo ~{p}% de los tokens en circulación: vienen desbloqueos, esperamos")),
    ("dip_ok", r"^[Пп]росадка (?P<p>[−-]?[\d.]+%) от максимума за 30 дней(, проверки пройдены)?$", None, _L(
        None, "Down {p} from its 30-day high, checks passed", "Просідання {p} від максимуму за 30 днів, перевірки пройдено",
        "Πτώση {p} από το υψηλό 30 ημερών, οι έλεγχοι πέρασαν", "Caída de {p} desde el máximo de 30 días, controles superados")),
    ("rating", r"^Анкета (?P<r>\d+|—)/100 ниже порога (?P<m>\d+) — ждём$", "rating", _L(
        None, "Rating {r}/100, below the {m} bar — we wait", "Анкета {r}/100 нижче порогу {m} — чекаємо",
        "Βαθμολογία {r}/100, κάτω από το όριο {m} — περιμένουμε", "Puntuación {r}/100, por debajo del umbral {m}: esperamos")),
    ("desk_okx", r"^Мем-деск: нет на OKX и Hyperliquid, пул Solana меньше (?P<v>\$[\d.,]+\s?[KMB]?)$", "desk", _L(
        None, "Meme desk: not on OKX or Hyperliquid, Solana pool under {v}", "Мем-деск: немає на OKX і Hyperliquid, пул Solana менше {v}",
        "Meme desk: δεν είναι σε OKX και Hyperliquid, pool Solana κάτω από {v}",
        "Meme desk: no está en OKX ni Hyperliquid, pool de Solana menor de {v}")),
    ("desk_vol", r"^Мем-деск: мало оборота: (?P<v>\$[\d.,]+\s?[KMB]?) в сутки — цену легко сдвинуть$", "vol", _L(
        None, "Meme desk: low volume, {v} a day — the price is easy to push", "Мем-деск: малий оборот, {v} на добу — ціну легко зсунути",
        "Meme desk: χαμηλός όγκος, {v} την ημέρα — η τιμή μετακινείται εύκολα",
        "Meme desk: poco volumen, {v} al día: el precio se mueve fácilmente")),
    ("desk_whale", r"^Мем-деск: красный флаг: RugCheck — один кошелёк держит большую долю$", "desk", _L(
        None, "Meme desk red flag: one wallet holds a large share", "Червоний прапорець мем-деску: один гаманець тримає велику частку",
        "Κόκκινη σημαία meme desk: ένα πορτοφόλι κρατά μεγάλο μερίδιο", "Alerta del meme desk: una sola cartera tiene una gran parte")),
    ("desk_top10", r"^Мем-деск: красный флаг: топ-10 кошельков держат (?P<p>\d+%)$", "desk", _L(
        None, "Meme desk red flag: the top 10 wallets hold {p}", "Червоний прапорець мем-деску: топ-10 гаманців тримають {p}",
        "Κόκκινη σημαία meme desk: τα 10 μεγαλύτερα πορτοφόλια κρατούν {p}", "Alerta del meme desk: las 10 mayores carteras tienen {p}")),
    ("desk_mint", r"^Мем-деск: красный флаг: RugCheck — могут допечатать токены$", "desk", _L(
        None, "Meme desk red flag: more tokens can be minted", "Червоний прапорець мем-деску: можуть допечатати токени",
        "Κόκκινη σημαία meme desk: μπορούν να κοπούν κι άλλα tokens", "Alerta del meme desk: pueden emitir más tokens")),
    ("desk", r"^Мем-деск: .*$", "desk", _L(
        None, "Meme desk: did not pass its checks", "Мем-деск: перевірки не пройдено", "Meme desk: δεν πέρασε τους ελέγχους",
        "Meme desk: no superó los controles")),
    ("cex", r"^Токен биржи — наши портфели такие не берут$", "cex", _L(
        None, "An exchange's own token — our portfolios don't take these", "Токен біржі — наші портфелі такі не беруть",
        "Token ανταλλακτηρίου — τα χαρτοφυλάκιά μας δεν τα παίρνουν", "Token de un exchange: nuestras carteras no los compran")),
    ("core_buy", r"^Ядро, анкета (?P<r>\d+)/100, BTC бычий — можно брать частями$", None, _L(
        None, "Core coin, rating {r}/100, BTC bullish — worth buying in parts", "Ядро, анкета {r}/100, BTC бичачий — можна брати частинами",
        "Πυρήνας, βαθμολογία {r}/100, BTC ανοδικό — για αγορά σε δόσεις", "Núcleo, puntuación {r}/100, BTC alcista: para comprar por partes")),
    ("core_bear", r"^Ядро, анкета (?P<r>\d+)/100; BTC медвежий — брать только частями$", "bear", _L(
        None, "Core coin, rating {r}/100; BTC bearish — buy only in parts", "Ядро, анкета {r}/100; BTC ведмежий — брати лише частинами",
        "Πυρήνας, βαθμολογία {r}/100· BTC πτωτικό — μόνο σε δόσεις", "Núcleo, puntuación {r}/100; BTC bajista: solo por partes")),
    ("meme_wait", r"^(?P<p>[−+-]?[\d.]+%) от максимума за 30 дней — ждём просадку от (?P<n>[−-]?\d+%)$", None, _L(
        None, "{p} from its 30-day high — we wait for a drop of {n}", "{p} від максимуму за 30 днів — чекаємо просідання від {n}",
        "{p} από το υψηλό 30 ημερών — περιμένουμε πτώση από {n}", "{p} desde el máximo de 30 días: esperamos una caída de {n}")),
    ("high_wait", r"^Высокий риск без просадки: (?P<p>[−+-]?[\d.]+%) от максимума 30 дней, ждём (?P<n>[−-]?\d+%)$", None, _L(
        None, "High risk without a dip: {p} from the 30-day high, we wait for {n}",
        "Високий ризик без просідання: {p} від максимуму 30 днів, чекаємо {n}",
        "Υψηλός κίνδυνος χωρίς πτώση: {p} από το υψηλό 30 ημερών, περιμένουμε {n}",
        "Alto riesgo sin caída: {p} desde el máximo de 30 días, esperamos {n}")),
    ("cons_low", r"^Консультант (?P<c>\d+)/100 ниже порога (?P<m>\d+) — ждём$", "consultant", _L(
        None, "Advisor {c}/100, below the {m} bar — we wait", "Консультант {c}/100 нижче порогу {m} — чекаємо",
        "Σύμβουλος {c}/100, κάτω από το όριο {m} — περιμένουμε", "Asesor {c}/100, por debajo del umbral {m}: esperamos")),
    ("add_support", r"^Уже в портфеле, цена (в зоне поддержки|у поддержки) — можно добрать$", None, _L(
        None, "Already in our portfolio, price near support — worth adding to", "Уже в портфелі, ціна біля підтримки — можна докупити",
        "Ήδη στο χαρτοφυλάκιο, τιμή κοντά στη στήριξη — για συμπλήρωση", "Ya está en la cartera, precio cerca del soporte: para ampliar")),
    ("add_plan", r"^Портфель «.+» сам добирает до плановой доли$", None, _L(
        None, "Our portfolio is topping it up to its planned share", "Наш портфель сам докуповує до планової частки",
        "Το χαρτοφυλάκιό μας τη συμπληρώνει ως το προγραμματισμένο μερίδιο", "Nuestra cartera la completa hasta su peso previsto")),
    ("cons_none", r"^Консультант не выделил — тренд не подтверждён$", "consultant", _L(
        None, "The advisor didn't pick it — trend not confirmed", "Консультант не виділив — тренд не підтверджено",
        "Ο σύμβουλος δεν το επέλεξε — η τάση δεν επιβεβαιώθηκε", "El asesor no la eligió: tendencia sin confirmar")),
    ("meme_blind", r"^Мем без проверки мем-деска — не берём вслепую$", "desk", _L(
        None, "A meme not checked by the meme desk — we don't buy blind", "Мем без перевірки мем-деску — наосліп не беремо",
        "Meme χωρίς έλεγχο από το meme desk — δεν αγοράζουμε στα τυφλά", "Meme sin revisar por el meme desk: no compramos a ciegas")),
    ("overheat", r"^Уже (?P<p>[+]?[\d.]+%) за (?P<d>\d+) дней — не догоняем, ждём отката$", "overheat", _L(
        None, "Already {p} in {d} days — we don't chase it, we wait for a pullback", "Уже {p} за {d} днів — не доганяємо, чекаємо відкату",
        "Ήδη {p} σε {d} ημέρες — δεν το κυνηγάμε, περιμένουμε διόρθωση", "Ya {p} en {d} días: no lo perseguimos, esperamos un retroceso")),
    ("overheat_core", r"^Рывок (?P<p>[−+-]?[\d.]+%) — ядро тоже не догоняем, ждём$", "overheat", _L(
        None, "A spike of {p} — we don't chase even the core, we wait", "Ривок {p} — навіть ядро не доганяємо, чекаємо",
        "Άλμα {p} — ούτε τον πυρήνα κυνηγάμε, περιμένουμε", "Subida de {p}: ni el núcleo perseguimos, esperamos")),
    ("stop", r"^Наш портфель «.+» у стопа \((?P<p>[−+-]?[\d.]+%)\) — не добираем$", "stop", _L(
        None, "Our portfolio holds it near its stop ({p}) — no adding", "Наш портфель тримає її біля стопу ({p}) — не докуповуємо",
        "Το χαρτοφυλάκιό μας το κρατά κοντά στο stop ({p}) — δεν προσθέτουμε", "Nuestra cartera la tiene cerca del stop ({p}): no ampliamos")),
    ("bear", r"^BTC в медвежьем режиме — покупаем только ядро$", "bear", _L(
        None, "BTC in a bear phase — we buy only the core", "BTC у ведмежому режимі — купуємо лише ядро",
        "BTC σε πτωτική φάση — αγοράζουμε μόνο τον πυρήνα", "BTC en fase bajista: solo compramos el núcleo")),
    ("no_rating", r"^Анкеты пока нет — ждём данных$", "rating", _L(
        None, "No rating yet — we wait for data", "Анкети поки немає — чекаємо даних", "Δεν υπάρχει ακόμη βαθμολογία — περιμένουμε δεδομένα",
        "Aún sin puntuación: esperamos datos")),
    ("trend200", r"^Ниже, чем 200 дней назад \((?P<p>[−+-]?[\d.]+%)\) — тренда нет$", None, _L(
        None, "Lower than 200 days ago ({p}) — no trend", "Нижче, ніж 200 днів тому ({p}) — тренду немає",
        "Χαμηλότερα από πριν 200 ημέρες ({p}) — καμία τάση", "Más bajo que hace 200 días ({p}): sin tendencia")),
    ("trim", r"^У точки фиксации в нашем портфеле — не добираем$", None, _L(
        None, "At the profit-taking point in our portfolio — no adding", "Біля точки фіксації в нашому портфелі — не докуповуємо",
        "Στο σημείο κατοχύρωσης κέρδους στο χαρτοφυλάκιό μας — δεν προσθέτουμε",
        "En el punto de toma de beneficios de nuestra cartera: no ampliamos")),
    ("no_hi30", r"^Нет данных о максимуме за 30 дней — ждём$", None, _L(
        None, "No 30-day high data — we wait", "Немає даних про максимум за 30 днів — чекаємо",
        "Δεν υπάρχουν δεδομένα για το υψηλό 30 ημερών — περιμένουμε", "Sin datos del máximo de 30 días: esperamos")),
    ("high_small", r"^Высокий риск, просадка (?P<p>[−+-]?[\d.]+%) от максимума 30 дней — небольшой суммой$", None, _L(
        None, "High risk, down {p} from the 30-day high — a small amount only",
        "Високий ризик, просідання {p} від максимуму 30 днів — невеликою сумою",
        "Υψηλός κίνδυνος, πτώση {p} από το υψηλό 30 ημερών — μόνο μικρό ποσό",
        "Alto riesgo, caída de {p} desde el máximo de 30 días: solo poca cantidad")),
    ("unlock", r"^(Сегодня|Через \d+ дн\.) \(?[\d.-]*\)? ?разлок (?P<p>[\d.]+)% монет в обращении — до разлока не берём.*$", "dilution", _L(
        None, "An unlock of {p}% of circulation is coming — we don't buy before it", "Попереду розлок {p}% монет в обігу — до розлоку не беремо",
        "Έρχεται ξεκλείδωμα {p}% της κυκλοφορίας — δεν αγοράζουμε πριν από αυτό",
        "Se acerca un desbloqueo del {p}% en circulación: no compramos antes")),
    ("dip_none", r"^проверки пройдены, просадки нет — ждём$", None, _L(
        None, "Checks passed, no dip yet — we wait", "Перевірки пройдено, просідання немає — чекаємо",
        "Οι έλεγχοι πέρασαν, χωρίς πτώση ακόμη — περιμένουμε", "Controles superados, aún sin caída: esperamos")),
]
_REASONS = [(k, re.compile(p), f, tr) for k, p, f, tr in REASONS]

# 28.10 (build 46 QA): the Russian reasons in plain words too (the table's own sentence was jargon: «Анкета 32/100 ниже порога 40 —
# ждём», «Ядро, анкета 54/100, BTC бычий — можно брать частями»); the numbers are the table's own
REASONS_RU = {
    "vol_low": "Торгуется всего на {v} в сутки — цену легко сдвинуть",
    "vol_buy": "Торгуется на {v} в сутки — для покупки этого мало",
    "buy_mid": "Оценка проекта {r} из 100, оценка консультанта {c} из 100, тренд вверх, биткоин в росте — проверки пройдены",
    "ath": "Цена {p} от исторического максимума — такие монеты система не покупает",
    "dilution": "В обращении только около {p}% токенов — впереди разлоки, пока не покупаем",
    "dip_ok": "Цена {p} от максимума за 30 дней, проверки пройдены",
    "rating": "Оценка проекта {r} из 100 — ниже минимума системы ({m}), пока не покупаем",
    "desk_okx": "Мем-монета: нет на OKX и Hyperliquid, а пул на Solana меньше {v} — не покупаем",
    "desk_vol": "Мем-монета торгуется всего на {v} в сутки — цену легко сдвинуть",
    "desk_whale": "Мем-монета: один кошелёк держит большую долю — это красный флаг",
    "desk_top10": "Мем-монета: 10 крупнейших кошельков держат {p} — это красный флаг",
    "desk_mint": "Мем-монета: токены могут допечатать — это красный флаг",
    "desk": "Мем-монета не прошла проверки системы",
    "cex": "Токен биржи — такие система не покупает",
    "core_buy": "Монета ядра, оценка проекта {r} из 100, биткоин в росте — система покупает её частями",
    "core_bear": "Монета ядра, оценка проекта {r} из 100; биткоин в спаде — только частями",
    "meme_wait": "Цена {p} от максимума за 30 дней — система ждёт снижения от {n}",
    "high_wait": "Рискованная монета без просадки: {p} от максимума за 30 дней, система ждёт {n}",
    "cons_low": "Оценка консультанта {c} из 100 — ниже минимума системы ({m}), пока не покупаем",
    "add_support": "Уже в портфеле, цена у поддержки — система может докупить",
    "add_plan": "Портфель системы сам докупает её до плановой доли",
    "cons_none": "Консультант системы её не выделил — тренд не подтверждён",
    "meme_blind": "Мем-монета без проверки — вслепую система не покупает",
    "overheat": "Уже {p} за {d} дней — не догоняем, ждём отката",
    "overheat_core": "Рывок {p} — даже монеты ядра не догоняем, ждём",
    "stop": "В портфеле системы монета у стопа ({p}) — не докупаем",
    "bear": "Биткоин в спаде — система покупает только ядро",
    "no_rating": "Оценки проекта пока нет — ждём данных",
    "trend200": "Цена ниже, чем 200 дней назад ({p}) — тренда нет",
    "trim": "В портфеле системы монета у точки фиксации прибыли — не докупаем",
    "no_hi30": "Нет данных о максимуме за 30 дней — ждём",
    "high_small": "Рискованная монета, {p} от максимума за 30 дней — только небольшой суммой",
    "unlock": "Скоро разлок {p}% монет в обращении — до него не покупаем",
    "dip_none": "Проверки пройдены, но просадки нет — ждём",
}

GENERIC = {
    "buy": _L("Проходит проверки таблицы", "Passes the table's checks", "Проходить перевірки таблиці", "Περνά τους ελέγχους του πίνακα",
              "Supera los controles de la tabla"),
    "add": _L("Проходит проверки — система может докупить", "Passes the checks — worth adding to", "Проходить перевірки — можна докупити",
              "Περνά τους ελέγχους — για συμπλήρωση", "Supera los controles: para ampliar"),
    "small": _L("По правилам — только небольшой суммой", "By the rules — a small amount only", "За правилами — лише невеликою сумою",
                "Με τους κανόνες — μόνο μικρό ποσό", "Según las reglas: solo poca cantidad"),
    "wait": _L("Не все проверки пройдены — ждём", "Not all checks passed yet — we wait", "Не всі перевірки пройдено — чекаємо",
               "Δεν πέρασαν όλοι οι έλεγχοι — περιμένουμε", "Aún no supera todos los controles: esperamos"),
    "avoid": _L("Не проходит проверки таблицы — не берём", "Fails the table's checks — we don't take it",
                "Не проходить перевірки таблиці — не беремо", "Δεν περνά τους ελέγχους του πίνακα — δεν το παίρνουμε",
                "No supera los controles de la tabla: no la compramos"),
    "cash": _L("Запас в долларах — портфель CRYPTO NOVA держит от 10%", "Dollar reserve — the CRYPTO NOVA portfolio keeps 10% or more",
               "Запас у доларах — портфель CRYPTO NOVA тримає від 10%", "Αποθεματικό σε δολάρια — το χαρτοφυλάκιο CRYPTO NOVA κρατά από 10%",
               "Reserva en dólares: la cartera CRYPTO NOVA mantiene un 10% o más"),
    "market": _L("Нет в таблице CRYPTO NOVA — система эту монету не проверяет",
                 "Not in the CRYPTO NOVA table — the system doesn't check this coin",
                 "Немає в таблиці CRYPTO NOVA — система цю монету не перевіряє",
                 "Δεν είναι στον πίνακα CRYPTO NOVA — το σύστημα δεν ελέγχει αυτό το νόμισμα",
                 "No está en la tabla CRYPTO NOVA: el sistema no revisa esta moneda"),
    "unknown": _L("Нет в данных CRYPTO NOVA — ни цены, ни проверок", "Not in CRYPTO NOVA's data — no price, no checks",
                  "Немає в даних CRYPTO NOVA — ні ціни, ні перевірок", "Δεν υπάρχει στα δεδομένα του CRYPTO NOVA — ούτε τιμή ούτε έλεγχοι",
                  "No está en los datos de CRYPTO NOVA: sin precio ni controles"),
    "unknown_file": _L("Нет в данных CRYPTO NOVA — цена из вашего файла, проверок нет",
                       "Not in CRYPTO NOVA's data — the price comes from your file, no checks",
                       "Немає в даних CRYPTO NOVA — ціна з вашого файлу, перевірок немає",
                       "Δεν υπάρχει στα δεδομένα του CRYPTO NOVA — η τιμή είναι από το αρχείο σας, χωρίς ελέγχους",
                       "No está en los datos de CRYPTO NOVA: el precio sale de tu archivo, sin controles"),
}

RULE = {   # the rule a recommendation comes from: «its name» · where it is written
    "avoid": ("ideas", _L("«Не брать»", "“Avoid”", "«Не брати»", "«Αποφυγή»", "«Evitar»")),
    "core": ("pf", _L("«Ядро — BTC 30%, ETH 10%, SOL 5%»", "“Core: BTC 30%, ETH 10%, SOL 5%”", "«Ядро — BTC 30%, ETH 10%, SOL 5%»",
                      "«Πυρήνας: BTC 30%, ETH 10%, SOL 5%»", "«Núcleo: BTC 30%, ETH 10%, SOL 5%»")),
    "core_high": ("pf", _L("«Лишнее ядро — если доля больше плана в 1.5 раза»", "“Excess core: a share over 1.5× the plan”",
                           "«Зайве ядро — якщо частка більша за план у 1.5 раза»", "«Πλεόνασμα πυρήνα: μερίδιο πάνω από 1.5× του πλάνου»",
                           "«Exceso de núcleo: un peso de más de 1.5× el plan»")),
    "conc": ("pf", _L("«Монета вне ядра — до 7.5%, рискованная — до 2.5%»", "“A non-core coin: up to 7.5%, a risky one: up to 2.5%”",
                      "«Монета поза ядром — до 7.5%, ризикова — до 2.5%»", "«Νόμισμα εκτός πυρήνα: έως 7.5%, ριψοκίνδυνο: έως 2.5%»",
                      "«Moneda fuera del núcleo: hasta 7.5%; arriesgada: hasta 2.5%»")),
    "risky": ("ideas", _L("«Можно на небольшую сумму»", "“A small amount only”", "«Можна на невелику суму»", "«Μόνο μικρό ποσό»",
                          "«Solo poca cantidad»")),
    "cash": ("pf", _L("«Кэш не меньше 10%»", "“Cash at least 10%”", "«Кеш не менше 10%»", "«Μετρητά τουλάχιστον 10%»",
                      "«Efectivo, al menos un 10%»")),
    "overheat": ("ideas", _L("«Ждём: рывок вверх»", "“Wait: a sharp spike”", "«Чекаємо: різкий ривок угору»",
                             "«Αναμονή: απότομο άλμα»", "«Esperar: subida brusca»")),
    "dilution": ("ideas", _L("«Ждём: много токенов впереди»", "“Wait: many tokens still to come”", "«Чекаємо: багато токенів попереду»",
                             "«Αναμονή: πολλά tokens ακόμη να έρθουν»", "«Esperar: faltan muchos tokens por salir»")),
    "cex": ("ideas", _L("«Токены бирж не берём»", "“No exchange tokens”", "«Токени бірж не беремо»", "«Όχι tokens ανταλλακτηρίων»",
                        "«Sin tokens de exchanges»")),
    "unknown": ("ideas", _L("«Только проверенные монеты»", "“Only checked coins”", "«Лише перевірені монети»",
                            "«Μόνο ελεγμένα νομίσματα»", "«Solo monedas revisadas»")),
    "count": ("pf", _L("«Не больше 8 монет вместе с ядром»", "“No more than 8 coins, core included”", "«Не більше 8 монет разом із ядром»",
                       "«Έως 8 νομίσματα μαζί με τον πυρήνα»", "«No más de 8 monedas con el núcleo»")),
    "quality": ("q", _L("«Нижняя треть оценки качества»", "“The bottom third of the quality check”", "«Нижня третина анкети якості»",
                        "«Το κάτω τρίτο του ελέγχου ποιότητας»", "«El tercio inferior de la ficha de calidad»")),
    "buy": ("ideas", _L("«Можно брать»", "“Worth buying”", "«Можна брати»", "«Για αγορά»", "«Para comprar»")),
}

REC = {
    "avoid": _L(
        "Статус «не брать» сейчас у {coins} — это {you} портфеля. Пример — {top}: {reason}. Портфель CRYPTO NOVA такие монеты не держит и продаёт на следующем прогоне.",
        "“Avoid” is now the status of {coins} — {you} of the portfolio. For example, {top}: {reason}. The CRYPTO NOVA portfolio doesn't hold such coins and sells them on its next run.",
        "Статус «не брати» зараз у {coins} — це {you} портфеля. Приклад — {top}: {reason}. Портфель CRYPTO NOVA такі монети не тримає і продає на наступному прогоні.",
        "Την ένδειξη «αποφυγή» έχουν τώρα: {coins} — {you} του χαρτοφυλακίου. Παράδειγμα — {top}: {reason}. Το χαρτοφυλάκιο CRYPTO NOVA δεν κρατά τέτοια νομίσματα και τα πουλά στον επόμενο κύκλο.",
        "Con el estado «evitar» están ahora: {coins}, el {you} de la cartera. Por ejemplo, {top}: {reason}. La cartera CRYPTO NOVA no tiene estas monedas y las vende en su siguiente pasada."),
    "avoid1": _L(
        "Статус «не брать» сейчас у {top} — это {you} портфеля: {reason}. Портфель CRYPTO NOVA такие монеты не держит и продаёт на следующем прогоне.",
        "“Avoid” is now the status of {top} — {you} of the portfolio: {reason}. The CRYPTO NOVA portfolio doesn't hold such coins and sells them on its next run.",
        "Статус «не брати» зараз у {top} — це {you} портфеля: {reason}. Портфель CRYPTO NOVA такі монети не тримає і продає на наступному прогоні.",
        "Την ένδειξη «αποφυγή» έχει τώρα το {top} — {you} του χαρτοφυλακίου: {reason}. Το χαρτοφυλάκιο CRYPTO NOVA δεν κρατά τέτοια νομίσματα και τα πουλά στον επόμενο κύκλο.",
        "{top} está ahora en «evitar», el {you} de la cartera: {reason}. La cartera CRYPTO NOVA no tiene estas monedas y las vende en su siguiente pasada."),
    "core_low": _L(
        "Ядро — BTC, ETH и SOL — у вас {you}. В портфеле CRYPTO NOVA ядро по плану 45%, сейчас {model}. Ядро система держит без стопа.",
        "Your core — BTC, ETH and SOL — is {you}. In the CRYPTO NOVA portfolio the core is 45% by plan, {model} now. The system holds the core without a stop.",
        "Ядро — BTC, ETH і SOL — у вас {you}. У портфелі CRYPTO NOVA ядро за планом 45%, зараз {model}. Ядро система тримає без стопу.",
        "Ο πυρήνας σας — BTC, ETH και SOL — είναι {you}. Στο χαρτοφυλάκιο CRYPTO NOVA ο πυρήνας είναι 45% με βάση το πλάνο, τώρα {model}. Το σύστημα κρατά τον πυρήνα χωρίς stop.",
        "Tu núcleo — BTC, ETH y SOL — es el {you}. En la cartera CRYPTO NOVA el núcleo es del 45% según el plan, ahora {model}. El sistema mantiene el núcleo sin stop."),
    "core_high": _L(
        "{coin} — {you} портфеля. В портфеле CRYPTO NOVA план для {coin} — {plan}, и лишнее продают, когда доля больше плановой в 1.5 раза: одна монета не должна решать судьбу всего счёта.",
        "{coin} is {you} of the portfolio. The CRYPTO NOVA portfolio plans {plan} for {coin} and sells the excess when the share passes 1.5× the plan: one coin shouldn't decide the whole account.",
        "{coin} — {you} портфеля. У портфелі CRYPTO NOVA план для {coin} — {plan}, і зайве продають, коли частка більша за планову в 1.5 раза: одна монета не має вирішувати долю всього рахунку.",
        "Το {coin} είναι {you} του χαρτοφυλακίου. Το χαρτοφυλάκιο CRYPTO NOVA προβλέπει {plan} για το {coin} και πουλά το πλεόνασμα όταν το μερίδιο ξεπεράσει το 1.5× του πλάνου: ένα νόμισμα δεν πρέπει να κρίνει όλο τον λογαριασμό.",
        "{coin} es el {you} de la cartera. La cartera CRYPTO NOVA prevé un {plan} para {coin} y vende el exceso cuando el peso pasa de 1.5× el plan: una sola moneda no debe decidir toda la cuenta."),
    "conc": _L(
        "{coins} — {you} портфеля. В портфеле CRYPTO NOVA одна монета вне ядра занимает не больше 7.5%, рискованная — 2.5%: если она сильно упадёт, весь счёт не провалится.",
        "{coins}: {you} of the portfolio. In the CRYPTO NOVA portfolio one coin outside the core takes no more than 7.5%, a risky one 2.5%: if it falls hard, the whole account doesn't sink.",
        "{coins} — {you} портфеля. У портфелі CRYPTO NOVA одна монета поза ядром займає не більше 7.5%, ризикова — 2.5%: якщо вона сильно впаде, весь рахунок не провалиться.",
        "{coins}: {you} του χαρτοφυλακίου. Στο χαρτοφυλάκιο CRYPTO NOVA ένα νόμισμα εκτός πυρήνα πιάνει έως 7.5%, ένα ριψοκίνδυνο έως 2.5%: αν πέσει πολύ, δεν βουλιάζει όλος ο λογαριασμός.",
        "{coins}: el {you} de la cartera. En la cartera CRYPTO NOVA una moneda fuera del núcleo ocupa como mucho un 7.5%, una arriesgada un 2.5%: si cae mucho, no se hunde toda la cuenta."),
    "risky": _L(
        "Мемы и монеты высокого риска — {you} портфеля ({coins}). По правилам CRYPTO NOVA их берут только небольшой суммой: до 2.5% на монету и не больше 3 таких монет.",
        "Memes and high-risk coins are {you} of the portfolio ({coins}). By CRYPTO NOVA's rules they are bought only in small amounts: up to 2.5% per coin and no more than 3 such coins.",
        "Меми та монети високого ризику — {you} портфеля ({coins}). За правилами CRYPTO NOVA їх беруть лише невеликою сумою: до 2.5% на монету і не більше 3 таких монет.",
        "Τα meme και τα νομίσματα υψηλού κινδύνου είναι {you} του χαρτοφυλακίου ({coins}). Με τους κανόνες του CRYPTO NOVA αγοράζονται μόνο σε μικρά ποσά: έως 2.5% ανά νόμισμα και όχι πάνω από 3 τέτοια νομίσματα.",
        "Los memes y las monedas de alto riesgo son el {you} de la cartera ({coins}). Según las reglas de CRYPTO NOVA solo se compran en poca cantidad: hasta un 2.5% por moneda y no más de 3 monedas así."),
    "cash_low": _L(
        "Запас в долларах (USDT, USDC) у вас {you}. Портфель CRYPTO NOVA всегда держит не меньше 10% — чтобы было чем докупить ядро, если оно подешевеет.",
        "Your dollar reserve (USDT, USDC) is {you}. The CRYPTO NOVA portfolio always keeps at least 10% — so there is something to add to the core with if it gets cheaper.",
        "Запас у доларах (USDT, USDC) у вас {you}. Портфель CRYPTO NOVA завжди тримає не менше 10% — щоб було чим докупити ядро, якщо воно подешевшає.",
        "Το αποθεματικό σας σε δολάρια (USDT, USDC) είναι {you}. Το χαρτοφυλάκιο CRYPTO NOVA κρατά πάντα τουλάχιστον 10% — για να υπάρχει κάτι για τον πυρήνα αν φθηνύνει.",
        "Tu reserva en dólares (USDT, USDC) es del {you}. La cartera CRYPTO NOVA siempre mantiene al menos un 10%, para poder ampliar el núcleo si se abarata."),
    "overheat": _L(
        "Резкий рост за последние дни: {coins}. По правилу таблицы после рывка (+40% за 7 дней или +120% за 30) система не докупает, а ждёт отката.",
        "A sharp jump in recent days: {coins}. By the table's rule, after a spike (+40% in 7 days or +120% in 30) the system doesn't add, it waits for a pullback.",
        "Різке зростання за останні дні: {coins}. За правилом таблиці після ривка (+40% за 7 днів або +120% за 30) система не докуповує, а чекає відкату.",
        "Απότομη άνοδος τις τελευταίες ημέρες: {coins}. Με τον κανόνα του πίνακα, μετά από άλμα (+40% σε 7 ημέρες ή +120% σε 30) το σύστημα δεν προσθέτει, περιμένει διόρθωση.",
        "Subida brusca en los últimos días: {coins}. Según la regla de la tabla, tras una subida brusca (+40% en 7 días o +120% en 30) el sistema no amplía: espera un retroceso."),
    "dilution": _L(
        "У {coins} в обращении меньше половины токенов или скоро разлок: новые монеты выходят на рынок и давят на цену. Система такие монеты пока не берёт.",
        "{coins}: less than half of the tokens are in circulation, or an unlock is near — new coins reach the market and weigh on the price. The system doesn't take such coins for now.",
        "У {coins} в обігу менше половини токенів або скоро розлок: нові монети виходять на ринок і тиснуть на ціну. Система такі монети поки не бере.",
        "{coins}: σε κυκλοφορία είναι λιγότερα από τα μισά tokens ή έρχεται ξεκλείδωμα — νέα νομίσματα βγαίνουν στην αγορά και πιέζουν την τιμή. Το σύστημα δεν τα παίρνει προς το παρόν.",
        "{coins}: hay menos de la mitad de los tokens en circulación o se acerca un desbloqueo; las monedas nuevas llegan al mercado y presionan el precio. El sistema no las compra por ahora."),
    "cex": _L(
        "Токен биржи: {coins}. Портфели CRYPTO NOVA такие монеты не берут.",
        "Exchange token: {coins}. CRYPTO NOVA portfolios don't take such coins.",
        "Токен біржі: {coins}. Портфелі CRYPTO NOVA такі монети не беруть.",
        "Token ανταλλακτηρίου: {coins}. Τα χαρτοφυλάκια CRYPTO NOVA δεν παίρνουν τέτοια νομίσματα.",
        "Token de exchange: {coins}. Las carteras CRYPTO NOVA no compran estas monedas."),
    "unknown": _L(
        "Для {coins} ({you}) у CRYPTO NOVA нет оценки: таблица проверяет {n} монет. Без проверки не видно ни разлоков, ни падения от максимума.",
        "CRYPTO NOVA has no rating for {coins} ({you}): the table checks {n} coins. Without a check you can't see unlocks or the fall from the high.",
        "Для {coins} ({you}) у CRYPTO NOVA немає оцінки: таблиця перевіряє {n} монет. Без перевірки не видно ні розлоків, ні падіння від максимуму.",
        "Το CRYPTO NOVA δεν έχει αξιολόγηση για τα {coins} ({you}): ο πίνακας ελέγχει {n} νομίσματα. Χωρίς έλεγχο δεν φαίνονται ούτε ξεκλειδώματα ούτε η πτώση από το υψηλό.",
        "CRYPTO NOVA no tiene evaluación para {coins} ({you}): la tabla revisa {n} monedas. Sin revisión no se ven ni los desbloqueos ni la caída desde el máximo."),
    "count": _L(
        "У вас {count}. В портфеле CRYPTO NOVA их не больше 8 вместе с ядром — так за каждой можно следить.",
        "You hold {count}. The CRYPTO NOVA portfolio holds no more than 8, core included — so each one can be watched.",
        "У вас {count}. У портфелі CRYPTO NOVA їх не більше 8 разом із ядром — так за кожною можна стежити.",
        "Έχετε {count}. Το χαρτοφυλάκιο CRYPTO NOVA κρατά έως 8 μαζί με τον πυρήνα — έτσι παρακολουθείται το καθένα.",
        "Tienes {count}. La cartera CRYPTO NOVA tiene como mucho 8 con el núcleo: así se puede seguir cada una."),
    "quality": _L(
        "В нижней трети оценки качества из {n} альткоинов: {coins}. На истории 2022–2026 лучше были альты с выручкой и долгой историей торгов; прошлое не обещает будущего.",
        "In the bottom third of the quality check of {n} altcoins: {coins}. In 2022–2026, alts with revenue and a long trading history did better; the past promises nothing.",
        "У нижній третині анкети якості з {n} альтів: {coins}. На історії 2022–2026 кращими були альти з виручкою та довгою історією торгів; минуле не обіцяє майбутнього.",
        "Στο κάτω τρίτο του ελέγχου ποιότητας {n} altcoins: {coins}. Το 2022–2026 τα πήγαν καλύτερα τα altcoins με έσοδα και μεγάλο ιστορικό συναλλαγών· το παρελθόν δεν υπόσχεται τίποτα.",
        "En el tercio inferior de la ficha de calidad de {n} altcoins: {coins}. En 2022–2026 fueron mejor las altcoins con ingresos y un largo historial; el pasado no promete nada."),
    "core_ok": _L(
        "Ядро — BTC, ETH и SOL — у вас {you}: основа на месте. В портфеле CRYPTO NOVA ядро сейчас {model}.",
        "Your core — BTC, ETH and SOL — is {you}: the base is in place. In the CRYPTO NOVA portfolio the core is {model} now.",
        "Ядро — BTC, ETH і SOL — у вас {you}: основа на місці. У портфелі CRYPTO NOVA ядро зараз {model}.",
        "Ο πυρήνας σας — BTC, ETH και SOL — είναι {you}: η βάση είναι στη θέση της. Στο χαρτοφυλάκιο CRYPTO NOVA ο πυρήνας είναι τώρα {model}.",
        "Tu núcleo — BTC, ETH y SOL — es el {you}: la base está en su sitio. En la cartera CRYPTO NOVA el núcleo es ahora el {model}."),
    "cash_ok": _L(
        "Запас в долларах {you} — не меньше 10%, как в портфеле CRYPTO NOVA.",
        "Your dollar reserve is {you} — at least 10%, as in the CRYPTO NOVA portfolio.",
        "Запас у доларах {you} — не менше 10%, як у портфелі CRYPTO NOVA.",
        "Το αποθεματικό σε δολάρια είναι {you} — τουλάχιστον 10%, όπως στο χαρτοφυλάκιο CRYPTO NOVA.",
        "Tu reserva en dólares es del {you}: al menos un 10%, como en la cartera CRYPTO NOVA."),
    "buy_ok": _L(
        "Подходят для покупки по правилам системы: {coins} — они проходят все её проверки.",
        "Marked “worth buying” or “worth adding to”: {coins} — they pass all the table's checks.",
        "Зі статусом «можна брати» або «можна докупити»: {coins} — вони проходять усі перевірки таблиці.",
        "Με ένδειξη «για αγορά» ή «για συμπλήρωση»: {coins} — περνούν όλους τους ελέγχους του πίνακα.",
        "En «para comprar» o «para ampliar»: {coins}; superan todos los controles de la tabla."),
    "count_ok": _L(
        "У вас {count} — в пределах 8, как в портфеле CRYPTO NOVA.",
        "You hold {count} — within 8, as in the CRYPTO NOVA portfolio.",
        "У вас {count} — у межах 8, як у портфелі CRYPTO NOVA.",
        "Έχετε {count} — έως 8, όπως στο χαρτοφυλάκιο CRYPTO NOVA.",
        "Tienes {count}: dentro de 8, como en la cartera CRYPTO NOVA."),
}

TXT = {
    "title": _L("Разбор портфеля", "Portfolio review", "Розбір портфеля", "Ανάλυση χαρτοφυλακίου", "Análisis de cartera"),
    "intro": _L("Я проверил {n} по данным CRYPTO NOVA на {date}.", "I checked {n} against CRYPTO NOVA's data of {date}.",
                "Я перевірив {n} за даними CRYPTO NOVA на {date}.", "Έλεγξα {n} με τα δεδομένα του CRYPTO NOVA της {date}.",
                "He revisado {n} con los datos de CRYPTO NOVA del {date}."),
    "total": _L("Итого", "Total", "Разом", "Σύνολο", "Total"),
    "unpriced": _L("Без цены, в итог не вошли: {coins}.", "No price, left out of the total: {coins}.",
                   "Без ціни, до підсумку не ввійшли: {coins}.", "Χωρίς τιμή, εκτός συνόλου: {coins}.", "Sin precio, fuera del total: {coins}."),
    "cmp_title": _L("Как выглядел бы этот портфель по модели CRYPTO NOVA", "How this portfolio would look by the CRYPTO NOVA model",
                    "Як виглядав би цей портфель за моделлю CRYPTO NOVA", "Πώς θα ήταν αυτό το χαρτοφυλάκιο με το μοντέλο CRYPTO NOVA",
                    "Cómo sería esta cartera con el modelo CRYPTO NOVA"),
    "you": _L("у вас", "you", "у вас", "εσείς", "tú"),
    "model": _L("модель", "model", "модель", "μοντέλο", "modelo"),
    "b_core": _L("Ядро BTC · ETH · SOL", "Core BTC · ETH · SOL", "Ядро BTC · ETH · SOL", "Πυρήνας BTC · ETH · SOL", "Núcleo BTC · ETH · SOL"),
    "b_buy": _L("Подходят для покупки", "“Worth buying” and “adding”", "«Можна брати» і «докупити»", "«Για αγορά» και «συμπλήρωση»",
                "«Para comprar» y «ampliar»"),
    "b_small": _L("Небольшой суммой", "Small amounts", "Невеликою сумою", "Μικρά ποσά", "Poca cantidad"),
    "b_other": _L("«Ждём», «не брать», без оценки", "“Wait”, “avoid”, not rated", "«Чекаємо», «не брати», без оцінки",
                  "«Αναμονή», «αποφυγή», χωρίς αξιολόγηση", "«Esperar», «evitar», sin evaluar"),
    "b_cash": _L("Доллары (USDT, USDC)", "Dollars (USDT, USDC)", "Долари (USDT, USDC)", "Δολάρια (USDT, USDC)", "Dólares (USDT, USDC)"),
    "cash_word": _L("доллары", "dollars", "долари", "δολάρια", "dólares"),
    "model_hold": _L("На ваши {total} портфель CRYPTO NOVA держал бы: {items}.", "With your {total}, the CRYPTO NOVA portfolio would hold: {items}.",
                     "На ваші {total} портфель CRYPTO NOVA тримав би: {items}.", "Με τα {total} σας, το χαρτοφυλάκιο CRYPTO NOVA θα κρατούσε: {items}.",
                     "Con tus {total}, la cartera CRYPTO NOVA tendría: {items}."),
    "match": _L("Совпадение с портфелем CRYPTO NOVA: {p}", "Overlap with the CRYPTO NOVA portfolio: {p}", "Збіг із портфелем CRYPTO NOVA: {p}",
                "Επικάλυψη με το χαρτοφυλάκιο CRYPTO NOVA: {p}", "Coincidencia con la cartera CRYPTO NOVA: {p}"),
    # 28.10 (build 46 QA): the model is the real account now — how its money is split (okx_alloc shares), not «ядро + альтсезон»
    "split": _L("Весь реальный счёт CRYPTO NOVA делится так: спот-портфель {spot}, фьючерсы {fut}, активный спот {act}.",
                "CRYPTO NOVA's whole real account is split like this: the spot portfolio {spot}, futures {fut}, active spot {act}.",
                "Весь реальний рахунок CRYPTO NOVA поділено так: спот-портфель {spot}, ф'ючерси {fut}, активний спот {act}.",
                "Ολόκληρος ο πραγματικός λογαριασμός του CRYPTO NOVA μοιράζεται έτσι: χαρτοφυλάκιο spot {spot}, futures {fut}, ενεργό spot {act}.",
                "Toda la cuenta real de CRYPTO NOVA se reparte así: cartera spot {spot}, futuros {fut}, spot activo {act}."),
    # 28.10 (owner's decision): + the extra core BTC / ETH / SOL (alloc core_pct)
    "split_core": _L("Весь реальный счёт CRYPTO NOVA делится так: спот-портфель {spot}, дополнительное ядро BTC, ETH и SOL {core}, "
                     "фьючерсы {fut}, активный спот {act}.",
                     "CRYPTO NOVA's whole real account is split like this: the spot portfolio {spot}, extra core BTC, ETH and SOL {core}, "
                     "futures {fut}, active spot {act}.",
                     "Весь реальний рахунок CRYPTO NOVA поділено так: спот-портфель {spot}, додаткове ядро BTC, ETH і SOL {core}, "
                     "ф'ючерси {fut}, активний спот {act}.",
                     "Ολόκληρος ο πραγματικός λογαριασμός του CRYPTO NOVA μοιράζεται έτσι: χαρτοφυλάκιο spot {spot}, επιπλέον πυρήνας "
                     "BTC, ETH και SOL {core}, futures {fut}, ενεργό spot {act}.",
                     "Toda la cuenta real de CRYPTO NOVA se reparte así: cartera spot {spot}, núcleo adicional BTC, ETH y SOL {core}, "
                     "futuros {fut}, spot activo {act}."),
    "alt": _L("Модель «ядро + альтсезон» сейчас: ядро {core}, альт-корзина {alts} ({state}), кэш {cash}, фьючерсы-сигналы {fut}.",
              "The “core + altseason” model now: core {core}, alt basket {alts} ({state}), cash {cash}, futures signals {fut}.",
              "Модель «ядро + альтсезон» зараз: ядро {core}, альт-кошик {alts} ({state}), кеш {cash}, ф'ючерси-сигнали {fut}.",
              "Το μοντέλο «πυρήνας + altseason» τώρα: πυρήνας {core}, καλάθι altcoins {alts} ({state}), μετρητά {cash}, σήματα futures {fut}.",
              "El modelo «núcleo + altseason» ahora: núcleo {core}, cesta de altcoins {alts} ({state}), efectivo {cash}, señales de futuros {fut}."),
    "alt_on": _L("датчик альтсезона включён", "the altseason gauge is on", "датчик альтсезону ввімкнено", "ο δείκτης altseason είναι ενεργός",
                 "el indicador de altseason está activo"),
    "alt_off": _L("датчик выключен — альт-часть в BTC", "the gauge is off — the alt part sits in BTC", "датчик вимкнено — альт-частина в BTC",
                  "ο δείκτης είναι ανενεργός — το κομμάτι των altcoins είναι σε BTC",
                  "el indicador está apagado: la parte de altcoins está en BTC"),
    "rule": _L("Правило", "Rule", "Правило", "Κανόνας", "Regla"),
    "disclaimer": _L("Это разбор по правилам CRYPTO NOVA, а не финансовый совет.", "This is a review by CRYPTO NOVA's rules, not financial advice.",
                     "Це розбір за правилами CRYPTO NOVA, а не фінансова порада.",
                     "Αυτή είναι ανάλυση με τους κανόνες του CRYPTO NOVA, όχι επενδυτική συμβουλή.",
                     "Este es un análisis según las reglas de CRYPTO NOVA, no un consejo financiero."),
    "and_more": _L("и ещё {n}", "and {n} more", "і ще {n}", "και άλλα {n}", "y {n} más"),
    "and": _L(" и ", " and ", " і ", " και ", " y "),
    "e_g": _L("например", "e.g.", "наприклад", "π.χ.", "p. ej."),
}

MSG = {   # error codes → what the app shows (parse and review)
    "too_large": _L("Файл больше 10 МБ. Пришлите файл поменьше — например, только активы, без истории.",
                    "The file is over 10 MB. Please send a smaller one — for example, assets only, without history.",
                    "Файл більший за 10 МБ. Надішліть менший — наприклад, лише активи, без історії.",
                    "Το αρχείο είναι πάνω από 10 MB. Στείλτε ένα μικρότερο — για παράδειγμα, μόνο τα υπόλοιπα, χωρίς ιστορικό.",
                    "El archivo pesa más de 10 MB. Envía uno más pequeño, por ejemplo solo los activos, sin historial."),
    "bad_type": _L("Этот файл не прочитать. Подойдёт скриншот, CSV, XLSX или PDF.", "I can't read this file. A screenshot, CSV, XLSX or PDF will do.",
                   "Цей файл не прочитати. Підійде скриншот, CSV, XLSX або PDF.",
                   "Δεν μπορώ να διαβάσω αυτό το αρχείο. Ταιριάζει στιγμιότυπο οθόνης, CSV, XLSX ή PDF.",
                   "No puedo leer este archivo. Sirve una captura, un CSV, un XLSX o un PDF."),
    "xls": _L("Это старый формат Excel (XLS). Сохраните файл как XLSX или CSV и пришлите снова.",
              "This is the old Excel format (XLS). Save it as XLSX or CSV and send it again.",
              "Це старий формат Excel (XLS). Збережіть файл як XLSX або CSV і надішліть знову.",
              "Αυτή είναι η παλιά μορφή Excel (XLS). Αποθηκεύστε το ως XLSX ή CSV και στείλτε το ξανά.",
              "Es el formato antiguo de Excel (XLS). Guárdalo como XLSX o CSV y envíalo de nuevo."),
    "no_rows": _L("В файле не нашлось монет с количеством. Пришлите выгрузку активов (Assets) или скриншот экрана «Активы».",
                  "I found no coins with amounts in the file. Send the assets export or a screenshot of the “Assets” screen.",
                  "У файлі не знайшлося монет із кількістю. Надішліть вивантаження активів (Assets) або скриншот екрана «Активи».",
                  "Δεν βρήκα νομίσματα με ποσότητες στο αρχείο. Στείλτε την εξαγωγή των assets ή στιγμιότυπο της οθόνης «Assets».",
                  "No encontré monedas con cantidades en el archivo. Envía la exportación de activos o una captura de la pantalla «Activos»."),
    "image_unsupported": _L("Скриншоты я пока не читаю. Пришлите CSV, XLSX или PDF-выписку с биржи.",
                            "I can't read screenshots yet. Please send a CSV, XLSX or PDF statement from the exchange.",
                            "Скриншоти я поки не читаю. Надішліть CSV, XLSX або PDF-виписку з біржі.",
                            "Δεν διαβάζω ακόμη στιγμιότυπα οθόνης. Στείλτε CSV, XLSX ή PDF από το ανταλλακτήριο.",
                            "Todavía no leo capturas de pantalla. Envía un CSV, un XLSX o un extracto en PDF del exchange."),
    "read_failed": _L("Файл повреждён или защищён — не получилось его открыть.", "The file is damaged or protected — I couldn't open it.",
                      "Файл пошкоджений або захищений — не вдалося його відкрити.",
                      "Το αρχείο είναι κατεστραμμένο ή προστατευμένο — δεν μπόρεσα να το ανοίξω.",
                      "El archivo está dañado o protegido: no pude abrirlo."),
    "model_busy": _L("Не получилось прочитать файл прямо сейчас. Попробуйте ещё раз через минуту или пришлите CSV.",
                     "I couldn't read the file right now. Try again in a minute or send a CSV.",
                     "Не вдалося прочитати файл саме зараз. Спробуйте ще раз за хвилину або надішліть CSV.",
                     "Δεν μπόρεσα να διαβάσω το αρχείο αυτή τη στιγμή. Δοκιμάστε ξανά σε ένα λεπτό ή στείλτε CSV.",
                     "No pude leer el archivo ahora mismo. Inténtalo de nuevo en un minuto o envía un CSV."),
    "rate": _L("Слишком много разборов подряд. Попробуйте через {m} мин.", "Too many reviews in a row. Try again in {m} min.",
               "Забагато розборів поспіль. Спробуйте за {m} хв.", "Πάρα πολλές αναλύσεις στη σειρά. Δοκιμάστε ξανά σε {m} λεπτά.",
               "Demasiados análisis seguidos. Inténtalo en {m} min."),
    "no_data": _L("Данные CRYPTO NOVA сейчас обновляются. Попробуйте через пару минут.", "CRYPTO NOVA's data is updating. Try again in a couple of minutes.",
                  "Дані CRYPTO NOVA зараз оновлюються. Спробуйте за кілька хвилин.",
                  "Τα δεδομένα του CRYPTO NOVA ενημερώνονται. Δοκιμάστε ξανά σε λίγα λεπτά.",
                  "Los datos de CRYPTO NOVA se están actualizando. Inténtalo en un par de minutos."),
    "bad_holdings": _L("Список монет пуст. Добавьте хотя бы одну монету с количеством.", "The coin list is empty. Add at least one coin with an amount.",
                       "Список монет порожній. Додайте хоча б одну монету з кількістю.",
                       "Η λίστα νομισμάτων είναι κενή. Προσθέστε τουλάχιστον ένα νόμισμα με ποσότητα.",
                       "La lista de monedas está vacía. Añade al menos una moneda con su cantidad."),
    "server": _L("Не получилось сделать разбор. Попробуйте ещё раз.", "I couldn't do the review. Please try again.",
                 "Не вдалося зробити розбір. Спробуйте ще раз.", "Δεν μπόρεσα να κάνω την ανάλυση. Δοκιμάστε ξανά.",
                 "No pude hacer el análisis. Inténtalo de nuevo."),
    # notes on a successful parse (shown above the confirm list)
    "note_ledger": _L("Количества посчитаны по истории операций. Если в файле не вся история — поправьте их.",
                      "Amounts are summed from the transaction history. If the file doesn't cover all of it, fix them.",
                      "Кількості пораховано за історією операцій. Якщо у файлі не вся історія — виправте їх.",
                      "Οι ποσότητες υπολογίστηκαν από το ιστορικό κινήσεων. Αν το αρχείο δεν τις καλύπτει όλες, διορθώστε τις.",
                      "Las cantidades salen del historial de operaciones. Si el archivo no lo cubre entero, corrígelas."),
    "note_model": _L("Монеты распознал NOVA AI — проверьте количества.", "NOVA AI recognised the coins — please check the amounts.",
                     "Монети розпізнав NOVA AI — перевірте кількості.", "Το NOVA AI αναγνώρισε τα νομίσματα — ελέγξτε τις ποσότητες.",
                     "NOVA AI reconoció las monedas: revisa las cantidades."),
    "note_check": _L("Проверьте монеты и количества — их можно поправить.", "Check the coins and amounts — you can fix them.",
                     "Перевірте монети й кількості — їх можна виправити.", "Ελέγξτε τα νομίσματα και τις ποσότητες — μπορείτε να τα διορθώσετε.",
                     "Revisa las monedas y cantidades: puedes corregirlas."),
}


def message(code: str, lang: str = DEFAULT, **kw) -> str:
    row = MSG.get(code) or MSG["server"]
    s = row.get(norm_lang(lang)) or row[DEFAULT]
    return s.format(**kw) if kw else s


def _t(row: dict, lang: str, **kw) -> str:
    s = row.get(lang) or row[DEFAULT]
    return s.format(**kw) if kw else s


def coins_word(n: int, lang: str) -> str:
    """«3 монеты», «5 coins», «1 νόμισμα»…"""
    n = int(n)
    if lang in ("ru", "uk"):
        one, few, many = ("монета", "монеты", "монет") if lang == "ru" else ("монета", "монети", "монет")
        w = one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many
    elif lang == "el":
        w = "νόμισμα" if n == 1 else "νομίσματα"
    elif lang == "es":
        w = "moneda" if n == 1 else "monedas"
    else:
        w = "coin" if n == 1 else "coins"
    return f"{n} {w}"


# ------------------------------------------------------------------------------------------------ numbers
def usd(v: float) -> str:
    v = float(v or 0)
    if abs(v) >= 100:
        return "$" + f"{v:,.0f}".replace(",", " ")
    return "$" + f"{v:,.2f}".replace(",", " ")


def pct(v: float) -> str:
    v = float(v or 0)
    if v <= 0:
        return "0%"
    if v < 0.1:
        return "<0.1%"
    if v < 10:
        return f"{v:.1f}".rstrip("0").rstrip(".") + "%"
    return f"{v:.0f}%"


def _list(syms: list[str], lang: str, k: int = 3) -> str:
    syms = list(dict.fromkeys(syms))
    if len(syms) <= k:
        if len(syms) <= 1:
            return "".join(syms)
        return ", ".join(syms[:-1]) + _t(TXT["and"], lang) + syms[-1]
    return ", ".join(syms[:k]) + " " + _t(TXT["and_more"], lang, n=len(syms) - k)


# ------------------------------------------------------------------------------------------------ the system's data
class Data:
    FILES = ("ideas", "quality", "altseason", "portfolio", "markets", "public_real")

    def __init__(self, web_dir: str):
        self.web = web_dir
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> dict:
        p = os.path.join(self.web, f"{name}.json")
        try:
            m = os.path.getmtime(p)
        except OSError:
            return {}
        with self._lock:
            hit = self._cache.get(name)
            if hit and hit[0] == m:
                return hit[1]
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
            if not isinstance(doc, dict):
                raise ValueError
        except (OSError, ValueError):
            return hit[1] if hit else {}
        with self._lock:
            self._cache[name] = (m, doc)
        return doc

    def known(self) -> set[str]:
        """every ticker the system knows (for reading files: «LDBTC» → BTC, a coin in a PDF line)"""
        out = set(STABLES) | set(CORE)
        out |= set((self.get("ideas").get("statuses") or {}).keys())
        out |= {str(c.get("symbol") or "").upper() for c in self.get("markets").get("top") or [] if isinstance(c, dict)}
        out |= {str(r.get("symbol") or "").upper() for r in self.get("quality").get("rows") or [] if isinstance(r, dict)}
        out.discard("")
        return out


def _price(sym: str, ideas: dict, markets: dict) -> float | None:
    st = (ideas.get("statuses") or {}).get(sym) or {}
    for v in (st.get("price"),):
        try:
            if v is not None and float(v) > 0:
                return float(v)
        except (TypeError, ValueError):
            pass
    for c in markets.get("top") or []:
        if isinstance(c, dict) and str(c.get("symbol") or "").upper() == sym:
            try:
                if float(c.get("price")) > 0:
                    return float(c["price"])
            except (TypeError, ValueError):
                return None
    if sym in STABLES:
        return 1.0
    return None


def reason(why_ru: str, status: str, lang: str) -> tuple[str, str | None, str | None]:
    """(the reason in this language, its key, the check that decided — ath / dilution / overheat / vol / desk / cex / …)"""
    s = (why_ru or "").strip()
    for key, rx, flag, tr in _REASONS:
        m = rx.match(s)
        if m:
            vals = {k: v for k, v in m.groupdict().items() if v is not None}
            if lang == DEFAULT:
                return (REASONS_RU[key].format(**vals) if key in REASONS_RU else s), key, flag
            return tr[lang].format(**vals), key, flag
    return _t(GENERIC.get(status, GENERIC["wait"]), lang), None, None      # an unknown sentence: the plain status, never jargon


def _date(ms) -> str:
    try:
        return dt.datetime.fromtimestamp(float(ms) / 1000, dt.timezone(dt.timedelta(hours=3))).strftime("%d.%m.%Y")
    except (TypeError, ValueError, OverflowError, OSError):
        return dt.datetime.now(dt.timezone.utc).strftime("%d.%m.%Y")


def clean_holdings(items, cap: int = 100) -> list[dict]:
    """the app's confirmed list → [{"sym", "amount", "value_usd"?}]; anything odd is dropped, the same coin is summed"""
    agg: dict[str, dict] = {}
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        s = str(it.get("sym") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.]{0,14}", s):
            continue
        try:
            a = float(it.get("amount"))
        except (TypeError, ValueError):
            continue
        if not (a > 0 and a < 1e15):
            continue
        g = agg.setdefault(s, {"sym": s, "amount": 0.0})
        g["amount"] += a
        try:
            v = float(it.get("value_usd")) if it.get("value_usd") is not None else None
        except (TypeError, ValueError):
            v = None
        if v is not None and 0 <= v < 1e13:
            g["value_usd"] = g.get("value_usd", 0.0) + v
        if len(agg) >= cap:
            break
    return list(agg.values())


# ------------------------------------------------------------------------------------------------ the model: the real account
def real_model(real: dict, statuses: dict) -> dict | None:
    """the spot part of the real CRYPTO NOVA account (web/public_real.json) as the review's model: {"model": {sym: % of the spot
    holdings}, "buckets", "cash" (%), "split": the account's spot / futures / active shares as text, "asof"}; None without it"""
    if not isinstance(real, dict) or not real.get("asof") or not isinstance(real.get("spot"), list):
        return None
    al = real.get("alloc") if isinstance(real.get("alloc"), dict) else {}
    coins = {}
    for x in real["spot"]:
        try:
            if x.get("sym") and float(x.get("usd") or 0) > 0:
                coins[str(x["sym"]).upper()] = coins.get(str(x["sym"]).upper(), 0.0) + float(x["usd"])
        except (TypeError, ValueError):
            continue
    cash = sum(float(al.get(k) or 0) for k in ("spot_cash_usd", "active_wait_usd", "other_usd")) if al else 0.0
    hold = sum(coins.values()) + cash
    if hold <= 0:
        return None
    model = {s: v / hold * 100 for s, v in coins.items()}
    b = {"core": 0.0, "buy": 0.0, "small": 0.0, "other": 0.0, "cash": 0.0}
    for s, w in model.items():
        st = (statuses.get(s) or {}).get("status")
        b["core" if s in CORE else "small" if st == "small" else "buy" if st in ("buy", "add") else "other"] += w
    split = None
    if al.get("spot_pct") is not None and al.get("fut_pct") is not None:
        split = {"spot": pct(float(al["spot_pct"])), "fut": pct(float(al["fut_pct"])), "act": pct(float(al.get("active_pct") or 0))}
        if float(al.get("core_pct") or 0) > 0:                         # 28.10: the extra core
            split["core"] = pct(float(al["core_pct"]))
    return {"model": model, "buckets": b, "cash": cash / hold * 100, "split": split, "asof": int(real["asof"])}


# ------------------------------------------------------------------------------------------------ the review
def review(holdings: list[dict], data: Data, lang: str = DEFAULT) -> dict:
    lang = norm_lang(lang)
    ideas, quality, alts, pfdoc, markets, real = (data.get(n) for n in Data.FILES)
    statuses = ideas.get("statuses") or {}
    if not statuses:
        return {"error": "no_data", "message": message("no_data", lang)}
    mkt = {str(c.get("symbol") or "").upper() for c in markets.get("top") or [] if isinstance(c, dict)}
    qrows = {str(r.get("symbol") or "").upper(): r for r in quality.get("rows") or [] if isinstance(r, dict)}
    qn = int(quality.get("universe") or len(qrows) or 0)

    rows, unpriced = [], []
    for h in holdings:
        s, a = h["sym"], float(h["amount"])
        p = _price(s, ideas, markets)
        val = a * p if p is not None else h.get("value_usd")
        st = statuses.get(s) or {}
        if s in STABLES:
            status, why, key, flag = "cash", _t(GENERIC["cash"], lang), "cash", None
        elif st.get("status") in STATUS_LABEL:
            status = st["status"]
            why, key, flag = reason(st.get("why_ru") or "", status, lang)
        else:
            status, key, flag = "unknown", "unknown", None
            why = _t(GENERIC["market" if s in mkt else ("unknown_file" if p is None and val is not None else "unknown")], lang)
        kind = "cash" if s in STABLES else "core" if s in CORE else st.get("kind")
        qr = qrows.get(s)
        row = {"sym": s, "amount": a, "price": p, "value_usd": round(val, 2) if val is not None else None, "status": status,
               "status_label": _t(STATUS_LABEL[status], lang), "reason": why, "kind": kind, "reason_key": key, "flag": flag,
               "quality_rank": qr.get("rank") if qr else None, "price_source": "system" if p is not None else ("file" if val is not None else None)}
        (rows if val is not None else unpriced).append(row)
    total = sum(r["value_usd"] or 0 for r in rows)
    for r in rows:
        r["share_pct"] = round((r["value_usd"] or 0) / total * 100, 2) if total > 0 else 0.0
    rows.sort(key=lambda r: -(r["value_usd"] or 0))
    for r in unpriced:
        r["share_pct"] = None

    share = lambda pred: sum(r["share_pct"] for r in rows if pred(r))
    syms = lambda pred: [r["sym"] for r in rows if pred(r)]
    core = share(lambda r: r["sym"] in CORE)
    cash = share(lambda r: r["status"] == "cash")
    coins_n = len([r for r in rows + unpriced if r["status"] != "cash"])

    # -------- the model: «CRYPTO NOVA портфель» today (web/portfolio.json), «ядро + альтсезон» (web/altseason.json)
    pf = (pfdoc.get("portfolios") or {}).get("ideas") or {}
    model: dict[str, float] = {}
    mbuck = {"core": 0.0, "buy": 0.0, "small": 0.0, "other": 0.0, "cash": 0.0}
    for p in pf.get("positions") or []:
        try:
            w = float(p.get("weight") or 0) * 100
        except (TypeError, ValueError):
            continue
        sym = str(p.get("symbol") or "").upper()
        if not sym or w <= 0:
            continue
        model[sym] = model.get(sym, 0) + w
        b = p.get("bucket")
        mbuck["core" if sym in CORE or b == "core" else "small" if b in ("meme", "high", "small") else "buy"] += w
    try:
        mcash = float(pf.get("cash_pct")) if pf.get("cash_pct") is not None else max(0.0, 100 - sum(model.values()))
    except (TypeError, ValueError):
        mcash = max(0.0, 100 - sum(model.values()))
    # 28.10 (build 46 QA: the model still showed coins the account had sold, «ядро 50% … датчик альтсезона», «на 22.10»): the model
    # is the spot part of the REAL CRYPTO NOVA account now (web/public_real.json — the same file and moment as the app's PUBLIC
    # portfolio): each coin's share of the spot holdings, the spot dollars; the account's split (65 / 10 / 15 / 10 since the owner's decision of 28.10) in a line of its own
    rm = real_model(real, statuses)
    model_src, model_asof = "pf", ideas.get("asof")
    if rm:
        model, mbuck, mcash = rm["model"], rm["buckets"], rm["cash"]
        model_src, model_asof = "real", rm["asof"]
    mbuck["cash"] = mcash
    model_core = mbuck["core"]
    tg = alts.get("targets_pct") or {}
    a_core = sum(float(tg.get(c) or 0) for c in CORE)
    a_fut = sum(float(v or 0) for k, v in tg.items() if "фьючерс" in k.lower() or "futures" in k.lower())
    a_cash = sum(float(v or 0) for k, v in tg.items() if "usdt" in k.lower() or "usdc" in k.lower() or "кэш" in k.lower())
    a_alts = max(0.0, sum(float(v or 0) for v in tg.values()) - a_core - a_fut - a_cash)

    # -------- recommendations: each one a rule of the system, the weight = how much of the portfolio it concerns
    cand: list[tuple[float, str, dict]] = []
    def add(w, key, rule, **kw):
        cand.append((w, key, {"rule": rule, "kw": kw}))
    avoid = [r for r in rows if r["status"] == "avoid"]
    av = share(lambda r: r["status"] == "avoid")
    if avoid and av >= 0.5:
        top = avoid[0]
        rs = top["reason"]
        add(av * 3, "avoid" if len(avoid) > 1 else "avoid1", "avoid", coins=_list([r["sym"] for r in avoid], lang), you=pct(av), top=top["sym"],
            reason=rs[:1].lower() + rs[1:] if rs and not rs[:2].isupper() else rs)
    if total > 0 and core < 30:
        add((CORE_PLAN_SUM - core) * 1.5, "core_low", "core", you=pct(core), model=pct(model_core), alt=pct(a_core))
    for c in CORE:
        sh = share(lambda r, c=c: r["sym"] == c)
        if sh > CORE_PLAN[c] * 1.5:
            add((sh - CORE_PLAN[c] * 1.5) * 0.8, "core_high", "core_high", coin=c, you=pct(sh), plan=pct(CORE_PLAN[c]))
            break
    big = [r for r in rows if r["sym"] not in CORE and r["status"] != "cash" and r["share_pct"] > 2 * ALT_MAX]
    if big:
        add(sum(r["share_pct"] - ALT_MAX for r in big) * 1.2, "conc", "conc", coins=_list([r["sym"] for r in big], lang),
            you=pct(sum(r["share_pct"] for r in big)))
    risky_rows = [r for r in rows if r["sym"] not in CORE and r["status"] != "avoid" and (r["kind"] in ("meme", "high") or r["status"] == "small")]
    rk = sum(r["share_pct"] for r in risky_rows)
    if rk > SMALL_MAX * SMALL_COUNT:
        add((rk - SMALL_MAX * SMALL_COUNT) * 1.5, "risky", "risky", you=pct(rk), coins=_list([r["sym"] for r in risky_rows], lang))
    if total > 0 and cash < CASH_MIN:
        add((CASH_MIN - cash) * 1.0, "cash_low", "cash", you=pct(cash))
    for flag, key, wt in (("overheat", "overheat", 1.0), ("dilution", "dilution", 1.0), ("cex", "cex", 0.8)):
        fr = [r for r in rows if r["flag"] == flag and r["status"] != "avoid"]
        if fr:
            add(sum(r["share_pct"] for r in fr) * wt, key, key, coins=_list([r["sym"] for r in fr], lang))
    unk = [r for r in rows + unpriced if r["status"] == "unknown"]
    uk = sum(r["share_pct"] or 0 for r in unk)
    if unk and (uk >= 5 or len(unk) >= 3):
        add(max(uk, 3.0) * 0.9, "unknown", "unknown", coins=_list([r["sym"] for r in unk], lang), you=pct(uk), n=len(statuses))
    if coins_n > COINS_MAX:
        add((coins_n - COINS_MAX) * 2.0, "count", "count", count=coins_word(coins_n, lang))
    if qn:
        low = [r for r in rows if r["quality_rank"] and r["quality_rank"] > qn * 2 / 3 and r["status"] in ("wait", "small", "unknown")]
        lq = sum(r["share_pct"] for r in low)
        if low and lq >= 5:
            add(lq * 0.6, "quality", "quality", coins=_list([r["sym"] for r in low], lang), n=qn)
    cand.sort(key=lambda c: -c[0])
    picked = [c for c in cand if c[0] > 0][:5]
    good: list[tuple[float, str, dict]] = []
    keys = {k for _w, k, _d in picked}
    if total > 0 and core >= 30 and "core_high" not in keys:
        good.append((0, "core_ok", {"rule": "core", "kw": {"you": pct(core), "model": pct(model_core)}}))
    if total > 0 and cash >= CASH_MIN:
        good.append((0, "cash_ok", {"rule": "cash", "kw": {"you": pct(cash)}}))
    ok_coins = syms(lambda r: r["status"] in ("buy", "add"))
    if ok_coins:
        good.append((0, "buy_ok", {"rule": "buy", "kw": {"coins": _list(ok_coins, lang)}}))
    if coins_n <= COINS_MAX:
        good.append((0, "count_ok", {"rule": "count", "kw": {"count": coins_word(coins_n, lang)}}))
    while len(picked) < 3 and good:
        picked.append(good.pop(0))
    recs = []
    for _w, key, d in picked:
        src, name = RULE[d["rule"]]
        recs.append({"id": key, "tone": "ok" if key.endswith("_ok") else "warn",
                     "rule": f"{_t(name, lang)} · {_t(SRC[src], lang)}", "rule_id": d["rule"], "rule_source": src,
                     "text": _t(REC[key], lang, **d["kw"])})

    # -------- the comparison
    ub = {"core": core, "buy": share(lambda r: r["sym"] not in CORE and r["status"] in ("buy", "add")),
          "small": share(lambda r: r["sym"] not in CORE and r["status"] == "small"), "cash": cash}
    ub["other"] = max(0.0, 100 - sum(ub.values())) if total > 0 else 0.0
    user_w = {r["sym"]: r["share_pct"] for r in rows if r["status"] != "cash"}
    match = sum(min(w, model.get(s, 0)) for s, w in user_w.items()) + min(cash, mcash) if total > 0 else 0.0
    items = [f"{s} {usd(total * w / 100)}" for s, w in sorted(model.items(), key=lambda x: -x[1])]
    items.append(f"{_t(TXT['cash_word'], lang)} {usd(total * mcash / 100)}")
    compare = {
        "title": _t(TXT["cmp_title"], lang), "you": _t(TXT["you"], lang), "model": _t(TXT["model"], lang),
        "model_name": _t(SRC[model_src], lang),
        "rows": [{"key": k, "label": _t(TXT["b_" + k], lang), "you_pct": round(ub[k], 1), "model_pct": round(mbuck[k], 1),
                  "you": pct(ub[k]), "model_txt": pct(mbuck[k])} for k in ("core", "buy", "small", "other", "cash")],
        "coins": [{"sym": s, "pct": round(w, 1), "usd": round(total * w / 100, 2)} for s, w in sorted(model.items(), key=lambda x: -x[1])],
        "cash_pct": round(mcash, 1),
        "hold": _t(TXT["model_hold"], lang, total=usd(total), items=" · ".join(items)) if total > 0 else "",
        "match_pct": round(match, 1), "match": _t(TXT["match"], lang, p=pct(match)),
        # the old «ядро + альтсезон» line is gone; the key stays (build 45 shows it) with the real account's split
        "alt": _t(TXT["split_core" if "core" in (rm or {}).get("split", {}) else "split"], lang, **rm["split"]) if rm and rm.get("split") else "",
        "split": _t(TXT["split_core" if "core" in (rm or {}).get("split", {}) else "split"], lang, **rm["split"]) if rm and rm.get("split") else "",
    }
    n_all = len(rows) + len(unpriced)
    return {
        "lang": lang, "title": _t(TXT["title"], lang),
        "intro": _t(TXT["intro"], lang, n=coins_word(n_all, lang), date=_date(model_asof or time.time() * 1000)),
        "asof": model_asof, "total_usd": round(total, 2), "total": usd(total), "total_label": _t(TXT["total"], lang),
        "count": n_all,
        "rows": [{**r, "share": pct(r["share_pct"]) if r["share_pct"] is not None else "—"} for r in rows + unpriced],
        "unpriced": [r["sym"] for r in unpriced],
        "unpriced_note": _t(TXT["unpriced"], lang, coins=_list([r["sym"] for r in unpriced], lang, 6)) if unpriced else "",
        "recs": recs, "rule_label": _t(TXT["rule"], lang), "compare": compare, "disclaimer": _t(TXT["disclaimer"], lang),
    }


# ------------------------------------------------------------------------------------------------ per-user limits
class Limiter:
    """at most `per_window` calls in `window_s` and `per_day` in 24 h per user; kept in memory (uid → call times), never on disk"""

    def __init__(self, per_window: int, window_s: int, per_day: int, cap_users: int = 20000):
        self.per_window, self.window_s, self.per_day, self.cap = per_window, window_s, per_day, cap_users
        self._calls: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, uid: str, now: float | None = None) -> int:
        """0 = allowed (and counted); else the minutes to wait"""
        now = time.time() if now is None else now
        with self._lock:
            ts = [t for t in self._calls.get(uid, []) if now - t < 86400]
            recent = [t for t in ts if now - t < self.window_s]
            if len(recent) >= self.per_window:
                return max(1, int((self.window_s - (now - recent[0])) // 60) + 1)
            if len(ts) >= self.per_day:
                return max(1, int((86400 - (now - ts[0])) // 60) + 1)
            ts.append(now)
            self._calls[uid] = ts
            if len(self._calls) > self.cap:
                for k in sorted(self._calls, key=lambda k: self._calls[k][-1] if self._calls[k] else 0)[: self.cap // 10]:
                    self._calls.pop(k, None)
            return 0
