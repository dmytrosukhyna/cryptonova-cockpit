"""Owner sign-in for the closed panel CRYPTONOVA VIP (https://cryptonova.vip) — owner spec §3, §12, §17 (28.10.2026, owner: «для
входа используются какие-то дополнительные обозначения вроде CNQ и отдельные пароли … не могу нормально войти с мобильного»).

ONE sign-in, checked on the server, replaces the per-section passwords:
  1. The page signs the owner in with Firebase Auth (the app's project, crypto-nova): email + password (reset by Firebase's own
     email), «Войти через Apple», «Войти через Google». It sends the fresh Firebase ID token to POST /v2/auth/session.
  2. The server checks Google's signature (cnq/api/auth.py), that the email is the owner's (CNQ_OWNER_EMAIL in /etc/cnq.env, one
     or several addresses separated by commas), that Firebase VERIFIED that email (a password account made with someone else's
     address is unverified) and that the person really signed in during the last 10 minutes (auth_time — a silently refreshed
     token from an old login is refused). Then it opens an owner session.
  3. Passkeys (Face ID / Touch ID / the device PIN, WebAuthn — cnq/api/passkey.py): added inside a fresh owner session, then used
     to sign in again with one touch. Only the public key is stored; the face or finger never leaves the device.

Why bearer tokens and not a cookie: the page is on GitHub Pages (cryptonova.vip) and the API is on another site
(app.crypto-capital.app → nginx → this process). A cookie of the API's site is a third-party cookie on cryptonova.vip: SameSite=Strict
or Lax cookies are not sent with the page's requests at all, and SameSite=None cookies are blocked by Safari (iPhone) and more and
more by Chrome. So the session is:
  • an ACCESS token «cno_…» — 15 minutes, kept only in the page's memory, sent as «Authorization: Bearer»;
  • a REFRESH token «cnr_…» — single use: every refresh returns a new one (rotation). Presenting an already rotated token after
    the 30-second grace (two tabs at once) means it was copied → the whole session is revoked. Stored by the page in
    localStorage («запомнить устройство», 30 days of inactivity, 90 days at most) or sessionStorage (12 hours);
  • optionally bound to the device: the page makes a non-extractable P-256 key in the browser (WebCrypto, IndexedDB) and sends
    its public half when the session opens; every refresh must then be signed by that key, so a refresh token copied off the
    device is useless elsewhere (a refresh without the right signature revokes the session).
No cookie means no CSRF. Tokens are stored only as SHA-256 hashes (data/api.sqlite, git-ignored); nothing is logged in clear.

Rate limits (data/api.sqlite, survive restarts): per address 10 failed sign-ins / refreshes per 15 minutes and 60 attempts in
all; per account (email, passkey or session) 5 failures per 15 minutes → 429 {"retry_after_sec"}. Firebase itself throttles
password guessing (auth/too-many-requests) before anything reaches us.

Endpoints (all JSON; CORS for https://cryptonova.vip — cnq/api/server.py):
  GET  /v2/auth/config                       public: the Firebase web config (CNQ_FIREBASE_WEB_API_KEY …), passkey RP id, TTLs
  POST /v2/auth/session                      {id_token, remember, device_key?} → {access_token, expires_in, refresh_token, session}
  POST /v2/auth/refresh                      {refresh_token, proof?} → the same shape (new refresh token); 409 rotated (retry)
  POST /v2/auth/logout                       this session (access token, or {refresh_token}); {all: true, confirm: true} = every one
  GET  /v2/auth/me                           {signedIn, email, isOwner, method, session}
  GET  /v2/auth/sessions                     the owner's open sessions (devices)
  POST /v2/auth/sessions/revoke              {id, confirm: true}
  POST /v2/auth/passkey/register/options     (owner, signed in ≤ 15 min ago) → WebAuthn creation options
  POST /v2/auth/passkey/register/verify      {credential, name?} → the stored passkey (public key only)
  POST /v2/auth/passkey/login/options        public → WebAuthn request options (discoverable credentials)
  POST /v2/auth/passkey/login/verify         {credential, remember, device_key?} → an owner session
  GET  /v2/auth/passkeys                     the owner's passkeys
  POST /v2/auth/passkeys/delete              {id, confirm: true}
Owner data and admin calls accept this session: GET /v2/owner/* (server.py) and /v2/admin/* (console.py, next to CNQ_ADMIN_TOKEN).
Nothing here touches trading, risk, the autopilot or exchange keys."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from . import passkey as pk
from .auth import AuthError

ACCESS_PREFIX, REFRESH_PREFIX = "cno_", "cnr_"
ACCESS_TTL = 15 * 60
REMEMBER_IDLE = 30 * 86400
SHORT_IDLE = 12 * 3600
ABSOLUTE = 90 * 86400
FRESH_SIGNIN = 10 * 60          # Firebase auth_time must be at most this old when a session opens
PASSKEY_ADD_WINDOW = 15 * 60    # a passkey is added only within this time after a full sign-in
ROTATE_GRACE = 30               # s: the token just rotated away answers 409 (two tabs) instead of revoking the session
CHALLENGE_TTL = 5 * 60
PROOF_SKEW = 5 * 60
WINDOW = 15 * 60
IP_FAIL_MAX, ACCT_FAIL_MAX, IP_TRY_MAX = 10, 5, 60
MAX_PASSKEYS = 10
MAX_CHALLENGES = 500
RP_NAME = "CRYPTONOVA VIP"
PROVIDERS = {"password": "email", "google.com": "google", "apple.com": "apple"}
API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{30,40}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_sessions(id TEXT PRIMARY KEY, uid TEXT, email TEXT, method TEXT, created INTEGER, auth_time INTEGER,
    last_used INTEGER, expires INTEGER, abs_expires INTEGER, remember INTEGER, refresh_hash TEXT, revoked INTEGER DEFAULT 0,
    revoked_why TEXT, ip TEXT, ua TEXT, device_jwk TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS owner_sessions_refresh ON owner_sessions(refresh_hash);
CREATE TABLE IF NOT EXISTS owner_access(hash TEXT PRIMARY KEY, sid TEXT, expires INTEGER);
CREATE TABLE IF NOT EXISTS owner_refresh_old(hash TEXT PRIMARY KEY, sid TEXT, t INTEGER);
CREATE TABLE IF NOT EXISTS auth_attempts(k TEXT, t INTEGER, ok INTEGER);
CREATE INDEX IF NOT EXISTS auth_attempts_k ON auth_attempts(k, t);
CREATE TABLE IF NOT EXISTS passkeys(id TEXT PRIMARY KEY, uid TEXT, email TEXT, user_handle TEXT, public_key TEXT, alg INTEGER,
    sign_count INTEGER, transports TEXT, name TEXT, fmt TEXT, aaguid TEXT, backed_up INTEGER, created INTEGER, last_used INTEGER);
CREATE TABLE IF NOT EXISTS webauthn_challenges(hash TEXT PRIMARY KEY, purpose TEXT, sid TEXT, expires INTEGER);
"""


