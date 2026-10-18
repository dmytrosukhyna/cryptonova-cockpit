"""Access codes for guests of cryptonova.vip — the owner issues them from his Mac:

    bash scripts/access_code.sh add "Имя"      → prints a new code for that person (e.g. NOVA-7K3P-Q9WD)
    bash scripts/access_code.sh list           → who has a code
    bash scripts/access_code.sh remove "Имя"   → the code stops working

Only a hash of each code is published (web/access.json: sha256('cnq:' + code), base64url — the same as the panel's
sha()), so the file does not reveal the codes. A guest who enters a valid code gets the invited view («Тесты
стратегий» and the invited welcome). Needs CNQ_GH_TOKEN (from ~/.cryptonova.env).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time

from . import ivan_watch

PATH = "web/access.json"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O, 1/I — easy to dictate


def new_code() -> str:
    s = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"NOVA-{s[:4]}-{s[4:]}"


def code_hash(code: str) -> str:
    c = (code or "").strip().upper()
    return base64.urlsafe_b64encode(hashlib.sha256(("cnq:" + c).encode()).digest()).decode().rstrip("=")


def add(doc: dict, label: str, now: int, level: str = "tests") -> tuple[dict, str]:
    code = new_code()
    doc = dict(doc or {})
    doc["codes"] = [c for c in doc.get("codes") or []] + [{"h": code_hash(code), "label": label.strip()[:60], "level": level, "created": now}]
    doc["v"] = 1
    return doc, code


def remove(doc: dict, label: str) -> tuple[dict, int]:
    keep = [c for c in (doc or {}).get("codes") or [] if c.get("label", "").lower() != label.strip().lower()]
    n = len((doc or {}).get("codes") or []) - len(keep)
    return {**(doc or {}), "codes": keep, "v": 1}, n


def _get(token: str, repo: str, http) -> tuple[dict, str | None]:
    url = f"https://api.github.com/repos/{repo}/contents/{PATH}?ref=main"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-access"}
    try:
        st, js = http("GET", url, hdr, None)
        if st == 200 and isinstance(js, dict):
            return json.loads(base64.b64decode(js.get("content") or "").decode() or "{}"), js.get("sha")
    except Exception:  # noqa: BLE001
        pass
    return {"v": 1, "codes": []}, None


def _put(doc: dict, sha: str | None, token: str, repo: str, http, msg: str) -> bool:
    url = f"https://api.github.com/repos/{repo}/contents/{PATH}"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-access", "Content-Type": "application/json"}
    data = {"message": msg, "branch": "main", "content": base64.b64encode(json.dumps(doc, ensure_ascii=False, indent=1).encode()).decode(),
            **({"sha": sha} if sha else {})}
    st, _ = http("PUT", url, hdr, json.dumps(data).encode())
    return st in (200, 201)


def main(argv=None, env=None, http=None, out=print) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    http = http or ivan_watch._http
    tok, repo = env.get("CNQ_GH_TOKEN", ""), env.get("CNQ_GH_REPO") or ivan_watch.DEFAULT_REPO
    if not tok:
        out("Нет CNQ_GH_TOKEN. Запусти так:  bash scripts/access_code.sh add \"Имя\"")
        return 1
    cmd = (argv[0] if argv else "list").lower()
    doc, sha = _get(tok, repo, http)
    if cmd == "list":
        codes = doc.get("codes") or []
        out("Коды доступа:" if codes else "Кодов пока нет.")
        for c in codes:
            out(f" — {c.get('label') or 'без имени'} · выдан {time.strftime('%d.%m.%Y', time.localtime((c.get('created') or 0) / 1000))}")
        return 0
    if cmd == "add":
        label = " ".join(argv[1:]).strip() or "гость"
        doc, code = add(doc, label, int(time.time() * 1000))
        if not _put(doc, sha, tok, repo, http, f"access: code for {label}"):
            out("GitHub не принял файл — код не создан.")
            return 1
        out(f"Код для «{label}»:  {code}\nОн заработает через 5–10 минут. Человек открывает cryptonova.vip → «У меня есть код доступа» → вводит код.")
        return 0
    if cmd == "remove":
        label = " ".join(argv[1:]).strip()
        doc, n = remove(doc, label)
        if not n:
            out(f"Кода «{label}» нет. Список: bash scripts/access_code.sh list")
            return 1
        if not _put(doc, sha, tok, repo, http, f"access: remove {label}"):
            out("GitHub не принял файл — ничего не изменено.")
            return 1
        out(f"Код «{label}» отключён (через 5–10 минут).")
        return 0
    out("Команды: add \"Имя\" · list · remove \"Имя\"")
    return 1


if __name__ == "__main__":
    sys.exit(main())
