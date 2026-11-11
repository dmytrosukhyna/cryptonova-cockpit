"""«Разворот (тест)» on the server (11.11.2026, owner: copying / whales are internal, never public). The paper desk
cnq/agents/reversal_desk.py wrote web/reversal_desk.json from the GitHub pipeline — web/ is published. Now the API runs it itself
every 10 minutes: it reads <run>/web/whales_live.json (the server's own live copy desk) and OKX public prices, keeps its state and
its file in <run>/data/reversal (never pushed, never in web/) and serves the desk only to a signed-in owner:
GET /v2/owner/reversal. Paper only — nothing here can place an order."""
from __future__ import annotations

import json
import os
import threading
import time

EVERY_S = 600


class _NoDb:
    def commit(self) -> None:
        pass


class Reversal:
    def __init__(self, data_dir: str, web_dir: str):
        self.dir, self.web = data_dir, web_dir
        os.makedirs(self.dir, exist_ok=True)
        self.lock = threading.Lock()
        self.last_err: str | None = None

    def _state(self, _conn, key: str):
        try:
            with open(os.path.join(self.dir, "state.json"), encoding="utf-8") as f:
                return (json.load(f) or {}).get(key)
        except (OSError, ValueError):
            return None

    def _set(self, _conn, key: str, value) -> None:
        p = os.path.join(self.dir, "state.json")
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump({key: value}, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)

    def tick(self, now: int | None = None, **kw) -> dict | None:
        from cnq.agents import reversal_desk
        with self.lock:
            try:
                doc = reversal_desk.run(_NoDb(), self.dir, now=now, kv_get=self._state, kv_set=self._set, src=self.web, **kw)
                self.last_err = None
                return doc
            except Exception as e:  # noqa: BLE001
                self.last_err = type(e).__name__
                return None

    def desk(self) -> tuple[int, dict]:
        """GET /v2/owner/reversal (owner session only — checked by the router)"""
        try:
            with open(os.path.join(self.dir, "reversal_desk.json"), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return 503, {"error": "no_data", "detail": "the desk has not run on the server yet", "last_error": self.last_err}
        return 200, {"name": "reversal", "title": d.get("name"), "asof": d.get("asof"), "data": d}

    def start(self) -> None:
        def loop():
            while True:
                self.tick()
                time.sleep(EVERY_S)
        threading.Thread(target=loop, daemon=True, name="reversal-desk").start()
