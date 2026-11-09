"""Build 53 (owner's TestFlight feedback 31.10–04.11, «"Откуп дисконта фьючерса" — никто не понимает»): the real account explains
itself in plain words, in the app's five languages, with only the numbers the system recorded.

  trade_why(...)  public_portfolio.futures.open[] / closed[] → "why_full": what the signal was (this trade's own numbers from the desk
                  trade it mirrors, web/paper.json — facts or the desk's own reason), the checks the system really ran before the
                  entry (the rule's condition, the risk layer cnq/risk/gate.py, the rule's slots, the real account's caps of
                  cnq/execution/okx_futures.py), the rule's record on history (app_home.HISTORY, docs/research) and on the real
                  account since its start, and the plan: entry, stop, exit, leverage and the $ at risk.
  coin_why(...)   public_portfolio.spot.coins[] → "why_full": why the system holds the coin — its kind, the spot table's checks with
                  this coin's numbers (web/ideas.json rows: rating, consultant, 200 days, overheating, all-time high, turnover,
                  bitcoin's regime), the table's status today and how many coins the table checks.
  calm_note(...)  public_portfolio.calm_note: one calm sentence about a drawdown — only when the market (bitcoin since the account's
                  start) fell more than the account; and the 24/7 line from the engine's real counters (app_home._engine_facts).

Nothing is filled in when the data is missing: a check without its number is left out, a sentence without its facts is not said.
Not financial advice. Stdlib only."""
from __future__ import annotations

import datetime as dt
import math
import re

from .i18n import DEFAULT, T, plural, t

L5 = ("ru", "en", "uk", "el", "es")


def _w(ru, en, uk, el, es):
    return dict(zip(L5, (ru, en, uk, el, es)))


