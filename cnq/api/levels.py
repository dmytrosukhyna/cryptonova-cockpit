"""«Торговля от уровней · CRYPTO NOVA AI (тест)» on the server (owner 19.11.2026 23:42). PAPER ONLY, owner-only, TEST.
Every 10 minutes the API runs cnq/agents/levels_desk.step on OKX public hourly candles (no keys), keeps its state and its file in
<run>/data/levels (never in web/, never pushed) and serves the desk only to the owner: GET /v2/owner/levels (owner session) and
GET /v2/admin/levels («Управление»: the owner session or CNQ_ADMIN_TOKEN). Nothing here can place an order, and the desk has no
copy-signal id — the real account can never open its trades."""
from __future__ import annotations

import json
import os
import threading
import time

EVERY_S = 600


class Levels:
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
        from cnq.agents import levels_desk as LD
        now = int(now or time.time() * 1000)
        cache, base = {}, kw.get("candles") or LD.hourly

        def cd(coin):                              # one read per coin per pass, shared with the wide-stop copies
            if coin not in cache:
                cache[coin] = base(coin)
            return cache[coin]
        kw = {**kw, "candles": cd}
        with self.lock:
            try:
                c = LD.cfg()
                v = c.get("variant") or "range|L|sym"
                st = LD.step(self._load("state.json"), now, v, **kw)
                self._save("state.json", st)
                doc = LD.public(st, v, now, c.get("backtest"))
                self._save("levels_desk.json", doc)
                self._wide(now, v, kw)
                self.last_err = None
                return doc
            except Exception as e:  # noqa: BLE001
                self.last_err = type(e).__name__
                return None

    def _wide(self, now, v, kw):
        """27.11 (owner 09:38): the same desk with wider stops (cnq/agents/levels_wide.py) — separate paper states, one candle read"""
        from cnq.agents import levels_wide as LW
        out = {}
        for m in LW.MODES:
            name = f"wide_{m}.json"
            st = LW.step(self._load(name), now, v, m, **kw)
            self._save(name, st)
            cl = st["closed"]
            out[m] = {"trades": len(cl), "wins": sum(1 for x in cl if x["usd"] > 0), "equity": st["equity"], "open": len(st["open"]),
                      "started": st["started"]}
        self._save("wide.json", out)
        return out

    def desk(self) -> tuple[int, dict]:
        d = self._load("levels_desk.json")
        if d is None:
            return 503, {"error": "no_data", "detail": "the desk has not run on the server yet", "last_error": self.last_err}
        return 200, {"name": "levels", "title": d.get("name"), "asof": d.get("asof"), "data": d, "wide_stops": self._load("wide.json")}

    def start(self) -> None:
        def loop():
            while True:
                self.tick()
                time.sleep(EVERY_S)
        threading.Thread(target=loop, daemon=True, name="levels-desk").start()
