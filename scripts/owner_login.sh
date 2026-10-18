#!/bin/bash
# Set or change the owner's login and password for cryptonova.vip:   bash scripts/owner_login.sh
# Asks for a login and a password (typed twice, hidden), encrypts a small owner token with it and publishes
# web/owner.enc.json. The password itself is not saved anywhere. Needs CNQ_GH_TOKEN in ~/.cryptonova.env.
cd "$(dirname "$0")/.." || exit 1
[ -f ~/.cryptonova.env ] && source ~/.cryptonova.env
git pull -q 2>/dev/null
python3 -m cnq.execution.owner_setup