W: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------------ the signal, in plain words (this trade's numbers)
    "rw.sig.D": _w(
        "Фьючерс {sym} торговался на {b} дешевле самой монеты (спота) — в среднем за час. Так бывает, когда толпа в спешке продаёт "
        "фьючерсы с плечом. На истории после такого перекоса цена чаще возвращается вверх в течение суток.",
        "The {sym} futures contract traded {b} below the coin itself (spot) on a one-hour average. This happens when the crowd "
        "rushes to sell leveraged futures. On history, after such a gap the price more often comes back up within a day.",
        "Ф'ючерс {sym} торгувався на {b} дешевше за саму монету (спот) — у середньому за годину. Так буває, коли натовп поспіхом "
        "продає ф'ючерси з плечем. На історії після такого перекосу ціна частіше повертається вгору протягом доби.",
        "Το συμβόλαιο future του {sym} διαπραγματευόταν {b} φθηνότερα από το ίδιο το νόμισμα (spot), κατά μέσο όρο σε μία ώρα. Αυτό "
        "συμβαίνει όταν το πλήθος πουλά βιαστικά futures με μόχλευση. Ιστορικά, μετά από τέτοια απόκλιση η τιμή συχνότερα "
        "επιστρέφει προς τα πάνω μέσα σε μία ημέρα.",
        "El futuro de {sym} cotizaba {b} por debajo de la propia moneda (spot), de media en una hora. Pasa cuando la gente vende "
        "con prisa futuros apalancados. En el histórico, tras un desfase así el precio suele volver a subir en un día."),
    "rw.sig.D0": _w(
        "Фьючерс {sym} торговался заметно дешевле самой монеты (спота). Так бывает, когда толпа в спешке продаёт фьючерсы с "
        "плечом. На истории после такого перекоса цена чаще возвращается вверх в течение суток.",
        "The {sym} futures contract traded well below the coin itself (spot). This happens when the crowd rushes to sell leveraged "
        "futures. On history, after such a gap the price more often comes back up within a day.",
        "Ф'ючерс {sym} торгувався помітно дешевше за саму монету (спот). Так буває, коли натовп поспіхом продає ф'ючерси з плечем. "
        "На історії після такого перекосу ціна частіше повертається вгору протягом доби.",
        "Το future του {sym} διαπραγματευόταν αισθητά φθηνότερα από το ίδιο το νόμισμα (spot). Αυτό συμβαίνει όταν το πλήθος πουλά "
        "βιαστικά futures με μόχλευση. Ιστορικά, μετά από τέτοια απόκλιση η τιμή συχνότερα επιστρέφει προς τα πάνω μέσα σε μία ημέρα.",
        "El futuro de {sym} cotizaba claramente por debajo de la propia moneda (spot). Pasa cuando la gente vende con prisa futuros "
        "apalancados. En el histórico, tras un desfase así el precio suele volver a subir en un día."),
    "rw.sig.E": _w(
        "Рынок падал широким фронтом, и свеча {sym} упала на {drop} за 15 минут на объёме в {vol} раза больше обычного. Это похоже "
        "на панику: на истории такие свечи чаще выкупают в течение суток.",
        "The market was falling across the board, and a {sym} candle dropped {drop} in 15 minutes on {vol} times the usual volume. "
        "That looks like panic: on history such candles are more often bought back within a day.",
        "Ринок падав широким фронтом, і свічка {sym} впала на {drop} за 15 хвилин на обсязі в {vol} раза більшому за звичайний. Це "
        "схоже на паніку: на історії такі свічки частіше викуповують протягом доби.",
        "Η αγορά έπεφτε σε όλο το μέτωπο και ένα κερί του {sym} έπεσε {drop} σε 15 λεπτά με όγκο {vol} φορές πάνω από τον συνηθισμένο. "
        "Μοιάζει με πανικό: ιστορικά τέτοια κεριά συχνότερα αγοράζονται πίσω μέσα σε μία ημέρα.",
        "El mercado caía en bloque y una vela de {sym} bajó {drop} en 15 minutos con {vol} veces el volumen habitual. Parece pánico: "
        "en el histórico estas velas suelen recomprarse en un día."),
    "rw.sig.E0": _w(
        "Рынок падал широким фронтом, и {sym} резко продали на большом объёме. Это похоже на панику: на истории такие свечи чаще "
        "выкупают в течение суток.",
        "The market was falling across the board, and {sym} was sold off sharply on heavy volume. That looks like panic: on history "
        "such candles are more often bought back within a day.",
        "Ринок падав широким фронтом, і {sym} різко продали на великому обсязі. Це схоже на паніку: на історії такі свічки частіше "
        "викуповують протягом доби.",
        "Η αγορά έπεφτε σε όλο το μέτωπο και το {sym} πουλήθηκε απότομα με μεγάλο όγκο. Μοιάζει με πανικό: ιστορικά τέτοια κεριά "
        "συχνότερα αγοράζονται πίσω μέσα σε μία ημέρα.",
        "El mercado caía en bloque y {sym} se vendió de golpe con mucho volumen. Parece pánico: en el histórico estas velas suelen "
        "recomprarse en un día."),
    "rw.sig.F": _w(
        "За 4 часа открытый интерес по {sym} (сумма открытых сделок с плечом) упал на {oi}, а цена — на {px}: биржа принудительно "
        "закрыла много позиций. После такого «вытряхивания» цена на истории чаще отскакивает в течение суток.",
        "In 4 hours the open interest in {sym} (the total of open leveraged trades) fell {oi} and the price {px}: the exchange "
        "force-closed many positions. After such a flush the price more often bounces within a day on history.",
        "За 4 години відкритий інтерес по {sym} (сума відкритих угод із плечем) впав на {oi}, а ціна — на {px}: біржа примусово "
        "закрила багато позицій. Після такого «витрушування» ціна на історії частіше відскакує протягом доби.",
        "Σε 4 ώρες το ανοιχτό ενδιαφέρον του {sym} (το σύνολο των ανοιχτών θέσεων με μόχλευση) έπεσε {oi} και η τιμή {px}: το "
        "χρηματιστήριο έκλεισε αναγκαστικά πολλές θέσεις. Μετά από τέτοιο «ξεσκαρτάρισμα» η τιμή ιστορικά συχνότερα αναπηδά μέσα "
        "σε μία ημέρα.",
        "En 4 horas el interés abierto de {sym} (el total de operaciones apalancadas abiertas) cayó {oi} y el precio {px}: el "
        "exchange cerró a la fuerza muchas posiciones. Tras una limpieza así, en el histórico el precio suele rebotar en un día."),
    "rw.sig.F0": _w(
        "Открытый интерес по {sym} (сумма открытых сделок с плечом) резко упал вместе с ценой: биржа принудительно закрыла много "
        "позиций. После такого «вытряхивания» цена на истории чаще отскакивает в течение суток.",
        "The open interest in {sym} (the total of open leveraged trades) fell sharply together with the price: the exchange "
        "force-closed many positions. After such a flush the price more often bounces within a day on history.",
        "Відкритий інтерес по {sym} (сума відкритих угод із плечем) різко впав разом із ціною: біржа примусово закрила багато "
        "позицій. Після такого «витрушування» ціна на історії частіше відскакує протягом доби.",
        "Το ανοιχτό ενδιαφέρον του {sym} (το σύνολο των ανοιχτών θέσεων με μόχλευση) έπεσε απότομα μαζί με την τιμή: το "
        "χρηματιστήριο έκλεισε αναγκαστικά πολλές θέσεις. Ιστορικά η τιμή μετά συχνότερα αναπηδά μέσα σε μία ημέρα.",
        "El interés abierto de {sym} (el total de operaciones apalancadas abiertas) cayó de golpe junto con el precio: el exchange "
        "cerró a la fuerza muchas posiciones. En el histórico el precio suele rebotar en un día."),
    "rw.sig.B": _w(
        "Свеча {sym} упала на {ret} за 15 минут — в {x} раза сильнее обычного колебания, но длинный хвост снизу показал, что падение "
        "выкупили. После таких каскадов ликвидаций цена на истории чаще возвращается.",
        "A {sym} candle fell {ret} in 15 minutes, {x} times a usual swing, but the long lower wick showed the drop was bought back. "
        "After such liquidation cascades the price more often recovers on history.",
        "Свічка {sym} впала на {ret} за 15 хвилин — у {x} раза сильніше за звичайне коливання, але довгий хвіст знизу показав, що "
        "падіння викупили. Після таких каскадів ліквідацій ціна на історії частіше повертається.",
        "Ένα κερί του {sym} έπεσε {ret} σε 15 λεπτά, {x} φορές πάνω από μια συνηθισμένη διακύμανση, αλλά η μακριά κάτω ουρά έδειξε "
        "ότι η πτώση αγοράστηκε. Μετά από τέτοιους καταρράκτες ρευστοποιήσεων η τιμή ιστορικά συχνότερα επανέρχεται.",
        "Una vela de {sym} cayó {ret} en 15 minutos, {x} veces una oscilación normal, pero la larga mecha inferior mostró que la "
        "caída se recompró. Tras cascadas de liquidaciones así, en el histórico el precio suele recuperarse."),
    "rw.sig.other": _w(
        "Сделка по правилам CRYPTO NOVA AI: условия входа, стоп и выход заданы заранее.",
        "A trade by CRYPTO NOVA AI's rules: the entry conditions, the stop and the exit are set in advance.",
        "Угода за правилами CRYPTO NOVA AI: умови входу, стоп і вихід задані заздалегідь.",
        "Συναλλαγή με τους κανόνες του CRYPTO NOVA AI: οι όροι εισόδου, το stop και η έξοδος ορίζονται από πριν.",
        "Una operación según las reglas de CRYPTO NOVA AI: las condiciones de entrada, el stop y la salida se fijan de antemano."),
    # ------------------------------------------------------------------ what the system checked (only what it really checks)
    "rw.chk.D": _w("Перекос фьючерса к споту: {b} ниже спота в среднем за час (нужно от 0.20%)",
                   "Futures vs spot gap: {b} below spot on a one-hour average (needs 0.20% or more)",
                   "Перекіс ф'ючерса до спота: {b} нижче спота в середньому за годину (потрібно від 0.20%)",
                   "Απόκλιση future από spot: {b} κάτω από το spot κατά μέσο όρο σε μία ώρα (χρειάζεται 0.20% ή περισσότερο)",
                   "Desfase futuro–spot: {b} por debajo del spot de media en una hora (hace falta 0.20% o más)"),
    "rw.chk.E": _w("Свеча и объём: {drop} за 15 минут, объём ×{vol} к обычному",
                   "Candle and volume: {drop} in 15 minutes, volume ×{vol} the usual",
                   "Свічка й обсяг: {drop} за 15 хвилин, обсяг ×{vol} до звичайного",
                   "Κερί και όγκος: {drop} σε 15 λεπτά, όγκος ×{vol} του συνηθισμένου",
                   "Vela y volumen: {drop} en 15 minutos, volumen ×{vol} el habitual"),
    "rw.chk.breadth": _w("Ширина распродажи: за сутки упали больше чем на 3% {b} крупных монет (нужно от {need})",
                         "Breadth of the sell-off: {b} of large coins down more than 3% in a day (needs {need} or more)",
                         "Ширина розпродажу: за добу впали більш ніж на 3% {b} великих монет (потрібно від {need})",
                         "Εύρος του ξεπουλήματος: {b} των μεγάλων νομισμάτων κάτω πάνω από 3% σε μία ημέρα (χρειάζεται {need} ή περισσότερο)",
                         "Amplitud de la venta: {b} de las monedas grandes bajó más de un 3% en un día (hace falta {need} o más)"),
    "rw.chk.F": _w("Открытый интерес и цена за 4 часа: {oi} и {px} (нужно −12% и −5% и ниже)",
                   "Open interest and price over 4 hours: {oi} and {px} (needs −12% and −5% or lower)",
                   "Відкритий інтерес і ціна за 4 години: {oi} і {px} (потрібно −12% і −5% і нижче)",
                   "Ανοιχτό ενδιαφέρον και τιμή σε 4 ώρες: {oi} και {px} (χρειάζεται −12% και −5% ή χαμηλότερα)",
                   "Interés abierto y precio en 4 horas: {oi} y {px} (hace falta −12% y −5% o menos)"),
    "rw.chk.B": _w("Сила свечи: {ret} за 15 минут — в {x} раза больше обычного колебания (нужно от 3.5)",
                   "Strength of the candle: {ret} in 15 minutes, {x} times a usual swing (needs 3.5 or more)",
                   "Сила свічки: {ret} за 15 хвилин — у {x} раза більше за звичайне коливання (потрібно від 3.5)",
                   "Δύναμη του κεριού: {ret} σε 15 λεπτά, {x} φορές μια συνηθισμένη διακύμανση (χρειάζεται 3.5 ή περισσότερο)",
                   "Fuerza de la vela: {ret} en 15 minutos, {x} veces una oscilación normal (hace falta 3.5 o más)"),
    "rw.chk.coins": _w("Монета — одна из 18 крупных, на которых правило проверено на истории",
                       "The coin is one of the 18 large ones the rule was tested on in history",
                       "Монета — одна з 18 великих, на яких правило перевірене на історії",
                       "Το νόμισμα είναι ένα από τα 18 μεγάλα στα οποία ο κανόνας ελέγχθηκε ιστορικά",
                       "La moneda es una de las 18 grandes con las que se probó la regla en el histórico"),
    "rw.chk.fresh": _w("Свежесть данных: цены и сигнал не старше 10 минут",
                       "Fresh data: prices and the signal no older than 10 minutes",
                       "Свіжість даних: ціни й сигнал не старші за 10 хвилин",
                       "Φρέσκα δεδομένα: τιμές και σήμα όχι παλαιότερα από 10 λεπτά",
                       "Datos frescos: precios y señal de hace 10 minutos como mucho"),
    "rw.chk.limits": _w("Лимиты риска счёта: дневной стоп −3%, стоп по просадке −15%",
                        "The account's risk limits: a −3% daily stop, a −15% drawdown stop",
                        "Ліміти ризику рахунку: денний стоп −3%, стоп за просіданням −15%",
                        "Όρια κινδύνου του λογαριασμού: ημερήσιο stop −3%, stop πτώσης −15%",
                        "Límites de riesgo de la cuenta: stop diario −3%, stop por caída −15%"),
    "rw.chk.slots": _w("Не больше {n} сделок этого правила одновременно и одна сделка на монету",
                       "No more than {n} trades of this rule at once and one trade per coin",
                       "Не більше {n} угод цього правила одночасно й одна угода на монету",
                       "Όχι περισσότερες από {n} συναλλαγές αυτού του κανόνα ταυτόχρονα και μία ανά νόμισμα",
                       "No más de {n} operaciones de esta regla a la vez y una por moneda"),
    "rw.chk.real": _w("Реальный счёт: плечо {lev}x, риск сделки — не больше {risk} бюджета фьючерсов",
                      "The real account: {lev}x leverage, a trade's risk no more than {risk} of the futures budget",
                      "Реальний рахунок: плече {lev}x, ризик угоди — не більше {risk} бюджету ф'ючерсів",
                      "Πραγματικός λογαριασμός: μόχλευση {lev}x, κίνδυνος συναλλαγής όχι πάνω από {risk} του προϋπολογισμού των futures",
                      "Cuenta real: apalancamiento {lev}x, el riesgo de una operación no supera el {risk} del presupuesto de futuros"),
    "rw.checked": _w("Что система проверила перед входом", "What the system checked before the entry", "Що система перевірила перед входом",
                     "Τι έλεγξε το σύστημα πριν από την είσοδο", "Qué comprobó el sistema antes de entrar"),
    # ------------------------------------------------------------------ the rule's record
    "rw.hist.D": _w(
        "Правило проверено на истории 2023–2026 на 18 крупных монетах: в портфельной проверке — 394 сделки за 3,7 года; по всем "
        "сигналам 55% в плюсе, в среднем +1.77% за сутки, каждый год в плюсе.",
        "The rule was tested on 2023–2026 history on 18 large coins: 394 trades in 3.7 years in the portfolio test; across all "
        "signals 55% profitable, +1.77% per day on average, every year positive.",
        "Правило перевірене на історії 2023–2026 на 18 великих монетах: у портфельній перевірці — 394 угоди за 3,7 року; за всіма "
        "сигналами 55% у плюсі, у середньому +1.77% за добу, кожен рік у плюсі.",
        "Ο κανόνας ελέγχθηκε στο ιστορικό 2023–2026 σε 18 μεγάλα νομίσματα: 394 συναλλαγές σε 3,7 χρόνια στον έλεγχο χαρτοφυλακίου· "
        "σε όλα τα σήματα 55% κερδοφόρα, κατά μέσο όρο +1.77% την ημέρα, κάθε χρονιά θετική.",
        "La regla se probó en el histórico 2023–2026 con 18 monedas grandes: 394 operaciones en 3,7 años en la prueba de cartera; "
        "en todas las señales, 55% con ganancia, +1.77% al día de media, todos los años en positivo."),
    "rw.hist.n": _w(
        "Правило проверено на истории {period}: {n} {trades}, {w}% в плюсе, в среднем {avg} на сделку.",
        "The rule was tested on {period} history: {n} {trades}, {w}% profitable, {avg} per trade on average.",
        "Правило перевірене на історії {period}: {n} {trades}, {w}% у плюсі, у середньому {avg} на угоду.",
        "Ο κανόνας ελέγχθηκε στο ιστορικό {period}: {n} {trades}, {w}% κερδοφόρες, κατά μέσο όρο {avg} ανά συναλλαγή.",
        "La regla se probó en el histórico {period}: {n} {trades}, {w}% con ganancia, {avg} por operación de media."),
    "rw.hist.years": _w(" Каждый год проверки — в плюсе.", " Every year of the test was positive.", " Кожен рік перевірки — у плюсі.",
                        " Κάθε χρονιά του ελέγχου ήταν θετική.", " Todos los años de la prueba, en positivo."),
    "rw.live": _w("На реальном счёте CRYPTO NOVA AI с {d} сделок по этому правилу закрыто: {n}, из них в плюсе: {w}.",
                  "On CRYPTO NOVA AI's real account since {d}, trades of this rule closed: {n}, in profit: {w}.",
                  "На реальному рахунку CRYPTO NOVA AI з {d} угод за цим правилом закрито: {n}, з них у плюсі: {w}.",
                  "Στον πραγματικό λογαριασμό του CRYPTO NOVA AI από {d}, κλειστές συναλλαγές αυτού του κανόνα: {n}, με κέρδος: {w}.",
                  "En la cuenta real de CRYPTO NOVA AI desde el {d}, operaciones cerradas de esta regla: {n}; con ganancia: {w}."),
    "rw.live0": _w("На реальном счёте CRYPTO NOVA AI это первая сделка по этому правилу.",
                   "On CRYPTO NOVA AI's real account this is the rule's first trade.",
                   "На реальному рахунку CRYPTO NOVA AI це перша угода за цим правилом.",
                   "Στον πραγματικό λογαριασμό του CRYPTO NOVA AI αυτή είναι η πρώτη συναλλαγή του κανόνα.",
                   "En la cuenta real de CRYPTO NOVA AI es la primera operación de esta regla."),
    "rw.few": _w(" По нескольким сделкам о правиле судить рано.", " A few trades are too few to judge the rule.",
                 " За кількома угодами про правило судити зарано.", " Λίγες συναλλαγές δεν αρκούν για να κριθεί ο κανόνας.",
                 " Con pocas operaciones aún es pronto para juzgar la regla."),
    # ------------------------------------------------------------------ the plan and the risk
    "rw.plan.time": _w(
        "Вход {entry}, аварийный стоп {stop} ({sp} от входа). Главный выход — по времени, через {h} ч; цель на бирже {tgt} — "
        "далёкая страховка.",
        "Entry {entry}, emergency stop {stop} ({sp} from the entry). The main exit is by time, after {h} h; the target on the "
        "exchange, {tgt}, is only a far safeguard.",
        "Вхід {entry}, аварійний стоп {stop} ({sp} від входу). Головний вихід — за часом, через {h} год; ціль на біржі {tgt} — "
        "далека страховка.",
        "Είσοδος {entry}, stop έκτακτης ανάγκης {stop} ({sp} από την είσοδο). Η κύρια έξοδος είναι χρονική, μετά από {h} ώρες· ο "
        "στόχος στο χρηματιστήριο, {tgt}, είναι μόνο μια μακρινή ασφάλεια.",
        "Entrada {entry}, stop de emergencia {stop} ({sp} desde la entrada). La salida principal es por tiempo, a las {h} h; el "
        "objetivo en el exchange, {tgt}, es solo un seguro lejano."),
    "rw.plan.tgt": _w("Вход {entry}, стоп {stop} ({sp} от входа), цель {tgt}.", "Entry {entry}, stop {stop} ({sp} from the entry), target {tgt}.",
                      "Вхід {entry}, стоп {stop} ({sp} від входу), ціль {tgt}.", "Είσοδος {entry}, stop {stop} ({sp} από την είσοδο), στόχος {tgt}.",
                      "Entrada {entry}, stop {stop} ({sp} desde la entrada), objetivo {tgt}."),
    "rw.risk": _w("Плечо {lev}x. Под риском до стопа — около {r}: не больше {risk} бюджета фьючерсов ({cap}).",
                  "Leverage {lev}x. At risk down to the stop: about {r}, no more than {risk} of the futures budget ({cap}).",
                  "Плече {lev}x. Під ризиком до стопа — близько {r}: не більше {risk} бюджету ф'ючерсів ({cap}).",
                  "Μόχλευση {lev}x. Σε κίνδυνο έως το stop: περίπου {r}, όχι πάνω από {risk} του προϋπολογισμού των futures ({cap}).",
                  "Apalancamiento {lev}x. En riesgo hasta el stop: unos {r}, no más del {risk} del presupuesto de futuros ({cap})."),
    "rw.risk_cap": _w("Плечо {lev}x. Под риском до стопа — не больше {cap}: {risk} бюджета фьючерсов.",
                      "Leverage {lev}x. At risk down to the stop: no more than {cap}, {risk} of the futures budget.",
                      "Плече {lev}x. Під ризиком до стопа — не більше {cap}: {risk} бюджету ф'ючерсів.",
                      "Μόχλευση {lev}x. Σε κίνδυνο έως το stop: όχι πάνω από {cap}, {risk} του προϋπολογισμού των futures.",
                      "Apalancamiento {lev}x. En riesgo hasta el stop: no más de {cap}, el {risk} del presupuesto de futuros."),
    "rw.result": _w("Итог сделки: {usd} ({mv} по цене).", "The trade's result: {usd} ({mv} in price).", "Підсумок угоди: {usd} ({mv} за ціною).",
                    "Αποτέλεσμα της συναλλαγής: {usd} ({mv} στην τιμή).", "Resultado de la operación: {usd} ({mv} en precio)."),
    # ------------------------------------------------------------------ build 54: a copy of a whale's trade (owner 04.11: «Сделка по
    # правилам CRYPTO NOVA… 3 из 3» says nothing for a copy) — who the whale is, what it did, its record, the plan with a reference target
    "rw.w.title": _w("Копия сделки кита", "A copy of a whale's trade", "Копія угоди кита", "Αντίγραφο συναλλαγής «φάλαινας»",
                     "Copia de la operación de una ballena"),
    "rw.w.sig": _w("Система повторила сделку крупного трейдера («кита») {name}{src}: {side} по {sym}.",
                   "The system copied a trade of a big trader (a «whale»), {name}{src}: {side} on {sym}.",
                   "Система повторила угоду великого трейдера («кита») {name}{src}: {side} по {sym}.",
                   "Το σύστημα αντέγραψε μια συναλλαγή μεγάλου trader («φάλαινας»), {name}{src}: {side} στο {sym}.",
                   "El sistema copió una operación de un gran trader (una «ballena»), {name}{src}: {side} en {sym}."),
    "rw.w.long": _w("ставка на рост", "a bet on a rise", "ставка на зростання", "στοίχημα σε άνοδο", "una apuesta al alza"),
    "rw.w.short": _w("ставка на падение", "a bet on a fall", "ставка на падіння", "στοίχημα σε πτώση", "una apuesta a la baja"),
    "rw.w.pos": _w(" Кит вошёл по {we}{wl}{wn}.", " The whale entered at {we}{wl}{wn}.", " Кит увійшов за {we}{wl}{wn}.",
                   " Η φάλαινα μπήκε στα {we}{wl}{wn}.", " La ballena entró a {we}{wl}{wn}."),
    "rw.w.lev": _w(" с плечом {l}x", " with {l}x leverage", " з плечем {l}x", " με μόχλευση {l}x", " con apalancamiento {l}x"),
    "rw.w.size": _w(", его позиция — около {u}", ", its position is about {u}", ", його позиція — близько {u}", ", η θέση της είναι περίπου {u}",
                    ", su posición es de unos {u}"),
    "rw.w.src.okx": _w("лид-трейдер копитрейдинга OKX", "an OKX copy-trading lead trader", "лід-трейдер копітрейдингу OKX",
                       "lead trader του copy trading της OKX", "lead trader del copy trading de OKX"),
    "rw.w.src.hl": _w("Hyperliquid, его сделки видны в блокчейне", "Hyperliquid, its trades are visible on the blockchain",
                      "Hyperliquid, його угоди видно в блокчейні", "Hyperliquid, οι συναλλαγές της φαίνονται στο blockchain",
                      "Hyperliquid, sus operaciones se ven en la blockchain"),
    "rw.w.why": _w(" Система копирует только китов из своего отобранного списка и только с защитным стопом.",
                   " The system copies only whales from its own vetted list, and only with a protective stop.",
                   " Система копіює лише китів зі свого відібраного списку й лише із захисним стопом.",
                   " Το σύστημα αντιγράφει μόνο φάλαινες από τη δική του επιλεγμένη λίστα και μόνο με προστατευτικό stop.",
                   " El sistema solo copia ballenas de su propia lista seleccionada y siempre con un stop de protección."),
    "rw.w.chk.list": _w("Кит в отобранном списке системы: закрытых сделок — {n}, в плюсе — {w}%",
                        "The whale is on the system's vetted list: {n} closed trades, {w}% profitable",
                        "Кит у відібраному списку системи: {n} закритих угод, {w}% у плюсі",
                        "Η φάλαινα είναι στη λίστα του συστήματος: {n} κλειστές συναλλαγές, {w}% κερδοφόρες",
                        "La ballena está en la lista del sistema: {n} operaciones cerradas, {w}% con ganancia"),
    "rw.w.chk.30d": _w("Кит в плюсе за 30 дней: {u}", "The whale is in profit over 30 days: {u}", "Кит у плюсі за 30 днів: {u}",
                       "Η φάλαινα είναι σε κέρδος στις 30 ημέρες: {u}", "La ballena gana en 30 días: {u}"),
    "rw.w.chk.stop": _w("Защитный стоп {sp} от входа — у каждой копии, даже если у кита его нет",
                        "A protective stop {sp} from the entry on every copy, even when the whale has none",
                        "Захисний стоп {sp} від входу — у кожної копії, навіть якщо в кита його немає",
                        "Προστατευτικό stop {sp} από την είσοδο σε κάθε αντίγραφο, ακόμη κι αν η φάλαινα δεν έχει",
                        "Un stop de protección a {sp} de la entrada en cada copia, aunque la ballena no lo tenga"),
    "rw.w.hist": _w("История кита: закрытых сделок — {n}, в плюсе — {w}%{pf}{d30}.",
                    "The whale's record: {n} closed trades, {w}% profitable{pf}{d30}.",
                    "Історія кита: {n} закритих угод, {w}% у плюсі{pf}{d30}.",
                    "Το ιστορικό της φάλαινας: {n} κλειστές συναλλαγές, {w}% κερδοφόρες{pf}{d30}.",
                    "El historial de la ballena: {n} operaciones cerradas, {w}% con ganancia{pf}{d30}."),
    "rw.w.pf": _w(", прибыль к убыткам — {x} к 1", ", profits {x} times the losses", ", прибуток більший за збитки в {x} раза",
                  ", κέρδη {x} φορές τις ζημιές", ", ganancias {x} veces las pérdidas"),
    "rw.w.d30": _w(", за 30 дней {u}", ", {u} over 30 days", ", за 30 днів {u}", ", {u} στις 30 ημέρες", ", {u} en 30 días"),
    "rw.w.desk": _w("Копирование китов на демо-счёте системы с {d}: {n} сделок, в плюсе {w}.",
                    "Whale copying on the system's demo account since {d}: {n} trades, {w} in profit.",
                    "Копіювання китів на демо-рахунку системи з {d}: {n} угод, у плюсі {w}.",
                    "Αντιγραφή φαλαινών στον demo λογαριασμό του συστήματος από {d}: {n} συναλλαγές, {w} με κέρδος.",
                    "Copia de ballenas en la cuenta demo del sistema desde el {d}: {n} operaciones, {w} con ganancia."),
    "rw.w.plan": _w("Вход {entry}, стоп {stop} ({sp} от входа). Ориентир — {tgt}: ход 2R, вдвое больше стопа. Выходим, когда кит закроет позицию, или по стопу.",
                    "Entry {entry}, stop {stop} ({sp} from the entry). Guide: {tgt}, a 2R move, twice the stop. We exit when the whale closes, or at the stop.",
                    "Вхід {entry}, стоп {stop} ({sp} від входу). Орієнтир — {tgt}: хід 2R, удвічі більший за стоп. Виходимо, коли кит закриє позицію, або за стопом.",
                    "Είσοδος {entry}, stop {stop} ({sp} από την είσοδο). Οδηγός: {tgt}, κίνηση 2R, διπλάσια από το stop. Βγαίνουμε όταν κλείσει η φάλαινα ή στο stop.",
                    "Entrada {entry}, stop {stop} ({sp} desde la entrada). Guía: {tgt}, un movimiento 2R, el doble del stop. Salimos cuando la ballena cierra o en el stop."),
    "rw.w.ctx": _w("Рынок при входе: {x}.", "The market at the entry: {x}.", "Ринок під час входу: {x}.", "Η αγορά στην είσοδο: {x}.",
                   "El mercado al entrar: {x}."),
    "rw.w.c.btc": _w("биткоин за сутки {v}", "bitcoin {v} in 24 h", "біткоїн за добу {v}", "bitcoin {v} σε 24 ώρες", "bitcoin {v} en 24 h"),
    "rw.w.c.fund": _w("фандинг {v} за 8 ч", "funding {v} per 8 h", "фандинг {v} за 8 год", "funding {v} ανά 8 ώρες", "funding {v} cada 8 h"),
    "rw.w.c.oi": _w("открытый интерес {v}", "open interest {v}", "відкритий інтерес {v}", "open interest {v}", "interés abierto {v}"),
    "rw.w.c.sup": _w("поддержка {v}", "support {v}", "підтримка {v}", "στήριξη {v}", "soporte {v}"),
    "rw.w.c.res": _w("сопротивление {v}", "resistance {v}", "опір {v}", "αντίσταση {v}", "resistencia {v}"),
    "rw.w.exit": _w("Кит закрыл позицию — копия закрылась вместе с ним.", "The whale closed its position, and the copy closed with it.",
                    "Кит закрив позицію — копія закрилася разом із ним.", "Η φάλαινα έκλεισε τη θέση της και το αντίγραφο έκλεισε μαζί της.",
                    "La ballena cerró su posición y la copia se cerró con ella."),
    # ------------------------------------------------------------------ the spot coin
    "rw.c.core": _w(
        "{sym} — ядро портфеля. BTC, ETH и SOL система держит всегда: докупает, когда доля ниже плана, и продаёт лишнее, когда "
        "доля сильно выросла. Доля {sym} сейчас {share} счёта.",
        "{sym} is the portfolio's core. The system always holds BTC, ETH and SOL: it buys more when the share is below plan and sells "
        "the excess when the share has grown a lot. {sym} is now {share} of the account.",
        "{sym} — ядро портфеля. BTC, ETH і SOL система тримає завжди: докуповує, коли частка нижча за план, і продає зайве, коли "
        "частка сильно зросла. Частка {sym} зараз {share} рахунку.",
        "Το {sym} είναι ο πυρήνας του χαρτοφυλακίου. Το σύστημα κρατά πάντα BTC, ETH και SOL: αγοράζει όταν το μερίδιο είναι κάτω από "
        "το σχέδιο και πουλά το επιπλέον όταν έχει ανέβει πολύ. Το {sym} είναι τώρα {share} του λογαριασμού.",
        "{sym} es el núcleo de la cartera. El sistema siempre mantiene BTC, ETH y SOL: compra más cuando el peso está por debajo del "
        "plan y vende lo que sobra cuando ha crecido mucho. {sym} es ahora el {share} de la cuenta."),
    "rw.c.alt": _w(
        "{sym} прошла отбор таблицы «Что брать на спот» и правила портфеля: тренд, оборот и качество проекта. Доля каждой монеты "
        "ограничена, чтобы одна не решала результат всего портфеля. Доля {sym} сейчас {share} счёта.",
        "{sym} passed the «What to buy on spot» table and the portfolio's rules: trend, turnover and the project's quality. Each "
        "coin's share is capped so that one coin never decides the whole portfolio. {sym} is now {share} of the account.",
        "{sym} пройшла відбір таблиці «Що брати на спот» і правила портфеля: тренд, обіг і якість проєкту. Частка кожної монети "
        "обмежена, щоб одна не вирішувала результат усього портфеля. Частка {sym} зараз {share} рахунку.",
        "Το {sym} πέρασε τον πίνακα «Τι να αγοράσω σε spot» και τους κανόνες του χαρτοφυλακίου: τάση, όγκος συναλλαγών και ποιότητα "
        "του έργου. Το μερίδιο κάθε νομίσματος έχει όριο, ώστε κανένα να μην κρίνει μόνο του το αποτέλεσμα. Το {sym} είναι τώρα "
        "{share} του λογαριασμού.",
        "{sym} pasó la tabla «Qué comprar en spot» y las reglas de la cartera: tendencia, volumen y calidad del proyecto. El peso de "
        "cada moneda está limitado para que ninguna decida sola el resultado. {sym} es ahora el {share} de la cuenta."),
    "rw.c.rating": _w("Анкета проекта: {v} из 100 (нужно от 40)", "Project rating: {v}/100 (needs 40+)", "Анкета проєкту: {v} зі 100 (потрібно від 40)",
                      "Βαθμολογία έργου: {v}/100 (χρειάζεται 40+)", "Puntuación del proyecto: {v}/100 (mínimo 40)"),
    "rw.c.consultant": _w("Оценка консультанта: тренд {m}, фундамент {l} из 100 (нужно от 60)",
                          "Consultant's score: trend {m}, fundamentals {l} out of 100 (needs 60+)",
                          "Оцінка консультанта: тренд {m}, фундамент {l} зі 100 (потрібно від 60)",
                          "Βαθμός συμβούλου: τάση {m}, θεμελιώδη {l} στα 100 (χρειάζεται 60+)",
                          "Nota del consultor: tendencia {m}, fundamentales {l} de 100 (mínimo 60)"),
    "rw.c.trend200": _w("Тренд: цена {x} к уровню 200 дней назад", "Trend: price {x} vs 200 days ago", "Тренд: ціна {x} до рівня 200 днів тому",
                        "Τάση: τιμή {x} σε σχέση με 200 ημέρες πριν", "Tendencia: precio {x} frente a hace 200 días"),
    "rw.c.overheat": _w("Без перегрева: за 7 дней {c7}, от максимума 30 дней {h30}", "No overheating: {c7} in 7 days, {h30} from the 30-day high",
                        "Без перегріву: за 7 днів {c7}, від максимуму 30 днів {h30}", "Χωρίς υπερθέρμανση: {c7} σε 7 ημέρες, {h30} από το υψηλό 30 ημερών",
                        "Sin sobrecalentamiento: {c7} en 7 días, {h30} desde el máximo de 30 días"),
    "rw.c.ath": _w("От исторического максимума {x} (предел −90%)", "{x} from the all-time high (limit −90%)", "Від історичного максимуму {x} (межа −90%)",
                   "{x} από το ιστορικό υψηλό (όριο −90%)", "{x} desde el máximo histórico (límite −90%)"),
    "rw.c.vol": _w("Ликвидность: оборот {v} в сутки", "Liquidity: {v} traded a day", "Ліквідність: обіг {v} на добу",
                   "Ρευστότητα: όγκος {v} την ημέρα", "Liquidez: {v} negociados al día"),
    "rw.c.dilution": _w("В обращении ~{x} токенов", "~{x} of the tokens in circulation", "В обігу ~{x} токенів",
                        "~{x} των tokens σε κυκλοφορία", "~{x} de los tokens en circulación"),
    "rw.c.regime": _w("Режим рынка: биткоин в бычьем режиме", "Market regime: bitcoin in a bull regime", "Режим ринку: біткоїн у бичачому режимі",
                      "Καθεστώς αγοράς: το bitcoin σε ανοδικό καθεστώς", "Régimen del mercado: bitcoin en régimen alcista"),
    "rw.c.regime_no": _w("Режим рынка: биткоин не в бычьем режиме", "Market regime: bitcoin not in a bull regime",
                         "Режим ринку: біткоїн не в бичачому режимі", "Καθεστώς αγοράς: το bitcoin όχι σε ανοδικό καθεστώς",
                         "Régimen del mercado: bitcoin no está en régimen alcista"),
    "rw.c.backtest": _w("Проверка ядра на истории 2021–2026: доли BTC / ETH / SOL 50 / 25 / 15 дали ×{x}, худшая просадка {dd}",
                        "The core's test on 2021–2026 history: BTC / ETH / SOL at 50 / 25 / 15 gave ×{x}, worst drawdown {dd}",
                        "Перевірка ядра на історії 2021–2026: частки BTC / ETH / SOL 50 / 25 / 15 дали ×{x}, найгірше просідання {dd}",
                        "Έλεγχος του πυρήνα στο ιστορικό 2021–2026: BTC / ETH / SOL με 50 / 25 / 15 έδωσαν ×{x}, χειρότερη πτώση {dd}",
                        "Prueba del núcleo en el histórico 2021–2026: BTC / ETH / SOL al 50 / 25 / 15 dieron ×{x}, peor caída {dd}"),
    "rw.c.status": _w("Сейчас в таблице: «{s}»", "In the table now: «{s}»", "Зараз у таблиці: «{s}»", "Στον πίνακα τώρα: «{s}»", "En la tabla ahora: «{s}»"),
    "rw.c.st.buy": _w("можно брать", "can buy", "можна брати", "μπορεί να αγοραστεί", "se puede comprar"),
    "rw.c.st.add": _w("можно добрать", "can add", "можна добрати", "μπορεί να συμπληρωθεί", "se puede ampliar"),
    "rw.c.st.small": _w("на небольшую сумму", "a small amount only", "на невелику суму", "μόνο μικρό ποσό", "solo una cantidad pequeña"),
    "rw.c.st.wait": _w("ждём", "wait", "чекаємо", "αναμονή", "esperar"),
    "rw.c.st.avoid": _w("не брать", "don't buy", "не брати", "όχι αγορά", "no comprar"),
    "rw.c.wait_note": _w("Монету держим, пока таблица не скажет «не брать»; новых покупок по ней сейчас нет.",
                         "The coin is held until the table says «don't buy»; no new buys of it right now.",
                         "Монету тримаємо, доки таблиця не скаже «не брати»; нових купівель зараз немає.",
                         "Το νόμισμα κρατιέται μέχρι ο πίνακας να πει «όχι αγορά»· νέες αγορές του τώρα δεν γίνονται.",
                         "La moneda se mantiene hasta que la tabla diga «no comprar»; ahora no hay compras nuevas."),
    "rw.c.scale": _w("Таблица «Что брать на спот» каждый день проверяет {n} монет; сейчас все фильтры проходят {k}.",
                     "The «What to buy on spot» table checks {n} coins every day; {k} pass every filter now.",
                     "Таблиця «Що брати на спот» щодня перевіряє {n} монет; зараз усі фільтри проходять {k}.",
                     "Ο πίνακας «Τι να αγοράσω σε spot» ελέγχει κάθε μέρα {n} νομίσματα· τώρα όλα τα φίλτρα τα περνούν {k}.",
                     "La tabla «Qué comprar en spot» revisa {n} monedas cada día; ahora {k} pasan todos los filtros."),
    "rw.c.checked": _w("Что проверено", "What was checked", "Що перевірено", "Τι ελέγχθηκε", "Qué se comprobó"),
    # ------------------------------------------------------------------ the calm drawdown line and the 24/7 line
    "rw.calm": _w(
        "Рынок в просадке: биткоин с {d} {btc}. Система держит стопы и ограничивает риск, поэтому счёт снизился меньше рынка: {acct} "
        "против {btc}.",
        "The market is in a drawdown: bitcoin {btc} since {d}. The system keeps its stops and limits the risk, so the account fell "
        "less than the market: {acct} against {btc}.",
        "Ринок у просіданні: біткоїн з {d} {btc}. Система тримає стопи й обмежує ризик, тому рахунок знизився менше за ринок: {acct} "
        "проти {btc}.",
        "Η αγορά είναι σε πτώση: το bitcoin {btc} από {d}. Το σύστημα κρατά τα stops και περιορίζει τον κίνδυνο, γι' αυτό ο "
        "λογαριασμός έπεσε λιγότερο από την αγορά: {acct} έναντι {btc}.",
        "El mercado está en caída: bitcoin {btc} desde el {d}. El sistema mantiene sus stops y limita el riesgo, por eso la cuenta "
        "bajó menos que el mercado: {acct} frente a {btc}."),
    "rw.allday": _w(
        "Система работает круглосуточно: проверяет рынок каждые {s} секунд, а новые идеи сначала испытывает на истории — из {a} "
        "вариантов ночной лаборатории проверку прошли {p}.",
        "The system works around the clock: it checks the market every {s} seconds and tests new ideas on history first — {p} of "
        "the night lab's {a} variants passed.",
        "Система працює цілодобово: перевіряє ринок кожні {s} секунд, а нові ідеї спершу випробовує на історії — з {a} варіантів "
        "нічної лабораторії перевірку пройшли {p}.",
        "Το σύστημα δουλεύει όλο το εικοσιτετράωρο: ελέγχει την αγορά κάθε {s} δευτερόλεπτα και δοκιμάζει πρώτα τις νέες ιδέες στο "
        "ιστορικό — από τις {a} παραλλαγές του νυχτερινού εργαστηρίου πέρασαν {p}.",
        "El sistema funciona día y noche: revisa el mercado cada {s} segundos y prueba primero las ideas nuevas en el histórico: de "
        "las {a} variantes del laboratorio nocturno pasaron {p}."),
}
T.update(W)

