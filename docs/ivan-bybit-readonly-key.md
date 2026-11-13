# Bybit: ключ «только чтение» для наблюдения / Read-only key for observation

## Для Ивана (по-русски)

Мы только **смотрим** демо-счета: позиции и закрытые сделки. С ключом «только чтение» нельзя торговать, переводить или выводить средства.

Сделайте ключ на **каждом** демо-счёте (A, B, C):

1. Войдите на bybit.com и переключитесь в **Demo Trading**. Это отдельный счёт со своим ID.
2. Нажмите на аватар → **API** → **Create New Key** → **System-generated API Keys**.
3. Выберите **Read-Only**. Галочки торговли (Orders/Positions/Trade) и вывода (Withdraw/Transfer) не ставьте.
4. Привязку к IP можно не включать. Тогда у ключа будет срок действия, и его придётся пересоздать.
5. Сохраните **API Key** и **Secret**. Secret показывается один раз.

Отправьте владельцу:
- имя счёта (A, B или C), API Key и Secret;
- **только лично**, в личных сообщениях. Никогда не публикуйте их в общих чатах и группах.

Ключ на демо-счёте работает только с демо-сервером. Отозвать доступ можно в любой момент: удалите ключ в разделе API.

## For Ivan (English)

We only **watch** the demo accounts: positions and closed trades. A read-only key cannot trade, transfer or withdraw.

Create a key on **each** demo account (A, B, C):

1. Log in at bybit.com and switch to **Demo Trading**. It is a separate account with its own ID.
2. Click your avatar → **API** → **Create New Key** → **System-generated API Keys**.
3. Choose **Read-Only**. Do not tick trading (Orders/Positions/Trade) or withdraw/transfer permissions.
4. IP binding is optional. Without it the key expires and has to be created again later.
5. Save the **API Key** and the **Secret**. The secret is shown only once.

Send to the owner:
- the account name (A, B or C), the API key and the secret;
- **only privately**, in a direct message. Never post them in a group chat.

A demo key works only with the demo server. To revoke access, delete the key in the API section.

## Для владельца

1. Откройте cryptonova.vip → вкладка **«Иван · тест-стратегии»**.
2. Раскройте «Подключить счёт Ивана», введите имя («Иван A»), API Key и Secret.
3. Для демо-счёта оставьте галочку «Демо-счёт» и нажмите **«Подключить»**.

Как работает проверка:
- сервер проверяет ключ у Bybit;
- ключ с правом торговли или вывода он **не сохраняет** и пишет почему;
- ключи хранятся только на сервере и обратно не показываются;
- «Проверить» читает счёт сразу;
- «Отключить» удаляет ключ с сервера. Сам ключ удалите и в кабинете Bybit, или попросите Ивана.
