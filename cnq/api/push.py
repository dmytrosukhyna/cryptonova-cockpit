"""Push messages to the 2.0 app through Firebase Cloud Messaging (HTTP v1).

Key: the Firebase service-account JSON on the server (path in CNQ_FCM_SA, /etc/cnq.env). Without it nothing is sent — the
messages are only logged, so the rest of the system works the same."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class Fcm:
    def __init__(self, sa_path: str | None = None, project: str | None = None):
        self.sa_path = sa_path or os.environ.get("CNQ_FCM_SA")
        self.sa = None
        if self.sa_path and os.path.exists(self.sa_path):
            with open(self.sa_path, encoding="utf-8") as f:
                self.sa = json.load(f)
        self.project = project or (self.sa or {}).get("project_id") or os.environ.get("CNQ_FIREBASE_PROJECT", "crypto-nova")
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
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode())
        self._tok, self._exp = d["access_token"], time.time() + int(d.get("expires_in", 3600))
        return self._tok

    def send(self, token: str, title: str, body: str, data: dict | None = None) -> str:
        """→ "ok" | "gone" (the app was removed — drop the token) | "error" | "dry" (no key)"""
        if not self.ready:
            return "dry"
        msg = {"message": {"token": token, "notification": {"title": title, "body": body},
                           "data": {k: str(v) for k, v in (data or {}).items()},
                           "apns": {"payload": {"aps": {"sound": "default"}}},
                           "android": {"priority": "high", "notification": {"channel_id": "cryptonova"}}}}
        req = urllib.request.Request(f"https://fcm.googleapis.com/v1/projects/{self.project}/messages:send",
                                     data=json.dumps(msg).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15):
                return "ok"
        except urllib.error.HTTPError as e:
            return "gone" if e.code in (404, 410) else "error"
        except Exception:  # noqa: BLE001
            return "error"