# build 55 (owner 04.11 on build 54: «Своей цели у копии нет. Выход вместе с китом. Ориентир 2R — вдвое больше стопа» is unclear): a
# whale copy explains itself as a short summary + a target line under the live bar + «Подробнее» sections — the strategy, the whale,
# what he did, what we track, our entry, our stop, the target and the exit. Only the numbers the whale desk recorded.
W55: dict[str, dict[str, str]] = {
    "w5.strategy": _w("Стратегия «Копирование китов»", "The «Whale copying» strategy", "Стратегія «Копіювання китів»",
                      "Η στρατηγική «Αντιγραφή φαλαινών»", "La estrategia «Copia de ballenas»"),
    "w5.sum": _w("Сделку открыла стратегия «Копирование китов»: повторили {side} {sym} за китом {name}{src}. Ориентир — {tgt} ({tp}); выходим вместе с китом или по стопу {stop}.",
                 "Opened by the «Whale copying» strategy: we repeated the {side} on {sym} of the whale {name}{src}. Guide: {tgt} ({tp}); we exit with the whale or at the stop {stop}.",
                 "Угоду відкрила стратегія «Копіювання китів»: повторили {side} {sym} за китом {name}{src}. Орієнтир — {tgt} ({tp}); виходимо разом із китом або за стопом {stop}.",
                 "Την άνοιξε η στρατηγική «Αντιγραφή φαλαινών»: επαναλάβαμε το {side} της φάλαινας {name}{src} στο {sym}. Οδηγός: {tgt} ({tp}). Βγαίνουμε μαζί με τη φάλαινα ή στο stop {stop}.",
                 "La abrió la estrategia «Copia de ballenas»: repetimos el {side} de la ballena {name}{src} en {sym}. Guía: {tgt} ({tp}); salimos con la ballena o en el stop {stop}."),
    "w5.sum0": _w("Сделку открыла стратегия «Копирование китов»: повторили {side} {sym} за китом {name}{src}. Выходим вместе с китом или по стопу.",
                  "Opened by the «Whale copying» strategy: we repeated the {side} on {sym} of the whale {name}{src}. We exit with the whale or at the stop.",
                  "Угоду відкрила стратегія «Копіювання китів»: повторили {side} {sym} за китом {name}{src}. Виходимо разом із китом або за стопом.",
                  "Την άνοιξε η στρατηγική «Αντιγραφή φαλαινών»: επαναλάβαμε το {side} της φάλαινας {name}{src} στο {sym}. Βγαίνουμε μαζί με τη φάλαινα ή στο stop.",
                  "La abrió la estrategia «Copia de ballenas»: repetimos el {side} de la ballena {name}{src} en {sym}. Salimos con la ballena o en el stop."),
    "w5.long": _w("лонг", "long", "лонг", "long", "largo"),
    "w5.short": _w("шорт", "short", "шорт", "short", "corto"),
    "w5.tline": _w("Ориентир {tgt} ({tp}{usd}) — ход вдвое больше стопа. Выход: кит закрыл позицию или стоп {stop}.",
                   "Guide {tgt} ({tp}{usd}), a move twice the stop. Exit: the whale closes, or the stop {stop}.",
                   "Орієнтир {tgt} ({tp}{usd}) — хід удвічі більший за стоп. Вихід: кит закрив позицію або стоп {stop}.",
                   "Οδηγός {tgt} ({tp}{usd}), κίνηση διπλάσια από το stop. Έξοδος: η φάλαινα κλείνει ή το stop {stop}.",
                   "Guía {tgt} ({tp}{usd}), un movimiento el doble del stop. Salida: la ballena cierra o el stop {stop}."),
    "w5.usd": _w(", ≈ {u}", ", ≈ {u}", ", ≈ {u}", ", ≈ {u}", ", ≈ {u}"),
    "w5.s.strategy": _w("Стратегия", "Strategy", "Стратегія", "Στρατηγική", "Estrategia"),
    "w5.strategy.t": _w("«Копирование китов»: система каждые ~30 минут смотрит позиции 5 лучших трейдеров из своего списка (за 60 дней не меньше 20 решений, 70% и больше в плюсе, прибыль больше убытков минимум вдвое) и повторяет их сделки — всегда со своим стопом.",
                        "«Whale copying»: about every 30 minutes the system checks the positions of the 5 best traders on its list (over 60 days at least 20 decisions, 70% or more in profit, profits at least twice the losses) and repeats their trades, always with its own stop.",
                        "«Копіювання китів»: система кожні ~30 хвилин дивиться позиції 5 найкращих трейдерів зі свого списку (за 60 днів не менше 20 рішень, 70% і більше в плюсі, прибуток більший за збитки щонайменше вдвічі) і повторює їхні угоди — завжди зі своїм стопом.",
                        "«Αντιγραφή φαλαινών»: περίπου κάθε 30 λεπτά το σύστημα ελέγχει τις θέσεις των 5 καλύτερων traders της λίστας του (σε 60 ημέρες τουλάχιστον 20 αποφάσεις, 70% ή περισσότερο με κέρδος, κέρδη τουλάχιστον διπλάσια από τις ζημιές) και επαναλαμβάνει τις συναλλαγές τους, πάντα με δικό του stop.",
                        "«Copia de ballenas»: cada ~30 minutos el sistema revisa las posiciones de los 5 mejores traders de su lista (en 60 días al menos 20 decisiones, 70% o más con ganancia, ganancias al menos el doble de las pérdidas) y repite sus operaciones, siempre con su propio stop."),
    "w5.s.whale": _w("Кит", "The whale", "Кит", "Η φάλαινα", "La ballena"),
    "w5.whale.who": _w("{name} — {src}.", "{name}: {src}.", "{name} — {src}.", "{name}: {src}.", "{name}: {src}."),
    "w5.whale.style": _w(" Стиль — {s}.", " Style: {s}.", " Стиль — {s}.", " Στυλ: {s}.", " Estilo: {s}."),
    "w5.whale.hold": _w(" Обычно держит позицию {h}.", " Usually holds a position {h}.", " Зазвичай тримає позицію {h}.",
                        " Συνήθως κρατά μια θέση {h}.", " Suele mantener una posición {h}."),
    "w5.h": _w("около {n} ч", "about {n} h", "близько {n} год", "περίπου {n} ώρες", "unas {n} h"),
    "w5.d": _w("около {n} {u}", "about {n} days", "близько {n} {u}", "περίπου {n} ημέρες", "unos {n} días"),
    "w5.s.did": _w("Что сделал кит", "What the whale did", "Що зробив кит", "Τι έκανε η φάλαινα", "Qué hizo la ballena"),
    "w5.did": _w("Открыл {side} {sym}{we}{wl}{wn}.", "Opened a {side} on {sym}{we}{wl}{wn}.", "Відкрив {side} {sym}{we}{wl}{wn}.",
                 "Άνοιξε {side} στο {sym}{we}{wl}{wn}.", "Abrió un {side} en {sym}{we}{wl}{wn}."),
    "w5.did.we": _w(" по {p}", " at {p}", " за {p}", " στα {p}", " a {p}"),
    "w5.did.wl": _w(", плечо {l}x", ", {l}x leverage", ", плече {l}x", ", μόχλευση {l}x", ", apalancamiento {l}x"),
    "w5.did.wn": _w(", позиция около {u}", ", a position of about {u}", ", позиція близько {u}", ", θέση περίπου {u}", ", una posición de unos {u}"),
    "w5.did.at": _w(" Система заметила это {d}.", " The system saw it on {d}.", " Система помітила це {d}.", " Το σύστημα το είδε στις {d}.",
                    " El sistema lo vio el {d}."),
    "w5.s.track": _w("Что отслеживаем", "What we track", "Що відстежуємо", "Τι παρακολουθούμε", "Qué seguimos"),
    "w5.track": _w("Каждые ~30 минут — его позицию по {sym}: доливает ли он, сокращает или закрывает. Доливки и частичные сокращения мы видим, но копию не меняем; закрыл или развернул позицию — закрываем и мы.",
                   "About every 30 minutes, his {sym} position: does he add, cut or close. We see adds and partial cuts but leave the copy as it is; when he closes or flips, we close too.",
                   "Кожні ~30 хвилин — його позицію по {sym}: чи доливає він, скорочує чи закриває. Доливки й часткові скорочення ми бачимо, але копію не змінюємо; закрив або розвернув позицію — закриваємо й ми.",
                   "Περίπου κάθε 30 λεπτά τη θέση του στο {sym}: αν προσθέτει, μειώνει ή κλείνει. Βλέπουμε τις προσθήκες και τις μερικές μειώσεις αλλά δεν αλλάζουμε το αντίγραφο· όταν κλείσει ή αντιστρέψει, κλείνουμε κι εμείς.",
                   "Cada ~30 minutos, su posición en {sym}: si añade, reduce o cierra. Vemos los añadidos y los recortes parciales, pero no tocamos la copia; cuando cierra o da la vuelta, cerramos también."),
    "w5.track.plus": _w(" Сейчас цена {px} — кит в плюсе: {g} от его входа.", " The price is now {px}: the whale is in profit, {g} from his entry.",
                        " Зараз ціна {px} — кит у плюсі: {g} від його входу.", " Η τιμή τώρα είναι {px}: η φάλαινα κερδίζει, {g} από την είσοδό του.",
                        " El precio ahora es {px}: la ballena gana, {g} desde su entrada."),
    "w5.track.minus": _w(" Сейчас цена {px} — кит пока в минусе: {g} от его входа.", " The price is now {px}: the whale is down for now, {g} from his entry.",
                         " Зараз ціна {px} — кит поки в мінусі: {g} від його входу.", " Η τιμή τώρα είναι {px}: η φάλαινα χάνει προς το παρόν, {g} από την είσοδό του.",
                         " El precio ahora es {px}: la ballena pierde por ahora, {g} desde su entrada."),
    "w5.better": _w("на {g} лучше, чем у кита", "{g} better than the whale's", "на {g} краще, ніж у кита", "{g} καλύτερα από της φάλαινας",
                    "un {g} mejor que la de la ballena"),
    "w5.worse": _w("на {g} хуже, чем у кита", "{g} worse than the whale's", "на {g} гірше, ніж у кита", "{g} χειρότερα από της φάλαινας",
                   "un {g} peor que la de la ballena"),
    "w5.s.entry": _w("Наш вход", "Our entry", "Наш вхід", "Η είσοδός μας", "Nuestra entrada"),
    "w5.entry.new": _w("Мы вошли по {e} — по текущей цене, как только заметили новую позицию кита: цена {g}.",
                       "We entered at {e}, at the market price as soon as we saw the whale's new position: a price {g}.",
                       "Ми увійшли за {e} — за поточною ціною, щойно помітили нову позицію кита: ціна {g}.",
                       "Μπήκαμε στα {e}, στην τρέχουσα τιμή μόλις είδαμε τη νέα θέση της φάλαινας: τιμή {g}.",
                       "Entramos a {e}, al precio de mercado en cuanto vimos la nueva posición de la ballena: un precio {g}."),
    "w5.entry.join": _w("Мы вошли по {e} — в уже открытую позицию кита: цена {g}. Так входим, только пока цена не хуже его входа больше чем на {mx}% и кит не в минусе больше чем на {bt}%; иначе ждём его следующую сделку.",
                        "We entered at {e}, into the whale's already open position: a price {g}. We join only while the price is no more than {mx}% worse than his entry and he is not down more than {bt}%; otherwise we wait for his next trade.",
                        "Ми увійшли за {e} — у вже відкриту позицію кита: ціна {g}. Так входимо, лише поки ціна не гірша за його вхід більше ніж на {mx}% і кит не в мінусі більше ніж на {bt}%; інакше чекаємо його наступну угоду.",
                        "Μπήκαμε στα {e}, σε ήδη ανοιχτή θέση της φάλαινας: τιμή {g}. Μπαίνουμε έτσι μόνο όσο η τιμή δεν είναι χειρότερη από την είσοδό του πάνω από {mx}% και η φάλαινα δεν χάνει πάνω από {bt}%· αλλιώς περιμένουμε την επόμενη συναλλαγή του.",
                        "Entramos a {e}, en una posición de la ballena ya abierta: un precio {g}. Solo entramos así mientras el precio no sea más de un {mx}% peor que su entrada y la ballena no pierda más de un {bt}%; si no, esperamos su siguiente operación."),
    "w5.entry.plain": _w("Мы вошли по {e}.", "We entered at {e}.", "Ми увійшли за {e}.", "Μπήκαμε στα {e}.", "Entramos a {e}."),
    "w5.s.stop": _w("Наш стоп", "Our stop", "Наш стоп", "Το stop μας", "Nuestro stop"),
    "w5.stop.whale": _w("{stop} ({sp} от входа) — там же, где стоп самого кита на бирже. Стоп стоит на бирже OKX и сработает, даже если сервер молчит.",
                        "{stop} ({sp} from the entry), where the whale's own stop sits on the exchange. The stop is placed at OKX and works even if our server is silent.",
                        "{stop} ({sp} від входу) — там само, де стоп самого кита на біржі. Стоп стоїть на біржі OKX і спрацює, навіть якщо сервер мовчить.",
                        "{stop} ({sp} από την είσοδο), εκεί που βρίσκεται το stop της ίδιας της φάλαινας στο χρηματιστήριο. Το stop είναι στην OKX και λειτουργεί ακόμη κι αν ο server σωπαίνει.",
                        "{stop} ({sp} desde la entrada), donde está el stop de la propia ballena en el exchange. El stop está en OKX y funciona aunque el servidor calle."),
    "w5.stop.own": _w("{stop} ({sp} от входа) — защитный стоп системы: кит свой стоп не показывает, поэтому ставим свой на {d}% от входа. Стоп стоит на бирже OKX и сработает, даже если сервер молчит.",
                      "{stop} ({sp} from the entry), the system's protective stop: the whale shows no stop, so we set our own {d}% from the entry. The stop is placed at OKX and works even if our server is silent.",
                      "{stop} ({sp} від входу) — захисний стоп системи: кит свій стоп не показує, тому ставимо свій на {d}% від входу. Стоп стоїть на біржі OKX і спрацює, навіть якщо сервер мовчить.",
                      "{stop} ({sp} από την είσοδο), το προστατευτικό stop του συστήματος: η φάλαινα δεν δείχνει stop, οπότε βάζουμε δικό μας στο {d}% από την είσοδο. Το stop είναι στην OKX και λειτουργεί ακόμη κι αν ο server σωπαίνει.",
                      "{stop} ({sp} desde la entrada), el stop de protección del sistema: la ballena no muestra su stop, así que ponemos el nuestro a un {d}% de la entrada. Está en OKX y funciona aunque el servidor calle."),
    "w5.s.exit": _w("Цель и выход", "Target and exit", "Ціль і вихід", "Στόχος και έξοδος", "Objetivo y salida"),
    "w5.exit.tgt": _w("Ориентир — {tgt} ({tp} от входа{usd}): ход вдвое больше стопа (2R). Это ориентир, а не заявка: на нём сделку не закрываем, ведь кит может держать дольше.",
                      "Guide: {tgt} ({tp} from the entry{usd}), a move twice the stop (2R). It is a guide, not an order: we do not close there, since the whale may hold longer.",
                      "Орієнтир — {tgt} ({tp} від входу{usd}): хід удвічі більший за стоп (2R). Це орієнтир, а не заявка: на ньому угоду не закриваємо, адже кит може тримати довше.",
                      "Οδηγός: {tgt} ({tp} από την είσοδο{usd}), κίνηση διπλάσια από το stop (2R). Είναι οδηγός, όχι εντολή: δεν κλείνουμε εκεί, αφού η φάλαινα μπορεί να κρατήσει περισσότερο.",
                      "Guía: {tgt} ({tp} desde la entrada{usd}), un movimiento el doble del stop (2R). Es una guía, no una orden: no cerramos ahí, porque la ballena puede mantener más tiempo."),
    "w5.exit.usd": _w(", ≈ {u} к результату", ", ≈ {u} to the result", ", ≈ {u} до результату", ", ≈ {u} στο αποτέλεσμα", ", ≈ {u} al resultado"),
    "w5.exit.when": _w(" Выходим в одном из двух случаев: кит закрыл или развернул позицию — закрываем и мы; цена дошла до стопа {stop}. Скользящего стопа у копий нет.",
                       " We exit in one of two cases: the whale closes or flips his position, and we close too; or the price reaches the stop {stop}. Copies have no trailing stop.",
                       " Виходимо в одному з двох випадків: кит закрив або розвернув позицію — закриваємо й ми; ціна дійшла до стопа {stop}. Ковзного стопа в копій немає.",
                       " Βγαίνουμε σε μία από δύο περιπτώσεις: η φάλαινα κλείνει ή αντιστρέφει τη θέση της, και κλείνουμε κι εμείς· ή η τιμή φτάνει στο stop {stop}. Τα αντίγραφα δεν έχουν κινητό stop.",
                       " Salimos en uno de dos casos: la ballena cierra o da la vuelta a su posición, y cerramos también; o el precio llega al stop {stop}. Las copias no tienen stop dinámico."),
    "w5.exit.hold": _w(" Кит обычно держит {h} — примерно столько может длиться и копия.", " The whale usually holds {h}, so the copy may last about as long.",
                       " Кит зазвичай тримає {h} — приблизно стільки може тривати й копія.", " Η φάλαινα συνήθως κρατά {h}, άρα και το αντίγραφο μπορεί να κρατήσει περίπου τόσο.",
                       " La ballena suele mantener {h}, así que la copia puede durar más o menos lo mismo."),
    "w5.s.market": _w("Рынок при входе", "The market at the entry", "Ринок під час входу", "Η αγορά στην είσοδο", "El mercado al entrar"),
    "w5.s.risk": _w("Риск", "Risk", "Ризик", "Κίνδυνος", "Riesgo"),
    "w5.s.record": _w("Результаты стратегии", "The strategy's record", "Результати стратегії", "Το ιστορικό της στρατηγικής", "Resultados de la estrategia"),
}
T.update(W55)


