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
    first = [d for m, d in calls if m == "sendMessage"][0]
    assert "CRYPTO NOVA (отобранные сигналы)" in first["text"] and "Где вы живёте" in first["text"]   # 22.10 decision B: region first
    assert first["reply_markup"]["keyboard"][0][0]["text"] == "Живу в ЕС"
    # the answer «другая страна» → the BingX steps for the strategy asked before
    calls.clear()
    def answer_api(token, method, data=None, timeout=20):
        calls.append((method, data))
        if method == "getUpdates":
            return {"ok": True, "result": [{"update_id": 2, "message": {"chat": {"id": 777, "type": "private"}, "from": {"username": "ivan_k"},
                                                                       "text": "Живу в другой стране"}}]}
        return {"ok": True}
    botmail._api = answer_api
    mb.poll("t")
    reply = [d for m, d in calls if m == "sendMessage"][0]["text"]
    assert "CRYPTO NOVA (отобранные сигналы)" in reply and "бесплатно" not in reply and "пошаговая инструкция" in reply.lower()
    assert "BingX" in reply and "10%" not in reply and "ключ" not in reply.lower()       # 21.10 owner: referral only — no fee, no API keys
    botmail._api = fake_api
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


def test_eu_resident_gets_okx_never_bingx_and_the_uid_goes_to_okx():
    """22.10 decision B: an EU resident is pointed to OKX (USDC/EUR, X-Perps) and never sees BingX; the UID is checked as an OKX UID."""
    conn = sqlite3.connect(":memory:")
    calls = []
    msgs = iter([["/start connect_capital"], ["Живу в ЕС"], ["12345678"]])

    def fake_api(token, method, data=None, timeout=20):
        calls.append((method, data))
        if method == "getUpdates":
            batch = next(msgs, [])
            return {"ok": True, "result": [{"update_id": 10 + i, "message": {"chat": {"id": 31, "type": "private"}, "from": {"username": "eva"},
                                                                         "text": t}} for i, t in enumerate(batch)]}
        return {"ok": True}
    botmail._api = fake_api
    import os
    for k in ("CNQ_OKX_AFF_KEY", "CNQ_OKX_AFF_SECRET", "CNQ_OKX_AFF_PASSPHRASE"):
        os.environ.pop(k, None)
    mb = botmail.Mailbox(conn)
    mb.poll("t"); mb.poll("t")
    okx = [d for m, d in calls if m == "sendMessage"][-1]
    assert "OKX" in okx["text"] and "USDC" in okx["text"] and "X-Perps" in okx["text"] and "bingx" not in okx["text"].lower()
    assert "okx.com/eu" in okx["text"] and okx["reply_markup"] == {"remove_keyboard": True}
    assert mb.region(31) == "eu"
    mb.poll("t")
    uid_reply = [d for m, d in calls if m == "sendMessage"][-1]["text"]
    assert "12345678" in uid_reply and "24 час" in uid_reply and "bingx" not in uid_reply.lower()
    st, detail = conn.execute("SELECT status, detail FROM bot_uids").fetchone()
    assert st == "pending" and '"exchange": "okx"' in detail
    calls.clear()
    assert mb.notify_uids("t", 555) == 1
    assert "UID OKX" in [d for m, d in calls if m == "sendMessage"][0]["text"]
    # the region answers are recognised, other text is not
    assert botmail.EU_RE.match("ЕС") and botmail.EU_RE.match("Живу в ЕС") and botmail.WORLD_RE.match("другая страна")
    assert not botmail.EU_RE.match("что в ЕС с налогами?") and not botmail.WORLD_RE.match("привет")
    # an OKX UID with no deposit asks for USDC or euro, not USDT
    assert "USDC или евро" in mb.uid(31, "eva", "87654321", 0, check=lambda u: {"ok": False, "reason": "no_deposit", "invited": True})


def test_okx_affiliate_check_reads_the_invitee_detail():
    from cnq.api import okx_aff
    assert okx_aff.check_invitee("123", env={}) == {"ok": False, "reason": "not_configured"}
    seen = {}

    def fetch(url, headers):
        seen["url"], seen["h"] = url, headers
        return {"code": "0", "data": [{"depAmt": "250.5", "kycTime": "1700000000000", "volMonth": "12000", "accFee": "6.1"}]}
    r = okx_aff.check_invitee("123456", fetch=fetch, env={"CNQ_OKX_AFF_KEY": "k", "CNQ_OKX_AFF_SECRET": "s", "CNQ_OKX_AFF_PASSPHRASE": "p"})
    assert r["ok"] and r["reason"] == "ok" and r["kyc"] and r["volume_month"] == 12000
    assert seen["url"] == "https://www.okx.com/api/v5/affiliate/invitee/detail?uid=123456"
    assert seen["h"]["OK-ACCESS-KEY"] == "k" and seen["h"]["OK-ACCESS-PASSPHRASE"] == "p" and seen["h"]["OK-ACCESS-SIGN"]
    env = {"CNQ_OKX_AFF_KEY": "k", "CNQ_OKX_AFF_SECRET": "s", "CNQ_OKX_AFF_PASSPHRASE": "p"}
    assert okx_aff.check_invitee("1", fetch=lambda u, h: {"code": "0", "data": [{"depAmt": "0"}]}, env=env)["reason"] == "no_deposit"
    assert okx_aff.check_invitee("1", fetch=lambda u, h: {"code": "51621", "msg": "not invitee"}, env=env)["reason"] == "not_invited"
    assert okx_aff.check_invitee("1", fetch=lambda u, h: {"code": "50011", "msg": "rate"}, env=env)["reason"] == "exchange_error"
    assert okx_aff.check_invitee("abc", fetch=lambda u, h: {}, env=env)["reason"] == "bad_uid"
