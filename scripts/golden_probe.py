"""Golden signals: how deep is the app API's history? (19.10.2026, owner: «голден сигналс — с ними нужно хорошо поработать»).
Our table starts 18.09.2026 — is that the API's window or the channel's start? Tries the history endpoint with common paging
parameters and reports, for each, how many signals came back and their date range. Runs in GitHub Actions (research-golden.yml)."""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from cnq.agents.app_signals import API_HISTORY, APP_HEADERS  # noqa: E402


def note(title, msg):
    print(f"::notice title={title}::{str(msg).replace('%', '%25').replace(chr(10), '%0A')}", flush=True)


def get(url):
    req = urllib.request.Request(url, headers=APP_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return -1, str(e)[:120]


def summary(body):
    b = body.get("body") if isinstance(body, dict) else body
    if isinstance(b, dict):
        b = next((v for v in b.values() if isinstance(v, list)), [])
    if not isinstance(b, list):
        return {"n": 0, "shape": type(b).__name__}
    ds = [x.get("date") for x in b if isinstance(x, dict) and x.get("date")]
    ids = [int(x.get("signal_id")) for x in b if isinstance(x, dict) and str(x.get("signal_id", "")).isdigit()]
    f = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d") if ms else None  # noqa: E731
    return {"n": len(b), "from": f(min(ds)) if ds else None, "to": f(max(ds)) if ds else None,
            "id_min": min(ids) if ids else None, "id_max": max(ids) if ids else None}


out = {}
variants = ["", "?page=2", "?page=10", "?page=1&size=1000", "?limit=1000", "?size=2000", "?offset=300", "?skip=300",
            "?pageNumber=2&pageSize=200", "?from=0", "?start=0&count=2000", "?before=3300", "?last_id=3300", "?id_lt=3300"]
for v in variants:
    s, body = get(API_HISTORY + v)
    out[v or "(plain)"] = summary(body) if s == 200 else {"status": s, "err": body if isinstance(body, str) else None}
note("golden · history paging", json.dumps(out, ensure_ascii=False))