def owner_emails() -> set[str]:
    """CNQ_OWNER_EMAIL: one address, or several separated by commas (e.g. the Gmail and an Apple «Hide my email» address)."""
    return {e.strip().lower() for e in re.split(r"[,;\s]+", os.environ.get("CNQ_OWNER_EMAIL") or "") if e.strip()}


def is_owner(email: str | None, verified: bool | None = True) -> bool:
    """The owner = a VERIFIED email in CNQ_OWNER_EMAIL. Unset → nobody."""
    return bool(verified) and (email or "").strip().lower() in owner_emails() and bool(owner_emails())


def _h(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _bearer(headers: dict) -> str:
    got = str(headers.get("authorization") or "")
    return got[7:].strip() if got[:7].lower() == "bearer " else ""


def client_ip(headers: dict) -> str:
    return str(headers.get("x-real-ip") or headers.get("x-forwarded-for") or "local").split(",")[0].strip()[:64]


def device_label(ua: str) -> str:
    ua = ua or ""
    dev = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
                               ("Macintosh", "Mac"), ("Linux", "Linux")) if k in ua), "устройство")
    br = next((n for k, n in (("Edg/", "Edge"), ("CriOS", "Chrome"), ("Chrome/", "Chrome"), ("FxiOS", "Firefox"),
                              ("Firefox/", "Firefox"), ("Safari/", "Safari")) if k in ua), "")
    return f"{dev} · {br}" if br else dev


def _jwk(raw) -> dict | None:
    """The device's public key (P-256 JWK from WebCrypto) → a clean {kty, crv, x, y}, or None. Raises ValueError when malformed."""
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict) or raw.get("kty") != "EC" or raw.get("crv") != "P-256":
        raise ValueError("bad device key")
    from cryptography.hazmat.primitives.asymmetric import ec
    x, y = pk.unb64u(str(raw.get("x") or "")), pk.unb64u(str(raw.get("y") or ""))
    if len(x) != 32 or len(y) != 32:
        raise ValueError("bad device key")
    try:
        ec.EllipticCurvePublicNumbers(int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()).public_key()
    except ValueError as e:
        raise ValueError("bad device key") from e
    return {"kty": "EC", "crv": "P-256", "x": pk.b64u(x), "y": pk.b64u(y)}


