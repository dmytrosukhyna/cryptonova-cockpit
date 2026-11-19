"""19.11 (owner 23:42): «Торговля от уровней · CRYPTO NOVA AI (тест)» — PAPER, owner-only (cnq/agents/levels_desk.py, cnq/api/levels.py).
Every price here is made up; no real API is called."""
import json
import math
import os
import tempfile

from cnq.agents import levels_desk as LD
from cnq.api import levels as LA
from cnq.execution import okx_futures as fu

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = LD.H
NOW = 1_795_000_000_000 - 1_795_000_000_000 % H


def P3(h, l, c, v=(100, 100, 100), a=1.0, vmean=100.0):
    return {"h": list(h), "l": list(l), "c": list(c), "v": list(v), "atr": [a] * len(c), "vmean": [vmean] * len(c), "t": [0] * len(c)}


def test_the_three_modes_fire_where_the_rules_say():
    L = [90.0, 100.0, 110.0]
    # range long: the low touches support 100 (± 0.25 ATR), the close holds above; target R = 110, stop 100 − 0.5 ATR
    s = LD.signals(P3((104, 103, 102), (101, 100.5, 99.9), (103, 102, 101)), 2, L, L, "range", "L")
    assert s and s[0]["tgt"] == 110.0 and s[0]["stop"] == 99.5 and s[0]["kind"] == "market"
    assert not LD.signals(P3((104, 103, 102), (101, 100.5, 101.5), (103, 102, 102)), 2, L, L, "range", "L")   # did not reach support
    # breakout with volume: close above 110 by > 0.1 ATR on 1.5× volume → long to the next level ≥ 1 ATR away
    L2 = [100.0, 110.0, 120.0]
    s = LD.signals(P3((109, 109.5, 111.5), (108, 108.5, 109.8), (108.5, 109.5, 111.2), v=(100, 100, 200)), 2, L2, L2, "bo_vol", "L")
    assert s and s[0]["tgt"] == 120.0 and s[0]["stop"] == 109.5
    assert not LD.signals(P3((109, 109.5, 111.5), (108, 108.5, 109.8), (108.5, 109.5, 111.2), v=(100, 100, 120)), 2, L2, L2, "bo_vol", "L")
    # crash readiness: a close below support on 2× volume → short at once
    s = LD.signals(P3((101, 100.5, 100.2), (100.2, 100.1, 98.5), (100.5, 100.3, 98.7), v=(100, 100, 250)), 2, L, L, "crash", "S")
    assert s and s[0]["stop"] == 100.5 and s[0]["tgt"] == 90.0
    # false breakout: the high pierces 110 and the bar closes back below → short to the support of the range
    s = LD.signals(P3((108, 109, 111), (107, 108, 108.5), (108.5, 109, 109.2)), 2, L, L, "fbo1", "S")
    assert s and s[0]["tgt"] == 100.0 and math.isclose(s[0]["stop"], 111.25)


def test_exits_and_the_owner_asymmetry():
    sg = {"stop": 95.0, "tgt": 110.0, "tgt2": 120.0}
    assert LD.plan(sg, "L", "sym", 100.0)["tgt"] == 110.0 and LD.plan(sg, "L", "asym", 100.0)["tgt"] == 120.0
    p = LD.plan({"stop": 105.0, "tgt": 80.0, "tgt2": 80.0}, "S", "asym", 100.0)
    assert p["tgt"] == 92.5 and p["maxb"] == 24 and p["trail"] is True            # shorts: closer target, short hold, trailing
    tr = {"side": "S", "entry": 100.0, "stop": 105.0, "tgt": 92.5, "maxb": 24, "trail": True, "risk": 5.0, "bars": 0}
    assert LD.step_trade(tr, (100, 101, 94.5, 94.8), 1.0) is None and tr["stop"] == 95.8        # after +1R: trailed 1 ATR behind
    assert LD.step_trade(tr, (95, 96, 94, 95.5), 1.0) == (95.8, "стоп")
    tr = {"side": "L", "entry": 100.0, "stop": 95.0, "tgt": 110.0, "maxb": 72, "trail": False, "risk": 5.0, "bars": 0}
    assert LD.step_trade(tr, (100, 111, 94, 100), 1.0) == (95.0, "стоп")                       # both touched → the stop
    assert round(LD.ret_pct("L", 100.0, 110.0, 16), 4) == round((0.10 - 0.001 - 0.0002) * 100, 4)