# 05.11 (owner, 09:52: «нигде не надо писать, что это копии китов, копии трейдеров … везде должна быть система CRYPTO NOVA AI … людям
# нужен только результат»): the real futures trade is explained as a signal of the system CRYPTO NOVA AI — the market at the entry
# (trend, levels, BTC, funding, open interest), the plan (entry, stop, target) and the exit rule. No whale, no copy, no trader.
# The document keeps the build 55 shape (title / signal / checks / plan / summary / target_line / sections …) so the app renders it.
SY: dict[str, dict[str, str]] = {
    "sy.title": _w("Сигнал системы CRYPTO NOVA AI", "CRYPTO NOVA AI system signal", "Сигнал системи CRYPTO NOVA AI", "Σήμα του συστήματος CRYPTO NOVA AI",
                   "Señal del sistema CRYPTO NOVA AI"),
    "sy.sig": _w("Система CRYPTO NOVA AI открыла сделку: {side} по {sym}.", "The CRYPTO NOVA AI system opened a trade: {side} on {sym}.",
                 "Система CRYPTO NOVA AI відкрила угоду: {side} по {sym}.", "Το σύστημα CRYPTO NOVA AI άνοιξε συναλλαγή: {side} στο {sym}.",
                 "El sistema CRYPTO NOVA AI abrió una operación: {side} en {sym}."),
    "sy.sig.why": _w(" Вход — по сигналу системы с учётом состояния рынка; у сделки сразу стоит стоп на бирже.",
                     " The entry follows the system's signal and the state of the market; the stop is on the exchange from the start.",
                     " Вхід — за сигналом системи з урахуванням стану ринку; стоп одразу стоїть на біржі.",
                     " Η είσοδος γίνεται με σήμα του συστήματος και με βάση την κατάσταση της αγοράς· το stop μπαίνει αμέσως στο ανταλλακτήριο.",
                     " La entrada sigue la señal del sistema y el estado del mercado; el stop está en el exchange desde el inicio."),
    "sy.chk.trend": _w("Тренд {sym}: {x}", "{sym} trend: {x}", "Тренд {sym}: {x}", "Τάση {sym}: {x}", "Tendencia de {sym}: {x}"),
    # 10.11: {d} = the days the regime's average really covers (terminal regime sma_days: ~106 now, up to 200)
    "sy.tr.bull": _w("цена выше средней за {d} дней", "the price is above its {d}-day average", "ціна вища за середню за {d} днів",
                     "η τιμή είναι πάνω από τον μέσο όρο {d} ημερών", "el precio está por encima de su media de {d} días"),
    "sy.tr.bear": _w("цена ниже средней за {d} дней", "the price is below its {d}-day average", "ціна нижча за середню за {d} днів",
                     "η τιμή είναι κάτω από τον μέσο όρο {d} ημερών", "el precio está por debajo de su media de {d} días"),
    "sy.tr.up": _w("среднесрочно вверх", "up over the medium term", "середньостроково вгору", "ανοδική μεσοπρόθεσμα", "al alza a medio plazo"),
    "sy.tr.down": _w("среднесрочно вниз", "down over the medium term", "середньостроково вниз", "πτωτική μεσοπρόθεσμα", "a la baja a medio plazo"),
    "sy.chk.levels": _w("Уровни при входе: {x}", "Levels at the entry: {x}", "Рівні під час входу: {x}", "Επίπεδα στην είσοδο: {x}",
                        "Niveles al entrar: {x}"),
    "sy.chk.stop": _w("Стоп {sp} от входа стоит на бирже сразу", "The stop, {sp} from the entry, is on the exchange at once",
                      "Стоп {sp} від входу одразу стоїть на біржі", "Το stop, {sp} από την είσοδο, μπαίνει αμέσως στο ανταλλακτήριο",
                      "El stop, a {sp} de la entrada, está en el exchange desde el inicio"),
    "sy.desk": _w("Те же сигналы на демо-счёте системы с {d}: сделок {n}, в плюсе {w}.",
                  "The same signals on the system's demo account since {d}: {n} trades, {w} in profit.",
                  "Ті самі сигнали на демо-рахунку системи з {d}: угод {n}, у плюсі {w}.",
                  "Τα ίδια σήματα στον demo λογαριασμό του συστήματος από {d}: {n} συναλλαγές, κερδοφόρες {w}.",
                  "Las mismas señales en la cuenta demo del sistema desde el {d}: {n} operaciones, {w} con ganancia."),
    "sy.plan": _w("Вход {entry}, стоп {stop} ({sp} от входа), цель {tgt}. Система закрывает позицию при развороте сигнала или по стопу.",
                  "Entry {entry}, stop {stop} ({sp} from the entry), target {tgt}. The system closes the position when the signal reverses or at the stop.",
                  "Вхід {entry}, стоп {stop} ({sp} від входу), ціль {tgt}. Система закриває позицію при розвороті сигналу або за стопом.",
                  "Είσοδος {entry}, stop {stop} ({sp} από την είσοδο), στόχος {tgt}. Το σύστημα κλείνει τη θέση όταν αντιστραφεί το σήμα ή στο stop.",
                  "Entrada {entry}, stop {stop} ({sp} de la entrada), objetivo {tgt}. El sistema cierra la posición cuando la señal se invierte o en el stop."),
    "sy.exit": _w("Сигнал развернулся — система закрыла позицию.", "The signal reversed, and the system closed the position.",
                  "Сигнал розвернувся — система закрила позицію.", "Το σήμα αντιστράφηκε και το σύστημα έκλεισε τη θέση.",
                  "La señal se invirtió y el sistema cerró la posición."),
    "sy.sum": _w("Сигнал системы CRYPTO NOVA AI: {side} {sym}. Цель {tgt} ({tp}), стоп {stop}.",
                 "CRYPTO NOVA AI system signal: {side} {sym}. Target {tgt} ({tp}), stop {stop}.",
                 "Сигнал системи CRYPTO NOVA AI: {side} {sym}. Ціль {tgt} ({tp}), стоп {stop}.",
                 "Σήμα του συστήματος CRYPTO NOVA AI: {side} {sym}. Στόχος {tgt} ({tp}), stop {stop}.",
                 "Señal del sistema CRYPTO NOVA AI: {side} {sym}. Objetivo {tgt} ({tp}), stop {stop}."),
    "sy.sum0": _w("Сигнал системы CRYPTO NOVA AI: {side} {sym}. Стоп стоит на бирже; система закроет позицию при развороте сигнала или по стопу.",
                  "CRYPTO NOVA AI system signal: {side} {sym}. The stop is on the exchange; the system closes the position when the signal reverses or at the stop.",
                  "Сигнал системи CRYPTO NOVA AI: {side} {sym}. Стоп стоїть на біржі; система закриє позицію при розвороті сигналу або за стопом.",
                  "Σήμα του συστήματος CRYPTO NOVA AI: {side} {sym}. Το stop είναι στο ανταλλακτήριο· το σύστημα κλείνει τη θέση όταν αντιστραφεί το σήμα ή στο stop.",
                  "Señal del sistema CRYPTO NOVA AI: {side} {sym}. El stop está en el exchange; el sistema cierra la posición cuando la señal se invierte o en el stop."),
    "sy.tline": _w("Цель {tgt} ({tp}{usd}). Выход: разворот сигнала системы или стоп {stop}.",
                   "Target {tgt} ({tp}{usd}). Exit: the system's signal reverses or the stop {stop}.",
                   "Ціль {tgt} ({tp}{usd}). Вихід: розворот сигналу системи або стоп {stop}.",
                   "Στόχος {tgt} ({tp}{usd}). Έξοδος: αντιστροφή του σήματος του συστήματος ή stop {stop}.",
                   "Objetivo {tgt} ({tp}{usd}). Salida: la señal del sistema se invierte o el stop {stop}."),
    "sy.s.signal": _w("Сигнал", "Signal", "Сигнал", "Σήμα", "Señal"),
    "sy.s.entry": _w("Вход", "Entry", "Вхід", "Είσοδος", "Entrada"),
    "sy.s.stop": _w("Стоп", "Stop", "Стоп", "Stop", "Stop"),
    "sy.signal.t": _w("Система CRYPTO NOVA AI открыла {side} {sym}{at}.", "The CRYPTO NOVA AI system opened a {side} on {sym}{at}.",
                      "Система CRYPTO NOVA AI відкрила {side} {sym}{at}.", "Το σύστημα CRYPTO NOVA AI άνοιξε {side} στο {sym}{at}.",
                      "El sistema CRYPTO NOVA AI abrió un {side} en {sym}{at}."),
    "sy.at": _w(" — {d}", " on {d}", " — {d}", " στις {d}", " el {d}"),
    "sy.entry": _w("Вход по {e}.", "Entry at {e}.", "Вхід за {e}.", "Είσοδος στα {e}.", "Entrada a {e}."),
    "sy.stop": _w("{stop} ({sp} от входа). Стоп стоит на бирже OKX и сработает, даже если система недоступна.",
                  "{stop} ({sp} from the entry). The stop sits on OKX and works even if the system is offline.",
                  "{stop} ({sp} від входу). Стоп стоїть на біржі OKX і спрацює, навіть якщо система недоступна.",
                  "{stop} ({sp} από την είσοδο). Το stop βρίσκεται στο OKX και λειτουργεί ακόμη κι αν το σύστημα δεν είναι διαθέσιμο.",
                  "{stop} ({sp} de la entrada). El stop está en OKX y funciona aunque el sistema no esté disponible."),
    "sy.exit.tgt": _w("Цель — {tgt} ({tp} от входа{usd}).", "Target: {tgt} ({tp} from the entry{usd}).", "Ціль — {tgt} ({tp} від входу{usd}).",
                      "Στόχος: {tgt} ({tp} από την είσοδο{usd}).", "Objetivo: {tgt} ({tp} desde la entrada{usd})."),
    "sy.exit.when": _w(" Система закрывает позицию при развороте сигнала или по стопу {stop}.",
                       " The system closes the position when the signal reverses or at the stop {stop}.",
                       " Система закриває позицію при розвороті сигналу або за стопом {stop}.",
                       " Το σύστημα κλείνει τη θέση όταν αντιστραφεί το σήμα ή στο stop {stop}.",
                       " El sistema cierra la posición cuando la señal se invierte o en el stop {stop}."),
}
T.update(SY)

