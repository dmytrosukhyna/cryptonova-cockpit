"""The owner's admin console API (/v2/admin/*, cnq/api/console.py): token, numbers, paging, CRM, segment push, leads,
connectors (presence only), CORS. Pytest-free (tests/run.py)."""
import contextlib
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cnq.api import console as con  # noqa: E402
from cnq.api.auth import FirebaseVerifier  # noqa: E402
from cnq.api.server import Api, make_handler  # noqa: E402
from cnq.api.store import Store  # noqa: E402

TOKEN = "t" * 40
DAY = 86_400_000


@contextlib.contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FakeFcm:
    def __init__(self, ready=True, gone=()):
        self.ready, self.gone, self.sent = ready, set(gone), []

    def send(self, token, title, body, data=None):
        self.sent.append((token, title, body, data))
        return "gone" if token in self.gone else "ok"


def make(tmp_path, fcm=None, leads_db=None):
    web = tmp_path / "web"
    web.mkdir(exist_ok=True)
    (web / "app_home.json").write_text(json.dumps({"asof": int(time.time() * 1000), "total": {"usd": 1}}))
    (web / "academy.json").write_text(json.dumps({"levels": [{"lessons": [{"id": f"l{i}"} for i in range(10)]},
                                                             {"lessons": [{"id": f"m{i}"} for i in range(5)]}]}))
    (web / "app_config.json").write_text(json.dumps({"exchange_name": "BingX", "exchange_ref": None}))
    store = Store(str(tmp_path / "api.sqlite"))
    console = con.Console(store, str(web), leads_db=leads_db or str(tmp_path / "agents.sqlite"), fcm=fcm or FakeFcm(),
                          background=False)
    return Api(store, FirebaseVerifier("crypto-nova", keys={}), str(web), console=console), store


def call(api, method, path, body=None, tok=TOKEN, ip="1.2.3.4"):
    h = {"x-real-ip": ip}
    if tok:
        h["authorization"] = f"Bearer {tok}"
    return api.handle(method, path, h, json.dumps(body).encode() if body is not None else b"")


def seed(store):
    now = int(time.time() * 1000)
    rows = [  # uid, email, name, created, seen, tier, bingx_uid, lang
        ("u1", "anna@example.com", "Анна", now - 1 * DAY, now - 1000, "free", None, "ru"),
        ("u2", "bob@example.com", "Bob", now - 2 * DAY, now - 3 * DAY, "vip", "111", "en"),
        ("u3", "kate@example.com", "Катя", now - 40 * DAY, now - 20 * DAY, "free", "222", "uk"),
        ("u4", "dim@example.com", "Дима", now - 3 * DAY, now - 2 * DAY, "free", None, "ru"),
    ]
    for uid, email, name, created, seen, tier, bx, lang in rows:
        store._q("INSERT INTO users(uid, email, name, created, seen, tier, bingx_uid, lang) VALUES(?,?,?,?,?,?,?,?)",
                 (uid, email, name, created, seen, tier, bx, lang))
    store.add_push("u1", "tok-u1-" + "x" * 20, "ios")
    store.add_push("u2", "tok-u2-" + "x" * 20, "android")
    store.add_push("u2", "tok-u2b-" + "x" * 20, "ios")
    store.add_push("u4", "tok-u4-" + "x" * 20, "android")
    store.set_prefs("u1", {"news": True})                                 # u1 and u2 switched «Важное на рынке» on
    store.set_prefs("u2", {"news": True})
    store.set_prefs("u4", {"news": False})                                # u4 switched the news pushes off
    today = time.strftime("%Y-%m-%d", time.gmtime())
    store._q("INSERT INTO usage VALUES('u1', ?, 3)", (today,))
    store._q("INSERT INTO usage VALUES('u2', ?, 2)", (today,))
    store._q("INSERT INTO usage VALUES('u2', '2020-01-01', 7)")
    store.set_progress("u1", {"lessons": {"l1": {"done": True}, "l2": {"done": True}, "l3": {"done": False}}, "xp": 40})
    store.set_progress("u2", {"lessons": {f"l{i}": {"done": True} for i in range(6)}, "xp": 90})
    store.set_piggy("u1", {"amount": 20, "freq": "week", "goal": "kids", "on": True})
    store._q("INSERT INTO vip_checks VALUES('u2','111',?,1,?)", (now - DAY, json.dumps({"reason": "ok"})))
    store._q("INSERT INTO vip_checks VALUES('u3','222',?,0,?)", (now - 30 * DAY, json.dumps({"reason": "not_invited"})))


