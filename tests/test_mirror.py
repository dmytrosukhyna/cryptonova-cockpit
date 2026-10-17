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


def test_mirror_is_neutral_to_a_desk_reset():
    """17.10 reset of the autopilot desks: the mirror keys only on `open` trade ids (and equity for sizing) — a reset with no
    open positions changes nothing on Bybit; a position the reset closed would be closed on Bybit too (same as any autopilot exit)."""
    os.chdir(tempfile.mkdtemp())
    now = 1_800_000_000_000
    bx = FakeBX()
    st = {"started": now - 3600_000, "trades": {}}
    before = {"open": [], "equity": 9_701.16, "closed": [{"id": "R-ETH-1", "symbol": "ETH", "side": "SHORT"}], "equity_history": [{"time": now - 9e6, "equity": 9_701.16}]}
    after = {"open": [], "equity": 10_000.0, "closed": [], "equity_history": [{"time": now, "equity": 10_000.0}],
             "reset": {"time": now, "archived_pnl_usd": -298.84, "note_ru": "Счёт обнулён 17.10 …"}, "since": now}
    assert mirror.step(bx, st, before, now) == {"opened": 0, "closed": 0, "stops": 0}
    assert mirror.step(bx, st, after, now + 60_000) == {"opened": 0, "closed": 0, "stops": 0}
    assert bx.orders == [] and st["trades"] == {}
    # had the desk held a mirrored position, the reset (closing it at the last price) is mirrored as a normal close
    t = {"id": "D-SOL-1", "symbol": "SOL", "side": "LONG", "entry": 100.0, "qty": 5.0, "qty_left": 5.0, "stop": 88.0, "stop_now": 88.0, "tps": [150.0], "opened": now}
    mirror.step(bx, st, {"open": [t], "equity": 10_000.0}, now + 120_000)
    r = mirror.step(bx, st, after, now + 180_000)
    assert r == {"opened": 0, "closed": 1, "stops": 0} and bx.orders[-1] == ("close", "SOLUSDT", 5.0)
