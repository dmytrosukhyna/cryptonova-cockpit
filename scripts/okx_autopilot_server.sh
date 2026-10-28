#!/bin/bash
# «Автопилот OKX» на сервере — 24/7 без Мака. ПОДГОТОВЛЕНО И ВЫКЛЮЧЕНО: само ничего не запускается (ни git pull, ни
# авто-обновление сервера). Включает только владелец, руками, на сервере под root:
#   bash /opt/cnq/repo/scripts/okx_autopilot_server.sh install     проверки → ДА → служба cnq-okx-autopilot установлена и запущена
#   bash /opt/cnq/repo/scripts/okx_autopilot_server.sh status      служба, замок (где работает автопилот), последние строки журнала
#   bash /opt/cnq/repo/scripts/okx_autopilot_server.sh stop        остановить (и не запускать после перезагрузки)
#   bash /opt/cnq/repo/scripts/okx_autopilot_server.sh start       запустить снова
#   bash /opt/cnq/repo/scripts/okx_autopilot_server.sh uninstall   убрать службу, вернуть автопилот на Мак
#   bash /opt/cnq/repo/scripts/okx_autopilot_server.sh monitors    28.10: мониторы «Иван тест» и «Мой портфель» на сервере (только чтение)
#   bash /opt/cnq/repo/scripts/okx_autopilot_server.sh monitors-stop  выключить мониторы на сервере
# One autopilot per account: the lease (cnq/execution/okx_lease.py) — install moves it to «server» only when no Mac window holds it.
# Its own env file /etc/cnq-okx.env (deploy/okx/cnq-okx.env.example); CNQ_LIVE_TRADING=yes only there, never in /etc/cnq.env.
# Env for tests only: CNQ_OKX_TEST=1 and the CNQ_OKX_* / CNQ_* paths below.
set -u
REPO="${CNQ_OKX_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
UNIT=cnq-okx-autopilot
MON=cnq-okx-monitors
ENVF="${CNQ_OKX_ENV:-/etc/cnq-okx.env}"
MAINENV="${CNQ_MAIN_ENV:-/etc/cnq.env}"
STATE="${CNQ_OKX_STATE:-/var/lib/cnq-okx}"
UNITDIR="${CNQ_UNIT_DIR:-/etc/systemd/system}"
PY="${CNQ_OKX_PY:-/opt/cnq/venv/bin/python}"
SYSTEMCTL="${CNQ_SYSTEMCTL:-systemctl}"
FROM="${CNQ_OKX_COPY_FROM:-/root}"
TEST="${CNQ_OKX_TEST:-}"
cmd="${1:-status}"

die() { echo "$*"; exit 1; }
need_root() { [ -n "$TEST" ] || [ "$(id -u)" = 0 ] || die "Нужен root: ssh root@91.99.193.20, потом эта же команда."; }
val() { grep -E "^[[:space:]]*$1=" "$ENVF" 2>/dev/null | tail -1 | sed -E "s/^[[:space:]]*$1=//; s/^['\"]//; s/['\"][[:space:]]*$//"; }
run_py() {   # python with exactly the service's environment (systemd reads /etc/cnq-okx.env the same way as for the service)
  if [ -n "$TEST" ]; then
    ( set -a; . "$ENVF"; set +a; HOME="$STATE" OKX_NODE=server OKX_LEASE_ID=server PYTHONPATH="$REPO" "$PY" "$@" )
  else
    systemd-run --quiet --pipe --wait --collect --uid=cnq --gid=cnq -p EnvironmentFile="$ENVF" -p WorkingDirectory="$STATE" \
      -E HOME="$STATE" -E OKX_NODE=server -E OKX_LEASE_ID=server -E PYTHONPATH="$REPO" -E PYTHONDONTWRITEBYTECODE=1 "$PY" "$@"
  fi
}

