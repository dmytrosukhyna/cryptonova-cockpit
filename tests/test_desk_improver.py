"""27.11 (owner 09:38): the desk improver — diagnosis, symptom → fix, history test with the correction, children with lineage."""
import json, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_levels_lab import _walk, H  # noqa: E402


def test_diagnosis_finds_stop_then_target_and_maps_it_to_wider_stops():
    from cnq.agents import desk_improver as DI
    K = [[i * H, 100, 101, 99, 100, 1] for i in range(400)]
    for i in range(50, 60):
        K[i] = [i * H, 100, 100.2, 97.5, 98, 1]                 # the stop at 98 is hit …
    for i in range(60, 70):
        K[i] = [i * H, 100, 106, 99, 105, 1]                    # … then the target 105 is reached
    tr = [{"coin": "BTC", "side": "L", "t": 45 * H, "t_close": 52 * H, "entry": 100.0, "stop": 98.0, "tgt": 105.0, "pct": -2.1, "why": "стоп",
           "level": 99.0} for _ in range(6)]
    tr += [{"coin": "ETH", "side": "L", "t": 45 * H, "t_close": 80 * H, "entry": 100.0, "stop": 98.0, "tgt": 105.0, "pct": 1.0, "why": "цель"}
           for _ in range(6)]
    d = DI.diagnose({"key": "x", "trades": tr}, lambda c: K)
    assert d["n"] == 12 and d["stop_then_target"] == 6 and d["stt_share"] == 1.0 and "stop_then_target" in d["symptoms"]
    fx = DI.lab_fixes("desk|L|E_close|a0.5|opp|none", d["symptoms"], d["concentration"])
    names = [f[0] for f in fx]
    assert "стоп за свингом" in names and any("3 ATR" in n for n in names) and any("после выноса" in n for n in names)
    assert DI.z_for(10) > DI.z_for(1) > 1.9
    assert DI.factory_fixes({"src": "whales", "stop": 16}, ["stop_then_target", "flat"], None)[0][1]["stop"] == 24


def test_improver_run_end_to_end_spawns_children_with_lineage_and_writes_the_log(tmp_path):
    from cnq.api import improver as IM
    root = tmp_path / "data"
    (root / "levels").mkdir(parents=True)
    K = _walk(2600, seed=21)
    rnd = random.Random(1)
    closed = []
    for j in range(14):
        t0 = K[2200 + j * 20][0]
        e = K[2200 + j * 20][4]
        win = rnd.random() < 0.4
        closed.append({"coin": "BTC", "side": "L", "entry": e, "stop0": e * 0.99, "tgt": e * 1.03, "opened_t": t0, "closed_t": t0 + 5 * H,
                       "pct": 2.9 if win else -1.1, "usd": 1.0, "why": "цель" if win else "стоп", "level": e * 0.995})
    json.dump({"closed": closed}, open(root / "levels" / "state.json", "w"))
    pub = []
    st = IM.run(str(root), now=K[-1][0] + H, candles=lambda c: K, hist=lambda c: K, publish=lambda md, day, env, *a: pub.append(md) or True)
    assert st["desks_seen"] == 1 and "levels" in st["diagnosed"]
    log = (root / "improver" / "log.md").read_text()
    assert pub and log.startswith("# Журнал улучшателя") and "**levels**" in log
    ch = json.load(open(root / "levels_lab" / "children.json"))
    assert all(c["parent"] == "levels" and c["fix"] and c["born"] and "label_ru" in c for c in ch) and len(ch) <= 2
    st2 = IM.run(str(root), now=K[-1][0] + H + 86_400_000, candles=lambda c: K, hist=lambda c: K, publish=lambda *a: True)
    assert len(json.load(open(root / "levels_lab" / "children.json"))) <= 4                       # ≤ 2 a day, no duplicates
    from cnq.api import server
    import inspect
    assert "api.improver.start()" in inspect.getsource(server)


def test_scout_columns_promotion_demotion_and_watch_sources():
    from cnq.agents import trader_scout as TS
    now = 1_800_000_000_000
    wt = {"top": [{"addr": "bn:1", "name": "a", "eligible": True, "pf": 3.0}, {"addr": "bn:2", "eligible": True, "pf": 5.0},
                  {"addr": "bn:3", "eligible": False, "pf": 9.0}], "others": []}
    wt["follow_lock"] = {"passing": {"bn:1": [now - 4 * TS.DAY, now]}}
    A = TS.column_a(wt, {"bn:2": ["offset"]}, now, {"bn:1": now - TS.DAY})
    assert [x["addr"] for x in A] == ["bn:1"] and A[0]["stable"]
    assert not TS.column_a(wt, {}, now, {"bn:1": now - 9 * TS.DAY})[0]["stable"]          # no trade for 9 days → not stable                        # the «хедж» gate holds column A too
    state = {"B": {"bn:7": {"src": "bn"}, "bn:8": {"src": "bn"}, "bn:9": {"src": "bn"}, "0xab": {"src": "hl"}}}
    fwd = {"bn:7": {"n": 31, "pf": 1.8, "max_dd": 2.0, "gain": 10.0}, "bn:8": {"n": 25, "pf": 0.7}}
    prom, dem = TS.promote_demote(state, fwd, {"bn:9": ["carry"]}, {"bn:7": now, "bn:8": now, "0xab": now - 15 * TS.DAY}, {}, now)
    assert [p["addr"] for p in prom] == ["bn:7"] and state["B"]["bn:7"]["promoted"] == now
    assert {d["addr"] for d in dem} == {"bn:8", "bn:9", "0xab"} and set(state["B"]) == {"bn:7"}
    state["B"]["0xcd"] = {"src": "hl", "why_ru": "x"}
    w = TS.apply_watch({"ws": [], "hl": [{"addr": "0xold", "keep": True}]}, state)
    assert [x["addr"] for x in w["ws"]] == ["bn:7"] and {x["addr"] for x in w["hl"]} == {"0xold", "0xcd"}
    md = TS.log_md("2026-11-27", {"screened_ws": 284, "screened_hl": 80, "a_n": 1, "a_stable": 1, "a_names": ["a"], "b_n": 2, "added": ["0xcd"],
                                  "promoted": prom, "demoted": dem, "venues": {}, "weekly": False})
    assert md.startswith("# Журнал поиска трейдеров") and "ежедневно" in md


def test_scout_forward_from_factory_solo_specs(tmp_path):
    from cnq.api import improver as IM
    root = tmp_path / "data"
    (root / "exp_factory").mkdir(parents=True)
    json.dump({"specs": [{"id": "s1", "src": "hlw", "sub": "0xaa", "filt": []}, {"id": "s2", "src": "hlw", "filt": ["agree2"]}],
               "closed": [{"spec": "s1", "pct": 2.0, "closed_t": 1}, {"spec": "s1", "pct": -1.0, "closed_t": 2}, {"spec": "s2", "pct": 5.0}]},
              open(root / "exp_factory" / "state.json", "w"))
    fw = IM.scout_forward(str(root), 0)
    assert fw["traders"] == {"0xaa": {"n": 2, "pf": 2.0, "mean": 0.5, "gain": 1.0, "max_dd": 1.0}}
