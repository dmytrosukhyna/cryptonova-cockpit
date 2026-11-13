"""«Альты от китов (тест)» on the server (13.11.2026, owner 22:03: paper only, owner-only). The API runs the paper desk
cnq/agents/smart_alts_desk.py every 10 minutes on <run>/web/smart_money.json + markets.json (copied from the pipeline); state and
file in <run>/data/smart_alts (never pushed, never in web/); served only to the signed-in owner: GET /v2/owner/smart_alts.
Nothing here can place an order."""
from __future__ import annotations

import json
import os
import threading
import time

EVERY_S = 600


class SmartAlts:
    def __init__(self, data_dir: str, web_dir: str):
        self.dir, self.web = data_dir, web_dir
        os.makedirs(self.dir, exist_ok=True)
        self.lock = threading.Lock()
        self.last_err: str | None = None

    def _get(self, key: str):
        try:
            with open(os.path.join(self.dir, "state.json"), encoding="utf-8") as f:
                return (json.load(f) or {}).get(key)
        except (OSError, ValueError):
            return None

    def _set(self, key: str, value) -> None:
        p = os.path.join(self.dir, "state.json")
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump({key: value}, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)

    def snap_dir(self) -> str | None:
        """the pipeline's daily watch-list snapshots (data/smart_money/watchlist) — the repo checkout first, then <run>/data"""
        for base in (os.environ.get("CNQ_REPO_DIR", "/opt/cnq/repo"), os.path.dirname(os.path.dirname(self.dir))):
            p = os.path.join(base, "data", "smart_money", "watchlist")
            if os.path.isdir(p):
                return p
        return None

    def tick(self, now: int | None = None) -> dict | None:
        from cnq.agents import smart_alts_desk
        with self.lock:
            try:
                doc = smart_alts_desk.run(self.dir, self.web, now=now, kv_get=self._get, kv_set=self._set, snap_dir=self.snap_dir())
                self.last_err = None
                return doc
            except Exception as e:  # noqa: BLE001
                self.last_err = type(e).__name__
                return None

    def desk(self) -> tuple[int, dict]:
        """GET /v2/owner/smart_alts (owner session only — checked by the router)"""
        try:
            with open(os.path.join(self.dir, "smart_alts.json"), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return 503, {"error": "no_data", "detail": "the desk has not run on the server yet", "last_error": self.last_err}
        return 200, {"name": "smart_alts", "title": d.get("name"), "asof": d.get("asof"), "data": d}

    def start(self) -> None:
        def loop():
            while True:
                self.tick()
                time.sleep(EVERY_S)
        threading.Thread(target=loop, daemon=True, name="smart-alts-desk").start()