def test_admin_closed_without_token(tmp_path):
    api, _ = make(tmp_path)
    with env(CNQ_ADMIN_TOKEN=None):
        assert call(api, "GET", "/v2/admin/overview")[0] == 403
        assert call(api, "POST", "/v2/admin/crm", {"uid": "u1", "note": "x", "confirm": True})[0] == 403
    with env(CNQ_ADMIN_TOKEN="short"):                       # a short token counts as no token
        assert call(api, "GET", "/v2/admin/ping", tok="short")[0] == 403


def test_admin_token_wrong_and_throttled(tmp_path):
    api, _ = make(tmp_path)
    with env(CNQ_ADMIN_TOKEN=TOKEN):
        code, doc = call(api, "GET", "/v2/admin/ping", tok="w" * 40)
        assert code == 401 and TOKEN not in json.dumps(doc)
        assert call(api, "GET", "/v2/admin/ping", tok=None)[0] == 401
        assert call(api, "GET", "/v2/admin/ping")[0] == 200
        for _ in range(25):
            call(api, "GET", "/v2/admin/ping", tok="w" * 40, ip="9.9.9.9")
        assert call(api, "GET", "/v2/admin/ping", ip="9.9.9.9")[0] == 429      # that address is locked for a while
        assert call(api, "GET", "/v2/admin/ping", ip="1.2.3.4")[0] == 200      # the owner's own address still works
        # the admin token is not a Firebase login, and a Firebase-style token is not the admin token
        # (the Firebase check needs PyJWT; the light test gate in tests.yml does not install it)
        try:
            import jwt  # noqa: F401
        except ImportError:
            return
        assert call(api, "POST", "/v2/me", {}, tok=TOKEN)[0] == 401


def test_admin_overview_numbers(tmp_path):
    api, store = make(tmp_path)
    seed(store)
    with env(CNQ_ADMIN_TOKEN=TOKEN):
        code, d = call(api, "GET", "/v2/admin/overview")
    assert code == 200
    u = d["users"]
    assert u["total"] == 4 and u["new_30d"] == 3 and len(u["new_per_day"]) == 30 and sum(u["new_per_day"]) == 3
    assert u["active_1d"] == 1 and u["active_7d"] == 3 and u["active_30d"] == 4
    assert u["by_lang"] == {"ru": 2, "en": 1, "uk": 1}
    assert d["push"] == {"users": 3, "devices": 4, "by_platform": {"ios": 2, "android": 2}}
    assert d["nova"]["total_30d"] == 5 and d["nova"]["users_30d"] == 2 and d["nova"]["per_day"][-1] == 5
    assert d["lessons"]["total"] == 15 and d["lessons"]["started"] == 2
    assert {x["label"]: x["n"] for x in d["lessons"]["dist"]} == {"0": 2, "1–4": 1, "5–9": 1, "10–19": 0, "20–49": 0, "50+": 0}
    assert d["piggy"]["on"] == 1 and d["piggy"]["by_goal"] == {"kids": 1}
    assert d["bingx"] == {"submitted": 2, "verified": 1}
    assert [s["n"] for s in d["funnel"]] == [None, 4, 2, 1]               # installs: only what the owner enters
    r = d["referrals"]
    assert r["checks"] == 2 and r["confirmed"] == 1 and r["reasons"] == {"ok": 1, "not_invited": 1}
    with env(CNQ_ADMIN_TOKEN=TOKEN):
        assert call(api, "POST", "/v2/admin/plan", {"data": {"installs_ios": "120", "installs_android": 80}})[0] == 400
        assert call(api, "POST", "/v2/admin/plan", {"data": {"installs_ios": "120", "installs_android": 80}, "confirm": True})[0] == 200
        assert call(api, "GET", "/v2/admin/overview")[1]["funnel"][0]["n"] == 200
        assert call(api, "GET", "/v2/admin/plan")[1]["plan"]["installs_android"] == 80


