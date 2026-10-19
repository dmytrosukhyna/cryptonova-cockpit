#!/usr/bin/env bash
# CRYPTO NOVA fast engine — first start (as root). Takes the newest state from GitHub, loads 400 days of candles once
# (10–20 minutes), then starts the service that works every minute. Safe to run again (updates and restarts).
set -euo pipefail
set -a; . /etc/cnq.env; set +a
if [ -z "${CNQ_GH_TOKEN:-}" ] || [ -z "${CNQ_TG_TOKEN:-}" ]; then echo "Впишите CNQ_GH_TOKEN и CNQ_TG_TOKEN в /etc/cnq.env"; exit 1; fi
R=/opt/cnq/repo
sudo -u cnq git -C $R config user.name "cnq-engine"
sudo -u cnq git -C $R config user.email "cnq-engine@users.noreply.github.com"
sudo -u cnq git -C $R remote set-url origin "https://x-access-token:${CNQ_GH_TOKEN}@github.com/dmytrosukhyna/cryptonova-cockpit.git"
sudo -u cnq git -C $R pull -q --rebase
FIRST=0; [ -f /opt/cnq/run/data/agents.sqlite ] || FIRST=1
sudo -u cnq env CNQ_REPO_DIR=$R CNQ_RUN_DIR=/opt/cnq/run PYTHONPATH=$R /opt/cnq/venv/bin/python -c "from cnq import engine; engine.prepare()"
if [ "$FIRST" = 1 ]; then
  echo "Первый запуск: свечи за 400 дней (10–20 минут, один раз)…"
  cd /opt/cnq/run && sudo -u cnq env CNQ_REFRESH=full PYTHONPATH=/opt/cnq/run /opt/cnq/venv/bin/python scripts/ci_refresh.py | tail -3
fi
systemctl enable -q cnq-engine
systemctl restart cnq-engine
sleep 90
journalctl -u cnq-engine -n 5 --no-pager
echo "Движок работает. GitHub сам увидит его (web/engine.json) и перестанет запускать эти столы у себя."
echo "Статус: systemctl status cnq-engine · журнал: journalctl -u cnq-engine -f"
