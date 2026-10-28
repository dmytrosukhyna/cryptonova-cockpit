"""SQLite state of the 2.0 app: users (Firebase uid), piggy bank settings, notification prefs, AI-agent usage, VIP checks; the
owner's admin console (cnq/api/console.py): CRM notes and tags per user, an audit log of admin actions, the growth plan.
One file on the server (data/api.sqlite, never in git). Nothing secret is stored in clear text here: exchange keys (2.1) go
through cnq.api.vault (encrypted with a server key from /etc/cnq.env)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(uid TEXT PRIMARY KEY, email TEXT, name TEXT, created INTEGER, seen INTEGER,
    tier TEXT DEFAULT 'free', vip_since INTEGER, bingx_uid TEXT, lang TEXT);
CREATE TABLE IF NOT EXISTS prefs(uid TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS piggy(uid TEXT PRIMARY KEY, data TEXT, updated INTEGER);
CREATE TABLE IF NOT EXISTS push(uid TEXT, token TEXT, platform TEXT, updated INTEGER, PRIMARY KEY(uid, token));
CREATE TABLE IF NOT EXISTS usage(uid TEXT, day TEXT, n INTEGER, PRIMARY KEY(uid, day));
CREATE TABLE IF NOT EXISTS vip_checks(uid TEXT, bingx_uid TEXT, t INTEGER, ok INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS progress(uid TEXT PRIMARY KEY, data TEXT, updated INTEGER);
CREATE TABLE IF NOT EXISTS crm(uid TEXT, t INTEGER, note TEXT, tags TEXT);
CREATE INDEX IF NOT EXISTS crm_uid ON crm(uid, t);
CREATE TABLE IF NOT EXISTS admin_log(t INTEGER, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS admin_kv(k TEXT PRIMARY KEY, v TEXT, t INTEGER);
"""

# 28.10 (owner's spec §14: no notifications without consent): every switch is OFF until the person turns it on in the app.
# A saved row is the person's own choice and is kept as saved (rows from before 28.10 hold the whole screen they saved);
# from now on a row holds only the switches the person actually sent, so a default stays a default.
DEFAULT_PREFS = {"trades": False, "news": False, "piggy": False, "brief": False}


class Store:
    def __init__(self, path: str = "data/api.sqlite"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def _q(self, sql: str, args: tuple = ()) -> list:
        with self.lock:
            cur = self.conn.execute(sql, args)
            rows = cur.fetchall()
            self.conn.commit()
            return rows

    def upsert_user(self, uid: str, email: str | None, name: str | None, lang: str | None = None) -> dict:
        now = int(time.time() * 1000)
        self._q("INSERT INTO users(uid, email, name, created, seen, lang) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET email=COALESCE(excluded.email, email), name=COALESCE(excluded.name, name), "
                "seen=excluded.seen, lang=COALESCE(excluded.lang, lang)", (uid, email, name, now, now, lang))
        return self.user(uid)

    def user(self, uid: str) -> dict:
        r = self._q("SELECT uid, email, name, created, seen, tier, vip_since, bingx_uid, lang FROM users WHERE uid=?", (uid,))
        if not r:
            return {}
        keys = ("uid", "email", "name", "created", "seen", "tier", "vip_since", "bingx_uid", "lang")
        return dict(zip(keys, r[0]))

    def set_vip(self, uid: str, bingx_uid: str, ok: bool, detail: dict) -> None:
        now = int(time.time() * 1000)
        self._q("INSERT INTO vip_checks VALUES(?,?,?,?,?)", (uid, bingx_uid, now, int(ok), json.dumps(detail, ensure_ascii=False)))
        if ok:
            self._q("UPDATE users SET tier='vip', vip_since=COALESCE(vip_since, ?), bingx_uid=? WHERE uid=?", (now, bingx_uid, uid))
        else:
            self._q("UPDATE users SET bingx_uid=? WHERE uid=?", (bingx_uid, uid))

    def bingx_owner(self, bingx_uid: str) -> str | None:
        r = self._q("SELECT uid FROM users WHERE bingx_uid=? AND tier='vip'", (bingx_uid,))
        return r[0][0] if r else None

    def _doc(self, table: str, uid: str, default):
        r = self._q(f"SELECT data FROM {table} WHERE uid=?", (uid,))
        return json.loads(r[0][0]) if r else default

    def _put(self, table: str, uid: str, data) -> None:
        if table == "prefs":
            self._q("INSERT OR REPLACE INTO prefs VALUES(?,?)", (uid, json.dumps(data, ensure_ascii=False)))
        else:
            self._q(f"INSERT OR REPLACE INTO {table} VALUES(?,?,?)", (uid, json.dumps(data, ensure_ascii=False), int(time.time() * 1000)))

    def _own_prefs(self, uid: str) -> dict:
        """only the switches this person saved (no defaults mixed in)"""
        d = self._doc("prefs", uid, {})
        return {k: bool(v) for k, v in d.items() if k in DEFAULT_PREFS} if isinstance(d, dict) else {}

    def prefs(self, uid: str) -> dict:
        return {**DEFAULT_PREFS, **self._own_prefs(uid)}

    def set_prefs(self, uid: str, data: dict) -> dict:
        own = {**self._own_prefs(uid), **{k: bool(v) for k, v in (data or {}).items() if k in DEFAULT_PREFS}}
        self._put("prefs", uid, own)
        return {**DEFAULT_PREFS, **own}

    def piggy(self, uid: str) -> dict | None:
        return self._doc("piggy", uid, None)

    def set_piggy(self, uid: str, data: dict) -> dict:
        self._put("piggy", uid, data)
        return data

    def progress(self, uid: str) -> dict:
        return self._doc("progress", uid, {})

    def set_progress(self, uid: str, data: dict) -> dict:
        self._put("progress", uid, data)
        return data

    def add_push(self, uid: str, token: str, platform: str) -> None:
        self._q("INSERT OR REPLACE INTO push VALUES(?,?,?,?)", (uid, token, platform, int(time.time() * 1000)))

    def push_tokens(self, uids: list[str] | None = None, pref: str | None = None) -> list[tuple[str, str]]:
        rows = self._q("SELECT uid, token FROM push")
        out = []
        for uid, tok in rows:
            if uids is not None and uid not in uids:
                continue
            if pref and not self.prefs(uid).get(pref, False):
                continue
            out.append((uid, tok))
        return out

    def drop_push(self, token: str) -> None:
        self._q("DELETE FROM push WHERE token=?", (token,))

    def delete_user(self, uid: str) -> int:
        """Account deletion from the app: every row of this user goes, nothing is kept (GDPR erasure, Apple 5.1.1(v))."""
        n = 0
        for table in ("users", "prefs", "piggy", "push", "usage", "vip_checks", "progress", "crm"):
            with self.lock:
                n += self.conn.execute(f"DELETE FROM {table} WHERE uid=?", (uid,)).rowcount
                self.conn.commit()
        return n

    def used_today(self, uid: str, day: str) -> int:
        r = self._q("SELECT n FROM usage WHERE uid=? AND day=?", (uid, day))
        return int(r[0][0]) if r else 0

    def use(self, uid: str, day: str) -> int:
        self._q("INSERT INTO usage VALUES(?,?,1) ON CONFLICT(uid, day) DO UPDATE SET n=n+1", (uid, day))
        return self.used_today(uid, day)
