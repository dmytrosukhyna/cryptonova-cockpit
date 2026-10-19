"""BingX partner (agent) API — is this BingX account one of OUR referrals, and did it deposit?

GET /openApi/agent/v1/account/inviteRelationCheck?uid=<BingX UID>, signed with the partner key (read-only, from the BingX
partner cabinet; env CNQ_BINGX_AGENT_KEY / CNQ_BINGX_AGENT_SECRET in /etc/cnq.env). Answer fields used: inviteResult (invited by
us), deposit (has deposited), kycResult. VIP = invited by us AND deposited. Without the key the check says «not configured»."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

HOST = "https://open-api.bingx.com"
PATH = "/openApi/agent/v1/account/inviteRelationCheck"


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    return str(v).strip().lower() in ("true", "1", "yes")


def signed_get(path: str, params: dict, key: str, secret: str, fetch=None) -> dict:
    q = {**params, "timestamp": int(time.time() * 1000)}
    qs = urllib.parse.urlencode(sorted(q.items()))
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{HOST}{path}?{qs}&signature={sig}"
    if fetch:
        return fetch(url)
    req = urllib.request.Request(url, headers={"X-BX-APIKEY": key, "User-Agent": "cryptonova-api/2"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def check_referral(bingx_uid: str, fetch=None) -> dict:
    key, secret = os.environ.get("CNQ_BINGX_AGENT_KEY"), os.environ.get("CNQ_BINGX_AGENT_SECRET")
    if not (key and secret) and not fetch:
        return {"ok": False, "reason": "not_configured"}
    if not str(bingx_uid).isdigit():
        return {"ok": False, "reason": "bad_uid"}
    try:
        res = signed_get(PATH, {"uid": int(bingx_uid)}, key or "", secret or "", fetch)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "exchange_error", "detail": str(e)[:200]}
    if res.get("code") not in (0, "0"):
        return {"ok": False, "reason": "exchange_error", "detail": str(res.get("msg"))[:200]}
    d = res.get("data") or {}
    invited = _truthy(d.get("inviteResult", d.get("invite_result")))
    deposited = _truthy(d.get("deposit"))
    reason = "ok" if invited and deposited else ("not_invited" if not invited else "no_deposit")
    return {"ok": invited and deposited, "reason": reason, "invited": invited, "deposited": deposited,
            "kyc": _truthy(d.get("kycResult", d.get("kyc_result")))}
