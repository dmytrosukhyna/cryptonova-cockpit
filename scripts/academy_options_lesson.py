# -*- coding: utf-8 -*-
"""Lesson «Опционы простыми словами» (l4-options) in 5 languages — the source; `python scripts/academy_options_lesson.py --apply web`
appends it to web/academy*.json after l4-yield (level L4 «Продвинутый») or replaces it when already there. Owner 30.10.2026."""
import json, os, sys

ID = "l4-options"
AFTER = "l4-yield"

L = {}

L["ru"] = {
 "id": ID, "title": "Опционы простыми словами", "minutes": 6,
 "goal": "После урока вы поймёте, что такое пут и колл, почему страховка от падения стоит денег каждый месяц и почему CRYPTO NOVA опционами не торгует.",
 "cards": [
  {"title": "Пут и колл",
   "text": "Опцион — это страховой полис на цену. Пут даёт право продать биткоин по заранее выбранной цене, даже если он упал ниже. Колл — право купить по выбранной цене, даже если он вырос выше. За право платят премию. Если право не пригодилось, премия сгорает целиком.",
   "example": "Как в жизни: страховка на машину. Платите каждый год. Аварии не было — деньги не вернут. Была — страховая платит.",
   "art": "umbrella"},
  {"title": "Почему пут зарабатывает на падении",
   "text": "Купили пут с правом продать BTC по 70 000, когда он стоил 78 000. BTC упал до 55 000. Право продать по 70 000 теперь стоит около 15 000 на 1 BTC: это разница между 70 000 и 55 000. Так и «зарабатывают на падении». Остался BTC выше 70 000 — пут истёк пустым, премия потеряна.",
   "example": "Как в жизни: страховка дома от пожара. Дом сгорел — получили выплату. Не сгорел — просто платили каждый год.",
   "art": "chart_down"},
  {"title": "Премия — цена страховки",
   "text": "Пут на 30 дней с защитой от падения глубже 10% обычно стоит 2–5% от цены BTC. Через месяц страховку надо покупать снова. За год набегает примерно 17–25% от суммы. Чтобы хотя бы выйти в ноль, падение должно быть сильным и вовремя — пока полис действует.",
   "example": "Пример: BTC 78 000, пут с правом продать по 70 000 стоит 2 000. Заработок начнётся только ниже 68 000: 70 000 минус 2 000 премии.",
   "art": "calculator"},
  {"title": "Почему большинство покупателей теряет",
   "text": "Опцион дешевеет каждый день — это распад времени. Страховку продлевают снова и снова, а сильные падения приходят редко. На истории BTC 2023–2026 из 27 купленных путов в деньгах истекли только 5. Все 3 больших падения начались, когда наш индикатор ещё показывал «рост»: пут покупался уже после первых −15…−25%.",
   "example": "Как в жизни: зонт покупают, когда уже промокли. Дождь к этому моменту часто заканчивается.",
   "art": "clock"},
  {"title": "Что говорит история",
   "text": "Проверили на BTC с 2023 по октябрь 2026. Просто держать BTC: +369%, просадка −53%. Наше правило «режим «падение» → USDT»: +218%, просадка −18.5%. Держать и покупать пут при «падении»: +235%, просадка те же −53%, премий 17% в год. Страховка просадку не сократила, а уход в USDT сделал это без премий.",
   "example": "Как в жизни: в Индии опционами торгуют миллионы частных лиц. Регулятор насчитал 89–93% убыточных за год. Продавец премии зарабатывает чаще покупателя.",
   "art": "books"},
  {"title": "Почему CRYPTO NOVA их не торгует",
   "text": "3 причины. Опционов нет на OKX Europe — наш реальный счёт в ЕС их не видит. Копитрейдинг OKX копирует только фьючерсы и спот: подписчики сделок с опционами не получили бы. И на истории они не улучшили ни результат, ни просадку. Поэтому у нас опционы — только знания и индикатор настроения.",
   "example": "Как в жизни: инструмент может быть интересным, но если его нет в вашем магазине и он не помогает — покупать его незачем.",
   "art": "shield"},
  {"title": "Индикатор на панели",
   "text": "Плитка «Настроение опционов». DVOL — какого движения BTC за год ждёт рынок опционов; процентиль — дорого это или дёшево по меркам года. Пут/колл — сколько страховок от падения на 1 ставку на рост. Дорогие путы — страх, часто рядом с дном. Дешёвые на росте — самоуспокоенность. Это информация, не сигнал.",
   "example": "Пример: DVOL 55 и 80-й процентиль — рынок напуган сильнее, чем в 80% дней года. Пут/колл 0.95 — страховок почти столько же, сколько ставок на рост.",
   "art": "thermometer"},
 ],
 "remember": [
  "Пут — право продать по выбранной цене, колл — право купить. За право платят премию, и она сгорает, если право не пригодилось.",
  "Пут зарабатывает на падении, но страховку надо покупать каждый месяц: 2–5% в месяц, около 17–25% в год.",
  "На истории 2023–2026 путы не снизили просадку (−53%), а правило «падение → USDT» снизило её до −18.5% без премий.",
  "CRYPTO NOVA опционами не торгует: их нет на OKX Europe, они не копируются, на истории не помогли. На панели — только индикатор настроения.",
 ],
 "quiz": [
  {"q": "Вы купили пут с правом продать BTC по 70 000 за премию 2 000. BTC к сроку стоит 72 000. Что с вашими деньгами?",
   "options": ["Получите 2 000 обратно", "Премия 2 000 потеряна целиком", "Заработали 2 000", "Получите 70 000"],
   "answer": 1, "why": "BTC выше 70 000, право продать по 70 000 не пригодилось. Полис сгорел, премия потеряна."},
  {"q": "Почему покупка путов каждый месяц обычно заканчивается убытком?",
   "options": ["Биржи запрещают покупать путы", "Опцион дешевеет каждый день, а сильные падения приходят редко", "Путы растут только летом", "Премия всегда возвращается"],
   "answer": 1, "why": "Распад времени и повторная оплата: премия 2–5% в месяц сгорает, а в деньгах истекли лишь 5 путов из 27 на истории 2023–2026."},
  {"q": "Что показывает плитка «Настроение опционов» на панели?",
   "options": ["Сигнал купить или продать", "Настроение рынка опционов: DVOL и пут/колл, только информация", "Прибыль CRYPTO NOVA на опционах", "Цену биткоина через месяц"],
   "answer": 1, "why": "Это индикатор страха и спокойствия. CRYPTO NOVA опционами не торгует, сделок по этой плитке нет."},
 ],
}

