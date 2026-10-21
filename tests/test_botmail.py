import sqlite3
from cnq.agents import botmail


def test_mailbox_welcomes_and_remembers(monkeypatch=None):
    conn = sqlite3.connect(":memory:")
    calls = []
    def fake_api(token, method, data=None, timeout=20):
        calls.append((method, data))
        if method == "getUpdates":
            return {"ok": True, "result": [
                {"update_id": 10, "message": {"chat": {"id": 555, "type": "private"}, "from": {"username": "dmytro", "first_name": "D"}, "text": "/start"}},
                {"update_id": 11, "message": {"chat": {"id": 555, "type": "private"}, "from": {"username": "dmytro"}, "text": "когда доступ?"}},
                {"update_id": 12, "message": {"chat": {"id": -100, "type": "channel"}, "text": "ignored"}}]}
        return {"ok": True}
    botmail._api = fake_api
    mb = botmail.Mailbox(conn)
    assert mb.setup_profile("t") and not mb.setup_profile("t")          # once per version
    r = mb.poll("t", lambda text, who: "скоро" if "доступ" in text else None)
    assert r["new_chats"] == 1 and r["replies"] == 2 and r["chats"] == 1
    sent = [d for m, d in calls if m == "sendMessage"]
    assert "CRYPTO NOVA" in sent[0]["text"] and sent[1]["text"] == "скоро"
    assert mb.owner_chat("@dmytro") == 555 and mb.meta("offset") == "13"
    # next poll starts after the last update
    calls.clear(); mb.poll("t")
    assert [d for m, d in calls if m == "getUpdates"][0]["offset"] == 13


def test_connect_deep_link_explains_terms_and_tells_the_owner_once():
    conn = sqlite3.connect(":memory:")
    calls = []

    def fake_api(token, method, data=None, timeout=20):
        calls.append((method, data))
        if method == "getUpdates":
            return {"ok": True, "result": [
                {"update_id": 1, "message": {"chat": {"id": 777, "type": "private"}, "from": {"username": "ivan_k", "first_name": "I"},
                                             "text": "/start connect_cryptonova"}}]}
        return {"ok": True}
    botmail._api = fake_api
    mb = botmail.Mailbox(conn)
    mb.poll("t")
    reply = [d for m, d in calls if m == "sendMessage"][0]["text"]
    assert "CRYPTO NOVA (отобранные сигналы)" in reply and "бесплатно" not in reply and "пошаговая инструкция" in reply.lower()
    assert "10%" not in reply and "ключ" not in reply.lower()       # 21.10 owner: referral only — no fee, no API keys
    calls.clear()
    assert mb.notify_leads("t", 555) == 1 and mb.notify_leads("t", 555) == 0
    note = [d for m, d in calls if m == "sendMessage"][0]
    assert note["chat_id"] == 555 and "@ivan_k" in note["text"] and "CRYPTO NOVA" in note["text"]


def test_connect_name_reads_the_users_model_and_options():
    assert botmail.connect_name("capital_bold_r_d") == "CRYPTO NOVA Портфолио · модель «Смелая» + рискованные монеты, больше DeFi-кредитования"
    assert botmail.connect_name("capital_calm") == "CRYPTO NOVA Портфолио · модель «Спокойная»"
    assert botmail.connect_name("cryptonova").startswith("CRYPTO NOVA") and botmail.connect_name("zzz") == "стратегия CRYPTO NOVA"


def test_owner_connects_a_signals_channel_by_adding_the_bot():
    import sqlite3
    from cnq.agents import botmail, signal_feed
    conn = sqlite3.connect(":memory:")
    mb = botmail.Mailbox(conn)
    conn.execute("INSERT INTO bot_chats VALUES(?,?,?,?,?,1)", (111, "owner_x", "D", 0, 0))
    ev = lambda title, uid, un, status="administrator", cid=-1001: {"chat": {"id": cid, "type": "channel", "title": title},  # noqa: E731
                                                                    "from": {"id": uid, "username": un}, "new_chat_member": {"status": status}}
    assert mb.channel_event(ev("CRYPTO NOVA Signals", 999, "stranger"), "owner_x") is None          # not the owner
    assert mb.channel_event(ev("CRYPTO NOVA Signals", 111, "owner_x"), "owner_x").startswith("Канал «CRYPTO NOVA Signals» подключён")
    assert signal_feed.signals_chat(conn) == "-1001"
    assert mb.channel_event(ev("Новости", 111, "owner_x", cid=-1009), "owner_x") is None             # another channel while one is on
    assert signal_feed.signals_chat(conn) == "-1001"
    assert "убран" in mb.channel_event(ev("CRYPTO NOVA Signals", 111, "owner_x", "left"), "owner_x")
    assert signal_feed.signals_chat(conn) is None


def test_channel_connects_by_a_post_or_a_forward_only_when_the_owner_is_its_admin():
    import sqlite3
    from cnq.agents import botmail, signal_feed
    conn = sqlite3.connect(":memory:")
    mb = botmail.Mailbox(conn)
    conn.execute("INSERT INTO bot_chats VALUES(?,?,?,?,?,1)", (111, "owner_x", "D", 0, 0))
    admins = {-1002: [{"user": {"id": 999, "username": "stranger"}, "status": "creator"}],
              -1003: [{"user": {"id": 111, "username": "owner_x"}, "status": "creator"}, {"user": {"id": 5, "is_bot": True}}]}
    api = lambda token, method, data: {"ok": True, "result": admins.get(data["chat_id"], [])}  # noqa: E731
    ch = lambda cid, title: {"id": cid, "type": "channel", "title": title}  # noqa: E731
    assert mb.connect_checked("t", ch(-1002, "Best Signals"), "owner_x", api) is None            # a stranger's channel
    assert "подключён" in mb.connect_checked("t", ch(-1003, "CRYPTO NOVA FUTURES"), "owner_x", api)   # the owner's real channel
    assert signal_feed.signals_chat(conn) == "-1003"
    assert mb.connect_checked("t", ch(-1004, "Новости"), "owner_x", api) is None                 # not a signals channel, one is on
    assert mb.connect_checked("t", ch(-1003, "CRYPTO NOVA FUTURES"), "owner_x", api) is None     # already connected → no repeat note