def test_admin_users_paging_search_segments(tmp_path):
    api, store = make(tmp_path)
    seed(store)
    with env(CNQ_ADMIN_TOKEN=TOKEN):
        code, d = call(api, "GET", "/v2/admin/users?size=2&page=1")
        assert code == 200 and d["total"] == 4 and d["pages"] == 2 and [r["uid"] for r in d["rows"]] == ["u1", "u2"]
        assert [r["uid"] for r in call(api, "GET", "/v2/admin/users?size=2&page=2")[1]["rows"]] == ["u4", "u3"]
        r1 = call(api, "GET", "/v2/admin/users?q=ANNA")[1]["rows"]
        assert len(r1) == 1 and r1[0]["lessons"] == 2 and r1[0]["nova_30d"] == 3 and r1[0]["piggy"]["amount"] == 20 \
            and r1[0]["push"] == 1 and r1[0]["bingx"] == "none"
        assert call(api, "GET", "/v2/admin/users?q=222")[1]["rows"][0]["uid"] == "u3"
        assert [r["uid"] for r in call(api, "GET", "/v2/admin/users?seg=verified")[1]["rows"]] == ["u2"]
        assert [r["uid"] for r in call(api, "GET", "/v2/admin/users?seg=uid_pending")[1]["rows"]] == ["u3"]
        assert {r["uid"] for r in call(api, "GET", "/v2/admin/users?seg=lang:ru")[1]["rows"]} == {"u1", "u4"}
        assert [r["uid"] for r in call(api, "GET", "/v2/admin/users?seg=inactive7")[1]["rows"]] == ["u3"]
        assert call(api, "GET", "/v2/admin/users?sort=nova")[1]["rows"][0]["uid"] == "u2"
        assert call(api, "GET", "/v2/admin/users?size=9999")[1]["size"] == 100
        card = call(api, "GET", "/v2/admin/user/u2")[1]
        assert card["bingx"] == "verified" and card["nova"]["total"] == 9 and len(card["devices"]) == 2
        assert "token" not in json.dumps(card["devices"]) and "tok-u2" not in json.dumps(card)
        assert call(api, "GET", "/v2/admin/user/nobody")[0] == 404


def test_admin_crm_notes_and_tags(tmp_path):
    api, store = make(tmp_path)
    seed(store)
    with env(CNQ_ADMIN_TOKEN=TOKEN):
        assert call(api, "POST", "/v2/admin/crm", {"uid": "u1", "note": "позвонить"})[0] == 400       # no confirm
        assert call(api, "POST", "/v2/admin/crm", {"uid": "zz", "note": "x", "confirm": True})[0] == 404
        code, d = call(api, "POST", "/v2/admin/crm", {"uid": "u1", "note": "  позвонить в пятницу ", "tags": ["VIP", "VIP", "горячий"],
                                                      "confirm": True})
        assert code == 200 and d["crm"]["tags"] == ["VIP", "горячий"] and d["crm"]["notes"][0]["note"] == "позвонить в пятницу"
        time.sleep(0.002)
        call(api, "POST", "/v2/admin/crm", {"uid": "u1", "note": "договорились", "confirm": True})   # a note keeps the tags
        card = call(api, "GET", "/v2/admin/user/u1")[1]
        assert card["crm"]["tags"] == ["VIP", "горячий"] and [n["note"] for n in card["crm"]["notes"]] == ["договорились",
                                                                                                          "позвонить в пятницу"]
        assert [r["uid"] for r in call(api, "GET", "/v2/admin/users?seg=tag:VIP")[1]["rows"]] == ["u1"]
        assert call(api, "POST", "/v2/admin/crm", {"uid": "u1", "tags": "VIP", "confirm": True})[0] == 400
        assert call(api, "POST", "/v2/admin/crm", {"uid": "u1", "confirm": True})[0] == 400
    store.delete_user("u1")                                     # account deletion takes the CRM rows too
    assert store._q("SELECT COUNT(*) FROM crm WHERE uid='u1'")[0][0] == 0


