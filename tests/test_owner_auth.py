"""The owner's one sign-in for cryptonova.vip (cnq/api/owner_auth.py, cnq/api/owner_data.py, the /v2/auth, /v2/owner and /v2/admin
routes in cnq/api/server.py): Firebase token checks (mocked keys), session issue / refresh rotation / expiry / revoke, the rate
limits, owner-only endpoints refusing anonymous calls, the device-bound refresh, the decrypted private sections and the full
passkey ceremony with a software authenticator."""
import hashlib
import json
import struct
import time

import jwt
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa, utils

from cnq.api import owner_auth as oa
from cnq.api import passkey as pk
from cnq.api.auth import FirebaseVerifier
from cnq.api.server import Api
from cnq.api.store import Store
from cnq.execution import ivan_watch
from test_passkey import _cbor

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OWNER = "owner@cryptonova.test"
ORIGIN = "https://cryptonova.vip"


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


def fb_token(email=OWNER, uid="owner-uid", verified=True, auth_age=5, provider="password", project="crypto-nova", clock=None):
    now = int(time.time())
    base = int(clock.t) if clock else now
    return jwt.encode({"sub": uid, "aud": project, "iss": f"https://securetoken.google.com/{project}", "iat": now, "exp": now + 3600,
                       "auth_time": base - auth_age, "email": email, "email_verified": verified,
                       "firebase": {"sign_in_provider": provider}}, KEY, algorithm="RS256", headers={"kid": "k1"})


def make(tmp_path, monkeypatch, owner=OWNER):
    monkeypatch.setenv("CNQ_OWNER_EMAIL", owner)
    monkeypatch.setenv("CNQ_ADMIN_TOKEN", "a" * 40)
    monkeypatch.delenv("CNQ_ADMIN_ORIGIN", raising=False)
    monkeypatch.delenv("CNQ_PASSKEY_ORIGINS", raising=False)
    monkeypatch.delenv("CNQ_PASSKEY_RP_ID", raising=False)
    web = tmp_path / "web"
    web.mkdir()
    (web / "app_home.json").write_text(json.dumps({"asof": int(time.time() * 1000)}))
    store = Store(str(tmp_path / "api.sqlite"))
    ver = FirebaseVerifier("crypto-nova", keys={"k1": KEY.public_key()})
    api = Api(store, ver, str(web), None)
    clock = Clock()
    api.owner_auth = oa.OwnerAuth(store, ver, now=clock)      # the same tables, a clock the test moves
    api.console.owner_auth = api.owner_auth
    return api, clock, web


def call(api, method, path, body=None, tok=None, ip="1.2.3.4", ua="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Safari/604.1"):
    h = {"x-real-ip": ip, "user-agent": ua}
    if tok:
        h["authorization"] = f"Bearer {tok}"
    return api.handle(method, path, h, json.dumps(body).encode() if body is not None else b"")


def signin(api, clock, **kw):
    code, d = call(api, "POST", "/v2/auth/session", {"id_token": fb_token(clock=clock, **kw), "remember": True})
    assert code == 200, d
    return d


