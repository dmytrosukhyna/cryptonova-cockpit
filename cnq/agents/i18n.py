"""Server texts in the app's five languages (20.10.2026: the 2.0 app became multilingual — it picks the phone's language,
English when the phone's is not one of these, and the user can switch).

  t(lang, key, **params)   a template of the catalog T, filled with str.format — the same {placeholders} in every language
  plural(lang, n, key)     the word for n in that language (ru / uk: one · few · many; en / el / es: one · other)
  norm_lang(v)             "en-US" / "UK" / "ua" / None → one of LANGS; anything unknown → DEFAULT (ru, the language of the old builds)

Used by cnq/agents/app_home.py (web/app_home.json = ru, web/app_home.<lang>.json = the others; field names stay *_ru, the text
is the file's language) and cnq/api (NOVA's limit / busy answers, ?lang= on the feeds). Russian is the source; the others are
written for people, not word for word: Ukrainian proper (not calqued Russian), Greek with accents and the polite «εσείς»,
Spanish neutral with «tú», English plain. The app's own labels (its i18n files) are used for its sections: Piggy bank /
Скарбничка / Κουμπαράς / Hucha, demo account, «Реальный счёт» = Real account … Stdlib only."""
from __future__ import annotations

import re
import string

LANGS = ("ru", "en", "uk", "el", "es")
DEFAULT = "ru"
ALIASES = {"ua": "uk", "gr": "el", "rus": "ru", "eng": "en", "ukr": "uk", "ell": "el", "gre": "el", "spa": "es"}


def norm_lang(v) -> str:
    s = str(v or "").strip().lower().replace("_", "-").split("-")[0]
    s = ALIASES.get(s, s)
    return s if s in LANGS else DEFAULT


def file_name(base: str, lang: str, ext: str = "json") -> str:
    """app_home + ru → app_home.json; app_home + en → app_home.en.json"""
    return f"{base}.{ext}" if lang == DEFAULT else f"{base}.{lang}.{ext}"