MONTHS = {"ru": ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"),
          "uk": ("січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"),
          "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
                 "December"),
          "el": ("Ιανουαρίου", "Φεβρουαρίου", "Μαρτίου", "Απριλίου", "Μαΐου", "Ιουνίου", "Ιουλίου", "Αυγούστου", "Σεπτεμβρίου",
                 "Οκτωβρίου", "Νοεμβρίου", "Δεκεμβρίου"),
          "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")}
KYIV = dt.timezone(dt.timedelta(hours=2))      # the date words only (day + month): the account's start 22.10 is the same day either way


def date_words(ms: int, lang: str) -> str:
    """«22 октября» · «October 22» · «22 жовтня» · «22 Οκτωβρίου» · «22 de octubre»"""
    d = dt.datetime.fromtimestamp(int(ms) / 1000, KYIV)
    m = MONTHS.get(lang, MONTHS[DEFAULT])[d.month - 1]
    return f"{m} {d.day}" if lang == "en" else f"{d.day} de {m}" if lang == "es" else f"{d.day} {m}"


def _pct(v: float, d: int = 1, sign: bool = True) -> str:
    """a percent number (not a fraction): −7.6 → «−7.6%»"""
    r = round(float(v), d)
    s = f"{abs(r):.{d}f}%"
    return (("+" if r > 0 else "−" if r < 0 else "") if sign else "") + s


