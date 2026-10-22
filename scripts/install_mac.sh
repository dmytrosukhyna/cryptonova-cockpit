#!/bin/bash
# CRYPTO NOVA on the Mac with one double-click (22.10.2026). Run once:   bash ~/cryptonova-cockpit/scripts/install_mac.sh
# It puts two files on the Desktop (running it again just rewrites them — safe to repeat):
#   «CRYPTO NOVA.command»       git pull, then opens the mirror window (scripts/mirror_mac.sh) and the OKX autopilot window
#                               (scripts/okx_autopilot_mac.sh loop --live — it still asks you to type ДА). A window that is
#                               already running is never opened a second time (checked by its process). If the autopilot was
#                               moved to the server (scripts/okx_autopilot_server.sh), only the mirror window opens.
#   «CRYPTO NOVA стоп.command»  the autopilot's stop: no new orders anywhere (the Mac window and the server), open spot orders
#                               of the autopilot cancelled; futures keep their stop-losses on OKX.
# Nothing secret is written into these files: the keys stay in ~/.cryptonova.env. Works with the Mac's own bash 3.2.
# Env for tests only: CNQ_DESKTOP (where to write), CNQ_REPO (the repository folder written into the files).
set -u
REPO="${CNQ_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
DESK="${CNQ_DESKTOP:-$HOME/Desktop}"
if [ ! -f "$REPO/scripts/mirror_mac.sh" ] || [ ! -f "$REPO/scripts/okx_autopilot_mac.sh" ]; then
  echo "Не нашёл папку CRYPTO NOVA ($REPO). Запустите так:  bash ~/cryptonova-cockpit/scripts/install_mac.sh"; exit 1
fi
mkdir -p "$DESK" || { echo "Нет папки $DESK"; exit 1; }
# the repository folder, single-quoted for the generated scripts (a ' inside the path is written as '\'')
Q="'$(printf '%s' "$REPO" | sed "s/'/'\\\\''/g")'"
START="$DESK/CRYPTO NOVA.command"
STOP="$DESK/CRYPTO NOVA стоп.command"

cat > "$START.tmp" <<EOF
#!/bin/bash
# CRYPTO NOVA — one double-click: fresh code, then the mirror window and the OKX autopilot window (made by scripts/install_mac.sh).
REPO=$Q
cd "\$REPO" || { echo "Нет папки \$REPO — запустите заново: bash ~/cryptonova-cockpit/scripts/install_mac.sh"; read -r -p "Enter — закрыть окно. " _; exit 1; }
echo "CRYPTO NOVA: обновляю код…"
git pull || echo "Обновить код не получилось — запускаю то, что уже есть на Маке."
[ -f ~/.cryptonova.env ] && source ~/.cryptonova.env
unset CNQ_LIVE_TRADING
running() { pgrep -f "\$1" >/dev/null 2>&1; }
open_window() {   # a new Terminal window: cd into the repository, then the command
  osascript - "\$REPO" "\$1" <<'APPLESCRIPT' >/dev/null
on run argv
  tell application "Terminal"
    activate
    do script "cd " & quoted form of (item 1 of argv) & " && " & (item 2 of argv)
  end tell
end run
APPLESCRIPT
}
if running "scripts/mirror_mac.sh" || running "cnq.execution.mirror"; then
  echo "Зеркало уже работает — второе окно не открываю."
else
  open_window "bash scripts/mirror_mac.sh" && echo "Открыл окно зеркала."
fi
if [ -z "\${OKX_API_KEY:-}" ]; then
  echo "Ключа автопилота OKX нет (строка OKX_API_KEY в ~/.cryptonova.env) — окно автопилота не открываю."
elif running "okx_autopilot_mac.sh loop" || running "cnq.execution.okx_autopilot --loop"; then
  echo "Автопилот уже работает — второе окно не открываю."
elif [ "\$(python3 -m cnq.execution.okx_lease --node 2>/dev/null)" = "server" ]; then
  echo "Автопилот работает на сервере, круглые сутки — окно автопилота на Маке не нужно."
else
  [ -f ~/.cryptonova_okx_stop ] && echo "Внимание: автопилот на стопе. Снять стоп: bash scripts/okx_autopilot_mac.sh resume, потом run --live."
  open_window "bash scripts/okx_autopilot_mac.sh loop --live" && echo "Открыл окно автопилота — напишите там ДА и нажмите Enter."
fi
echo "Готово. Это окно можно закрыть."
EOF

cat > "$STOP.tmp" <<EOF
#!/bin/bash
# CRYPTO NOVA — stop the OKX autopilot (made by scripts/install_mac.sh). No new orders anywhere; futures keep their stops on OKX.
REPO=$Q
cd "\$REPO" || { echo "Нет папки \$REPO"; read -r -p "Enter — закрыть окно. " _; exit 1; }
echo "Останавливаю автопилот OKX…"
bash scripts/okx_autopilot_mac.sh stop
echo
echo "Открытые фьючерсы остались со своими стоп-лоссами на OKX. Закрыть их сейчас: bash scripts/okx_autopilot_mac.sh fut-close --live"
echo "Включить автопилот снова: bash scripts/okx_autopilot_mac.sh resume, потом run --live."
read -r -p "Enter — закрыть окно. " _
EOF

for f in "$START" "$STOP"; do
  if [ -f "$f" ] && cmp -s "$f.tmp" "$f"; then
    rm -f "$f.tmp"; echo "Уже на месте: $(basename "$f")"
  else
    mv "$f.tmp" "$f"; echo "Готово: $(basename "$f")"
  fi
  chmod +x "$f"
done
echo "На рабочем столе: «CRYPTO NOVA» — запуск двойным щелчком, «CRYPTO NOVA стоп» — остановить автопилот."
echo "Первый запуск: если Мак спросит, разрешите Терминалу управлять Терминалом (Системные настройки → Конфиденциальность → Автоматизация)."
