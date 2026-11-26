"""«Квазимодо 4ч · лонг · 3R (тест)» on the server (owner 26.11.2026). PAPER ONLY, owner-only, TEST.
Every 10 minutes the API runs cnq/agents/qm_desk.step on OKX public hourly candles (no keys), keeps its state and its file in
<run>/data/qm (never in web/, never pushed) and serves the desk only to the owner: GET /v2/owner/qm (owner session) and
GET /v2/admin/qm («Управление»: the owner session or CNQ_ADMIN_TOKEN). Nothing here can place an order, and the desk has no
copy-signal id — the real account can never open its trades."""
from __future__ import annotations

import json
import os
import threading
import time

EVERY_S = 600
# the pre-registered study's result for this variant (docs/research/levels-qm-2026-11-26.md), shown on the owner's card
BACKTEST = {"study": "scripts/research/levels_qm_study.py", "variant": "qm|4h|L|3R", "passed": True,
            "is": {"n": 235, "mean": 0.812, "win": 0.374, "pf": 1.43}, "oos": {"n": 224, "mean": 0.751, "win": 0.397, "pf": 1.43, "t_daily": 3.01},
            "null_oos": {"mean": -0.402, "max": 0.588}, "doc": "docs/research/levels-qm-2026-11-26.md"}


class QM:
    def __init__(self, data_dir: str):
        self.dir = data_dir
        os.makedirs(self.dir, exist_ok=True)
        self.lock = threading.Lock()
        self.last_err: str | None = None

    def _load(self, name: str):
        try:
            with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save(self, name: str, doc) -> None:
        p = os.path.join(self.dir, name)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)

    def tick(self, now: int | None = None, **kw) -> dict | None:
        from cnq.agents import qm_desk as QD
        now = int(now or time.time() * 1000)
        with self.lock:
            try:
                st = QD.step(self._load("state.json"), now, **kw)
                self._save("state.json", st)
                doc = QD.public(st, now, BACKTEST)
                self._save("qm_desk.json", doc)
                self.last_err = None
                return doc
            except Exception as e:  # noqa: BLE001
                self.last_err = type(e).__name__
                return None

    def desk(self) -> tuple[int, dict]:
        d = self._load("qm_desk.json")
        if d is None:
            return 503, {"error": "no_data", "detail": "the desk has not run on the server yet", "last_error": self.last_err}
        return 200, {"name": "qm", "title": d.get("name"), "asof": d.get("asof"), "data": d}

    def start(self) -> None:
        def loop():
            while True:
                self.tick()
                time.sleep(EVERY_S)
        threading.Thread(target=loop, daemon=True, name="qm-desk").start()
