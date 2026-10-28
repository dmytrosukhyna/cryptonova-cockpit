import json
import os
import threading
import time
import urllib.request

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from cnq.api.auth import FirebaseVerifier
from cnq.api.server import Api, make_handler
from cnq.api.store import Store

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(uid="u1", project="crypto-nova", exp=3600):
    now = int(time.time())
    return jwt.encode({"sub": uid, "aud": project, "iss": f"https://securetoken.google.com/{project}", "iat": now,
                       "exp": now + exp, "email": f"{uid}@x.io"}, KEY, algorithm="RS256", headers={"kid": "k1"})


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def complete(self, system, user, temperature=0.2):
        self.calls += 1
        assert "ДАННЫЕ CRYPTO NOVA" in user and "не обещай" in system.lower().replace("никогда не обещай", "не обещай")
        return "Ответ простыми словами."


def make(tmp_path, fetch=None):
    web = tmp_path / "web"
    web.mkdir()
    (web / "app_home.json").write_text(json.dumps({"asof": int(time.time() * 1000), "total": {"usd": 1}, "futures": {}, "spot": {}}))
    llm = FakeLLM()
    api = Api(Store(str(tmp_path / "api.sqlite")), FirebaseVerifier("crypto-nova", keys={"k1": KEY.public_key()}), str(web), llm, fetch)
    return api, llm


def call(api, method, path, body=None, tok=None):
    h = {"authorization": f"Bearer {tok}"} if tok else {}
    return api.handle(method, path, h, json.dumps(body).encode() if body is not None else b"")


def test_public_and_auth(tmp_path):
    api, _ = make(tmp_path)
    c, d = call(api, "GET", "/v2/health")
    assert c == 200 and d["ok"] and d["home_age_sec"] is not None
    assert call(api, "GET", "/v2/home")[0] == 200
    assert call(api, "GET", "/v2/feed/secrets")[0] == 404
    assert call(api, "POST", "/v2/me", {})[0] == 401
    assert call(api, "POST", "/v2/me", {}, token(project="other"))[0] == 401
    assert call(api, "POST", "/v2/me", {}, token(exp=-120))[0] == 401
    c, d = call(api, "POST", "/v2/me", {"lang": "ru"}, token())
    assert c == 200 and d["user"]["uid"] == "u1" and d["user"]["tier"] == "free" and d["agent"] == {"limit": 5, "left": 5}
    assert d["user"]["is_owner"] is False                       # CNQ_OWNER_EMAIL unset → nobody is the owner


def test_me_is_owner(tmp_path, monkeypatch):
    # build 45: owner mode — only the signed-in email equal to CNQ_OWNER_EMAIL (case and spaces ignored)
    api, _ = make(tmp_path)
    monkeypatch.setenv("CNQ_OWNER_EMAIL", " Owner@X.io ")
    assert call(api, "POST", "/v2/me", {}, token(uid="owner"))[1]["user"]["is_owner"] is True
    assert call(api, "POST", "/v2/me", {}, token(uid="u2"))[1]["user"]["is_owner"] is False
    monkeypatch.setenv("CNQ_OWNER_EMAIL", "")
    assert call(api, "POST", "/v2/me", {}, token(uid="owner"))[1]["user"]["is_owner"] is False


def test_home_geo_country(tmp_path):
    api, _ = make(tmp_path)
    c, d = api.handle("GET", "/v2/home", {"cf-ipcountry": "de"}, b"")
    assert c == 200 and d["geo"] == {"country": "DE"}
    c, d = api.handle("GET", "/v2/home", {"x-country": "XX"}, b"")
    assert c == 200 and "geo" not in d
    assert "geo" not in call(api, "GET", "/v2/home")[1]


