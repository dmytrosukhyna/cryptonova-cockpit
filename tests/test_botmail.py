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
