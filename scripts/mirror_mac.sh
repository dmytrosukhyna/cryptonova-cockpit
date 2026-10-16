#!/bin/bash
# Start the Bybit demo mirror on the Mac with one command:   bash scripts/mirror_mac.sh
# - keys are read from ~/.cryptonova.env (lives only on this Mac, never in the repo):
#     export BYBIT_API_KEY=...
#     export BYBIT_API_SECRET=...
#     export CNQ_GH_TOKEN=...
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
