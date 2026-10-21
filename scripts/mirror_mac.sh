#!/bin/bash
# Start the Bybit demo mirror on the Mac with one command:   bash scripts/mirror_mac.sh
# - keys are read from ~/.cryptonova.env (lives only on this Mac, never in the repo):
#     export BYBIT_API_KEY=...
#     export BYBIT_API_SECRET=...
#     export CNQ_GH_TOKEN=...
# - optional, private monitor «Иван тест» (cnq/execution/ivan_watch.py). Add these lines only if you want it;
#   without them the mirror runs exactly as before:
#     export IVAN_BYBIT_KEY=...      # API key of the «Иван тест» Bybit account, created as READ-ONLY
#     export IVAN_BYBIT_SECRET=...   # its secret
#     export IVAN_PASS=...           # long passphrase (4+ random words); the panel asks for it. The encrypted file
#                                    # web/ivan.enc.json is public, only this passphrase protects it. Never send it to anyone.
#   history of that account is kept in ~/.cryptonova_ivan.json (outside the repo)
# - optional, private card «Мой портфель · OKX» (cnq/execution/myspot_watch.py) — your own OKX spot portfolio with the model's
#   plan, visible only in your panel. Add these 4 lines only if you want it:
#     export MYSPOT_OKX_KEY=...         # OKX API key created with the «Read» permission ONLY (no trade, no withdraw)
#     export MYSPOT_OKX_SECRET=...      # its secret key
#     export MYSPOT_OKX_PASSPHRASE=...  # the passphrase you typed when creating that key
#     export MYSPOT_PASS=...            # the panel login password (then the panel opens the card by itself)
#   the file web/myspot.enc.json is public but encrypted; only MYSPOT_PASS opens it. Nothing is traded.
# - optional «Автопилот OKX» (cnq/execution/okx_autopilot.py) — your OWN OKX spot account follows the same model by itself.
#   A SEPARATE key: «Чтение» + «Торговля», NEVER «Вывод», bound to this Mac's IP. Steps: claude/cryptonova-okx-autopilot-2026-10-22.md
#     export OKX_API_KEY=...            # the trade key (read + trade, no withdraw)
#     export OKX_API_SECRET=...
#     export OKX_API_PASSPHRASE=...
#     export OKX_AUTOPILOT=on           # after the first manual run the autopilot window rebalances by itself (model change, Mondays)
#   check / plan / run / stop:  python3 -m cnq.execution.okx_autopilot --check | --plan | --run | --stop
#   Real money: CNQ_LIVE_TRADING=yes ONLY on the autopilot's own command line, in its own window:
#     bash scripts/okx_autopilot_mac.sh        (never put CNQ_LIVE_TRADING=yes into this file: this mirror would read it too)
# - caffeinate keeps the Mac awake while the mirror runs (keep it plugged in; closing the lid on battery still sleeps)
# - if the mirror stops (network, error), it restarts by itself after 30 s. Stop: Ctrl+C twice.
cd "$(dirname "$0")/.." || exit 1
[ -f ~/.cryptonova.env ] && source ~/.cryptonova.env
for v in BYBIT_API_KEY BYBIT_API_SECRET CNQ_GH_TOKEN; do
  if [ -z "${!v}" ]; then echo "Нет $v. Открой файл ключей командой:  open -e ~/.cryptonova.env  — и впиши строку: export $v=..."; touch ~/.cryptonova.env; exit 1; fi
done
git pull -q 2>/dev/null || echo "(git pull не удался — работаю с текущей версией)"
python3 -m cnq.execution.mirror --check || { echo "Проверка не прошла — пришли этот текст в чат."; exit 1; }
while true; do
  echo "$(date '+%H:%M') зеркало запущено. Окно не закрывай."
  caffeinate -i python3 -m cnq.execution.mirror
  echo "$(date '+%H:%M') зеркало остановилось — перезапуск через 30 секунд (Ctrl+C — выйти)"
  sleep 30
done
