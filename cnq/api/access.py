"""Guest access codes for cryptonova.vip, checked on the server (22.10.2026 — audit 20.10: web/access.json published unsalted
SHA-256 hashes of 40-bit codes in a public repository, cheap to brute-force offline).

Now:
  • the codes live only in the server's own database (data/api.sqlite, git-ignored, never in web/);
  • a new code is NOVA-AAAA-BBBB-CCCC (60 bits). The first group is a lookup id; the whole code is stored as salted
    PBKDF2-SHA256 (200 000 rounds, 16-byte random salt per code), compared in constant time — one PBKDF2 per check;
  • POST /v2/access/verify {code} answers {ok, level, label, until}; 10 wrong codes from one address within 10 minutes → 429;
  • the owner issues, lists and removes codes with his «Управление» token (POST/GET /v2/admin/access, cnq/api/console.py) or
    from his Mac (scripts/access_code.sh → the same API).

Legacy: the two codes published before 22.10 (the 18.10 test link and its code in web/access.json) keep working as «legacy»
rows so no guest is locked out, but their hashes are in public git history — the owner should issue new codes and remove
them (`remove "legacy"`). Owner login never depends on this module (it is the owner.enc.json passphrase).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

ITER = 200_000
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O, 1/I — easy to dictate
FAIL_MAX, FAIL_WINDOW = 10, 600
LEGACY_LABEL = "legacy"
# sha256('cnq:' + code) base64url — already public in git history (web/access.json, web/app.html TESTS_HASH); rotate them
LEGACY = [("vzWL5VbX6CBAYPtR_fTOyRiMEH1ZLrhLucxIl9vZZhA", "ссылка для тестов (18.10)", "tests", 1792322483110),
          ("ROdIZ334R2yDxsFY7tmgo85pp104R5jz__T9uHqMAW4", "ссылка ?tests= (18.10)", "tests", 1792322483110)]

SCHEMA = """CREATE TABLE IF NOT EXISTS access_codes(id TEXT, label TEXT, level TEXT, created INTEGER, until INTEGER,
            salt TEXT, hash TEXT, legacy INTEGER DEFAULT 0, PRIMARY KEY(id, hash))"""


def norm(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum() or ch == "-").strip("-")


def new_code() -> str:
    s = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"NOVA-{s[:4]}-{s[4:8]}-{s[8:]}"


def _pbkdf2(code: str, salt: bytes) -> str:
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", norm(code).encode(), salt, ITER)).decode()


def _legacy_hash(code: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(("cnq:" + code).encode()).digest()).decode().rstrip("=")


def _code_id(code: str) -> str | None:
    p = norm(code).split("-")
    return p[1] if len(p) == 4 and p[0] == "NOVA" and all(len(x) == 4 for x in p[1:]) else None


class AccessCodes:
    def __init__(self, store, legacy: list | None = None):
        self.store = store
        store._q(SCHEMA)
        self.fails: dict[str, list[float]] = {}
        self.lock = threading.Lock()
        if not store._q("SELECT 1 FROM access_codes WHERE legacy=1 OR legacy=2 LIMIT 1"):   # one-time import; 2 = imported and removed
            for h, label, level, created in (LEGACY if legacy is None else legacy):
                store._q("INSERT OR IGNORE INTO access_codes VALUES(?,?,?,?,?,?,?,1)", ("", label, level, created, None, "", h))

    # ------------------------------------------------------------ owner
    def add(self, label: str, level: str = "tests", days: int | None = None, now: int | None = None) -> str:
        now = now or int(time.time() * 1000)
        code = new_code()
        salt = secrets.token_bytes(16)
        self.store._q("INSERT INTO access_codes VALUES(?,?,?,?,?,?,?,0)",
                      (_code_id(code), str(label or "гость").strip()[:60], level if level in ("tests", "full") else "tests", now,
                       now + int(days) * 86_400_000 if days else None, base64.b64encode(salt).decode(), _pbkdf2(code, salt)))
        return code

    def remove(self, label: str) -> int:
        lab = str(label or "").strip().lower()             # Python lower(): SQLite's lower() does not fold Cyrillic
        rows = [(cid, h, leg) for cid, h, leg, lb in self.store._q("SELECT id, hash, legacy, label FROM access_codes WHERE legacy!=2")
                if (lb or "").strip().lower() == lab or (lab == LEGACY_LABEL and leg == 1)]
        for cid, h, leg in rows:
            if leg:      # keep a tombstone so the legacy import never runs again
                self.store._q("UPDATE access_codes SET legacy=2, hash='removed-' || rowid, label=label || ' (removed)' WHERE id=? AND hash=?", (cid, h))
            else:
                self.store._q("DELETE FROM access_codes WHERE id=? AND hash=?", (cid, h))
        return len(rows)

    def list(self) -> list[dict]:
        return [{"label": lab, "level": lev, "created": cr, "until": un, "legacy": bool(leg)}
                for lab, lev, cr, un, leg in self.store._q("SELECT label, level, created, until, legacy FROM access_codes WHERE legacy!=2 ORDER BY created")]

    # ------------------------------------------------------------ guests
    def throttled(self, ip: str, now: float | None = None) -> int | None:
        now = now or time.time()
        with self.lock:
            recent = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
            self.fails[ip] = recent
            return int(FAIL_WINDOW - (now - recent[0])) + 1 if len(recent) >= FAIL_MAX else None

    def verify(self, code: str, ip: str = "local", now: int | None = None) -> tuple[int, dict]:
        now = now or int(time.time() * 1000)
        wait = self.throttled(ip)
        if wait:
            return 429, {"ok": False, "error": "too_many_attempts", "retry_after_sec": wait}
        c = norm(code)
        hit = None
        cid = _code_id(c)
        if cid:
            for label, level, until, salt, h in self.store._q(
                    "SELECT label, level, until, salt, hash FROM access_codes WHERE id=? AND legacy=0", (cid,)):
                if hmac.compare_digest(_pbkdf2(c, base64.b64decode(salt)), h):
                    hit = (label, level, until)
                    break
        elif 6 <= len(c) <= 40:
            cands = {_legacy_hash(c), _legacy_hash(c.lower())}
            for label, level, until, h in self.store._q("SELECT label, level, until, hash FROM access_codes WHERE legacy=1"):
                if any(hmac.compare_digest(x, h) for x in cands):
                    hit = (label, level, until)
                    break
        if not hit:
            with self.lock:
                self.fails.setdefault(ip, []).append(time.time())
            return 200, {"ok": False, "error": "unknown_code"}
        label, level, until = hit
        if until and int(until) < now:
            return 200, {"ok": False, "error": "expired", "until": until}
        return 200, {"ok": True, "level": level or "tests", "label": label or "", "until": until}
