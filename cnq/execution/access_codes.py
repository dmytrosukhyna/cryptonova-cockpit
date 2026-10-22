"""Access codes for guests of cryptonova.vip — the owner issues them from his Mac:

    bash scripts/access_code.sh add "Имя"      → prints a new code for that person (e.g. NOVA-7K3P-Q9WD-X2MA)
    bash scripts/access_code.sh list           → who has a code
    bash scripts/access_code.sh remove "Имя"   → the code stops working at once
    bash scripts/access_code.sh month "Имя"    → a code valid for 31 days (paid access; a new one each month)
    bash scripts/access_code.sh remove legacy  → switches off the two codes made before 22.10 (their hashes are in public git history)

22.10.2026 (audit 20.10): the codes are no longer published as hashes in web/access.json. They live only on the server
(cnq/api/access.py: salted PBKDF2-SHA256, 200 000 rounds) and the panel checks a code with POST /v2/access/verify. This tool
talks to the owner-only API with the «Управление» token: CNQ_ADMIN_TOKEN in ~/.cryptonova.env (the same value as in
/etc/cnq.env; never in chat). CNQ_API_URL overrides the server address.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://app.crypto-capital.app"


def _http(method: str, url: str, headers: dict, data: bytes | None = None, timeout: int = 20):
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}


def main(argv=None, env=None, http=None, out=print) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    http = http or _http
    tok = (env.get("CNQ_ADMIN_TOKEN") or "").strip()
    base = (env.get("CNQ_API_URL") or API).rstrip("/") + "/v2/admin/access"
    if not tok:
        out("Нет CNQ_ADMIN_TOKEN. Добавь в ~/.cryptonova.env строку  export CNQ_ADMIN_TOKEN=…  (тот же токен, что во вкладке «Управление»).")
        return 1
    hdr = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "User-Agent": "cnq-access"}
    cmd = (argv[0] if argv else "list").lower()
    label = " ".join(argv[1:]).strip()
    if cmd == "list":
        st, js = http("GET", base, hdr, None)
        if st != 200:
            out(f"Сервер не ответил ({st}: {js.get('error', '')}). Проверь токен.")
            return 1
        codes = js.get("codes") or []
        out("Коды доступа:" if codes else "Кодов пока нет.")
        for c in codes:
            till = f" · до {time.strftime('%d.%m.%Y', time.localtime(c['until'] / 1000))}" if c.get("until") else " · бессрочно"
            out(f" — {c.get('label') or 'без имени'}{' (старый — заменить)' if c.get('legacy') else ''} · выдан "
                f"{time.strftime('%d.%m.%Y', time.localtime((c.get('created') or 0) / 1000))}{till}")
        return 0
    if cmd in ("add", "month"):
        label = label or "гость"
        days = 31 if cmd == "month" else None
        if cmd == "month":                                      # the person's previous monthly code is replaced
            http("POST", base, hdr, json.dumps({"op": "remove", "label": label, "confirm": True}).encode())
        st, js = http("POST", base, hdr, json.dumps({"op": "add", "label": label, "days": days, "confirm": True}).encode())
        if st != 200 or not js.get("code"):
            out(f"Сервер не создал код ({st}: {js.get('error', '')}).")
            return 1
        till = f"\nДействует 31 день — до {time.strftime('%d.%m.%Y', time.localtime(time.time() + 31 * 86400))}." if days else ""
        out(f"Код для «{label}»:  {js['code']}\nРаботает сразу. Человек открывает cryptonova.vip → «У меня есть код доступа» → вводит код.{till}")
        return 0
    if cmd == "remove":
        if not label:
            out("Укажи имя: remove \"Имя\"  (или remove legacy — старые коды до 22.10)")
            return 1
        st, js = http("POST", base, hdr, json.dumps({"op": "remove", "label": label, "confirm": True}).encode())
        if st != 200:
            out(f"Сервер не ответил ({st}: {js.get('error', '')}).")
            return 1
        if not js.get("removed"):
            out(f"Кода «{label}» нет. Список: bash scripts/access_code.sh list")
            return 1
        out(f"Код «{label}» отключён.")
        return 0
    out("Команды: add \"Имя\" · month \"Имя\" · list · remove \"Имя\" · remove legacy")
    return 1


if __name__ == "__main__":
    sys.exit(main())
