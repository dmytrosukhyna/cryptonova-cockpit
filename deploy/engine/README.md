# Быстрый контур на сервере

Торговые демо-столы каждую минуту вместо 16–30 минут. Ордеров на бирже нет, все счета демо.

## Как работает

- Каждую минуту сервер забирает только новые 15-минутные свечи по 18 монетам. Потом проходят столы:
  - CNPTS — Консервативная, Сбалансированная, Агрессивная, тесты;
  - шорт листингов;
  - «Сигналы CRYPTO NOVA»;
  - «Нейтральная корзина»;
  - «Турбо»;
  - лента сигналов: Telegram и приложение.
- Раз в 2 минуты сервер отправляет результаты в GitHub, и панель обновляется. Новости, исследования и макро берёт из GitHub.
- GitHub сам видит, что сервер жив: `web/engine.json` не старше 10 минут. Тогда он не запускает эти столы у себя. Если
  сервер замолчит, GitHub сам вернёт столы себе.
- Мост в приложение (`cnq_bridge.py` v4) читает сигналы прямо с этого сервера, без ожидания сайта.

## Установка (один раз, около 30 минут, из них 15 — ожидание)

1. **Токен GitHub** (5 минут).
   - github.com → аватар → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.
   - Repository access: Only select repositories → `cryptonova-cockpit`.
   - Permissions → Contents: **Read and write**.
   - Expiration: 1 год. Скопировать токен.
2. **Токен бота** — @BotFather → /mybots → бот CRYPTO NOVA → API Token.
3. **Сервер:**
   ```
   ssh root@91.99.193.20
   curl -fsSL https://raw.githubusercontent.com/dmytrosukhyna/cryptonova-cockpit/main/deploy/engine/install.sh | bash
   nano /etc/cnq.env          # вписать CNQ_GH_TOKEN= и CNQ_TG_TOKEN=, сохранить: Ctrl+O, Enter, Ctrl+X
   bash /opt/cnq/repo/deploy/engine/start.sh
   ```
   Первый запуск один раз загружает свечи за 400 дней, это 10–20 минут.
4. **Мост v4** (с вашего Mac):
   ```
   scp "$(ls -t ~/Downloads/cnq_bridge*.py | head -1)" root@91.99.193.20:/root/cnq_bridge.py
   ```

## Проверка и управление

- Статус: `systemctl status cnq-engine`.
- Журнал вживую: `journalctl -u cnq-engine -f`. Каждую минуту строка `cycle N 25s ok`.
- Остановить: `systemctl stop cnq-engine`. Через 10 минут GitHub сам вернёт столы себе.
- Обновлять ничего не нужно: код подтягивается из GitHub автоматически раз в 2 минуты.

## Безопасность

- Секреты только в `/etc/cnq.env`, права 600. В GitHub и в чат их не отправлять.
- Движок работает от отдельного пользователя `cnq` и уступает процессор приложению: Nice 10, не больше 60% CPU и 2 ГБ памяти.
