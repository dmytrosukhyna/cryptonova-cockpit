#!/usr/bin/env bash
# CRYPTO NOVA API v2 — one-time install on the server (run as root after the engine is installed):
#   bash /opt/cnq/repo/deploy/api/install_api.sh
# 1) libraries into the engine's venv  2) systemd service cnq-api  3) nginx: app.crypto-capital.app/v2/ → the API
# Safe: nginx is checked with `nginx -t`; on any error the old config is put back.
set -euo pipefail
REPO=/opt/cnq/repo
/opt/cnq/venv/bin/pip install -q "pyjwt>=2.8" "cryptography>=42" "edge-tts>=7"   # edge-tts: NOVA's voice (build 46, cnq/api/tts.py)
install -m 644 "$REPO/deploy/api/cnq-api.service" /etc/systemd/system/cnq-api.service
systemctl daemon-reload
systemctl enable --now cnq-api
sleep 2
curl -fsS http://127.0.0.1:8090/v2/health && echo "  ← API answers locally"

install -m 644 "$REPO/deploy/api/cnq-v2.nginx.conf" /etc/nginx/snippets/cnq-v2.conf
CONF=$(grep -rlE "server_name[^;]*app\.crypto-capital\.app" /etc/nginx/sites-enabled /etc/nginx/conf.d 2>/dev/null | head -1 || true)
if [ -z "$CONF" ]; then echo "nginx: no server block for app.crypto-capital.app found — send this output to Claude"; exit 1; fi
CONF=$(readlink -f "$CONF")
if grep -q "snippets/cnq-v2.conf" "$CONF"; then echo "nginx: already included in $CONF"; else
  cp "$CONF" "$CONF.bak-cnq"
  # after every server_name line that names app.crypto-capital.app (both the 80 and 443 blocks)
  sed -i -E '/server_name[^;]*app\.crypto-capital\.app[^;]*;/a\    include snippets/cnq-v2.conf;' "$CONF"
  if nginx -t; then systemctl reload nginx; echo "nginx: /v2/ added to $CONF"; else
    mv "$CONF.bak-cnq" "$CONF"; nginx -t && systemctl reload nginx; echo "nginx: test failed, old config restored — send this output to Claude"; exit 1; fi
fi
curl -fsS https://app.crypto-capital.app/v2/health && echo "  ← API answers from the internet"