L["en"] = {
 "id": ID, "title": "Options in plain words", "minutes": 6,
 "goal": "After this lesson you will know what a put and a call are, why insurance against a fall costs money every month and why CRYPTO NOVA does not trade options.",
 "cards": [
  {"title": "Put and call",
   "text": "An option is an insurance policy on a price. A put gives the right to sell bitcoin at a price chosen in advance, even if it has fallen below it. A call is the right to buy at the chosen price, even if it has risen above it. The right costs a premium. If the right was not needed, the premium is lost in full.",
   "example": "In real life: car insurance. You pay every year. No accident — the money is not returned. An accident — the insurer pays.",
   "art": "umbrella"},
  {"title": "Why a put earns in a fall",
   "text": "You bought a put with the right to sell BTC at 70,000 when it cost 78,000. BTC fell to 55,000. The right to sell at 70,000 is now worth about 15,000 per BTC: the gap between 70,000 and 55,000. That is how people «earn on a fall». BTC stayed above 70,000 — the put expired empty, the premium is gone.",
   "example": "In real life: fire insurance on a house. The house burned — you got the payout. It did not — you simply paid every year.",
   "art": "chart_down"},
  {"title": "The premium is the price of insurance",
   "text": "A 30-day put protecting against a fall deeper than 10% usually costs 2–5% of the BTC price. A month later the insurance must be bought again. Over a year that adds up to about 17–25% of the sum. Just to break even, the fall has to be strong and on time — while the policy is valid.",
   "example": "Example: BTC 78,000, a put with the right to sell at 70,000 costs 2,000. The profit starts only below 68,000: 70,000 minus the 2,000 premium.",
   "art": "calculator"},
  {"title": "Why most buyers lose",
   "text": "An option gets cheaper every day — this is time decay. The insurance is renewed again and again, while strong falls are rare. On BTC history 2023–2026, only 5 of 27 puts bought expired in the money. All 3 big falls began while our indicator still showed «growth»: the put was bought only after the first −15…−25%.",
   "example": "In real life: people buy an umbrella once they are already wet. By then the rain often stops.",
   "art": "clock"},
  {"title": "What history says",
   "text": "Tested on BTC from 2023 to October 2026. Just holding BTC: +369%, drawdown −53%. Our rule «regime «fall» → USDT»: +218%, drawdown −18.5%. Holding and buying a put in a «fall»: +235%, the same −53% drawdown, 17% a year in premiums. The insurance did not cut the drawdown; moving to USDT did, with no premiums.",
   "example": "In real life: in India millions of private traders use options. The regulator counted 89–93% losing over a year. The seller of the premium earns more often than the buyer.",
   "art": "books"},
  {"title": "Why CRYPTO NOVA does not trade them",
   "text": "3 reasons. There are no options on OKX Europe — our real account in the EU cannot see them. OKX copy trading copies only futures and spot: subscribers would not receive options trades. And on history they improved neither the result nor the drawdown. So for us options are knowledge and a mood indicator only.",
   "example": "In real life: a tool may be interesting, but if your shop does not sell it and it does not help, there is no reason to buy it.",
   "art": "shield"},
  {"title": "The indicator on the panel",
   "text": "The «Options mood» tile. DVOL — how much movement in BTC the options market expects over a year; the percentile — whether that is expensive or cheap by the year's standards. Put/call — how many hedges against a fall per 1 bet on a rise. Expensive puts mean fear, often near a bottom. Cheap puts in a rise mean complacency. Information, not a signal.",
   "example": "Example: DVOL 55 at the 80th percentile — the market is more frightened than on 80% of the year's days. Put/call 0.95 — almost as many hedges as bets on a rise.",
   "art": "thermometer"},
 ],
 "remember": [
  "A put is the right to sell at a chosen price, a call the right to buy. The right costs a premium, and it is lost if the right was not needed.",
  "A put earns in a fall, but the insurance must be bought every month: 2–5% a month, about 17–25% a year.",
  "On 2023–2026 history puts did not reduce the drawdown (−53%), while the rule «fall → USDT» cut it to −18.5% with no premiums.",
  "CRYPTO NOVA does not trade options: none on OKX Europe, not copied, no help on history. The panel shows a mood indicator only.",
 ],
 "quiz": [
  {"q": "You bought a put with the right to sell BTC at 70,000 for a 2,000 premium. At expiry BTC is 72,000. What happens to your money?",
   "options": ["You get the 2,000 back", "The 2,000 premium is lost in full", "You earned 2,000", "You receive 70,000"],
   "answer": 1, "why": "BTC is above 70,000, so the right to sell at 70,000 was not needed. The policy expired, the premium is lost."},
  {"q": "Why does buying puts every month usually end in a loss?",
   "options": ["Exchanges forbid buying puts", "An option gets cheaper every day, and strong falls are rare", "Puts only rise in summer", "The premium is always refunded"],
   "answer": 1, "why": "Time decay plus repeated payment: a 2–5% monthly premium burns, and only 5 of 27 puts expired in the money on 2023–2026 history."},
  {"q": "What does the «Options mood» tile on the panel show?",
   "options": ["A signal to buy or sell", "The mood of the options market: DVOL and put/call, information only", "CRYPTO NOVA's profit on options", "The bitcoin price a month ahead"],
   "answer": 1, "why": "It is a fear-and-calm indicator. CRYPTO NOVA does not trade options; no trade follows from this tile."},
 ],
}