def test_poll_connects_the_channel_from_the_owners_forward(monkeypatch):
    import sqlite3
    from cnq.agents import botmail, signal_feed
    conn = sqlite3.connect(":memory:")
    mb = botmail.Mailbox(conn)
    conn.execute("INSERT INTO bot_chats VALUES(?,?,?,?,?,1)", (111, "owner_x", "D", 0, 0))
    sent = []
    src = {"id": -1003, "type": "channel", "title": "CRYPTO NOVA Signals"}

    def api(token, method, data=None, timeout=20):
        if method == "getUpdates":
            return {"ok": True, "result": [{"update_id": 7, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "username": "owner_x"},
                                                                         "forward_origin": {"type": "channel", "chat": src}, "text": "x"}}]}
        if method == "getChatAdministrators":
            return {"ok": True, "result": [{"user": {"id": 111}, "status": "creator"}]}
        sent.append(data)
        return {"ok": True}
    monkeypatch.setattr(botmail, "_api", api)
    mb.poll("t", owner_username="owner_x")
    assert signal_feed.signals_chat(conn) == "-1003"
    assert "подключён" in sent[0]["text"]


def test_owner_dm_without_a_channel_gets_the_forward_instruction_and_the_poll_is_recorded(monkeypatch):
    import json as _json
    import sqlite3
    from cnq.agents import botmail
    conn = sqlite3.connect(":memory:")
    mb = botmail.Mailbox(conn)
    conn.execute("INSERT INTO bot_chats VALUES(?,?,?,?,?,1)", (111, "owner_x", "D", 0, 0))
    sent = []

    def api(token, method, data=None, timeout=20):
        if method == "getUpdates":
            return {"ok": True, "result": [{"update_id": 9, "message": {"chat": {"id": 111, "type": "private"},
                                                                         "from": {"id": 111, "username": "owner_x"}, "text": "Старт"}}]}
        sent.append(data)
        return {"ok": True}
    monkeypatch.setattr(botmail, "_api", api)
    mb.poll("t", owner_username="owner_x")
    assert "Перешлите мне" in sent[0]["text"]
    lp = _json.loads(mb.meta("last_poll"))
    assert lp["ok"] and lp["n"] == 1 and lp["types"] == ["message"]


def test_uid_is_checked_and_answered_honestly_and_the_owner_hears_once():
    """21.10 22:52 owner: «бот перепроверит UID и подключит?» — a UID is checked with the BingX partner API; without the partner key
    it waits for the owner (24 h). Only the number is stored."""
    conn = sqlite3.connect(":memory:")
    calls = []

    def fake_api(token, method, data=None, timeout=20):
        calls.append((method, data))
        if method == "getUpdates":
            return {"ok": True, "result": [
                {"update_id": 1, "message": {"chat": {"id": 901, "type": "private"}, "from": {"username": "anna"}, "text": "UID: 123456789"}},
                {"update_id": 2, "message": {"chat": {"id": 902, "type": "private"}, "from": {"username": "bob"}, "text": "мой uid какой?"}}]}
        return {"ok": True}
    botmail._api = fake_api
    assert botmail.uid_of("123456789") == "123456789" and botmail.uid_of("uid 12345678") == "12345678"
    assert botmail.uid_of("BingX UID: 98765432") == "98765432" and botmail.uid_of("12345") is None and botmail.uid_of("цена 123456789 $") is None
    mb = botmail.Mailbox(conn)
    import os
    os.environ.pop("CNQ_BINGX_AGENT_KEY", None); os.environ.pop("CNQ_BINGX_AGENT_SECRET", None)
    mb.poll("t")
    sent = [d for m, d in calls if m == "sendMessage"]
    assert "123456789" in sent[0]["text"] and "24 час" in sent[0]["text"]          # no partner key → honest manual check
    assert "цифрами" in sent[1]["text"]
    row = conn.execute("SELECT uid, status FROM bot_uids").fetchone()
    assert row == ("123456789", "pending")
    calls.clear()
    assert mb.notify_uids("t", 555) == 1 and mb.notify_uids("t", 555) == 0
    note = [d for m, d in calls if m == "sendMessage"][0]
    assert note["chat_id"] == 555 and "@anna" in note["text"] and "123456789" in note["text"]
    # with the partner answer: verified / not deposited / not ours
    assert "подтверждён" in mb.uid(1, "x", "11111111", 0, check=lambda u: {"ok": True, "reason": "ok", "invited": True, "deposited": True})
    assert "не пополнен" in mb.uid(1, "x", "22222222", 0, check=lambda u: {"ok": False, "reason": "no_deposit", "invited": True, "deposited": False})
    assert "не по ссылке" in mb.uid(1, "x", "33333333", 0, check=lambda u: {"ok": False, "reason": "not_invited"})
    assert "вручную" in mb.uid(1, "x", "44444444", 0, check=lambda u: (_ for _ in ()).throw(RuntimeError("net")))
