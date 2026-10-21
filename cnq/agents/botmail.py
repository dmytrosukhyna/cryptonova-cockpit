"""Bot mailbox — the channel bot answers people who write to it. The server runs every 30 minutes, so this is a
polling mailbox, not a live chat: new /start and messages get a welcome / an answer on the next run, chat ids are
remembered (table bot_chats) so the owner can later be addressed directly, and the bot's profile texts
(description, about, commands) are set once per version. Stdlib only. Env: CNQ_TG_TOKEN, CNQ_TG_OWNER (username)."""
from __future__ import annotations

import json
import os
import re
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
                 "altport": "Портфель «Ядро + альтсезон» (ядро + альты по датчику альтсезона + фьючерсы)",
                 "capital": "CRYPTO NOVA Портфолио (система сама распределяет вашу сумму)", "exp": "Волна", "brk": "Прорыв",
                 "imp": "Импульс", "lab": "Эксперименты", "memes": "Мем-радар", "whales": "По следам китов",
                 "cons": "портфель «Консервативный»", "mod": "портфель «Умеренный»", "high": "портфель «Высокий риск»",
                 "meme": "портфель «Мемы»", "bal": "портфель «Сбалансированный»"}
MODEL_RU = {"calm": "Спокойная", "balanced": "Сбалансированная", "bold": "Смелая"}
OPTION_RU = {"r": "рискованные монеты", "d": "больше DeFi-кредитования", "n": "без фьючерсов"}


def connect_name(what: str) -> str:
    """connect_capital_bold_r_d → «CRYPTO NOVA Портфолио · модель «Смелая» + рискованные монеты, больше DeFi-кредитования»."""
    if what in CONNECT_NAMES:
        return CONNECT_NAMES[what]
    parts = what.split("_")
    if parts and parts[0] == "capital" and len(parts) > 1 and parts[1] in MODEL_RU:
        opts = [OPTION_RU[p] for p in parts[2:] if p in OPTION_RU]
        return f"CRYPTO NOVA Портфолио · модель «{MODEL_RU[parts[1]]}»" + (" + " + ", ".join(opts) if opts else "")
    return "стратегия CRYPTO NOVA"


# 21.10 owner: «сосредоточимся просто на реферальной системе … 10% — потом, когда изучим юридическую часть» — no fee, no API keys,
# no administrator: the person opens an exchange account (our partner link when web/app_config.json has one) and follows the app.
CONNECT = ("Вы выбрали: <b>{name}</b>.\n\n"
           "<b>Как начать работу с CRYPTO NOVA — около 15 минут.</b> Пошаговая инструкция:\n"
           "1. Откройте счёт на бирже BingX{ref}. Счёт на ваше имя — деньги всегда у вас.\n"
           "2. Пройдите проверку личности (KYC) — так требует биржа.\n"
           "3. Пополните счёт в USDT на сумму, которую не страшно потерять.\n"
           "4. <b>Пришлите сюда свой UID</b> — номер счёта BingX: в приложении биржи нажмите на аватар, UID рядом с ним. "
           "Я проверю, что счёт открыт по ссылке CRYPTO NOVA и пополнен, и отвечу. UID — не пароль: по нему нельзя войти в счёт.\n"
           "5. Откройте в приложении CRYPTO NOVA портфель и сделки: у каждой сделки видны вход, стоп и цель — повторяйте их на своём счёте.\n\n"
           "Копирование сделок в один клик на BingX готовим: запустим, когда биржа письменно подтвердит программу. Кто прислал UID — узнает первым.\n"
           "Нужна помощь — напишите сюда, подскажем по шагам.\n\n"
           "Важно: торговля связана с риском, прошлый результат не гарантирует будущий.")