L["uk"] = {
 "id": ID, "title": "Опціони простими словами", "minutes": 6,
 "goal": "Після уроку ви зрозумієте, що таке пут і кол, чому страховка від падіння коштує грошей щомісяця і чому CRYPTO NOVA опціонами не торгує.",
 "cards": [
  {"title": "Пут і кол",
   "text": "Опціон — це страховий поліс на ціну. Пут дає право продати біткоїн за заздалегідь обраною ціною, навіть якщо він упав нижче. Кол — право купити за обраною ціною, навіть якщо він виріс вище. За право платять премію. Якщо право не знадобилося, премія згорає повністю.",
   "example": "Як у житті: страховка на авто. Платите щороку. Аварії не було — гроші не повернуть. Була — страхова платить.",
   "art": "umbrella"},
  {"title": "Чому пут заробляє на падінні",
   "text": "Купили пут із правом продати BTC по 70 000, коли він коштував 78 000. BTC упав до 55 000. Право продати по 70 000 тепер коштує близько 15 000 на 1 BTC: це різниця між 70 000 і 55 000. Так і «заробляють на падінні». Лишився BTC вище 70 000 — пут сплив порожнім, премію втрачено.",
   "example": "Як у житті: страховка будинку від пожежі. Будинок згорів — отримали виплату. Не згорів — просто платили щороку.",
   "art": "chart_down"},
  {"title": "Премія — ціна страховки",
   "text": "Пут на 30 днів із захистом від падіння глибше 10% зазвичай коштує 2–5% від ціни BTC. Через місяць страховку треба купувати знову. За рік набігає приблизно 17–25% від суми. Щоб хоча б вийти в нуль, падіння має бути сильним і вчасним — поки поліс діє.",
   "example": "Приклад: BTC 78 000, пут із правом продати по 70 000 коштує 2 000. Заробіток почнеться лише нижче 68 000: 70 000 мінус 2 000 премії.",
   "art": "calculator"},
  {"title": "Чому більшість покупців втрачає",
   "text": "Опціон дешевшає щодня — це розпад часу. Страховку продовжують знову і знову, а сильні падіння приходять рідко. На історії BTC 2023–2026 із 27 куплених путів у грошах спливли лише 5. Усі 3 великі падіння почалися, коли наш індикатор ще показував «зростання»: пут купувався вже після перших −15…−25%.",
   "example": "Як у житті: парасольку купують, коли вже змокли. Дощ на той момент часто закінчується.",
   "art": "clock"},
  {"title": "Що каже історія",
   "text": "Перевірили на BTC з 2023 по жовтень 2026. Просто тримати BTC: +369%, просадка −53%. Наше правило «режим «падіння» → USDT»: +218%, просадка −18.5%. Тримати й купувати пут при «падінні»: +235%, просадка ті самі −53%, премій 17% на рік. Страховка просадку не зменшила, а вихід в USDT зробив це без премій.",
   "example": "Як у житті: в Індії опціонами торгують мільйони приватних осіб. Регулятор нарахував 89–93% збиткових за рік. Продавець премії заробляє частіше за покупця.",
   "art": "books"},
  {"title": "Чому CRYPTO NOVA ними не торгує",
   "text": "3 причини. Опціонів немає на OKX Europe — наш реальний рахунок у ЄС їх не бачить. Копітрейдинг OKX копіює лише ф'ючерси і спот: підписники угод з опціонами не отримали б. І на історії вони не покращили ні результат, ні просадку. Тому в нас опціони — лише знання та індикатор настрою.",
   "example": "Як у житті: інструмент може бути цікавим, але якщо його немає у вашій крамниці і він не допомагає — купувати його нема чого.",
   "art": "shield"},
  {"title": "Індикатор на панелі",
   "text": "Плитка «Настрій опціонів». DVOL — якого руху BTC за рік чекає ринок опціонів; процентиль — дорого це чи дешево за мірками року. Пут/кол — скільки страховок від падіння на 1 ставку на зростання. Дорогі пути — страх, часто поруч із дном. Дешеві на зростанні — самозаспокоєння. Це інформація, не сигнал.",
   "example": "Приклад: DVOL 55 і 80-й процентиль — ринок наляканий сильніше, ніж у 80% днів року. Пут/кол 0.95 — страховок майже стільки ж, скільки ставок на зростання.",
   "art": "thermometer"},
 ],
 "remember": [
  "Пут — право продати за обраною ціною, кол — право купити. За право платять премію, і вона згорає, якщо право не знадобилося.",
  "Пут заробляє на падінні, але страховку треба купувати щомісяця: 2–5% на місяць, близько 17–25% на рік.",
  "На історії 2023–2026 пути не зменшили просадку (−53%), а правило «падіння → USDT» зменшило її до −18.5% без премій.",
  "CRYPTO NOVA опціонами не торгує: їх немає на OKX Europe, вони не копіюються, на історії не допомогли. На панелі — лише індикатор настрою.",
 ],
 "quiz": [
  {"q": "Ви купили пут із правом продати BTC по 70 000 за премію 2 000. BTC на дату закінчення коштує 72 000. Що з вашими грошима?",
   "options": ["Отримаєте 2 000 назад", "Премію 2 000 втрачено повністю", "Заробили 2 000", "Отримаєте 70 000"],
   "answer": 1, "why": "BTC вище 70 000, право продати по 70 000 не знадобилося. Поліс згорів, премію втрачено."},
  {"q": "Чому купівля путів щомісяця зазвичай закінчується збитком?",
   "options": ["Біржі забороняють купувати пути", "Опціон дешевшає щодня, а сильні падіння приходять рідко", "Пути ростуть лише влітку", "Премія завжди повертається"],
   "answer": 1, "why": "Розпад часу і повторна оплата: премія 2–5% на місяць згорає, а в грошах спливли лише 5 путів із 27 на історії 2023–2026."},
  {"q": "Що показує плитка «Настрій опціонів» на панелі?",
   "options": ["Сигнал купити або продати", "Настрій ринку опціонів: DVOL і пут/кол, лише інформація", "Прибуток CRYPTO NOVA на опціонах", "Ціну біткоїна через місяць"],
   "answer": 1, "why": "Це індикатор страху і спокою. CRYPTO NOVA опціонами не торгує, угод за цією плиткою немає."},
 ],
}

