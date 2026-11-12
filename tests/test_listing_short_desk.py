"""12.11 (owner, PAPER ONLY): «Шорт листингов (тест)» — a new OKX perpetual shorted on paper after its first day."""
import json
import os

from cnq.agents import listing_short_desk as L
from cnq.execution import okx_futures as fu

H, DAY = L.H, L.DAY
T0 = 1_800_000_000_000 // H * H
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class World:
    def __init__(self):
        self.list = {}
        self.bars = {}
        self.px = {}

    def kw(self):
        return dict(lists=lambda: dict(self.list),
                    get_bars=lambda inst, t0, n: [b for b in self.bars.get(inst, []) if b[0] >= t0][:n],
                    px_of=lambda inst: self.px.get(inst), fund=lambda inst, a, b: 0.01)


def _bars(t0, n, f):
    return [(t0 + i * H, f(i), f(i) * 1.01, f(i) * 0.99, f(i)) for i in range(n)]


def test_listed_before_the_desk_started_is_ignored_and_a_new_one_is_shorted_after_day_one():
    w = World()
    w.list["OLD-USDT-SWAP"] = T0 - DAY
    d = L.step(None, T0, **w.kw())
    assert d["watch"] == {}
    w.list["XTEN-USDT-SWAP"] = T0 + H
    w.bars["XTEN-USDT-SWAP"] = _bars(T0 + H, 24 * 20, lambda i: 1.0)
    w.px["XTEN-USDT-SWAP"] = 0.80
    d = L.step(d, T0 + 2 * H, **w.kw())
    assert "XTEN-USDT-SWAP" in d["watch"] and d["open"] == []                      # waiting for the end of day one
    d = L.step(d, T0 + H + 24 * H + 600_000, **w.kw())
    o = d["open"][0]
    assert o["coin"] == "XTEN" and o["side"] == "SHORT" and o["entry"] == 0.80 and o["notional"] == 1000.0 and abs(o["stop"] - 1.08) < 1e-9
    w.px["XTEN-USDT-SWAP"] = 0.60
    d = L.step(d, o["opened_t"] + 14 * DAY + H, **w.kw())                            # time exit after 14 days, +funding 1 %
    c = d["closed"][0]
    assert "по времени" in c["why"] and abs(c["pnl_pct"] - ((1 - 0.60 / 0.80) - 0.002 + 0.01) * 100) < 1e-6 and d["equity"] > 10000


def test_stop_fills_at_the_gap_open_and_a_late_entry_is_skipped():
    w = World()
    d = L.step(None, T0, **w.kw())
    w.list["AAA-USDT-SWAP"] = T0 + H
    w.px["AAA-USDT-SWAP"] = 1.0
    w.bars["AAA-USDT-SWAP"] = _bars(T0 + H, 24, lambda i: 1.0)
    d = L.step(d, T0 + 25 * H + 60_000, **w.kw())
    t_in = d["open"][0]["opened_t"]
    w.bars["AAA-USDT-SWAP"] += [(t_in + 3 * H, 1.6, 1.7, 1.5, 1.6)]              # opens far above the +35 % stop
    d = L.step(d, t_in + 4 * H, **w.kw())
    c = d["closed"][0]
    assert c["exit"] == 1.6 and c["pnl_pct"] < -55                                 # −60 % + 1 % funding: worse than −35 %, the gap
    w.list["BBB-USDT-SWAP"] = t_in
    w.bars["BBB-USDT-SWAP"] = _bars(t_in, 30, lambda i: 2.0)
    w.px["BBB-USDT-SWAP"] = 2.0
    d = L.step(d, t_in + 28 * H, **w.kw())                                         # we look 3 h after day one → skipped
    assert d["watch"]["BBB-USDT-SWAP"]["state"] == "missed" and not d["open"]


def test_the_desk_is_owner_only_and_never_reaches_the_real_account(tmp_path):
    st = {}
    doc = L.run(str(tmp_path), now=T0, kv_get=st.get, kv_set=st.__setitem__, lists=lambda: {}, get_bars=lambda *a: [],
                px_of=lambda i: None, fund=lambda *a: 0.0)
    assert doc["test"] and doc["owner_only"] and doc["real_account"] is False and doc["watch_ru"]["XTEN"] == "ждём листинга на OKX"
    assert (tmp_path / "listing_short.json").exists() and not fu.copy_sig("listing:XTEN", {})
    srv = open(os.path.join(ROOT, "cnq", "api", "server.py"), encoding="utf-8").read()
    assert 'path == "/v2/owner/listing_short"' in srv
    assert not os.path.exists(os.path.join(ROOT, "web", "listing_short.json"))
