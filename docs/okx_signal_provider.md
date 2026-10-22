# CRYPTO NOVA — сигнал на OKX Signal Marketplace

Что это: сделки фьючерсов CRYPTO NOVA («отобранные сигналы» — столы «откуп падений» и «Импульс BTC», те же, что в шапке панели)
сами уходят в OKX как сигнал. Подписчики на OKX создают своего бота на этот сигнал, и бот торгует на их собственном счёте.
Начинаем с бесплатного сигнала (Free), без доли прибыли.

Пока вы не сделаете шаги ниже, отправка **выключена**: сервер ничего не шлёт.

Источник: справка OKX «How do I publish or subscribe to a signal on Signal Marketplace»
(https://www.okx.com/en-eu/help/how-do-i-publish-or-subscribe-to-a-signal-on-signal-marketplace) и «Trading Signal Bot Alert
Message 2.0 Specifications» (https://www.okx.com/help/signal-bot-alert-message-specifications).

## 1. Подать заявку провайдера сигналов

1. Войдите на okx.com под **основным** аккаунтом. Субаккаунт не подходит: «The account used to apply as a signal provider must be
   a main account (sub-accounts aren't supported)». Доход от сигнала получает тот же аккаунт, который подал заявку.
2. Откройте ссылку заявки из справки OKX: https://www.okx.com/en-eu/copy-trading/apply-signal-provider
3. Заполните форму и отправьте. Ждите одобрения от OKX.
4. Если ссылка перекидывает на главную OKX Europe (так же, как okx.com/copy-trading): в той же справке есть второй путь —
   «contact our business team». Напишите в поддержку OKX (чат на сайте): «I want to apply as a signal provider on Signal
   Marketplace, main account, OKX Europe».

## 2. Создать свой сигнал

1. На сайте OKX: **Trade → Bots & Copy → Trading bots → Signal bot**.
2. Нажмите **Add custom signal**.
3. Signal name: `CRYPTO NOVA`. Signal description (можно так):
   «CRYPTO NOVA selected futures signals: buying liquidation cascades and panic drops, and alts catching up with a BTC impulse.
   Market entries and exits on USDT perpetuals; position size is a share of the bot's investment. Past results are no promise.»
4. Нажмите **Create signal**.
5. OKX покажет **Webhook URL** и **signal token** (в блоке AlertMsg Specification). Это два значения для сервера (шаг 3).
   Никому их не пересылайте и не вставляйте в чаты.
6. Важно: созданный сигнал-бот нельзя удалить (лимит — 50), поэтому создайте его один раз.

## 3. Вписать два значения на сервер

1. Зайдите на сервер (как обычно, под root).
2. Откройте файл настроек:
   ```
   nano /etc/cnq.env
   ```
3. Допишите в конец три строки (вместо `<…>` — ваши значения из шага 2.5, без кавычек и пробелов):
   ```
   CNQ_OKX_SIGNAL_URL=<Webhook URL>
   CNQ_OKX_SIGNAL_TOKEN=<signal token>
   CNQ_OKX_SIGNAL=on
   ```
   Если там уже есть строка `CNQ_OKX_SIGNAL=off` — поменяйте `off` на `on`.
4. Сохраните (Ctrl+O, Enter, Ctrl+X) и перезапустите движок:
   ```
   systemctl restart cnq-engine
   ```
5. Проверка: в панели, вкладка «Фьючерсы», под заголовком «Фьючерсы · CRYPTO NOVA» строка
   «Сигнал CRYPTO NOVA на OKX: **включён** · отправлено 0 · последний —». Если там «выключен» — наведите на строку, там причина
   (например, нет одного из значений).
6. С этого момента каждая **новая** сделка столов уходит в OKX. Сделки, открытые до включения, не отправляются никогда.
7. Проверить своими глазами (по желанию): создайте на свой сигнал собственного бота (Signal bot → ваш сигнал → Create bot) с
   маленькой маржой, например 20 USDT, плечо 1×. При следующей сделке CRYPTO NOVA бот откроет позицию, при выходе — закроет.

## 4. Опубликовать как Free

1. **Trade → Bots & Copy → Trading bots** → **Marketplace dashboard** → **Signal subscription**.
2. Переключите на **Unpublished**, выберите сигнал CRYPTO NOVA → **Publish signal**.
3. В окне Publish signal:
   - Signal name и description — как в шаге 2.3;
   - Fee model: **Free**;
   - Recommended amount: можно «optional» (рекомендуем от 100 USDT);
   - Supported trading pairs: 18 монет, которыми торгуют столы — BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, LINK, LTC, DOT, NEAR,
     BCH, TRX, SUI, APT, ARB, OP (все в паре к USDT, бессрочные).
4. Нажмите **Publish signal** — сигнал уйдёт на проверку OKX. После одобрения он появится в разделе **Free signal**.
5. Важно: после публикации модель оплаты изменить нельзя («the fee model and amount … can't be modified»). Платная версия потом —
   это отдельный новый сигнал.

## 5. Что увидят подписчики

1. На сайте: **Trade → Bots & Copy → Trading bots → Signal Marketplace** → раздел Free signal → «CRYPTO NOVA» с вашим описанием.
   В приложении OKX: Trade → Bots & Copy → Trading bots → Signal.
2. Подписчик нажимает **Subscribe** / **Create bot**, выбирает пары, **сам** ставит плечо (Leverage) и сумму (Margin) → Create.
3. Дальше его бот работает сам:
   - стол CRYPTO NOVA открыл сделку → бот входит по рынку (лонг или шорт) на долю своей суммы. Доля = доля этой сделки в счёте
     CRYPTO NOVA, обычно 5–15 %. При плече 1× бот держит то же, что счёт на панели;
   - стол взял часть прибыли → бот закрывает ту же часть позиции;
   - стол закрыл сделку (стоп, цель или время) → бот закрывает позицию.
4. Стоп и цель в сообщении OKX не передаются (это настройки самого бота). Выход приходит сообщением в момент, когда стол выходит.
   Подписчик может дополнительно поставить свои TP/SL в настройках бота.
5. Одна позиция на монету: если оба стола хотят одну и ту же монету, в сигнал уходит только первая сделка.

## Честно

- Плечо и маржу каждый подписчик ставит сам. Плечо выше 1× делает и прибыль, и просадку больше, чем на панели. По справке OKX,
  если позицию ликвидировало, бот подписчика не останавливается.
- Прошлые результаты — не обещание будущих. Счета на панели — демо по настоящим ценам; у подписчика вход и выход могут отличаться
  (задержка, проскальзывание, комиссии, фандинг).
- Сигнал бесплатный. Платная версия (доля прибыли или месячная плата) — только после консультации с юристом.
- Подписку на сигнал на OKX пока нельзя отменить («cancellation is currently not supported») — об этом стоит сказать в описании.

## Выключить

1. В `/etc/cnq.env` поставьте `CNQ_OKX_SIGNAL=off`, затем `systemctl restart cnq-engine`.
2. Лучше выключать, когда в шапке нет открытых сделок: пока отправка выключена, выходы не уходят, и у подписчиков позиции
   остаются открытыми. После повторного включения недошедшие выходы отправятся, а сделки, открытые во время паузы, — нет.

## Для разработчика

- Код: `cnq/agents/okx_signal.py`, шаг `okxsignal` движка (`cnq/engine.py`, каждую минуту после столов). Тесты:
  `tests/test_okx_signal.py`.
- Сообщение (раздел B спецификации OKX), значения — строками:
  ```
  {"action": "ENTER_LONG", "instrument": "APT-USDT-SWAP", "signalToken": "***", "timestamp": "2026-10-22T12:00:03.000Z",
   "maxLag": "60", "orderType": "market", "investmentType": "percentage_investment", "amount": "12.7"}
  ```
  Частичный выход: `EXIT_LONG` / `EXIT_SHORT`, `percentage_position`, доля от остатка. Полный выход: `percentage_position` `100`.
- Каждое событие записано в `data/agents.sqlite` (таблица `okx_signal_events`) — после перезапуска ничего не уходит дважды.
- Вход старше 30 минут не отправляется (`CNQ_OKX_SIGNAL_MAX_AGE_MIN`). Свой фиксированный размер входа вместо доли счёта:
  `CNQ_OKX_SIGNAL_PCT=10`.
- Монета, которой нет среди USDT-бессрочных OKX, пропускается с причиной (список инструментов OKX обновляется раз в 12 ч,
  `data/okx_swaps.json`).
- Публичный журнал: `web/okx_signal.json` (время, монета, действие, стол, статус). Токена и адреса там нет.
- Часы сервера должны быть точными (OKX отбрасывает сообщение старше `maxLag` = 60 с): `timedatectl` → «System clock
  synchronized: yes».