def _price(x: float) -> str:
    a = abs(x)
    # build 54: below 0.01 four significant digits (PEPE's entry 0.000004978 and stop 0.000004615 both read «0,000005»)
    d = 0 if a >= 1000 else 2 if a >= 10 else 3 if a >= 1 else 4 if a >= 0.01 else (min(12, 3 - math.floor(math.log10(a))) if a > 0 else 6)
    return f"{x:,.{d}f}".replace(",", " ")


def _usd(x: float, lang: str, cents: bool = False) -> str:
    s = f"{abs(float(x)):,.{2 if cents else 0}f}"
    if lang != "en":
        s = s.replace(",", " ").replace(".", ",")
    return ("−" if float(x) < 0 else "") + "$" + s


def _num(x: float, d: int = 1) -> str:
    s = f"{float(x):.{d}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s          # build 54: «20» stayed «2» (a 20x whale read as 2x)


# --------------------------------------------------------------------------------------------- the trade
_RE = {
    "D": re.compile(r"на (-?[\d.]+)% ниже спота"),
    "E": re.compile(r"свеча (-?[\d.]+)% на объёме ([\d.]+)×"),
    "F": re.compile(r"интерес за 4 ч (-?[\d.]+)%, цена (-?[\d.]+)%"),
    "B": re.compile(r"свеча (-?[\d.]+)% \((-?[\d.]+)σ\)"),
}
TIME_RULES = {"D": 24, "E": 24, "F": 24}           # signal_feed.HOLD_H: the exit is by time; the exchange target is a far guard
SLOTS = {"D": "d_open", "F": "f_open"}             # paper_desk.PROFILES[desk]: how many trades of the rule at once


def grade_of(sig: str | None) -> str:
    """«aggr:D-OP-498168» → «D»"""
    if not sig or ":" not in sig:
        return ""
    return sig.split(":", 1)[1].split("-", 1)[0]


def _facts(o: dict, grade: str) -> dict:
    """this trade's numbers: the desk's stored facts, else the numbers in its own reason (as the desk wrote them)"""
    f = dict(o.get("facts") or {}) if isinstance(o.get("facts"), dict) else {}
    m = _RE.get(grade).search(str(o.get("reason") or "")) if grade in _RE else None
    if grade == "D" and f.get("basis") is None and m:
        f["basis"] = float(m.group(1)) / 100
    if grade == "E" and m:
        f.setdefault("drop", float(m.group(1)) / 100)
        f.setdefault("vol_x", float(m.group(2)))
    if grade == "F" and m:
        f.setdefault("oi4", float(m.group(1)) / 100)
        f.setdefault("px4", float(m.group(2)) / 100)
    if grade == "B" and m:
        f.setdefault("ret", float(m.group(1)) / 100)
        f.setdefault("sigma_x", float(m.group(2)))
    return f


