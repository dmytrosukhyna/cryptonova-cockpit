"""Who is calling: the app sends the Firebase ID token it already has (Google on Android, Apple on iOS) as
«Authorization: Bearer <token>». We check Google's signature, the project (crypto-nova), expiry and issuer; the user id is
the Firebase uid — the same id the 1.x app already sends to the old backend as google_uuid."""
from __future__ import annotations

import json
import re
import time
import urllib.request

CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"


class AuthError(Exception):
    pass


class FirebaseVerifier:
    def __init__(self, project_id: str = "crypto-nova", keys: dict | None = None):
        self.project = project_id
        self._keys = keys            # kid → public key (tests inject this)
        self._exp = float("inf") if keys else 0.0

    def _load_keys(self) -> dict:
        if self._keys is not None and time.time() < self._exp:
            return self._keys
        from cryptography.x509 import load_pem_x509_certificate
        with urllib.request.urlopen(CERTS_URL, timeout=10) as r:
            certs = json.loads(r.read().decode())
            m = re.search(r"max-age=(\d+)", r.headers.get("Cache-Control", ""))
        self._keys = {kid: load_pem_x509_certificate(pem.encode()).public_key() for kid, pem in certs.items()}
        self._exp = time.time() + (int(m.group(1)) if m else 3600)
        return self._keys

    def verify(self, token: str) -> dict:
        import jwt
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as e:
            raise AuthError("bad token") from e
        keys = self._load_keys()
        if kid not in keys:
            self._exp = 0.0
            keys = self._load_keys()
        if kid not in keys:
            raise AuthError("unknown key")
        try:
            claims = jwt.decode(token, keys[kid], algorithms=["RS256"], audience=self.project,
                                issuer=f"https://securetoken.google.com/{self.project}", leeway=60)
        except jwt.PyJWTError as e:
            raise AuthError(str(e)) from e
        if not claims.get("sub"):
            raise AuthError("no uid")
        # 28.10 (owner sign-in, cnq/api/owner_auth.py): also whether Firebase verified the email, when the person actually signed
        # in (auth_time — an ID token is refreshed silently for weeks, auth_time stays) and with what (password / google.com /
        # apple.com). The app's own calls only read uid, email and name.
        fb = claims.get("firebase") if isinstance(claims.get("firebase"), dict) else {}
        return {"uid": claims["sub"], "email": claims.get("email"), "name": claims.get("name"),
                "email_verified": claims.get("email_verified") is True, "auth_time": claims.get("auth_time"),
                "provider": fb.get("sign_in_provider")}
