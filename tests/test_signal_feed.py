import json
import sqlite3

from cnq.agents import signal_feed as SF

T0 = 1_792_000_000_000


def trade(i, grade="F", side="LONG", entry=100.0, stop=94.0, **kw):
    return {"id": f"{grade}-X{i}", "symbol": f"X{i}", "side": side, "grade": grade, "entry": entry, "stop": stop,
            "tps": json.dumps([150.0, 150.0, 150.0]), "qty": 10.0, "opened": T0, **kw}


def test_first_run_seeds_then_new_trades_post_and_close_as_reply():
    conn = sqlite3.connect(":memory:")
    sent = []

    def send(text, reply_to=None):
        sent.append((text, reply_to)); return 100 + len(sent)

    # first run: a trade already open → recorded, not posted (it is hours old)
    r = SF.step(conn, {"open": [trade(1)]}, T0, send=send)
    assert r["opened"] == ["F-X1"] and sent == []
    # new trade → posted in Cornix format; targets +1R/+2R/+3R for a time-exit rule
    r = SF.step(conn, {"open": [trade(1), trade(2)]}, T0 + 1, send=send)
    assert r["opened"] == ["F-X2"] and len(sent) == 1
    txt = sent[0][0]
    assert "#X2/USDT" in txt and "\nLong\n" in txt and "Leverage: Isolated 3x" in txt
    assert "Take-Profit Targets:\n1) 106.00\n2) 112.00\n3) 118.00\nStop Targets:\n1) 94.00" in txt
    # it closes on the desk by time → «Close» as a reply to the open message, with the % move
    closed = dict(trade(2), closed=T0 + 86_400_000, exit_kind="TIME", pnl_usd=20.0)
    r = SF.step(conn, {"open": [trade(1)], "closed": [closed]}, T0 + 2, send=send)
    assert r["closed"] == ["F-X2"] and sent[-1][1] == 101 and sent[-1][0].startswith("Close #X2/USDT — время вышло, +2.00%")
    pub = SF.public(conn, T0 + 3)
    assert pub["stats"] == {"signals": 1, "closed": 1, "wins": 1, "avg_pct": 2.0}
    assert {i["id"] for i in pub["items"]} == {"F-X1", "F-X2"}
    json.dumps(pub)


def test_cascade_short_uses_desk_targets_descending():
    t = trade(3, grade="B", side="SHORT", entry=100.0, stop=103.0, tps=json.dumps([97.0, 97.0, 97.0]))
    txt = SF.open_text(t)
    assert "Short" in txt and "Leverage: Isolated 5x" in txt and "Take-Profit Targets:\n1) 97.00\nStop" in txt and "24 ч" not in txt


def test_mix_seeds_each_new_source_and_drops_a_losing_one():
    conn = sqlite3.connect(":memory:")
    sent = []
    send = lambda text, reply_to=None: sent.append(text) or 1
    SF.step(conn, {"open": [trade(1)]}, T0, send=send, source="bal")             # first run of bal → seeded
    SF.step(conn, {"open": [trade(2, grade="K")]}, T0, send=send, source="imp")  # first run of imp → seeded too
    assert sent == []
    SF.step(conn, {"open": [trade(3, grade="K")]}, T0 + 1, send=send, source="imp")
    assert len(sent) == 1 and "12 ч" in sent[0] and "альт догоняет рывок BTC" in sent[0]
    # 10 closed losers in 30 days → imp drops out of the mix; its new trades are recorded but not posted
    for i in range(10, 20):
        SF.step(conn, {"open": [trade(i, grade="K")]}, T0 + 2, send=send, source="imp")
        SF.step(conn, {"open": [], "closed": [dict(trade(i, grade="K"), closed=T0 + 3, exit_kind="STOP", pnl_usd=-10.0)]}, T0 + 3, send=send, source="imp")
    ok, st = SF.live_ok(conn, "imp", T0 + 4)
    assert not ok and st["n"] == 10 and st["avg_pct"] < 0
    pub = SF.public(conn, T0 + 4)
    assert pub["name"] == "CRYPTO NOVA" and [m["in_mix"] for m in pub["mix"] if m["key"] == "imp"] == [False]