def test_piggy_prefs_progress(tmp_path):
    api, _ = make(tmp_path)
    t = token()
    assert call(api, "PUT", "/v2/piggy", {"amount": 0, "freq": "week"}, t)[0] == 400
    c, d = call(api, "PUT", "/v2/piggy", {"amount": 50, "freq": "week", "goal": "kids", "start_day": 20745}, t)
    assert c == 200 and d["piggy"]["mode"] == "save" and d["piggy"]["goal"] == "kids"
    c, d = call(api, "PUT", "/v2/prefs", {"piggy": True, "hack": 1}, t)
    assert d["prefs"]["piggy"] is True and "hack" not in d["prefs"]
    call(api, "PUT", "/v2/progress", {"lessons": {"l1-bitcoin": {"done": True, "score": 3}}, "xp": 20}, t)
    d = call(api, "POST", "/v2/me", {}, t)[1]
    assert d["piggy"]["amount"] == 50 and d["progress"]["xp"] == 20


def test_agent_limit(tmp_path):
    api, llm = make(tmp_path)
    t = token("u2")
    for i in range(5):
        c, d = call(api, "POST", "/v2/agent", {"q": "Что такое плечо?"}, t)
        assert c == 200 and d["agent"]["left"] == 4 - i
    c, d = call(api, "POST", "/v2/agent", {"q": "Ещё вопрос"}, t)
    assert c == 200 and d["error"] == "limit" and d["answer"] and d["agent"]["left"] == 0 and llm.calls == 5
    # 28.10 (build 46 §11.4): a ready-made question still gets its FAQ answer after the limit — not counted, no model call
    c, d = call(api, "POST", "/v2/agent", {"q": "Что такое стоп?"}, t)
    assert c == 200 and d.get("error") is None and d["source"] == "faq" and d["answer"] and d["agent"]["left"] == 0 and llm.calls == 5
    c, d = call(api, "POST", "/v2/agent", {"q": "What is a stop?", "lang": "en"}, t)
    assert d["source"] == "faq" and d["answer"].startswith("A stop")
    assert "готового списка" in call(api, "POST", "/v2/agent", {"q": "Расскажи анекдот"}, t)[1]["answer"]


def test_vip_check(tmp_path):
    answers = {"1001": {"code": 0, "data": {"inviteResult": True, "deposit": True}},
               "1002": {"code": 0, "data": {"inviteResult": True, "deposit": False}}}
    fetch = lambda url: answers[url.split("uid=")[1].split("&")[0]]
    api, _ = make(tmp_path, fetch)
    c, d = call(api, "POST", "/v2/vip/check", {"bingx_uid": "1002"}, token("a"))
    assert c == 200 and not d["vip"] and d["reason"] == "no_deposit"
    c, d = call(api, "POST", "/v2/vip/check", {"bingx_uid": "1001"}, token("a"))
    assert d["vip"] and d["user"]["tier"] == "vip"
    assert call(api, "POST", "/v2/vip/check", {"bingx_uid": "1001"}, token("b"))[0] == 409
    assert call(api, "POST", "/v2/me", {}, token("a"))[1]["agent"]["limit"] == 100


