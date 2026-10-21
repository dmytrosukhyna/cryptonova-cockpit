#!/bin/bash
# «Автопилот OKX» — your OWN OKX spot account follows the CRYPTO NOVA model (cnq/execution/okx_autopilot.py). Own Terminal window.
#   bash scripts/okx_autopilot_mac.sh check            keys, server, permissions, money — places nothing
#   bash scripts/okx_autopilot_mac.sh plan             the full list of sells and buys — places nothing
#   bash scripts/okx_autopilot_mac.sh run --live       one rebalance NOW with real money (asks to type ДА)
#   bash scripts/okx_autopilot_mac.sh loop --live      keeps the account on the model: checks once an hour (asks to type ДА)
#   bash scripts/okx_autopilot_mac.sh stop             kill switch: no more orders, open autopilot orders cancelled
#   bash scripts/okx_autopilot_mac.sh resume           removes the kill switch
# Keys: ~/.cryptonova.env (OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE — «Чтение» + «Торговля», never «Вывод»; OKX_AUTOPILOT=on).
# CNQ_LIVE_TRADING=yes is set HERE, for this window only, after you type ДА. Never put it into ~/.cryptonova.env: the Bybit futures
# mirror (scripts/mirror_mac.sh) reads that file too and would switch to a real account.
cd "$(dirname "$0")/.." || exit 1
[ -f ~/.cryptonova.env ] && source ~/.cryptonova.env
unset CNQ_LIVE_TRADING
cmd="${1:-check}"
for v in OKX_API_KEY OKX_API_SECRET OKX_API_PASSPHRASE; do
  if [ -z "${!v}" ] && [ "$cmd" != "stop" ] && [ "$cmd" != "resume" ]; then
    echo "Нет строки $v. Открой файл ключей:  open -e ~/.cryptonova.env  — и впиши: export $v=..."; exit 1
  fi
done
git pull -q 2>/dev/null || echo "(git pull не удался — работаю с текущей версией)"
if [ "$2" = "--live" ]; then
  echo "Автопилот будет ставить ордера РЕАЛЬНЫМИ деньгами на вашем счёте OKX: только спот, без плеча, без вывода."
  read -r -p "Напишите ДА (большими буквами) и нажмите Enter: " answer
  if [ "$answer" != "ДА" ]; then echo "Отменено — ничего не сделано."; exit 1; fi
  export CNQ_LIVE_TRADING=yes
fi
case "$cmd" in
  check)  python3 -m cnq.execution.okx_autopilot --check ;;
  plan)   python3 -m cnq.execution.okx_autopilot --plan ;;
  run)    python3 -m cnq.execution.okx_autopilot --run ;;
  stop)   python3 -m cnq.execution.okx_autopilot --stop ;;
  resume) python3 -m cnq.execution.okx_autopilot --resume ;;
  loop)
    python3 -m cnq.execution.okx_autopilot --check
    while true; do
      caffeinate -i python3 -m cnq.execution.okx_autopilot --loop
      echo "$(date '+%H:%M') окно автопилота остановилось — перезапуск через 60 секунд (Ctrl+C — выйти)"
      sleep 60
    done ;;
  *) echo "Команды: check | plan | run --live | loop --live | stop | resume" ;;
esac
