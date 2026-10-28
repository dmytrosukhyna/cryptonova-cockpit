"""Read-only monitors on the server (28.10.2026, owner's decision: «перенести монитор Ивана и «Мой портфель» с Мака на сервер»).

Until 22.10 the Mac mirror (scripts/mirror_mac.sh → cnq/execution/mirror.py) ran them; since the autopilot moved to the server the
Mac is off and web/ivan.enc.json, web/myspot.enc.json stopped on 22.10 11:57 UTC. Here they run on the server, as a systemd timer
of their own (deploy/okx/cnq-okx-monitors.{service,timer}, every 15 minutes) — apart from the autopilot service, so a slow Bybit
or OKX answer never holds an order cycle:

  • «Иван тест» (ivan_watch.py) — the Bybit account connected to Ivan's platform, read with a READ-ONLY key → the encrypted
    web/ivan.enc.json (IVAN_PASS) and the stats-only web/ivan_stats.json;
  • «Мой портфель · OKX» (myspot_watch.py) — the owner's personal OKX account (READ-ONLY key) and, inside the same encrypted
    web/myspot.enc.json (MYSPOT_PASS), the CRYPTO NOVA sub-account read with GET requests only (myspot_sub.py).
  The files are published exactly as the Mac did (same encryption, same allow-listed publishers).

GET only: the three readers use clients that can send GET requests and nothing else (ivan_watch.make_fetch, myspot_watch.make_fetch;
tests/test_myspot_sub.py and tests/test_vip_feeds.py check the sources). This module never calls an order path.
One writer: a monitor publishes only on the machine the lease names (okx_lease «node»: mac | server) — the same rule as
web/public_real.json (public_real.writer). The Mac mirror checks the same before its own ticks, so the two never both write; an
unreadable lease → nobody writes.

  python -m cnq.execution.monitors --once     one pass (the timer)
  python -m cnq.execution.monitors --check    the keys (read-only?), the lease's node; publishes nothing

Env (/etc/cnq-okx.env on the server; the owner adds them himself):
  IVAN_BYBIT_KEY, IVAN_BYBIT_SECRET, IVAN_PASS                          «Иван тест» (Bybit key «Read-Only»)
  MYSPOT_OKX_KEY, MYSPOT_OKX_SECRET, MYSPOT_OKX_PASSPHRASE, MYSPOT_PASS  «Мой портфель» (OKX key with «Чтение» only)
  CNQ_GH_TOKEN (already there: publishing and the lease), OKX_API_* (already there: the sub-account, read with GET only).
Stdlib only."""
from __future__ import annotations

import os
import sys
import time

from . import ivan_watch, myspot_watch

REFUSE = "ОТКАЗ"          # a line the install script stops on: a monitor key that can trade


def _env(env=None) -> dict:
    return os.environ if env is None else env


def writer(env=None) -> tuple[bool, str]:
    """(may this machine publish the monitors' files, why) — the lease's node must be this machine (OKX_NODE)"""
    try:
        from . import okx_lease
        s = okx_lease.status(env)
    except Exception as e:  # noqa: BLE001
        return False, f"замок не прочитан ({str(e)[:80]}) — никто не публикует"
    if s is None:
        return False, "нет строки CNQ_GH_TOKEN — замок не прочитать, никто не публикует"
    if s.get("error"):
        return False, f"замок не прочитан ({s['error']}) — никто не публикует"
    me = okx_lease.node_of(env)
    if s.get("node") != me:
        return False, f"мониторы публикует {'сервер' if s.get('node') == 'server' else 'Мак'} (замок), здесь ({me}) — нет"
    return True, f"мониторы публикует эта машина ({me})"


def once(env=None, ivan_run=None, myspot_run=None) -> list[str]:
    """One pass: each configured monitor once. A failure is one line, never an exception."""
    ok, why = writer(env)
    if not ok:
        return ["Мониторы: " + why]
    out = []
    if ivan_watch.creds(env):
        try:
            r = (ivan_run or (lambda: ivan_watch.run(env=env)))()
            st = (r or {}).get("stats") or {}
            out.append(f"Иван тест: опубликовано (закрытых сделок {st.get('n')}, открытых позиций {len((r or {}).get('positions') or [])})")
        except Exception as e:  # noqa: BLE001
            out.append(ivan_watch.describe_error(e))
    else:
        out.append("Иван тест: нет строк IVAN_BYBIT_KEY / IVAN_BYBIT_SECRET / IVAN_PASS — пропускаю")
    if myspot_watch.creds(env):
        try:
            r = (myspot_run or (lambda: myspot_watch.run(env=env)))()
            out.append(f"Мой портфель OKX: опубликовано (активов {len((r or {}).get('assets') or [])}"
                       + (", субаккаунт CRYPTO NOVA добавлен" if (r or {}).get("sub") else "") + ")")
        except Exception as e:  # noqa: BLE001
            out.append(myspot_watch.describe_error(e))
    else:
        out.append("Мой портфель OKX: нет строк MYSPOT_OKX_KEY / MYSPOT_OKX_SECRET / MYSPOT_OKX_PASSPHRASE / MYSPOT_PASS — пропускаю")
    return out


def check(env=None, ivan_check=None, myspot_check=None) -> list[str]:
    """The keys work, are read-only and the passphrases are long enough; where the files are published. Publishes nothing.
    A monitor key that can trade → a line with REFUSE (the install script stops on it)."""
    out = ["Мониторы: " + writer(env)[1]]
    for name, fn, has in (("Иван тест", ivan_check or (lambda: ivan_watch.check(env)), ivan_watch.creds(env)),
                          ("Мой портфель OKX", myspot_check or (lambda: myspot_watch.check(env)), myspot_watch.creds(env))):
        if not has:
            out.append(f"{name}: строк ключа нет в файле — монитор выключен")
            continue
        try:
            msg = fn() or f"{name}: проверка не ответила"
        except Exception as e:  # noqa: BLE001
            msg = f"{name}: проверка не прошла — {str(e)[:120]}"
        if "право торговли" in msg:
            msg += f" · {REFUSE}: для монитора нужен ключ только на чтение — пересоздайте ключ без права торговли"
        out.append(msg)
    if not (_env(env).get("CNQ_GH_TOKEN") or "").strip():
        out.append(f"Нет строки CNQ_GH_TOKEN — публиковать нечем · {REFUSE}")
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--check" in argv:
        lines = check()
        print("\n".join(lines), flush=True)
        return 1 if any(REFUSE in ln for ln in lines) else 0
    if "--once" in argv:
        for ln in once():
            print(time.strftime("%H:%M ") + ln, flush=True)
        return 0
    print("python -m cnq.execution.monitors --once | --check")
    return 2


if __name__ == "__main__":
    sys.exit(main())
