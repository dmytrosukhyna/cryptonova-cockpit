"""Команды риск-слоя (cnq/cli.py передаёт сюда всё, что начинается с kill или risk):

  python -m cnq.cli kill on --reason "…"     аварийный выключатель: новые входы запрещены на всех демо-счетах движка
  python -m cnq.cli kill off [--reason "…"]  снова можно входить (стопы и цели открытых позиций работают всегда)
  python -m cnq.cli kill status              включён ли, с какого времени, почему, последние переключения
  python -m cnq.cli risk [--out web]         шаг движка: посчитать риск → data/risk_state.json + web/risk.json
  python -m cnq.cli risk status              коротко: состояние, лимиты, блоки
  python -m cnq.cli risk reset --reason "…"  владелец снимает стоп по просадке (пик считается заново от текущего уровня)

Каждое переключение и сброс записываются в data/risk_log.jsonl."""
from __future__ import annotations

import argparse
import getpass
import sys
import time

from . import gate as G
from . import limits as L


def _who() -> str:
    try:
        return f"cli:{getpass.getuser()}"
    except Exception:  # noqa: BLE001
        return "cli"


def _print_state(base: str | None) -> None:
    st, err = G.read_json(L.path(L.STATE_FILE, base))
    if err:
        print("risk_state.json не читается:", err)
        return
    if not st:
        print("risk_state.json ещё нет — шаг risk не запускался")
        return
    age = (time.time() * 1000 - (st.get("updated_at") or 0)) / 60_000
    ix = st.get("index") or {}
    print(f"состояние: {st.get('state')} · обновлено {age:.1f} мин назад · день {(ix.get('daily') or 0) * 100:+.2f}% "
          f"({ix.get('day_pnl_usd')}$) · просадка {(ix.get('drawdown') or 0) * 100:+.2f}%" + (f" · ОШИБКА: {st['error']}" if st.get("error") else ""))
    for r in st.get("limits") or []:
        pct = "—" if r.get("pct") is None else f"{r['pct'] * 100:.0f}% лимита"
        print(f"  {r['status']:7} {r['key']:10} {r.get('value')} / {r.get('limit')} ({pct})")
    for b in st.get("blocks") or []:
        print("  БЛОК:", b.get("ru"))


def main(argv: list[str]) -> int:
    cmd = argv[0]
    ap = argparse.ArgumentParser(prog=f"cnq {cmd}")
    if cmd == "kill":
        ap.add_argument("action", choices=["on", "off", "status"])
    else:
        ap.add_argument("action", nargs="?", default="run", choices=["run", "status", "reset"])
    ap.add_argument("--reason", default="")
    ap.add_argument("--out", default="web")
    a = ap.parse_args(argv[1:])
    if cmd == "kill":
        if a.action == "status":
            k = G.kill_status()
            print(f"аварийный выключатель: {'ВКЛЮЧЁН' if k['on'] else 'выключен'}"
                  + (f" с {G.hm(k['since'])}, причина: {k.get('reason') or '—'}" if k["on"] else ""))
            for h in (k.get("history") or [])[-5:]:
                print(f"  {G.hm(h.get('t'))}: {'on ' if h.get('on') else 'off'} {h.get('by') or ''} {h.get('reason') or ''}")
            _print_state(None)
            return 0
        if a.action == "on" and not a.reason.strip():
            print("укажите причину: kill on --reason \"…\"")
            return 2
        d = G.set_kill(a.action == "on", a.reason.strip(), _who())
        print(f"аварийный выключатель {'ВКЛЮЧЁН' if d['on'] else 'выключен'} ({G.hm(d['changed_at'])})"
              + (": новые входы запрещены на всех демо-счетах; стопы и цели открытых позиций работают" if d["on"] else
                 ": входы снова разрешены (если нет других стопов риск-слоя)"))
        return 0
    if a.action == "status":
        _print_state(None)
        return 0
    if a.action == "reset":
        if not a.reason.strip():
            print("укажите причину: risk reset --reason \"…\"")
            return 2
        now = int(time.time() * 1000)
        G.write_json(L.path(L.RESET_FILE), {"t": now, "reason": a.reason.strip(), "by": _who()})
        G.log_event("drawdown_reset_requested", {"reason": a.reason.strip(), "by": _who()}, None, now)
        print("сброс стопа по просадке записан — шаг risk исполнит его в течение минуты (пик считается заново от текущего уровня)")
        return 0
    from . import portfolio
    st, err = portfolio.run(a.out)
    ix = st.get("index") or {}
    print(f"risk.json: state={st.get('state')} accounts={len(st.get('accounts') or {})} gross={(st.get('combined') or {}).get('gross')} "
          f"day={(ix.get('daily') or 0) * 100:+.2f}% dd={(ix.get('drawdown') or 0) * 100:+.2f}% data_age={(st.get('data') or {}).get('age_min')} "
          f"blocks={[b.get('kind') for b in st.get('blocks') or []]} skips_today={(st.get('skips') or {}).get('today')}"
          + (f" ERROR={err}" if err else ""))
    for w in (st.get("warnings") or [])[:3]:
        print("  note:", w)
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["risk"]))
