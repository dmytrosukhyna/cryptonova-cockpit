"""OKX affiliate API — is this OKX account one of OUR invitees, and did it deposit? (decision B, 22.10.2026)

GET /api/v5/affiliate/invitee/detail?uid=<OKX UID>, signed OKX v5 style with the affiliate's READ-ONLY key:
OK-ACCESS-SIGN = base64(HMAC-SHA256(secret, timestamp + 'GET' + requestPath)). Keys live only in /etc/cnq.env on the server:
CNQ_OKX_AFF_KEY / CNQ_OKX_AFF_SECRET / CNQ_OKX_AFF_PASSPHRASE (optional CNQ_OKX_AFF_HOST, default https://www.okx.com).
Without the key the check says «not_configured» and the bot queues the UID for the owner (24 h), like the BingX check.

Answer fields used (OKX docs, Affiliate → Get the invitee's detail): depAmt (deposits), kycTime, joinTime, accFee, volMonth.
A non-zero code means «not our invitee» or an exchange error — both stay «pending» for a manual look, never a false «ok».
Field names and error codes are from the public v5 docs and could not be tested here without a key [проверить on the first real UID]."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import urllib.request

PATH = "/api/v5/affiliate/invitee/detail"
NOT_INVITEE = {"51621", "51620"}      # «the user is not your invitee» family [проверить exact codes]


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def signed_get(path_qs: str, key: str, secret: str, passphrase: str, host: str, fetch=None) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    sig = base64.b64encode(hmac.new(secret.encode(), (ts + "GET" + path_qs).encode(), hashlib.sha256).digest()).decode()
    headers = {"OK-ACCESS-KEY": key, "OK-ACCESS-SIGN": sig, "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": passphrase,
               "Content-Type": "application/json", "User-Agent": "cryptonova-api/2"}
    if fetch:
        return fetch(host + path_qs, headers)
    req = urllib.request.Request(host + path_qs, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def check_invitee(okx_uid: str, fetch=None, env=None) -> dict:
    env = os.environ if env is None else env
    key, secret, pp = env.get("CNQ_OKX_AFF_KEY"), env.get("CNQ_OKX_AFF_SECRET"), env.get("CNQ_OKX_AFF_PASSPHRASE")
    if not (key and secret and pp) and not fetch:
        return {"ok": False, "reason": "not_configured"}
    if not str(okx_uid).isdigit():
        return {"ok": False, "reason": "bad_uid"}
    host = (env.get("CNQ_OKX_AFF_HOST") or "https://www.okx.com").rstrip("/")
    try:
        res = signed_get(f"{PATH}?uid={okx_uid}", key or "", secret or "", pp or "", host, fetch)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "exchange_error", "detail": str(e)[:200]}
    code = str(res.get("code"))
    if code in NOT_INVITEE:
        return {"ok": False, "reason": "not_invited", "detail": str(res.get("msg"))[:200]}
    if code != "0":
        return {"ok": False, "reason": "exchange_error", "detail": f"{code} {str(res.get('msg'))[:180]}"}
    rows = res.get("data") or []
    d = rows[0] if rows else {}
    if not d:
        return {"ok": False, "reason": "not_invited"}
    deposited = _f(d.get("depAmt")) > 0
    return {"ok": deposited, "reason": "ok" if deposited else "no_deposit", "invited": True, "deposited": deposited,
            "kyc": bool(d.get("kycTime")), "volume_month": _f(d.get("volMonth")), "fees": _f(d.get("accFee"))}
