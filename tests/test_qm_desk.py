"""26.11 (owner «да»): «Квазимодо 4ч · лонг · 3R (тест)» — PAPER, owner-only (cnq/agents/qm_desk.py, cnq/api/qm.py) and the whale
filters «Кит от уровня» / «Кит + квазимодо за» (cnq/agents/exp_desks.py f_level / f_qm). Every price here is made up; no API is called."""
import inspect
import os
import random
import sys

from cnq.agents import exp_desks as X
from cnq.agents import levels_desk as LD
from cnq.agents import qm_desk as QD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = LD.H
T0 = 1_795_000_000_000 - 1_795_000_000_000 % (4 * H)


def _walk(n, seed=7, px=100.0):
    rnd = random.Random(seed)
    K = []
    for i in range(n):
        o = px
        px = max(1.0, px * (1 + rnd.gauss(0, 0.012)))
        h, l = max(o, px) * (1 + abs(rnd.gauss(0, 0.004))), min(o, px) * (1 - abs(rnd.gauss(0, 0.004)))
        K.append([T0 + i * H, o, h, l, px, 1000.0 + rnd.random() * 500])
    return K


def _P(K):
    t, o, h, l, c, v = (list(x) for x in zip(*K))
    return LD.prep(t, o, h, l, c, v)


def test_desk_setups_are_the_studys_setups():
    sys.path.insert(0, os.path.join(ROOT, "scripts", "research"))
    import levels_qm_study as S
    P = _P(_walk(3000))
    for tf in ("1h", "4h"):
        assert QD.setups(P, tf) == S.qm_setups(P, tf)
    assert QD.setups(P, "4h")


def test_a_bullish_qm_break_and_the_left_shoulder():
    P = _P(_walk(3000))
    for (i, side, lvl, stop, brk) in QD.setups(P, "4h"):
        assert P["c"][i] > brk if side == "L" else P["c"][i] < brk
        assert (stop < lvl < brk) if side == "L" else (stop > lvl > brk)


def _feed(K):
    return lambda coin: K


def test_paper_desk_runs_bar_by_bar_and_never_looks_ahead():
    K = _walk(2400, seed=11)
    st, opened = None, 0
    for j in range(900, 2400, 7):            # runs every ~7 bars over a growing window, like the 10-minute server loop
        now = K[j][0] + H
        st = QD.step(st, now, candles=_feed(K[max(0, j - 800):j + 1]), coins=["BTC"])
        opened = max(opened, len(st["open"]) + len(st["closed"]))
        for tr in st["open"] + st["closed"]:
            assert tr["side"] == "L" and tr["stop"] < tr["entry"] < tr["tgt"]
            assert abs((tr["tgt"] - tr["level"]) - 3 * (tr["level"] - tr["stop"])) < 1e-6
            assert tr["notional"] <= 0.2 * 10_000 * 1.5
        assert len(st["open"]) <= 5 and st["errors"] == []
    assert st["equity"] == round(st["cash"], 2) and st["last_bar"]["BTC"] == K[2398][0]   # the last pass: j = 2398
    assert len(st["closed"]) >= 2 and all(c["why"] in ("стоп", "цель", "время") for c in st["closed"])
    assert {c["why"] for c in st["closed"]} >= {"стоп", "цель"}


def test_level_ctx_and_the_two_whale_filters():
    K = _walk(900, seed=11)
    lv = X.level_ctx(K)
    assert lv and set(lv) >= {"near_s", "near_r", "qm_l", "qm_s", "atr", "S", "R"}
    assert X.level_ctx(K[:200]) is None
    yes = {"near_s": True, "near_r": False, "qm_l": False, "qm_s": True}
    assert X.allowed("f_level", "ETH", "LONG", {"lv": yes}) and not X.allowed("f_level", "ETH", "SHORT", {"lv": yes})
    assert X.allowed("f_qm", "ETH", "SHORT", {"lv": yes}) and not X.allowed("f_qm", "ETH", "LONG", {"lv": yes})
    assert not X.allowed("f_level", "ETH", "LONG", {}) and not X.allowed("f_qm", "ETH", "LONG", {"lv": None})   # no data → skip
    st = {"started": T0}
    desk = {"open": [{"whale": "bn:1", "coin": "ETH", "side": "LONG", "entry": 1700.0, "opened_t": T0 + 60_000, "price": 1700.0}], "closed": []}
    X.step(st, desk, T0 + 120_000, {"lvl_of": lambda c: yes})
    vs = {p["v"] for p in st["open"]}
    assert "f_level" in vs and "f_qm" not in vs and "base" in vs
    st2 = {"started": T0}
    X.step(st2, desk, T0 + 120_000, {"lvl_of": lambda c: (_ for _ in ()).throw(RuntimeError("okx down"))})
    assert {p["v"] for p in st2["open"]} >= {"base"} and not {"f_level", "f_qm"} & {p["v"] for p in st2["open"]}


def test_owner_only_routes_thread_and_board_row():
    from cnq.api import server
    from cnq.agents import strategy_board as SB
    src = inspect.getsource(server)
    assert '"/v2/owner/qm"' in src and '"/v2/admin/qm"' in src and "api.qm.start()" in src and '"qm_desk.py"' in src
    assert '"qm/state.json"' in inspect.getsource(SB)
    body = inspect.getsource(QD).split('"""', 2)[2]                          # the code, without the docstring
    assert "whales:" not in body and "ivan:" not in body and "okx_futures" not in body and "execution" not in body
    assert X.NAMES_RU["f_level"] and X.NAMES_RU["f_qm"]


def test_api_tick_keeps_state_out_of_web(tmp_path):
    from cnq.api import qm as QA
    q = QA.QM(str(tmp_path / "qm"))
    K = _walk(900, seed=5)
    doc = q.tick(now=K[-1][0] + H, candles=_feed(K), coins=["BTC"])
    assert doc and doc["test"] and doc["owner_only"] and doc["backtest"]["passed"]
    code, body = q.desk()
    assert code == 200 and body["name"] == "qm"
    assert sorted(os.listdir(tmp_path / "qm")) == ["qm_desk.json", "risk_copies.json", "risk_ladder.json", "risk_r0.5.json", "risk_r1.5.json",
                                                   "risk_r2.json", "state.json"]                       # 27.11: + the risk-sheet copies