# ------------------------------------------------------------------------------------------------ the catalog
T: dict[str, dict[str, str]] = {
    # ---------------------------------------------------------------- spot allocation groups (spot.alloc[].name)
    "alloc.btc": {"ru": "Биткоин", "en": "Bitcoin", "uk": "Біткоїн", "el": "Bitcoin", "es": "Bitcoin"},
    "alloc.eth": {"ru": "Эфир", "en": "Ether", "uk": "Ефір", "el": "Ether", "es": "Ether"},
    "alloc.sol": {"ru": "Солана", "en": "Solana", "uk": "Солана", "el": "Solana", "es": "Solana"},
    "alloc.meme": {"ru": "мем-монеты", "en": "meme coins", "uk": "мем-монети", "el": "meme coins", "es": "monedas meme"},
    "alloc.alt": {"ru": "крупные альты", "en": "large altcoins", "uk": "великі альткоїни", "el": "μεγάλα altcoins",
                  "es": "altcoins grandes"},
    "alloc.core": {"ru": "ядро", "en": "core", "uk": "ядро", "el": "πυρήνας", "es": "núcleo"},
    "alloc.other": {"ru": "другие", "en": "other", "uk": "інші", "el": "άλλα", "es": "otras"},
    "alloc.cash": {"ru": "доллары", "en": "dollars", "uk": "долари", "el": "δολάρια", "es": "dólares"},
    # 21.10: the app showed «жадность» / «рост» in English — market words per language
    "fng.Extreme Fear": {"ru": "крайний страх", "en": "extreme fear", "uk": "крайній страх", "el": "ακραίος φόβος", "es": "miedo extremo"},
    "fng.Fear": {"ru": "страх", "en": "fear", "uk": "страх", "el": "φόβος", "es": "miedo"},
    "fng.Neutral": {"ru": "нейтрально", "en": "neutral", "uk": "нейтрально", "el": "ουδέτερο", "es": "neutral"},
    "fng.Greed": {"ru": "жадность", "en": "greed", "uk": "жадібність", "el": "απληστία", "es": "codicia"},
    "fng.Extreme Greed": {"ru": "крайняя жадность", "en": "extreme greed", "uk": "крайня жадібність", "el": "ακραία απληστία", "es": "codicia extrema"},
    "regime.up": {"ru": "рост", "en": "rising", "uk": "зростання", "el": "άνοδος", "es": "subida"},
    "regime.down": {"ru": "спад", "en": "falling", "uk": "спад", "el": "πτώση", "es": "bajada"},

    # ---------------------------------------------------------------- «why we entered» (futures.open[].why)
    "why.CLIMAX_LONG": {
        "ru": "Рынок резко продали: свеча упала на большом объёме. После таких провалов цена чаще отскакивает за сутки.",
        "en": "The market was sold off sharply: the candle dropped on heavy volume. After dips like this, the price more often "
              "bounces back within a day.",
        "uk": "Ринок різко розпродали: свічка впала на великому обсязі. Після таких провалів ціна частіше відскакує протягом доби.",
        "el": "Η αγορά δέχτηκε απότομες πωλήσεις: το κερί έπεσε με μεγάλο όγκο. Μετά από τέτοιες βουτιές η τιμή πιο συχνά "
              "ανακάμπτει μέσα σε 24 ώρες.",
        "es": "El mercado se vendió de golpe: la vela cayó con mucho volumen. Tras caídas así, el precio rebota más a menudo en "
              "menos de un día."},
    "why.CLIMAX_SHORT": {
        "ru": "Цену резко разогнали на большом объёме. После таких всплесков цена чаще откатывает за сутки.",
        "en": "The price was pushed up sharply on heavy volume. After spikes like this, the price more often pulls back within a day.",
        "uk": "Ціну різко розігнали на великому обсязі. Після таких сплесків ціна частіше відкочується протягом доби.",
        "el": "Η τιμή εκτινάχθηκε απότομα με μεγάλο όγκο. Μετά από τέτοιες εκρήξεις η τιμή πιο συχνά υποχωρεί μέσα σε 24 ώρες.",
        "es": "El precio se disparó de golpe con mucho volumen. Tras subidas así, el precio retrocede más a menudo en menos de un día."},
    "why.P": {
        "ru": "Монета в растущем тренде на день откатилась вниз. Покупаем откат: на истории 2023–2026 такие входы в среднем "
              "давали плюс.",
        "en": "A coin in an uptrend dipped for a day. We buy the dip: on 2023–2026 history, entries like this were profitable on "
              "average.",
        "uk": "Монета у висхідному тренді на день відкотилася вниз. Купуємо відкат: на даних 2023–2026 такі входи в середньому "
              "давали плюс.",
        "el": "Ένα νόμισμα σε ανοδική τάση υποχώρησε για μία μέρα. Αγοράζουμε την υποχώρηση: στο ιστορικό 2023–2026 τέτοιες "
              "είσοδοι έδιναν κατά μέσο όρο κέρδος.",
        "es": "Una moneda en tendencia alcista retrocedió un día. Compramos el retroceso: en el histórico 2023–2026, estas entradas "
              "dieron ganancias de media."},
    "why.C": {
        "ru": "Цена пробила максимум 20 дней. На истории после таких пробоев тренд чаще продолжается.",
        "en": "The price broke above its 20-day high. Historically, after breakouts like this the trend more often continues.",
        "uk": "Ціна пробила максимум 20 днів. Історично після таких пробоїв тренд частіше триває.",
        "el": "Η τιμή έσπασε το υψηλό 20 ημερών. Ιστορικά, μετά από τέτοιες διασπάσεις η τάση πιο συχνά συνεχίζεται.",
        "es": "El precio superó su máximo de 20 días. En el histórico, tras rupturas así la tendencia continúa más a menudo."},
    "why.LST": {
        "ru": "Новая монета на бирже: после листинга цена чаще снижается. Шорт на 14 дней, небольшой размер.",
        "en": "A new coin on the exchange: after a listing, the price more often goes down. A 14-day short, small size.",
        "uk": "Нова монета на біржі: після лістингу ціна частіше знижується. Шорт на 14 днів, невеликий розмір.",
        "el": "Νέο νόμισμα στο ανταλλακτήριο: μετά την εισαγωγή του η τιμή πιο συχνά πέφτει. Short για 14 ημέρες, μικρό μέγεθος.",
        "es": "Una moneda nueva en el exchange: tras el listado, el precio baja más a menudo. Short a 14 días, tamaño pequeño."},
    "why.K": {
        "ru": "Биткоин резко вырос, а эта монета ещё нет — на истории альты чаще догоняют рывок BTC.",
        "en": "Bitcoin jumped, but this coin hasn't yet — historically, alts more often catch up with a BTC surge.",
        "uk": "Біткоїн різко зріс, а ця монета ще ні — історично альти частіше наздоганяють ривок BTC.",
        "el": "Το bitcoin ανέβηκε απότομα, αυτό το νόμισμα όχι ακόμη — ιστορικά τα altcoins πιο συχνά ακολουθούν το άλμα του BTC.",
        "es": "Bitcoin subió de golpe y esta moneda todavía no: en el histórico, las altcoins alcanzan más a menudo el impulso de BTC."},
    "why.default": {
        "ru": "Сделка по правилам CRYPTO NOVA: вход, стоп и цель заданы заранее.",
        "en": "A trade by CRYPTO NOVA's rules: entry, stop and target are set in advance.",
        "uk": "Угода за правилами CRYPTO NOVA: вхід, стоп і ціль задано заздалегідь.",
        "el": "Συναλλαγή με τους κανόνες του CRYPTO NOVA: είσοδος, stop και στόχος ορίζονται εκ των προτέρων.",
        "es": "Una operación según las reglas de CRYPTO NOVA: entrada, stop y objetivo fijados de antemano."},
    "why.vol": {"ru": " Объём дня сигнала — ×{vol} к обычному.", "en": " The signal day's volume was ×{vol} the usual.",
                "uk": " Обсяг дня сигналу — ×{vol} до звичайного.", "el": " Ο όγκος της ημέρας του σήματος ήταν ×{vol} του συνηθισμένου.",
                "es": " El volumen del día de la señal fue ×{vol} el habitual."},

    # ---------------------------------------------------------------- which desk / rule (futures.open[].src_ru)
    "src.bal": {"ru": "основная стратегия", "en": "main strategy", "uk": "основна стратегія", "el": "κύρια στρατηγική",
                "es": "estrategia principal"},
    "src.imp": {"ru": "импульс BTC", "en": "BTC momentum", "uk": "імпульс BTC", "el": "ορμή BTC", "es": "impulso de BTC"},
    "src.own": {"ru": "сигналы CRYPTO NOVA", "en": "CRYPTO NOVA signals", "uk": "сигнали CRYPTO NOVA", "el": "σήματα CRYPTO NOVA",
                "es": "señales de CRYPTO NOVA"},
    "src.lst": {"ru": "шорт листингов", "en": "listing shorts", "uk": "шорт лістингів", "el": "short σε νέες εισαγωγές",
                "es": "shorts de listados"},
    "src.default": {"ru": "CRYPTO NOVA", "en": "CRYPTO NOVA", "uk": "CRYPTO NOVA", "el": "CRYPTO NOVA", "es": "CRYPTO NOVA"},
    # the rules (Russian = signal_feed.RULE_RU, the Telegram names)
    "rule.A": {"ru": "откуп гэпа", "en": "gap buy", "uk": "викуп гепу", "el": "αγορά στο κενό τιμής", "es": "compra del gap"},
    "rule.B": {"ru": "откуп каскада ликвидаций", "en": "liquidation cascade buy", "uk": "викуп каскаду ліквідацій",
               "el": "αγορά μετά από ρευστοποιήσεις", "es": "compra tras liquidaciones en cascada"},
    "rule.C": {"ru": "пробой 20 дней", "en": "20-day breakout", "uk": "пробій 20 днів", "el": "διάσπαση 20 ημερών",
               "es": "ruptura de 20 días"},
    "rule.D": {"ru": "откуп дисконта фьючерса", "en": "futures discount buy", "uk": "викуп дисконту ф'ючерса",
               "el": "αγορά στην έκπτωση του future", "es": "compra con descuento del futuro"},
    "rule.E": {"ru": "откуп паники", "en": "panic buy", "uk": "викуп паніки", "el": "αγορά στον πανικό", "es": "compra en el pánico"},
    "rule.F": {"ru": "откуп сброса позиций", "en": "position flush buy", "uk": "викуп скидання позицій",
               "el": "αγορά μετά από μαζικό κλείσιμο θέσεων", "es": "compra tras cierre masivo de posiciones"},
    "rule.K": {"ru": "альт догоняет рывок BTC", "en": "alt catches up with BTC", "uk": "альт наздоганяє ривок BTC",
               "el": "altcoin ακολουθεί το BTC", "es": "altcoin alcanza a BTC"},
    "rule.LST": {"ru": "шорт нового листинга", "en": "new listing short", "uk": "шорт нового лістингу", "el": "short σε νέα εισαγωγή",
                 "es": "short de nuevo listado"},
    "rule.P": {"ru": "откат в тренде", "en": "trend pullback", "uk": "відкат у тренді", "el": "υποχώρηση σε τάση",
               "es": "retroceso en tendencia"},

    # ---------------------------------------------------------------- the setup in a few words (futures.open[].why_title)
    "title.P": {"ru": "Откат в растущем тренде", "en": "Pullback in an uptrend", "uk": "Відкат у висхідному тренді",
                "el": "Υποχώρηση σε ανοδική τάση", "es": "Retroceso en tendencia alcista"},
    "title.C": {"ru": "Пробой максимума 20 дней", "en": "Breakout above the 20-day high", "uk": "Пробій максимуму 20 днів",
                "el": "Διάσπαση του υψηλού 20 ημερών", "es": "Ruptura del máximo de 20 días"},
    "title.E": {"ru": "Откуп паники на большом объёме", "en": "Buying the panic on heavy volume",
                "uk": "Викуп паніки на великому обсязі", "el": "Αγορά στον πανικό με μεγάλο όγκο",
                "es": "Compra en el pánico con mucho volumen"},
    "title.B": {"ru": "Откуп каскада ликвидаций", "en": "Buying a liquidation cascade", "uk": "Викуп каскаду ліквідацій",
                "el": "Αγορά μετά από καταρράκτη ρευστοποιήσεων", "es": "Compra tras una cascada de liquidaciones"},
    "title.F": {"ru": "Откуп сброса позиций", "en": "Buying a position flush", "uk": "Викуп скидання позицій",
                "el": "Αγορά μετά από μαζικό κλείσιμο θέσεων", "es": "Compra tras un cierre masivo de posiciones"},
    "title.D": {"ru": "Откуп дисконта фьючерса", "en": "Buying the futures discount", "uk": "Викуп дисконту ф'ючерса",
                "el": "Αγορά στην έκπτωση του future", "es": "Compra con el futuro en descuento"},
    "title.K": {"ru": "Альт догоняет рывок биткоина", "en": "An alt catching up with bitcoin's surge",
                "uk": "Альт наздоганяє ривок біткоїна", "el": "Ένα altcoin ακολουθεί το άλμα του bitcoin",
                "es": "Una altcoin alcanza el impulso de bitcoin"},
    "title.LST": {"ru": "Шорт нового листинга", "en": "Shorting a new listing", "uk": "Шорт нового лістингу",
                  "el": "Short σε νέα εισαγωγή", "es": "Short de un nuevo listado"},
    # the rule's name inside the «work» sentence
    "rname.E": {"ru": "откуп паники", "en": "panic buy", "uk": "викуп паніки", "el": "αγορά στον πανικό", "es": "compra en el pánico"},
    "rname.B": {"ru": "откуп каскада", "en": "cascade buy", "uk": "викуп каскаду", "el": "αγορά μετά από καταρράκτη",
                "es": "compra tras la cascada"},
    "rname.F": {"ru": "откуп сброса позиций", "en": "position flush buy", "uk": "викуп скидання позицій",
                "el": "αγορά μετά από μαζικό κλείσιμο θέσεων", "es": "compra tras cierre masivo de posiciones"},
    "rname.D": {"ru": "откуп дисконта фьючерса", "en": "futures discount buy", "uk": "викуп дисконту ф'ючерса",
                "el": "αγορά στην έκπτωση του future", "es": "compra con descuento del futuro"},
    "rname.K": {"ru": "альт догоняет рывок BTC", "en": "alt catches up with BTC", "uk": "альт наздоганяє ривок BTC",
                "el": "altcoin ακολουθεί το BTC", "es": "altcoin alcanza a BTC"},

    # ---------------------------------------------------------------- the rule's record on history (history.years_plus / .text)
    "hist.every_year": {"ru": "каждый год в плюсе", "en": "profitable every year", "uk": "щороку в плюсі",
                        "el": "κερδοφόρο κάθε χρόνο", "es": "en positivo cada año"},
    "hist.B.years": {"ru": "5 из 6 лет в плюсе", "en": "profitable in 5 of 6 years", "uk": "5 із 6 років у плюсі",
                     "el": "κερδοφόρο σε 5 από τα 6 έτη", "es": "en positivo 5 de 6 años"},
    "hist.K.years": {"ru": "2025 и 2026 в плюсе", "en": "2025 and 2026 profitable", "uk": "2025 і 2026 у плюсі",
                     "el": "κερδοφόρο το 2025 και το 2026", "es": "2025 y 2026 en positivo"},
    "hist.P.text": {
        "ru": "Честная проверка обоих правил «Сигналов» вместе (каждый день топ-40 монет Binance по обороту, включая снятые с "
              "торгов): в среднем +3.1% на сделку, около +2% в месяц к счёту; отдельной статистики «отката» в отчёте нет.",
        "en": "An honest test of both “Signals” rules together (each day the top 40 Binance coins by turnover, delisted ones "
              "included): +3.1% per trade on average, about +2% a month on the account; the report has no separate figures for "
              "the pullback rule.",
        "uk": "Чесна перевірка обох правил «Сигналів» разом (щодня топ-40 монет Binance за оборотом, включно зі знятими з "
              "торгів): у середньому +3.1% на угоду, близько +2% на місяць для рахунку; окремої статистики «відкату» у звіті немає.",
        "el": "Έντιμος έλεγχος και των δύο κανόνων των «Σημάτων» μαζί (κάθε μέρα τα 40 νομίσματα του Binance με τον μεγαλύτερο "
              "τζίρο, μαζί με όσα αποσύρθηκαν): κατά μέσο όρο +3.1% ανά συναλλαγή, περίπου +2% τον μήνα στον λογαριασμό· η "
              "αναφορά δεν δίνει ξεχωριστά στοιχεία για την «υποχώρηση».",
        "es": "Prueba honesta de las dos reglas de «Señales» juntas (cada día las 40 monedas de Binance con más volumen, "
              "incluidas las retiradas de cotización): +3.1% por operación de media, cerca de +2% al mes para la cuenta; el "
              "informe no da cifras aparte del «retroceso»."},
    "hist.C.text": {
        "ru": "18 крупных монет Binance, вход после закрытия дня с объёмом от ×1.5: 403 сделки, 53% в плюсе, в среднем +6.3% — "
              "без снятых с торгов монет это завышено; честная проверка обоих правил вместе даёт +3.1% на сделку.",
        "en": "18 large Binance coins, entry after a daily close on volume of ×1.5 or more: 403 trades, 53% profitable, +6.3% on "
              "average — without delisted coins this is inflated; the honest test of both rules together gives +3.1% per trade.",
        "uk": "18 великих монет Binance, вхід після закриття дня з обсягом від ×1.5: 403 угоди, 53% у плюсі, у середньому +6.3% — "
              "без знятих з торгів монет це завищено; чесна перевірка обох правил разом дає +3.1% на угоду.",
        "el": "18 μεγάλα νομίσματα του Binance, είσοδος μετά το ημερήσιο κλείσιμο με όγκο από ×1.5: 403 συναλλαγές, 53% με "
              "κέρδος, κατά μέσο όρο +6.3% — χωρίς τα νομίσματα που αποσύρθηκαν, αυτό είναι υπερεκτιμημένο· ο έντιμος έλεγχος "
              "και των δύο κανόνων μαζί δίνει +3.1% ανά συναλλαγή.",
        "es": "18 monedas grandes de Binance, entrada tras el cierre diario con volumen desde ×1.5: 403 operaciones, 53% en "
              "positivo, +6.3% de media; sin las monedas retiradas de cotización esto está inflado: la prueba honesta de las dos "
              "reglas juntas da +3.1% por operación."},
    "hist.E.text": {
        "ru": "18 монет Binance: 3 414 сделок, 60% в плюсе, в среднем +1.26% за сутки — при пороге «падает 70% рынка»; с 16.10 "
              "порог 40%, при 30–50% в среднем +1.07…+1.14%, тоже каждый год в плюсе.",
        "en": "18 Binance coins: 3 414 trades, 60% profitable, +1.26% per day on average — with the threshold “70% of the market "
              "is falling”; since 16.10 the threshold is 40%, and at 30–50% the average is +1.07…+1.14%, also profitable every year.",
        "uk": "18 монет Binance: 3 414 угод, 60% у плюсі, у середньому +1.26% за добу — при порозі «падає 70% ринку»; з 16.10 "
              "поріг 40%, при 30–50% у середньому +1.07…+1.14%, теж щороку в плюсі.",
        "el": "18 νομίσματα του Binance: 3 414 συναλλαγές, 60% με κέρδος, κατά μέσο όρο +1.26% σε 24 ώρες — με όριο «πέφτει το "
              "70% της αγοράς»· από 16.10 το όριο είναι 40%, και στο 30–50% ο μέσος όρος είναι +1.07…+1.14%, επίσης κερδοφόρο "
              "κάθε χρόνο.",
        "es": "18 monedas de Binance: 3 414 operaciones, 60% en positivo, +1.26% de media en un día, con el umbral «cae el 70% "
              "del mercado»; desde el 16.10 el umbral es 40%, y con 30–50% la media es +1.07…+1.14%, también en positivo cada año."},
    "hist.B.text": {
        "ru": "18 монет Binance, вход сразу после свечи с длинным хвостом снизу: 729 сделок, 59% в плюсе, в среднем +0.95%, "
              "за 2025–2026 +1.19%.",
        "en": "18 Binance coins, entry right after a candle with a long lower wick: 729 trades, 59% profitable, +0.95% on "
              "average, +1.19% in 2025–2026.",
        "uk": "18 монет Binance, вхід одразу після свічки з довгим хвостом знизу: 729 угод, 59% у плюсі, у середньому +0.95%, "
              "за 2025–2026 +1.19%.",
        "el": "18 νομίσματα του Binance, είσοδος αμέσως μετά από κερί με μακριά κάτω σκιά: 729 συναλλαγές, 59% με κέρδος, κατά "
              "μέσο όρο +0.95%, το 2025–2026 +1.19%.",
        "es": "18 monedas de Binance, entrada justo después de una vela con una mecha inferior larga: 729 operaciones, 59% en "
              "positivo, +0.95% de media; en 2025–2026, +1.19%."},
    "hist.F.text": {
        "ru": "18 монет Binance с настоящим открытым интересом: 650 сделок, 56% в плюсе, в среднем +1.53% за сутки при стопе "
              "−6%; случайная покупка в те же годы −0.08…−0.14%.",
        "en": "18 Binance coins with real open interest data: 650 trades, 56% profitable, +1.53% per day on average with a −6% "
              "stop; a random buy in the same years gave −0.08…−0.14%.",
        "uk": "18 монет Binance зі справжнім відкритим інтересом: 650 угод, 56% у плюсі, у середньому +1.53% за добу при стопі "
              "−6%; випадкова купівля в ті самі роки −0.08…−0.14%.",
        "el": "18 νομίσματα του Binance με πραγματικά δεδομένα ανοιχτού ενδιαφέροντος: 650 συναλλαγές, 56% με κέρδος, κατά μέσο "
              "όρο +1.53% σε 24 ώρες με stop −6%· μια τυχαία αγορά τα ίδια χρόνια έδινε −0.08…−0.14%.",
        "es": "18 monedas de Binance con datos reales de interés abierto: 650 operaciones, 56% en positivo, +1.53% de media en un "
              "día con stop de −6%; una compra al azar en los mismos años daba −0.08…−0.14%."},
    "hist.D.text": {
        "ru": "18 монет Binance: около 0.4 сделки в день, 55% в плюсе, в среднем +1.77% за сутки; случайный вход на тот же срок "
              "−0.12%. Прибыль держится на редких сильных перекосах.",
        "en": "18 Binance coins: about 0.4 trades a day, 55% profitable, +1.77% per day on average; a random entry for the same "
              "period gave −0.12%. The profit rests on rare, strong mispricings.",
        "uk": "18 монет Binance: близько 0.4 угоди на день, 55% у плюсі, у середньому +1.77% за добу; випадковий вхід на той "
              "самий строк −0.12%. Прибуток тримається на рідкісних сильних перекосах.",
        "el": "18 νομίσματα του Binance: περίπου 0.4 συναλλαγές την ημέρα, 55% με κέρδος, κατά μέσο όρο +1.77% σε 24 ώρες· μια "
              "τυχαία είσοδος για το ίδιο διάστημα έδινε −0.12%. Το κέρδος στηρίζεται σε σπάνιες, έντονες αποκλίσεις.",
        "es": "18 monedas de Binance: unas 0.4 operaciones al día, 55% en positivo, +1.77% de media en un día; una entrada al "
              "azar por el mismo plazo daba −0.12%. La ganancia depende de desajustes fuertes y poco frecuentes."},
    "hist.K.text": {
        "ru": "Проверка «Лаборатории» на топ-50 монет по обороту (с 2023): счёт из 5 позиций +34% за 2025–2026, просадка −12%; "
              "в обвальном 2022 правило было в минусе.",
        "en": "The “Lab” test on the top 50 coins by turnover (since 2023): a 5-position account made +34% in 2025–2026, "
              "drawdown −12%; in the 2022 crash the rule lost money.",
        "uk": "Перевірка «Лабораторії» на топ-50 монет за оборотом (з 2023): рахунок із 5 позицій +34% за 2025–2026, просідання "
              "−12%; в обвальному 2022 правило було в мінусі.",
        "el": "Έλεγχος του «Εργαστηρίου» στα 50 νομίσματα με τον μεγαλύτερο τζίρο (από το 2023): λογαριασμός 5 θέσεων +34% το "
              "2025–2026, μέγιστη πτώση −12%· στην κατάρρευση του 2022 ο κανόνας ήταν σε ζημιά.",
        "es": "Prueba del «Laboratorio» con las 50 monedas de mayor volumen (desde 2023): una cuenta de 5 posiciones ganó +34% "
              "en 2025–2026, con caída máxima de −12%; en el desplome de 2022 la regla perdió dinero."},
    "hist.LST.text": {
        "ru": "Все 729 листингов Binance 2023–2026, включая снятые с торгов, с фандингом и комиссиями: 55% сделок в плюсе, в "
              "среднем +2.6%; худшая сделка −43%, поэтому стоп обязателен.",
        "en": "All 729 Binance listings of 2023–2026, delisted ones included, with funding and fees: 55% of trades profitable, "
              "+2.6% on average; the worst trade was −43%, so the stop is a must.",
        "uk": "Усі 729 лістингів Binance 2023–2026, включно зі знятими з торгів, із фандингом і комісіями: 55% угод у плюсі, у "
              "середньому +2.6%; найгірша угода −43%, тому стоп обов'язковий.",
        "el": "Και οι 729 εισαγωγές του Binance 2023–2026, μαζί με όσες αποσύρθηκαν, με funding και προμήθειες: 55% των "
              "συναλλαγών με κέρδος, κατά μέσο όρο +2.6%· η χειρότερη συναλλαγή −43%, γι' αυτό το stop είναι υποχρεωτικό.",
        "es": "Los 729 listados de Binance de 2023–2026, incluidos los retirados, con funding y comisiones: 55% de operaciones en "
              "positivo, +2.6% de media; la peor operación fue −43%, así que el stop es obligatorio."},

    # ---------------------------------------------------------------- the conditions checked at entry (checks[].t / .v)
    "vs.above": {"ru": "на {pct} выше", "en": "{pct} above", "uk": "на {pct} вище", "el": "{pct} πάνω από",
                 "es": "un {pct} por encima de"},
    "vs.below": {"ru": "на {pct} ниже", "en": "{pct} below", "uk": "на {pct} нижче", "el": "{pct} κάτω από",
                 "es": "un {pct} por debajo de"},
    "chk.trend": {"ru": "Тренд", "en": "Trend", "uk": "Тренд", "el": "Τάση", "es": "Tendencia"},
    "chk.trend.v50": {"ru": "цена {vs} средней за 50 дней", "en": "price {vs} the 50-day average",
                      "uk": "ціна {vs} середньої за 50 днів", "el": "τιμή {vs} τον μέσο όρο 50 ημερών",
                      "es": "precio {vs} la media de 50 días"},
    "chk.trend.v20": {"ru": "цена {vs} средней за 20 дней", "en": "price {vs} the 20-day average",
                      "uk": "ціна {vs} середньої за 20 днів", "el": "τιμή {vs} τον μέσο όρο 20 ημερών",
                      "es": "precio {vs} la media de 20 días"},
    "chk.trend_up": {"ru": "Тренд растёт", "en": "Trend rising", "uk": "Тренд зростає", "el": "Η τάση ανεβαίνει",
                     "es": "La tendencia sube"},
    "chk.trend_up.v": {"ru": "средняя за 50 дней {pct} за 5 дней", "en": "50-day average {pct} over 5 days",
                       "uk": "середня за 50 днів {pct} за 5 днів", "el": "μέσος όρος 50 ημερών {pct} σε 5 ημέρες",
                       "es": "media de 50 días {pct} en 5 días"},
    "chk.pullback": {"ru": "Откат", "en": "Pullback", "uk": "Відкат", "el": "Υποχώρηση", "es": "Retroceso"},
    "chk.pullback.v": {"ru": "{chg} за 2 дня, RSI(2) = {rsi} (вход ниже 10)", "en": "{chg} over 2 days, RSI(2) = {rsi} (entry below 10)",
                       "uk": "{chg} за 2 дні, RSI(2) = {rsi} (вхід нижче 10)",
                       "el": "{chg} σε 2 ημέρες, RSI(2) = {rsi} (είσοδος κάτω από 10)",
                       "es": "{chg} en 2 días, RSI(2) = {rsi} (entrada por debajo de 10)"},
    "chk.pullback.v_rsi": {"ru": "RSI(2) = {rsi} (вход ниже 10)", "en": "RSI(2) = {rsi} (entry below 10)",
                           "uk": "RSI(2) = {rsi} (вхід нижче 10)", "el": "RSI(2) = {rsi} (είσοδος κάτω από 10)",
                           "es": "RSI(2) = {rsi} (entrada por debajo de 10)"},
    "chk.breakout": {"ru": "Пробой", "en": "Breakout", "uk": "Пробій", "el": "Διάσπαση", "es": "Ruptura"},
    "chk.breakout.v": {"ru": "закрытие {vs} максимума 20 дней", "en": "close {vs} the 20-day high",
                       "uk": "закриття {vs} максимуму 20 днів", "el": "κλείσιμο {vs} το υψηλό 20 ημερών",
                       "es": "cierre {vs} el máximo de 20 días"},
    "chk.market": {"ru": "Рынок", "en": "Market", "uk": "Ринок", "el": "Αγορά", "es": "Mercado"},
    "chk.market.v": {"ru": "BTC {vs} средней за 200 дней и {pct} за 60 дней — {mode}",
                     "en": "BTC {vs} the 200-day average and {pct} over 60 days — {mode}",
                     "uk": "BTC {vs} середньої за 200 днів і {pct} за 60 днів — {mode}",
                     "el": "BTC {vs} τον μέσο όρο 200 ημερών και {pct} σε 60 ημέρες — {mode}",
                     "es": "BTC {vs} la media de 200 días y {pct} en 60 días: {mode}"},
    "chk.market.bull": {"ru": "режим роста", "en": "uptrend mode", "uk": "режим зростання", "el": "καθεστώς ανόδου",
                        "es": "modo alcista"},
    "chk.market.not": {"ru": "не рост", "en": "not an uptrend", "uk": "не зростання", "el": "όχι άνοδος", "es": "no es alcista"},
    "chk.volume": {"ru": "Объём", "en": "Volume", "uk": "Обсяг", "el": "Όγκος", "es": "Volumen"},
    "chk.volume.v30": {"ru": "×{x} к среднему за 30 дней (нужно от ×1.5)", "en": "×{x} vs the 30-day average (needs ×1.5 or more)",
                       "uk": "×{x} до середнього за 30 днів (потрібно від ×1.5)",
                       "el": "×{x} σε σχέση με τον μέσο όρο 30 ημερών (χρειάζεται από ×1.5)",
                       "es": "×{x} frente a la media de 30 días (se necesita desde ×1.5)"},
    "chk.volume.vday": {"ru": "×{x} к среднему за сутки (нужно от ×{need})", "en": "×{x} vs the daily average (needs ×{need} or more)",
                        "uk": "×{x} до середнього за добу (потрібно від ×{need})",
                        "el": "×{x} σε σχέση με τον μέσο όρο της ημέρας (χρειάζεται από ×{need})",
                        "es": "×{x} frente a la media del día (se necesita desde ×{need})"},
    "chk.liquidity": {"ru": "Ликвидность", "en": "Liquidity", "uk": "Ліквідність", "el": "Ρευστότητα", "es": "Liquidez"},
    "chk.liquidity.v": {"ru": "№{rank} из {of} самых торгуемых монет за 30 дней",
                        "en": "No. {rank} of the {of} most traded coins over 30 days",
                        "uk": "№{rank} із {of} найбільш торгованих монет за 30 днів",
                        "el": "θέση {rank} από τα {of} νομίσματα με τις περισσότερες συναλλαγές σε 30 ημέρες",
                        "es": "puesto {rank} de las {of} monedas más negociadas en 30 días"},
    "chk.liquidity.vK": {"ru": "№{rank} из {n} альткоинов по обороту за сутки — места получают самые торгуемые",
                         "en": "No. {rank} of {n} altcoins by 24h turnover — the slots go to the most traded",
                         "uk": "№{rank} із {n} альткоїнів за оборотом за добу — місця отримують найбільш торговані",
                         "el": "θέση {rank} από {n} altcoins σε τζίρο 24 ωρών — τις θέσεις παίρνουν όσα έχουν τις περισσότερες "
                               "συναλλαγές",
                         "es": "puesto {rank} de {n} altcoins por volumen en 24 h: los puestos son para las más negociadas"},
    "chk.selloff": {"ru": "Распродажа", "en": "Sell-off", "uk": "Розпродаж", "el": "Ξεπούλημα", "es": "Venta masiva"},
    "chk.selloff.v": {"ru": "за сутки упали больше чем на 3%: {b} монет (нужно от {need})",
                      "en": "fell more than 3% in 24h: {b} of coins (needs {need} or more)",
                      "uk": "за добу впали більш ніж на 3%: {b} монет (потрібно від {need})",
                      "el": "έπεσαν πάνω από 3% σε 24 ώρες: το {b} των νομισμάτων (χρειάζεται από {need})",
                      "es": "cayeron más de un 3% en 24 h: el {b} de las monedas (se necesita desde {need})"},
    "chk.candle": {"ru": "Свеча", "en": "Candle", "uk": "Свічка", "el": "Κερί", "es": "Vela"},
    "chk.candle.v": {"ru": "{drop} за 15 минут (нужно {need} и ниже)", "en": "{drop} in 15 minutes (needs {need} or lower)",
                     "uk": "{drop} за 15 хвилин (потрібно {need} і нижче)", "el": "{drop} σε 15 λεπτά (χρειάζεται {need} ή χαμηλότερα)",
                     "es": "{drop} en 15 minutos (se necesita {need} o menos)"},
    "chk.crash": {"ru": "Обвал свечи", "en": "Candle crash", "uk": "Обвал свічки", "el": "Βουτιά κεριού", "es": "Desplome de la vela"},
    "chk.crash.v": {"ru": "{ret} за 15 минут — в {x} раза больше обычного колебания (нужно от {need})",
                    "en": "{ret} in 15 minutes — {x} times the usual swing (needs {need} or more)",
                    "uk": "{ret} за 15 хвилин — у {x} раза більше за звичайне коливання (потрібно від {need})",
                    "el": "{ret} σε 15 λεπτά — {x} φορές πάνω από τη συνηθισμένη διακύμανση (χρειάζεται από {need})",
                    "es": "{ret} en 15 minutos: {x} veces la oscilación habitual (se necesita desde {need})"},
    "chk.wick": {"ru": "Хвост снизу", "en": "Lower wick", "uk": "Хвіст знизу", "el": "Κάτω σκιά", "es": "Mecha inferior"},
    "chk.wick.v": {"ru": "{w} свечи — падение выкупили (нужно от {need})", "en": "{w} of the candle — the drop was bought up (needs {need} or more)",
                   "uk": "{w} свічки — падіння викупили (потрібно від {need})",
                   "el": "{w} του κεριού — η πτώση αγοράστηκε (χρειάζεται από {need})",
                   "es": "{w} de la vela: la caída se compró (se necesita desde {need})"},
    "chk.oi": {"ru": "Открытый интерес", "en": "Open interest", "uk": "Відкритий інтерес", "el": "Ανοιχτό ενδιαφέρον",
               "es": "Interés abierto"},
    "chk.price": {"ru": "Цена", "en": "Price", "uk": "Ціна", "el": "Τιμή", "es": "Precio"},
    "chk.4h.v": {"ru": "{x} за 4 часа (нужно {need} и ниже)", "en": "{x} in 4 hours (needs {need} or lower)",
                 "uk": "{x} за 4 години (потрібно {need} і нижче)", "el": "{x} σε 4 ώρες (χρειάζεται {need} ή χαμηλότερα)",
                 "es": "{x} en 4 horas (se necesita {need} o menos)"},
    "chk.discount": {"ru": "Дисконт фьючерса", "en": "Futures discount", "uk": "Дисконт ф'ючерса", "el": "Έκπτωση του future",
                     "es": "Descuento del futuro"},
    "chk.discount.below": {"ru": "фьючерс {b} ниже спота в среднем за час (нужно от {need} ниже)",
                           "en": "futures {b} below spot on average over an hour (needs {need} below or more)",
                           "uk": "ф'ючерс на {b} нижче спота в середньому за годину (потрібно від {need} нижче)",
                           "el": "το future {b} κάτω από το spot κατά μέσο όρο σε μία ώρα (χρειάζεται από {need} κάτω)",
                           "es": "el futuro un {b} por debajo del spot de media en una hora (se necesita desde {need} por debajo)"},
    "chk.discount.above": {"ru": "фьючерс {b} выше спота в среднем за час (нужно от {need} ниже)",
                           "en": "futures {b} above spot on average over an hour (needs {need} below or more)",
                           "uk": "ф'ючерс на {b} вище спота в середньому за годину (потрібно від {need} нижче)",
                           "el": "το future {b} πάνω από το spot κατά μέσο όρο σε μία ώρα (χρειάζεται από {need} κάτω)",
                           "es": "el futuro un {b} por encima del spot de media en una hora (se necesita desde {need} por debajo)"},
    "chk.btc_jump": {"ru": "Рывок BTC", "en": "BTC surge", "uk": "Ривок BTC", "el": "Άλμα BTC", "es": "Impulso de BTC"},
    "chk.btc_jump.v": {"ru": "{ret} за час (нужно от {need})", "en": "{ret} in an hour (needs {need} or more)",
                       "uk": "{ret} за годину (потрібно від {need})", "el": "{ret} σε μία ώρα (χρειάζεται από {need})",
                       "es": "{ret} en una hora (se necesita desde {need})"},
    "chk.listing": {"ru": "Новый листинг", "en": "New listing", "uk": "Новий лістинг", "el": "Νέα εισαγωγή", "es": "Nuevo listado"},
    "chk.listing.v": {"ru": "фьючерс появился {h} ч назад (вход через 4–24 ч)",
                      "en": "the futures contract appeared {h} h ago (entry 4–24 h after listing)",
                      "uk": "ф'ючерс з'явився {h} год тому (вхід через 4–24 год)",
                      "el": "το future εμφανίστηκε πριν από {h} ώρ. (είσοδος σε 4–24 ώρ.)",
                      "es": "el futuro apareció hace {h} h (entrada a las 4–24 h)"},
    "chk.no_pump": {"ru": "Без пампа", "en": "No pump", "uk": "Без пампу", "el": "Χωρίς pump", "es": "Sin pump"},
    "chk.no_pump.v": {"ru": "{move} от первой цены (пропускаем от {skip})", "en": "{move} from the first price (skipped at {skip} or more)",
                      "uk": "{move} від першої ціни (пропускаємо від {skip})",
                      "el": "{move} από την πρώτη τιμή (παραλείπουμε από {skip})",
                      "es": "{move} desde el primer precio (descartamos desde {skip})"},
    "chk.turnover": {"ru": "Оборот", "en": "Turnover", "uk": "Оборот", "el": "Τζίρος", "es": "Volumen negociado"},
    "chk.turnover.v": {"ru": "${q} млн с листинга (нужно от ${min} млн)", "en": "${q}M since listing (needs ${min}M or more)",
                       "uk": "${q} млн з лістингу (потрібно від ${min} млн)",
                       "el": "${q} εκατ. από την εισαγωγή (χρειάζεται από ${min} εκατ.)",
                       "es": "${q} M desde el listado (se necesita desde ${min} M)"},
    "chk.token": {"ru": "Токен", "en": "Token", "uk": "Токен", "el": "Token", "es": "Token"},
    "chk.token.v": {"ru": "настоящая монета, не синтетическая пара (валюта, товар, акция)",
                    "en": "a real coin, not a synthetic pair (currency, commodity, stock)",
                    "uk": "справжня монета, не синтетична пара (валюта, товар, акція)",
                    "el": "πραγματικό κρυπτονόμισμα, όχι συνθετικό ζεύγος (συνάλλαγμα, εμπόρευμα, μετοχή)",
                    "es": "una moneda real, no un par sintético (divisa, materia prima, acción)"},
    "chk.coin": {"ru": "Монета", "en": "Coin", "uk": "Монета", "el": "Νόμισμα", "es": "Moneda"},
    "chk.coin.v": {"ru": "одна из {n} основных монет, на которых правило проверено на истории",
                   "en": "one of the {n} main coins the rule was tested on historically",
                   "uk": "одна з {n} основних монет, на яких правило перевірено на історичних даних",
                   "el": "ένα από τα {n} βασικά νομίσματα στα οποία ο κανόνας ελέγχθηκε στο ιστορικό",
                   "es": "una de las {n} monedas principales con las que se probó la regla en el histórico"},

    # ---------------------------------------------------------------- holding time (risk.hold)
    "hold.P": {"ru": "до {d} дней; раньше — когда закрытие вернётся выше средней за 5 дней",
               "en": "up to {d} days; earlier if the close gets back above the 5-day average",
               "uk": "до {d} днів; раніше — коли закриття повернеться вище середньої за 5 днів",
               "el": "έως {d} ημέρες· νωρίτερα, όταν το κλείσιμο επιστρέψει πάνω από τον μέσο όρο 5 ημερών",
               "es": "hasta {d} días; antes, si el cierre vuelve por encima de la media de 5 días"},
    "hold.C": {"ru": "до {d} дней; раньше — если закрытие уйдёт на 3 ATR ниже лучшего",
               "en": "up to {d} days; earlier if the close falls 3 ATR below the best one",
               "uk": "до {d} днів; раніше — якщо закриття опуститься на 3 ATR нижче найкращого",
               "el": "έως {d} ημέρες· νωρίτερα, αν το κλείσιμο πέσει 3 ATR κάτω από το καλύτερο",
               "es": "hasta {d} días; antes, si el cierre cae 3 ATR por debajo del mejor"},
    "hold.time": {"ru": "{h} {hours} — выход по времени, цель только ориентир", "en": "{h} {hours} — time-based exit, the target is only a guide",
                  "uk": "{h} {hours} — вихід за часом, ціль лише орієнтир",
                  "el": "{h} {hours} — έξοδος βάσει χρόνου, ο στόχος είναι μόνο ενδεικτικός",
                  "es": "{h} {hours}: salida por tiempo, el objetivo es solo orientativo"},
    "hold.B": {"ru": "до {h} часов или до цели +1R", "en": "up to {h} hours or until the +1R target",
               "uk": "до {h} годин або до цілі +1R", "el": "έως {h} ώρες ή μέχρι τον στόχο +1R",
               "es": "hasta {h} horas o hasta el objetivo +1R"},
    "hold.LST": {"ru": "{d} дней — выход по времени", "en": "{d} days — time-based exit", "uk": "{d} днів — вихід за часом",
                 "el": "{d} ημέρες — έξοδος βάσει χρόνου", "es": "{d} días: salida por tiempo"},

    # ---------------------------------------------------------------- the scan behind the signals (futures.scan.text, work)
    "scan.head.today": {"ru": "Сегодня система проверила {n} {coins} по {k} условиям",
                        "en": "Today the system checked {n} {coins} against {k} conditions",
                        "uk": "Сьогодні система перевірила {n} {coins} за {k} умовами",
                        "el": "Σήμερα το σύστημα έλεγξε {n} {coins} με {k} κριτήρια",
                        "es": "Hoy el sistema revisó {n} {coins} con {k} condiciones"},
    "scan.head.day": {"ru": "{day} система проверила {n} {coins} по {k} условиям",
                      "en": "On {day} the system checked {n} {coins} against {k} conditions",
                      "uk": "{day} система перевірила {n} {coins} за {k} умовами",
                      "el": "Στις {day} το σύστημα έλεγξε {n} {coins} με {k} κριτήρια",
                      "es": "El {day} el sistema revisó {n} {coins} con {k} condiciones"},
    "scan.bear": {"ru": ": рынок не в режиме роста — покупок нет, деньги ждут в долларах.",
                  "en": ": the market is not in uptrend mode — no buys, the money waits in dollars.",
                  "uk": ": ринок не в режимі зростання — купівель немає, гроші чекають у доларах.",
                  "el": ": η αγορά δεν είναι σε καθεστώς ανόδου — καμία αγορά, τα χρήματα περιμένουν σε δολάρια.",
                  "es": ": el mercado no está en modo alcista; no hay compras y el dinero espera en dólares."},
    "scan.signals": {"ru": ": правила дали {s} {signals}, все условия прошли {p}.",
                     "en": ": the rules gave {s} {signals}, {p} passed every condition.",
                     "uk": ": правила дали {s} {signals}, усі умови пройшли {p}.",
                     "el": ": οι κανόνες έδωσαν {s} {signals}, όλα τα κριτήρια πέρασαν {p}.",
                     "es": ": las reglas dieron {s} {signals} y {p} cumplieron todas las condiciones."},
    "work.K": {"ru": "В момент входа система проверяла {n} {alts}: рывок BTC даёт сигнал всем, места получают самые торгуемые.",
               "en": "At entry the system was checking {n} {alts}: a BTC surge signals them all, the slots go to the most traded.",
               "uk": "У момент входу система перевіряла {n} {alts}: ривок BTC дає сигнал усім, місця отримують найбільш торговані.",
               "el": "Τη στιγμή της εισόδου το σύστημα έλεγχε {n} {alts}: το άλμα του BTC δίνει σήμα σε όλα, τις θέσεις παίρνουν "
                     "όσα έχουν τις περισσότερες συναλλαγές.",
               "es": "En el momento de la entrada el sistema revisaba {n} {alts}: el impulso de BTC da señal a todas y los puestos "
                     "son para las más negociadas."},
    "work.K.slots": {"ru": "В момент входа система проверяла {n} {alts}: рывок BTC даёт сигнал всем, места получают {slots} самых "
                           "торгуемых.",
                     "en": "At entry the system was checking {n} {alts}: a BTC surge signals them all, the slots go to the {slots} "
                           "most traded.",
                     "uk": "У момент входу система перевіряла {n} {alts}: ривок BTC дає сигнал усім, місця отримують {slots} "
                           "найбільш торгованих.",
                     "el": "Τη στιγμή της εισόδου το σύστημα έλεγχε {n} {alts}: το άλμα του BTC δίνει σήμα σε όλα, τις θέσεις "
                           "παίρνουν τα {slots} με τις περισσότερες συναλλαγές.",
                     "es": "En el momento de la entrada el sistema revisaba {n} {alts}: el impulso de BTC da señal a todas y los "
                           "puestos son para las {slots} más negociadas."},
    "work.rule": {"ru": "В момент входа система проверяла {n} {coins} по {r} {rules} — «{rule}» сработал у {k} из них.",
                  "en": "At entry the system was checking {n} {coins} against {r} {rules} — “{rule}” fired on {k} of them.",
                  "uk": "У момент входу система перевіряла {n} {coins} за {r} {rules} — «{rule}» спрацював у {k} з них.",
                  "el": "Τη στιγμή της εισόδου το σύστημα έλεγχε {n} {coins} με {r} {rules} — ο κανόνας «{rule}» ενεργοποιήθηκε "
                        "σε {k} από αυτά.",
                  "es": "En el momento de la entrada el sistema revisaba {n} {coins} con {r} {rules}: «{rule}» se activó en {k} "
                        "de ellas."},
    "work.LST": {"ru": "Система следит за {w} {futures} BingX: шорт получает только новый листинг, прошедший все фильтры.",
                 "en": "The system watches {w} BingX {futures}: only a new listing that passes every filter gets a short.",
                 "uk": "Система стежить за {w} {futures} BingX: шорт отримує лише новий лістинг, що пройшов усі фільтри.",
                 "el": "Το σύστημα παρακολουθεί {w} {futures} του BingX: short παίρνει μόνο μια νέα εισαγωγή που περνά όλα τα φίλτρα.",
                 "es": "El sistema vigila {w} {futures} de BingX: solo recibe un short el nuevo listado que pasa todos los filtros."},
    "note.v1": {"ru": "Сделка открыта {d} по первой версии правил: объём дня и место в топ-40 по обороту тогда ещё не проверялись.",
                "en": "The trade was opened on {d} under the first version of the rules: the day's volume and the top-40 turnover "
                      "rank weren't checked yet.",
                "uk": "Угоду відкрито {d} за першою версією правил: обсяг дня та місце в топ-40 за оборотом тоді ще не перевірялися.",
                "el": "Η συναλλαγή άνοιξε στις {d} με την πρώτη έκδοση των κανόνων: ο όγκος της ημέρας και η θέση στο top-40 σε "
                      "τζίρο δεν ελέγχονταν ακόμη.",
                "es": "La operación se abrió el {d} con la primera versión de las reglas: entonces aún no se revisaban el volumen "
                      "del día ni el puesto en el top 40 por volumen."},

    # ---------------------------------------------------------------- the futures card (futures.status_ru / about_ru)
    "status.main": {"ru": "Основная стратегия сейчас без сделок: она входит только после резкой распродажи на большом объёме — "
                          "так дешевле и на истории выгоднее.",
                    "en": "The main strategy has no trades right now: it only enters after a sharp sell-off on heavy volume — "
                          "that's cheaper and historically more profitable.",
                    "uk": "Основна стратегія зараз без угод: вона входить лише після різкого розпродажу на великому обсязі — так "
                          "дешевше й на історичних даних вигідніше.",
                    "el": "Η κύρια στρατηγική δεν έχει τώρα συναλλαγές: μπαίνει μόνο μετά από απότομο ξεπούλημα με μεγάλο όγκο — "
                          "έτσι είναι φθηνότερα και ιστορικά πιο επικερδές.",
                    "es": "La estrategia principal no tiene operaciones ahora: solo entra tras una venta brusca con mucho volumen; "
                          "así sale más barato y, en el histórico, más rentable."},
    "status.last": {"ru": " Последняя сделка: {sym}, {pnl}, {d}.", "en": " Last trade: {sym}, {pnl}, {d}.",
                    "uk": " Остання угода: {sym}, {pnl}, {d}.", "el": " Τελευταία συναλλαγή: {sym}, {pnl}, {d}.",
                    "es": " Última operación: {sym}, {pnl}, {d}."},
    "status.other": {"ru": " В работе {n} {trades} других сигналов CRYPTO NOVA.",
                     "en": " Other CRYPTO NOVA signals in progress: {n} {trades}.",
                     "uk": " У роботі {n} {trades} інших сигналів CRYPTO NOVA.",
                     "el": " Σε εξέλιξη {n} {trades} άλλων σημάτων του CRYPTO NOVA.",
                     "es": " En curso: {n} {trades} de otras señales de CRYPTO NOVA."},
    "futures.about": {"ru": "Один демо-счёт $10 000 по настоящим ценам: система сама делит его между проверенными стратегиями. "
                            "Сделки «в проверке» войдут в итог после 30 закрытых сделок.",
                      "en": "One $10 000 demo account at real prices: the system splits it between proven strategies itself. "
                            "Trades marked «in testing» join the result after 30 closed trades.",
                      "uk": "Один демо-рахунок $10 000 за справжніми цінами: система сама ділить його між перевіреними стратегіями. "
                            "Угоди «на перевірці» увійдуть у підсумок після 30 закритих угод.",
                      "el": "Ένας demo λογαριασμός $10 000 σε πραγματικές τιμές: το σύστημα τον μοιράζει μόνο του σε δοκιμασμένες "
                            "στρατηγικές. Οι συναλλαγές «σε δοκιμή» μπαίνουν στο αποτέλεσμα μετά από 30 κλεισμένες συναλλαγές.",
                      "es": "Una cuenta demo de $10 000 a precios reales: el sistema la reparte solo entre estrategias probadas. "
                            "Las operaciones «en prueba» entran en el resultado tras 30 operaciones cerradas."},
    # ---------------------------------------------------------------- «Если покупать сегодня» (buy_now.items[].why / grade_text)
    "buy.sma200": {"ru": "цена выше средней за 200 дней", "en": "above its 200-day average", "uk": "вище 200-денної середньої",
                   "el": "πάνω από τον μέσο όρο 200 ημερών", "es": "por encima de su media de 200 días"},
    "buy.support": {"ru": "у поддержки {p}: цена {n} {times} отскакивала от этого уровня",
                    "en": "at support {p}: the price bounced off this level {n} {times}",
                    "uk": "біля підтримки {p}: ціна {n} {times} відскакувала від цього рівня",
                    "el": "κοντά στη στήριξη {p}: η τιμή αναπήδησε {n} {times} από αυτό το επίπεδο",
                    "es": "en el soporte {p}: el precio rebotó {n} {times} en este nivel"},
    "buy.rsi": {"ru": "RSI {v} — не перегрета", "en": "RSI {v}: not overheated", "uk": "RSI {v} — не перегріта",
                "el": "RSI {v}: όχι υπερθερμασμένο", "es": "RSI {v}: no sobrecalentada"},
    "buy.quality": {"ru": "оценка качества {v} из 100", "en": "quality score {v}/100", "uk": "оцінка якості {v} зі 100",
                    "el": "βαθμός ποιότητας {v}/100", "es": "puntuación de calidad {v}/100"},
    "buy.whales": {"ru": "крупные трейдеры в лонге", "en": "the largest traders are long", "uk": "великі трейдери в лонгу",
                   "el": "οι μεγαλύτεροι traders είναι long", "es": "los grandes traders están en largo"},
    "buy.grade.strong": {"ru": "сильный", "en": "strong", "uk": "сильний", "el": "ισχυρός", "es": "fuerte"},
    "buy.grade.good": {"ru": "хороший", "en": "good", "uk": "добрий", "el": "καλός", "es": "bueno"},
    "buy.grade.possible": {"ru": "можно рассмотреть", "en": "possible", "uk": "можливий", "el": "πιθανός", "es": "posible"},
    "sources.soon": {"ru": "арбитраж, рынки прогнозов", "en": "arbitrage, prediction markets", "uk": "арбітраж, ринки прогнозів",
                     "el": "αρμπιτράζ, αγορές προβλέψεων", "es": "arbitraje, mercados de predicción"},
    "home.note": {"ru": "Демо-счета CRYPTO NOVA. Прошлые результаты не гарантируют будущих. Не финансовый совет.",
                  "en": "CRYPTO NOVA demo accounts. Past results don't guarantee future ones. Not financial advice.",
                  "uk": "Демо-рахунки CRYPTO NOVA. Минулі результати не гарантують майбутніх. Не фінансова порада.",
                  "el": "Λογαριασμοί demo του CRYPTO NOVA. Οι προηγούμενες αποδόσεις δεν εγγυώνται τις μελλοντικές. Δεν "
                        "αποτελεί επενδυτική συμβουλή.",
                  "es": "Cuentas demo de CRYPTO NOVA. Los resultados pasados no garantizan los futuros. No es asesoramiento "
                        "financiero."},

    # ---------------------------------------------------------------- the spot portfolio's last action (spot.last_action.text)
    # Russian keeps the portfolio's own sentence (why_ru); the other languages get these, with the numbers read from it
    "act.trim_plan": {"ru": "{sym} выросла до {w}% счёта — больше плановых {p}%; лишнее продаём.",
                      "en": "{sym} grew to {w}% of the portfolio, above its {p}% plan — the excess is sold.",
                      "uk": "{sym}: {w}% портфеля — більше за план {p}%; зайве продаємо.",
                      "el": "Το {sym} έφτασε το {w}% του χαρτοφυλακίου, πάνω από το πλάνο του {p}% — πουλάμε το επιπλέον.",
                      "es": "{sym} llegó al {w}% de la cartera, por encima de su plan del {p}%: vendemos el exceso."},
    "act.add_core": {"ru": "{sym} — монета ядра: сейчас {w}% счёта, система докупает её до {p}%.",
                     "en": "{sym} is a core coin: {w}% of the portfolio now, the system tops it up to {p}%.",
                     "uk": "{sym} — монета ядра: зараз {w}% портфеля, система докуповує її до {p}%.",
                     "el": "Το {sym} είναι νόμισμα του πυρήνα: τώρα {w}% του χαρτοφυλακίου, το σύστημα το συμπληρώνει έως το {p}%.",
                     "es": "{sym} es una moneda del núcleo: ahora el {w}% de la cartera; el sistema la completa hasta el {p}%."},
    "act.buy_table": {"ru": "Купили {sym}: монета прошла все проверки системы; {w}% счёта.",
                      "en": "Bought {sym}: the coin passed all of the system's checks; {w}% of the portfolio.",
                      "uk": "Купили {sym}: монета пройшла всі перевірки системи; {w}% портфеля.",
                      "el": "Αγοράσαμε {sym}: το νόμισμα πέρασε όλους τους ελέγχους του συστήματος· {w}% του χαρτοφυλακίου.",
                      "es": "Compramos {sym}: la moneda pasó todos los controles del sistema; {w}% de la cartera."},
    "act.BUY": {"ru": "Купили {sym}: монета прошла проверки портфеля.", "en": "Bought {sym}: the coin passed the portfolio's checks.",
                "uk": "Купили {sym}: монета пройшла перевірки портфеля.",
                "el": "Αγοράσαμε {sym}: το νόμισμα πέρασε τους ελέγχους του χαρτοφυλακίου.",
                "es": "Compramos {sym}: la moneda pasó los controles de la cartera."},
    "act.ADD": {"ru": "Докупили {sym} до плановой доли.", "en": "Topped up {sym} to its planned share.",
                "uk": "Докупили {sym} до планової частки.", "el": "Συμπληρώσαμε το {sym} έως το προβλεπόμενο μερίδιο.",
                "es": "Completamos {sym} hasta su peso previsto."},
    "act.TRIM": {"ru": "Продали часть {sym}: зафиксировали прибыль или вернули долю к плану.",
                 "en": "Sold part of {sym}: profit taken or the share brought back to plan.",
                 "uk": "Продали частину {sym}: зафіксували прибуток або повернули частку до плану.",
                 "el": "Πουλήσαμε μέρος του {sym}: κατοχύρωση κέρδους ή επιστροφή του μεριδίου στο πλάνο.",
                 "es": "Vendimos parte de {sym}: recogimos ganancias o devolvimos su peso al plan."},
    "act.SELL": {"ru": "Продали {sym} по правилам портфеля (стоп, статус или пересмотр).",
                 "en": "Sold {sym} by the portfolio's rules (stop, status or review).",
                 "uk": "Продали {sym} за правилами портфеля (стоп, статус або перегляд).",
                 "el": "Πουλήσαμε το {sym} σύμφωνα με τους κανόνες του χαρτοφυλακίου (stop, κατάσταση ή επανεξέταση).",
                 "es": "Vendimos {sym} según las reglas de la cartera (stop, estado o revisión)."},
    "act.other": {"ru": "Изменение в портфеле: {sym}.", "en": "Portfolio change: {sym}.", "uk": "Зміна в портфелі: {sym}.",
                  "el": "Αλλαγή στο χαρτοφυλάκιο: {sym}.", "es": "Cambio en la cartera: {sym}."},

    # ---------------------------------------------------------------- NOVA's own answers (POST /v2/agent, cnq/api/nova.py)
    "nova.busy": {"ru": "NOVA сейчас отвечает очень многим сразу. Попробуйте через минуту — этот вопрос не списан.",
                  "en": "NOVA is answering a lot of people at once right now. Try again in a minute — this question wasn't counted.",
                  "uk": "NOVA зараз відповідає дуже багатьом одночасно. Спробуйте за хвилину — це запитання не списано.",
                  "el": "Η NOVA απαντά αυτή τη στιγμή σε πάρα πολλούς ταυτόχρονα. Δοκιμάστε ξανά σε ένα λεπτό — αυτή η ερώτηση "
                        "δεν χρεώθηκε.",
                  "es": "NOVA está respondiendo a muchísima gente a la vez. Prueba de nuevo en un minuto: esta pregunta no se ha "
                        "descontado."},
    "nova.bad_question": {"ru": "Не получилось разобрать вопрос. Скажите или напишите его ещё раз — я на связи.",
                          "en": "I couldn't make out the question. Say or type it again — I'm here.",
                          "uk": "Не вдалося розібрати запитання. Скажіть або напишіть його ще раз — я на зв'язку.",
                          "el": "Δεν κατάλαβα την ερώτηση. Πείτε ή γράψτε τη ξανά — είμαι εδώ.",
                          "es": "No he podido entender la pregunta. Dímela o escríbela otra vez: aquí estoy."},
    # 28.10 (build 46 §11.4): what to do after the limit — the ready-made questions still work (answered from the FAQ, not counted);
    # no promise of more questions for connecting: an OKX copy is not confirmed in the app (only a BingX-checked account is «vip»)
    "nova.limit_free": {"ru": "На сегодня 5 вопросов закончились — завтра будут новые. На вопросы из готового списка ниже я отвечу и "
                              "сейчас.",
                        "en": "Your 5 questions for today are used up — new ones come tomorrow. I can still answer the ready-made "
                              "questions from the list below.",
                        "uk": "На сьогодні 5 запитань закінчилися — завтра будуть нові. На запитання з готового списку нижче я "
                              "відповім і зараз.",
                        "el": "Οι 5 ερωτήσεις για σήμερα τελείωσαν — αύριο θα έχετε καινούργιες. Στις έτοιμες ερωτήσεις της λίστας "
                              "παρακάτω απαντώ και τώρα.",
                        "es": "Ya has usado tus 5 preguntas de hoy; mañana tendrás nuevas. A las preguntas de la lista de abajo te "
                              "respondo también ahora."},
    "nova.limit_vip": {"ru": "На сегодня вопросы закончились — завтра лимит обновится. На вопросы из готового списка ниже я отвечу и сейчас.",
                       "en": "You're out of questions for today — the limit resets tomorrow. I can still answer the ready-made questions "
                             "from the list below.",
                       "uk": "На сьогодні запитання закінчилися — завтра ліміт оновиться. На запитання з готового списку нижче я "
                             "відповім і зараз.",
                       "el": "Οι ερωτήσεις για σήμερα τελείωσαν — αύριο το όριο ανανεώνεται. Στις έτοιμες ερωτήσεις της λίστας "
                             "παρακάτω απαντώ και τώρα.",
                       "es": "Por hoy se acabaron las preguntas; mañana se renueva el límite. A las preguntas de la lista de abajo te "
                             "respondo también ahora."},

    # ---------------------------------------------------------------- build 44: the public portfolio = the panel's header
    # (app_home "public": spot coins «почему», «Рассчитать мой портфель» — every sentence is a rule or a number of the system)
    "pub.why.core": {"ru": "{sym} — ядро портфеля: держим долго, через рыночные циклы, без стопа. План — {plan}% портфеля: если доля "
                           "упадёт ниже ¾ плана, система докупит, если вырастет больше чем в 1,5 раза — продаст лишнее.",
                     "en": "{sym} is the core of the portfolio: held for the long run, through market cycles, with no stop. The plan "
                           "is {plan}% of the portfolio: if its share falls below ¾ of the plan, the system buys more; if it grows "
                           "past 1.5 times the plan, it sells the extra.",
                     "uk": "{sym} — ядро портфеля: тримаємо довго, крізь ринкові цикли, без стопа. План — {plan}% портфеля: якщо "
                           "частка впаде нижче ¾ плану, система докупить, якщо виросте більш ніж у 1,5 раза — продасть зайве.",
                     "el": "Το {sym} είναι ο πυρήνας του χαρτοφυλακίου: το κρατάμε μακροπρόθεσμα, μέσα από τους κύκλους της "
                           "αγοράς, χωρίς stop. Το σχέδιο είναι {plan}% του χαρτοφυλακίου: αν το μερίδιο πέσει κάτω από τα ¾ "
                           "του σχεδίου, το σύστημα αγοράζει κι άλλο· αν ξεπεράσει το 1,5 φορές, πουλά το επιπλέον.",
                     "es": "{sym} es el núcleo de la cartera: lo mantenemos a largo plazo, a través de los ciclos del mercado, sin "
                           "stop. El plan es el {plan}% de la cartera: si su parte baja de ¾ del plan, el sistema compra más; si "
                           "supera 1,5 veces el plan, vende lo que sobra."},
    "pub.why.btc": {"ru": "Биткоин — самая большая доля: в 2025–2026 он падал меньше эфира и соланы.",
                    "en": "Bitcoin gets the biggest share: in 2025–2026 it fell less than Ether and Solana.",
                    "uk": "Біткоїн — найбільша частка: у 2025–2026 він падав менше, ніж ефір і солана.",
                    "el": "Το Bitcoin έχει το μεγαλύτερο μερίδιο: το 2025–2026 έπεσε λιγότερο από το Ether και το Solana.",
                    "es": "Bitcoin tiene la parte más grande: en 2025–2026 cayó menos que Ether y Solana."},
    "pub.why.alt": {"ru": "{sym} прошла отбор системы: оценка проекта {rating} из 100, оценка консультанта {cons} из 100.",
                    "en": "{sym} passed the system's selection: questionnaire {rating} of 100, consultant {cons} of 100.",
                    "uk": "{sym} пройшла відбір системи: анкета {rating} зі 100, консультант {cons} зі 100.",
                    "el": "Το {sym} πέρασε την επιλογή του συστήματος: ερωτηματολόγιο {rating} στα 100, σύμβουλος {cons} στα 100.",
                    "es": "{sym} pasó la selección del sistema: cuestionario {rating} de 100, consultor {cons} de 100."},
    "pub.why.alt0": {"ru": "{sym} прошла отбор системы по таблице «Что брать на спот».",
                     "en": "{sym} passed the system's selection in the «What to buy on spot» table.",
                     "uk": "{sym} пройшла відбір системи за таблицею «Що брати на спот».",
                     "el": "Το {sym} πέρασε την επιλογή του συστήματος στον πίνακα «Τι αγοράζουμε στο spot».",
                     "es": "{sym} pasó la selección del sistema en la tabla «Qué comprar en spot»."},
    "pub.why.trend": {"ru": "Тренд монеты — вверх.", "en": "The coin's trend is up.", "uk": "Тренд монети — вгору.",
                      "el": "Η τάση του νομίσματος είναι ανοδική.", "es": "La tendencia de la moneda es al alza."},
    "pub.why.bull": {"ru": "Биткоин в росте.", "en": "Bitcoin is in an uptrend.", "uk": "Біткоїн у зростанні.",
                     "el": "Το Bitcoin είναι σε άνοδο.", "es": "Bitcoin está en subida."},
    "pub.why.alt_rules": {"ru": "Доля по плану — {plan}%, стоп −20% от цены покупки: если цена упадёт на 20%, система продаст.",
                          "en": "The planned share is {plan}%, with a stop at −20% from the buy price: if the price falls 20%, "
                                "the system sells.",
                          "uk": "Частка за планом — {plan}%, стоп −20% від ціни купівлі: якщо ціна впаде на 20%, система продасть.",
                          "el": "Το μερίδιο του σχεδίου είναι {plan}%, με stop στο −20% από την τιμή αγοράς: αν η τιμή πέσει "
                                "20%, το σύστημα πουλά.",
                          "es": "La parte prevista es el {plan}%, con stop en −20% del precio de compra: si el precio cae un 20%, "
                                "el sistema vende."},
    "pub.why.small": {"ru": "{sym} — небольшая доля с высоким риском: монета на {dd}% ниже своего максимума за 30 дней и прошла "
                            "проверки системы.",
                      "en": "{sym} is a small, high-risk share: the coin is {dd}% below its 30-day high and passed the system's "
                            "checks.",
                      "uk": "{sym} — невелика частка з високим ризиком: монета на {dd}% нижче свого максимуму за 30 днів і пройшла "
                            "перевірки системи.",
                      "el": "Το {sym} είναι ένα μικρό μερίδιο υψηλού ρίσκου: το νόμισμα είναι {dd}% κάτω από το υψηλό 30 ημερών "
                            "και πέρασε τους ελέγχους του συστήματος.",
                      "es": "{sym} es una parte pequeña y de alto riesgo: la moneda está un {dd}% por debajo de su máximo de 30 "
                            "días y pasó los controles del sistema."},
    "pub.why.small0": {"ru": "{sym} — небольшая доля с высоким риском: монета прошла проверки системы.",
                       "en": "{sym} is a small, high-risk share: the coin passed the system's checks.",
                       "uk": "{sym} — невелика частка з високим ризиком: монета пройшла перевірки системи.",
                       "el": "Το {sym} είναι ένα μικρό μερίδιο υψηλού ρίσκου: το νόμισμα πέρασε τους ελέγχους του συστήματος.",
                       "es": "{sym} es una parte pequeña y de alto riesgo: la moneda pasó los controles del sistema."},
    "pub.why.small_rules": {"ru": "Доля — {plan}%, стоп −35%; если цена вырастет вдвое, система продаст половину.",
                            "en": "The share is {plan}%, with a stop at −35%; if the price doubles, the system sells half.",
                            "uk": "Частка — {plan}%, стоп −35%; якщо ціна зросте вдвічі, система продасть половину.",
                            "el": "Το μερίδιο είναι {plan}%, με stop στο −35%· αν η τιμή διπλασιαστεί, το σύστημα πουλά τη μισή "
                                  "θέση.",
                            "es": "La parte es el {plan}%, con stop en −35%; si el precio se duplica, el sistema vende la mitad."},
    "pub.why.wait": {"ru": "Сейчас система новых покупок не делает: монета пока не проходит все проверки, позицию держим по правилам.",
                     "en": "Right now the table says «wait»: no new buys, the position is held by the rules.",
                     "uk": "Зараз таблиця каже «чекаємо»: нових купівель немає, позицію тримаємо за правилами.",
                     "el": "Τώρα ο πίνακας λέει «περιμένουμε»: καμία νέα αγορά, η θέση κρατιέται με τους κανόνες.",
                     "es": "Ahora la tabla dice «esperamos»: no hay compras nuevas, la posición se mantiene según las reglas."},
    "pub.why.avoid": {"ru": "Монета больше не проходит проверки системы: система продаст её при следующем пересчёте.",
                      "en": "The table marked it «don't buy»: the system sells the coin on its next run.",
                      "uk": "Таблиця поставила «не брати»: система продасть монету на наступному прогоні.",
                      "el": "Ο πίνακας το σημείωσε «δεν αγοράζουμε»: το σύστημα πουλά το νόμισμα στον επόμενο κύκλο.",
                      "es": "La tabla la marcó «no comprar»: el sistema vende la moneda en su próxima pasada."},
    "pub.why.cash": {"ru": "Не меньше 10% портфеля система всегда держит в долларах: из них она докупает ядро на просадках.",
                     "en": "The system always keeps at least 10% of the portfolio in dollars: that is what it uses to top up "
                           "the core in dips.",
                     "uk": "Щонайменше 10% портфеля система завжди тримає в доларах: з них вона докуповує ядро на просіданнях.",
                     "el": "Το σύστημα κρατά πάντα τουλάχιστον το 10% του χαρτοφυλακίου σε δολάρια: με αυτά συμπληρώνει τον "
                           "πυρήνα στις πτώσεις.",
                     "es": "El sistema siempre guarda al menos el 10% de la cartera en dólares: con ellos refuerza el núcleo en "
                           "las caídas."},
    "pub.why.spot": {"ru": "Спот — портфель CRYPTO NOVA, без плеча. Ядро — биткоин 30%, эфир 10%, солана 5% — держим долго; монеты "
                           "которые проходят проверки системы, — по 7,5% со стопом −20%; рискованные — по 2,5%; не меньше 10% — "
                           "в долларах. Всего не больше 8 монет вместе с ядром. Ещё 10% счёта — дополнительное ядро: BTC, ETH и SOL в тех же "
                           "пропорциях 30 / 10 / 5.",
                     "en": "Spot is the CRYPTO NOVA portfolio, with no leverage. The core — Bitcoin 30%, Ether 10%, Solana 5% — is "
                           "held for the long run; coins the table marks «can buy» get 7.5% each with a −20% stop; risky ones "
                           "2.5% each; at least 10% stays in dollars. At most 8 coins including the core. Another 10% of the account is extra "
                           "core: BTC, ETH and SOL in the same 30 / 10 / 5 proportions.",
                     "uk": "Спот — портфель CRYPTO NOVA, без плеча. Ядро — біткоїн 30%, ефір 10%, солана 5% — тримаємо довго; "
                           "монети з позначкою таблиці «можна брати» — по 7,5% зі стопом −20%; ризикові — по 2,5%; щонайменше "
                           "10% — у доларах. Усього не більше 8 монет разом з ядром. Ще 10% рахунку — додаткове ядро: BTC, ETH і SOL у тих "
                           "самих пропорціях 30 / 10 / 5.",
                     "el": "Το spot είναι το χαρτοφυλάκιο του CRYPTO NOVA, χωρίς μόχλευση. Ο πυρήνας — Bitcoin 30%, Ether 10%, "
                           "Solana 5% — κρατιέται μακροπρόθεσμα· τα νομίσματα που ο πίνακας σημειώνει «μπορούμε να αγοράσουμε» "
                           "παίρνουν 7,5% το καθένα με stop −20%· τα ριψοκίνδυνα 2,5%· τουλάχιστον 10% μένει σε δολάρια. Έως "
                           "8 νομίσματα μαζί με τον πυρήνα. Ακόμη 10% του λογαριασμού είναι επιπλέον πυρήνας: BTC, ETH και SOL στις ίδιες "
                           "αναλογίες 30 / 10 / 5.",
                     "es": "El spot es la cartera de CRYPTO NOVA, sin apalancamiento. El núcleo —Bitcoin 30%, Ether 10%, Solana "
                           "5%— se mantiene a largo plazo; las monedas que la tabla marca «se puede comprar» llevan un 7,5% cada "
                           "una con stop en −20%; las arriesgadas, un 2,5%; al menos un 10% queda en dólares. Como máximo 8 "
                           "monedas con el núcleo. Otro 10% de la cuenta es núcleo adicional: BTC, ETH y SOL en las mismas "
                           "proporciones 30 / 10 / 5."},
    "pub.why.futures": {"ru": "Фьючерсы — отобранные сигналы CRYPTO NOVA: в счёт идут только стратегии, прошедшие проверку на "
                              "истории. У каждой сделки есть стоп; риск — {risk}% счёта на сделку, у основной стратегии не больше "
                              "{max_open} сделок одновременно. Минус тоже бывает: "
                              "убыточную сделку закрывает стоп.",
                        "en": "Futures are CRYPTO NOVA's selected signals: only strategies that passed the test on history trade "
                              "this account. Every trade has a stop; the risk is {risk}% of the account per trade, and the main "
                              "strategy holds no more than {max_open} trades at once. Losses happen too: the stop closes a losing trade.",
                        "uk": "Ф’ючерси — відібрані сигнали CRYPTO NOVA: у рахунок ідуть лише стратегії, що пройшли перевірку на "
                              "історії. У кожної угоди є стоп; ризик — {risk}% рахунку на угоду, в основної стратегії не більше "
                              "{max_open} угод одночасно. Мінус теж буває: збиткову "
                              "угоду закриває стоп.",
                        "el": "Τα futures είναι τα επιλεγμένα σήματα του CRYPTO NOVA: στον λογαριασμό μπαίνουν μόνο στρατηγικές "
                              "που πέρασαν τον έλεγχο στο ιστορικό. Κάθε συναλλαγή έχει stop· το ρίσκο είναι {risk}% του "
                              "λογαριασμού ανά συναλλαγή και η κύρια στρατηγική κρατά έως {max_open} συναλλαγές ταυτόχρονα. "
                              "Συμβαίνουν και απώλειες: τη "
                              "ζημιογόνα συναλλαγή την κλείνει το stop.",
                        "es": "Los futuros son las señales seleccionadas de CRYPTO NOVA: en la cuenta solo operan estrategias que "
                              "pasaron la prueba con el histórico. Cada operación tiene stop; el riesgo es el {risk}% de la cuenta "
                              "por operación, y la estrategia principal no tiene más de {max_open} operaciones a la vez. "
                              "También hay pérdidas: el "
                              "stop cierra la operación perdedora."},
    "pub.why.active": {"ru": "Активный спот — «Импульс BTC»: когда биткоин за час вырос на 2,5% и больше, система покупает на споте "
                             "самые ликвидные альткоины — они обычно догоняют. Без плеча, до 5 позиций, выход через 12 часов. Между сделками эта "
                             "часть ждёт в BTC; после выхода — до 3 дней в долларах, пока не придёт новый сигнал.",
                       "en": "Active spot is «BTC momentum»: when Bitcoin rises 2.5% or more in an hour, the system buys the most "
                             "liquid altcoins on spot — they usually catch up. No leverage, up to 5 positions, exit after 12 hours. Between "
                             "trades this part waits in BTC; after an exit it stays in dollars for up to 3 days, until a new signal.",
                       "uk": "Активний спот — «Імпульс BTC»: коли біткоїн за годину зріс на 2,5% і більше, система купує на "
                             "споті найліквідніші альткоїни — вони зазвичай доганяють. Без плеча, до 5 позицій, вихід через 12 "
                             "годин. Між угодами ця частина чекає в BTC; після виходу — до 3 днів у доларах, поки не прийде новий сигнал.",
                       "el": "Το ενεργό spot είναι η «ώθηση BTC»: όταν το Bitcoin ανεβαίνει 2,5% ή περισσότερο μέσα σε μία ώρα, "
                             "το σύστημα αγοράζει στο spot τα πιο ρευστά altcoins — συνήθως ακολουθούν. Χωρίς μόχλευση, έως 5 "
                             "θέσεις, έξοδος σε 12 ώρες. Ανάμεσα στις συναλλαγές το μερίδιο περιμένει σε BTC· μετά την έξοδο μένει σε δολάρια "
                             "έως 3 ημέρες, μέχρι να έρθει νέο σήμα.",
                       "es": "El spot activo es el «impulso BTC»: cuando Bitcoin sube un 2,5% o más en una hora, el sistema compra "
                             "en spot las altcoins más líquidas, que suelen ponerse al día. Sin apalancamiento, hasta 5 "
                             "posiciones, salida a las 12 horas. Entre operaciones esta parte espera en BTC; tras una salida queda en dólares "
                             "hasta 3 días, hasta que llegue una nueva señal."},

    # ---------------------------------------------------------------- build 46 (28.10): app_home "public_portfolio" — the PUBLIC mode
    # = the real CRYPTO NOVA account on OKX since 22.10 (web/public_real.json), its allocation, spot / futures parts and real actions
    "pp.title": {"ru": "Публичный портфель CRYPTO NOVA", "en": "CRYPTO NOVA public portfolio", "uk": "Публічний портфель CRYPTO NOVA",
                 "el": "Δημόσιο χαρτοφυλάκιο CRYPTO NOVA", "es": "Cartera pública de CRYPTO NOVA"},
    "pp.note": {"ru": "Реальный счёт CRYPTO NOVA на OKX. История начинается {d}, стартовый капитал {usd}. Сделки ставит автопилот "
                      "системы. Прошлый результат не гарантирует будущий.",
                "en": "CRYPTO NOVA's real account on OKX. The history starts on {d} with a starting capital of {usd}. The system's "
                      "autopilot places the trades. Past results don't guarantee future ones.",
                "uk": "Реальний рахунок CRYPTO NOVA на OKX. Історія починається {d}, стартовий капітал {usd}. Угоди відкриває "
                      "автопілот системи. Минулий результат не гарантує майбутнього.",
                "el": "Ο πραγματικός λογαριασμός του CRYPTO NOVA στο OKX. Το ιστορικό ξεκινά στις {d} με αρχικό κεφάλαιο {usd}. "
                      "Τις συναλλαγές τις ανοίγει ο αυτόματος πιλότος του συστήματος. Οι προηγούμενες αποδόσεις δεν εγγυώνται "
                      "τις μελλοντικές.",
                "es": "La cuenta real de CRYPTO NOVA en OKX. El historial empieza el {d} con un capital inicial de {usd}. Las "
                      "operaciones las abre el piloto automático del sistema. Los resultados pasados no garantizan los futuros."},
    "pp.stale": {"ru": "Данные могли устареть: показываем последние подтверждённые цифры счёта.",
                 "en": "The data may be out of date: these are the account's last confirmed numbers.",
                 "uk": "Дані могли застаріти: показуємо останні підтверджені цифри рахунку.",
                 "el": "Τα δεδομένα ίσως δεν είναι ενημερωμένα: δείχνουμε τους τελευταίους επιβεβαιωμένους αριθμούς του λογαριασμού.",
                 "es": "Puede que los datos no estén al día: mostramos las últimas cifras confirmadas de la cuenta."},
    "pp.base.spot": {"ru": "База расчёта — {share}% стартового капитала ({usd}): столько система выделяет на спот.",
                     "en": "Calculated on {share}% of the starting capital ({usd}): the part the system gives to spot.",
                     "uk": "База розрахунку — {share}% стартового капіталу ({usd}): стільки система виділяє на спот.",
                     "el": "Η βάση υπολογισμού είναι το {share}% του αρχικού κεφαλαίου ({usd}): τόσο διαθέτει το σύστημα στο spot.",
                     "es": "La base de cálculo es el {share}% del capital inicial ({usd}): lo que el sistema destina al spot."},
    "pp.base.fut": {"ru": "База расчёта — {share}% стартового капитала ({usd}): столько система выделяет на фьючерсы.",
                    "en": "Calculated on {share}% of the starting capital ({usd}): the part the system gives to futures.",
                    "uk": "База розрахунку — {share}% стартового капіталу ({usd}): стільки система виділяє на ф'ючерси.",
                    "el": "Η βάση υπολογισμού είναι το {share}% του αρχικού κεφαλαίου ({usd}): τόσο διαθέτει το σύστημα στα futures.",
                    "es": "La base de cálculo es el {share}% del capital inicial ({usd}): lo que el sistema destina a futuros."},
    "pp.split_from": {"ru": "Отдельная история спота и фьючерсов ведётся с {d}.",
                      "en": "Separate spot and futures history is kept from {d}.",
                      "uk": "Окрема історія спота і ф'ючерсів ведеться з {d}.",
                      "el": "Ξεχωριστό ιστορικό για spot και futures τηρείται από {d}.",
                      "es": "El historial separado de spot y futuros se lleva desde el {d}."},
    "pp.flows": {"ru": "Пополнения и выводы не считаются результатом: {usd}.", "en": "Deposits and withdrawals don't count as the result: {usd}.",
                 "uk": "Поповнення й виведення не рахуються результатом: {usd}.",
                 "el": "Οι καταθέσεις και οι αναλήψεις δεν μετράνε ως αποτέλεσμα: {usd}.",
                 "es": "Los depósitos y retiros no cuentan como resultado: {usd}."},
    "pub.why.fut_rec": {"ru": "Реальный счёт CRYPTO NOVA с {since}: закрыто фьючерсных сделок — {n}, из них в плюсе {w}.",
                        "en": "CRYPTO NOVA's real account since {since}: {n} futures trades closed, {w} of them in profit.",
                        "uk": "Реальний рахунок CRYPTO NOVA з {since}: закрито ф'ючерсних угод — {n}, з них у плюсі {w}.",
                        "el": "Πραγματικός λογαριασμός CRYPTO NOVA από τις {since}: έκλεισαν {n} συναλλαγές futures, κερδοφόρες οι {w}.",
                        "es": "Cuenta real de CRYPTO NOVA desde el {since}: {n} operaciones de futuros cerradas, {w} de ellas con ganancia."},
    # 28.10 (build 47 §6.4): the per-part note no longer says «перевод» — no money moves between spot and futures on OKX (one USDT
    # balance, acctLv 2); the system re-splits the account's current value (see "pp.moved.why.*" and public_portfolio.moved)
    "pp.moved": {"ru": "Перераспределение внутри одного счёта: {usd}. Это не перевод денег и не прибыль или убыток.",
                 "en": "Reallocation inside one account: {usd}. This is not a money transfer and not a profit or loss.",
                 "uk": "Перерозподіл усередині одного рахунку: {usd}. Це не переказ грошей і не прибуток чи збиток.",
                 "el": "Ανακατανομή μέσα στον ίδιο λογαριασμό: {usd}. Δεν είναι μεταφορά χρημάτων ούτε κέρδος ή ζημία.",
                 "es": "Redistribución dentro de la misma cuenta: {usd}. No es una transferencia de dinero ni una ganancia o pérdida."},
    "coin.live": {"ru": "Сейчас — {w}% спота реального счёта.", "en": "Now: {w}% of the real account's spot.",
                  "uk": "Зараз — {w}% спота реального рахунку.", "el": "Τώρα: {w}% του spot του πραγματικού λογαριασμού.",
                  "es": "Ahora: el {w}% del spot de la cuenta real."},
    "reco.core": {"ru": "{t}: монета ядра — держим без стопа; продадим, только если система перестанет её рекомендовать.",
                  "en": "{t}: a core coin — held with no stop; it is sold only if the system stops recommending it.",
                  "uk": "{t}: монета ядра — тримаємо без стопа; продамо, лише якщо система перестане її рекомендувати.",
                  "el": "{t}: νόμισμα του πυρήνα — το κρατάμε χωρίς stop· πουλιέται μόνο αν το σύστημα πάψει να το προτείνει.",
                  "es": "{t}: moneda del núcleo — se mantiene sin stop; se vende solo si el sistema deja de recomendarla."},
    "reco.stop": {"ru": "{t}: стоп {stop} от покупки; продадим и тогда, когда монета перестанет проходить проверки системы.",
                  "en": "{t}: stop at {stop} from the buy; it is also sold when the coin stops passing the system's checks.",
                  "uk": "{t}: стоп {stop} від купівлі; продамо й тоді, коли монета перестане проходити перевірки системи.",
                  "el": "{t}: stop στο {stop} από την αγορά· πουλιέται και όταν το νόμισμα πάψει να περνά τους ελέγχους του συστήματος.",
                  "es": "{t}: stop en {stop} desde la compra; también se vende cuando la moneda deja de pasar los controles del sistema."},
    "reco.small": {"ru": "{t}: небольшая доля, стоп {stop}; когда цена вырастет вдвое, система заберёт вложенное.",
                   "en": "{t}: a small share, stop at {stop}; when the price doubles, the system takes back what it put in.",
                   "uk": "{t}: невелика частка, стоп {stop}; коли ціна зросте вдвічі, система забере вкладене.",
                   "el": "{t}: μικρό μερίδιο, stop στο {stop}· όταν η τιμή διπλασιαστεί, το σύστημα παίρνει πίσω ό,τι έβαλε.",
                   "es": "{t}: una parte pequeña, stop en {stop}; cuando el precio se duplique, el sistema recupera lo invertido."},
    "pp.g.spot_coins": {"ru": "Монеты спот-портфеля", "en": "Spot portfolio coins", "uk": "Монети спот-портфеля",
                        "el": "Νομίσματα του χαρτοφυλακίου spot", "es": "Monedas de la cartera spot"},
    "pp.g.spot_cash": {"ru": "Доллары спот-портфеля", "en": "Spot portfolio dollars", "uk": "Долари спот-портфеля",
                       "el": "Δολάρια του χαρτοφυλακίου spot", "es": "Dólares de la cartera spot"},
    "pp.g.active_coins": {"ru": "Активный спот: монеты (между сигналами — BTC)", "en": "Active spot: coins (BTC between signals)",
                          "uk": "Активний спот: монети (між сигналами — BTC)", "el": "Ενεργό spot: νομίσματα (BTC ανάμεσα στα σήματα)",
                          "es": "Spot activo: monedas (BTC entre señales)"},
    # 28.10 (owner's decision): «Импульс BTC» waits in BTC between its trades; USDT for 3 days after the desk's exit
    "pp.a.active_park": {"ru": "Куплено {sym} на {usd}: «Импульс BTC» между сигналами держит свою часть в BTC, а не в долларах.",
                         "en": "Bought {sym} for {usd}: between its signals «BTC momentum» keeps its part in BTC, not in dollars.",
                         "uk": "Куплено {sym} на {usd}: «Імпульс BTC» між сигналами тримає свою частину в BTC, а не в доларах.",
                         "el": "Αγοράστηκε {sym} για {usd}: ανάμεσα στα σήματα η «ώθηση BTC» κρατά το μερίδιό της σε BTC, όχι σε δολάρια.",
                         "es": "Comprado {sym} por {usd}: entre señales, el «impulso BTC» mantiene su parte en BTC, no en dólares."},
    "pp.a.active_unpark": {"ru": "Продано {sym} на {usd}: часть «Импульс BTC» перешла в доллары — выход сигнала или покупка по новому сигналу.",
                           "en": "Sold {sym} for {usd}: the «BTC momentum» part moved to dollars — a signal's exit or a buy on a new signal.",
                           "uk": "Продано {sym} на {usd}: частина «Імпульс BTC» перейшла в долари — вихід сигналу або купівля за новим сигналом.",
                           "el": "Πουλήθηκε {sym} για {usd}: το μερίδιο της «ώθησης BTC» πέρασε σε δολάρια — έξοδος σήματος ή αγορά με νέο σήμα.",
                           "es": "Vendido {sym} por {usd}: la parte del «impulso BTC» pasó a dólares — salida de una señal o compra por una nueva."},
    "pp.g.active_wait": {"ru": "Активный спот: ждёт сигнала", "en": "Active spot: waiting for a signal",
                         "uk": "Активний спот: чекає на сигнал", "el": "Ενεργό spot: σε αναμονή σήματος",
                         "es": "Spot activo: esperando una señal"},
    "pp.g.fut_margin": {"ru": "Фьючерсы: в открытых сделках", "en": "Futures: in open trades", "uk": "Ф'ючерси: у відкритих угодах",
                        "el": "Futures: σε ανοιχτές συναλλαγές", "es": "Futuros: en operaciones abiertas"},
    "pp.g.fut_reserve": {"ru": "Фьючерсы: резерв для новых сделок", "en": "Futures: reserve for new trades",
                         "uk": "Ф'ючерси: резерв для нових угод", "el": "Futures: αποθεματικό για νέες συναλλαγές",
                         "es": "Futuros: reserva para nuevas operaciones"},
    "pp.g.other": {"ru": "Прочее (комиссии, округления)", "en": "Other (fees, rounding)", "uk": "Інше (комісії, округлення)",
                   "el": "Λοιπά (προμήθειες, στρογγυλοποιήσεις)", "es": "Otros (comisiones, redondeo)"},
    "pp.c.core": {"ru": "Ядро: BTC, ETH, SOL", "en": "Core: BTC, ETH, SOL", "uk": "Ядро: BTC, ETH, SOL", "el": "Πυρήνας: BTC, ETH, SOL",
                  "es": "Núcleo: BTC, ETH, SOL"},
    "pp.c.alt": {"ru": "Другие монеты", "en": "Other coins", "uk": "Інші монети", "el": "Άλλα νομίσματα", "es": "Otras monedas"},
    "pp.c.cash": {"ru": "Доллары (USDT)", "en": "Dollars (USDT)", "uk": "Долари (USDT)", "el": "Δολάρια (USDT)", "es": "Dólares (USDT)"},
    "pp.side.long": {"ru": "на рост (LONG)", "en": "long (betting on a rise)", "uk": "на зростання (LONG)", "el": "long (για άνοδο)",
                     "es": "long (al alza)"},
    "pp.side.short": {"ru": "на падение (SHORT)", "en": "short (betting on a fall)", "uk": "на падіння (SHORT)", "el": "short (για πτώση)",
                      "es": "short (a la baja)"},
    "pp.a.spot_buy": {"ru": "Куплено {sym} на {usd}: спот-портфель приведён к плану системы.",
                      "en": "Bought {sym} for {usd}: the spot portfolio was brought back to the system's plan.",
                      "uk": "Куплено {sym} на {usd}: спот-портфель приведено до плану системи.",
                      "el": "Αγορά {sym} αξίας {usd}: το χαρτοφυλάκιο spot επανήλθε στο σχέδιο του συστήματος.",
                      "es": "Comprado {sym} por {usd}: la cartera spot volvió al plan del sistema."},
    "pp.a.spot_sell": {"ru": "Продано {sym} на {usd}: спот-портфель приведён к плану системы.",
                       "en": "Sold {sym} for {usd}: the spot portfolio was brought back to the system's plan.",
                       "uk": "Продано {sym} на {usd}: спот-портфель приведено до плану системи.",
                       "el": "Πώληση {sym} αξίας {usd}: το χαρτοφυλάκιο spot επανήλθε στο σχέδιο του συστήματος.",
                       "es": "Vendido {sym} por {usd}: la cartera spot volvió al plan del sistema."},
    "pp.a.active_buy": {"ru": "Куплено {sym} на {usd} по сигналу активного спота.",
                        "en": "Bought {sym} for {usd} on an active spot signal.",
                        "uk": "Куплено {sym} на {usd} за сигналом активного спота.",
                        "el": "Αγορά {sym} αξίας {usd} με σήμα του ενεργού spot.",
                        "es": "Comprado {sym} por {usd} con una señal del spot activo."},
    "pp.a.active_sell": {"ru": "Продано {sym} на {usd}: сигнал активного спота завершён.",
                         "en": "Sold {sym} for {usd}: the active spot signal ended.",
                         "uk": "Продано {sym} на {usd}: сигнал активного спота завершено.",
                         "el": "Πώληση {sym} αξίας {usd}: το σήμα του ενεργού spot έληξε.",
                         "es": "Vendido {sym} por {usd}: la señal del spot activo terminó."},
    "pp.a.fut_open": {"ru": "Открыта фьючерсная сделка {sym} {side} по цене {px}; стоп сразу стоит на бирже.",
                      "en": "Opened a futures trade on {sym}, {side}, at {px}; the stop is on the exchange from the start.",
                      "uk": "Відкрито ф'ючерсну угоду {sym} {side} за ціною {px}; стоп одразу стоїть на біржі.",
                      "el": "Άνοιξε συναλλαγή futures στο {sym}, {side}, στα {px}· το stop είναι στο ανταλλακτήριο από την αρχή.",
                      "es": "Abierta una operación de futuros en {sym}, {side}, a {px}; el stop está en el exchange desde el inicio."},
    "pp.a.fut_open0": {"ru": "Открыта фьючерсная сделка {sym} {side}; стоп сразу стоит на бирже.",
                       "en": "Opened a futures trade on {sym}, {side}; the stop is on the exchange from the start.",
                       "uk": "Відкрито ф'ючерсну угоду {sym} {side}; стоп одразу стоїть на біржі.",
                       "el": "Άνοιξε συναλλαγή futures στο {sym}, {side}· το stop είναι στο ανταλλακτήριο από την αρχή.",
                       "es": "Abierta una operación de futuros en {sym}, {side}; el stop está en el exchange desde el inicio."},
    "pp.a.fut_close": {"ru": "Закрыта фьючерсная сделка {sym} по цене {px}.", "en": "Closed the futures trade on {sym} at {px}.",
                       "uk": "Закрито ф'ючерсну угоду {sym} за ціною {px}.", "el": "Έκλεισε η συναλλαγή futures στο {sym} στα {px}.",
                       "es": "Cerrada la operación de futuros en {sym} a {px}."},
    "pp.a.fut_close0": {"ru": "Закрыта фьючерсная сделка {sym}.", "en": "Closed the futures trade on {sym}.",
                        "uk": "Закрито ф'ючерсну угоду {sym}.", "el": "Έκλεισε η συναλλαγή futures στο {sym}.",
                        "es": "Cerrada la operación de futuros en {sym}."},
    "pp.a.fut_reduce": {"ru": "Частично закрыта фьючерсная сделка {sym}.", "en": "Part of the futures trade on {sym} was closed.",
                        "uk": "Частково закрито ф'ючерсну угоду {sym}.", "el": "Έκλεισε εν μέρει η συναλλαγή futures στο {sym}.",
                        "es": "Cerrada en parte la operación de futuros en {sym}."},
    "pp.a.fut_gone": {"ru": "Сделку {sym} закрыла биржа: сработал стоп или цель.",
                      "en": "The exchange closed the {sym} trade: the stop or the target was hit.",
                      "uk": "Угоду {sym} закрила біржа: спрацював стоп або ціль.",
                      "el": "Το ανταλλακτήριο έκλεισε τη συναλλαγή στο {sym}: ενεργοποιήθηκε το stop ή ο στόχος.",
                      "es": "El exchange cerró la operación de {sym}: saltó el stop o el objetivo."},
    # ---------------------------------------------------------------- build 46 (28.10) §10.4: app_home "market_view" — the market in
    # plain words from the data the app already has (web/markets.json, the real account, the demo desks); never a forecast
    "mv.now": {"ru": "Биткоин сейчас {price}, за сутки {chg}.", "en": "Bitcoin is at {price} now, {chg} over the past day.",
               "uk": "Біткоїн зараз {price}, за добу {chg}.", "el": "Το bitcoin είναι τώρα στα {price}, {chg} στο τελευταίο 24ωρο.",
               "es": "Bitcoin está ahora en {price}, {chg} en el último día."},
    "mv.week": {"ru": "За неделю {chg}: {word}.", "en": "Over the week: {chg}, {word}.", "uk": "За тиждень {chg}: {word}.",
                "el": "Μέσα στην εβδομάδα: {chg}, {word}.", "es": "En la semana: {chg}, {word}."},
    "mv.w.down2": {"ru": "заметное снижение", "en": "a noticeable decline", "uk": "помітне зниження", "el": "αισθητή πτώση",
                   "es": "una bajada notable"},
    "mv.w.down1": {"ru": "небольшое снижение", "en": "a small decline", "uk": "невелике зниження", "el": "μικρή πτώση",
                   "es": "una bajada pequeña"},
    "mv.w.flat": {"ru": "цена почти на месте", "en": "the price has barely moved", "uk": "ціна майже на місці",
                  "el": "η τιμή σχεδόν δεν κουνήθηκε", "es": "el precio apenas se ha movido"},
    "mv.w.up1": {"ru": "небольшой рост", "en": "a small rise", "uk": "невелике зростання", "el": "μικρή άνοδος", "es": "una subida pequeña"},
    "mv.w.up2": {"ru": "заметный рост", "en": "a noticeable rise", "uk": "помітне зростання", "el": "αισθητή άνοδος", "es": "una subida notable"},
    "mv.month": {"ru": "За месяц {chg}.", "en": "Over the month: {chg}.", "uk": "За місяць {chg}.", "el": "Μέσα στον μήνα: {chg}.",
                 "es": "En el mes: {chg}."},
    "mv.regime.up": {"ru": "Биткоин выше своей средней цены за 200 дней — по правилам системы это режим «рост».",
                     "en": "Bitcoin is above its 200-day average price — by the system's rules that's the “rising” mode.",
                     "uk": "Біткоїн вище за свою середню ціну за 200 днів — за правилами системи це режим «зростання».",
                     "el": "Το bitcoin είναι πάνω από τη μέση τιμή του των 200 ημερών — με τους κανόνες του συστήματος αυτό είναι η "
                           "κατάσταση «άνοδος».",
                     "es": "Bitcoin está por encima de su precio medio de 200 días: según las reglas del sistema es el modo «subida»."},
    "mv.regime.down": {"ru": "Биткоин ниже своей средней цены за 200 дней — по правилам системы это режим «спад»: в спот-портфеле "
                             "система покупает только ядро BTC, ETH и SOL.",
                       "en": "Bitcoin is below its 200-day average price — by the system's rules that's the “falling” mode: in the spot "
                             "portfolio the system buys only the core, BTC, ETH and SOL.",
                       "uk": "Біткоїн нижче за свою середню ціну за 200 днів — за правилами системи це режим «спад»: у спот-портфелі "
                             "система купує лише ядро BTC, ETH і SOL.",
                       "el": "Το bitcoin είναι κάτω από τη μέση τιμή του των 200 ημερών — με τους κανόνες του συστήματος αυτό είναι η "
                             "κατάσταση «πτώση»: στο χαρτοφυλάκιο spot το σύστημα αγοράζει μόνο τον πυρήνα, BTC, ETH και SOL.",
                       "es": "Bitcoin está por debajo de su precio medio de 200 días: según las reglas del sistema es el modo «bajada», "
                             "y en la cartera spot el sistema solo compra el núcleo: BTC, ETH y SOL."},
    "mv.diverge.down_in_up": {"ru": "Короткое снижение идёт на фоне режима «рост»: это разные горизонты, одно не отменяет другое — но и "
                                    "разворота не обещает.",
                              "en": "The short-term dip is happening within the “rising” mode: these are different horizons, one doesn't "
                                    "cancel the other — and it doesn't promise a turn either.",
                              "uk": "Коротке зниження відбувається на тлі режиму «зростання»: це різні горизонти, одне не скасовує "
                                    "іншого — але й розвороту не обіцяє.",
                              "el": "Η βραχυπρόθεσμη πτώση γίνεται μέσα στην κατάσταση «άνοδος»: είναι διαφορετικοί ορίζοντες, το ένα "
                                    "δεν ακυρώνει το άλλο — ούτε όμως υπόσχεται αναστροφή.",
                              "es": "La bajada a corto plazo ocurre dentro del modo «subida»: son horizontes distintos, uno no anula al "
                                    "otro, pero tampoco promete un giro."},
    "mv.diverge.up_in_down": {"ru": "Короткий рост идёт на фоне режима «спад»: одно не отменяет другое, и это ещё не смена режима.",
                              "en": "The short-term rise is happening within the “falling” mode: one doesn't cancel the other, and it "
                                    "isn't a change of mode yet.",
                              "uk": "Коротке зростання відбувається на тлі режиму «спад»: одне не скасовує іншого, і це ще не зміна режиму.",
                              "el": "Η βραχυπρόθεσμη άνοδος γίνεται μέσα στην κατάσταση «πτώση»: το ένα δεν ακυρώνει το άλλο, και δεν "
                                    "είναι ακόμη αλλαγή κατάστασης.",
                              "es": "La subida a corto plazo ocurre dentro del modo «bajada»: una no anula a la otra, y todavía no es "
                                    "un cambio de modo."},
    "mv.year": {"ru": "За год {chg}; биткоин на {gap} ниже своего рекорда.",
                "en": "Over the year: {chg}; bitcoin is {gap} below its record.",
                "uk": "За рік {chg}; біткоїн на {gap} нижче за свій рекорд.",
                "el": "Μέσα στη χρονιά: {chg}· το bitcoin είναι {gap} κάτω από το ρεκόρ του.",
                "es": "En el año: {chg}; bitcoin está un {gap} por debajo de su récord."},
    "mv.fng": {"ru": "Индекс страха и жадности — {v} ({label}), неделю назад был {w}.",
               "en": "The fear & greed index is {v} ({label}); a week ago it was {w}.",
               "uk": "Індекс страху й жадібності — {v} ({label}), тиждень тому був {w}.",
               "el": "Ο δείκτης φόβου και απληστίας είναι {v} ({label}), πριν από μια εβδομάδα ήταν {w}.",
               "es": "El índice de miedo y codicia está en {v} ({label}); hace una semana estaba en {w}."},
    "mv.fng0": {"ru": "Индекс страха и жадности — {v} ({label}).", "en": "The fear & greed index is {v} ({label}).",
                "uk": "Індекс страху й жадібності — {v} ({label}).", "el": "Ο δείκτης φόβου και απληστίας είναι {v} ({label}).",
                "es": "El índice de miedo y codicia está en {v} ({label})."},
    "mv.risk.move": {"ru": "Сильное движение за сутки ({chg}): цена сейчас сильно прыгает.",
                     "en": "A big move over the day ({chg}): the price is swinging hard right now.",
                     "uk": "Сильний рух за добу ({chg}): ціна зараз сильно стрибає.",
                     "el": "Μεγάλη κίνηση μέσα στο 24ωρο ({chg}): η τιμή κάνει τώρα μεγάλα σκαμπανεβάσματα.",
                     "es": "Un movimiento fuerte en el día ({chg}): el precio está saltando mucho ahora."},
    "mv.risk.always": {"ru": "Риск есть всегда: у фьючерсных сделок системы стоп стоит на бирже, у альткоинов спот-портфеля — стоп {stop} "
                             "от покупки, а ядро BTC, ETH и SOL держится без стопа.",
                       "en": "There is always risk: the system's futures trades have their stop on the exchange, the spot portfolio's "
                             "altcoins have a stop at {stop} from the buy, and the core — BTC, ETH and SOL — is held without a stop.",
                       "uk": "Ризик є завжди: у ф'ючерсних угод системи стоп стоїть на біржі, в альткоїнів спот-портфеля — стоп {stop} "
                             "від купівлі, а ядро BTC, ETH і SOL тримається без стопа.",
                       "el": "Κίνδυνος υπάρχει πάντα: οι συναλλαγές futures του συστήματος έχουν το stop στο ανταλλακτήριο, τα altcoins "
                             "του χαρτοφυλακίου spot έχουν stop στο {stop} από την αγορά, και ο πυρήνας — BTC, ETH και SOL — κρατιέται "
                             "χωρίς stop.",
                       "es": "Siempre hay riesgo: las operaciones de futuros del sistema tienen su stop en el exchange, las altcoins de "
                             "la cartera spot tienen un stop del {stop} desde la compra y el núcleo —BTC, ETH y SOL— se mantiene sin stop."},
    "mv.risk.fall": {"ru": "Открытых фьючерсных сделок на реальном счёте: {n}; у каждой стоит стоп, сейчас по ним {usd}.",
                     "en": "Open futures trades on the real account: {n}; each has its stop, and right now they stand at {usd}.",
                     "uk": "Відкритих ф'ючерсних угод на реальному рахунку: {n}; у кожної стоїть стоп, зараз по них {usd}.",
                     "el": "Ανοιχτές συναλλαγές futures στον πραγματικό λογαριασμό: {n}· η καθεμία έχει stop, και τώρα βρίσκονται "
                           "στα {usd}.",
                     "es": "Operaciones de futuros abiertas en la cuenta real: {n}; cada una tiene su stop y ahora están en {usd}."},
    "mv.sys.real_open": {"ru": "Реальный счёт CRYPTO NOVA: открыто фьючерсных сделок — {n}, у каждой стоп на бирже.",
                         "en": "CRYPTO NOVA's real account: {n} open futures trades, each with its stop on the exchange.",
                         "uk": "Реальний рахунок CRYPTO NOVA: відкрито ф'ючерсних угод — {n}, у кожної стоп на біржі.",
                         "el": "Πραγματικός λογαριασμός CRYPTO NOVA: ανοιχτές συναλλαγές futures — {n}, η καθεμία με stop στο "
                               "ανταλλακτήριο.",
                         "es": "Cuenta real de CRYPTO NOVA: {n} operaciones de futuros abiertas, cada una con su stop en el exchange."},
    "mv.sys.real_none": {"ru": "Реальный счёт CRYPTO NOVA: открытых фьючерсных сделок сейчас нет — система открывает их только по своим "
                               "сигналам.",
                         "en": "CRYPTO NOVA's real account: no open futures trades right now — the system opens them only on its own "
                               "signals.",
                         "uk": "Реальний рахунок CRYPTO NOVA: відкритих ф'ючерсних угод зараз немає — система відкриває їх лише за "
                               "своїми сигналами.",
                         "el": "Πραγματικός λογαριασμός CRYPTO NOVA: δεν υπάρχουν ανοιχτές συναλλαγές futures αυτή τη στιγμή — το "
                               "σύστημα τις ανοίγει μόνο με τα δικά του σήματα.",
                         "es": "Cuenta real de CRYPTO NOVA: ahora no hay operaciones de futuros abiertas; el sistema solo las abre con "
                               "sus propias señales."},
    "mv.sys.last": {"ru": "Последнее действие ({d}): {text}", "en": "Last action ({d}): {text}", "uk": "Остання дія ({d}): {text}",
                    "el": "Τελευταία ενέργεια ({d}): {text}", "es": "Última acción ({d}): {text}"},
    "mv.sys.demo_open": {"ru": "Демо-счета системы: в работе сделок — {n}.", "en": "The system's demo accounts: {n} trades open.",
                         "uk": "Демо-рахунки системи: в роботі угод — {n}.",
                         "el": "Οι λογαριασμοί demo του συστήματος: ανοιχτές συναλλαγές — {n}.",
                         "es": "Cuentas demo del sistema: {n} operaciones abiertas."},
    "mv.limits": {"ru": "Это описание того, что уже произошло с ценой, а не прогноз: куда пойдёт рынок, никто не знает заранее.",
                  "en": "This describes what has already happened to the price, not a forecast: nobody knows in advance where the "
                        "market will go.",
                  "uk": "Це опис того, що вже сталося з ціною, а не прогноз: куди піде ринок, ніхто не знає заздалегідь.",
                  "el": "Αυτή είναι περιγραφή όσων έχουν ήδη συμβεί στην τιμή, όχι πρόβλεψη: κανείς δεν ξέρει από πριν πού θα πάει "
                        "η αγορά.",
                  "es": "Esto describe lo que ya ha pasado con el precio, no es un pronóstico: nadie sabe de antemano adónde irá el "
                        "mercado."},
    # ---------------------------------------------------------------- build 46 (28.10) §12.4: the spot portfolio's actions in plain
    # words (app_home spot.actions) — «исключение владельца», «хайп из списка доверия», «анкета ниже порога» said simply
    "act2.sell_quality": {"ru": "Продано {sym}: монета перестала проходить проверку качества системы (оценка {r} из 100 при пороге {p}), "
                                "позиция была {pct} от входа — деньги освобождены для других монет.",
                          "en": "Sold {sym}: the coin stopped passing the system's quality check (a score of {r} out of 100, the bar is "
                                "{p}); the position was {pct} from the entry — the money is freed for other coins.",
                          "uk": "Продано {sym}: монета перестала проходити перевірку якості системи (оцінка {r} зі 100 при порозі {p}), "
                                "позиція була {pct} від входу — гроші звільнено для інших монет.",
                          "el": "Πουλήθηκε το {sym}: το νόμισμα δεν περνά πια τον έλεγχο ποιότητας του συστήματος (βαθμός {r} στα 100, "
                                "το όριο είναι {p}), η θέση ήταν {pct} από την είσοδο — τα χρήματα ελευθερώθηκαν για άλλα νομίσματα.",
                          "es": "Vendido {sym}: la moneda dejó de pasar el control de calidad del sistema (nota de {r} sobre 100, el "
                                "mínimo es {p}); la posición estaba en {pct} desde la entrada y el dinero queda libre para otras monedas."},
    "act2.sell_stop": {"ru": "Продано {sym}: цена подошла к стопу, и система перестала рекомендовать монету. Итог {pct} от входа.",
                       "en": "Sold {sym}: the price came close to the stop and the system stopped recommending the coin. The result "
                             "is {pct} from the entry.",
                       "uk": "Продано {sym}: ціна підійшла до стопа, і система перестала рекомендувати монету. Підсумок {pct} від входу.",
                       "el": "Πουλήθηκε το {sym}: η τιμή πλησίασε το stop και το σύστημα σταμάτησε να προτείνει το νόμισμα. "
                             "Αποτέλεσμα {pct} από την είσοδο.",
                       "es": "Vendido {sym}: el precio se acercó al stop y el sistema dejó de recomendar la moneda. Resultado: {pct} "
                             "desde la entrada."},
    "act2.sell_okx": {"ru": "Продано {sym}: этой монеты нет на споте OKX, а портфель повторяет реальный счёт CRYPTO NOVA на OKX. Итог {pct} "
                            "от входа.",
                      "en": "Sold {sym}: this coin isn't on OKX spot, and the portfolio is repeated by CRYPTO NOVA's real account on OKX. "
                            "The result is {pct} from the entry.",
                      "uk": "Продано {sym}: цієї монети немає на споті OKX, а портфель повторює реальний рахунок CRYPTO NOVA на OKX. "
                            "Підсумок {pct} від входу.",
                      "el": "Πουλήθηκε το {sym}: αυτό το νόμισμα δεν υπάρχει στο spot του OKX, και το χαρτοφυλάκιο το επαναλαμβάνει ο "
                            "πραγματικός λογαριασμός του CRYPTO NOVA στο OKX. Αποτέλεσμα {pct} από την είσοδο.",
                      "es": "Vendido {sym}: esta moneda no está en el spot de OKX, y la cartera la repite la cuenta real de CRYPTO NOVA "
                            "en OKX. Resultado: {pct} desde la entrada."},
    "act2.buy_owner": {"ru": "Куплено {sym} на небольшую долю ({w}% портфеля) по решению владельца: монета в его списке доверия, хотя "
                             "оценка системы пока ниже порога. Стоп {stop}.",
                       "en": "Bought {sym} for a small share ({w}% of the portfolio) by the owner's decision: the coin is on his trusted "
                             "list, although the system's score is still below the bar. Stop at {stop}.",
                       "uk": "Куплено {sym} на невелику частку ({w}% портфеля) за рішенням власника: монета в його списку довіри, хоча "
                             "оцінка системи поки нижча за поріг. Стоп {stop}.",
                       "el": "Αγορά {sym} σε μικρό μερίδιο ({w}% του χαρτοφυλακίου) με απόφαση του ιδιοκτήτη: το νόμισμα είναι "
                             "στη λίστα εμπιστοσύνης του, αν και ο βαθμός του συστήματος είναι ακόμη κάτω από το όριο. Stop στο {stop}.",
                       "es": "Comprado {sym} con una parte pequeña ({w}% de la cartera) por decisión del propietario: la moneda está en "
                             "su lista de confianza, aunque la nota del sistema aún no llega al mínimo. Stop en {stop}."},
    "act2.add_swap": {"ru": "Докуплено {sym} (+{w}% портфеля): деньги проданных монет переложены в ядро портфеля.",
                      "en": "Topped up {sym} (+{w}% of the portfolio): the money from the coins sold went into the portfolio's core.",
                      "uk": "Докуплено {sym} (+{w}% портфеля): гроші проданих монет перекладено в ядро портфеля.",
                      "el": "Συμπληρώθηκε το {sym} (+{w}% του χαρτοφυλακίου): τα χρήματα από τα νομίσματα που πουλήθηκαν πήγαν στον "
                            "πυρήνα του χαρτοφυλακίου.",
                      "es": "Reforzado {sym} (+{w}% de la cartera): el dinero de las monedas vendidas pasó al núcleo de la cartera."},
    # ---------------------------------------------------------------- build 46 (28.10) #6: app_home "report" — NOVA's spoken report on
    # the PUBLIC portfolio, written for the ear (amounts in words, no $), read by the server's male voice (cnq/api/tts.py)
    "rep.value": {"ru": "Публичный портфель CRYPTO NOVA сейчас — {v}.", "en": "The CRYPTO NOVA public portfolio is now at {v}.",
                  "uk": "Публічний портфель CRYPTO NOVA зараз — {v}.",
                  "el": "Το δημόσιο χαρτοφυλάκιο του CRYPTO NOVA είναι τώρα στα {v}.",
                  "es": "La cartera pública de CRYPTO NOVA está ahora en {v}."},
    "rep.today": {"ru": "За сегодня {chg}, это {pct}.", "en": "Today: {chg}, that's {pct}.", "uk": "За сьогодні {chg}, це {pct}.",
                  "el": "Σήμερα: {chg}, δηλαδή {pct}.", "es": "Hoy: {chg}, es decir, {pct}."},
    "rep.since": {"ru": "С {d} — {chg}, это {pct}.", "en": "Since {d}: {chg}, that's {pct}.", "uk": "З {d} — {chg}, це {pct}.",
                  "el": "Από τις {d}: {chg}, δηλαδή {pct}.", "es": "Desde el {d}: {chg}, es decir, {pct}."},
    "rep.fut.none": {"ru": "Открытых фьючерсных сделок сейчас нет.", "en": "There are no open futures trades right now.",
                     "uk": "Відкритих ф'ючерсних угод зараз немає.", "el": "Δεν υπάρχουν ανοιχτές συναλλαγές futures αυτή τη στιγμή.",
                     "es": "Ahora no hay operaciones de futuros abiertas."},
    "rep.fut.open": {"ru": "Открытых фьючерсных сделок: {n}, у каждой стоп на бирже.",
                     "en": "Open futures trades: {n}, each with its stop on the exchange.",
                     "uk": "Відкритих ф'ючерсних угод: {n}, у кожної стоп на біржі.",
                     "el": "Ανοιχτές συναλλαγές futures: {n}, η καθεμία με stop στο ανταλλακτήριο.",
                     "es": "Operaciones de futuros abiertas: {n}, cada una con su stop en el exchange."},
    "rep.last": {"ru": "Последнее действие системы, {d}: {what} {sym}.", "en": "The system's last action, {d}: {what} {sym}.",
                 "uk": "Остання дія системи, {d}: {what} {sym}.", "el": "Τελευταία ενέργεια του συστήματος, {d}: {what} {sym}.",
                 "es": "Última acción del sistema, el {d}: {what} {sym}."},
    "rep.w.buy": {"ru": "покупка", "en": "a buy of", "uk": "купівля", "el": "αγορά", "es": "compra de"},
    "rep.w.sell": {"ru": "продажа", "en": "a sale of", "uk": "продаж", "el": "πώληση", "es": "venta de"},
    "rep.w.fut_open": {"ru": "открыта сделка по", "en": "a trade opened on", "uk": "відкрито угоду на", "el": "άνοιξε συναλλαγή στο",
                       "es": "se abrió una operación en"},
    "rep.w.fut_close": {"ru": "закрыта сделка по", "en": "a trade closed on", "uk": "закрито угоду на", "el": "έκλεισε συναλλαγή στο",
                        "es": "se cerró una operación en"},
    "rep.w.fut_gone": {"ru": "биржа закрыла по стопу или цели сделку по", "en": "the exchange closed, at the stop or the target, the trade on",
                       "uk": "біржа закрила за стопом або ціллю угоду на", "el": "το ανταλλακτήριο έκλεισε, στο stop ή στον στόχο, τη συναλλαγή στο",
                       "es": "el exchange cerró, en el stop o en el objetivo, la operación en"},
    "rep.btc": {"ru": "Биткоин стоит {v}, за сутки {pct}.", "en": "Bitcoin is at {v}, {pct} over the day.",
                "uk": "Біткоїн коштує {v}, за добу {pct}.", "el": "Το bitcoin είναι στα {v}, {pct} μέσα στο εικοσιτετράωρο.",
                "es": "Bitcoin está en {v}, {pct} en el día."},
    "rep.stale": {"ru": "Данные счёта могли устареть.", "en": "The account's data may be out of date.",
                  "uk": "Дані рахунку могли застаріти.", "el": "Τα δεδομένα του λογαριασμού ίσως δεν είναι ενημερωμένα.",
                  "es": "Puede que los datos de la cuenta no estén al día."},
    "rep.none": {"ru": "Свежих данных публичного портфеля пока нет.", "en": "There is no fresh public portfolio data yet.",
                 "uk": "Свіжих даних публічного портфеля поки немає.", "el": "Δεν υπάρχουν ακόμη νέα δεδομένα του δημόσιου χαρτοφυλακίου.",
                 "es": "Todavía no hay datos recientes de la cartera pública."},
    "rep.plus": {"ru": "плюс", "en": "plus", "uk": "плюс", "el": "συν", "es": "más"},
    "rep.minus": {"ru": "минус", "en": "minus", "uk": "мінус", "el": "μείον", "es": "menos"},
    "rep.pct_frac": {"ru": "процента", "en": "percent", "uk": "відсотка", "el": "τοις εκατό", "es": "por ciento"},

    # ---------------------------------------------------------------- build 47 (28.10): audits → display data (owner spec §6.4, §6.5,
    # §12.1, §13.6, §15). Numbers come from the data; the words only say what the code and the data show.
    # §6.4 public_portfolio.moved — the «перевод между спотом и фьючерсами» is a re-split of one USDT balance, not a transfer / P&L
    "pp.moved.label": {"ru": "Перераспределение между спотом и фьючерсами", "en": "Reallocation between spot and futures",
                       "uk": "Перерозподіл між спотом і ф'ючерсами", "el": "Ανακατανομή μεταξύ spot και futures",
                       "es": "Redistribución entre spot y futuros"},
    "pp.moved.why.f2s": {
        "ru": "Это не перевод денег, не комиссия и не прибыль или убыток. Спот и фьючерсы работают на одном счёте OKX с одними и теми "
              "же USDT. Система делит текущую стоимость счёта, а не стартовую: спот {sp}%, фьючерсы {fu}%, активный спот {ac}%. "
              "Поэтому на фьючерсы сейчас приходится на {usd} меньше, чем их стартовая доля плюс их собственный результат, а на спот — "
              "на столько же больше. Из этой разницы {rebase} — доля фьючерсов в изменении стоимости всего счёта, {drift} — доли "
              "сдвинулись между пересчётами: цены монет меняются постоянно, а система выравнивает доли, только когда отклонение больше "
              "порога.",
        "en": "This is not a money transfer, a fee, or a profit or loss. Spot and futures run on one OKX account with the same USDT. The "
              "system splits the account's current value, not its starting value: spot {sp}%, futures {fu}%, active spot {ac}%. So "
              "futures now get {usd} less than their starting share plus their own result, and spot gets the same amount more. Of this "
              "difference, {rebase} is the futures' share of the change in the whole account's value, and {drift} is how the shares "
              "drifted between recalculations: coin prices move all the time, and the system rebalances only when the gap passes a "
              "threshold.",
        "uk": "Це не переказ грошей, не комісія і не прибуток чи збиток. Спот і ф'ючерси працюють на одному рахунку OKX з тими самими "
              "USDT. Система ділить поточну вартість рахунку, а не стартову: спот {sp}%, ф'ючерси {fu}%, активний спот {ac}%. Тому на "
              "ф'ючерси зараз припадає на {usd} менше, ніж їхня стартова частка плюс їхній власний результат, а на спот — на стільки ж "
              "більше. З цієї різниці {rebase} — частка ф'ючерсів у зміні вартості всього рахунку, {drift} — частки зсунулися між "
              "перерахунками: ціни монет змінюються постійно, а система вирівнює частки, лише коли відхилення більше за поріг.",
        "el": "Δεν είναι μεταφορά χρημάτων, ούτε προμήθεια, ούτε κέρδος ή ζημία. Το spot και τα futures λειτουργούν στον ίδιο "
              "λογαριασμό OKX με τα ίδια USDT. Το σύστημα μοιράζει την τρέχουσα αξία του λογαριασμού, όχι την αρχική: spot {sp}%, "
              "futures {fu}%, ενεργό spot {ac}%. Γι' αυτό στα futures αντιστοιχούν τώρα {usd} λιγότερα από το αρχικό τους μερίδιο συν "
              "το δικό τους αποτέλεσμα, και στο spot τόσα περισσότερα. Από αυτή τη διαφορά, {rebase} είναι το μερίδιο των futures στη "
              "μεταβολή της αξίας όλου του λογαριασμού και {drift} είναι η μετατόπιση των μεριδίων ανάμεσα στους επανυπολογισμούς: οι "
              "τιμές των νομισμάτων αλλάζουν συνεχώς και το σύστημα εξισορροπεί μόνο όταν η απόκλιση ξεπεράσει ένα όριο.",
        "es": "No es una transferencia de dinero, ni una comisión, ni una ganancia o pérdida. El spot y los futuros funcionan en la misma "
              "cuenta de OKX con los mismos USDT. El sistema reparte el valor actual de la cuenta, no el inicial: spot {sp}%, futuros "
              "{fu}%, spot activo {ac}%. Por eso a los futuros ahora les corresponden {usd} menos que su parte inicial más su propio "
              "resultado, y al spot lo mismo de más. De esta diferencia, {rebase} es la parte de los futuros en el cambio de valor de "
              "toda la cuenta y {drift} es cuánto se desplazaron las partes entre recálculos: los precios de las monedas cambian todo "
              "el tiempo y el sistema reequilibra solo cuando la desviación supera un umbral."},
    "pp.moved.why.s2f": {
        "ru": "Это не перевод денег, не комиссия и не прибыль или убыток. Спот и фьючерсы работают на одном счёте OKX с одними и теми "
              "же USDT. Система делит текущую стоимость счёта, а не стартовую: спот {sp}%, фьючерсы {fu}%, активный спот {ac}%. "
              "Поэтому на фьючерсы сейчас приходится на {usd} больше, чем их стартовая доля плюс их собственный результат, а на спот — "
              "на столько же меньше. Из этой разницы {rebase} — доля фьючерсов в изменении стоимости всего счёта, {drift} — доли "
              "сдвинулись между пересчётами: цены монет меняются постоянно, а система выравнивает доли, только когда отклонение больше "
              "порога.",
        "en": "This is not a money transfer, a fee, or a profit or loss. Spot and futures run on one OKX account with the same USDT. The "
              "system splits the account's current value, not its starting value: spot {sp}%, futures {fu}%, active spot {ac}%. So "
              "futures now get {usd} more than their starting share plus their own result, and spot gets the same amount less. Of this "
              "difference, {rebase} is the futures' share of the change in the whole account's value, and {drift} is how the shares "
              "drifted between recalculations: coin prices move all the time, and the system rebalances only when the gap passes a "
              "threshold.",
        "uk": "Це не переказ грошей, не комісія і не прибуток чи збиток. Спот і ф'ючерси працюють на одному рахунку OKX з тими самими "
              "USDT. Система ділить поточну вартість рахунку, а не стартову: спот {sp}%, ф'ючерси {fu}%, активний спот {ac}%. Тому на "
              "ф'ючерси зараз припадає на {usd} більше, ніж їхня стартова частка плюс їхній власний результат, а на спот — на стільки ж "
              "менше. З цієї різниці {rebase} — частка ф'ючерсів у зміні вартості всього рахунку, {drift} — частки зсунулися між "
              "перерахунками: ціни монет змінюються постійно, а система вирівнює частки, лише коли відхилення більше за поріг.",
        "el": "Δεν είναι μεταφορά χρημάτων, ούτε προμήθεια, ούτε κέρδος ή ζημία. Το spot και τα futures λειτουργούν στον ίδιο "
              "λογαριασμό OKX με τα ίδια USDT. Το σύστημα μοιράζει την τρέχουσα αξία του λογαριασμού, όχι την αρχική: spot {sp}%, "
              "futures {fu}%, ενεργό spot {ac}%. Γι' αυτό στα futures αντιστοιχούν τώρα {usd} περισσότερα από το αρχικό τους μερίδιο "
              "συν το δικό τους αποτέλεσμα, και στο spot τόσα λιγότερα. Από αυτή τη διαφορά, {rebase} είναι το μερίδιο των futures "
              "στη μεταβολή της αξίας όλου του λογαριασμού και {drift} είναι η μετατόπιση των μεριδίων ανάμεσα στους επανυπολογισμούς: "
              "οι τιμές των νομισμάτων αλλάζουν συνεχώς και το σύστημα εξισορροπεί μόνο όταν η απόκλιση ξεπεράσει ένα όριο.",
        "es": "No es una transferencia de dinero, ni una comisión, ni una ganancia o pérdida. El spot y los futuros funcionan en la misma "
              "cuenta de OKX con los mismos USDT. El sistema reparte el valor actual de la cuenta, no el inicial: spot {sp}%, futuros "
              "{fu}%, spot activo {ac}%. Por eso a los futuros ahora les corresponden {usd} más que su parte inicial más su propio "
              "resultado, y al spot lo mismo de menos. De esta diferencia, {rebase} es la parte de los futuros en el cambio de valor de "
              "toda la cuenta y {drift} es cuánto se desplazaron las partes entre recálculos: los precios de las monedas cambian todo "
              "el tiempo y el sistema reequilibra solo cuando la desviación supera un umbral."},
    # §6.5 the leverage cap in one short note: the real account (okx_futures caps) and the demo signals (signal_feed.LEV)
    "pp.lev_note": {
        "ru": "Плечо на реальном счёте — {lev}x, предел системы — {max}x. Сумму риска плечо не меняет: размер сделки считается от стопа, "
              "и одна сделка рискует не больше {risk}% фьючерсного бюджета. Большее плечо лишь уменьшило бы залог, но приблизило бы "
              "ликвидацию.",
        "en": "Leverage on the real account is {lev}x; the system's cap is {max}x. Leverage doesn't change the amount at risk: the trade "
              "size comes from the stop, and one trade risks at most {risk}% of the futures budget. Higher leverage would only lower "
              "the margin and bring liquidation closer.",
        "uk": "Плече на реальному рахунку — {lev}x, межа системи — {max}x. Суму ризику плече не змінює: розмір угоди рахується від "
              "стопа, і одна угода ризикує не більше ніж {risk}% ф'ючерсного бюджету. Більше плече лише зменшило б заставу, але "
              "наблизило б ліквідацію.",
        "el": "Η μόχλευση στον πραγματικό λογαριασμό είναι {lev}×, το όριο του συστήματος είναι {max}×. Η μόχλευση δεν αλλάζει το ποσό "
              "σε κίνδυνο: το μέγεθος της συναλλαγής υπολογίζεται από το stop και μία συναλλαγή ρισκάρει το πολύ {risk}% του "
              "προϋπολογισμού για futures. Μεγαλύτερη μόχλευση θα μείωνε μόνο το περιθώριο, αλλά θα έφερνε πιο κοντά τη ρευστοποίηση.",
        "es": "El apalancamiento en la cuenta real es de {lev}x; el límite del sistema es {max}x. El apalancamiento no cambia la cantidad "
              "en riesgo: el tamaño de la operación se calcula desde el stop, y una operación arriesga como máximo el {risk}% del "
              "presupuesto de futuros. Más apalancamiento solo reduciría el margen, pero acercaría la liquidación."},
    "fut.lev_note": {
        "ru": "Плечо в сигналах — от {lo}x до {hi}x: чем дальше стоп, тем меньше плечо. Сумму риска плечо не меняет: её задают размер "
              "сделки и расстояние до стопа.",
        "en": "Leverage in the signals is {lo}x to {hi}x: the farther the stop, the lower the leverage. Leverage doesn't change the "
              "amount at risk: the trade size and the distance to the stop set it.",
        "uk": "Плече в сигналах — від {lo}x до {hi}x: що далі стоп, то менше плече. Суму ризику плече не змінює: її задають розмір угоди "
              "й відстань до стопа.",
        "el": "Η μόχλευση στα σήματα είναι από {lo}× ως {hi}×: όσο πιο μακριά το stop, τόσο μικρότερη η μόχλευση. Το ποσό σε κίνδυνο δεν "
              "αλλάζει με τη μόχλευση: το ορίζουν το μέγεθος της συναλλαγής και η απόσταση ως το stop.",
        "es": "El apalancamiento en las señales va de {lo}x a {hi}x: cuanto más lejos el stop, menor el apalancamiento. El apalancamiento "
              "no cambia la cantidad en riesgo: la fijan el tamaño de la operación y la distancia al stop."},
    # §12.1 «Если покупать сегодня»: each coin's real state in the spot portfolio (web/spot_pipeline.json) — never «рекомендовано»
    "buy.note": {
        "ru": "Система проверила {n} {coins} по графику: цена выше средней за 200 дней, отскоки от поддержки, RSI, оценка проекта и "
              "позиции крупных трейдеров. Это кандидаты по графику, а не покупки: портфель CRYPTO NOVA покупает только монеты, которые "
              "прошли его таблицу и правила. Статус рядом с монетой — что с ней сейчас в портфеле.",
        "en": "The system checked {n} {coins} on the chart: price above its 200-day average, bounces off a support, RSI, the project's "
              "score and the largest traders' positions. These are chart candidates, not buys: the CRYPTO NOVA portfolio buys only "
              "coins that pass its table and rules. The status next to a coin shows where it stands in the portfolio now.",
        "uk": "Система перевірила {n} {coins} за графіком: ціна вище за 200-денну середню, відскоки від підтримки, RSI, оцінка проєкту "
              "й позиції великих трейдерів. Це кандидати за графіком, а не покупки: портфель CRYPTO NOVA купує лише монети, які пройшли "
              "його таблицю й правила. Статус біля монети — що з нею зараз у портфелі.",
        "el": "Το σύστημα έλεγξε {n} {coins} στο γράφημα: τιμή πάνω από τον μέσο όρο 200 ημερών, αναπηδήσεις από στήριξη, RSI, "
              "βαθμολογία του έργου και θέσεις των μεγαλύτερων traders. Είναι υποψήφια από το γράφημα, όχι αγορές: το χαρτοφυλάκιο "
              "CRYPTO NOVA αγοράζει μόνο νομίσματα που περνούν τον πίνακα και τους κανόνες του. Η κατάσταση δίπλα σε κάθε νόμισμα "
              "δείχνει πού βρίσκεται τώρα στο χαρτοφυλάκιο.",
        "es": "El sistema revisó {n} {coins} en el gráfico: precio por encima de su media de 200 días, rebotes en un soporte, RSI, la "
              "puntuación del proyecto y las posiciones de los grandes traders. Son candidatas por el gráfico, no compras: la cartera "
              "CRYPTO NOVA compra solo monedas que pasan su tabla y sus reglas. El estado junto a cada moneda muestra dónde está ahora "
              "en la cartera."},
    "bst.watch": {"ru": "наблюдение", "en": "watching", "uk": "спостереження", "el": "παρακολούθηση", "es": "en observación"},
    "bst.analysis": {"ru": "анализ", "en": "under analysis", "uk": "аналіз", "el": "υπό ανάλυση", "es": "en análisis"},
    "bst.not_met": {"ru": "условия не выполнены", "en": "conditions not met", "uk": "умови не виконано",
                    "el": "οι προϋποθέσεις δεν πληρούνται", "es": "condiciones no cumplidas"},
    "bst.signal": {"ru": "сигнал сформирован", "en": "signal formed", "uk": "сигнал сформовано", "el": "το σήμα σχηματίστηκε",
                   "es": "señal generada"},
    "bst.pending": {"ru": "ожидается исполнение", "en": "awaiting execution", "uk": "очікується виконання", "el": "αναμένεται εκτέλεση",
                    "es": "pendiente de ejecución"},
    "bst.open": {"ru": "позиция открыта", "en": "position open", "uk": "позицію відкрито", "el": "η θέση είναι ανοιχτή",
                 "es": "posición abierta"},
    "bst.n.chart": {"ru": "Только кандидат по графику: таблица спот-портфеля эту монету не рассматривает.",
                    "en": "Only a chart candidate: the spot portfolio's table doesn't consider this coin.",
                    "uk": "Лише кандидат за графіком: таблиця спот-портфеля цю монету не розглядає.",
                    "el": "Μόνο υποψήφιο από το γράφημα: ο πίνακας του χαρτοφυλακίου spot δεν εξετάζει αυτό το νόμισμα.",
                    "es": "Solo una candidata por el gráfico: la tabla de la cartera spot no considera esta moneda."},
    "bst.n.found": {"ru": "Монета в списке таблицы портфеля, но оценки ещё нет.", "en": "The coin is on the portfolio table's list but has no score yet.",
                    "uk": "Монета в списку таблиці портфеля, але оцінки ще немає.",
                    "el": "Το νόμισμα είναι στη λίστα του πίνακα του χαρτοφυλακίου, αλλά δεν έχει ακόμη βαθμολογία.",
                    "es": "La moneda está en la lista de la tabla de la cartera, pero aún no tiene puntuación."},
    "bst.n.table_wait": {"ru": "Таблица портфеля пишет «ждём»: не все проверки пройдены.",
                         "en": "The portfolio's table says “wait”: not all checks are passed.",
                         "uk": "Таблиця портфеля пише «чекаємо»: не всі перевірки пройдено.",
                         "el": "Ο πίνακας του χαρτοφυλακίου λέει «αναμονή»: δεν πέρασαν όλοι οι έλεγχοι.",
                         "es": "La tabla de la cartera dice «esperar»: no pasó todos los controles."},
    "bst.n.table_avoid": {"ru": "Таблица портфеля поставила «не брать».", "en": "The portfolio's table marked it “don't buy”.",
                          "uk": "Таблиця портфеля поставила «не брати».", "el": "Ο πίνακας του χαρτοφυλακίου το σημείωσε «όχι αγορά».",
                          "es": "La tabla de la cartera la marcó como «no comprar»."},
    "bst.n.no_okx": {"ru": "На споте OKX нет пары с этой монетой, а портфель повторяет реальный счёт на OKX.",
                     "en": "OKX has no spot pair for this coin, and the portfolio mirrors the real account on OKX.",
                     "uk": "На споті OKX немає пари з цією монетою, а портфель повторює реальний рахунок на OKX.",
                     "el": "Στο spot του OKX δεν υπάρχει ζεύγος για αυτό το νόμισμα, και το χαρτοφυλάκιο ακολουθεί τον πραγματικό λογαριασμό στο OKX.",
                     "es": "OKX no tiene par spot para esta moneda, y la cartera replica la cuenta real en OKX."},
    "bst.n.cooldown": {"ru": "Монету недавно продали: по правилу портфеля снова купить её можно не раньше {d}.",
                       "en": "The coin was sold recently: by the portfolio's rule it can be bought again no earlier than {d}.",
                       "uk": "Монету нещодавно продали: за правилом портфеля знову купити її можна не раніше {d}.",
                       "el": "Το νόμισμα πουλήθηκε πρόσφατα: με τον κανόνα του χαρτοφυλακίου μπορεί να αγοραστεί ξανά όχι πριν από τις {d}.",
                       "es": "La moneda se vendió hace poco: por la regla de la cartera se puede volver a comprar no antes del {d}."},
    "bst.n.slots": {"ru": "Прошла таблицу, но все места в портфеле для таких монет заняты.",
                    "en": "Passed the table, but all the portfolio's places for such coins are taken.",
                    "uk": "Пройшла таблицю, але всі місця в портфелі для таких монет зайняті.",
                    "el": "Πέρασε τον πίνακα, αλλά όλες οι θέσεις του χαρτοφυλακίου για τέτοια νομίσματα είναι πιασμένες.",
                    "es": "Pasó la tabla, pero todos los huecos de la cartera para este tipo de monedas están ocupados."},
    "bst.n.cash": {"ru": "Прошла таблицу, но покупка опустила бы доллары портфеля ниже минимума {p}%.",
                   "en": "Passed the table, but a buy would take the portfolio's dollars below the {p}% minimum.",
                   "uk": "Пройшла таблицю, але купівля опустила б долари портфеля нижче мінімуму {p}%.",
                   "el": "Πέρασε τον πίνακα, αλλά μια αγορά θα κατέβαζε τα δολάρια του χαρτοφυλακίου κάτω από το ελάχιστο {p}%.",
                   "es": "Pasó la tabla, pero una compra dejaría los dólares de la cartera por debajo del mínimo del {p}%."},
    "bst.n.rule": {"ru": "Прошла таблицу, но правило портфеля пока не даёт купить.",
                   "en": "Passed the table, but a portfolio rule doesn't allow a buy yet.",
                   "uk": "Пройшла таблицю, але правило портфеля поки не дає купити.",
                   "el": "Πέρασε τον πίνακα, αλλά ένας κανόνας του χαρτοφυλακίου δεν επιτρέπει ακόμη αγορά.",
                   "es": "Pasó la tabla, pero una regla de la cartera aún no permite comprar."},
    "bst.n.price": {"ru": "Прошла таблицу и правила портфеля, но живая цена ещё не сверилась: покупка ждёт проверки цены.",
                    "en": "Passed the table and the portfolio's rules, but the live price hasn't been checked yet: the buy waits for it.",
                    "uk": "Пройшла таблицю й правила портфеля, але жива ціна ще не звірилася: купівля чекає перевірки ціни.",
                    "el": "Πέρασε τον πίνακα και τους κανόνες του χαρτοφυλακίου, αλλά η τρέχουσα τιμή δεν επιβεβαιώθηκε ακόμη: η αγορά περιμένει.",
                    "es": "Pasó la tabla y las reglas de la cartera, pero el precio en vivo aún no se verificó: la compra espera."},
    "bst.n.all_filters": {"ru": "Прошла таблицу; портфель её ещё не рассматривал.", "en": "Passed the table; the portfolio hasn't looked at it yet.",
                          "uk": "Пройшла таблицю; портфель її ще не розглядав.",
                          "el": "Πέρασε τον πίνακα· το χαρτοφυλάκιο δεν το έχει εξετάσει ακόμη.",
                          "es": "Pasó la tabla; la cartera aún no la ha revisado."},
    "bst.n.recommended": {"ru": "Прошла таблицу и правила портфеля: портфель покупает её на ближайшем прогоне.",
                          "en": "Passed the table and the portfolio's rules: the portfolio buys it on its next run.",
                          "uk": "Пройшла таблицю й правила портфеля: портфель купує її на найближчому прогоні.",
                          "el": "Πέρασε τον πίνακα και τους κανόνες του χαρτοφυλακίου: το χαρτοφυλάκιο το αγοράζει στον επόμενο κύκλο.",
                          "es": "Pasó la tabla y las reglas de la cartera: la cartera la compra en su próxima ejecución."},
    "bst.n.pending": {"ru": "Портфель CRYPTO NOVA её купил, реальный счёт OKX ещё не повторил покупку.",
                      "en": "The CRYPTO NOVA portfolio bought it; the real OKX account hasn't repeated the buy yet.",
                      "uk": "Портфель CRYPTO NOVA її купив, реальний рахунок OKX ще не повторив купівлю.",
                      "el": "Το χαρτοφυλάκιο CRYPTO NOVA το αγόρασε· ο πραγματικός λογαριασμός OKX δεν έχει επαναλάβει ακόμη την αγορά.",
                      "es": "La cartera CRYPTO NOVA la compró; la cuenta real de OKX aún no repitió la compra."},
    "bst.n.open": {"ru": "Монета в портфеле CRYPTO NOVA и на реальном счёте OKX.", "en": "The coin is in the CRYPTO NOVA portfolio and on the real OKX account.",
                   "uk": "Монета в портфелі CRYPTO NOVA і на реальному рахунку OKX.",
                   "el": "Το νόμισμα είναι στο χαρτοφυλάκιο CRYPTO NOVA και στον πραγματικό λογαριασμό OKX.",
                   "es": "La moneda está en la cartera CRYPTO NOVA y en la cuenta real de OKX."},
    "bst.n.unknown": {"ru": "Статус в портфеле сейчас не проверен: данные портфеля не обновлялись.",
                      "en": "Its status in the portfolio isn't checked right now: the portfolio data hasn't been updated.",
                      "uk": "Статус у портфелі зараз не перевірено: дані портфеля не оновлювалися.",
                      "el": "Η κατάστασή του στο χαρτοφυλάκιο δεν έχει ελεγχθεί τώρα: τα δεδομένα του χαρτοφυλακίου δεν ενημερώθηκαν.",
                      "es": "Su estado en la cartera no está comprobado ahora: los datos de la cartera no se actualizaron."},
    # the table's own recorded reason (web/spot_pipeline.json why_ru, written by the engine in Russian) in plain words, per language
    "bst.n.table_wait_r": {"ru": "Таблица портфеля пишет «ждём»: {why}.", "en": "The portfolio's table says “wait”: {why}.",
                           "uk": "Таблиця портфеля пише «чекаємо»: {why}.", "el": "Ο πίνακας του χαρτοφυλακίου λέει «αναμονή»: {why}.",
                           "es": "La tabla de la cartera dice «esperar»: {why}."},
    "bst.n.table_avoid_r": {"ru": "Таблица портфеля поставила «не брать»: {why}.", "en": "The portfolio's table marked it “don't buy”: {why}.",
                            "uk": "Таблиця портфеля поставила «не брати»: {why}.",
                            "el": "Ο πίνακας του χαρτοφυλακίου το σημείωσε «όχι αγορά»: {why}.",
                            "es": "La tabla de la cartera la marcó como «no comprar»: {why}."},
    "bst.n.found_r": {"ru": "Монета в списке таблицы портфеля, оценки ещё нет: {why}.",
                      "en": "The coin is on the portfolio table's list with no score yet: {why}.",
                      "uk": "Монета в списку таблиці портфеля, оцінки ще немає: {why}.",
                      "el": "Το νόμισμα είναι στη λίστα του πίνακα του χαρτοφυλακίου χωρίς βαθμολογία ακόμη: {why}.",
                      "es": "La moneda está en la lista de la tabla de la cartera, aún sin puntuación: {why}."},
    "bst.r.rating": {"ru": "оценка проекта {r} из 100 ниже порога {p}", "en": "the project's score, {r}/100, is below the {p} threshold",
                     "uk": "оцінка проєкту {r} зі 100 нижча за поріг {p}", "el": "η βαθμολογία του έργου, {r}/100, είναι κάτω από το όριο {p}",
                     "es": "la puntuación del proyecto, {r}/100, está por debajo del umbral de {p}"},
    "bst.r.exchange_token": {"ru": "токены бирж портфель не берёт", "en": "the portfolio doesn't take exchange tokens",
                             "uk": "токени бірж портфель не бере", "el": "το χαρτοφυλάκιο δεν παίρνει token ανταλλακτηρίων",
                             "es": "la cartera no compra tokens de exchanges"},
    "bst.r.dead": {"ru": "монета на {pct}% ниже своего рекорда — такие портфель не берёт",
                   "en": "the coin is {pct}% below its record high — the portfolio doesn't take such coins",
                   "uk": "монета на {pct}% нижче за свій рекорд — такі портфель не бере",
                   "el": "το νόμισμα είναι {pct}% κάτω από το ρεκόρ του — τέτοια το χαρτοφυλάκιο δεν τα παίρνει",
                   "es": "la moneda está un {pct}% por debajo de su máximo histórico; la cartera no compra monedas así"},
    "bst.r.dead_all": {"ru": "монета потеряла почти всю цену от своего рекорда — такие портфель не берёт",
                       "en": "the coin has lost almost all of its value since its record high — the portfolio doesn't take such coins",
                       "uk": "монета втратила майже всю ціну від свого рекорду — такі портфель не бере",
                       "el": "το νόμισμα έχει χάσει σχεδόν όλη την αξία του από το ρεκόρ του — τέτοια το χαρτοφυλάκιο δεν τα παίρνει",
                       "es": "la moneda ha perdido casi todo su valor desde su máximo histórico; la cartera no compra monedas así"},
    "bst.r.turnover": {"ru": "слишком маленький оборот за сутки — цену легко сдвинуть", "en": "daily turnover is too small — the price is easy to move",
                       "uk": "замалий обіг за добу — ціну легко зсунути", "el": "πολύ μικρός ημερήσιος τζίρος — η τιμή μετακινείται εύκολα",
                       "es": "el volumen diario es demasiado bajo: el precio se mueve con facilidad"},
    "bst.r.trend": {"ru": "тренд не подтверждён", "en": "the trend isn't confirmed", "uk": "тренд не підтверджено",
                    "el": "η τάση δεν επιβεβαιώθηκε", "es": "la tendencia no está confirmada"},
    "bst.r.meme_unchecked": {"ru": "мем-монету без проверки мем-деска портфель не берёт",
                             "en": "the portfolio doesn't take a meme coin the meme desk hasn't checked",
                             "uk": "мем-монету без перевірки мем-деску портфель не бере",
                             "el": "το χαρτοφυλάκιο δεν παίρνει meme νόμισμα που δεν έχει ελέγξει το τμήμα meme",
                             "es": "la cartera no compra una moneda meme que la mesa de memes no ha revisado"},
    "bst.r.no_dip": {"ru": "цена не откатилась — портфель ждёт просадку", "en": "the price hasn't pulled back — the portfolio waits for a dip",
                     "uk": "ціна не відкотилася — портфель чекає просідання", "el": "η τιμή δεν έχει υποχωρήσει — το χαρτοφυλάκιο περιμένει πτώση",
                     "es": "el precio no ha retrocedido: la cartera espera una caída"},
    "bst.r.unlocks": {"ru": "впереди крупные разлоки токенов", "en": "large token unlocks are ahead", "uk": "попереду великі розблокування токенів",
                      "el": "έρχονται μεγάλα ξεκλειδώματα token", "es": "se acercan grandes desbloqueos de tokens"},
    "bst.r.red_flag": {"ru": "мем-деск нашёл у токена красный флаг", "en": "the meme desk found a red flag on the token",
                       "uk": "мем-деск знайшов у токена червоний прапорець", "el": "το τμήμα meme βρήκε κόκκινη σημαία στο token",
                       "es": "la mesa de memes encontró una señal de alarma en el token"},
    # §15 the spoken report says plainly when a part of the system is not working (web/system_status.json)
    "rep.degraded": {"ru": "Внимание: сейчас есть перебои — {parts}. Часть цифр может быть не самой свежей.",
                     "en": "Heads-up: there are interruptions right now — {parts}. Some numbers may not be the latest.",
                     "uk": "Увага: зараз є перебої — {parts}. Частина цифр може бути не найсвіжішою.",
                     "el": "Προσοχή: αυτή τη στιγμή υπάρχουν διακοπές — {parts}. Κάποιοι αριθμοί ίσως δεν είναι οι πιο πρόσφατοι.",
                     "es": "Atención: ahora mismo hay interrupciones — {parts}. Algunas cifras pueden no ser las más recientes."},
    "rep.part.account": {"ru": "данные и автопилот реального счёта", "en": "the real account's data and autopilot",
                         "uk": "дані й автопілот реального рахунку", "el": "τα δεδομένα και ο αυτόματος πιλότος του πραγματικού λογαριασμού",
                         "es": "los datos y el piloto automático de la cuenta real"},
    "rep.part.signals": {"ru": "расчёт сигналов", "en": "the signal engine", "uk": "розрахунок сигналів", "el": "ο υπολογισμός των σημάτων",
                         "es": "el cálculo de señales"},
    "rep.part.data": {"ru": "обновление рыночных данных и спот-портфеля", "en": "market data and spot portfolio updates",
                      "uk": "оновлення ринкових даних і спот-портфеля",
                      "el": "η ενημέρωση των δεδομένων της αγοράς και του χαρτοφυλακίου spot",
                      "es": "la actualización de los datos del mercado y de la cartera spot"},
    "rep.part.app": {"ru": "сервер приложения", "en": "the app's server", "uk": "сервер застосунку", "el": "ο διακομιστής της εφαρμογής",
                     "es": "el servidor de la app"},
    "rep.part.check": {"ru": "проверка состояния системы", "en": "the system health check", "uk": "перевірка стану системи",
                       "el": "ο έλεγχος κατάστασης του συστήματος", "es": "la comprobación del estado del sistema"},
    "rep.value_stale": {"ru": "По последним подтверждённым данным на {d} публичный портфель CRYPTO NOVA — {v}.",
                        "en": "By the last confirmed data, as of {d}, the CRYPTO NOVA public portfolio was at {v}.",
                        "uk": "За останніми підтвердженими даними на {d} публічний портфель CRYPTO NOVA — {v}.",
                        "el": "Σύμφωνα με τα τελευταία επιβεβαιωμένα δεδομένα, στις {d}, το δημόσιο χαρτοφυλάκιο CRYPTO NOVA ήταν {v}.",
                        "es": "Según los últimos datos confirmados, del {d}, la cartera pública de CRYPTO NOVA estaba en {v}."},
    # §13.6 the connection states of «Подключиться к системе CRYPTO NOVA» (app_config.connect_states): what each means and the next
    # step. CRYPTO NOVA has no technical link to a user's OKX account, so «confirmed» is never shown today (available: false).
    # The OKX section names are the app guide's own («Копитрейдинг» → «Мои копирования» / My copies).
    "cs.not_started.title": {"ru": "Подключение не начато", "en": "Not started", "uk": "Підключення не розпочато",
                             "el": "Η σύνδεση δεν ξεκίνησε", "es": "Sin empezar"},
    "cs.not_started.text": {"ru": "Вы ещё не начинали подключать свой счёт на бирже.", "en": "You haven't started connecting your exchange account yet.",
                            "uk": "Ви ще не почали підключати свій рахунок на біржі.",
                            "el": "Δεν έχετε ξεκινήσει ακόμη τη σύνδεση του λογαριασμού σας στο ανταλλακτήριο.",
                            "es": "Aún no has empezado a conectar tu cuenta del exchange."},
    "cs.not_started.next": {"ru": "Откройте «Подключиться к системе CRYPTO NOVA» и начните с регистрации на OKX.",
                            "en": "Open “Connect to the CRYPTO NOVA system” and start by signing up on OKX.",
                            "uk": "Відкрийте «Підключитися до системи CRYPTO NOVA» і почніть з реєстрації на OKX.",
                            "el": "Ανοίξτε το «Σύνδεση στο σύστημα CRYPTO NOVA» και ξεκινήστε με την εγγραφή στο OKX.",
                            "es": "Abre «Conectarse al sistema CRYPTO NOVA» y empieza registrándote en OKX."},
    "cs.action_on_exchange.title": {"ru": "Нужен шаг на бирже", "en": "A step on the exchange", "uk": "Потрібен крок на біржі",
                                    "el": "Χρειάζεται ένα βήμα στο ανταλλακτήριο", "es": "Falta un paso en el exchange"},
    "cs.action_on_exchange.text": {
        "ru": "Следующий шаг делается в приложении или на сайте OKX: регистрация, проверка личности, пополнение счёта или включение "
              "копирования CRYPTO NOVA.",
        "en": "The next step happens in the OKX app or website: sign-up, identity check, a deposit, or turning on copying of CRYPTO NOVA.",
        "uk": "Наступний крок робиться в застосунку або на сайті OKX: реєстрація, перевірка особи, поповнення рахунку або ввімкнення "
              "копіювання CRYPTO NOVA.",
        "el": "Το επόμενο βήμα γίνεται στην εφαρμογή ή στον ιστότοπο του OKX: εγγραφή, έλεγχος ταυτότητας, κατάθεση ή ενεργοποίηση "
              "της αντιγραφής του CRYPTO NOVA.",
        "es": "El siguiente paso se hace en la app o la web de OKX: registro, verificación de identidad, depósito o activar la copia "
              "de CRYPTO NOVA."},
    "cs.action_on_exchange.next": {"ru": "Откройте OKX, завершите шаг и вернитесь к инструкции.",
                                   "en": "Open OKX, finish the step and come back to the guide.",
                                   "uk": "Відкрийте OKX, завершіть крок і поверніться до інструкції.",
                                   "el": "Ανοίξτε το OKX, ολοκληρώστε το βήμα και επιστρέψτε στον οδηγό.",
                                   "es": "Abre OKX, termina el paso y vuelve a la guía."},
    "cs.waiting.title": {"ru": "Ожидается подтверждение", "en": "Waiting for confirmation", "uk": "Очікується підтвердження",
                         "el": "Αναμονή επιβεβαίωσης", "es": "Esperando confirmación"},
    "cs.waiting.text": {"ru": "Биржа ещё проверяет ваши данные или зачисляет пополнение. Это делает OKX, а не CRYPTO NOVA.",
                        "en": "The exchange is still checking your details or crediting your deposit. OKX does this, not CRYPTO NOVA.",
                        "uk": "Біржа ще перевіряє ваші дані або зараховує поповнення. Це робить OKX, а не CRYPTO NOVA.",
                        "el": "Το ανταλλακτήριο ακόμη ελέγχει τα στοιχεία σας ή πιστώνει την κατάθεσή σας. Αυτό το κάνει το OKX, όχι το CRYPTO NOVA.",
                        "es": "El exchange aún está revisando tus datos o abonando tu depósito. Lo hace OKX, no CRYPTO NOVA."},
    "cs.waiting.next": {"ru": "Дождитесь уведомления OKX и затем продолжите инструкцию.", "en": "Wait for OKX's notification, then continue the guide.",
                        "uk": "Дочекайтеся сповіщення OKX і потім продовжте інструкцію.",
                        "el": "Περιμένετε την ειδοποίηση του OKX και μετά συνεχίστε τον οδηγό.",
                        "es": "Espera la notificación de OKX y luego continúa la guía."},
    "cs.confirmed.title": {"ru": "Подключение подтверждено", "en": "Connection confirmed", "uk": "Підключення підтверджено",
                           "el": "Η σύνδεση επιβεβαιώθηκε", "es": "Conexión confirmada"},
    "cs.confirmed.text": {"ru": "Биржа подтвердила, что копирование CRYPTO NOVA на вашем счёте активно.",
                          "en": "The exchange confirmed that copying CRYPTO NOVA is active on your account.",
                          "uk": "Біржа підтвердила, що копіювання CRYPTO NOVA на вашому рахунку активне.",
                          "el": "Το ανταλλακτήριο επιβεβαίωσε ότι η αντιγραφή του CRYPTO NOVA είναι ενεργή στον λογαριασμό σας.",
                          "es": "El exchange confirmó que la copia de CRYPTO NOVA está activa en tu cuenta."},
    "cs.confirmed.next": {"ru": "Следите за сделками в приложении. Остановить копирование можно в OKX в любой момент.",
                          "en": "Follow the trades in the app. You can stop copying in OKX at any time.",
                          "uk": "Стежте за угодами в застосунку. Зупинити копіювання можна в OKX у будь-який момент.",
                          "el": "Παρακολουθήστε τις συναλλαγές στην εφαρμογή. Μπορείτε να σταματήσετε την αντιγραφή στο OKX οποιαδήποτε στιγμή.",
                          "es": "Sigue las operaciones en la app. Puedes detener la copia en OKX en cualquier momento."},
    "cs.error.title": {"ru": "Ошибка", "en": "Error", "uk": "Помилка", "el": "Σφάλμα", "es": "Error"},
    "cs.error.text": {"ru": "Шаг не удался: например, биржа отклонила проверку личности или пополнение не дошло.",
                      "en": "A step didn't go through: for example, the exchange rejected the identity check or the deposit didn't arrive.",
                      "uk": "Крок не вдався: наприклад, біржа відхилила перевірку особи або поповнення не надійшло.",
                      "el": "Ένα βήμα δεν ολοκληρώθηκε: για παράδειγμα, το ανταλλακτήριο απέρριψε τον έλεγχο ταυτότητας ή η κατάθεση δεν έφτασε.",
                      "es": "Un paso no salió bien: por ejemplo, el exchange rechazó la verificación de identidad o el depósito no llegó."},
    # owner's decision of 28.10: no statement that copying is banned / unavailable in the EU / EEA / UK — one calm note (an «i»)
    "cs.info": {"ru": "Доступность копитрейдинга и отдельных инструментов зависит от страны и региона — условия показывает OKX.",
                "en": "The availability of copy trading and of some instruments depends on your country and region — OKX shows the terms.",
                "uk": "Доступність копітрейдингу та окремих інструментів залежить від країни й регіону — умови показує OKX.",
                "el": "Η διαθεσιμότητα του copy trading και ορισμένων εργαλείων εξαρτάται από τη χώρα και την περιοχή — τους όρους τους δείχνει το OKX.",
                "es": "La disponibilidad del copy trading y de algunos instrumentos depende del país y la región: OKX muestra las condiciones."},
    "cs.error.next": {"ru": "Посмотрите причину в OKX, исправьте её там и повторите шаг. Если не получается — напишите в поддержку.",
                      "en": "Check the reason in OKX, fix it there and repeat the step. If it doesn't work, contact support.",
                      "uk": "Перегляньте причину в OKX, виправте її там і повторіть крок. Якщо не виходить — напишіть у підтримку.",
                      "el": "Δείτε την αιτία στο OKX, διορθώστε την εκεί και επαναλάβετε το βήμα. Αν δεν γίνεται, γράψτε στην υποστήριξη.",
                      "es": "Revisa el motivo en OKX, corrígelo allí y repite el paso. Si no funciona, escribe a soporte."},
    "cs.unverifiable.title": {"ru": "Проверить из приложения нельзя", "en": "Can't be checked from the app",
                              "uk": "Перевірити із застосунку не можна", "el": "Δεν ελέγχεται από την εφαρμογή",
                              "es": "No se puede comprobar desde la app"},
    "cs.unverifiable.text": {
        "ru": "У CRYPTO NOVA нет технической связи с вашим счётом на OKX, поэтому приложение не видит, включено ли копирование. Это не "
              "ошибка.",
        "en": "CRYPTO NOVA has no technical link to your OKX account, so the app can't see whether copying is on. This is not an error.",
        "uk": "У CRYPTO NOVA немає технічного зв'язку з вашим рахунком на OKX, тому застосунок не бачить, чи ввімкнено копіювання. Це "
              "не помилка.",
        "el": "Το CRYPTO NOVA δεν έχει τεχνική σύνδεση με τον λογαριασμό σας στο OKX, οπότε η εφαρμογή δεν βλέπει αν η αντιγραφή είναι "
              "ενεργή. Δεν είναι σφάλμα.",
        "es": "CRYPTO NOVA no tiene conexión técnica con tu cuenta de OKX, así que la app no ve si la copia está activa. No es un error."},
    "cs.unverifiable.next": {
        "ru": "Проверьте сами в OKX: «Копитрейдинг» → «Мои копирования» (My copies) — там должен быть CRYPTO NOVA со статусом «активно».",
        "en": "Check it yourself in OKX: “Copy trading” → “My copies”: CRYPTO NOVA should be there with the status “active”.",
        "uk": "Перевірте самі в OKX: «Копітрейдинг» → «Мої копіювання» (My copies) — там має бути CRYPTO NOVA зі статусом «активно».",
        "el": "Ελέγξτε το μόνοι σας στο OKX: «Copy trading» → «My copies» — εκεί πρέπει να υπάρχει το CRYPTO NOVA με κατάσταση «ενεργό».",
        "es": "Compruébalo tú en OKX: «Copy trading» → «Mis copias» (My copies): ahí debe aparecer CRYPTO NOVA con el estado «activo»."},
}

