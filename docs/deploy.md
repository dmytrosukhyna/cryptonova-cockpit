# Развёртывание терминала на сервере (Ubuntu, pm2/cron)

```bash
git clone <репозиторий> ~/cryptonova-quant && cd ~/cryptonova-quant
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

# 1. История по всем монетам стартового списка (BingX, 365 дней, 1m…1d) — ~6 мин на монету
for s in BTC-USDT ETH-USDT SOL-USDT BNB-USDT XRP-USDT DOGE-USDT ADA-USDT AVAX-USDT LINK-USDT LTC-USDT; do
  CNQ_SYMBOL=$s python -m cnq.cli backfill; done

# 2. Telegram
export CNQ_TG_TOKEN=123456:ABC...   # @BotFather
export CNQ_TG_CHAT=-100...          # id канала/группы, бот — админ

# 3. Терминал: JSON + статическая страница
python -m cnq.cli terminal --out web        # web/terminal.json
pm2 serve web 8080 --name cnq-web --spa     # или nginx: root → ~/cryptonova-quant/web
#   /terminal.html — рабочий терминал аналитика, /cockpit.html (= корень сайта) — кокпит, /live.html — панель для эфира (вкладки Монеты / Сетапы / Новости)

# 4. Расписание (cron, UTC)
# ежедневный бриф в канал 06:00 UTC
0 6 * * *   cd ~/cryptonova-quant && . .venv/bin/activate && CNQ_SYMBOL=BTC-USDT python -m cnq.cli backfill --days 3 >/dev/null && python -m cnq.cli brief --send
# обновление терминала и алерты каждые 15 минут
*/15 * * * * cd ~/cryptonova-quant && . .venv/bin/activate && for s in BTC-USDT ETH-USDT SOL-USDT BNB-USDT XRP-USDT DOGE-USDT ADA-USDT AVAX-USDT LINK-USDT LTC-USDT; do CNQ_SYMBOL=$s python -m cnq.cli backfill --days 2 >/dev/null; done && python -m cnq.cli terminal --out web && python -m cnq.cli alerts

# 4b. Отделы (новости/фундаментал/риск/редактор) — раз в час, пост с утренним брифом
export ANTHROPIC_API_KEY=...
0 * * * *   cd ~/cryptonova-quant && . .venv/bin/activate && python -m cnq.cli agents --out web
5 6 * * *   cd ~/cryptonova-quant && . .venv/bin/activate && python -m cnq.cli agents --out web --send

# 4c. Гэпы понедельника по валютам (класс A) — воскресенье 22:05 UTC
export TWELVE_DATA_KEY=...
5 22 * * 0  cd ~/cryptonova-quant && . .venv/bin/activate && python -m cnq.cli fx-gaps --send

# 5. Запись стакана/сделок BingX 24/7 (для исследований исполнения)
pm2 start "python -m cnq.cli stream" --name cnq-stream
```

Внутри страницы кнопка «Отправить в Telegram» шлёт напрямую в Bot API из браузера (токен в localStorage);
cron-алерты идут с сервера через `cnq.terminal.notify`, дубликаты отсекаются по `data/notified.json`.

## Чего пока нет
- Новости/токеномика: нужен ключ API языковой модели и источники (CoinGecko/CMC API, RSS); слот в JSON (`news`) зарезервирован.
- Футпринт для монет кроме BTC: конвертировать ladder-файлы датасета (`scripts/pq2csv.mjs`) или строить из BingX `stream` (сделки → объём по ценам).
- Ликвидации вживую: Binance WS `forceOrder` (BingX их не даёт).


## Cockpit (панель для эфира, v4)

`web/cockpit.html` — один экран в стиле «кокпит»: тёмный фон, неон, график кубики/свечи с яркими зонами
поддержки/сопротивления, подсказки на графике (▲ вход, ■ стоп, ◆ цель, ⚠ внимание, ● каскад ликвидаций),
живой стакан на ценовой шкале, профиль объёма (POC), аномалии объёма, EMA/VWAP/Фибо/RSI/MACD по кнопкам.
Сегменты «Крипта» и «Валюты · Металлы» (металлы PAXG/XAU/XAG, 9 FX-пар через Twelve Data).
Сигналы классов A (гэп понедельника, FX), B (каскад ликвидаций), C (отбой от зоны 4ч) с плечом, стопом,
3 целями, маржой, ликвидацией, кнопками «Копировать» / «Исполнить» (через `cnq.execution.server`, демо VST по умолчанию).
Новости из `decision.json`; алерты: тосты + системные уведомления + звук. Открывается как файл или с любого статического хостинга
рядом с `terminal.json`, `decision.json`, `profiles.json` (обновляются `python -m cnq.cli terminal` и `agents`).

### Cockpit v6 (13.10)
- Стиль HUD (тёмно-синий, циан), звёздное поле, уголки-скобки, орбитальное кольцо режима.
- График: тень ликвидности у зон (из стакана; без стакана — доля объёма), «стены» крупных заявок через весь график,
  прогноз — два сценария A/B с оценкой вероятности (режим + импульс + стакан + дельта; это оценка, не гарантия),
  подписи-выводы на всплесках объёма («покупатели выкупили поддержку — сигнал к развороту вверх»).
- «Вывод CRYPTO NOVΛ» под графиком: что делать, почему, сценарии, положение цены в диапазоне, кто давит, стены, 3 горизонта.
- Киты: публичные позиции кошельков на Hyperliquid (POST api.hyperliquid.xyz/info clearinghouseState), лидерборд месяца,
  алерт при смене позиций; список хранится в localStorage. Индикаторы и метрики спрятаны за кнопками.
