"""13.11 (owner, backlog 160): «Киты-20» demo desk — list by the 60-day copy test (whale_study.k20_select), paper desk
(cnq/execution/whale20.py) with the real desk's rules; the real account never reads it."""
import json

from cnq.agents import whale_study as W
from cnq.execution import whale20 as K
from test_whale_live import Feed, NOW, MIN


def prof(addr, dec=30, s=10.0, n=10, hidden=False, too_active=False):
    pos = [{"coin": None, "side": None}] if hidden else []
    return {"addr": addr, "short": addr[:6], "name": addr, "src": "okx", "decisions": dec, "too_active": too_active, "positions": pos,
            "copy": {"2": {"n": n, "sum_pct": s}}, "per_day": 2, "win": 0.6, "pf": 1.2}


def test_k20_ranks_by_copy_sum_and_drops_thin_hidden_and_negative():
    ps = [prof("a", s=5), prof("b", s=50), prof("c", dec=10, s=99), prof("d", s=-3), prof("e", s=70, hidden=True), prof("f", s=1, too_active=True)]
    r = W.k20_select(ps, None, NOW)
    assert [x["addr"] for x in r["following"]] == ["b", "a"] and r["pool"] == 2 and r["rule"] == W.K20_RULE
    many = [prof(f"t{i}", s=i + 1) for i in range(30)]
    r2 = W.k20_select(many, None, NOW)
    assert len(r2["following"]) == W.K20_N and r2["following"][0]["addr"] == "t29"


def test_k20_list_is_fixed_until_the_review():
    r = W.k20_select([prof("a", s=5)], None, NOW)
    kept = W.k20_select([prof("z", s=500)], r, NOW + W.LOCK_MS - MIN)
    assert [x["addr"] for x in kept["following"]] == ["a"]
    new = W.k20_select([prof("z", s=500)], r, NOW + W.LOCK_MS + MIN)
    assert [x["addr"] for x in new["following"]] == ["z"] and new["t"] == NOW + W.LOCK_MS + MIN


def wtop(asof=NOW):
    return {"asof": asof, "k20": {"rule": W.K20_RULE, "t": NOW, "following": [{"addr": "okx:Q", "name": "Queen", "per_day": 2, "win": 0.75, "pf": 2.5}]},
            "following": [], "desk": None}


def test_the_demo_desk_copies_and_closes_with_the_trader_in_its_own_state(tmp_path):
    f = Feed()
    env = {"CNQ_WHALES20_STATE": str(tmp_path / "k20.json")}
    kw = dict(env=env, post=f.post, okx_get=f.okx_get)
    d = K.step(wtop(), now=NOW, **kw)
    assert d["desk"]["open"] == [] and d["eligible_real"] is False and d["desk"]["live"]["polled"] == 1
    f.okx = [("ETH", "long", 2500, NOW + 30_000)]
    K.step(wtop(), now=NOW + 40_000, **kw)
    d = K.step(wtop(), now=NOW + MIN, **kw)
    assert [o["coin"] for o in d["desk"]["open"]] == ["ETH"] and d["desk"]["name_ru"].startswith("Киты-20")
    f.okx = []
    d = K.step(wtop(), now=NOW + 2 * MIN, **kw)
    assert d["desk"]["open"] == [] and d["desk"]["trades"] == 1
    assert json.loads((tmp_path / "k20.json").read_text())["desk"]["closed"][0]["coin"] == "ETH"


def test_a_stale_list_opens_nothing_and_run_writes_the_file(tmp_path, monkeypatch):
    f = Feed()
    env = {"CNQ_WHALES20_STATE": str(tmp_path / "k20.json")}
    old = wtop(asof=NOW - K.FOLLOW_STALE_MS - MIN)
    K.step(old, now=NOW, env=env, post=f.post, okx_get=f.okx_get)
    f.okx = [("ETH", "long", 2500, NOW + 1000)]
    K.step(old, now=NOW + 40_000, env=env, post=f.post, okx_get=f.okx_get)
    assert K.step(old, now=NOW + MIN, env=env, post=f.post, okx_get=f.okx_get)["desk"]["open"] == []
    (tmp_path / "whales_top.json").write_text(json.dumps({"asof": 0}))
    monkeypatch.setenv("CNQ_WHALES20_STATE", str(tmp_path / "k20b.json"))
    monkeypatch.setattr(W, "_post", lambda body: {})
    doc = K.run(str(tmp_path))
    assert json.loads((tmp_path / "whales20.json").read_text())["asof"] == doc["asof"] and doc["following"] == []
