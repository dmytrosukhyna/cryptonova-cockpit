import datetime as dt
import json
import sqlite3

from cnq.agents import app_push
from cnq.api.store import Store


class FakeFcm:
    def __init__(self):
        self.sent = []

    def send(self, token, title, body, data=None):
        self.sent.append((token, title, body))
        return "ok"


def ms(h, m=0, day=19, weekday_fri=False):
    d = dt.datetime(2026, 10, 23 if weekday_fri else day, h, m, tzinfo=app_push.KYIV)
    return int(d.timestamp() * 1000)


def paper(opens=(), closes=()):
    return {"profiles": {"bal": {"open": [{"id": o, "symbol": o, "side": "LONG", "entry": 0.1755, "stop": 0.1544, "tps": [0.2632], "opened": 1}
                                          for o in opens],
                                 "closed": [{"id": c, "symbol": c, "pnl_usd": 2.04, "closed": 2} for c in closes]}}}


ON = {"trades": True, "news": True, "brief": True}        # what this person switched on in the app


def setup(tmp_path, prefs=None):
    web = tmp_path / "web"
    web.mkdir()
    api = str(tmp_path / "api.sqlite")
    st = Store(api)
    st.upsert_user("u1", None, None)
    st.add_push("u1", "tok-" + "x" * 30, "ios")
    if prefs != {}:
        st.set_prefs("u1", ON if prefs is None else prefs)
    return web, api, sqlite3.connect(str(tmp_path / "agents.sqlite"))


