#!/bin/bash
# Access codes for guests:  bash scripts/access_code.sh add "Имя"  |  list  |  remove "Имя"
cd "$(dirname "$0")/.." || exit 1
[ -f ~/.cryptonova.env ] && source ~/.cryptonova.env
git pull -q 2>/dev/null
python3 -m cnq.execution.access_codes "$@"
