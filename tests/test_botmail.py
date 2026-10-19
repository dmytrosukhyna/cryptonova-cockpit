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
    assert "CRYPTO NOVA (отобранные сигналы)" in reply and "10% от прибыли за месяц" in reply and "без права вывода" in reply
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
    assert mb.channel_event(ev("Новости", 111, "owner_x"), "owner_x") is None                        # not a signals channel
    assert mb.channel_event(ev("CRYPTO NOVA Signals", 111, "owner_x"), "owner_x").startswith("Канал «CRYPTO NOVA Signals» подключён")
    assert signal_feed.signals_chat(conn) == "-1001"
    assert "убран" in mb.channel_event(ev("CRYPTO NOVA Signals", 111, "owner_x", "left"), "owner_x")
    assert signal_feed.signals_chat(conn) is None