def _signal(grade: str, sym: str, f: dict, lang: str) -> str:
    if grade == "D":
        return t(lang, "rw.sig.D", sym=sym, b=_pct(abs(f["basis"]) * 100, 2, False)) if f.get("basis") is not None else t(lang, "rw.sig.D0", sym=sym)
    if grade == "E":
        return (t(lang, "rw.sig.E", sym=sym, drop=_pct(f["drop"] * 100), vol=_num(f["vol_x"])) if f.get("drop") is not None and f.get("vol_x")
                else t(lang, "rw.sig.E0", sym=sym))
    if grade == "F":
        return (t(lang, "rw.sig.F", sym=sym, oi=_pct(f["oi4"] * 100, 0), px=_pct(f["px4"] * 100)) if f.get("oi4") is not None and f.get("px4") is not None
                else t(lang, "rw.sig.F0", sym=sym))
    if grade == "B" and f.get("ret") is not None and f.get("sigma_x") is not None:
        return t(lang, "rw.sig.B", sym=sym, ret=_pct(f["ret"] * 100), x=_num(abs(f["sigma_x"])))
    return t(lang, "rw.sig.other")


def _checks(grade: str, f: dict, prof: dict, caps: dict, lang: str) -> list[dict]:
    """the checks the system really ran before this entry, this trade's numbers where they were recorded"""
    out = []

    def add(key, ok=True, **kw):
        out.append({"t": t(lang, key, **kw), "ok": bool(ok)})
    if grade == "D" and f.get("basis") is not None:
        add("rw.chk.D", f["basis"] <= -0.002, b=_pct(abs(f["basis"]) * 100, 2, False))
    if grade == "E":
        if f.get("drop") is not None and f.get("vol_x"):
            add("rw.chk.E", drop=_pct(f["drop"] * 100), vol=_num(f["vol_x"]))
        if f.get("breadth") is not None and f.get("need_breadth") is not None:
            add("rw.chk.breadth", f["breadth"] >= f["need_breadth"], b=_pct(f["breadth"] * 100, 0, False), need=_pct(f["need_breadth"] * 100, 0, False))
    if grade == "F" and f.get("oi4") is not None and f.get("px4") is not None:
        add("rw.chk.F", oi=_pct(f["oi4"] * 100, 0), px=_pct(f["px4"] * 100))
    if grade == "B" and f.get("ret") is not None and f.get("sigma_x") is not None:
        add("rw.chk.B", ret=_pct(f["ret"] * 100), x=_num(abs(f["sigma_x"])))
    if grade in ("D", "E", "F", "B"):
        add("rw.chk.coins")
    add("rw.chk.fresh")
    add("rw.chk.limits")
    n = prof.get(SLOTS.get(grade, "")) if grade in SLOTS else None
    if isinstance(n, int) and n > 0:
        add("rw.chk.slots", n=n)
    if caps.get("lev") and caps.get("risk_pct") is not None:
        add("rw.chk.real", lev=_num(caps["lev"], 0), risk=_pct(caps["risk_pct"], 1, False).replace(".0%", "%"))
    return out


def _history(grade: str, hist: dict | None, lang: str) -> str | None:
    if grade == "D":
        return t(lang, "rw.hist.D")
    if not hist or not hist.get("n") or hist.get("win_pct") is None or hist.get("avg_pct") is None:
        return None
    n = int(hist["n"])
    s = t(lang, "rw.hist.n", period=hist.get("period") or "", n=f"{n:,}".replace(",", " "), trades=plural(lang, n, "trade"),
          w=int(hist["win_pct"]), avg=_pct(hist["avg_pct"], 2))
    return s + (t(lang, "rw.hist.years") if hist.get("years") == "hist.every_year" else "")


def _live(grade: str, ctx: dict, lang: str) -> str:
    rows = [p for g, p in ctx.get("closed_grades") or [] if g == grade]
    if not rows:
        return t(lang, "rw.live0")
    n, w = len(rows), sum(1 for p in rows if p > 0)
    s = t(lang, "rw.live", d=date_words(ctx["since"], lang), n=n, w=w)
    return s + (t(lang, "rw.few") if n < 10 else "")


def _risk_usd(row: dict, stop_pct: float | None) -> float | None:
    """the $ at risk to the stop from this position's own size: notional = $ result ÷ price move (only when the move is big enough
    to give a stable size)"""
    usd, mv = row.get("pnl_usd"), row.get("move_pct")
    if usd is None or mv is None or abs(float(mv)) < 0.5 or not stop_pct:
        return None
    notional = abs(float(usd) / (float(mv) / 100))
    return notional * stop_pct / 100


def trade_why(row: dict, desk_trade: dict | None, grade: str, prof: dict, caps: dict, hist: dict | None, ctx: dict,
              lang: str = DEFAULT, title: str | None = None, closed: bool = False) -> dict | None:
    """one real trade explained: {title, signal, checked_title, checks, history, live, plan, risk, result, text}"""
    if not grade:
        return None
    sym = str(row.get("sym") or "")
    f = _facts(desk_trade or {}, grade)
    entry = row.get("entry") if not closed else row.get("open_px")
    stop, tgt = row.get("stop"), row.get("target")
    if closed and desk_trade:
        stop = desk_trade.get("stop")
        tps = desk_trade.get("tps") or []
        tgt = tps[-1] if tps else None
    plan = None
    sp = abs(float(stop) / float(entry) - 1) * 100 if entry and stop else None
    if entry and stop:
        side = str(row.get("side") or "").upper()
        sgn = "−" if side != "SHORT" else "+"
        if grade in TIME_RULES:
            plan = t(lang, "rw.plan.time", entry=_price(float(entry)), stop=_price(float(stop)), sp=f"{sgn}{_num(sp, 1)}%",
                     h=TIME_RULES[grade], tgt=_price(float(tgt)) if tgt else "—")
        else:
            plan = t(lang, "rw.plan.tgt", entry=_price(float(entry)), stop=_price(float(stop)), sp=f"{sgn}{_num(sp, 1)}%",
                     tgt=_price(float(tgt)) if tgt else "—")
    risk = None
    lev = row.get("lev") or caps.get("lev")
    cap = ctx.get("budget_usd") * caps["risk_pct"] / 100 if ctx.get("budget_usd") and caps.get("risk_pct") is not None else None
    if lev and cap:
        rp = _pct(caps["risk_pct"], 1, False).replace(".0%", "%")
        r = _risk_usd(row, sp)
        risk = (t(lang, "rw.risk", lev=_num(lev, 0), r=_usd(r, lang), risk=rp, cap=_usd(cap, lang)) if r is not None and r <= cap * 1.25
                else t(lang, "rw.risk_cap", lev=_num(lev, 0), cap=_usd(cap, lang), risk=rp))
    result = None
    if closed and row.get("pnl_usd") is not None and row.get("move_pct") is not None:
        result = t(lang, "rw.result", usd=("+" if row["pnl_usd"] > 0 else "") + _usd(row["pnl_usd"], lang, True), mv=_pct(row["move_pct"], 2))
    checks = _checks(grade, f, prof, caps, lang)
    doc = {"title": title, "signal": _signal(grade, sym, f, lang), "checked_title": t(lang, "rw.checked"), "checks": checks,
           "history": _history(grade, hist, lang), "live": _live(grade, ctx, lang), "plan": plan, "risk": risk, "result": result}
    doc["text"] = " ".join(x for x in (doc["signal"], doc["history"], doc["live"], plan, risk, result) if x)
    return doc


def _coin_key(c) -> str:
    """«1000PEPE» / «kPEPE» / «PEPE-USDT-SWAP» → «PEPE» (the whale's venue and OKX name small coins differently)"""
    c = str(c or "").upper().split("-")[0]
    for p in ("1000000", "1000", "K"):
        if c.startswith(p) and len(c) > len(p) + 1:
            return c[len(p):]
    return c


def whale_trade(sig: str | None, desk: dict | None) -> dict | None:
    """the copy desk's trade (web/whales_top.json desk.open / desk.closed) that a real «whales:W-<COIN>-<L|S>-<t>» trade mirrors"""
    if not sig or not str(sig).startswith("whales:") or not isinstance(desk, dict):
        return None
    parts = str(sig).split(":", 1)[1].split("-")
    if len(parts) < 4:
        return None
    coin, side = _coin_key(parts[1]), ("LONG" if parts[2] == "L" else "SHORT")
    try:
        ts = int(parts[3])
    except ValueError:
        ts = None
    rows = [r for r in list(desk.get("open") or []) + list(desk.get("closed") or []) if isinstance(r, dict)]
    same = [r for r in rows if _coin_key(r.get("coin")) == coin and str(r.get("side") or "").upper() == side]
    if ts is not None:
        exact = [r for r in same if r.get("opened_t") == ts]
        if exact:
            return exact[0]
        same = [r for r in same if isinstance(r.get("opened_t"), (int, float)) and abs(r["opened_t"] - ts) <= 3_600_000]
    return same[0] if same else None


def whale_record(addr: str | None, wt: dict | None) -> dict | None:
    """the whale's own record in the system's list (web/whales_top.json top / others)"""
    if not addr or not isinstance(wt, dict):
        return None
    for r in list(wt.get("top") or []) + list(wt.get("others") or []) + list(wt.get("following") or []):
        if isinstance(r, dict) and r.get("addr") == addr:
            return r
    return None


def _pick(d: dict | None, *keys):
    for k in keys:
        v = (d or {}).get(k)
        if v not in (None, "", []):
            return v
    return None


def ref_target(entry, stop, side: str | None) -> float | None:
    """the copy's reference target: 2R — twice the stop's distance on the other side of the entry"""
    try:
        e, st = float(entry), float(stop)
    except (TypeError, ValueError):
        return None
    if e <= 0 or st <= 0 or e == st:
        return None
    t_ = e + 2 * (e - st)
    return t_ if t_ > 0 else None


def flat_real_why(w: dict | None) -> dict | None:
    """web/real_why.json's why (cnq/agents/real_why.py: whale{…, record_30d{…}}, copy{…}, target{ref_px}, market{…}) → the flat
    fields [copy_why] reads; anything missing stays missing"""
    if not isinstance(w, dict):
        return None
    wh, cp, tg, mk = (w.get(k) if isinstance(w.get(k), dict) else {} for k in ("whale", "copy", "target", "market"))
    r30 = wh.get("record_30d") if isinstance(wh.get("record_30d"), dict) else {}
    src = str(wh.get("source") or "")
    out = {"whale_name": wh.get("name"), "whale_venue": "okx" if src.startswith("okx") else "hl" if src.startswith("hyper") else None,
           "whale_entry": wh.get("entry"), "whale_lev": wh.get("lev"), "whale_usd": wh.get("notional_usd"),
           "whale_trades_30d": r30.get("trades"), "whale_win_30d": r30.get("win"), "whale_pnl_30d": r30.get("pnl_usd"), "whale_pf": r30.get("pf"),
           "whale_hold_h": r30.get("hold_med_h"), "whale_style": r30.get("style_ru"), "whale_src_ru": wh.get("source_ru"),
           "stop_src": cp.get("stop_src_ru"),
           "stop": cp.get("stop"), "target_ref": tg.get("ref_px"), "btc_24h": mk.get("btc_24h_pct"), "funding": mk.get("funding_8h_pct"),
           "support": mk.get("support"), "resistance": mk.get("resistance"), "oi_chg": mk.get("oi_4h_pct")}
    return {k: v for k, v in out.items() if v not in (None, "")}