def candles_factory(path):
    """a slow sine between 95 and 105 (a range), then the last bar dips to the low edge"""
    def candles(coin):
        out, t0 = [], NOW - 700 * H
        for i in range(700):
            px = 100 + 5 * math.sin(i / 12.0)
            out.append([t0 + i * H, px, px + 0.6, px - 0.6, px + 0.1, 1000.0])
        return out
    return candles


def test_the_paper_desk_runs_owner_only_and_never_reaches_the_real_account():
    d = tempfile.mkdtemp()
    lv = LA.Levels(d)
    doc = lv.tick(now=NOW, candles=candles_factory(d), coins=["AAA", "BBB"])
    assert doc and doc["test"] is True and doc["owner_only"] is True and doc["start"] == 10000.0 and "ТЕСТ" in doc["rule_ru"]
    st = json.load(open(os.path.join(d, "state.json")))
    assert st["last_bar"]["AAA"] == NOW - H and len(st["open"]) <= LD.MAX_OPEN
    for tr in st["open"]:
        assert tr["notional"] <= 2000.0 and tr["risk_usd"] <= 100.0 + 1e-6 and tr["ctx"]["levels_below"] is not None and "atr_pct" in tr["ctx"]
    code, r = lv.desk()
    assert code == 200 and r["name"] == "levels"
    again = lv.tick(now=NOW, candles=candles_factory(d), coins=["AAA", "BBB"])        # same bar again → nothing new
    assert len(again["open"]) == len(st["open"])
    # never a copy signal: the real sleeve refuses any «levels:» / desk id
    assert not fu.copy_sig("levels:W-1") and not fu.copy_sig("levels:W-1", {})
    srv = open(os.path.join(ROOT, "cnq", "api", "server.py"), encoding="utf-8").read()
    i = srv.index("def _owner(")
    assert '"/v2/owner/levels"' in srv[i:] and srv.index("self.owner_auth.require(headers)", i) < srv.index('"/v2/owner/levels"', i)
    j = srv.index('if path == "/v2/admin/levels"')
    assert srv.index("deny = self.console.check(headers)", j) < srv.index("self.levels.desk()", j)
    assert not os.path.exists(os.path.join(ROOT, "web", "levels_desk.json"))
    assert len(LD.VARIANTS) == 21 and LD.RISK == 0.01 and LD.MAX_OPEN == 5


def test_one_position_is_capped_at_20_percent_and_the_risk_shrinks():
    st = {"cash": 10_000.0, "open": []}
    LD._open(st, "AAA", "S", "asym", {"stop": 100.5, "tgt": 99.0, "tgt2": 99.0}, 100.0, NOW, {})      # stop 0.5 % → 1 % risk wants $20 000
    tr = st["open"][0]
    assert tr["notional"] == 2000.0 and tr["risk_usd"] == 10.0 and tr["capped"] is True and tr["capped_ru"] == "размер урезан лимитом"
    LD._open(st, "BBB", "S", "asym", {"stop": 110.0, "tgt": 90.0, "tgt2": 90.0}, 100.0, NOW, {})      # stop 10 % → $1 000, not capped
    tr = st["open"][1]
    assert tr["notional"] == 1000.0 and tr["risk_usd"] == 100.0 and tr["capped"] is False and "capped_ru" not in tr
    for k in "CDEFG":
        LD._open(st, k, "S", "asym", {"stop": 100.5, "tgt": 99.0, "tgt2": 99.0}, 100.0, NOW, {})
    assert len(st["open"]) == 5 and sum(x["notional"] for x in st["open"]) <= 10_000.0
