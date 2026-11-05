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


def whales(closes=(), n_open=2):
    """the «Киты» demo desk (whales_top.json) with copies closed at the given (kyiv hour, day, pnl)"""
    return {"desk": {"start": 10000.0, "equity": 10100.0, "open": [{"coin": f"C{i}"} for i in range(n_open)],
                     "closed": [{"coin": c, "pnl": pnl, "closed_t": ms(h, day=day)} for c, h, day, pnl in closes]}}


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
    (web / "whales_top.json").write_text(json.dumps(whales([("ZEC", 12, 23, 104.0)])))
    (web / "markets.json").write_text(json.dumps({"top": [{"symbol": "BTC", "price": 70000, "chg24": -9.0}]}))
    f = FakeFcm()
    for h, m in ((10, 2), (12, 0), (18, 2), (20, 5)):                     # piggy day, big move, Friday summary, evening summary
        app_push.run(conn, api, str(web), ms(h, m, weekday_fri=True), f)
    assert f.sent == []
    st.set_prefs("u1", {"trades": True})                                  # the person turns one switch on → only that one
    assert st.prefs("u1") == {"trades": True, "news": False, "piggy": False, "brief": False}
    app_push.run(conn, api, str(web), ms(20, 6, weekday_fri=True), f)
    assert len(f.sent) == 1 and "ZEC +$104" in f.sent[0][2] and "Киты" in f.sent[0][1]


def test_saved_settings_are_kept_and_only_sent_switches_are_stored(tmp_path):
    st = Store(str(tmp_path / "api.sqlite"))
    st._put("prefs", "old", {"trades": True, "news": True, "piggy": False, "brief": True})   # saved by the app before 28.10
    assert st.prefs("old") == {"trades": True, "news": True, "piggy": False, "brief": True}
    st.set_prefs("old", {"news": False})
    assert st.prefs("old") == {"trades": True, "news": False, "piggy": False, "brief": True}
    st.set_prefs("new", {"piggy": True, "hack": 1})
    assert st._doc("prefs", "new", None) == {"piggy": True}                # defaults are not written into the row
    assert [u for u, _ in st.push_tokens(pref="news")] == []


def test_whale_summary_once_in_the_evening_never_per_trade(tmp_path):
    """05.11: the retired rule desks are not pushed; ONE «Киты» summary a day at 20:00 Kyiv, only if a copy moved ≥ $5"""
    web, api, conn = setup(tmp_path)
    (web / "paper.json").write_text(json.dumps({"profiles": {"bal": {"open": [{"id": "X", "symbol": "XRP", "side": "LONG", "opened": 1}], "closed": []}}}))
    (web / "whales_top.json").write_text(json.dumps(whales([("PEPE", 9, 19, -1.2), ("BTC", 11, 19, 0.8)])))
    f = FakeFcm()
    for h in (12, 15, 20):
        app_push.run(conn, api, str(web), ms(h), f)
    assert f.sent == []                                   # only $1–2 copies today → nothing; retired desks never pushed
    (web / "whales_top.json").write_text(json.dumps(whales([("PEPE", 9, 19, -1.2), ("ZEC", 13, 19, 103.6), ("BNB", 14, 19, -20.0)], n_open=5)))
    app_push.run(conn, api, str(web), ms(15), f)          # before 20:00 → wait for the evening
    assert f.sent == []
    r = app_push.run(conn, api, str(web), ms(20, 3), f)
    assert r["sent"] == 1 and f.sent[0][1] == "Киты за день · демо"
    assert f.sent[0][2] == "Закрыто копий: 3, в плюсе 1; итог +$82 на демо-счёте $10 000. Лучшая: ZEC +$104. В работе 5."
    for m_ in (10, 30, 59):
        app_push.run(conn, api, str(web), ms(20, m_), f)  # the same evening → still one
    assert len(f.sent) == 1
    (web / "whales_top.json").write_text(json.dumps(whales([("ZEC", 13, 20, 50.0)])))
    app_push.run(conn, api, str(web), ms(20, 1, day=20), f)   # next day → the next summary
    assert len(f.sent) == 2


def test_quiet_hours_and_prefs(tmp_path):
    web, api, conn = setup(tmp_path, {"trades": False})
    (web / "whales_top.json").write_text(json.dumps(whales([("ZEC", 13, 19, 103.6)])))
    f = FakeFcm()
    app_push.run(conn, api, str(web), ms(20, 5), f)          # trades switched off
    assert f.sent == []
    assert app_push.run(conn, api, str(web), ms(23), f)["quiet"] and app_push.quiet(ms(8, 59)) and not app_push.quiet(ms(9))
    assert f.sent == []


def test_friday_week_summary_of_the_whale_desk(tmp_path):
    web, api, conn = setup(tmp_path)
    (web / "whales_top.json").write_text(json.dumps(whales([("ZEC", 13, 21, 103.6), ("OP", 10, 22, -40.0)])))
    f = FakeFcm()
    app_push.run(conn, api, str(web), ms(18, 20, weekday_fri=True), f)
    app_push.run(conn, api, str(web), ms(18, 40, weekday_fri=True), f)
    briefs = [x for x in f.sent if x[1] == "Итоги недели · Киты"]
    assert len(briefs) == 1 and "закрыто 2, итог +$64" in briefs[0][2]


def test_big_move_news_once_a_day_and_brief_dedupe(tmp_path):
    web, api, conn = setup(tmp_path)
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