def test_http_roundtrip(tmp_path):
    from http.server import ThreadingHTTPServer
    api, _ = make(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v2/me", data=b"{}", method="POST",
                                     headers={"Authorization": f"Bearer {token('h')}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read())["user"]["uid"] == "h"
    finally:
        srv.shutdown()


class FakeAdmin:
    def __init__(self):
        self.deleted = []

    def delete_user(self, uid):
        self.deleted.append(uid)
        return "ok"


def test_delete_account(tmp_path):
    api, _ = make(tmp_path)
    api.admin = FakeAdmin()
    t = token("del1")
    assert call(api, "DELETE", "/v2/me", None)[0] == 401
    call(api, "POST", "/v2/me", {}, t)
    call(api, "PUT", "/v2/piggy", {"amount": 50, "freq": "week"}, t)
    call(api, "PUT", "/v2/progress", {"lessons": {"g1": {"done": True, "score": 3}}, "xp": 10}, t)
    call(api, "POST", "/v2/push", {"token": "x" * 40, "platform": "ios"}, t)
    call(api, "POST", "/v2/agent", {"q": "что такое биткоин?"}, t)
    keep = token("other")
    call(api, "POST", "/v2/me", {}, keep)
    c, d = call(api, "DELETE", "/v2/me", None, t)
    assert c == 200 and d == {"deleted": True, "firebase": "ok"} and api.admin.deleted == ["del1"]
    s = api.store
    for table in ("users", "prefs", "piggy", "push", "usage", "vip_checks", "progress"):
        assert s._q(f"SELECT COUNT(*) FROM {table} WHERE uid='del1'")[0][0] == 0, table
    assert s.user("other")["uid"] == "other"
    # signing in again later starts from zero
    c, d = call(api, "POST", "/v2/me", {}, t)
    assert c == 200 and d["piggy"] is None and d["progress"] == {} and d["agent"]["left"] == 5


def test_firebase_admin_delete():
    import io
    import urllib.error
    from cnq.api.firebase_admin import FirebaseAdmin
    a = FirebaseAdmin(sa_path="/nonexistent")
    assert not a.ready and a.delete_user("x") == "dry"
    sent = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=15):
        sent.append((req.full_url, req.data))
        if "oauth2" in req.full_url:
            return Resp(json.dumps({"access_token": "AT", "expires_in": 3600}).encode())
        if b"gone" in req.data:
            raise urllib.error.HTTPError(req.full_url, 400, "USER_NOT_FOUND", {}, None)
        assert req.headers["Authorization"] == "Bearer AT" and req.get_method() == "POST"
        return Resp(b"{}")

    from cryptography.hazmat.primitives import serialization
    pem = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    a = FirebaseAdmin(sa_path="/nonexistent", project="crypto-nova", opener=opener)
    a.sa = {"client_email": "x@crypto-nova.iam.gserviceaccount.com", "private_key": pem}
    assert a.ready and a.delete_user("u1") == "ok"
    assert sent[-1][0].endswith("/projects/crypto-nova/accounts:delete") and json.loads(sent[-1][1]) == {"localId": "u1"}
    assert a.delete_user("gone") == "gone"
    assert sum(1 for u, _ in sent if "oauth2" in u) == 1


def test_real_llm_property_and_health(tmp_path):
    """the production LLM exposes `available` as a property — health and the agent must not crash on it"""
    from cnq.agents.llm import LLM
    api, _ = make(tmp_path)
    api.llm = LLM(api_key="")
    c, d = call(api, "GET", "/v2/health")
    assert c == 200 and {k: v for k, v in d["ready"].items() if k != "tts"} == {"llm": False, "firebase_admin": False, "bingx_partner": False} and "tts" in d["ready"]
    c, d = call(api, "POST", "/v2/agent", {"q": "Как прошёл ваш день, NOVA?"}, token("p1"))
    assert c == 200 and d["error"] == "busy" and d["answer"] and d["agent"]["left"] == 5
    c, d = call(api, "POST", "/v2/agent", {"q": "что такое биткоин?"}, token("p1"))     # a FAQ question: answered from the FAQ, not counted
    assert c == 200 and "error" not in d and d["source"] == "faq" and "21 млн" in d["answer"] and d["agent"]["left"] == 5

    class Ok(LLM):
        def complete(self, system, user, temperature=0.2):
            return "Ответ."
    api.llm = Ok(api_key="k")
    assert call(api, "GET", "/v2/health")[1]["ready"]["llm"] is True
    c, d = call(api, "POST", "/v2/agent", {"q": "что такое биткоин?"}, token("p1"))
    assert c == 200 and d["answer"] == "Ответ." and d["agent"]["left"] == 4


def test_diag_shows_the_engine_pulse_without_auth(tmp_path):
    api, _ = make(tmp_path)
    eng = {"asof": int(time.time() * 1000) - 90_000, "cycle": 7, "cycle_sec": 41.0, "errors": ["candles"],
           "steps": {"candles": {"ok": False, "sec": 3, "err": "network failure"}, "paper": {"ok": True, "sec": 2}},
           "last_sync": {"t": int(time.time() * 1000) - 30_000, "pulled": True, "pushed": False, "committed": True}}
    (tmp_path / "web" / "engine.json").write_text(json.dumps(eng))
    c, d = call(api, "GET", "/v2/diag")
    assert c == 200 and 80 <= d["engine_age_sec"] <= 100 and d["engine_errors"] == ["candles"]
    assert d["engine_err_tail"] == {"candles": "network failure"} and d["last_sync"]["pushed"] is False
    assert d["disk_free_gb"] >= 0 and "app_home.json_age_sec" in d