check_env() {
  [ -f "$ENVF" ] || { echo "Нет файла $ENVF. Создайте его (nano $ENVF), строки — как в образце:"; cat "$REPO/deploy/okx/cnq-okx.env.example"; exit 1; }
  [ -n "$TEST" ] || { chown root:root "$ENVF"; chmod 600 "$ENVF"; }
  grep -Eq '^[[:space:]]*export[[:space:]]' "$ENVF" && die "В $ENVF строки пишутся без слова export: KEY=значение. Исправьте и повторите."
  for k in OKX_API_KEY OKX_API_SECRET OKX_API_PASSPHRASE; do [ -n "$(val $k)" ] || die "В $ENVF нет строки $k=… (значение из ~/.cryptonova.env на Маке)."; done
  [ "$(val OKX_AUTOPILOT)" = "on" ] || die "В $ENVF нужна строка OKX_AUTOPILOT=on."
  [ "$(val CNQ_LIVE_TRADING)" = "yes" ] || die "В $ENVF нужна строка CNQ_LIVE_TRADING=yes (только в этом файле)."
  for k in OKX_NODE OKX_LEASE_ID OKX_LEASE_FILE OKX_LEASE_BRANCH HOME OKX_AUTOPILOT_STATE OKX_FUT_STATE OKX_ACT_STATE OKX_STOP_FILE; do
    grep -Eq "^[[:space:]]*$k=" "$ENVF" && die "Уберите из $ENVF строку $k — её задаёт служба."
  done
  if grep -Eq '^[[:space:]]*(export[[:space:]]+)?CNQ_LIVE_TRADING=' "$MAINENV" 2>/dev/null; then
    die "В $MAINENV есть строка CNQ_LIVE_TRADING — уберите её: этот файл читают движок и API. Реальные деньги — только в $ENVF."
  fi
  if [ -z "$(val CNQ_GH_TOKEN)" ]; then
    line=$(grep -E '^CNQ_GH_TOKEN=.+' "$MAINENV" 2>/dev/null | tail -1)
    [ -n "$line" ] || die "В $ENVF нет строки CNQ_GH_TOKEN=… (токен GitHub, тот же, что в $MAINENV) — без него нет замка."
    sed -i -E '/^[[:space:]]*CNQ_GH_TOKEN=/d' "$ENVF"; echo "$line" >> "$ENVF"
    echo "Строку CNQ_GH_TOKEN взял из $MAINENV (нужна для замка: один автопилот на счёт)."
  fi
}

case "$cmd" in
  install)
    need_root
    [ -d "$REPO/cnq/execution" ] || die "Нет кода CRYPTO NOVA в $REPO."
    [ -x "$PY" ] || die "Нет Python движка ($PY) — сначала установите движок: deploy/engine/install.sh."
    check_env
    mkdir -p "$STATE/data"
    for f in .cryptonova_okx_autopilot.json .cryptonova_okx_futures.json .cryptonova_okx_active.json .cryptonova_public_real.json; do
      if [ -f "$FROM/$f" ]; then cp "$FROM/$f" "$STATE/$f" && mv "$FROM/$f" "$FROM/$f.copied" && echo "Состояние с Мака: $f"; fi
    done
    [ -n "$TEST" ] || { chown -R cnq:cnq "$STATE"; chmod 700 "$STATE"; chmod 600 "$STATE"/.cryptonova_*.json 2>/dev/null; }
    [ -f "$STATE/.cryptonova_okx_autopilot.json" ] || die "Нет файлов состояния автопилота с Мака. Сначала закройте окно автопилота на Маке (Ctrl+C), потом на Маке:
  scp ~/.cryptonova_okx_*.json ~/.cryptonova_public_real.json root@91.99.193.20:/root/
