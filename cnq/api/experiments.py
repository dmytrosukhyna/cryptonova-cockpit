"""Experiment program on the server (owner 22.11.2026 16:18). PAPER ONLY, owner-only.
Every 5 minutes: the experiment desks (cnq/agents/exp_desks.py) follow the whale copy stream (web/whales_live.json) in every
variant; every hour: the strategy board (cnq/agents/strategy_board.py) → <run>/data/strategy_board.json; once a day after
18:30 UTC the board is published as docs/research/strategy_board.md (GitHub contents API, the same token as web/ files) for the
21:07 Athens evening report. Owner routes: GET /v2/owner/exp_desks, GET /v2/owner/strategy_board. Nothing here places an order;
the variants have no copy-signal id, so the real account can never open their trades."""
from __future__ import annotations

import base64
import json
import os
import threading
import time

EVERY_S = 300
BOARD_EVERY_MS = 3_600_000


class Experiments:
    def __init__(self, data_dir: str, web_dir: str, env=None):
        self.root, self.web = data_dir, web_dir           # data_dir = <run>/data
        self.dir = os.path.join(data_dir, "exp_desks")
        os.makedirs(self.dir, exist_ok=True)
        self.env = os.environ if env is None else env
        self.lock = threading.Lock()
        self.last_err: str | None = None
        self.c4: tuple[int, list] = (0, [])

    def _load(self, p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save(self, p, doc) -> None:
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)

    def tick(self, now: int | None = None, get=None) -> dict | None:
        from cnq.agents import exp_desks as X, strategy_board as SB
        now = int(now or time.time() * 1000)
        with self.lock:
            try:
                desk = (self._load(os.path.join(self.web, "whales_live.json")) or {}).get("desk") or {}
                if now - self.c4[0] > 15 * 60_000:
                    try:
                        self.c4 = (now, X.candles("BTC-USDT-SWAP", "4H", 60, get))
                    except Exception:  # noqa: BLE001 — no regime this pass: the filter lets trades through
                        pass
                ctx = {"regime": X.regime(self.c4[1]), "smart": X.smart_flags(self._load(os.path.join(self.web, "smart_money.json")))}

                def atr_of(coin):
                    return X.atr(X.candles(f"{coin}-USDT-SWAP", "1H", 30, get))
                sp = os.path.join(self.dir, "state.json")
                st = self._load(sp) or {}
                X.step(st, desk, now, ctx, atr_of)
                self._save(sp, st)
                b = self._load(os.path.join(self.root, "strategy_board.json"))
                if not b or now - int(b.get("asof") or 0) >= BOARD_EVERY_MS:
                    b = SB.write(self.web, self.root, now)
                    self._publish(b, now)
                self.last_err = None
                return st
            except Exception as e:  # noqa: BLE001
                self.last_err = type(e).__name__
                return None

    def _publish(self, b: dict, now: int) -> None:
        """docs/research/strategy_board.md once a UTC day after 18:30 — the evening report reads it"""
        from cnq.agents import strategy_board as SB
        from cnq.execution import ivan_watch
        if time.gmtime(now / 1000).tm_hour * 60 + time.gmtime(now / 1000).tm_min < 18 * 60 + 30:
            return
        mark = os.path.join(self.root, "strategy_board.published")
        day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
        try:
            if open(mark, encoding="utf-8").read().strip() == day:
                return
        except OSError:
            pass
        tok, repo = self.env.get("CNQ_GH_TOKEN", ""), self.env.get("CNQ_GH_REPO") or ivan_watch.DEFAULT_REPO
        if not tok:
            return
        url = f"https://api.github.com/repos/{repo}/contents/docs/research/strategy_board.md"
        hdr = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-strategy-board"}
        if ivan_watch.put_contents(url, hdr, base64.b64encode(SB.markdown(b).encode()).decode(), f"strategy board {day}"):
            with open(mark, "w", encoding="utf-8") as f:
                f.write(day)

    def desks(self) -> tuple[int, dict]:
        from cnq.agents import exp_desks as X
        st = self._load(os.path.join(self.dir, "state.json"))
        if st is None:
            return 503, {"error": "no_data", "last_error": self.last_err}
        b = self._load(os.path.join(self.root, "strategy_board.json")) or {}
        src = next((r["d30"] for r in b.get("rows") or [] if r.get("key") == "whales"), {})
        return 200, {"asof": int(time.time() * 1000), "summary": X.summary(st, {"pf": src.get("pf"), "max_dd_pct": src.get("max_dd_pct")}),
                     "open": st.get("open") or [], "closed": (st.get("closed") or [])[-200:]}

    def board(self) -> tuple[int, dict]:
        b = self._load(os.path.join(self.root, "strategy_board.json"))
        return (200, b) if b else (503, {"error": "no_data", "last_error": self.last_err})

    def start(self) -> None:
        def loop():
            while True:
                self.tick()
                time.sleep(EVERY_S)
        threading.Thread(target=loop, daemon=True, name="experiments").start()
