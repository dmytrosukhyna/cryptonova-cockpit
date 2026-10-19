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


def setup(tmp_path, prefs=None):
    web = tmp_path / "web"
    web.mkdir()
    api = str(tmp_path / "api.sqlite")
    st = Store(api)
    st.upsert_user("u1", None, None)
    st.add_push("u1", "tok-" + "x" * 30, "ios")
    if prefs:
        st.set_prefs("u1", prefs)
    return web, api, sqlite3.connect(str(tmp_path / "agents.sqlite"))


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
