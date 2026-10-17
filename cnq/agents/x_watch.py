"""X (Twitter) watch: recent posts of market-moving accounts → the news table, where the news analyst rates them like any news
(and the lab rule W can trade strong ones). Uses the official X API v2 with a bearer token in env CNQ_X_BEARER (GitHub secret).
Without the token this module does nothing — no scraping, no unofficial mirrors.
Cost (pay-per-use, 2026): ≈ $0.005 per post read; only NEW posts are requested (since_id), so ~15 accounts ≈ $20–40 a month."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request

ACCOUNTS = ["elonmusk", "realDonaldTrump", "saylor", "VitalikButerin", "cz_binance", "brian_armstrong", "DeItaone", "WatcherGuru",
            "whale_alert", "BitcoinMagazine", "federalreserve", "SECGov", "lookonchain", "EricBalchunas", "APompliano"]
API = "https://api.twitter.com/2"


def _get(url: str, token: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "cnq/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS x_meta(k TEXT PRIMARY KEY, v TEXT)")


def fetch(conn: sqlite3.Connection, token: str | None = None, accounts: list[str] | None = None, get=_get) -> list[dict]:
    """New posts since the last run as news items {id, source, title, summary, link, published, received, assets_hint}."""
    token = token or os.environ.get("CNQ_X_BEARER")
    if not token:
        return []
    _ensure(conn)
    accounts = accounts or ACCOUNTS
    ids = json.loads((conn.execute("SELECT v FROM x_meta WHERE k='ids'").fetchone() or ["{}"])[0])
    missing = [a for a in accounts if a not in ids]
    if missing:
        try:
            d = get(f"{API}/users/by?usernames={','.join(missing[:100])}", token)
            for u in d.get("data") or []:
                ids[u["username"]] = u["id"]
            conn.execute("INSERT OR REPLACE INTO x_meta VALUES('ids', ?)", (json.dumps(ids),))
        except Exception:  # noqa: BLE001
            pass
    since = json.loads((conn.execute("SELECT v FROM x_meta WHERE k='since'").fetchone() or ["{}"])[0])
    now = int(time.time() * 1000); out = []
    for name in accounts:
        uid = ids.get(name)
        if not uid:
            continue
        q = {"max_results": 10, "tweet.fields": "created_at", "exclude": "replies,retweets"}
        if since.get(name):
            q["since_id"] = since[name]
        try:
            d = get(f"{API}/users/{uid}/tweets?{urllib.parse.urlencode(q)}", token)
        except Exception:  # noqa: BLE001
            continue
        posts = d.get("data") or []
        if posts:
            since[name] = max(posts, key=lambda p: int(p["id"]))["id"]
        for p in posts:
            text = p.get("text", "")
            try:
                ts = int(time.mktime(time.strptime(p.get("created_at", "")[:19], "%Y-%m-%dT%H:%M:%S")) * 1000) - time.timezone * 1000
            except Exception:  # noqa: BLE001
                ts = now
            out.append({"id": hashlib.sha1(("x" + p["id"]).encode()).hexdigest()[:16], "source": f"x:{name}",
                        "title": f"@{name}: {text[:140]}", "summary": text[:600], "link": f"https://x.com/{name}/status/{p['id']}",
                        "published": ts, "received": now, "assets_hint": []})
    conn.execute("INSERT OR REPLACE INTO x_meta VALUES('since', ?)", (json.dumps(since),))
    conn.commit()
    return out
