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
        "Сделка по правилам CRYPTO NOVA: условия входа, стоп и выход заданы заранее.",
        "A trade by CRYPTO NOVA's rules: the entry conditions, the stop and the exit are set in advance.",
        "Угода за правилами CRYPTO NOVA: умови входу, стоп і вихід задані заздалегідь.",
        "Συναλλαγή με τους κανόνες του CRYPTO NOVA: οι όροι εισόδου, το stop και η έξοδος ορίζονται από πριν.",
        "Una operación según las reglas de CRYPTO NOVA: las condiciones de entrada, el stop y la salida se fijan de antemano."),
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
    "rw.chk.limits": _w("Лимиты риска счёта: дневной стоп −3%, стоп по просадке −15%, плечо всего счёта не выше 3×",
                        "The account's risk limits: a −3% daily stop, a −15% drawdown stop, the whole account's leverage no higher than 3×",
                        "Ліміти ризику рахунку: денний стоп −3%, стоп за просіданням −15%, плече всього рахунку не вище 3×",
                        "Όρια κινδύνου του λογαριασμού: ημερήσιο stop −3%, stop πτώσης −15%, μόχλευση όλου του λογαριασμού όχι πάνω από 3×",
                        "Límites de riesgo de la cuenta: stop diario −3%, stop por caída −15%, apalancamiento de toda la cuenta no mayor de 3×"),
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
    "rw.live": _w("На реальном счёте CRYPTO NOVA с {d} сделок по этому правилу закрыто: {n}, из них в плюсе: {w}.",
                  "On CRYPTO NOVA's real account since {d}, trades of this rule closed: {n}, in profit: {w}.",
                  "На реальному рахунку CRYPTO NOVA з {d} угод за цим правилом закрито: {n}, з них у плюсі: {w}.",
                  "Στον πραγματικό λογαριασμό του CRYPTO NOVA από {d}, κλειστές συναλλαγές αυτού του κανόνα: {n}, με κέρδος: {w}.",
                  "En la cuenta real de CRYPTO NOVA desde el {d}, operaciones cerradas de esta regla: {n}; con ganancia: {w}."),
    "rw.live0": _w("На реальном счёте CRYPTO NOVA это первая сделка по этому правилу.",
                   "On CRYPTO NOVA's real account this is the rule's first trade.",
                   "На реальному рахунку CRYPTO NOVA це перша угода за цим правилом.",
                   "Στον πραγματικό λογαριασμό του CRYPTO NOVA αυτή είναι η πρώτη συναλλαγή του κανόνα.",
                   "En la cuenta real de CRYPTO NOVA es la primera operación de esta regla."),
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
    d = 0 if a >= 1000 else 2 if a >= 10 else 3 if a >= 1 else 4 if a >= 0.01 else 6
    return f"{x:,.{d}f}".replace(",", " ")


def _usd(x: float, lang: str, cents: bool = False) -> str:
    s = f"{abs(float(x)):,.{2 if cents else 0}f}"
    if lang != "en":
        s = s.replace(",", " ").replace(".", ",")
    return ("−" if float(x) < 0 else "") + "$" + s


def _num(x: float, d: int = 1) -> str:
    return f"{float(x):.{d}f}".rstrip("0").rstrip(".")


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
