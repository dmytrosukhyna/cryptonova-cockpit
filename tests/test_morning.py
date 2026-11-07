"""07.11 (owner 16:25): «Утренний доклад» — the 07:00 push and web/morning.json; varied wording, honest numbers, no method words."""
import datetime as dt
import json
import re
import sqlite3

from cnq.agents import app_push, morning as M
from cnq.api.store import Store

METHOD = re.compile(r"кит|копи|трейдер|whale|copy", re.I)


def ms(day, h, m=0):
    return int(dt.datetime(2026, 11, day, h, m, tzinfo=M.KYIV).timestamp() * 1000)


def world(web, now, tot=-51.0, fut=-40.0):
    json.dump({"equity_usd": 9139.3, "pnl_usd": -885.1, "pnl_pct": -8.83, "stale": False, "futures": [{"inst": "ETH-USDT-SWAP"}, {"inst": "ZEC-USDT-SWAP"}],
               "curve": [[now - 30 * 3600_000, -800.0], [now - 25 * 3600_000, -834.1], [now, -834.1 + tot]],
               "fut_curve": [[now - 25 * 3600_000, -100.0], [now, -100.0 + fut]]}, open(web / "public_real.json", "w"))
    json.dump({"top": [{"symbol": "BTC", "price": 75879, "chg24": 0.38}], "fng": {"value": 25, "label": "Extreme Fear"}}, open(web / "markets.json", "w"))


def test_report_numbers_wording_and_no_repeat(tmp_path):
    conn = sqlite3.connect(":memory:")
    seen = []
    for d in range(8, 13):
        now = ms(d, 6, 45)
        world(tmp_path, now, tot=-51.0 if d % 2 else 23.0, fut=-40.0 if d % 2 else 20.0)
        doc = M.build(str(tmp_path), conn, now, audio=False)
        assert doc["day"] == f"2026-11-{d:02d}" and json.load(open(tmp_path / "morning.json"))["day"] == doc["day"]
        b = doc["body"]
        assert ("−$51" in b and "−$40" in b and "−$11" in b) if d % 2 else ("+$23" in b and "+$20" in b and "+$3,00" in b)
        assert "$75 879" in b and "крайний страх" in b and "$9 139" in b and len(b) <= 260 and len(doc["title"]) <= 40
        assert not METHOD.search(b) and not METHOD.search(doc["text"]) and "обещ" not in b
        assert "Твой счёт" in doc["owner"]["body"] or "твоём счёте" in doc["owner"]["body"] or "Счёт" in doc["owner"]["body"]
        seen.append((doc["picks"]["t"], doc["picks"]["m"], doc["picks"]["a"]))
    for a, b in zip(seen, seen[1:]):                           # no part is phrased the same way two days in a row
        assert a[0] != b[0] and a[1] != b[1] and a[2] != b[2]
    assert len({s[0] for s in seen}) == 5                       # 5 mornings, 5 different titles


def test_every_language_file_same_numbers_ai_wording(tmp_path):
    now = ms(8, 6, 45)
    world(tmp_path, now)
    M.build(str(tmp_path), sqlite3.connect(":memory:"), now, audio=False)
    ru = json.load(open(tmp_path / "morning.json", encoding="utf-8"))
    for lang in ("en", "uk", "el", "es"):
        d = json.load(open(tmp_path / f"morning.{lang}.json", encoding="utf-8"))
        assert d["lang"] == lang and d["picks"] == ru["picks"] and d["day"] == ru["day"]
        assert "CRYPTO NOVA AI" in d["title"] and d["body"] and d["owner"]["body"]
        for t in (d["title"], d["body"], d["owner"]["body"], d["text"]):
            assert not METHOD.search(t), (lang, t)
            if lang != "uk":
                assert not re.search(r"[А-Яа-яЁёІіЇїЄє]", t), (lang, t)
        assert "51" in d["body"]
    assert "$9,139" in json.load(open(tmp_path / "morning.en.json"))["body"]
    assert "$9 139" in json.load(open(tmp_path / "morning.es.json"))["body"]
    from cnq import engine
    assert {"morning.en.json", "morning.el.mp3"} <= set(engine.OWNED)


def test_ensure_builds_once_in_the_morning_window(tmp_path):
    conn = sqlite3.connect(":memory:")
    world(tmp_path, ms(9, 6, 0))
    assert M.ensure(str(tmp_path), conn, ms(9, 6, 0), audio=False) is None          # before 06:40 — nothing
    d1 = M.ensure(str(tmp_path), conn, ms(9, 6, 41), audio=False)
    d2 = M.ensure(str(tmp_path), conn, ms(9, 7, 5), audio=False)
    assert d1 and d2["asof"] == d1["asof"]                                           # the same report all morning


def test_morning_push_at_seven_to_the_owner_and_confirmed_accounts_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CNQ_OWNER_EMAIL", "owner@x.io")
    web = tmp_path / "web"
    web.mkdir()
    api = str(tmp_path / "api.sqlite")
    st = Store(api)
    for uid, email in (("own", "owner@x.io"), ("vip", "v@x.io"), ("vipoff", "o@x.io"), ("free", "f@x.io")):
        st.upsert_user(uid, email, None)
        st.add_push(uid, "tok-" + uid + "x" * 20, "ios")
    st.set_prefs("vip", {"brief": True}); st.set_prefs("free", {"brief": True, "trades": True})
    st._q("UPDATE users SET tier='vip' WHERE uid IN ('vip', 'vipoff')")
    sent = []

    class Fcm:
        def send(self, tok, title, body, data=None):
            sent.append((tok, title, body, data))
            return "ok"
    conn = sqlite3.connect(str(tmp_path / "agents.sqlite"))
    world(web, ms(9, 6, 50))
    app_push.run(conn, api, str(web), ms(9, 6, 50), Fcm())                 # report written, nothing sent before 07:00
    assert sent == [] and json.load(open(web / "morning.json"))["day"] == "2026-11-09"
    r = app_push.run(conn, api, str(web), ms(9, 7, 0), Fcm())              # quiet hours: the morning report is the exception
    assert r["quiet"] and r["morning"] == 2
    to = {t[0][4:7]: t for t in sent}
    assert set(to) == {"own", "vip"}                                        # not «free» (no real account), not «vipoff» (switches off)
    assert to["own"][2] != to["vip"][2] and ("твоём" in to["own"][2] or "Твой" in to["own"][2] or "позиций" in to["own"][2])
    assert to["vip"][3]["open"] == "morning" and to["vip"][3]["day"] == "2026-11-09"
    app_push.run(conn, api, str(web), ms(9, 7, 10), Fcm())
    app_push.run(conn, api, str(web), ms(9, 8, 0), Fcm())
    assert len(sent) == 2                                                   # once a day