L["el"] = {
 "id": ID, "title": "Τα options με απλά λόγια", "minutes": 6,
 "goal": "Μετά το μάθημα θα ξέρετε τι είναι το put και το call, γιατί η ασφάλεια από την πτώση κοστίζει κάθε μήνα και γιατί το CRYPTO NOVA δεν κάνει συναλλαγές σε options.",
 "cards": [
  {"title": "Put και call",
   "text": "Το option είναι ασφαλιστήριο για μια τιμή. Το put δίνει το δικαίωμα να πουλήσετε bitcoin σε τιμή που ορίσατε από πριν, ακόμη κι αν έπεσε πιο κάτω. Το call είναι το δικαίωμα να αγοράσετε στην ορισμένη τιμή, ακόμη κι αν ανέβηκε πιο πάνω. Το δικαίωμα κοστίζει ένα ασφάλιστρο. Αν δεν χρειάστηκε, το ασφάλιστρο χάνεται ολόκληρο.",
   "example": "Στη ζωή: ασφάλεια αυτοκινήτου. Πληρώνετε κάθε χρόνο. Χωρίς ατύχημα τα χρήματα δεν επιστρέφουν. Με ατύχημα πληρώνει η ασφαλιστική.",
   "art": "umbrella"},
  {"title": "Γιατί το put κερδίζει στην πτώση",
   "text": "Αγοράσατε put με δικαίωμα πώλησης BTC στα 70.000 όταν κόστιζε 78.000. Το BTC έπεσε στα 55.000. Το δικαίωμα πώλησης στα 70.000 αξίζει τώρα περίπου 15.000 ανά BTC: η διαφορά 70.000 μείον 55.000. Έτσι «κερδίζουν στην πτώση». Έμεινε το BTC πάνω από 70.000 — το put έληξε άδειο, το ασφάλιστρο χάθηκε.",
   "example": "Στη ζωή: ασφάλεια σπιτιού από πυρκαγιά. Κάηκε το σπίτι — πήρατε αποζημίωση. Δεν κάηκε — απλώς πληρώνατε κάθε χρόνο.",
   "art": "chart_down"},
  {"title": "Το ασφάλιστρο είναι η τιμή της ασφάλειας",
   "text": "Ένα put 30 ημερών που προστατεύει από πτώση πάνω από 10% κοστίζει συνήθως 2–5% της τιμής του BTC. Σε έναν μήνα η ασφάλεια αγοράζεται ξανά. Σε έναν χρόνο μαζεύονται περίπου 17–25% του ποσού. Για να βγείτε έστω στο μηδέν, η πτώση πρέπει να είναι ισχυρή και έγκαιρη — όσο ισχύει το συμβόλαιο.",
   "example": "Παράδειγμα: BTC 78.000, put με δικαίωμα πώλησης στα 70.000 κοστίζει 2.000. Το κέρδος αρχίζει μόνο κάτω από 68.000: 70.000 μείον 2.000 ασφάλιστρο.",
   "art": "calculator"},
  {"title": "Γιατί οι περισσότεροι αγοραστές χάνουν",
   "text": "Το option φθηναίνει κάθε μέρα — είναι η φθορά του χρόνου. Η ασφάλεια ανανεώνεται ξανά και ξανά, ενώ οι ισχυρές πτώσεις είναι σπάνιες. Στην ιστορία του BTC 2023–2026, από 27 puts μόνο 5 έληξαν με κέρδος. Και οι 3 μεγάλες πτώσεις ξεκίνησαν όταν ο δείκτης μας έδειχνε ακόμη «άνοδο»: το put αγοραζόταν μετά τα πρώτα −15…−25%.",
   "example": "Στη ζωή: την ομπρέλα την αγοράζουν όταν έχουν ήδη βραχεί. Ως τότε η βροχή συχνά σταματά.",
   "art": "clock"},
  {"title": "Τι λέει η ιστορία",
   "text": "Έλεγχος σε BTC από το 2023 ως τον Οκτώβριο 2026. Απλή διακράτηση BTC: +369%, πτώση −53%. Ο κανόνας μας «καθεστώς «πτώση» → USDT»: +218%, πτώση −18,5%. Διακράτηση με αγορά put στην «πτώση»: +235%, ίδια πτώση −53%, ασφάλιστρα 17% τον χρόνο. Η ασφάλεια δεν μείωσε την πτώση· η μετάβαση σε USDT το έκανε χωρίς ασφάλιστρα.",
   "example": "Στη ζωή: στην Ινδία εκατομμύρια ιδιώτες κάνουν options. Η εποπτική αρχή μέτρησε 89–93% με ζημιά στον χρόνο. Ο πωλητής του ασφαλίστρου κερδίζει πιο συχνά από τον αγοραστή.",
   "art": "books"},
  {"title": "Γιατί το CRYPTO NOVA δεν τα χρησιμοποιεί",
   "text": "3 λόγοι. Δεν υπάρχουν options στο OKX Europe — ο πραγματικός λογαριασμός μας στην ΕΕ δεν τα βλέπει. Το copy trading του OKX αντιγράφει μόνο futures και spot: οι συνδρομητές δεν θα λάμβαναν συναλλαγές options. Και στην ιστορία δεν βελτίωσαν ούτε το αποτέλεσμα ούτε την πτώση. Γι' αυτό σε εμάς τα options είναι μόνο γνώση και δείκτης διάθεσης.",
   "example": "Στη ζωή: ένα εργαλείο μπορεί να είναι ενδιαφέρον, αλλά αν το μαγαζί σας δεν το έχει και δεν βοηθά, δεν υπάρχει λόγος να το αγοράσετε.",
   "art": "shield"},
  {"title": "Ο δείκτης στον πίνακα",
   "text": "Το πλακίδιο «Διάθεση των options». DVOL — πόση κίνηση του BTC σε έναν χρόνο περιμένει η αγορά options· το εκατοστημόριο — αν αυτό είναι ακριβό ή φθηνό για τα μέτρα της χρονιάς. Put/call — πόσες ασφάλειες από πτώση ανά 1 στοίχημα ανόδου. Ακριβά puts σημαίνουν φόβο, συχνά κοντά σε πυθμένα. Φθηνά puts σε άνοδο, εφησυχασμό. Πληροφορία, όχι σήμα.",
   "example": "Παράδειγμα: DVOL 55 στο 80ό εκατοστημόριο — η αγορά φοβάται περισσότερο από ό,τι στο 80% των ημερών του έτους. Put/call 0,95 — σχεδόν όσες ασφάλειες όσα στοιχήματα ανόδου.",
   "art": "thermometer"},
 ],
 "remember": [
  "Put: δικαίωμα πώλησης σε ορισμένη τιμή, call: δικαίωμα αγοράς. Το δικαίωμα κοστίζει ασφάλιστρο, που χάνεται αν δεν χρειάστηκε.",
  "Το put κερδίζει στην πτώση, αλλά η ασφάλεια αγοράζεται κάθε μήνα: 2–5% τον μήνα, περίπου 17–25% τον χρόνο.",
  "Στην ιστορία 2023–2026 τα puts δεν μείωσαν την πτώση (−53%), ενώ ο κανόνας «πτώση → USDT» την έφερε στο −18,5% χωρίς ασφάλιστρα.",
  "Το CRYPTO NOVA δεν κάνει options: δεν υπάρχουν στο OKX Europe, δεν αντιγράφονται, δεν βοήθησαν στην ιστορία. Στον πίνακα υπάρχει μόνο δείκτης διάθεσης.",
 ],
 "quiz": [
  {"q": "Αγοράσατε put με δικαίωμα πώλησης BTC στα 70.000 για ασφάλιστρο 2.000. Στη λήξη το BTC είναι 72.000. Τι γίνεται με τα χρήματά σας;",
   "options": ["Παίρνετε πίσω τα 2.000", "Το ασφάλιστρο 2.000 χάνεται ολόκληρο", "Κερδίσατε 2.000", "Παίρνετε 70.000"],
   "answer": 1, "why": "Το BTC είναι πάνω από 70.000, το δικαίωμα πώλησης στα 70.000 δεν χρειάστηκε. Το συμβόλαιο έληξε, το ασφάλιστρο χάθηκε."},
  {"q": "Γιατί η αγορά puts κάθε μήνα συνήθως καταλήγει σε ζημιά;",
   "options": ["Τα ανταλλακτήρια απαγορεύουν τα puts", "Το option φθηναίνει κάθε μέρα και οι ισχυρές πτώσεις είναι σπάνιες", "Τα puts ανεβαίνουν μόνο το καλοκαίρι", "Το ασφάλιστρο επιστρέφεται πάντα"],
   "answer": 1, "why": "Φθορά χρόνου και επαναλαμβανόμενη πληρωμή: ασφάλιστρο 2–5% τον μήνα καίγεται, και μόνο 5 από 27 puts έληξαν με κέρδος στην ιστορία 2023–2026."},
  {"q": "Τι δείχνει το πλακίδιο «Διάθεση των options» στον πίνακα;",
   "options": ["Σήμα αγοράς ή πώλησης", "Τη διάθεση της αγοράς options: DVOL και put/call, μόνο πληροφορία", "Το κέρδος του CRYPTO NOVA από options", "Την τιμή του bitcoin σε έναν μήνα"],
   "answer": 1, "why": "Είναι δείκτης φόβου και ηρεμίας. Το CRYPTO NOVA δεν κάνει options· καμία συναλλαγή δεν προκύπτει από αυτό το πλακίδιο."},
 ],
}