def test_admin_push_dry_run_then_send(tmp_path):
    fcm = FakeFcm(gone={"tok-u2b-" + "x" * 20})
    api, store = make(tmp_path, fcm=fcm)
    seed(store)
    old_quiet = con.quiet_now
    con.quiet_now = lambda *a: False
    try:
        with env(CNQ_ADMIN_TOKEN=TOKEN):
            msg = {"segment": "all", "title": "Новый урок", "body": "В академии новый урок о рисках."}
            code, d = call(api, "POST", "/v2/admin/push", {**msg, "dry_run": True})
            assert code == 200 and d["dry_run"] and d["people"] == 2 and d["devices"] == 3 and d["news_off"] == 1
            assert fcm.sent == []                                                   # a dry run sends nothing
            assert call(api, "POST", "/v2/admin/push", msg)[0] == 400                # neither dry run nor confirm
            assert call(api, "POST", "/v2/admin/push", {**msg, "segment": "lang:xx", "dry_run": True})[0] == 400
            assert call(api, "POST", "/v2/admin/push", {**msg, "title": "x" * 61, "dry_run": True})[0] == 400
            assert call(api, "POST", "/v2/admin/push", {**msg, "segment": "verified", "dry_run": True})[1]["devices"] == 2
            code, d = call(api, "POST", "/v2/admin/push", {**msg, "confirm": True})
            assert code == 200 and d["started"] and len(fcm.sent) == 3 and d["push"]["results"] == {"ok": 2, "gone": 1}
            assert store._q("SELECT COUNT(*) FROM push WHERE token LIKE 'tok-u2b-%'")[0][0] == 0   # a removed app's token goes
            assert call(api, "POST", "/v2/admin/push", {**msg, "confirm": True})[1]["error"] == "duplicate"
            st = call(api, "GET", "/v2/admin/push")[1]
            assert st["history"][0]["title"] == "Новый урок" and st["history"][0]["results"] == {"ok": 2, "gone": 1}
            con.quiet_now = lambda *a: True
            assert call(api, "POST", "/v2/admin/push", {**msg, "title": "Другое", "confirm": True})[1]["error"] == "quiet_hours"
            con.quiet_now = lambda *a: False
            api.console._fcm = FakeFcm(ready=False)
            assert call(api, "POST", "/v2/admin/push", {**msg, "title": "Третье", "confirm": True})[0] == 503
    finally:
        con.quiet_now = old_quiet


def test_admin_leads(tmp_path):
    db = tmp_path / "agents.sqlite"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE bot_chats(chat_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, first_seen INTEGER, "
              "last_seen INTEGER, n_msgs INTEGER)")
    now = int(time.time() * 1000)
    c.executemany("INSERT INTO bot_chats VALUES(?,?,?,?,?,?)", [(1, "ivan", "Иван", now - DAY, now - 1000, 4),
                                                                (2, None, "Ольга", now - 50 * DAY, now - 40 * DAY, 1)])
    c.commit()
    c.close()
    api, _ = make(tmp_path, leads_db=str(db))
    with env(CNQ_ADMIN_TOKEN=TOKEN):
        d = call(api, "GET", "/v2/admin/leads")[1]
        assert d["available"] and d["total"] == 2 and d["active_7d"] == 1 and d["messages"] == 5 and sum(d["new_per_day"]) == 1
        assert [r["chat_id"] for r in d["rows"]] == [1, 2]
        assert call(api, "GET", "/v2/admin/leads?q=@IVAN")[1]["found"] == 1
    other = tmp_path / "other"
    other.mkdir()
    api2, _ = make(other, leads_db=str(tmp_path / "none.sqlite"))
    with env(CNQ_ADMIN_TOKEN=TOKEN):
        assert call(api2, "GET", "/v2/admin/leads")[1]["available"] is False