# plural words: ru / uk — (1, 2–4, 5+) in the case the sentence needs; en / el / es — (1, other)
P: dict[str, dict[str, tuple[str, ...]]] = {
    # 28.10 (build 46 #6): the spoken report — amounts and whole percents in words
    "times": {"ru": ("раз", "раза", "раз"), "uk": ("раз", "рази", "разів"), "en": ("time", "times"), "el": ("φορά", "φορές"),
              "es": ("vez", "veces")},
    "dollar": {"ru": ("доллар", "доллара", "долларов"), "uk": ("долар", "долари", "доларів"), "en": ("dollar", "dollars"),
               "el": ("δολάριο", "δολάρια"), "es": ("dólar", "dólares")},
    "percent": {"ru": ("процент", "процента", "процентов"), "uk": ("відсоток", "відсотки", "відсотків"), "en": ("percent", "percent"),
                "el": ("τοις εκατό", "τοις εκατό"), "es": ("por ciento", "por ciento")},
    "hour": {"ru": ("час", "часа", "часов"), "uk": ("година", "години", "годин"), "en": ("hour", "hours"), "el": ("ώρα", "ώρες"),
             "es": ("hora", "horas")},
    "coin_acc": {"ru": ("монету", "монеты", "монет"), "uk": ("монету", "монети", "монет"), "en": ("coin", "coins"),
                 "el": ("νόμισμα", "νομίσματα"), "es": ("moneda", "monedas")},
    "signal": {"ru": ("сигнал", "сигнала", "сигналов"), "uk": ("сигнал", "сигнали", "сигналів"), "en": ("signal", "signals"),
               "el": ("σήμα", "σήματα"), "es": ("señal", "señales")},
    "altcoin_acc": {"ru": ("альткоин", "альткоина", "альткоинов"), "uk": ("альткоїн", "альткоїни", "альткоїнів"),
                    "en": ("altcoin", "altcoins"), "el": ("altcoin", "altcoins"), "es": ("altcoin", "altcoins")},
    "rule_by": {"ru": ("правилу", "правилам", "правилам"), "uk": ("правилом", "правилами", "правилами"), "en": ("rule", "rules"),
                "el": ("κανόνα", "κανόνες"), "es": ("regla", "reglas")},
    "futures_by": {"ru": ("фьючерсом", "фьючерсами", "фьючерсами"), "uk": ("ф'ючерсом", "ф'ючерсами", "ф'ючерсами"),
                   "en": ("futures contract", "futures contracts"), "el": ("συμβόλαιο future", "συμβόλαια futures"),
                   "es": ("futuro", "futuros")},
    "trade": {"ru": ("сделка", "сделки", "сделок"), "uk": ("угода", "угоди", "угод"), "en": ("trade", "trades"),
              "el": ("συναλλαγή", "συναλλαγές"), "es": ("operación", "operaciones")},
}


