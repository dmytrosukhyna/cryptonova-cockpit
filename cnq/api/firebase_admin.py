"""Firebase Admin calls the 2.0 API needs, with the same service-account JSON as the pushes (CNQ_FCM_SA in /etc/cnq.env).

Only one call today: delete a user's Firebase login when they delete their account in the app (Apple 5.1.1(v): an app that
lets people sign in must let them delete the account inside the app). Without the key the call is skipped ("dry") — the app
deletes the login itself on the phone as well, so the account is still gone."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class FirebaseAdmin:
    def __init__(self, sa_path: str | None = None, project: str | None = None, opener=None):
        self.sa_path = sa_path or os.environ.get("CNQ_FCM_SA")
        self.sa = None
        if self.sa_path and os.path.exists(self.sa_path):
            with open(self.sa_path, encoding="utf-8") as f:
                self.sa = json.load(f)
        self.project = project or (self.sa or {}).get("project_id") or os.environ.get("CNQ_FIREBASE_PROJECT", "crypto-nova")
        self.opener = opener or urllib.request.urlopen
        self._tok, self._exp = None, 0.0

    @property
    def ready(self) -> bool:
        return bool(self.sa)

    def _access_token(self) -> str:
        if self._tok and time.time() < self._exp - 60:
            return self._tok
        import jwt
        now = int(time.time())
        assertion = jwt.encode({"iss": self.sa["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3600},
                               self.sa["private_key"], algorithm="RS256")
        body = f"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion={assertion}".encode()
        req = urllib.request.Request(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with self.opener(req, timeout=15) as r:
            d = json.loads(r.read().decode())
        self._tok, self._exp = d["access_token"], time.time() + int(d.get("expires_in", 3600))
        return self._tok

    def delete_user(self, uid: str) -> str:
        """→ "ok" | "gone" (already deleted) | "error" | "dry" (no key on the server)"""
        if not self.ready:
            return "dry"
        try:
            req = urllib.request.Request(
                f"https://identitytoolkit.googleapis.com/v1/projects/{self.project}/accounts:delete",
                data=json.dumps({"localId": uid}).encode(), method="POST",
                headers={"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json"})
            with self.opener(req, timeout=15):
                return "ok"
        except urllib.error.HTTPError as e:
            return "gone" if e.code in (400, 404) else "error"
        except Exception:  # noqa: BLE001
            return "error"
