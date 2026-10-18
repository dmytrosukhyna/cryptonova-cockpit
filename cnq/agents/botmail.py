"""Bot mailbox — the channel bot answers people who write to it. The server runs every 30 minutes, so this is a
polling mailbox, not a live chat: new /start and messages get a welcome / an answer on the next run, chat ids are
remembered (table bot_chats) so the owner can later be addressed directly, and the bot's profile texts
(description, about, commands) are set once per version. Stdlib only. Env: CNQ_TG_TOKEN, CNQ_TG_OWNER (username)."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request

PROFILE_VERSION = "v1"
SHORT = "AI-система оценки и анализа крипторынка. Канал: @crypto_nova_news"
DESCRIPTION = ("CRYPTO NOVA — AI-платформа, которая читает рынок по данным: зоны, стакан, потоки, крупные игроки, новости и их последствия. "
               "Ведёт канал @crypto_nova_news: утренний обзор, разбор главных новостей, итоги дня с проверкой прогноза.\n\n"
               "Напишите сообщение — мы ответим. Доступ к платформе откроется скоро.")
COMMANDS = [{"command": "start", "description": "О платформе и канале"}, {"command": "help", "description": "Что умеет система"},
            {"command": "channel", "description": "Ссылка на канал"}]
WELCOME = ("Здравствуйте! Это бот платформы <b>CRYPTO NOVA</b> — AI-системы оценки и анализа крипторынка.\n\n"
           "Что мы делаем: смотрим на рынок круглые сутки, читаем зоны разворота, стакан, потоки и позиции крупных игроков, "
           "разбираем новости и их последствия — и каждый вечер проверяем свой же утренний прогноз.\n\n"
           "Канал с обзорами: @crypto_nova_news\n"
           "Доступ к самой платформе откроем скоро — вы в списке. Если есть вопрос, просто напишите сюда: отвечаем в течение дня.")
# 18.10 (owner): «Подключить» on the panel opens t.me/<bot>?start=connect_<strategy>. The bot explains the terms and tells the owner.
CONNECT_NAMES = {"cryptonova": "CRYPTO NOVA (отобранные сигналы)", "portfolio": "CRYPTO NOVA портфель",
                 "capital": "CRYPTO NOVA Портфолио (система сама распределяет вашу сумму)", "exp": "Волна", "brk": "Прорыв",
                 "imp": "Импульс", "lab": "Эксперименты", "memes": "Мем-радар", "whales": "По следам китов",
                 "cons": "портфель «Консервативный»", "mod": "портфель «Умеренный»", "high": "портфель «Высокий риск»",
                 "meme": "портфель «Мемы»", "bal": "портфель «Сбалансированный»"}
CONNECT = ("Вы выбрали: <b>{name}</b>.\n\n"
           "<b>Как подключиться</b>\n"
           "1. Администратор CRYPTO NOVA напишет вам сюда и поможет подключить стратегию к вашей бирже — ключ API только с правом "
           "торговли, без права вывода средств. Деньги всегда остаются на вашем счёте.\n"
           "2. Система торгует по стратегии, вы видите все сделки и результат.\n"
           "3. <b>Оплата — 10% от прибыли за месяц, в USDT.</b> Месяц без прибыли — бесплатно. В конце месяца бот пришлёт отчёт "
           "и сумму к оплате; чтобы продолжить пользоваться системой, её нужно оплатить.\n\n"
           "Важно: торговля связана с риском, прошлый результат не гарантирует будущий. Начните с суммы, которую не страшно потерять.\n\n"
           "Мы получили вашу заявку — администратор свяжется с вами в течение дня.")
HELP = ("Система CRYPTO NOVA:\n— утренний обзор рынка (08:00 Киев), разбор главных новостей днём, итоги дня вечером;\n"
        "— сценарии A/B с вероятностью и честной проверкой;\n— три демо-стратегии и три спот-портфеля по риску;\n"
        "— зоны, стакан, киты Hyperliquid, карта перекупленности;\n— голосовой ассистент и автоматическая торговля под вашими ключами.\n\n"
        "Канал: @crypto_nova_news")


def _api(token: str, method: str, data: dict | None = None, timeout: int = 20) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    req = urllib.request.Request(url, data=json.dumps(data or {}).encode(), headers={"Content-Type": "application/json",
                                 "User-Agent": "Mozilla/5.0 cryptonova/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


class Mailbox:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.executescript("""CREATE TABLE IF NOT EXISTS bot_chats(chat_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, first_seen INTEGER, last_seen INTEGER, n_msgs INTEGER);
            CREATE TABLE IF NOT EXISTS bot_meta(k TEXT PRIMARY KEY, v TEXT);""")
        conn.commit()

    def meta(self, k: str, default: str = "") -> str:
        r = self.conn.execute("SELECT v FROM bot_meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def set_meta(self, k: str, v: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO bot_meta VALUES(?,?)", (k, v)); self.conn.commit()

    def setup_profile(self, token: str) -> bool:
        if self.meta("profile") == PROFILE_VERSION:
            return False
        ok = True
        for method, data in (("setMyShortDescription", {"short_description": SHORT}), ("setMyDescription", {"description": DESCRIPTION}),
                             ("setMyCommands", {"commands": COMMANDS})):
            try:
                ok = _api(token, method, data).get("ok", False) and ok
            except Exception:  # noqa: BLE001
                ok = False
        if ok:
            self.set_meta("profile", PROFILE_VERSION)
        return ok

    def owner_chat(self, owner_username: str | None) -> int | None:
        if owner_username:
            r = self.conn.execute("SELECT chat_id FROM bot_chats WHERE lower(username)=?", (owner_username.lstrip("@").lower(),)).fetchone()
            if r:
                return r[0]
        r = self.conn.execute("SELECT v FROM bot_meta WHERE k='owner_chat'").fetchone()
        return int(r[0]) if r else None

    def lead(self, chat_id: int, username: str | None, first_name: str | None, what: str, now: int) -> None:
        self.conn.execute("CREATE TABLE IF NOT EXISTS bot_leads(time INTEGER, chat_id INTEGER, username TEXT, first_name TEXT, strategy TEXT, notified INTEGER)")
        self.conn.execute("INSERT INTO bot_leads VALUES(?,?,?,?,?,0)", (now, chat_id, username, first_name, what))

    def notify_leads(self, token: str, owner_chat: int | None) -> int:
        """Tell the owner about new connection requests (once each)."""
        if not owner_chat:
            return 0
        try:
            rows = self.conn.execute("SELECT rowid, username, first_name, strategy, chat_id FROM bot_leads WHERE notified=0").fetchall()
        except sqlite3.OperationalError:
            return 0
        n = 0
        for rid, un, fn, what, cid in rows:
            who = f"@{un}" if un else (fn or f"id {cid}")
            try:
                _api(token, "sendMessage", {"chat_id": owner_chat, "parse_mode": "HTML", "disable_web_page_preview": True,
                                            "text": f"Заявка на подключение: <b>{CONNECT_NAMES.get(what, what)}</b>\nОт: {who}"
                                                    + (f" (<a href=\"tg://user?id={cid}\">написать</a>)" if not un else "")})
                self.conn.execute("UPDATE bot_leads SET notified=1 WHERE rowid=?", (rid,))
                n += 1
            except Exception:  # noqa: BLE001
                pass
        self.conn.commit()
        return n

    def poll(self, token: str, answer_fn=None, now: int | None = None) -> dict:
        """Read new updates, welcome newcomers, answer messages (answer_fn(text, chat) -> reply or None), remember chats."""
        now = now or int(time.time() * 1000)
        offset = int(self.meta("offset", "0") or 0)
        try:
            res = _api(token, "getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": ["message"]})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}
        n_new = n_reply = 0
        for u in res.get("result", []):
            offset = max(offset, u["update_id"] + 1)
            m = u.get("message") or {}
            chat = m.get("chat") or {}
            if chat.get("type") != "private":
                continue
            cid = chat["id"]; frm = m.get("from") or {}
            row = self.conn.execute("SELECT n_msgs FROM bot_chats WHERE chat_id=?", (cid,)).fetchone()
            if row:
                self.conn.execute("UPDATE bot_chats SET last_seen=?, n_msgs=n_msgs+1, username=COALESCE(?,username) WHERE chat_id=?", (now, frm.get("username"), cid))
            else:
                self.conn.execute("INSERT INTO bot_chats VALUES(?,?,?,?,?,1)", (cid, frm.get("username"), frm.get("first_name"), now, now)); n_new += 1
            text = (m.get("text") or "").strip()
            reply = None
            if text.startswith("/start connect_"):
                what = text.split("connect_", 1)[1].strip().split()[0][:24]
                reply = CONNECT.format(name=CONNECT_NAMES.get(what, "стратегия CRYPTO NOVA"))
                self.lead(cid, frm.get("username"), frm.get("first_name"), what, now)
            elif text.startswith("/start"):
                reply = WELCOME
            elif text.startswith("/help"):
                reply = HELP
            elif text.startswith("/channel"):
                reply = "Канал CRYPTO NOVA: https://t.me/crypto_nova_news"
            elif text:
                reply = answer_fn(text, {"chat_id": cid, "username": frm.get("username")}) if answer_fn else None
                if not reply:
                    reply = "Спасибо, сообщение получено — ответим в течение дня. Обзоры рынка: @crypto_nova_news"
            if reply:
                try:
                    _api(token, "sendMessage", {"chat_id": cid, "text": reply, "parse_mode": "HTML", "disable_web_page_preview": True}); n_reply += 1
                except Exception:  # noqa: BLE001
                    pass
        self.set_meta("offset", str(offset))
        self.conn.commit()
        return {"ok": True, "new_chats": n_new, "replies": n_reply, "chats": self.conn.execute("SELECT count(*) FROM bot_chats").fetchone()[0]}


def run(conn: sqlite3.Connection, llm=None, token: str | None = None, owner: str | None = None) -> dict:
    token = token or os.environ.get("CNQ_TG_TOKEN")
    if not token:
        return {"ok": False, "error": "CNQ_TG_TOKEN missing"}
    mb = Mailbox(conn)
    prof = mb.setup_profile(token)

    def answer(text: str, who: dict):
        if llm is None:
            return None
        try:
            out = llm.complete("Ты — ассистент платформы CRYPTO NOVA (AI-система оценки и анализа крипторынка). Отвечай по-русски коротко, дружелюбно и по делу, "
                               "3–5 предложений. Не давай торговых рекомендаций по конкретным сделкам и не называй цен или чисел, которых нет в вопросе. "
                               "Если спрашивают о доступе к платформе — скажи, что доступ откроется скоро, человек в списке. Канал: @crypto_nova_news.", text)
            return (out or "").strip()[:1500] or None
        except Exception:  # noqa: BLE001
            return None

    res = mb.poll(token, answer)
    res["profile_set"] = prof
    res["owner_chat"] = mb.owner_chat(owner or os.environ.get("CNQ_TG_OWNER") or "dmytrosukhyna")
    res["leads_sent"] = mb.notify_leads(token, res["owner_chat"])
    return res
