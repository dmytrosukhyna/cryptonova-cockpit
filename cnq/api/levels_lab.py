"""«Уровни · лаборатория» on the server (owner 27.11.2026). PAPER ONLY, owner-only, TEST. Every 10 minutes the API runs
cnq/agents/levels_lab_desk.step for every desk of config/levels_lab_desks.json on OKX public candles (no keys), keeps the state in
<run>/data/levels_lab (never in web/, never pushed) and serves it only to the owner: GET /v2/owner/levels_lab, GET /v2/admin/levels_lab.
Nothing here can place an order; the desks have no copy-signal id."""
from __future__ import annotations

import json
import os
import threading
import time

EVERY_S = 600


class LevelsLab:
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

    def tick(self, now: int | None = None, candles=None, c15=None, desks=None) -> dict | None:
        from cnq.agents import levels_desk as LD, levels_lab_desk as LL
        now = int(now or time.time() * 1000)
        cache: dict = {}

        def cd(coin):
            if ("h", coin) not in cache:
                cache[("h", coin)] = (candles or (lambda c_: LD.hourly(c_, pages=12)))(coin)
            return cache[("h", coin)]

        def q15(coin):
            if ("q", coin) not in cache:
                if c15:
                    cache[("q", coin)] = c15(coin)
                else:
                    rows, after = {}, None
                    for _ in range(4):
                        d = LD._http("/api/v5/market/history-candles", instId=f"{coin}-USDT-SWAP", bar="15m", limit=100,
                                     **({"after": after} if after else {}))
                        if not d:
                            break
                        for r in d:
                            rows[int(r[0])] = [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])]
                        after = d[-1][0]
                    cache[("q", coin)] = [rows[k] for k in sorted(rows)]
            return cache[("q", coin)]
        with self.lock:
            try:
                out = {}
                ch = [c for c in (self._load("children.json") or []) if isinstance(c, dict) and c.get("alive", True) and c.get("variant")]
                for d in (desks if desks is not None else LL.cfg() + ch):     # 27.11: + the improver's children (lineage)
                    name = f"desk_{d['key']}.json"
                    st = LL.step(self._load(name), d, now, cd, q15)
                    self._save(name, st)
                    out[d["key"]] = {"name_ru": d.get("name_ru"), "label_ru": d.get("label_ru"), "variant": d["variant"], "risk": d.get("risk"),
                                     "coins": d.get("coins"), "started": st["started"], "parent": d.get("parent"), "fix": d.get("fix"), "equity": st["equity"], "trades": st["trades"],
                                     "wins": st["wins"], "open": len(st["open"]), "errors": st["errors"], "backtest": d.get("backtest")}
                doc = {"asof": now, "desks": out, "test": True, "owner_only": True}
                self._save("levels_lab.json", doc)
                self.last_err = None
                return doc
            except Exception as e:  # noqa: BLE001
                self.last_err = type(e).__name__
                return None

    def desk(self) -> tuple[int, dict]:
        d = self._load("levels_lab.json")
        if d is None:
            return 503, {"error": "no_data", "detail": "the lab desks have not run on the server yet", "last_error": self.last_err}
        return 200, {"name": "levels_lab", "asof": d.get("asof"), "data": d}

    def start(self) -> None:
        def loop():
            while True:
                self.tick()
                time.sleep(EVERY_S)
        threading.Thread(target=loop, daemon=True, name="levels-lab").start()
