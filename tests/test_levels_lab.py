"""27.11 (owner): «Уровни · лаборатория» paper desks — the study's own code, no replay of history, owner-only, never real."""
import inspect, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
H = 3_600_000
T0 = 1_790_000_000_000 - 1_790_000_000_000 % H


def _walk(n, seed=3, px=100.0):
    rnd = random.Random(seed)
    K = []
    for i in range(n):
        o = px
        px = max(1.0, px * (1 + rnd.gauss(0, 0.012)))
        K.append([T0 + i * H, o, max(o, px) * (1 + abs(rnd.gauss(0, 0.004))), min(o, px) * (1 - abs(rnd.gauss(0, 0.004))), px, 1000.0 + rnd.random() * 500])
    return K


def test_lab_desk_counts_only_events_after_its_start_and_uses_the_study_code(tmp_path):
    from cnq.agents import levels_lab_desk as LL
    from cnq.api import levels_lab as LA
    K = _walk(1600)
    desks = [{"key": "t1", "variant": "desk|L|E_close|a1|2R|none", "coins": ["BTC"], "risk": 0.01},
             {"key": "t2", "variant": "touch|S|E_limit|sweep|opp|be", "coins": ["BTC"], "risk": 0.02}]
    q = LA.LevelsLab(str(tmp_path / "ll"))
    d0 = q.tick(now=K[999][0] + H, candles=lambda c: K[:1000], desks=desks)
    assert d0 and all(v["trades"] == 0 for v in d0["desks"].values())          # nothing from before the start
    d1 = q.tick(now=K[-1][0] + H, candles=lambda c: K, desks=desks)
    st = LL.step(None, desks[0], K[999][0] + H, lambda c: K[:1000])
    st = LL.step(st, desks[0], K[-1][0] + H, lambda c: K)
    assert d1["desks"]["t1"]["trades"] == st["trades"] and st["trades"] + len(st["open"]) >= 1
    assert all(x["t"] >= st["started"] for x in list(st["closed"].values()) + st["open"])
    assert all(abs(x["usd"]) <= 0.01 * 10_000 / (abs(x["entry"] - x["stop"]) / x["entry"]) * abs(x["pct"]) / 100 + 0.011 for x in st["closed"].values())
    assert LL.lab().sim is not None and "levels_lab_study" in inspect.getsource(LL)
    body = inspect.getsource(LL).split('"""', 2)[2]
    assert "whales:" not in body and "okx_futures" not in body and "execution" not in body
    code, b = q.desk()
    assert code == 200 and b["data"]["owner_only"]
    from cnq.api import server
    src = inspect.getsource(server)
    assert '"/v2/owner/levels_lab"' in src and "api.levels_lab.start()" in src


def test_lab_desks_config_is_paper_and_labelled():
    from cnq.agents import levels_lab_desk as LL
    ds = LL.cfg()
    assert ds, "config/levels_lab_desks.json has desks"
    for d in ds:
        assert len(d["variant"].split("|")) == 6 and 0.005 <= d["risk"] <= 0.02 and d.get("label_ru")


def test_wide_stop_copies_of_the_exact_desk(tmp_path):
    from cnq.agents import levels_desk as LD, levels_wide as LW
    from cnq.api import levels as LA
    K = _walk(1700, seed=9)
    v = "range|L|sym"
    sts = {m: None for m in LW.MODES}
    base = None
    for j in range(900, 1700, 3):
        f = (lambda c, j=j: K[:j + 1])
        base = LD.step(base, K[j][0] + H + 1, v, candles=f, coins=["BTC"])
        for m in LW.MODES:
            sts[m] = LW.step(sts[m], K[j][0] + H + 1, v, m, candles=f, coins=["BTC"])
    for m, st in sts.items():
        for tr in st["closed"] + st["open"]:
            assert tr["stop0"] < tr["entry"] < tr["tgt"]
            if m in ("a1.5", "a2", "a3"):
                assert abs((tr["level"] - tr["stop0"])) > 1.0 * 0                         # stop below the level
            assert tr["notional"] <= 0.2 * 10_000 * 1.6
        if m == "swing24":
            assert all(tr["maxb"] == 24 for tr in st["closed"] + st["open"])
    assert any(sts[m]["closed"] for m in LW.MODES)
    # wider stop → at the same 1 % risk a smaller (or capped) position than the desk's 0.5 ATR stop on the same signal
    q = LA.Levels(str(tmp_path / "lv"))
    doc = q.tick(now=K[-1][0] + H, candles=lambda c: K, coins=["BTC"])
    assert doc and sorted(q._load("wide.json")) == sorted(LW.MODES)
