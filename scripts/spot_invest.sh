#!/bin/bash
# «Инвестировать»: the CRYPTO NOVA spot portfolio on your Bybit account (DEMO unless CNQ_LIVE_TRADING=yes in ~/.cryptonova.env).
#   bash scripts/spot_invest.sh          → shows what it would buy / sell, places nothing
#   bash scripts/spot_invest.sh --run    → places the orders once
#   bash scripts/spot_invest.sh --run --loop 900   → keeps the account in line every 15 minutes
#   bash scripts/spot_invest.sh --portfolio --run --loop 900   → only the Портфолио's spot share in coins (60 % while BTC is above
#                                                     its 200-day average, 20 % below), the rest in USDT — the system rebalances itself
#   bash scripts/spot_invest.sh --exchange okx --portfolio        → the same on OKX (demo unless CNQ_LIVE_TRADING=yes)
#   … --keep SOL,LINK                                             → coins the mirror never touches
# Keys come from ~/.cryptonova.env (BYBIT_API_KEY / BYBIT_API_SECRET, OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE —
# trade permission only, never withdrawal) and never leave this Mac.
cd "$(dirname "$0")/.." || exit 1
[ -f ~/.cryptonova.env ] && source ~/.cryptonova.env
git pull -q 2>/dev/null
python3 -m cnq.execution.spot_mirror "$@"
