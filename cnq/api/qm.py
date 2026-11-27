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
        from cnq.agents import levels_desk as LD
        cache, base = {}, kw.get("candles") or LD.hourly

        def cached(coin):                      # one read per coin per pass, shared by the desk and its risk copies
            if coin not in cache:
                cache[coin] = base(coin)
            return cache[coin]
        kw = {**kw, "candles": cached}
        with self.lock:
            try:
                st = QD.step(self._load("state.json"), now, **kw)
                self._save("state.json", st)
                doc = QD.public(st, now, BACKTEST)
                self._save("qm_desk.json", doc)
                self._risk_copies(now, kw)
                self.last_err = None
                return doc
            except Exception as e:  # noqa: BLE001
                self.last_err = type(e).__name__
                return None

    # 27.11 (owner 07:52): the same desk at 0.5 / 1.5 / 2 % risk (a position up to 1× the equity) and on the ladder 1 → 1.5 → 2 %
    RISKS = {"r0.5": 0.005, "r1.5": 0.015, "r2": 0.02, "ladder": None}

    def _risk_copies(self, now, kw):
        from cnq.agents import qm_desk as QD
        from cnq.execution import risk_ladder as RL
        kw2 = kw
        out = {}
        for k, r in self.RISKS.items():
            name = f"risk_{k}.json"
            st = self._load(name)
            if st is None:                     # a copy starts now — no replay of old candles counts (QD._fwd)
                st = {"start": QD.START, "cash": QD.START, "open": [], "closed": [], "pending": [], "started": now, "last_bar": {}, "seen": []}
            if r is None:
                S = st.setdefault("ladder", {})
                RL.decide(S, [(c["closed_t"], c["usd"]) for c in st["closed"] if QD._fwd(c, st)], 0.0, float(st.get("start") or QD.START), now)
                r = RL.level(S)
            st = QD.step(st, now, risk=r, pos_cap=1.0, **kw2)
            st["risk_now"] = r
            self._save(name, st)
            cl = [c for c in st["closed"] if QD._fwd(c, st)]
            out[k] = {"risk": r, "equity": st.get("equity"), "trades": len(cl), "open": len(st["open"]), "pending": len(st["pending"])}
        self._save("risk_copies.json", out)
        return out

    def desk(self) -> tuple[int, dict]:
        d = self._load("qm_desk.json")
        if d is None:
            return 503, {"error": "no_data", "detail": "the desk has not run on the server yet", "last_error": self.last_err}
        return 200, {"name": "qm", "title": d.get("name"), "asof": d.get("asof"), "data": d, "risk_copies": self._load("risk_copies.json")}

    def start(self) -> None:
        def loop():
            while True:
                self.tick()
                time.sleep(EVERY_S)
        threading.Thread(target=loop, daemon=True, name="qm-desk").start()