и повторите install."
    grep -Eq '"armed": ?true' "$STATE/.cryptonova_okx_autopilot.json" || die "Автопилот в этом состоянии не включён (нет первого run --live на Маке) — сначала запустите его на Маке."
    echo "Проверка ключа с сервера (ордеров нет):"
    out=$(run_py -m cnq.execution.okx_autopilot --check 2>&1); echo "$out"
    echo "$out" | grep -q "ключ OKX_API_KEY работает" || die "С сервера ключ не работает — смотрите строку выше. Чаще всего: IP сервера 91.99.193.20 не добавлен в настройках ключа на OKX."
    echo "$out" | grep -q "РЕАЛЬНОГО счёта" || die "Это не ключ реального счёта — сервер с ним работать не будет."
    echo
    echo "Сервер будет торговать РЕАЛЬНЫМИ деньгами на субаккаунте OKX круглые сутки, как окно автопилота на Маке:"
    echo "  спот — как «CRYPTO NOVA портфель»; фьючерсы — изолированная маржа, стоп-лосс у каждой позиции; без вывода."
    echo "  Окно автопилота на Маке после этого ордеров не ставит (замок). Остановить: stop здесь или «CRYPTO NOVA стоп» на Маке."
    read -r -p "Напишите ДА (большими буквами) и нажмите Enter: " answer
    [ "$answer" = "ДА" ] || die "Отменено — служба не установлена, ничего не запущено."
    run_py -m cnq.execution.okx_lease --switch server || die "Замок не переведён на сервер — служба не установлена."
    install -m 644 "$REPO/deploy/okx/$UNIT.service" "$UNITDIR/$UNIT.service" || die "Не удалось записать $UNITDIR/$UNIT.service."
    $SYSTEMCTL daemon-reload
    $SYSTEMCTL enable --now "$UNIT" || die "Служба не запустилась — пришлите вывод: bash $0 status"
    echo "Готово: автопилот работает на сервере. Проверка: bash $0 status. Окно автопилота на Маке можно не открывать."
    ;;
  start)
    need_root
    [ -f "$UNITDIR/$UNIT.service" ] || die "Служба не установлена — сначала install."
    $SYSTEMCTL enable --now "$UNIT" && echo "Автопилот на сервере запущен." ;;
  stop)
    need_root
    $SYSTEMCTL disable --now "$UNIT" && echo "Автопилот на сервере остановлен (и не запустится после перезагрузки). Позиции — со стопами на OKX. Запустить: start." ;;
  status)
    $SYSTEMCTL status "$UNIT" --no-pager -n 0 2>/dev/null || echo "Служба $UNIT не работает или не установлена."
    [ -f "$ENVF" ] && run_py -m cnq.execution.okx_lease --status
    command -v journalctl >/dev/null && journalctl -u "$UNIT" -n 15 --no-pager 2>/dev/null
    # 28.10: the read-only monitors («Иван тест», «Мой портфель») — their timer and the last pass
    $SYSTEMCTL status "$MON.timer" --no-pager -n 0 2>/dev/null || echo "Мониторы ($MON.timer) не установлены — включить: bash $0 monitors"
    command -v journalctl >/dev/null && journalctl -u "$MON.service" -n 6 --no-pager 2>/dev/null ;;
  check)        # 22.10: the same --check as on the Mac, with the service's own environment (keys, lead role, profile link) — places nothing
    [ -f "$ENVF" ] || die "Нет файла $ENVF."
    run_py -m cnq.execution.okx_autopilot --check
    run_py -m cnq.execution.monitors --check ;;      # 28.10: the monitors' keys (read-only?) and where they publish — publishes nothing
  monitors)     # 28.10 (owner): «Иван тест» and «Мой портфель» on the server — read-only, a timer every 15 minutes, no ДА needed (no trading)
    need_root
    [ -d "$REPO/cnq/execution" ] || die "Нет кода CRYPTO NOVA в $REPO."
    [ -x "$PY" ] || die "Нет Python движка ($PY) — сначала установите движок: deploy/engine/install.sh."
    [ -f "$ENVF" ] || die "Нет файла $ENVF — сначала установите автопилот на сервере (install)."
    [ -n "$TEST" ] || { chown root:root "$ENVF"; chmod 600 "$ENVF"; }
    grep -Eq '^[[:space:]]*export[[:space:]]' "$ENVF" && die "В $ENVF строки пишутся без слова export: KEY=значение. Исправьте и повторите."
    for k in HOME OKX_NODE IVAN_STATE MYSPOT_STATE; do
      grep -Eq "^[[:space:]]*$k=" "$ENVF" && die "Уберите из $ENVF строку $k — её задаёт служба."
    done
    [ -n "$(val CNQ_GH_TOKEN)" ] || die "В $ENVF нет строки CNQ_GH_TOKEN=… — публиковать нечем (её добавляет install автопилота)."
    have=""
    if [ -n "$(val IVAN_BYBIT_KEY)" ] && [ -n "$(val IVAN_BYBIT_SECRET)" ] && [ -n "$(val IVAN_PASS)" ]; then have="$have Иван"; fi
    if [ -n "$(val MYSPOT_OKX_KEY)" ] && [ -n "$(val MYSPOT_OKX_SECRET)" ] && [ -n "$(val MYSPOT_OKX_PASSPHRASE)" ] && [ -n "$(val MYSPOT_PASS)" ]; then have="$have Портфель"; fi
    [ -n "$have" ] || die "В $ENVF нет ни строк IVAN_BYBIT_KEY / IVAN_BYBIT_SECRET / IVAN_PASS, ни MYSPOT_OKX_KEY / MYSPOT_OKX_SECRET / MYSPOT_OKX_PASSPHRASE / MYSPOT_PASS — включать нечего."
    mkdir -p "$STATE"
    for f in .cryptonova_ivan.json .cryptonova_myspot.json; do
      if [ -f "$FROM/$f" ]; then cp "$FROM/$f" "$STATE/$f" && mv "$FROM/$f" "$FROM/$f.copied" && echo "Состояние с Мака: $f"; fi
    done
    [ -n "$TEST" ] || { chown -R cnq:cnq "$STATE"; chmod 700 "$STATE"; chmod 600 "$STATE"/.cryptonova_*.json 2>/dev/null; }
    echo "Проверка ключей мониторов с сервера (только чтение, ничего не публикуется):"
    out=$(run_py -m cnq.execution.monitors --check 2>&1); echo "$out"
    echo "$out" | grep -q "ОТКАЗ" && die "Мониторы не включены — исправьте строку с ОТКАЗ выше и повторите."
    install -m 644 "$REPO/deploy/okx/$MON.service" "$UNITDIR/$MON.service" || die "Не удалось записать $UNITDIR/$MON.service."
    install -m 644 "$REPO/deploy/okx/$MON.timer" "$UNITDIR/$MON.timer" || die "Не удалось записать $UNITDIR/$MON.timer."
    $SYSTEMCTL daemon-reload
    $SYSTEMCTL enable --now "$MON.timer" || die "Таймер не запустился — пришлите вывод: bash $0 status"
    echo "Готово: мониторы ($have) работают на сервере каждые 15 минут, только чтение. Зеркало на Маке их больше не публикует (замок)." ;;
  monitors-stop)
    need_root
    $SYSTEMCTL disable --now "$MON.timer" && echo "Мониторы на сервере выключены (и не запустятся после перезагрузки). Включить: monitors." ;;
  plan)         # every order the next pass would place — places nothing
    [ -f "$ENVF" ] || die "Нет файла $ENVF."
    run_py -m cnq.execution.okx_autopilot --plan ;;
  uninstall)
    need_root
    $SYSTEMCTL disable --now "$UNIT" 2>/dev/null
    $SYSTEMCTL disable --now "$MON.timer" 2>/dev/null
    rm -f "$UNITDIR/$UNIT.service" "$UNITDIR/$MON.service" "$UNITDIR/$MON.timer"; $SYSTEMCTL daemon-reload
    [ -f "$ENVF" ] && run_py -m cnq.execution.okx_lease --switch mac
    echo "Служба убрана, автопилот снова на Маке. Состояние осталось в $STATE. Перенести его на Мак (на Маке):"
    echo "  scp 'root@91.99.193.20:$STATE/.cryptonova_*.json' ~/   — потом двойной щелчок «CRYPTO NOVA»." ;;
  *) echo "Команды: install | status | check | plan | stop | start | uninstall | monitors | monitors-stop" ;;
esac
