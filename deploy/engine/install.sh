#!/usr/bin/env bash
# CRYPTO NOVA fast engine — one-time install on the Ubuntu server (as root):
#   curl -fsSL https://raw.githubusercontent.com/dmytrosukhyna/cryptonova-cockpit/main/deploy/engine/install.sh | bash
# Then: nano /etc/cnq.env (2 values) and run start.sh (see README.md).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq sudo git python3 python3-venv python3-pip >/dev/null
id cnq >/dev/null 2>&1 || useradd --system --home-dir /opt/cnq --shell /usr/sbin/nologin cnq
mkdir -p /opt/cnq
chown cnq:cnq /opt/cnq
if [ ! -d /opt/cnq/repo/.git ]; then
  sudo -u cnq git clone -q https://github.com/dmytrosukhyna/cryptonova-cockpit.git /opt/cnq/repo
fi
sudo -u cnq python3 -m venv /opt/cnq/venv
sudo -u cnq /opt/cnq/venv/bin/pip install -q --upgrade pip
sudo -u cnq /opt/cnq/venv/bin/pip install -q numpy pillow
if [ ! -f /etc/cnq.env ]; then
  install -m 600 -o cnq -g cnq /opt/cnq/repo/deploy/engine/cnq.env.example /etc/cnq.env
fi
install -m 644 /opt/cnq/repo/deploy/engine/cnq-engine.service /etc/systemd/system/cnq-engine.service
systemctl daemon-reload
echo
echo "Готово. Дальше:"
echo "  1) nano /etc/cnq.env   — вписать CNQ_GH_TOKEN и CNQ_TG_TOKEN"
echo "  2) bash /opt/cnq/repo/deploy/engine/start.sh"