# ------------------------------------------------------------------------------------------------ 28.10 (build 46 QA): text polish
# Every server text the app shows: «CRYPTO NOVA» never wraps (U+00A0), a negative amount has the true minus (U+2212), and in ru / uk /
# el / es a decimal number has a comma and its thousands a non-breaking space (en keeps 2.5 and 10,000). A date (22.10, 06.10.2025),
# a version (2.0), a year range (2023–2026), a ticker pair (BTC-USDT) and a URL are left as they are.
NBSP, MINUS = "\u00a0", "\u2212"
_BRAND = re.compile(r"CRYPTO[ \u00a0]+NOV[AΛ]")
_MINUS = re.compile(r"(?<![\w)\]])-(?=\$?\d)")
_DEC = re.compile(r"(?<![\d.,])(?P<cur>[$€]\s?)?(?P<a>\d+)\.(?P<b>\d+)(?!\d)(?!\.\d)(?P<suf>\s?[%×x])?")
_THOU = re.compile(r"(?<![\d.,])(\d{1,3})((?:[ \u00a0]\d{3})+)(?![\d])")


def _dec_ok(m) -> bool:
    """a decimal number to convert, not a date / a version: with $ or %, or not shaped like dd.mm"""
    a, b = m.group("a"), m.group("b")
    if m.group("cur") or m.group("suf"):
        return True
    if len(b) == 1 and b == "0" and len(a) == 1:          # «2.0» — an app version, a multiplier like ×2.0 has its ×
        return False
    if len(b) == 2 and 1 <= int(a) <= 31 and 1 <= int(b) <= 12:   # «22.10» — a date
        return False
    return True