# ------------------------------------------------------------------------------------------------ Firebase token → session
def test_session_from_firebase_token_only_for_the_verified_owner(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    d = signin(api, clock)
    assert d["access_token"].startswith("cno_") and d["refresh_token"].startswith("cnr_") and d["expires_in"] == 15 * 60
    assert d["session"]["email"] == OWNER and d["session"]["method"] == "email" and d["session"]["remember"] is True
    assert d["session"]["device"] == "iPhone · Safari"
    assert signin(api, clock, provider="apple.com")["session"]["method"] == "apple"
    assert signin(api, clock, provider="google.com", email="OWNER@cryptonova.test")["session"]["method"] == "google"
    cases = [({"email": "someone@x.io"}, 403, "not_owner"),
             ({"verified": False}, 403, "email_not_verified"),
             ({"auth_age": 3600}, 401, "stale_sign_in"),             # a token refreshed from an old login, not a fresh sign-in
             ({"project": "other-project"}, 401, "bad_token")]
    for i, (kw, code, err) in enumerate(cases):
        c, x = call(api, "POST", "/v2/auth/session", {"id_token": fb_token(clock=clock, **kw)}, ip=f"9.9.9.{i}")
        assert (c, x["error"]) == (code, err), kw
    assert call(api, "POST", "/v2/auth/session", {"id_token": "not.a.jwt"}, ip="9.9.8.1")[1]["error"] == "bad_token"
    assert call(api, "POST", "/v2/auth/session", {})[0] == 400
    monkeypatch.setenv("CNQ_OWNER_EMAIL", "")
    assert call(api, "POST", "/v2/auth/session", {"id_token": fb_token(clock=clock)}, ip="9.9.8.2")[1]["error"] == "owner_not_configured"


def test_several_owner_addresses(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch, owner=f"{OWNER}, relay123@privaterelay.appleid.com")
    assert signin(api, clock, email="relay123@privaterelay.appleid.com", provider="apple.com")["session"]["method"] == "apple"
    assert signin(api, clock)["session"]["email"] == OWNER


# ------------------------------------------------------------------------------------------------ issue / expiry / rotation / revoke
def test_access_refresh_rotation_expiry_and_logout(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    d = signin(api, clock)
    at, rt = d["access_token"], d["refresh_token"]
    c, me = call(api, "GET", "/v2/auth/me", tok=at)
    assert c == 200 and me == {**me, "signedIn": True, "email": OWNER, "isOwner": True, "method": "email"}
    clock.t += 15 * 60 + 1                                           # the access token lives 15 minutes
    assert call(api, "GET", "/v2/auth/me", tok=at) == (401, {"error": "token_expired"})
    c, r = call(api, "POST", "/v2/auth/refresh", {"refresh_token": rt})
    assert c == 200 and r["refresh_token"] != rt and r["access_token"] != at
    assert call(api, "GET", "/v2/owner/overview", tok=r["access_token"])[0] == 200
    # the old refresh token: a second tab right after the rotation → 409 (try again), nothing revoked
    assert call(api, "POST", "/v2/auth/refresh", {"refresh_token": rt}) == (409, {"error": "rotated"})
    assert call(api, "GET", "/v2/auth/me", tok=r["access_token"])[0] == 200
    # … but the same old token a minute later = it was copied → the whole session is revoked
    clock.t += 60
    assert call(api, "POST", "/v2/auth/refresh", {"refresh_token": rt}) == (401, {"error": "session_revoked"})
    assert call(api, "GET", "/v2/auth/me", tok=r["access_token"])[0] == 401
    assert call(api, "POST", "/v2/auth/refresh", {"refresh_token": r["refresh_token"]})[1]["error"] == "session_revoked"
    # logout: the access token and the refresh token both stop at once
    d = signin(api, clock)
    assert call(api, "POST", "/v2/auth/logout", {}, tok=d["access_token"]) == (200, {"ok": True, "revoked": 1})
    assert call(api, "GET", "/v2/owner/overview", tok=d["access_token"])[0] == 401
    assert call(api, "POST", "/v2/auth/refresh", {"refresh_token": d["refresh_token"]})[1]["error"] == "session_revoked"
    # logout with only the refresh token (the access token already expired)
    d = signin(api, clock)
    assert call(api, "POST", "/v2/auth/logout", {"refresh_token": d["refresh_token"]})[0] == 200
    assert call(api, "POST", "/v2/auth/logout", {}) == (401, {"error": "unauthorized"})


def test_remembered_session_lasts_30_idle_days_and_the_short_one_12_hours(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    long = signin(api, clock)
    short = call(api, "POST", "/v2/auth/session", {"id_token": fb_token(clock=clock), "remember": False}, ip="5.5.5.5")[1]
    assert short["session"]["remember"] is False
    clock.t += 13 * 3600
    assert call(api, "POST", "/v2/auth/refresh", {"refresh_token": short["refresh_token"]}) == (401, {"error": "expired"})
    rt = long["refresh_token"]
    for _ in range(3):                                                # used every 29 days → stays open …
        clock.t += 29 * 86400
        c, r = call(api, "POST", "/v2/auth/refresh", {"refresh_token": rt})
        assert c == 200, r
        rt = r["refresh_token"]
    clock.t += 29 * 86400                                             # … but never past 90 days from the sign-in
    assert call(api, "POST", "/v2/auth/refresh", {"refresh_token": rt})[1]["error"] == "expired"
    d = signin(api, clock)
    clock.t += 31 * 86400                                             # 31 idle days
    assert call(api, "POST", "/v2/auth/refresh", {"refresh_token": d["refresh_token"]})[1]["error"] == "expired"


def test_sessions_list_revoke_and_logout_everywhere(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    a, b = signin(api, clock), signin(api, clock)
    c, d = call(api, "GET", "/v2/auth/sessions", tok=a["access_token"])
    assert c == 200 and len(d["sessions"]) == 2 and sum(s["current"] for s in d["sessions"]) == 1
    assert call(api, "POST", "/v2/auth/sessions/revoke", {"id": b["session"]["id"]}, tok=a["access_token"])[1]["error"] == "confirm_required"
    assert call(api, "POST", "/v2/auth/sessions/revoke", {"id": b["session"]["id"], "confirm": True}, tok=a["access_token"])[0] == 200
    assert call(api, "GET", "/v2/auth/me", tok=b["access_token"])[0] == 401
    signin(api, clock)
    assert call(api, "POST", "/v2/auth/logout", {"all": True}, tok=a["access_token"])[1]["error"] == "confirm_required"
    assert call(api, "POST", "/v2/auth/logout", {"all": True, "confirm": True}, tok=a["access_token"]) == (200, {"ok": True, "revoked": 2})
    assert call(api, "GET", "/v2/auth/me", tok=a["access_token"])[0] == 401


def test_a_changed_owner_email_closes_old_sessions(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    d = signin(api, clock)
    monkeypatch.setenv("CNQ_OWNER_EMAIL", "new-owner@x.io")
    assert call(api, "GET", "/v2/owner/overview", tok=d["access_token"])[0] == 401
    assert call(api, "POST", "/v2/auth/refresh", {"refresh_token": d["refresh_token"]}) == (403, {"error": "not_owner"})


# ------------------------------------------------------------------------------------------------ device-bound refresh
def _device():
    k = ec.generate_private_key(ec.SECP256R1())
    n = k.public_key().public_numbers()
    return k, {"kty": "EC", "crv": "P-256", "x": pk.b64u(n.x.to_bytes(32, "big")), "y": pk.b64u(n.y.to_bytes(32, "big")), "ext": True}


def _proof(k, rt, t_ms):
    r, s = utils.decode_dss_signature(k.sign(oa.proof_message(rt, t_ms), ec.ECDSA(hashes.SHA256())))
    return {"t": t_ms, "sig": pk.b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}      # WebCrypto's raw r‖s


def test_device_bound_refresh(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    k, jwk = _device()
    c, d = call(api, "POST", "/v2/auth/session", {"id_token": fb_token(clock=clock), "device_key": jwk})
    assert c == 200 and d["session"]["device_bound"] is True
    rt = d["refresh_token"]
    c, r = call(api, "POST", "/v2/auth/refresh", {"refresh_token": rt, "proof": _proof(k, rt, int(clock.t * 1000))})
    assert c == 200, r
    rt2 = r["refresh_token"]
    # the refresh token copied to another machine (no device key) → refused and the session closed
    other, _ = _device()
    assert call(api, "POST", "/v2/auth/refresh", {"refresh_token": rt2, "proof": _proof(other, rt2, int(clock.t * 1000))}) == \
        (401, {"error": "session_revoked"})
    assert call(api, "GET", "/v2/auth/me", tok=r["access_token"])[0] == 401
    assert call(api, "POST", "/v2/auth/session", {"id_token": fb_token(clock=clock), "device_key": {"kty": "EC", "crv": "P-256",
                                                                                                    "x": "AA", "y": "AA"}})[0] == 400


# ------------------------------------------------------------------------------------------------ rate limits
def test_rate_limit_per_address_and_per_account(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    for _ in range(10):
        assert call(api, "POST", "/v2/auth/session", {"id_token": "bad"}, ip="7.7.7.7")[0] == 401
    c, d = call(api, "POST", "/v2/auth/session", {"id_token": fb_token(clock=clock)}, ip="7.7.7.7")
    assert c == 429 and d["error"] == "too_many_attempts" and 0 < d["retry_after_sec"] <= 15 * 60 + 1
    assert call(api, "POST", "/v2/auth/session", {"id_token": fb_token(clock=clock)}, ip="7.7.7.8")[0] == 200   # other address
    # one account from many addresses: 5 failures (unverified email) → that account waits even from a new address
    for i in range(5):
        assert call(api, "POST", "/v2/auth/session", {"id_token": fb_token(clock=clock, verified=False)}, ip=f"8.8.8.{i}")[0] == 403
    c, d = call(api, "POST", "/v2/auth/session", {"id_token": fb_token(clock=clock)}, ip="8.8.9.9")
    assert c == 429 and d["retry_after_sec"] > 0
    clock.t += 15 * 60 + 1                                         # the window passes
    assert call(api, "POST", "/v2/auth/session", {"id_token": fb_token(clock=clock)}, ip="7.7.7.7")[0] == 200
    # refresh guessing counts too
    for _ in range(10):
        assert call(api, "POST", "/v2/auth/refresh", {"refresh_token": "cnr_guess"}, ip="6.6.6.6")[0] == 401
    assert call(api, "POST", "/v2/auth/refresh", {"refresh_token": "cnr_guess"}, ip="6.6.6.6")[0] == 429


# ------------------------------------------------------------------------------------------------ owner-only endpoints
OWNER_ONLY = [("GET", "/v2/owner/overview"), ("GET", "/v2/owner/myspot"), ("GET", "/v2/owner/ivan"), ("GET", "/v2/auth/me"),
              ("GET", "/v2/auth/sessions"), ("GET", "/v2/auth/passkeys"), ("POST", "/v2/auth/passkey/register/options"),
              ("POST", "/v2/auth/passkey/register/verify"), ("POST", "/v2/auth/sessions/revoke"), ("POST", "/v2/auth/passkeys/delete"),
              ("GET", "/v2/admin/ping"), ("GET", "/v2/admin/overview")]


@pytest.mark.parametrize("method,path", OWNER_ONLY)
def test_owner_endpoints_refuse_anonymous_and_fake_tokens(tmp_path, monkeypatch, method, path):
    api, clock, _ = make(tmp_path, monkeypatch)
    body = {} if method == "POST" else None
    assert call(api, method, path, body)[0] == 401
    assert call(api, method, path, body, tok="cno_" + "x" * 43)[0] == 401
    if not path.startswith("/v2/admin"):
        assert call(api, method, path, body, tok="a" * 40)[0] == 401          # the admin token is not an owner session
        assert call(api, method, path, body, tok=fb_token(clock=clock))[0] == 401   # nor is a raw Firebase token


def test_admin_accepts_the_owner_session_and_still_the_token(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    d = signin(api, clock)
    assert call(api, "GET", "/v2/admin/ping", tok=d["access_token"])[0] == 200
    assert call(api, "GET", "/v2/admin/ping", tok="a" * 40)[0] == 200                 # CNQ_ADMIN_TOKEN keeps working
    monkeypatch.setenv("CNQ_ADMIN_TOKEN", "")
    assert call(api, "GET", "/v2/admin/ping", tok=d["access_token"])[0] == 200        # the session does not need the token
    assert call(api, "GET", "/v2/admin/ping", tok="a" * 40)[0] == 403
    call(api, "POST", "/v2/auth/logout", {}, tok=d["access_token"])
    assert call(api, "GET", "/v2/admin/ping", tok=d["access_token"]) == (401, {"error": "token_expired"})


def test_owner_private_sections_decrypted_on_the_server(tmp_path, monkeypatch):
    api, clock, web = make(tmp_path, monkeypatch)
    now = int(time.time() * 1000)
    ivan = {"asof": now, "equity": 1010.5, "positions": [{"symbol": "ETHUSDT"}], "max_dd": 0.12, "host": "main",
            "stats": {"n": 3, "wins": 3, "losses": 0, "winrate": 1.0, "pnl_sum": 4.2}, "closed": []}
    spot = {"asof": now, "assets": [{"ccy": "BTC"}, {"ccy": "ETH"}], "plan": {"total_usd": 2500.0}, "autopilot": {"state": "on"}}
    (web / "ivan.enc.json").write_text(json.dumps(ivan_watch.encrypt(ivan, "ivan pass phrase 1")))
    (web / "myspot.enc.json").write_text(json.dumps(ivan_watch.encrypt(spot, "my spot phrase 22")))
    tok = signin(api, clock)["access_token"]
    monkeypatch.delenv("IVAN_PASS", raising=False)
    monkeypatch.delenv("MYSPOT_PASS", raising=False)
    assert call(api, "GET", "/v2/owner/ivan", tok=tok)[1]["error"] == "not_configured"
    monkeypatch.setenv("IVAN_PASS", "wrong phrase")
    assert call(api, "GET", "/v2/owner/ivan", tok=tok) == (502, {"error": "bad_passphrase",
                                                                 "detail": "IVAN_PASS on the server does not open web/ivan.enc.json"})
    monkeypatch.setenv("IVAN_PASS", "ivan pass phrase 1")
    monkeypatch.setenv("MYSPOT_PASS", "my spot phrase 22")
    c, d = call(api, "GET", "/v2/owner/ivan", tok=tok)
    assert c == 200 and d["data"]["stats"]["n"] == 3 and d["title"] == "Иван тест" and d["age_sec"] is not None
    c, d = call(api, "GET", "/v2/owner/myspot", tok=tok)
    assert c == 200 and d["data"]["plan"]["total_usd"] == 2500.0
    c, o = call(api, "GET", "/v2/owner/overview", tok=tok)
    assert c == 200 and o["owner"]["email"] == OWNER and o["configured"] == {"myspot": True, "ivan": True, "admin_token": True}
    iv = o["sections"]["ivan"]
    assert (iv["winrate"], iv["closed"], iv["open"], iv["test"]) == (1.0, 3, 1, True)   # the rate always with its sample size
    assert o["sections"]["myspot"] == {**o["sections"]["myspot"], "total_usd": 2500.0, "assets": 2, "autopilot": "on"}
    assert call(api, "POST", "/v2/owner/ivan", {}, tok=tok)[0] == 405              # view-only
    assert call(api, "GET", "/v2/owner/../admin/ping", tok=tok)[0] in (401, 404)
    (web / "ivan.enc.json").unlink()
    assert call(api, "GET", "/v2/owner/ivan", tok=tok)[1]["error"] == "no_data"


def test_public_config(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    monkeypatch.delenv("CNQ_FIREBASE_WEB_API_KEY", raising=False)
    c, d = call(api, "GET", "/v2/auth/config")
    assert c == 200 and d["firebase"] is None and d["passkey"] == {"available": True, "rpId": "cryptonova.vip"}
    assert d["owner_configured"] is True and d["providers"] == ["password", "apple.com", "google.com"] and OWNER not in json.dumps(d)
    monkeypatch.setenv("CNQ_FIREBASE_WEB_API_KEY", "AIzaSy" + "A" * 33)
    monkeypatch.setenv("CNQ_FIREBASE_WEB_APP_ID", "1:224033549404:web:abc")
    fb = call(api, "GET", "/v2/auth/config")[1]["firebase"]
    assert fb == {"apiKey": "AIzaSy" + "A" * 33, "authDomain": "crypto-nova.firebaseapp.com", "projectId": "crypto-nova",
                  "appId": "1:224033549404:web:abc"}
    monkeypatch.setenv("CNQ_FIREBASE_WEB_API_KEY", "not a key")
    assert call(api, "GET", "/v2/auth/config")[1]["firebase"] is None


# ------------------------------------------------------------------------------------------------ passkeys, end to end on the API
class SoftAuthenticator:
    """A platform authenticator in software: ES256, user verification on, counter 0 (like iCloud Keychain passkeys)."""

    def __init__(self, rp_id="cryptonova.vip", origin=ORIGIN):
        self.k = ec.generate_private_key(ec.SECP256R1())
        self.cid = b"soft-" + bytes(range(27))
        self.rp_id, self.origin, self.handle = rp_id, origin, None

    def create(self, opts):
        pkey = opts["publicKey"]
        self.handle = pk.unb64u(pkey["user"]["id"])
        n = self.k.public_key().public_numbers()
        cose = _cbor({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})
        ad = hashlib.sha256(pkey["rp"]["id"].encode()).digest() + bytes([0x45 | 0x18]) + struct.pack(">I", 0) + b"\0" * 16 + \
            struct.pack(">H", len(self.cid)) + self.cid + cose
        cdj = json.dumps({"type": "webauthn.create", "challenge": pkey["challenge"], "origin": self.origin}).encode()
        return {"id": pk.b64u(self.cid), "rawId": pk.b64u(self.cid), "type": "public-key",
                "response": {"clientDataJSON": pk.b64u(cdj), "transports": ["internal", "hybrid"],
                             "attestationObject": pk.b64u(_cbor({"fmt": "none", "attStmt": {}, "authData": ad}))}}

    def get(self, opts, flags=0x05 | 0x18):
        pkey = opts["publicKey"]
        ad = hashlib.sha256(pkey["rpId"].encode()).digest() + bytes([flags]) + struct.pack(">I", 0)
        cdj = json.dumps({"type": "webauthn.get", "challenge": pkey["challenge"], "origin": self.origin}).encode()
        sig = self.k.sign(ad + hashlib.sha256(cdj).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": pk.b64u(self.cid), "rawId": pk.b64u(self.cid), "type": "public-key",
                "response": {"clientDataJSON": pk.b64u(cdj), "authenticatorData": pk.b64u(ad), "signature": pk.b64u(sig),
                             "userHandle": pk.b64u(self.handle)}}


def test_passkey_register_then_sign_in(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    s = signin(api, clock)
    tok = s["access_token"]
    dev = SoftAuthenticator()
    c, opts = call(api, "POST", "/v2/auth/passkey/register/options", {}, tok=tok)
    assert c == 200 and opts["publicKey"]["rp"] == {"id": "cryptonova.vip", "name": "CRYPTONOVA VIP"}
    assert opts["publicKey"]["authenticatorSelection"]["userVerification"] == "required" and opts["publicKey"]["attestation"] == "none"
    c, reg = call(api, "POST", "/v2/auth/passkey/register/verify", {"credential": dev.create(opts)}, tok=tok)
    assert c == 200 and reg["passkey"]["name"] == "iPhone · Safari" and reg["passkey"]["backed_up"] is True
    # the same challenge cannot be used twice
    assert call(api, "POST", "/v2/auth/passkey/register/verify", {"credential": dev.create(opts)}, tok=tok)[1]["error"] == "bad_challenge"
    c, opts2 = call(api, "POST", "/v2/auth/passkey/register/options", {}, tok=tok)
    assert [x["id"] for x in opts2["publicKey"]["excludeCredentials"]] == [pk.b64u(dev.cid)]
    # sign in with the passkey on a new visit (no session)
    c, lo = call(api, "POST", "/v2/auth/passkey/login/options", {})
    assert c == 200 and lo["publicKey"]["rpId"] == "cryptonova.vip" and lo["publicKey"]["userVerification"] == "required"
    c, d = call(api, "POST", "/v2/auth/passkey/login/verify", {"credential": dev.get(lo), "remember": True})
    assert c == 200 and d["session"]["method"] == "passkey" and d["access_token"].startswith("cno_")
    assert call(api, "GET", "/v2/owner/overview", tok=d["access_token"])[0] == 200
    # replaying the same assertion (challenge used) / no Face ID (UV off) / a passkey we do not know / another site's origin
    assert call(api, "POST", "/v2/auth/passkey/login/verify", {"credential": dev.get(lo)})[1]["error"] == "bad_challenge"
    lo = call(api, "POST", "/v2/auth/passkey/login/options", {})[1]
    assert call(api, "POST", "/v2/auth/passkey/login/verify", {"credential": dev.get(lo, flags=0x01)})[1]["error"] == "passkey_invalid"
    lo = call(api, "POST", "/v2/auth/passkey/login/options", {})[1]
    stranger = SoftAuthenticator()
    stranger.cid, stranger.handle = b"unknown-credential-0001", b"x"
    assert call(api, "POST", "/v2/auth/passkey/login/verify", {"credential": stranger.get(lo)})[1]["error"] == "unknown_passkey"
    lo = call(api, "POST", "/v2/auth/passkey/login/options", {})[1]
    phish = SoftAuthenticator(origin="https://cryptonova-vip.example")
    phish.k, phish.cid, phish.handle = dev.k, dev.cid, dev.handle
    assert call(api, "POST", "/v2/auth/passkey/login/verify", {"credential": phish.get(lo)})[1]["error"] == "passkey_invalid"
    # list and delete (explicit confirm); afterwards the passkey no longer signs in
    c, lst = call(api, "GET", "/v2/auth/passkeys", tok=tok)
    assert c == 200 and len(lst["passkeys"]) == 1 and "public_key" not in lst["passkeys"][0]
    pid = lst["passkeys"][0]["id"]
    assert call(api, "POST", "/v2/auth/passkeys/delete", {"id": pid}, tok=tok)[1]["error"] == "confirm_required"
    assert call(api, "POST", "/v2/auth/passkeys/delete", {"id": pid, "confirm": True}, tok=tok)[0] == 200
    lo = call(api, "POST", "/v2/auth/passkey/login/options", {})[1]
    assert call(api, "POST", "/v2/auth/passkey/login/verify", {"credential": dev.get(lo)})[1]["error"] == "unknown_passkey"


def test_adding_a_passkey_needs_a_fresh_sign_in(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    s = signin(api, clock)
    clock.t += 16 * 60
    tok = call(api, "POST", "/v2/auth/refresh", {"refresh_token": s["refresh_token"]})[1]["access_token"]
    assert call(api, "POST", "/v2/auth/passkey/register/options", {}, tok=tok) == (403, {"error": "reauth_required"})


def test_passkey_of_a_former_owner_does_not_open_the_panel(tmp_path, monkeypatch):
    api, clock, _ = make(tmp_path, monkeypatch)
    tok = signin(api, clock)["access_token"]
    dev = SoftAuthenticator()
    opts = call(api, "POST", "/v2/auth/passkey/register/options", {}, tok=tok)[1]
    assert call(api, "POST", "/v2/auth/passkey/register/verify", {"credential": dev.create(opts)}, tok=tok)[0] == 200
    monkeypatch.setenv("CNQ_OWNER_EMAIL", "someone-else@x.io")
    lo = call(api, "POST", "/v2/auth/passkey/login/options", {})[1]
    assert call(api, "POST", "/v2/auth/passkey/login/verify", {"credential": dev.get(lo)}) == (403, {"error": "not_owner"})


def test_cors_preflight_for_the_sign_in_over_http(tmp_path, monkeypatch):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from cnq.api.server import make_handler
    api, clock, _ = make(tmp_path, monkeypatch)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        req = urllib.request.Request(base + "/v2/auth/session", method="OPTIONS")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 204 and r.headers["Access-Control-Allow-Origin"] == ORIGIN
            assert "Authorization" in r.headers["Access-Control-Allow-Headers"]
        with urllib.request.urlopen(base + "/v2/auth/config", timeout=5) as r:
            assert r.headers["Access-Control-Allow-Origin"] == ORIGIN and json.loads(r.read())["passkey"]["rpId"] == "cryptonova.vip"
        try:
            urllib.request.urlopen(base + "/v2/owner/overview", timeout=5)
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as e:
            assert e.code == 401 and e.headers["Access-Control-Allow-Origin"] == ORIGIN and e.headers["Cache-Control"] == "no-store"
    finally:
        srv.shutdown()