# 22.10 decision B (claude/cryptonova-okx-vs-bingx-2026-10-22.md): BingX does not register people from the EU, and BingX links must not
# be shown to EU residents. The bot asks once where the person lives: EU → OKX (OKX Europe: MiCA + MiFID; USDC/EUR, no USDT;
# futures = X-Perps up to 10x, ~20 pairs), elsewhere → BingX. The answer is kept per chat (bot_region) for the UID check.
REGION_Q = ("Вы выбрали: <b>{name}</b>.\n\n<b>Где вы живёте?</b> От этого зависит биржа: в ЕС работает OKX (лицензия ЕС), "
            "в других странах — BingX. Нажмите кнопку ниже или напишите «ЕС» или «другая страна».")
REGION_KB = {"keyboard": [[{"text": "Живу в ЕС"}, {"text": "Живу в другой стране"}]], "resize_keyboard": True, "one_time_keyboard": True}
EU_RE = re.compile(r"(?i)^\s*(?:я\s+)?(?:живу\s+)?(?:в\s+)?(?:ЕС|EU|Евросоюзе?|Европе|Европа|Греции|Греция)\s*[.!]?\s*$")
WORLD_RE = re.compile(r"(?i)^\s*(?:я\s+)?(?:живу\s+)?(?:в\s+)?(?:другой\s+стране|другая\s+страна|не\s+в\s+ЕС|вне\s+ЕС|other|Украине|Украина)\s*[.!]?\s*$")
CONNECT_OKX = ("Вы выбрали: <b>{name}</b>. Вы живёте в ЕС — значит, биржа <b>OKX</b> (лицензия ЕС).\n\n"
               "<b>Как начать работу с CRYPTO NOVA — около 15 минут.</b> Пошаговая инструкция:\n"
               "1. Откройте счёт на OKX{ref}. Счёт на ваше имя — деньги всегда у вас.\n"
               "2. Пройдите проверку личности (KYC) — так требует биржа.\n"
               "3. Пополните счёт в USDC или евро (USDT в ЕС на OKX недоступен) на сумму, которую не страшно потерять.\n"
               "4. <b>Пришлите сюда свой UID</b> — номер счёта OKX: в приложении биржи откройте профиль, UID рядом с именем. "
               "UID — не пароль: по нему нельзя войти в счёт.\n"
               "5. Откройте в приложении CRYPTO NOVA портфель и сделки: у каждой сделки видны вход, стоп и цель — повторяйте их на своём счёте. "
               "Фьючерсы в ЕС на OKX называются X-Perps: плечо до 10x, около 20 пар, так что часть монет может быть недоступна; "
               "спот и портфель работают полностью.\n\n"
               "Нужна помощь — напишите сюда, подскажем по шагам.\n\n"
               "Важно: торговля связана с риском, прошлый результат не гарантирует будущий.")

# 21.10 22:52 owner: «напишите в поддержку — это нашему боту? бот перепроверит UID и подключит?». A UID sent to the bot is checked with
# the BingX partner API (cnq/api/bingx.py, CNQ_BINGX_AGENT_KEY / _SECRET); without the partner key it is queued for the owner (24 h).
# Only the number is stored (bot_uids) — a UID is not a secret and gives no access to the account.
UID_RE = re.compile(r"(?i)^\s*(?:bingx\s*)?(?:uid|юид|уид|айди|id)?\s*[:#№-]?\s*(\d{6,12})\s*$")
UID_REPLY = {
    "ok": "UID {uid} подтверждён: счёт открыт по ссылке CRYPTO NOVA и пополнен. Спасибо! Дальше — сделки и портфель в приложении CRYPTO NOVA. "
          "Когда запустим копирование в один клик, напишу вам первым.",
    "no_deposit": "Счёт {uid} открыт по ссылке CRYPTO NOVA, но пока не пополнен. Пополните его в {cur} и пришлите UID ещё раз — проверю снова.",
    "not_invited": "Счёт {uid} открыт не по ссылке CRYPTO NOVA. Ничего менять не нужно: приложением можно пользоваться и так. "
                   "Если номер с ошибкой — пришлите UID ещё раз.",
    "pending": "UID {uid} получен. Проверим вручную в течение 24 часов и ответим здесь. Пока можно открыть сделки и портфель в приложении CRYPTO NOVA.",
}