def test_admin_connectors_presence_only(tmp_path):
    api, _ = make(tmp_path)
    secrets = {"CNQ_BINGX_AGENT_KEY": "bxkey-SECRET-1", "CNQ_BINGX_AGENT_SECRET": "bxsec-SECRET-2", "CNQ_TG_TOKEN": "tg-SECRET-3",
               "CNQ_NOVA_LLM_KEY": "nova-SECRET-4"}
    with env(CNQ_ADMIN_TOKEN=TOKEN, CNQ_TG_CHAT=None, CNQ_SIGNALS_CHAT=None, **secrets):
        code, d = call(api, "GET", "/v2/admin/connectors")
    raw = json.dumps(d)
    assert code == 200 and not any(v in raw for v in secrets.values()) and TOKEN not in raw
    items = {i["id"]: i for i in d["items"]}
    assert items["bingx_partner"]["ok"] and items["telegram"]["ok"] and items["nova"]["detail"]["own_key"]
    assert items["telegram"]["detail"]["news_channel"] is False
    assert items["exchange_ref"]["ok"] is False and items["meta"]["detail"]["app_id"]
    with env(CNQ_ADMIN_TOKEN=TOKEN, CNQ_BINGX_AGENT_KEY=None, CNQ_BINGX_AGENT_SECRET=None):
        assert {i["id"]: i for i in call(api, "GET", "/v2/admin/connectors")[1]["items"]}["bingx_partner"]["ok"] is False


def test_admin_cors_over_http(tmp_path):
    from http.server import ThreadingHTTPServer
    api, store = make(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with env(CNQ_ADMIN_TOKEN=TOKEN, CNQ_ADMIN_ORIGIN=None):
            req = urllib.request.Request(base + "/v2/admin/overview", method="OPTIONS",
                                         headers={"Origin": "https://cryptonova.vip", "Access-Control-Request-Method": "GET"})
            with urllib.request.urlopen(req, timeout=5) as r:
                assert r.status == 204 and r.headers["Access-Control-Allow-Origin"] == "https://cryptonova.vip"
                assert "Authorization" in r.headers["Access-Control-Allow-Headers"]
            req = urllib.request.Request(base + "/v2/admin/ping", headers={"Authorization": f"Bearer {TOKEN}"})
            with urllib.request.urlopen(req, timeout=5) as r:
                assert r.status == 200 and r.headers["Access-Control-Allow-Origin"] == "https://cryptonova.vip"
            try:
                urllib.request.urlopen(urllib.request.Request(base + "/v2/admin/ping"), timeout=5)
                assert False, "no token must fail"
            except urllib.error.HTTPError as e:
                assert e.code == 401 and e.headers["Access-Control-Allow-Origin"] == "https://cryptonova.vip"
            with urllib.request.urlopen(base + "/v2/home", timeout=5) as r:            # the app's routes get no CORS header
                assert r.headers.get("Access-Control-Allow-Origin") is None
            # 28.10 (build 46): the panel's chat body may be up to 30 KB (it was 16 KB, with the portfolio-file sentence on a 413)
            body = json.dumps({"system": "s", "user": "Вопрос: " + "я" * 9000}, ensure_ascii=False).encode()   # ≈ 18 KB
            req = urllib.request.Request(base + "/v2/admin/nova", data=body, method="POST",
                                         headers={"Authorization": "Bearer wrong", "Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as e:
                assert e.code == 401                                                 # read and checked, not refused by size
            req = urllib.request.Request(base + "/v2/admin/nova", data=b"x" * (31 * 1024), method="POST",
                                         headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=5)
                assert False, "over 30 KB must be refused"
            except urllib.error.HTTPError as e:
                d = json.loads(e.read().decode())
                assert e.code == 413 and d == {"error": "too_large", "limit_kb": 30} and e.headers["Access-Control-Allow-Origin"]
            req = urllib.request.Request(base + "/v2/me", data=b"x" * (17 * 1024), method="POST")
            try:
                urllib.request.urlopen(req, timeout=5)
                assert False
            except urllib.error.HTTPError as e:
                assert e.code == 413 and "message" not in json.loads(e.read().decode())   # no «Файл больше 10 МБ» outside the file route
    finally:
        srv.shutdown()
