"""Owner login for cryptonova.vip — set or change the login and password (run on the owner's Mac):

    python3 -m cnq.execution.owner_setup        (or: bash scripts/owner_login.sh)

The panel is a static site, so there is no server to check a password. Instead this command encrypts a small
token {"login": …, "role": "owner"} with the password (same scheme as «Иван тест»: PBKDF2-SHA256 200 000 rounds,
HMAC-CTR, HMAC tag) and publishes it as web/owner.enc.json. The panel's login window decrypts it with what the owner
types: the right login + password opens the owner view on that device; anything else stays a guest. The password is
never stored or sent anywhere — only the encrypted token is public. Needs CNQ_GH_TOKEN (from ~/.cryptonova.env).
"""
from __future__ import annotations

import getpass
import os
import sys
import time

from . import ivan_watch

PATH = "web/owner.enc.json"
MIN_LEN = 12          # 22.10 (audit): owner.enc.json is public — offline guessing is the threat, so 12+ characters


def make_blob(login: str, pw: str, now: int | None = None) -> dict:
    login = (login or "").strip()
    if len(login) < 3:
        raise ValueError("логин — минимум 3 символа")
    if len(pw or "") < MIN_LEN:
        raise ValueError(f"пароль — минимум {MIN_LEN} символов")
    now = int(now or time.time() * 1000)
    return ivan_watch.encrypt({"login": login.lower(), "role": "owner", "set": now}, pw, asof=now)


def main(argv=None, env=None, ask=input, ask_pw=getpass.getpass, put=None) -> int:
    env = os.environ if env is None else env
    tok, repo = env.get("CNQ_GH_TOKEN", ""), env.get("CNQ_GH_REPO") or ivan_watch.DEFAULT_REPO
    if not tok and put is None:
        print("Нет CNQ_GH_TOKEN. Запусти так:  source ~/.cryptonova.env && python3 -m cnq.execution.owner_setup")
        return 1
    print("Вход владельца для cryptonova.vip. Пароль нигде не сохраняется — запомни его (лучше 3–4 случайных слова).")
    login = ask("Логин: ").strip()
    pw = ask_pw(f"Пароль (не меньше {MIN_LEN} символов): ")
    if pw != ask_pw("Пароль ещё раз: "):
        print("Пароли не совпали — ничего не изменено.")
        return 1
    try:
        blob = make_blob(login, pw)
    except ValueError as e:
        print(f"Не подходит: {e}. Ничего не изменено.")
        return 1
    ok = put(blob) if put is not None else ivan_watch.publish_ivan(blob, tok, repo, path=PATH, message="owner: login updated")
    if not ok:
        print("GitHub не принял файл — проверь CNQ_GH_TOKEN. Ничего не изменено.")
        return 1
    print("Готово. Через 15–30 минут (после выкладки сайта) входи на cryptonova.vip кнопкой «Войти»: логин и этот пароль.\n"
          "Старый пароль перестанет работать на новых устройствах; уже открытые устройства остаются в режиме владельца, пока ты не нажмёшь «Выйти».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