L["es"] = {
 "id": ID, "title": "Opciones en palabras simples", "minutes": 6,
 "goal": "Tras la lección sabrá qué son un put y un call, por qué el seguro contra la caída cuesta dinero cada mes y por qué CRYPTO NOVA no opera con opciones.",
 "cards": [
  {"title": "Put y call",
   "text": "Una opción es una póliza de seguro sobre un precio. El put da el derecho a vender bitcoin a un precio elegido de antemano, aunque haya caído por debajo. El call es el derecho a comprar al precio elegido, aunque haya subido por encima. El derecho cuesta una prima. Si no hizo falta, la prima se pierde entera.",
   "example": "En la vida: el seguro del coche. Paga cada año. Sin accidente, el dinero no vuelve. Con accidente, paga la aseguradora.",
   "art": "umbrella"},
  {"title": "Por qué el put gana en la caída",
   "text": "Compró un put con derecho a vender BTC a 70 000 cuando costaba 78 000. BTC cayó a 55 000. El derecho a vender a 70 000 vale ahora unos 15 000 por BTC: la diferencia entre 70 000 y 55 000. Así «se gana en la caída». Si BTC se quedó por encima de 70 000, el put venció vacío y la prima se perdió.",
   "example": "En la vida: el seguro de incendio de la casa. Se quemó, cobró la indemnización. No se quemó, simplemente pagó cada año.",
   "art": "chart_down"},
  {"title": "La prima es el precio del seguro",
   "text": "Un put a 30 días que protege de una caída mayor del 10% suele costar un 2–5% del precio de BTC. Al mes hay que comprar el seguro de nuevo. En un año suma cerca del 17–25% del importe. Solo para quedar en cero, la caída debe ser fuerte y a tiempo, mientras la póliza está vigente.",
   "example": "Ejemplo: BTC 78 000, un put con derecho a vender a 70 000 cuesta 2 000. La ganancia empieza solo por debajo de 68 000: 70 000 menos la prima de 2 000.",
   "art": "calculator"},
  {"title": "Por qué la mayoría de compradores pierde",
   "text": "La opción se abarata cada día: es el paso del tiempo. El seguro se renueva una y otra vez, y las caídas fuertes son raras. En la historia de BTC 2023–2026, de 27 puts comprados solo 5 vencieron con ganancia. Las 3 grandes caídas empezaron cuando nuestro indicador aún marcaba «subida»: el put se compraba tras los primeros −15…−25%.",
   "example": "En la vida: el paraguas se compra cuando ya está mojado. Para entonces la lluvia suele parar.",
   "art": "clock"},
  {"title": "Qué dice la historia",
   "text": "Probado en BTC de 2023 a octubre de 2026. Solo mantener BTC: +369%, caída máxima −53%. Nuestra regla «régimen «caída» → USDT»: +218%, caída −18,5%. Mantener y comprar put en «caída»: +235%, la misma caída −53%, primas del 17% al año. El seguro no redujo la caída; pasar a USDT lo hizo sin primas.",
   "example": "En la vida: en India millones de particulares operan opciones. El regulador contó un 89–93% con pérdidas al año. El vendedor de la prima gana más a menudo que el comprador.",
   "art": "books"},
  {"title": "Por qué CRYPTO NOVA no las opera",
   "text": "3 razones. No hay opciones en OKX Europe: nuestra cuenta real en la UE no las ve. El copy trading de OKX copia solo futuros y spot: los suscriptores no recibirían operaciones con opciones. Y en la historia no mejoraron ni el resultado ni la caída. Por eso en CRYPTO NOVA las opciones son solo conocimiento y un indicador de ánimo.",
   "example": "En la vida: una herramienta puede ser interesante, pero si su tienda no la vende y no ayuda, no hay motivo para comprarla.",
   "art": "shield"},
  {"title": "El indicador en el panel",
   "text": "La tarjeta «Ánimo de las opciones». DVOL: cuánto movimiento de BTC en un año espera el mercado de opciones; el percentil, si eso es caro o barato para el año. Put/call: cuántos seguros contra la caída por cada 1 apuesta a la subida. Puts caros: miedo, a menudo cerca del suelo. Puts baratos en subida: exceso de confianza. Información, no señal.",
   "example": "Ejemplo: DVOL 55 en el percentil 80: el mercado tiene más miedo que en el 80% de los días del año. Put/call 0,95: casi tantos seguros como apuestas a la subida.",
   "art": "thermometer"},
 ],
 "remember": [
  "Put: derecho a vender a un precio elegido; call: derecho a comprar. El derecho cuesta una prima, que se pierde si no hizo falta.",
  "El put gana en la caída, pero el seguro se compra cada mes: 2–5% al mes, cerca del 17–25% al año.",
  "En la historia 2023–2026 los puts no redujeron la caída (−53%), mientras la regla «caída → USDT» la bajó a −18,5% sin primas.",
  "CRYPTO NOVA no opera opciones: no hay en OKX Europe, no se copian, no ayudaron en la historia. En el panel solo hay un indicador de ánimo.",
 ],
 "quiz": [
  {"q": "Compró un put con derecho a vender BTC a 70 000 por una prima de 2 000. Al vencimiento BTC vale 72 000. ¿Qué pasa con su dinero?",
   "options": ["Recupera los 2 000", "La prima de 2 000 se pierde entera", "Ganó 2 000", "Recibe 70 000"],
   "answer": 1, "why": "BTC está por encima de 70 000, el derecho a vender a 70 000 no hizo falta. La póliza venció, la prima se perdió."},
  {"q": "¿Por qué comprar puts cada mes suele acabar en pérdida?",
   "options": ["Los exchanges prohíben comprar puts", "La opción se abarata cada día y las caídas fuertes son raras", "Los puts solo suben en verano", "La prima siempre se devuelve"],
   "answer": 1, "why": "Paso del tiempo y pago repetido: la prima del 2–5% mensual se quema, y solo 5 de 27 puts vencieron con ganancia en la historia 2023–2026."},
  {"q": "¿Qué muestra la tarjeta «Ánimo de las opciones» en el panel?",
   "options": ["Una señal de compra o venta", "El ánimo del mercado de opciones: DVOL y put/call, solo información", "La ganancia de CRYPTO NOVA con opciones", "El precio de bitcoin dentro de un mes"],
   "answer": 1, "why": "Es un indicador de miedo y calma. CRYPTO NOVA no opera opciones; de esta tarjeta no sale ninguna operación."},
 ],
}