def proof_message(refresh_token: str, t: int) -> bytes:
    return f"cnq-refresh:{int(t)}:{refresh_token}".encode()


def verify_proof(jwk: dict, refresh_token: str, proof, now_ms: int) -> bool:
    """proof = {t: ms, sig: base64url of the raw (r‖s, 64 bytes) ECDSA P-256 / SHA-256 signature WebCrypto makes}"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    if not isinstance(proof, dict):
        return False
    try:
        t = int(proof.get("t"))
        sig = pk.unb64u(str(proof.get("sig") or ""))
    except (TypeError, ValueError):
        return False
    if abs(now_ms - t) > PROOF_SKEW * 1000 or len(sig) != 64:
        return False
    try:
        pub = ec.EllipticCurvePublicNumbers(int.from_bytes(pk.unb64u(jwk["x"]), "big"), int.from_bytes(pk.unb64u(jwk["y"]), "big"),
                                            ec.SECP256R1()).public_key()
        der = utils.encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
        pub.verify(der, proof_message(refresh_token, t), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, KeyError):
        return False


class OwnerAuth:
    def __init__(self, store, verifier, now=time.time, origin: str | None = None):
        self.store, self.verifier, self.now = store, verifier, now
        self._origin = origin                   # tests; else console.origin() (CNQ_ADMIN_ORIGIN or https://cryptonova.vip)
        self.lock = threading.Lock()
        self._calls = 0
        with store.lock:
            store.conn.executescript(SCHEMA)
            store.conn.commit()

    # ------------------------------------------------------------------------------------------------ small helpers
    def _ms(self) -> int:
        return int(self.now() * 1000)

    def _q(self, sql: str, args: tuple = ()) -> list:
        return self.store._q(sql, args)

    def _log(self, action: str, detail: dict) -> None:
        try:
            self._q("INSERT INTO admin_log VALUES(?,?,?)", (self._ms(), action, json.dumps(detail, ensure_ascii=False)[:2000]))
        except Exception:  # noqa: BLE001 — the audit line never breaks a sign-in
            pass

    def origins(self) -> list[str]:
        from .console import origin
        base = self._origin or origin()
        extra = [o.strip().rstrip("/") for o in (os.environ.get("CNQ_PASSKEY_ORIGINS") or "").split(",") if o.strip()]
        return [base.rstrip("/")] + [o for o in extra if o != base.rstrip("/")]

    def rp_id(self) -> str:
        return (os.environ.get("CNQ_PASSKEY_RP_ID") or urlsplit(self.origins()[0]).hostname or "cryptonova.vip").strip().lower()

    # ------------------------------------------------------------------------------------------------ rate limits
    def _recent(self, key: str, now: int, failed_only: bool = True) -> list[int]:
        sql = "SELECT t FROM auth_attempts WHERE k=? AND t>?" + (" AND ok=0" if failed_only else "") + " ORDER BY t"
        return [r[0] for r in self._q(sql, (key, now - WINDOW * 1000))]

    def throttled(self, ip: str, acct: str | None = None) -> int | None:
        """seconds to wait, or None"""
        now = self._ms()
        waits = []
        checks = [(f"ip:{ip}", IP_FAIL_MAX, True), (f"ip:{ip}", IP_TRY_MAX, False)]
        if acct:
            checks.append((f"acct:{acct}", ACCT_FAIL_MAX, True))
        for key, cap, failed in checks:
            ts = self._recent(key, now, failed)
            if len(ts) >= cap:
                waits.append(int((ts[-cap] + WINDOW * 1000 - now) / 1000) + 1)
        return max(waits) if waits else None

    def _note(self, ip: str, acct: str | None, ok: bool) -> None:
        now = self._ms()
        self._q("INSERT INTO auth_attempts VALUES(?,?,?)", (f"ip:{ip}", now, int(ok)))
        if acct:
            if ok:
                self._q("DELETE FROM auth_attempts WHERE k=?", (f"acct:{acct}",))
            else:
                self._q("INSERT INTO auth_attempts VALUES(?,?,?)", (f"acct:{acct}", now, 0))
        self._calls += 1
        if self._calls % 50 == 0:
            self._q("DELETE FROM auth_attempts WHERE t<?", (now - 86_400_000,))
            self._q("DELETE FROM owner_access WHERE expires<?", (now,))
            self._q("DELETE FROM owner_refresh_old WHERE t<?", (now - ABSOLUTE * 1000,))
            self._q("DELETE FROM webauthn_challenges WHERE expires<?", (now,))

    @staticmethod
    def _429(wait: int) -> tuple[int, dict]:
        return 429, {"error": "too_many_attempts", "retry_after_sec": int(wait)}

    # ------------------------------------------------------------------------------------------------ sessions
    def _pub(self, row: dict, current: str | None = None) -> dict:
        return {"id": row["id"], "email": row["email"], "method": row["method"], "remember": bool(row["remember"]),
                "created": row["created"], "auth_time": row["auth_time"], "last_used": row["last_used"], "expires": row["expires"],
                "device": device_label(row.get("ua") or ""), "ip": row.get("ip"), "device_bound": bool(row.get("device_jwk")),
                **({"current": row["id"] == current} if current is not None else {})}

    _COLS = ("id", "uid", "email", "method", "created", "auth_time", "last_used", "expires", "abs_expires", "remember",
             "refresh_hash", "revoked", "revoked_why", "ip", "ua", "device_jwk")

    def _row(self, where: str, args: tuple) -> dict | None:
        r = self._q(f"SELECT {', '.join(self._COLS)} FROM owner_sessions WHERE {where}", args)
        return dict(zip(self._COLS, r[0])) if r else None

    def _access(self, sid: str, now: int) -> tuple[str, int]:
        at = ACCESS_PREFIX + secrets.token_urlsafe(32)
        self._q("INSERT INTO owner_access VALUES(?,?,?)", (_h(at), sid, now + ACCESS_TTL * 1000))
        return at, ACCESS_TTL

    def _issue(self, uid: str, email: str, method: str, auth_time_ms: int, remember: bool, headers: dict, jwk: dict | None) -> dict:
        now = self._ms()
        sid = secrets.token_urlsafe(12)
        rt = REFRESH_PREFIX + secrets.token_urlsafe(32)
        idle = REMEMBER_IDLE if remember else SHORT_IDLE
        absolute = now + (ABSOLUTE if remember else SHORT_IDLE) * 1000
        self._q("INSERT INTO owner_sessions(id, uid, email, method, created, auth_time, last_used, expires, abs_expires, remember, "
                "refresh_hash, ip, ua, device_jwk) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, uid, email, method, now, auth_time_ms, now, min(now + idle * 1000, absolute), absolute, int(bool(remember)),
                 _h(rt), client_ip(headers), str(headers.get("user-agent") or "")[:200], json.dumps(jwk) if jwk else None))
        at, ttl = self._access(sid, now)
        self._log("owner_signin", {"method": method, "remember": bool(remember), "bound": bool(jwk), "ip": client_ip(headers),
                                   "device": device_label(str(headers.get("user-agent") or ""))})
        return {"access_token": at, "expires_in": ttl, "refresh_token": rt, "session": self._pub(self._row("id=?", (sid,)))}

    def revoke(self, sid: str, why: str) -> int:
        n = len(self._q("SELECT 1 FROM owner_sessions WHERE id=? AND revoked=0", (sid,)))
        self._q("UPDATE owner_sessions SET revoked=1, revoked_why=? WHERE id=? AND revoked=0", (why, sid))
        self._q("DELETE FROM owner_access WHERE sid=?", (sid,))
        return n

    def session(self, headers: dict) -> dict | None:
        """The owner session behind «Authorization: Bearer cno_…», or None (missing, wrong, expired, revoked, no longer the owner)."""
        tok = _bearer(headers)
        if not tok.startswith(ACCESS_PREFIX) or len(tok) > 100:
            return None
        now = self._ms()
        r = self._q("SELECT sid, expires FROM owner_access WHERE hash=?", (_h(tok),))
        if not r or r[0][1] < now:
            return None
        row = self._row("id=?", (r[0][0],))
        if not row or row["revoked"] or row["expires"] < now or not is_owner(row["email"]):
            return None
        if now - (row["last_used"] or 0) > 60_000:
            self._q("UPDATE owner_sessions SET last_used=? WHERE id=?", (now, row["id"]))
        return row

    def require(self, headers: dict) -> tuple[dict | None, tuple[int, dict] | None]:
        s = self.session(headers)
        if s:
            return s, None
        tok = _bearer(headers)
        return None, (401, {"error": "token_expired" if tok.startswith(ACCESS_PREFIX) else "unauthorized"})

    # ------------------------------------------------------------------------------------------------ sign-in
    def signin_firebase(self, data: dict, headers: dict) -> tuple[int, dict]:
        ip = client_ip(headers)
        tok = str(data.get("id_token") or "")
        if not tok or len(tok) > 4096:
            return 400, {"error": "no_token"}
        wait = self.throttled(ip)
        if wait:
            return self._429(wait)
        try:
            jwk = _jwk(data.get("device_key"))
        except ValueError:
            return 400, {"error": "bad_device_key"}
        try:
            who = self.verifier.verify(tok)
        except (AuthError, ValueError) as e:
            self._note(ip, None, False)
            return 401, {"error": "bad_token", "detail": str(e)[:80]}
        email = (who.get("email") or "").strip().lower()
        acct = email or who.get("uid")
        wait = self.throttled(ip, acct)
        if wait:
            return self._429(wait)
        if not owner_emails():
            return 503, {"error": "owner_not_configured"}
        if not is_owner(email, True):
            self._note(ip, acct, False)
            return 403, {"error": "not_owner"}
        if not who.get("email_verified"):
            self._note(ip, acct, False)
            return 403, {"error": "email_not_verified"}
        at = who.get("auth_time")
        try:
            at = int(at)
        except (TypeError, ValueError):
            at = 0
        if not at or self.now() - at > FRESH_SIGNIN:
            self._note(ip, acct, False)
            return 401, {"error": "stale_sign_in"}
        self._note(ip, acct, True)
        method = PROVIDERS.get(str(who.get("provider") or ""), str(who.get("provider") or "firebase")[:20])
        return 200, self._issue(who["uid"], email, method, at * 1000, bool(data.get("remember", True)), headers, jwk)

    def refresh(self, data: dict, headers: dict) -> tuple[int, dict]:
        ip = client_ip(headers)
        rt = str(data.get("refresh_token") or "")
        if not rt.startswith(REFRESH_PREFIX) or len(rt) > 100:
            return 400, {"error": "no_token"}
        wait = self.throttled(ip)
        if wait:
            return self._429(wait)
        with self.lock:                     # one rotation at a time
            now = self._ms()
            h = _h(rt)
            row = self._row("refresh_hash=?", (h,))
            if not row:
                old = self._q("SELECT sid, t FROM owner_refresh_old WHERE hash=?", (h,))
                if old:
                    sid, t = old[0]
                    if now - t <= ROTATE_GRACE * 1000:
                        return 409, {"error": "rotated"}
                    if self.revoke(sid, "refresh_reuse"):
                        self._log("owner_refresh_reuse", {"ip": ip})
                    self._note(ip, f"sid:{sid}", False)
                    return 401, {"error": "session_revoked"}
                self._note(ip, None, False)
                return 401, {"error": "invalid"}
            acct = f"sid:{row['id']}"
            if row["revoked"]:
                self._note(ip, acct, False)
                return 401, {"error": "session_revoked"}
            if row["expires"] < now:
                return 401, {"error": "expired"}
            if not is_owner(row["email"]):
                self.revoke(row["id"], "not_owner")
                return 403, {"error": "not_owner"}
            if row["device_jwk"]:
                if not verify_proof(json.loads(row["device_jwk"]), rt, data.get("proof"), now):
                    self.revoke(row["id"], "bad_device_proof")
                    self._log("owner_bad_device_proof", {"ip": ip})
                    self._note(ip, acct, False)
                    return 401, {"error": "session_revoked"}
            new = REFRESH_PREFIX + secrets.token_urlsafe(32)
            idle = REMEMBER_IDLE if row["remember"] else SHORT_IDLE
            self._q("INSERT OR REPLACE INTO owner_refresh_old VALUES(?,?,?)", (h, row["id"], now))
            self._q("UPDATE owner_sessions SET refresh_hash=?, last_used=?, expires=? WHERE id=?",
                    (_h(new), now, min(now + idle * 1000, row["abs_expires"]), row["id"]))
            at, ttl = self._access(row["id"], now)
            self._note(ip, None, True)
            return 200, {"access_token": at, "expires_in": ttl, "refresh_token": new, "session": self._pub(self._row("id=?", (row["id"],)))}

    def logout(self, data: dict, headers: dict) -> tuple[int, dict]:
        s = self.session(headers)
        if data.get("all"):
            if not s:
                return 401, {"error": "unauthorized"}
            if data.get("confirm") is not True:
                return 400, {"error": "confirm_required"}
            ids = [r[0] for r in self._q("SELECT id FROM owner_sessions WHERE uid=? AND revoked=0", (s["uid"],))]
            n = sum(self.revoke(i, "logout_all") for i in ids)
            self._log("owner_signout_all", {"n": n})
            return 200, {"ok": True, "revoked": n}
        sid = s["id"] if s else None
        rt = str(data.get("refresh_token") or "")
        if not sid and rt.startswith(REFRESH_PREFIX):
            row = self._row("refresh_hash=?", (_h(rt),))
            sid = row["id"] if row else None
        if not sid:
            return 401, {"error": "unauthorized"}
        n = self.revoke(sid, "logout")
        self._log("owner_signout", {"ip": client_ip(headers)})
        return 200, {"ok": True, "revoked": n}

    # ------------------------------------------------------------------------------------------------ passkeys
    def _handle_of(self, uid: str) -> bytes:
        return hashlib.sha256(("cnq-owner-passkey:" + uid).encode()).digest()[:16]

    def _challenge(self, purpose: str, sid: str | None) -> bytes:
        now = self._ms()
        n = self._q("SELECT COUNT(*) FROM webauthn_challenges")[0][0]
        if n >= MAX_CHALLENGES:
            self._q("DELETE FROM webauthn_challenges WHERE expires<?", (now,))
            self._q("DELETE FROM webauthn_challenges WHERE hash IN (SELECT hash FROM webauthn_challenges ORDER BY expires LIMIT ?)",
                    (max(1, n - MAX_CHALLENGES + 1),))
        ch = secrets.token_bytes(32)
        self._q("INSERT INTO webauthn_challenges VALUES(?,?,?,?)", (hashlib.sha256(ch).hexdigest(), purpose, sid or "", now + CHALLENGE_TTL * 1000))
        return ch

    def _take_challenge(self, credential, purpose: str, sid: str | None) -> bytes | None:
        """the challenge inside the response's clientDataJSON, if WE issued it for this purpose (and session) and it is unused"""
        try:
            c = credential if isinstance(credential, dict) else json.loads(credential)
            cd = json.loads(pk.unb64u(c["response"]["clientDataJSON"]).decode("utf-8"))
            ch = pk.unb64u(cd["challenge"])
        except Exception:  # noqa: BLE001
            return None
        h = hashlib.sha256(ch).hexdigest()
        with self.lock:
            r = self._q("SELECT purpose, sid, expires FROM webauthn_challenges WHERE hash=?", (h,))
            self._q("DELETE FROM webauthn_challenges WHERE hash=?", (h,))
        if not r or r[0][0] != purpose or r[0][2] < self._ms() or (sid is not None and r[0][1] != sid):
            return None
        return ch

    def _passkeys(self, uid: str) -> list[dict]:
        cols = ("id", "name", "created", "last_used", "backed_up", "transports", "alg")
        return [dict(zip(cols, r)) | {"backed_up": bool(r[4]), "transports": json.loads(r[5] or "[]")}
                for r in self._q(f"SELECT {', '.join(cols)} FROM passkeys WHERE uid=? ORDER BY created", (uid,))]

    def passkey_register_options(self, s: dict) -> tuple[int, dict]:
        if self.now() * 1000 - (s["auth_time"] or 0) > PASSKEY_ADD_WINDOW * 1000:
            return 403, {"error": "reauth_required"}
        have = self._passkeys(s["uid"])
        if len(have) >= MAX_PASSKEYS:
            return 409, {"error": "too_many_passkeys"}
        ch = self._challenge("register", s["id"])
        return 200, {"publicKey": {
            "challenge": pk.b64u(ch), "rp": {"id": self.rp_id(), "name": RP_NAME},
            "user": {"id": pk.b64u(self._handle_of(s["uid"])), "name": s["email"], "displayName": f"{RP_NAME} · {s['email']}"},
            "pubKeyCredParams": [{"type": "public-key", "alg": a} for a in (pk.ES256, pk.EDDSA, pk.RS256)],
            "timeout": 120_000, "attestation": "none",
            "authenticatorSelection": {"residentKey": "required", "requireResidentKey": True, "userVerification": "required"},
            "excludeCredentials": [{"type": "public-key", "id": p["id"], **({"transports": p["transports"]} if p["transports"] else {})}
                                   for p in have]}}

    def passkey_register_verify(self, s: dict, data: dict, headers: dict) -> tuple[int, dict]:
        if self.now() * 1000 - (s["auth_time"] or 0) > PASSKEY_ADD_WINDOW * 1000:
            return 403, {"error": "reauth_required"}
        cred = data.get("credential")
        ch = self._take_challenge(cred, "register", s["id"])
        if ch is None:
            return 400, {"error": "bad_challenge"}
        try:
            v = pk.verify_registration(cred, ch, self.origins(), self.rp_id(), require_uv=True)
        except pk.PasskeyError as e:
            return 400, {"error": "passkey_invalid", "detail": str(e)[:80]}
        cid = pk.b64u(v["credential_id"])
        if self._q("SELECT 1 FROM passkeys WHERE id=?", (cid,)):
            return 409, {"error": "already_registered"}
        if len(self._passkeys(s["uid"])) >= MAX_PASSKEYS:
            return 409, {"error": "too_many_passkeys"}
        now = self._ms()
        name = re.sub(r"[\x00-\x1f\x7f]", "", str(data.get("name") or "")).strip()[:40] or device_label(str(headers.get("user-agent") or ""))
        self._q("INSERT INTO passkeys VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, s["uid"], s["email"], pk.b64u(self._handle_of(s["uid"])), pk.b64u(v["public_key"]), v["alg"], v["sign_count"],
                 json.dumps(v["transports"]), name, v["fmt"], v["aaguid"], int(v["backed_up"]), now, None))
        self._log("passkey_add", {"name": name, "fmt": v["fmt"], "alg": v["alg"]})
        return 200, {"ok": True, "passkey": next(p for p in self._passkeys(s["uid"]) if p["id"] == cid)}

    def passkey_login_options(self, headers: dict) -> tuple[int, dict]:
        ip = client_ip(headers)
        wait = self.throttled(ip)
        if wait:
            return self._429(wait)
        self._note(ip, None, True)          # counts toward the per-address attempt cap (options are cheap but stored)
        ch = self._challenge("login", None)
        return 200, {"publicKey": {"challenge": pk.b64u(ch), "rpId": self.rp_id(), "timeout": 120_000,
                                   "userVerification": "required", "allowCredentials": []}}

    def passkey_login_verify(self, data: dict, headers: dict) -> tuple[int, dict]:
        ip = client_ip(headers)
        cred = data.get("credential")
        try:
            cid = pk.b64u(pk.credential_id(cred))
            jwk = _jwk(data.get("device_key"))
        except (pk.PasskeyError, ValueError):
            return 400, {"error": "bad_credential"}
        acct = f"pk:{cid[:40]}"
        wait = self.throttled(ip, acct)
        if wait:
            return self._429(wait)
        ch = self._take_challenge(cred, "login", None)
        if ch is None:
            self._note(ip, acct, False)
            return 400, {"error": "bad_challenge"}
        r = self._q("SELECT uid, email, user_handle, public_key, sign_count FROM passkeys WHERE id=?", (cid,))
        if not r:
            self._note(ip, acct, False)
            return 401, {"error": "unknown_passkey"}
        uid, email, handle, pub, count = r[0]
        try:
            v = pk.verify_authentication(cred, ch, self.origins(), self.rp_id(), pk.unb64u(pub), int(count or 0), require_uv=True,
                                         user_handle=pk.unb64u(handle))
        except pk.PasskeyError as e:
            self._note(ip, acct, False)
            return 401, {"error": "passkey_invalid", "detail": str(e)[:80]}
        if not is_owner(email):
            self._note(ip, acct, False)
            return 403, {"error": "not_owner"}
        now = self._ms()
        self._q("UPDATE passkeys SET sign_count=?, last_used=? WHERE id=?", (v["new_sign_count"], now, cid))
        self._note(ip, acct, True)
        # a passkey with user verification is a full sign-in of its own (something you have + Face ID / PIN)
        return 200, self._issue(uid, email, "passkey", now, bool(data.get("remember", True)), headers, jwk)

    # ------------------------------------------------------------------------------------------------ public config
    def config(self) -> dict:
        env = os.environ
        project = (env.get("CNQ_FIREBASE_PROJECT") or "crypto-nova").strip()
        key = (env.get("CNQ_FIREBASE_WEB_API_KEY") or "").strip()
        fb = None
        if API_KEY_RE.fullmatch(key):           # public by design (it is in every web page that uses Firebase); never a secret
            fb = {"apiKey": key, "authDomain": (env.get("CNQ_FIREBASE_AUTH_DOMAIN") or f"{project}.firebaseapp.com").strip(),
                  "projectId": project}
            for k, name in (("appId", "CNQ_FIREBASE_WEB_APP_ID"), ("messagingSenderId", "CNQ_FIREBASE_SENDER_ID")):
                if (env.get(name) or "").strip():
                    fb[k] = env[name].strip()[:80]
        provs = [p.strip() for p in (env.get("CNQ_OWNER_PROVIDERS") or "password,apple.com,google.com").split(",")
                 if p.strip() in PROVIDERS]
        return {"firebase": fb, "project": project, "providers": provs, "owner_configured": bool(owner_emails()),
                "passkey": {"available": True, "rpId": self.rp_id()},
                "session": {"access_ttl_sec": ACCESS_TTL, "remember_days": REMEMBER_IDLE // 86400, "short_hours": SHORT_IDLE // 3600},
                "fresh_sign_in_sec": FRESH_SIGNIN}

    # ------------------------------------------------------------------------------------------------ router: /v2/auth/*
    def handle(self, method: str, path: str, headers: dict, body: bytes) -> tuple[int, dict]:
        if method == "GET" and path == "/v2/auth/config":
            return 200, self.config()
        if method == "GET" and path in ("/v2/auth/me", "/v2/auth/sessions", "/v2/auth/passkeys"):
            s, deny = self.require(headers)
            if deny:
                return deny
            if path == "/v2/auth/me":
                return 200, {"signedIn": True, "email": s["email"], "isOwner": True, "method": s["method"],
                             "session": self._pub(s, s["id"])}
            if path == "/v2/auth/sessions":
                cols = ", ".join(self._COLS)
                rows = [dict(zip(self._COLS, r)) for r in self._q(
                    f"SELECT {cols} FROM owner_sessions WHERE uid=? AND revoked=0 AND expires>? ORDER BY last_used DESC",
                    (s["uid"], self._ms()))]
                return 200, {"sessions": [self._pub(r, s["id"]) for r in rows]}
            return 200, {"passkeys": self._passkeys(s["uid"])}
        if method != "POST":
            return (405, {"error": "method"}) if path in ROUTES else (404, {"error": "not_found"})
        try:
            data = json.loads(body.decode() or "{}") if body else {}
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return 400, {"error": "bad_json"}
        if path == "/v2/auth/session":
            return self.signin_firebase(data, headers)
        if path == "/v2/auth/refresh":
            return self.refresh(data, headers)
        if path == "/v2/auth/logout":
            return self.logout(data, headers)
        if path == "/v2/auth/passkey/login/options":
            return self.passkey_login_options(headers)
        if path == "/v2/auth/passkey/login/verify":
            return self.passkey_login_verify(data, headers)
        if path not in OWNER_POST:
            return (405, {"error": "method"}) if path in ROUTES else (404, {"error": "not_found"})
        s, deny = self.require(headers)                    # everything below: the owner only
        if deny:
            return deny
        if path == "/v2/auth/passkey/register/options":
            return self.passkey_register_options(s)
        if path == "/v2/auth/passkey/register/verify":
            return self.passkey_register_verify(s, data, headers)
        if data.get("confirm") is not True:                  # removing access is an explicit action
            return 400, {"error": "confirm_required"}
        if path == "/v2/auth/sessions/revoke":
            sid = str(data.get("id") or "")
            if not self._q("SELECT 1 FROM owner_sessions WHERE id=? AND uid=?", (sid, s["uid"])):
                return 404, {"error": "no_such_session"}
            n = self.revoke(sid, "revoked_by_owner")
            self._log("owner_session_revoke", {"current": sid == s["id"]})
            return 200, {"ok": True, "revoked": n}
        if path == "/v2/auth/passkeys/delete":
            pid = str(data.get("id") or "")
            if not self._q("SELECT 1 FROM passkeys WHERE id=? AND uid=?", (pid, s["uid"])):
                return 404, {"error": "no_such_passkey"}
            self._q("DELETE FROM passkeys WHERE id=? AND uid=?", (pid, s["uid"]))
            self._log("passkey_remove", {})
            return 200, {"ok": True}
        return 404, {"error": "not_found"}


OWNER_POST = {"/v2/auth/passkey/register/options", "/v2/auth/passkey/register/verify", "/v2/auth/sessions/revoke",
              "/v2/auth/passkeys/delete"}
ROUTES = {"/v2/auth/config", "/v2/auth/me", "/v2/auth/sessions", "/v2/auth/passkeys", "/v2/auth/session", "/v2/auth/refresh",
          "/v2/auth/logout", "/v2/auth/passkey/login/options", "/v2/auth/passkey/login/verify"} | OWNER_POST
