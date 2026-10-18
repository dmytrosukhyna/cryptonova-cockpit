#!/bin/bash
# «Инвестировать»: the CRYPTO NOVA spot portfolio on your Bybit account (DEMO unless CNQ_LIVE_TRADING=yes in ~/.cryptonova.env).
#   bash scripts/spot_invest.sh          → shows what it would buy / sell, places nothing
#   bash scripts/spot_invest.sh --run    → places the orders once
#   bash scripts/spot_invest.sh --run --loop 900   → keeps the account in line every 15 minutes
# Keys come from ~/.cryptonova.env (BYBIT_API_KEY / BYBIT_API_SECRET) and never leave this Mac.
cd "$(dirname "$0")/.." || exit 1
[ -f ~/.cryptonova.env ] && source ~/.cryptonova.env
git pull -q 2>/dev/null
python3 -m cnq.execution.spot_mirror "$@"