FILES = {"ru": "academy.json", "en": "academy.en.json", "uk": "academy.uk.json", "el": "academy.el.json", "es": "academy.es.json"}


def check():
    bad = []
    for lang, les in L.items():
        assert les["id"] == ID and len(les["quiz"]) == 3 and len(les["cards"]) == 7 and len(les["remember"]) == 4, lang
        for c in les["cards"]:
            if len(c["text"]) > 350: bad.append((lang, c["title"], "text", len(c["text"])))
            if len(c["example"]) > 220: bad.append((lang, c["title"], "example", len(c["example"])))
        for q in les["quiz"]:
            assert len(q["options"]) == 4 and 0 <= q["answer"] < 4
        s = json.dumps(les, ensure_ascii=False)
        assert "атом" not in s and "бесплатно" not in s.lower() and "free" not in s.lower().split() and "gratis" not in s.lower()
    return bad


if __name__ == "__main__":
    print(check())
    if "--apply" in sys.argv:
        web = sys.argv[sys.argv.index("--apply") + 1]
        for lang, fn in FILES.items():
            p = os.path.join(web, fn)
            d = json.load(open(p, encoding="utf-8"))
            lv = [x for x in d["levels"] if x.get("level") == "L4"][0]
            ids = [x["id"] for x in lv["lessons"]]
            if ID in ids:
                lv["lessons"][ids.index(ID)] = L[lang]
            else:
                lv["lessons"].insert(ids.index(AFTER) + 1, L[lang])
            d["updated"] = "2026-10-30"
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)      # the files' own format: 2 spaces, no final newline
            print(lang, "ok", len(lv["lessons"]))