def test_default_settings_send_nothing(tmp_path):
    """28.10 (spec §14): a person who never touched the switches gets no push of any kind"""
    web, api, conn = setup(tmp_path, {})
    st = Store(api)
    assert st.prefs("u1") == {"trades": False, "news": False, "piggy": False, "brief": False}
    st.set_piggy("u1", {"amount": 20, "freq": "week", "on": True, "start_day": int(ms(10, 2, weekday_fri=True) // 86_400_000)})
    (web / "paper.json").write_text(json.dumps({"profiles": {"bal": {"open": [], "closed": [], "equity_history": [
        {"time": ms(12, day=16), "equity": 10000}, {"time": ms(17, day=23), "equity": 10100}]}}}))
    f = FakeFcm()
    app_push.run(conn, api, str(web), ms(9, day=23), f)                   # first run: silent
    (web / "markets.json").write_text(json.dumps({"top": [{"symbol": "BTC", "price": 70000, "chg24": -9.0}]}))
    doc = json.loads((web / "paper.json").read_text())
    doc["profiles"]["bal"]["open"] = paper(["ARB"])["profiles"]["bal"]["open"]
    (web / "paper.json").write_text(json.dumps(doc))
    for h, m in ((10, 2), (12, 0), (18, 2)):                              # piggy day, big move, new trade, Friday summary
        app_push.run(conn, api, str(web), ms(h, m, weekday_fri=True), f)
    assert f.sent == []
    st.set_prefs("u1", {"trades": True})                                  # the person turns one switch on → only that one
    assert st.prefs("u1") == {"trades": True, "news": False, "piggy": False, "brief": False}
    doc["profiles"]["bal"]["open"] = paper(["ARB", "SEI"])["profiles"]["bal"]["open"]
    (web / "paper.json").write_text(json.dumps(doc))
    app_push.run(conn, api, str(web), ms(18, 3, weekday_fri=True), f)
    assert len(f.sent) == 1 and "SEI" in f.sent[0][2]


def test_saved_settings_are_kept_and_only_sent_switches_are_stored(tmp_path):
    st = Store(str(tmp_path / "api.sqlite"))
    st._put("prefs", "old", {"trades": True, "news": True, "piggy": False, "brief": True})   # saved by the app before 28.10
    assert st.prefs("old") == {"trades": True, "news": True, "piggy": False, "brief": True}
    st.set_prefs("old", {"news": False})
    assert st.prefs("old") == {"trades": True, "news": False, "piggy": False, "brief": True}
    st.set_prefs("new", {"piggy": True, "hack": 1})
    assert st._doc("prefs", "new", None) == {"piggy": True}                # defaults are not written into the row
    assert [u for u, _ in st.push_tokens(pref="news")] == []


def test_first_run_is_silent_then_new_trade_sends(tmp_path):
    web, api, conn = setup(tmp_path)
    (web / "paper.json").write_text(json.dumps(paper(["XRP"])))
    f = FakeFcm()
    assert app_push.run(conn, api, str(web), ms(12), f)["first_run"]
    assert f.sent == []
    (web / "paper.json").write_text(json.dumps(paper(["XRP", "ARB"])))
    r = app_push.run(conn, api, str(web), ms(12, 5), f)
    assert r["sent"] == 1 and "ARB" in f.sent[0][2] and "демо" in f.sent[0][1]
    app_push.run(conn, api, str(web), ms(12, 6), f)          # nothing new → nothing sent
    assert len(f.sent) == 1


def test_quiet_hours_and_prefs(tmp_path):
    web, api, conn = setup(tmp_path, {"trades": False})
    (web / "paper.json").write_text(json.dumps(paper([])))
    f = FakeFcm()
    app_push.run(conn, api, str(web), ms(12), f)
    (web / "paper.json").write_text(json.dumps(paper(["ARB"])))
    app_push.run(conn, api, str(web), ms(12, 1), f)          # trades switched off
    assert f.sent == []
    web2, api2, conn2 = setup(tmp_path / "b") if (tmp_path / "b").mkdir() is None else (None, None, None)
    (web2 / "paper.json").write_text(json.dumps(paper([])))
    app_push.run(conn2, api2, str(web2), ms(12), f)
    (web2 / "paper.json").write_text(json.dumps(paper(["ARB"])))
    assert app_push.run(conn2, api2, str(web2), ms(23), f)["quiet"]
    assert f.sent == []


def test_bundle_and_cap(tmp_path):
    web, api, conn = setup(tmp_path)
    (web / "paper.json").write_text(json.dumps(paper([])))
    f = FakeFcm()
    app_push.run(conn, api, str(web), ms(10, 30), f)
    (web / "paper.json").write_text(json.dumps(paper(["A", "B", "C"], ["D"])))
    app_push.run(conn, api, str(web), ms(10, 31), f)
    assert len(f.sent) == 1 and "4 события" in f.sent[0][1]
    for i in range(10):
        (web / "paper.json").write_text(json.dumps(paper([f"N{i}"])))
        app_push.run(conn, api, str(web), ms(11, i), f)
    assert len(f.sent) == app_push.TRADE_CAP


def test_big_move_news_once_a_day_and_brief_dedupe(tmp_path):
    web, api, conn = setup(tmp_path)
    (web / "paper.json").write_text(json.dumps(paper()))
    f = FakeFcm()
    app_push.run(conn, api, str(web), ms(12), f)                      # first run: silent
    (web / "markets.json").write_text(json.dumps({"top": [{"symbol": "BTC", "price": 76000, "chg24": -6.4}]}))
    app_push.run(conn, api, str(web), ms(9, 30), f)                   # before 10:00 → nothing
    assert f.sent == []
    app_push.run(conn, api, str(web), ms(12, 1), f)
    app_push.run(conn, api, str(web), ms(15, 0), f)                   # same day → still one
    assert len(f.sent) == 1 and "упал на 6%" in f.sent[0][1] and "76 000" in f.sent[0][2]
    (web / "markets.json").write_text(json.dumps({"top": [{"symbol": "BTC", "price": 80000, "chg24": 2.0}]}))
    app_push.run(conn, api, str(web), ms(12, 0, day=20), f)           # small move → nothing
    assert len(f.sent) == 1
    # news switched off → nothing even on a big move
    Store(api).set_prefs("u1", {"news": False})
    (web / "markets.json").write_text(json.dumps({"top": [{"symbol": "BTC", "price": 90000, "chg24": 9.0}]}))
    app_push.run(conn, api, str(web), ms(12, 0, day=21), f)
    assert len(f.sent) == 1


def test_new_source_is_seeded_silently_then_its_new_trades_send(tmp_path):
    web, api, conn = setup(tmp_path)
    (web / "paper.json").write_text(json.dumps(paper(["XRP"])))
    f = FakeFcm()
    app_push.run(conn, api, str(web), ms(12), f)                         # first run ever: silent
    own_open = lambda ids: [{"id": i, "symbol": i, "side": "LONG", "entry": 1.0, "stop": 0.85, "opened": 3} for i in ids]
    doc = paper(["XRP"])
    doc["profiles"]["own"] = {"open": own_open(["AVAX", "FIL", "APT"]), "closed": []}
    (web / "paper.json").write_text(json.dumps(doc))
    app_push.run(conn, api, str(web), ms(12, 5), f)                      # «own» appears with 3 old trades → nothing sent
    assert f.sent == []
    doc["profiles"]["own"]["open"] = own_open(["AVAX", "FIL", "APT", "SEI"])
    (web / "paper.json").write_text(json.dumps(doc))
    r = app_push.run(conn, api, str(web), ms(12, 10), f)                 # a genuinely new own trade → one push
    assert r["sent"] == 1 and "SEI" in f.sent[0][2]
