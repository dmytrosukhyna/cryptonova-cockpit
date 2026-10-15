"""Bybit mirror: opens fresh autopilot positions once, closes what the autopilot closed, never chases old positions."""
import os, tempfile
from cnq.execution import mirror


class FakeBX:
    mode = "BYBIT-DEMO"
    def __init__(self): self.pos = {}; self.orders = []
    def positions(self): return dict(self.pos)
    def execute_signal(self, sig):
        self.orders.append(("open", sig["symbol"], sig["qty"])); self.pos[sig["symbol"]] = {"side": "Buy" if sig["side"] == "LONG" else "Sell", "size": str(sig["qty"])}
        return {"mode": self.mode}
    def place(self, symbol, side, otype, qty, reduce_only=False, **k):
        self.orders.append(("close", symbol, qty)); self.pos.pop(symbol, None)
    def set_stop(self, symbol, stop): self.orders.append(("stop", symbol, stop))


def test_mirror_open_close():
    os.chdir(tempfile.mkdtemp())
    now = 1_800_000_000_000
    st = {"started": now - 60_000, "trades": {}}
    t_new = {"id": "F-SOL-1", "symbol": "SOL", "side": "LONG", "entry": 100.0, "qty": 15.0, "qty_left": 15.0, "stop": 88.0, "stop_now": 88.0, "tps": [150.0], "opened": now - 120_000}
    t_old = dict(t_new, id="E-ETH-0", symbol="ETH", opened=now - 5 * 3600_000)
    bx = FakeBX()
    r = mirror.step(bx, st, {"open": [t_new, t_old], "equity": 10_000}, now)
    assert r["opened"] == 1 and bx.orders == [("open", "SOLUSDT", 15.0)]          # old ETH position is not chased
    assert mirror.step(bx, st, {"open": [t_new], "equity": 10_000}, now + 60_000)["opened"] == 0   # no duplicates
    r = mirror.step(bx, st, {"open": [], "equity": 10_000}, now + 120_000)
    assert r["closed"] == 1 and bx.orders[-1][0] == "close" and not st["trades"]