def copy_why(row: dict, wd: dict | None, rec: dict | None, why: dict | None, desk: dict | None, caps: dict, ctx: dict,
             lang: str = DEFAULT, closed: bool = False) -> dict:
    """A real futures trade of the system's futures desk explained for people (owner 05.11 09:52: «сигнал системы CRYPTO NOVA AI»):
    the market at the entry (trend from ctx["trend"], levels, BTC, funding, open interest — from real_why.json when recorded), the
    plan (entry, stop, the 2R target) and the exit rule; the demo account on the same signals. [wd]/[rec] are used for nothing a
    person sees (kept in the signature for the callers). The keys are those of build 55 so the app renders it unchanged."""
    why = why if isinstance(why, dict) else {}
    sym = str(row.get("sym") or "")
    side = str(row.get("side") or (wd or {}).get("side") or "").upper()
    side_w = t(lang, "rw.w.short" if side == "SHORT" else "rw.w.long")
    s_side = t(lang, "w5.short" if side == "SHORT" else "w5.long")
    sig = t(lang, "sy.sig", side=side_w, sym=sym) + t(lang, "sy.sig.why")
    entry = row.get("entry") if not closed else row.get("open_px")
    stop = row.get("stop") if row.get("stop") is not None else _pick(why, "stop") or (wd or {}).get("stop")
    sp = abs(float(stop) / float(entry) - 1) * 100 if entry and stop else None
    spx = (("+" if side == "SHORT" else "−") + _num(sp, 1) + "%") if sp is not None else None
    tr = (ctx.get("trend") or {}).get(sym) or {}
    tparts = []
    if tr.get("long") in ("BULL", "BEAR"):
        tparts.append(t(lang, "sy.tr.bull" if tr["long"] == "BULL" else "sy.tr.bear", d=int(tr.get("sma_days") or 200)))
    if tr.get("mid") in ("LONG", "SHORT"):
        tparts.append(t(lang, "sy.tr.up" if tr["mid"] == "LONG" else "sy.tr.down"))
    lv = []
    for key, k in (("rw.w.c.sup", "support"), ("rw.w.c.res", "resistance")):
        v = _pick(why, k)
        if v is not None:
            lv.append(t(lang, key, v=_price(float(v))))
    checks = []
    if tparts:
        checks.append({"t": t(lang, "sy.chk.trend", sym=sym, x=", ".join(tparts)), "ok": True})
    if lv:
        checks.append({"t": t(lang, "sy.chk.levels", x=", ".join(lv)), "ok": True})
    if spx:
        checks.append({"t": t(lang, "sy.chk.stop", sp=spx), "ok": True})
    checks.append({"t": t(lang, "rw.chk.fresh"), "ok": True})
    checks.append({"t": t(lang, "rw.chk.limits"), "ok": True})
    if caps.get("risk_pct") is not None and (row.get("lev") or caps.get("lev")):
        checks.append({"t": t(lang, "rw.chk.real", lev=_num(row.get("lev") or caps["lev"], 0),
                              risk=_pct(caps["risk_pct"], 1, False).replace(".0%", "%")), "ok": True})
    live = []
    if isinstance(desk, dict) and desk.get("trades") and desk.get("started"):
        live.append(t(lang, "sy.desk", d=date_words(desk["started"], lang), n=int(desk["trades"]), w=int(desk.get("wins") or 0)))
    rows = [p for g, p in ctx.get("closed_grades") or [] if g == "W"]
    live.append(t(lang, "rw.live", d=date_words(ctx["since"], lang), n=len(rows), w=sum(1 for p in rows if p > 0)) if rows
                else t(lang, "rw.live0"))
    tgt = _pick(why, "target_ref", "ref_target") or ref_target(entry, stop, side)
    plan = t(lang, "sy.plan", entry=_price(float(entry)), stop=_price(float(stop)), sp=spx,
             tgt=_price(float(tgt)) if tgt else "—") if entry and stop else None
    ctx_parts = []
    for key, k in (("rw.w.c.btc", ("btc_24h", "btc_24h_pct", "btc24")), ("rw.w.c.fund", ("funding", "funding_pct")),
                   ("rw.w.c.oi", ("oi_chg", "oi_24h", "oi_pct"))):
        v = _pick(why, *k)
        if v is not None:
            ctx_parts.append(t(lang, key, v=_pct(float(v), 3 if key == "rw.w.c.fund" else 1)))
    mk_parts = tparts + lv + ctx_parts
    context = t(lang, "rw.w.ctx", x=", ".join(mk_parts)) if mk_parts else None
    risk = None
    lev = row.get("lev") or caps.get("lev")
    cap = ctx.get("budget_usd") * caps["risk_pct"] / 100 if ctx.get("budget_usd") and caps.get("risk_pct") is not None else None
    if lev and cap:
        rp = _pct(caps["risk_pct"], 1, False).replace(".0%", "%")
        r = _risk_usd(row, sp)
        risk = (t(lang, "rw.risk", lev=_num(lev, 0), r=_usd(r, lang), risk=rp, cap=_usd(cap, lang)) if r is not None and r <= cap * 1.25
                else t(lang, "rw.risk_cap", lev=_num(lev, 0), cap=_usd(cap, lang), risk=rp))
    result = None
    if closed and row.get("pnl_usd") is not None and row.get("move_pct") is not None:
        result = t(lang, "rw.result", usd=("+" if row["pnl_usd"] > 0 else "") + _usd(row["pnl_usd"], lang, True), mv=_pct(row["move_pct"], 2))
        if str((wd or {}).get("why") or "").startswith("кит"):
            result += " " + t(lang, "sy.exit")
    # summary, the target line under the live bar and the «Подробнее» sections
    e = float(entry) if entry else None
    st_ = float(stop) if stop else None
    tg = float(tgt) if tgt else None
    tp = (tg / e - 1) * 100 if tg and e else None
    usd, mv = row.get("pnl_usd"), row.get("move_pct")
    notional = abs(float(usd) / (float(mv) / 100)) if usd is not None and mv is not None and abs(float(mv)) >= 0.3 else None
    gain = notional * abs(tp) / 100 if notional and tp is not None else None
    if tg and e and st_ and not closed:
        summary = t(lang, "sy.sum", side=s_side, sym=sym, tgt=_price(tg), tp=_pct(tp, 1), stop=_price(st_))
        tline = t(lang, "sy.tline", tgt=_price(tg), tp=_pct(tp, 1), usd=t(lang, "w5.usd", u="+" + _usd(gain, lang)) if gain and gain >= 1 else "",
                  stop=_price(st_))
    else:
        summary, tline = t(lang, "sy.sum0", side=s_side, sym=sym), None
    seen = _when(row.get("opened") or (wd or {}).get("opened_t") or _sig_ms(row.get("sig")), lang)
    sec = [{"title": t(lang, "sy.s.signal"), "text": t(lang, "sy.signal.t", side=s_side, sym=sym, at=t(lang, "sy.at", d=seen) if seen else "")
            + " " + " ".join(live)}]
    if context:
        head = t(lang, "rw.w.ctx", x="").rstrip(" .")
        body = context[len(head):].lstrip(" :") if context.startswith(head) else context
        sec.append({"title": t(lang, "w5.s.market"), "text": body[:1].upper() + body[1:]})
    if e:
        sec.append({"title": t(lang, "sy.s.entry"), "text": t(lang, "sy.entry", e=_price(e))})
    if st_ and spx:
        sec.append({"title": t(lang, "sy.s.stop"), "text": t(lang, "sy.stop", stop=_price(st_), sp=spx)})
    ex = t(lang, "sy.exit.tgt", tgt=_price(tg), tp=_pct(tp, 1), usd=t(lang, "w5.exit.usd", u="+" + _usd(gain, lang)) if gain and gain >= 1 else "") \
        if tg and tp is not None else ""
    ex += t(lang, "sy.exit.when", stop=_price(st_) if st_ else "—")
    sec.append({"title": t(lang, "w5.s.exit"), "text": ex.strip()})
    if risk:
        sec.append({"title": t(lang, "w5.s.risk"), "text": risk})
    doc = {"title": t(lang, "sy.title"), "signal": sig, "checked_title": t(lang, "rw.checked"), "checks": checks, "history": None,
           "live": " ".join(live), "plan": plan, "risk": risk, "result": result, "context": context, "copy": True,
           "target_ref": round(float(tgt), 12) if tgt else None, "summary": summary, "target_line": tline, "sections": sec}
    doc["text"] = " ".join(x for x in (doc["signal"], context, doc["live"], plan, risk, result) if x)
    return doc


def _hold(h, lang: str) -> str | None:
    try:
        h = float(h)
    except (TypeError, ValueError):
        return None
    if h <= 0:
        return None
    if h < 48:
        return t(lang, "w5.h", n=int(round(h)))
    n = int(round(h / 24))
    few = n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14)
    one = n % 10 == 1 and n % 100 != 11
    u = {"ru": "дня" if few else "день" if one else "дней", "uk": "дні" if few else "день" if one else "днів"}.get(lang, "")
    return t(lang, "w5.d", n=n, u=u)


def _when(ms, lang: str) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    d = dt.datetime.fromtimestamp(int(ms) / 1000, KYIV)
    return f"{date_words(int(ms), lang)}, {d.hour:02d}:{d.minute:02d}"


def _sig_ms(sig) -> int | None:
    try:
        return int(str(sig).rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def open_sig_for_closed(c: dict, actions: list) -> str | None:
    """the desk trade a closed real trade mirrored: the fut_open action of the same coin within 10 minutes of its open time"""
    sym, op = str(c.get("inst") or "").split("-")[0], c.get("opened")
    if not sym or not isinstance(op, (int, float)):
        return None
    best = None
    for a in actions or []:
        if a.get("kind") == "fut_open" and a.get("sym") == sym and a.get("sig") and isinstance(a.get("t"), (int, float)):
            d = abs(a["t"] - op)
            if d <= 600_000 and (best is None or d < best[0]):
                best = (d, a["sig"])
    return best[1] if best else None


# --------------------------------------------------------------------------------------------- the spot coin
_P = re.compile(r"([+−-]?\d+(?:\.\d+)?)%")
_VOL = re.compile(r"оборот (\$[\d.]+[KMB]?)")
_BT = re.compile(r"дали ×([\d.]+), худшая просадка ([−-]\d+)%")


def _chk(row: dict, key: str) -> dict | None:
    return next((c for c in row.get("checks") or [] if c.get("key") == key), None)


def coin_why(sym: str, share: float | None, row: dict | None, status: dict | None, evaluated: int | None, passing: int | None,
             lang: str = DEFAULT) -> dict:
    """{why, checked_title, checks [{t, ok}], status, scale}: why the system holds the coin, from the spot table's own record"""
    row = row or {}
    core = sym in ("BTC", "ETH", "SOL") or row.get("kind") == "core"
    sh = _pct(share, 1, False) if share is not None else "—"
    out = {"why": t(lang, "rw.c.core" if core else "rw.c.alt", sym=sym, share=sh), "checked_title": t(lang, "rw.c.checked"), "checks": []}

    def add(key, ok, **kw):
        out["checks"].append({"t": t(lang, key, **kw), "ok": ok})
    if row.get("rating") is not None:
        add("rw.c.rating", float(row["rating"]) >= 40, v=int(row["rating"]))
    cons = row.get("consultant") or {}
    if not core and cons.get("mid") is not None and cons.get("long") is not None:
        add("rw.c.consultant", max(cons["mid"], cons["long"]) >= 60, m=int(cons["mid"]), l=int(cons["long"]))
    c = _chk(row, "trend200")
    m = _P.search(str((c or {}).get("t") or ""))
    if m:
        add("rw.c.trend200", (c or {}).get("ok") is not False, x=m.group(1).replace("-", "−") + "%")
    if row.get("chg7") is not None and row.get("from_high30_pct") is not None:
        c = _chk(row, "overheat")
        add("rw.c.overheat", (c or {}).get("ok") is not False, c7=_pct(row["chg7"]), h30=_pct(row["from_high30_pct"]))
    c = _chk(row, "ath")
    m = _P.search(str((c or {}).get("t") or ""))
    if m:
        add("rw.c.ath", c.get("ok") is not False, x=m.group(1).replace("-", "−") + "%")
    c = _chk(row, "vol")
    m = _VOL.search(str((c or {}).get("t") or ""))
    if m:
        add("rw.c.vol", c.get("ok") is not False, v=m.group(1))
    c = _chk(row, "dilution")
    m = _P.search(str((c or {}).get("t") or ""))
    if m:
        add("rw.c.dilution", c.get("ok") is not False, x=m.group(1) + "%")
    c = _chk(row, "regime")
    if c is not None:
        add("rw.c.regime" if c.get("ok") else "rw.c.regime_no", bool(c.get("ok")))
    m = _BT.search(str(row.get("info_ru") or ""))
    if core and m:
        add("rw.c.backtest", True, x=m.group(1), dd=m.group(2).replace("-", "−") + "%")
    st = (status or {}).get("status") or row.get("status")
    if st in ("buy", "add", "small", "wait", "avoid"):
        out["status"] = t(lang, "rw.c.status", s=t(lang, f"rw.c.st.{st}"))
        if st == "wait":
            out["note"] = t(lang, "rw.c.wait_note")
    if evaluated and passing is not None:
        out["scale"] = t(lang, "rw.c.scale", n=int(evaluated), k=int(passing))
    out["text"] = " ".join(x if x.endswith((".", "!", "?")) else x + "." for x in (out["why"], out.get("status"), out.get("note"), out.get("scale")) if x)
    return out


# --------------------------------------------------------------------------------------------- the calm line
def btc_change_since(btc: dict | None, since: int, price_now: float | None) -> float | None:
    """bitcoin's % change from the close of the day before the account's start (≈ its price at the start) to now"""
    if not btc or not price_now or not btc.get("closes") or btc.get("start_day") is None:
        return None
    i = int(since // 86_400_000) - 1 - int(btc["start_day"])
    cl = btc["closes"]
    if i < 0 or i >= len(cl) or not cl[i]:
        return None
    return (float(price_now) / float(cl[i]) - 1) * 100


def calm_note(pport: dict | None, btc_pct: float | None, engine: dict | None, lang: str = DEFAULT) -> dict | None:
    """{drawdown, allday}: the drawdown sentence only when the account fell and bitcoin fell more since the start (by 0.3 points
    or more); the 24/7 line from the engine's real counters"""
    out = {}
    acct = (pport or {}).get("pnl_pct")
    if acct is not None and btc_pct is not None and acct < -0.3 and btc_pct < 0 and btc_pct <= acct - 0.3:
        out["drawdown"] = t(lang, "rw.calm", d=date_words(pport["since"], lang), btc=_pct(btc_pct), acct=_pct(acct))
    e = engine or {}
    if e.get("cycle_sec") and e.get("lab_tried") and e.get("lab_passed") is not None:
        out["allday"] = t(lang, "rw.allday", s=int(e["cycle_sec"]), a=int(e["lab_tried"]), p=int(e["lab_passed"]))
    return out or None