def polish(text, lang: str = DEFAULT):
    """one text as the app shows it (see above); anything that is not a str, and URLs, come back unchanged"""
    if not isinstance(text, str) or not text or "://" in text:
        return text
    s = _BRAND.sub(lambda m: m.group(0).replace(" ", NBSP).replace(NBSP + NBSP, NBSP), text)
    s = re.sub(r"CRYPTO[ \u00a0]+(NOV[AΛ])", "CRYPTO" + NBSP + r"\1", s)
    s = _MINUS.sub(MINUS, s)
    if lang != "en":
        s = _DEC.sub(lambda m: (m.group("cur") or "") + m.group("a") + "," + m.group("b") + (m.group("suf") or "") if _dec_ok(m) else m.group(0), s)
        s = _THOU.sub(lambda m: m.group(1) + m.group(2).replace(" ", NBSP), s)
    return s


NO_POLISH = {"sym", "inst", "sig", "key", "kind", "lang", "src", "action", "side", "mode", "regime", "grade", "id", "image", "link",
             "url", "audio_url", "copy_url", "status", "rule_id", "rule_source", "art", "screen", "target", "level", "updated",
             "version", "v", "dir"}


def polish_doc(doc, lang: str = DEFAULT, _key: str | None = None):
    """polish() over every text of a JSON document — not over identifiers (NO_POLISH keys) and not over numbers"""
    if isinstance(doc, dict):
        return {k: (v if k in NO_POLISH and isinstance(v, str) else polish_doc(v, lang, k)) for k, v in doc.items()}
    if isinstance(doc, list):
        return [polish_doc(v, lang, _key) for v in doc]
    return polish(doc, lang) if isinstance(doc, str) else doc


def t(lang: str, key: str, **params) -> str:
    row = T[key]
    s = row.get(lang if lang in LANGS else DEFAULT) or row[DEFAULT]
    return s.format(**params) if params else s


def plural(lang: str, n, key: str) -> str:
    row = P[key]
    forms = row.get(lang if lang in LANGS else DEFAULT) or row[DEFAULT]
    n = abs(int(n))
    if len(forms) == 3:
        if n % 10 == 1 and n % 100 != 11:
            return forms[0]
        return forms[1] if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else forms[2]
    return forms[0] if n == 1 else forms[1]


def fields(template: str) -> set[str]:
    """the {placeholders} of a template (the catalog test: the same in every language)"""
    return {f for _lit, f, _spec, _conv in string.Formatter().parse(template) if f}
