"""Smoke test of web/vip/auth.js in a real browser (Playwright, Chromium): the page is served as https://cryptonova.vip from
memory, the API calls go to the real cnq/api/server.py in this process (some answers replaced to simulate 429 and a lost
connection), Firebase's two ES modules are replaced by a small fake, and Face ID is Chromium's virtual WebAuthn authenticator — so
the passkey, the device-bound refresh (WebCrypto ↔ cryptography) and the sign-in sheet are checked end to end.
Skipped where Playwright or its Chromium is not installed (CI's test gate runs tests/run.py, which does not list this file)."""
import json
import os
import time
from urllib.parse import urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from cnq.api.auth import FirebaseVerifier
from cnq.api.server import Api
from cnq.api.store import Store

sync_api = pytest.importorskip("playwright.sync_api")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH_JS = open(os.path.join(ROOT, "web", "vip", "auth.js"), encoding="utf-8").read()
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OWNER = "owner@cryptonova.test"
FB = "https://www.gstatic.com/firebasejs/10.14.1/"

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>VIP auth test</title>
<script>window.CN_AUTH_CONFIG = {api: 'https://cryptonova.vip'};</script>
<script src="/vip/auth.js"></script></head><body style="background:#07102a"><div id="login"></div></body></html>"""

FAKE_APP = "export function initializeApp(options, name) { return {options, name}; }"
FAKE_AUTH = """
const F = () => (window.__FAKE = window.__FAKE || {tokens: {}, passwords: {}, resets: [], calls: []});
const err = (code) => { const e = new Error(code); e.code = code; return e; };
const userOf = (email) => ({email, getIdToken: async () => { const t = F().tokens[email]; if (!t) throw err('auth/internal-error'); return t; }});
export const browserSessionPersistence = {type: 'SESSION'};
export const browserPopupRedirectResolver = {};
export function initializeAuth(app, deps) { F().calls.push('init'); return {app, deps, languageCode: null}; }
export async function signInWithEmailAndPassword(auth, email, pass) {
  F().calls.push('email');
  if (F().throttle) throw err('auth/too-many-requests');
  if (F().passwords[email] !== pass) throw err('auth/invalid-credential');
  return {user: userOf(email)};
}
export class GoogleAuthProvider { constructor() { this.providerId = 'google.com'; } setCustomParameters(p) { this.p = p; } addScope() {} }
export class OAuthProvider { constructor(id) { this.providerId = id; } setCustomParameters(p) { this.p = p; } addScope() {} }
export async function signInWithPopup(auth, provider) {
  F().calls.push('popup:' + provider.providerId);
  const email = F().popup && F().popup[provider.providerId];
  if (!email) throw err('auth/popup-closed-by-user');
  return {user: userOf(email)};
}
export async function signInWithRedirect() { F().calls.push('redirect'); }
export async function getRedirectResult() { return null; }
export async function sendPasswordResetEmail(auth, email, settings) { F().resets.push({email, url: settings && settings.url}); }
export async function sendEmailVerification(user) { F().calls.push('verify:' + user.email); }
export async function signOut() { F().calls.push('signOut'); }
"""


def fb_token(email=OWNER, provider="password", verified=True):
    now = int(time.time())
    return jwt.encode({"sub": "uid-" + email, "aud": "crypto-nova", "iss": "https://securetoken.google.com/crypto-nova", "iat": now,
                       "exp": now + 3600, "auth_time": now - 3, "email": email, "email_verified": verified,
                       "firebase": {"sign_in_provider": provider}}, KEY, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture()
def browser():
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"playwright unavailable: {e}")
    try:
        b = pw.chromium.launch()
    except Exception as e:  # noqa: BLE001
        pw.stop()
        pytest.skip(f"chromium unavailable: {e}")
    yield b
    b.close()
    pw.stop()


def test_auth_js_sign_in_sheet_sessions_and_passkey(tmp_path, monkeypatch, browser):
    monkeypatch.setenv("CNQ_OWNER_EMAIL", OWNER)
    monkeypatch.setenv("CNQ_FIREBASE_WEB_API_KEY", "AIzaSy" + "T" * 33)
    monkeypatch.delenv("CNQ_ADMIN_ORIGIN", raising=False)
    monkeypatch.delenv("CNQ_PASSKEY_RP_ID", raising=False)
    web = tmp_path / "web"
    web.mkdir()
    (web / "app_home.json").write_text(json.dumps({"asof": int(time.time() * 1000)}))
    api = Api(Store(str(tmp_path / "api.sqlite")), FirebaseVerifier("crypto-nova", keys={"k1": KEY.public_key()}), str(web), None)

    override, seen = {}, []

    def handle(route, request):
        u = urlsplit(request.url)
        if u.path == "/vip-test.html":
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=PAGE)
        if u.path == "/vip/auth.js":
            return route.fulfill(status=200, content_type="application/javascript; charset=utf-8", body=AUTH_JS)
        if u.path.startswith("/v2/"):
            seen.append((request.method, u.path, request.headers.get("authorization", "")))
            o = override.get(u.path)
            if o == "offline":
                return route.abort("internetdisconnected")
            if o:
                return route.fulfill(status=o[0], content_type="application/json", body=json.dumps(o[1]))
            body = request.post_data_buffer or b""
            hdrs = {k.lower(): v for k, v in request.headers.items()}
            code, doc = api.handle(request.method, u.path + ("?" + u.query if u.query else ""), hdrs, body)
            return route.fulfill(status=code, content_type="application/json", body=json.dumps(doc))
        return route.fulfill(status=404, body="")

    def gstatic(route, request):
        src = FAKE_APP if request.url.endswith("firebase-app.js") else FAKE_AUTH
        route.fulfill(status=200, content_type="application/javascript", body=src, headers={"Access-Control-Allow-Origin": "*"})

    ctx = browser.new_context(locale="ru-RU", user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
                                                          "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
    ctx.route("https://cryptonova.vip/**", handle)
    ctx.route(FB + "*", gstatic)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    cdp = ctx.new_cdp_session(page)                         # Face ID / Touch ID = Chromium's virtual platform authenticator
    cdp.send("WebAuthn.enable")
    cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {"protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
                                                               "hasUserVerification": True, "isUserVerified": True,
                                                               "automaticPresenceSimulation": True}})
    page.add_init_script(f"window.__FAKE = {{tokens: {{}}, passwords: {{{json.dumps(OWNER)}: 'right pass'}}, resets: [], calls: []}};")

    def fresh_tokens():
        page.evaluate("t => Object.assign(window.__FAKE.tokens, t)",
                      {OWNER: fb_token(), "stranger@x.io": fb_token("stranger@x.io"), "apple-owner": fb_token(provider="apple.com")})

    page.goto("https://cryptonova.vip/vip-test.html")
    page.evaluate("CNAuth.ready")
    assert page.evaluate("CNAuth.state") == {"signedIn": False, "email": None, "isOwner": False, "method": None, "ready": True}
    page.evaluate("CNAuth.mountLogin(document.getElementById('login'))")
    fresh_tokens()

    # the sheet: Russian, labelled inputs with iOS password autofill hints, the three ways in
    assert page.get_by_role("heading", name="Вход в CRYPTONOVA VIP").is_visible()
    assert page.get_by_label("Почта").get_attribute("autocomplete") == "username"
    assert page.get_by_label("Пароль", exact=True).get_attribute("autocomplete") == "current-password"
    for name in ("Войти через Apple", "Войти через Google", "Войти с Touch ID", "Забыли пароль?"):
        page.get_by_role("button", name=name).wait_for(state="visible")
    alert = page.locator("[data-view=signin] .cna-msg")

    # wrong password → the exact text; Firebase lockout → minutes; our server's 429 → its minutes; no network → «Нет связи»
    page.get_by_label("Почта").fill(OWNER)
    page.get_by_label("Пароль", exact=True).fill("wrong")
    page.get_by_role("button", name="Войти", exact=True).click()
    sync_api.expect(alert).to_have_text("Неверная почта или пароль")
    page.evaluate("window.__FAKE.throttle = true")
    page.get_by_label("Пароль", exact=True).fill("right pass")
    page.get_by_role("button", name="Войти", exact=True).click()
    sync_api.expect(alert).to_have_text("Слишком много попыток — подождите 15 мин")
    page.evaluate("window.__FAKE.throttle = false")
    override["/v2/auth/session"] = (429, {"error": "too_many_attempts", "retry_after_sec": 170})
    page.get_by_label("Пароль", exact=True).fill("right pass")
    page.get_by_role("button", name="Войти", exact=True).click()
    sync_api.expect(alert).to_have_text("Слишком много попыток — подождите 3 мин")
    override["/v2/auth/session"] = "offline"
    page.get_by_label("Пароль", exact=True).fill("right pass")
    page.get_by_role("button", name="Войти", exact=True).click()
    sync_api.expect(alert).to_have_text("Нет связи")
    override.clear()

    # a Google account that is not the owner's
    page.evaluate("window.__FAKE.popup = {'google.com': 'stranger@x.io'}")
    page.get_by_role("button", name="Войти через Google").click()
    sync_api.expect(alert).to_have_text("Этот аккаунт не владелец панели")

    # password reset goes through Firebase's own email
    page.get_by_role("button", name="Забыли пароль?").click()
    sync_api.expect(page.locator("[data-view=signin] .cna-note")).to_contain_text("письмо для смены пароля уже отправлено")
    assert page.evaluate("window.__FAKE.resets")[0]["email"] == OWNER

    # the owner signs in with email + password → offered Touch ID → enables it (real WebAuthn, verified by the server)
    page.get_by_label("Пароль", exact=True).fill("right pass")
    page.get_by_role("button", name="Войти", exact=True).click()
    page.get_by_role("button", name="Включить").click()
    sync_api.expect(page.locator(".cna-who")).to_contain_text(OWNER)
    st = page.evaluate("CNAuth.state")
    assert st["signedIn"] and st["isOwner"] and st["method"] == "email"
    assert "signOut" in page.evaluate("window.__FAKE.calls")            # Firebase's own login is dropped after the exchange
    ov = page.evaluate("CNAuth.fetchOwner('/v2/owner/overview')")
    assert ov["owner"]["email"] == OWNER and ov["session"]["device_bound"] is True
    assert any(p == "/v2/owner/overview" and a.startswith("Bearer cno_") for _m, p, a in seen)
    lst = page.evaluate("CNAuth.fetchOwner('/v2/auth/passkeys')")
    assert len(lst["passkeys"]) == 1 and lst["passkeys"][0]["name"] == "Mac · Safari"
    stored = page.evaluate("JSON.parse(localStorage.getItem('cnv_auth'))")
    assert stored["rt"].startswith("cnr_") and stored["bound"] is True and "access" not in json.dumps(stored)

    # a new visit: the remembered session comes back through a device-signed refresh, no password
    page.reload()
    page.evaluate("CNAuth.ready")
    assert page.evaluate("CNAuth.state")["signedIn"] is True
    assert page.evaluate("CNAuth.fetchOwner('/v2/auth/me')")["email"] == OWNER

    # sign out → the server session is gone, the device forgets it
    page.evaluate("CNAuth.signOut()")
    assert page.evaluate("CNAuth.state")["signedIn"] is False and page.evaluate("localStorage.getItem('cnv_auth')") is None
    err = page.evaluate("CNAuth.fetchOwner('/v2/owner/overview').then(() => null, e => e.code)")
    assert err == "unauthorized"
    assert api.owner_auth._q("SELECT COUNT(*) FROM owner_sessions WHERE revoked=0")[0][0] == 0

    # Touch ID sign-in with the passkey added above
    st = page.evaluate("CNAuth.passkeyLogin()")
    assert st["signedIn"] and st["method"] == "passkey" and st["email"] == OWNER
    assert page.evaluate("CNAuth.fetchOwner('/v2/owner/overview')")["owner"]["method"] == "passkey"

    # an expired access token is refreshed by fetchOwner itself
    page.evaluate("CNAuth.signOut()")
    fresh_tokens()
    page.evaluate("window.__FAKE.popup = {'apple.com': 'apple-owner'}")
    st = page.evaluate("CNAuth.signInApple()")
    assert st["method"] == "apple"
    api.owner_auth._q("UPDATE owner_access SET expires=0")
    assert page.evaluate("CNAuth.fetchOwner('/v2/auth/me')")["method"] == "apple"
    assert errors == []
