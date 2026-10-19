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
        assert "КОНТЕКСТ" in user and "не обещай" in system.lower().replace("никогда не обещай", "не обещай")
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
    assert c == 429 and llm.calls == 5


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