def uid_of(text: str) -> str | None:
    m = UID_RE.match(text or "")
    return m.group(1) if m else None


def exchange_ref(key: str = "exchange_ref") -> str:
    """The partner (referral) link from web/app_config.json (exchange_ref = BingX, okx_ref = OKX) → « по нашей ссылке: …»,
    or «» when it is not set yet (OKX: the plain EU site, no partner wording)."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "..", "web", "app_config.json"), encoding="utf-8") as f:
            ref = (json.load(f) or {}).get(key)
        if ref:
            return f" по нашей ссылке: {ref}"
    except Exception:  # noqa: BLE001
        pass
    return ": https://www.okx.com/eu" if key == "okx_ref" else ""


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
        self.notes: list[str] = []
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

    def channel_event(self, mcm: dict, owner_username: str | None) -> str | None:
        """19.10: the owner adds the bot as an admin to a private channel with «signal» / «сигнал» in its title → that channel
        becomes the CRYPTO NOVA signals channel (signal_feed.py posts there). Only when the OWNER added it (his user id or
        username), never the news channel (CNQ_TG_CHAT / @crypto_nova_news). Removing the bot switches it off. → a note for the owner."""
        chat = mcm.get("chat") or {}
        new = (mcm.get("new_chat_member") or {}).get("status")
        frm = mcm.get("from") or {}
        cid, title = chat.get("id"), chat.get("title") or ""
        if cid is None or chat.get("type") not in ("channel", "supergroup", "group"):
            return None
        if new in ("left", "kicked") and str(cid) == self.meta("signals_chat"):
            self.set_meta("signals_chat", "")
            return f"Бот убран из канала «{title}» — сигналы туда больше не идут."
        if new != "administrator":
            return None
        own = self.owner_chat(owner_username)
        by_owner = (own is not None and frm.get("id") == own) or (
            owner_username and (frm.get("username") or "").lower() == owner_username.lstrip("@").lower())
        news = str(cid) == str(os.environ.get("CNQ_TG_CHAT") or "") or (chat.get("username") or "").lower() == "crypto_nova_news"
        if not by_owner or news or not self.title_ok(title):
            return None
        self.set_meta("signals_chat", str(cid))
        self.set_meta("signals_chat_title", title)
        return f"Канал «{title}» подключён: новые сигналы CRYPTO NOVA (вход, стоп, цели и «Close» при выходе) будут приходить туда."

    def title_ok(self, title: str) -> bool:
        """19.10: the owner's channel is «CRYPTO NOVA FUTURES» — a title with signals / futures (any language), or ANY channel of
        the owner while no signals channel is connected yet (the owner is always checked; the news channel never qualifies)."""
        return bool(re.search(r"(?i)signal|сигнал|futures|фьючерс", title)) or not self.meta("signals_chat")

    def connect_checked(self, token: str, chat: dict, owner_username: str | None, api=None) -> str | None:
        """19.10 fallback when the «bot added» event was missed: a post in the channel (channel_post) or a post the owner forwards
        to the bot from it. The channel is connected only when its title says «signal» / «сигнал», it is not the news channel, and
        Telegram's admin list of the channel (getChatAdministrators — works only where the bot itself is an admin) has the OWNER."""
        api = api or _api
        cid, title = chat.get("id"), chat.get("title") or ""
        if cid is None or chat.get("type") not in ("channel", "supergroup", "group"):
            return None
        if str(cid) == self.meta("signals_chat"):
            return None
        news = str(cid) == str(os.environ.get("CNQ_TG_CHAT") or "") or (chat.get("username") or "").lower() == "crypto_nova_news"
        if news or not self.title_ok(title):
            return None
        own = self.owner_chat(owner_username)
        try:
            admins = api(token, "getChatAdministrators", {"chat_id": cid}).get("result") or []
        except Exception:  # noqa: BLE001
            return None
        un = (owner_username or "").lstrip("@").lower()
        if not any((own is not None and (a.get("user") or {}).get("id") == own) or (un and ((a.get("user") or {}).get("username") or "").lower() == un)
                   for a in admins):
            return None
        self.set_meta("signals_chat", str(cid))
        self.set_meta("signals_chat_title", title)
        return f"Канал «{title}» подключён: новые сигналы CRYPTO NOVA (вход, стоп, цели и «Close» при выходе) и портфель будут приходить туда."

    def owner_chat(self, owner_username: str | None) -> int | None:
        if owner_username:
            r = self.conn.execute("SELECT chat_id FROM bot_chats WHERE lower(username)=?", (owner_username.lstrip("@").lower(),)).fetchone()
            if r:
                return r[0]
        r = self.conn.execute("SELECT v FROM bot_meta WHERE k='owner_chat'").fetchone()
        return int(r[0]) if r else None

    def region(self, chat_id: int) -> str | None:
        """'eu' | 'world' | None — the answer to «Где вы живёте?» (decision B)."""
        try:
            r = self.conn.execute("SELECT region FROM bot_region WHERE chat_id=?", (chat_id,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return r[0] if r and r[0] in ("eu", "world") else None

    def set_region(self, chat_id: int, region: str | None, now: int, what: str | None = None) -> str | None:
        """Remember the region (and the strategy the person came for); → that strategy key (the last one asked)."""
        self.conn.execute("CREATE TABLE IF NOT EXISTS bot_region(chat_id INTEGER PRIMARY KEY, region TEXT, what TEXT, time INTEGER)")
        old = self.conn.execute("SELECT what FROM bot_region WHERE chat_id=?", (chat_id,)).fetchone()
        what = what or (old[0] if old else None)
        self.conn.execute("INSERT OR REPLACE INTO bot_region VALUES(?,?,?,?)", (chat_id, region, what, now))
        self.conn.commit()
        return what

    def uid(self, chat_id: int, username: str | None, uid: str, now: int, check=None, exchange: str | None = None) -> str:
        """Record a UID and check it once; → the reply for the person. EU (decision B) → OKX (cnq/api/okx_aff.py), else BingX
        (cnq/api/bingx.py). check(uid) → {ok, reason, invited, deposited, kyc}."""
        self.conn.execute("CREATE TABLE IF NOT EXISTS bot_uids(time INTEGER, chat_id INTEGER, username TEXT, uid TEXT, status TEXT, detail TEXT, notified INTEGER)")
        exchange = exchange or ("okx" if self.region(chat_id) == "eu" else "bingx")
        if check is None:
            if exchange == "okx":
                from ..api.okx_aff import check_invitee as check
            else:
                from ..api.bingx import check_referral as check
        try:
            res = check(uid) or {}
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "reason": "exchange_error", "detail": str(e)[:120]}
        status = "ok" if res.get("ok") else res.get("reason") if res.get("reason") in ("no_deposit", "not_invited") else "pending"
        detail = json.dumps({"exchange": exchange, **{k: res.get(k) for k in ("reason", "invited", "deposited", "kyc") if k in res}},
                            ensure_ascii=False)
        self.conn.execute("INSERT INTO bot_uids VALUES(?,?,?,?,?,?,0)", (now, chat_id, username, uid, status, detail))
        self.conn.commit()
        return UID_REPLY[status].format(uid=uid, cur="USDC или евро" if exchange == "okx" else "USDT")

    def notify_uids(self, token: str, owner_chat: int | None) -> int:
        """Tell the owner about each new UID once: verified ones for his records, «pending» ones to check by hand."""
        if not owner_chat:
            return 0
        try:
            rows = self.conn.execute("SELECT rowid, username, chat_id, uid, status FROM bot_uids WHERE notified=0").fetchall()
        except sqlite3.OperationalError:
            return 0
        n = 0
        for rid, un, cid, uid, st in rows:
            who = f"@{un}" if un else f"id {cid}"
            try:
                ex = (json.loads(self.conn.execute("SELECT detail FROM bot_uids WHERE rowid=?", (rid,)).fetchone()[0] or "{}") or {}).get("exchange")
            except Exception:  # noqa: BLE001
                ex = None
            exn = "OKX" if ex == "okx" else "BingX"
            ru = {"ok": "подтверждён (по ссылке CRYPTO NOVA, пополнен)", "no_deposit": "по нашей ссылке, но не пополнен",
                  "not_invited": "открыт не по нашей ссылке",
                  "pending": f"нужна ручная проверка в партнёрском кабинете {exn} (ключ партнёра не подключён)"}
            try:
                _api(token, "sendMessage", {"chat_id": owner_chat, "text": f"UID {exn} от {who}: {uid} — {ru.get(st, st)}."})
                self.conn.execute("UPDATE bot_uids SET notified=1 WHERE rowid=?", (rid,))
                n += 1
            except Exception:  # noqa: BLE001
                pass
        self.conn.commit()
        return n

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
                                            "text": f"Заявка на подключение: <b>{connect_name(what)}</b>\nОт: {who}"
                                                    + (f" (<a href=\"tg://user?id={cid}\">написать</a>)" if not un else "")})
                self.conn.execute("UPDATE bot_leads SET notified=1 WHERE rowid=?", (rid,))
                n += 1
            except Exception:  # noqa: BLE001
                pass
        self.conn.commit()
        return n

    def poll(self, token: str, answer_fn=None, now: int | None = None, owner_username: str | None = None) -> dict:
        """Read new updates, welcome newcomers, answer messages (answer_fn(text, chat) -> reply or None), remember chats."""
        now = now or int(time.time() * 1000)
        offset = int(self.meta("offset", "0") or 0)
        try:
            res = _api(token, "getUpdates", {"offset": offset, "timeout": 0,
                                             "allowed_updates": ["message", "my_chat_member", "channel_post"]})
        except Exception as e:  # noqa: BLE001
            err = str(e)[:200]
            if "409" in err:                 # a webhook is set somewhere → getUpdates gets nothing; say where it points (no token)
                try:
                    w = _api(token, "getWebhookInfo").get("result") or {}
                    err += f" · webhook: {(w.get('url') or '')[:60]}"
                except Exception:  # noqa: BLE001
                    pass
            self.set_meta("last_poll", json.dumps({"t": now, "ok": False, "err": err}))
            return {"ok": False, "error": err}
        self.set_meta("last_poll", json.dumps({"t": now, "ok": True, "n": len(res.get("result") or []),
                                               "types": sorted({k for u in res.get("result") or [] for k in u if k != "update_id"})}))
        n_new = n_reply = 0
        for u in res.get("result", []):
            offset = max(offset, u["update_id"] + 1)
            if u.get("my_chat_member"):
                msg = self.channel_event(u["my_chat_member"], owner_username)
                if msg:
                    self.notes.append(msg)
                continue
            if u.get("channel_post"):
                msg = self.connect_checked(token, (u["channel_post"].get("chat") or {}), owner_username)
                if msg:
                    self.notes.append(msg)
                continue
            m = u.get("message") or {}
            chat = m.get("chat") or {}
            if chat.get("type") in ("group", "supergroup"):
                if m.get("migrate_to_chat_id") and str(chat.get("id")) == self.meta("signals_chat"):
                    self.set_meta("signals_chat", str(m["migrate_to_chat_id"]))      # a group became a supergroup: new id
                else:
                    msg = self.connect_checked(token, chat, owner_username)
                    if msg:
                        self.notes.append(msg)
                continue
            if chat.get("type") != "private":
                continue
            cid = chat["id"]; frm = m.get("from") or {}
            src = (m.get("forward_origin") or {}).get("chat") or m.get("forward_from_chat")
            if src and src.get("type") == "channel":       # the owner forwards a post from his signals channel to the bot
                own = self.owner_chat(owner_username)
                is_owner = (own is not None and frm.get("id") == own) or (
                    owner_username and (frm.get("username") or "").lower() == owner_username.lstrip("@").lower())
                if is_owner:
                    msg = self.connect_checked(token, src, owner_username) or (
                        f"Канал «{src.get('title') or ''}» уже подключён." if str(src.get("id")) == self.meta("signals_chat") else
                        "Не получилось подключить канал: бот должен быть администратором канала, а вы — его владельцем или администратором.")
                    try:
                        _api(token, "sendMessage", {"chat_id": cid, "text": msg})
                    except Exception:  # noqa: BLE001
                        pass
                    continue
            row = self.conn.execute("SELECT n_msgs FROM bot_chats WHERE chat_id=?", (cid,)).fetchone()
            if row:
                self.conn.execute("UPDATE bot_chats SET last_seen=?, n_msgs=n_msgs+1, username=COALESCE(?,username) WHERE chat_id=?", (now, frm.get("username"), cid))
            else:
                self.conn.execute("INSERT INTO bot_chats VALUES(?,?,?,?,?,1)", (cid, frm.get("username"), frm.get("first_name"), now, now)); n_new += 1
            text = (m.get("text") or "").strip()
            reply, markup = None, None
            own = self.owner_chat(owner_username)
            is_owner = (own is not None and frm.get("id") == own) or (
                owner_username and (frm.get("username") or "").lower() == owner_username.lstrip("@").lower())
            if is_owner and not self.meta("signals_chat") and not text.startswith("/start connect_"):
                reply = ("Канал сигналов ещё не подключён. Перешлите мне сюда любой пост из вашего канала (стрелка справа от поста → "
                         "этот бот) — я проверю, что вы его администратор, и подключу. Бот должен быть администратором канала.")
            elif text.startswith("/start connect_"):
                what = text.split("connect_", 1)[1].strip().split()[0][:40]
                reply, markup = REGION_Q.format(name=connect_name(what)), REGION_KB      # decision B: ask where the person lives first
                self.set_region(cid, self.region(cid), now, what)
                self.lead(cid, frm.get("username"), frm.get("first_name"), what, now)
            elif EU_RE.match(text) or WORLD_RE.match(text):
                eu = bool(EU_RE.match(text))
                what = self.set_region(cid, "eu" if eu else "world", now) or "cryptonova"
                reply = (CONNECT_OKX.format(name=connect_name(what), ref=exchange_ref("okx_ref")) if eu
                         else CONNECT.format(name=connect_name(what), ref=exchange_ref()))
                markup = {"remove_keyboard": True}
            elif text.startswith("/start"):
                reply = WELCOME
            elif text.startswith("/help"):
                reply = HELP
            elif text.startswith("/channel"):
                reply = "Канал CRYPTO NOVA: https://t.me/crypto_nova_news"
            elif uid_of(text):
                reply = self.uid(cid, frm.get("username"), uid_of(text), now)
            elif re.search(r"(?i)\buid\b|\bюид\b", text) and not re.search(r"\d{6,12}", text):
                reply = "Пришлите UID цифрами: это номер вашего счёта на бирже — в приложении биржи откройте профиль, UID рядом с именем."
            elif text:
                reply = answer_fn(text, {"chat_id": cid, "username": frm.get("username")}) if answer_fn else None
                if not reply:
                    reply = "Спасибо, сообщение получено — ответим в течение дня. Обзоры рынка: @crypto_nova_news"
            if reply:
                try:
                    _api(token, "sendMessage", {"chat_id": cid, "text": reply, "parse_mode": "HTML", "disable_web_page_preview": True,
                                                **({"reply_markup": markup} if markup else {})}); n_reply += 1
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

    owner_un = owner or os.environ.get("CNQ_TG_OWNER") or "dmytrosukhyna"
    res = mb.poll(token, answer, owner_username=owner_un)
    res["profile_set"] = prof
    res["owner_chat"] = mb.owner_chat(owner_un)
    for msg in mb.notes:
        if res["owner_chat"]:
            try:
                _api(token, "sendMessage", {"chat_id": res["owner_chat"], "text": msg})
            except Exception:  # noqa: BLE001
                pass
    res["signals_chat"] = bool(mb.meta("signals_chat"))
    res["leads_sent"] = mb.notify_leads(token, res["owner_chat"])
    res["uids_sent"] = mb.notify_uids(token, res["owner_chat"])
    return res
