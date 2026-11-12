"""«Шорт листингов (тест)» on the server (12.11.2026, owner: paper only, owner-only). The API runs the paper desk
cnq/agents/listing_short_desk.py every 10 minutes on OKX public data; state and file in <run>/data/listing_short (never pushed,
never in web/); served only to the signed-in owner: GET /v2/owner/listing_short. Nothing here can place an order."""
from __future__ import annotations

import json
import os
import threading
import time

EVERY_S = 600


class ListingShort:
    def __init__(self, data_dir: str):
        self.dir = data_dir
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

    def tick(self, now: int | None = None, **kw) -> dict | None:
        from cnq.agents import listing_short_desk
        with self.lock:
            try:
                doc = listing_short_desk.run(self.dir, now=now, kv_get=self._get, kv_set=self._set, **kw)
                self.last_err = None
                return doc
            except Exception as e:  # noqa: BLE001
                self.last_err = type(e).__name__
                return None

    def desk(self) -> tuple[int, dict]:
        """GET /v2/owner/listing_short (owner session only — checked by the router)"""
        try:
            with open(os.path.join(self.dir, "listing_short.json"), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return 503, {"error": "no_data", "detail": "the desk has not run on the server yet", "last_error": self.last_err}
        return 200, {"name": "listing_short", "title": d.get("name"), "asof": d.get("asof"), "data": d}

    def start(self) -> None:
        def loop():
            while True:
                self.tick()
                time.sleep(EVERY_S)
        threading.Thread(target=loop, daemon=True, name="listing-short-desk").start()
