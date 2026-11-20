"""«Взломы → шорт (тест)» on the server (owner 20.11.2026). PAPER ONLY, owner-only, TEST. Every 5 minutes the API runs
cnq/agents/hack_desk on free public sources and OKX public prices (no keys); state and file in <run>/data/hacks (never in web/);
served only to the owner: GET /v2/owner/hacks (owner session) and GET /v2/admin/hacks («Управление»). No order can be placed here."""
from __future__ import annotations

import json
import os
import threading
import time

EVERY_S = 300
PROTO_TTL_S = 6 * 3600


class Hacks:
    def __init__(self, data_dir: str):
        self.dir = data_dir
        os.makedirs(self.dir, exist_ok=True)
        self.lock = threading.Lock()
        self.last_err: str | None = None
        self._ref: dict = {}

    def _load(self, name):
        try:
            with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save(self, name, doc):
        p = os.path.join(self.dir, name)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)

    def _refs(self, now_s, hd):
        """the DefiLlama protocol index and the OKX swap list, refreshed every 6 h"""
        if not self._ref or now_s - self._ref.get("t", 0) > PROTO_TTL_S:
            try:
                self._ref = {"t": now_s, "idx": hd.protocol_index(hd._get("https://api.llama.fi/protocols")), "swaps": hd.swaps()}
            except Exception:  # noqa: BLE001
                if not self._ref:
                    raise
        return self._ref["idx"], self._ref["swaps"]

    def tick(self, now: int | None = None, sources=None, idx=None, swap_set=None, **kw) -> dict | None:
        from cnq.agents import hack_desk as HD
        now = int(now or time.time() * 1000)
        with self.lock:
            try:
                if idx is None or swap_set is None:
                    idx, swap_set = self._refs(now // 1000, HD)
                src = sources if sources is not None else HD.collect()
                st = HD.step(self._load("state.json"), now, src, idx, swap_set, **kw)
                self._save("state.json", st)
                doc = HD.public(st, now)
                self._save("hacks_desk.json", doc)
                self.last_err = None
                return doc
            except Exception as e:  # noqa: BLE001
                self.last_err = type(e).__name__
                return None

    def desk(self):
        d = self._load("hacks_desk.json")
        if d is None:
            return 503, {"error": "no_data", "detail": "the desk has not run on the server yet", "last_error": self.last_err}
        return 200, {"name": "hacks", "title": d.get("name"), "asof": d.get("asof"), "data": d}

    def start(self):
        def loop():
            while True:
                self.tick()
                time.sleep(EVERY_S)
        threading.Thread(target=loop, daemon=True, name="hacks-desk").start()
