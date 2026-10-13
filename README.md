# CRYPTO NOVΛ Quant — этап 1

Отдельный проект (не часть мобильного приложения). Python 3.11+, SQLite, без внешних
зависимостей для исследований; `websockets` нужен только живому сборщику.

```
cnq/
  bingx/      REST (публичные данные), WebSocket (trade, incrDepth), локальный стакан
  binance/    data.binance.vision (klines, aggTrades, bookDepth, metrics), fapi (OI, L/S, ликвидации)
  store/      SQLite: свечи, сделки, стакан, OI, funding, качество данных, сетапы, журнал
  collector/  backfill (идемпотентный), stream (24/7 запись), quality (пропуски, целостность)
  features/   дельта/CVD/ATR/относительный объём (каузальные), уровни-зоны со скорингом
  setups/     Setup + статусы, Discovery (LEVEL_BOUNCE, FALSE_BREAKOUT_REVERSAL), Validation
  risk/       детерминированный расчёт позиции, лестница TP CRYPTO NOVΛ, матожидание
  paper/      симулятор по свечам без look-ahead, replay на истории
  cli.py
tests/        10 тестов, без сети: python3 tests/run.py  (или python -m pytest tests)
```

## Решение по источникам данных

| Что нужно | Откуда | Почему |
|---|---|---|
| Свечи с taker-buy (дельта, CVD) | BingX `klines v3` и Binance `klines` | Оба отдают за любой период |
| История каждой сделки (order flow) | **Binance aggTrades** (daily zip) | BingX отдаёт только последние ~100 за запрос |
| История стакана | **Binance bookDepth** (±1…5 % каждую минуту, с 2023) | У BingX истории стакана нет — пишем сами с WS |
| Open Interest, long/short, taker ratio | **Binance metrics** (каждые 5 мин) + `fapi` | BingX отдаёт только текущее OI |
| Ликвидации | **Binance WS `forceOrder`** | В публичном API BingX их нет |
| Funding | BingX и Binance (история есть у обоих) | |
| Список монет | CoinMarketCap (только отбор), фильтр по наличию контракта на BingX | |
| Исполнение, проскальзывание, спред | **BingX** (`stream`, `depth`, VST-демо) | Торгуем здесь |

Итог: **исследование и поиск сетапов — на данных Binance** (глубже рынок, бесплатная
история всего нужного); **проверка исполнимости и демо/реальные сделки — BingX**.
TradingView не является источником данных (API нет), Coinglass — платно и позже.

## Запуск

```bash
pip install websockets                      # только для stream
python -m cnq.cli backfill --days 120        # BingX свечи 1m…1d + funding → data/cnq.sqlite
python -m cnq.cli binance-peek               # проверить формат файлов Binance
python -m cnq.cli binance-sync --days 90     # klines 1m/1h, metrics, bookDepth (+ --aggtrades)
python -m cnq.cli status
python -m cnq.cli levels --interval 15m
python -m cnq.cli replay --interval 15m --mode STRICT_CRYPTO_NOVA --risk 1.0
python -m cnq.cli replay --interval 15m --mode LEVEL_3R
python -m cnq.cli ev
python -m cnq.cli stream                     # 24/7 на сервере: pm2 start "python -m cnq.cli stream" --name cnq-stream
```

Замечания по replay на 15m: порядок касания TP и стопа внутри свечи неизвестен,
симулятор считает, что стоп был первым (консервативно). Для честных цифр гоняйте на 1m.

## Что уже проверено
- Разбор свечей BingX v3 и расчёт дельты; инкрементальный стакан с контролем `lastUpdateId`.
- Каузальность признаков и уровней (будущее не меняет прошлое) — есть тест.
- Арифметика лестницы 40/30/10/10/10 и расчёт позиции от денежного риска с учётом комиссий.
- Сквозной прогон discovery → validation → план → paper → журнал в SQLite — есть тест.
- На синтетическом случайном блуждании система теряет (−0.27R/сделку): это правильно —
  в шуме преимущества быть не должно, и никакого look-ahead нет.

## Что НЕ сделано (и не должно считаться сделанным)
- Статистика сетапов на реальных данных — требует запуска backfill/binance-sync на машине с доступом к биржам.
- Веб-терминал, тепловая карта, новости, ML-калибровка вероятностей, BingX VST-адаптер.
- Сетапы VOLUME_ABSORPTION / LIQUIDITY_SWEEP (нужны aggTrades; загрузчик есть, правила — нет).

## Исследование на 6 годах (10.10.2026) — см. docs/research-findings-2026-10-10.md
Данные: открытый датасет Binance USDT-perp 15m, 18 монет, 09.2020–09.2026, с funding/OI/ликвидациями/
тейкерскими объёмами/футпринтом: `git clone https://github.com/kbsingh1399/Backtesting_Data` →
`node scripts/pq2csv.mjs out.csv X_15m_master_2020_2026.parquet` (нужен клон hyparquet рядом) →
`cnq.research.binance_master.load_master`. Скрипты исследований: `scripts/levels_multi.py`, `scripts/deriv.py`,
очередь `scripts/run_queue.sh`.


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
