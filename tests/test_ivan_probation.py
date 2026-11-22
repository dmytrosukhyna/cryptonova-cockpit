"""22.11 (owner 17:55): Ivan on probation — a pause of NEW Ivan copies after 3 losing closes in a row, a 10-close PF < 0.8 or a
drawdown of his copies > 4 % of the futures budget; risk-reducing only, resume only with the owner's yes. Made-up data."""
import json
import os

from cnq.execution import ivan_probation as IP

C = {"id": "t", "since": 0, "losses_in_row": 3, "pf10_min": 0.8, "dd_pct": 0.04}


def _web(tmp, pnls, upl=0.0, other=()):
    closed = [{"inst": f"X{i}-USDT-SWAP", "closed": 1000 + i, "pnl_usd": u} for i, u in enumerate(pnls)]
    closed += [{"inst": f"W{i}-USDT-SWAP", "closed": 5000 + i, "pnl_usd": u} for i, u in enumerate(other)]
    why = [{"inst": c["inst"], "closed": c["closed"], "sig": ("ivan:W-1" if c["inst"].startswith("X") else "whales:W-2")} for c in closed]
    json.dump({"closed": closed, "futures": [{"sig": "ivan:W-9", "upnl_usd": upl}, {"sig": "whales:W-8", "upnl_usd": -500}]},
              open(os.path.join(tmp, "public_real.json"), "w"))
    json.dump({"closed": why}, open(os.path.join(tmp, "real_why.json"), "w"))
    return str(tmp)


def test_three_losses_in_a_row_pause_and_only_the_owner_resumes(tmp_path):
    web = _web(tmp_path, [5, -1, -2, -3], other=[-9, -9, -9])
    st, logs = {}, []
    why = IP.gate(st, {IP.KEY: C}, web, 1000.0, 10_000, logs.append)
    assert why == "3 убыточных закрытия подряд" and st["ivan_probation"]["paused"] and logs[0]["ev"] == "ivan_probation_pause"
    _web(tmp_path, [5, -1, -2, -3, 50, 50])                                           # he recovers — still paused
    assert IP.gate(st, {IP.KEY: C}, web, 1000.0, 20_000, logs.append) == why and len(logs) == 1
    assert IP.gate(st, {IP.KEY: {**C, "resume_after": 15_000}}, web, 1000.0, 30_000, logs.append) is None    # the owner's yes
    assert logs[-1]["ev"] == "ivan_probation_resume"


def test_pf10_and_drawdown_and_other_whales_do_not_count(tmp_path):
    assert IP.check([(i, u) for i, u in enumerate([2, -4, 2, -4, 2, -4, 2, -4, 5, -1])], 0, 10_000, C).startswith("PF последних 10")
    assert IP.check([(0, 10), (1, -20), (2, 5)], -30.0, 1000, C).startswith("просадка копий")       # 10 − 20 + 5 − 30 → dd 45 > 40
    assert IP.check([(0, 10), (1, -20), (2, 5)], -10.0, 1000, C) is None
    web = _web(tmp_path, [4, 2], upl=-5, other=[-50, -50, -50])                       # other whales' losses are not his
    assert IP.copies(web, 0) == ([(1000, 4.0), (1001, 2.0)], -5.0) and IP.gate({}, {IP.KEY: C}, web, 1000.0, 1, None) is None
    assert IP.gate({}, {}, web, 1000.0, 1, None) is None                              # no config → no guard


def test_the_live_config_and_the_futures_hook():
    import inspect
    from cnq.execution import okx_futures
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    c = json.load(open(os.path.join(root, "config", "owner_overrides.json")))[IP.KEY]
    assert c["losses_in_row"] == 3 and c["pf10_min"] == 0.8 and c["dd_pct"] == 0.04 and c["resume_after"] is None
    src = inspect.getsource(okx_futures.step)
    assert "IP.gate(" in src and "if dk in IVAN_KEYS:" in src and "continue" in src.split("IP.gate(")[1][:900]


def test_halfrisk_override_halves_risk_and_tightens_the_day_stop_only():
    import okx_fake as F
    from cnq.execution import okx_futures as fu
    e = F.env()
    assert fu.caps(e)["risk"] == 0.03 and fu.caps(e)["day_loss"] == 0.03
    json.dump({"halfrisk_1122": {"risk": 0.01, "day_loss": 0.02}, "careful_21_11": {"risk": 0.02}}, open(e["CNQ_OWNER_OVERRIDES"], "w"))
    c = fu.caps(e)
    assert c["risk"] == 0.01 and c["day_loss"] == 0.02 and c["lev"] == fu.caps(F.env())["lev"]
    json.dump({"halfrisk_1122": {"risk": 0.09, "day_loss": 0.5}}, open(e["CNQ_OWNER_OVERRIDES"], "w"))
    assert fu.caps(e)["risk"] == 0.03 and fu.caps(e)["day_loss"] == 0.03          # never looser than the hard caps
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    live = json.load(open(os.path.join(root, "config", "owner_overrides.json")))
    assert live["halfrisk_1122"]["risk"] == 0.01 and live["halfrisk_1122"]["day_loss"] == 0.02


def test_factory_real_rules_specs_size_by_risk_and_day_stop():
    from cnq.agents import exp_factory as F
    st = {"fut_budget": 1000.0}
    F.step(st, 1_795_000_000_000, {}, {}, {"BTC": 60000.0})
    F.ensure_specs(st, F.POSTMORTEM_SPECS, 1_795_000_000_000, "pm")
    desk = {"open": [{"whale": "bn:1", "coin": "UNI", "side": "SHORT", "entry": 5.0, "opened_t": 1_795_000_060_000}], "closed": []}
    F.step(st, 1_795_000_120_000, {}, {"whales": desk}, {"UNI": 5.0, "BTC": 60000.0})
    by = {p["spec"]: p for p in st["open"]}
    r2 = next(v for k, v in by.items() if "risk2" in k)
    r1 = next(v for k, v in by.items() if "risk1" in k)
    assert abs(r2["usd"] - 125.0) < 0.01 and abs(r1["usd"] - 62.5) < 0.01                   # 2 % / 1 % of $1 000 at a 16 % stop


def test_risk_ladder_steps_up_after_a_winning_run_and_down_on_errors():
    from cnq.execution import risk_ladder as RL
    D_ = RL.DAY
    S = {}
    wins = [(i * 1000, 10.0 if i % 4 else -5.0) for i in range(1, 16)]              # 15 closes, PF 6, 73 % winners, at the high
    RL.decide(S, wins, 0.0, 1000.0, 4 * D_)
    assert RL.level(S) == 0.015 and S["log"][-1]["to"] == 0.015 and RL.day_loss(0.015) == 0.02
    more = wins + [(4 * D_ + i * 1000, 10.0 if i % 4 else -5.0) for i in range(1, 16)]
    RL.decide(S, more, 0.0, 1000.0, 5 * D_)
    assert RL.level(S) == 0.015                                                       # not before 3 days since the last step up
    RL.decide(S, more, 0.0, 1000.0, 8 * D_)
    assert RL.level(S) == 0.02 and RL.day_loss(0.02) == 0.03
    RL.decide(S, more + [(9 * D_ + 1, 40.0)], 0.0, 1000.0, 9 * D_ + 2)
    assert RL.level(S) == 0.02                                                        # never above 2 %
    bad = more + [(9 * D_ + 1, 40.0)] + [(10 * D_ + i, -3.0) for i in range(3)]
    RL.decide(S, bad, 0.0, 1000.0, 10 * D_ + 10)
    assert RL.level(S) == 0.015 and "3 убыточных" in S["log"][-1]["why"]
    RL.decide(S, bad, -60.0, 1000.0, 10 * D_ + 20)                                   # a 6.9 % drawdown from the high → straight to 1 %
    assert RL.level(S) == 0.01 and "сразу" in S["log"][-1]["why"]
    bt = {m: RL.backtest([(i, 2.0 if i % 3 else -3.0) for i in range(60)], 1000, m) for m in ("fixed1", "fixed2", "ladder")}
    assert bt["fixed2"]["pnl_usd"] == 2 * bt["fixed1"]["pnl_usd"] and bt["fixed1"]["pnl_usd"] <= bt["ladder"]["pnl_usd"] <= bt["fixed2"]["pnl_usd"]


def test_the_ladder_drives_the_real_caps_within_the_accepted_bounds(tmp_path):
    import okx_fake as F
    from cnq.execution import okx_futures as fu
    e = F.env()
    e = {**e, "CNQ_WEB_DIR": str(tmp_path)}
    json.dump({"halfrisk_1122": {"risk": 0.01, "day_loss": 0.02, "ladder": {"levels": [0.01, 0.015, 0.02]}, "set": 0},
               "careful_21_11": {"risk": 0.02}}, open(e["CNQ_OWNER_OVERRIDES"], "w"))
    assert fu.caps(e)["risk"] == 0.01 and fu.caps(e)["day_loss"] == 0.02            # level 0 at the start
    closes = [{"inst": f"X{i}-USDT-SWAP", "closed": i * 1000 + 1, "pnl_usd": 10.0 if i % 4 else -5.0} for i in range(1, 16)]
    json.dump({"closed": closes, "futures": []}, open(tmp_path / "public_real.json", "w"))
    st, logs = {}, []
    cap = fu.with_risk_ladder(fu.caps(e), st, e, 1000.0, 4 * 86_400_000, logs.append)
    assert cap["risk"] == 0.015 and cap["day_loss"] == 0.02 and logs[0]["ev"] == "risk_level"
    json.dump({"halfrisk_1122": {"risk": 0.01, "day_loss": 0.02, "ladder": {"levels": [0.01, 0.05, 0.09]}, "set": 0},
               "careful_21_11": {"risk": 0.02}}, open(e["CNQ_OWNER_OVERRIDES"], "w"))
    st2 = {"risk_ladder": {"i": 2}}
    cap2 = fu.with_risk_ladder(fu.caps(e), st2, e, 1000.0, 10 * 86_400_000, None)
    assert cap2["risk"] <= 0.02                                                       # levels above 2 % are dropped
    live = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "owner_overrides.json")))
    assert live["halfrisk_1122"]["ladder"]["levels"] == [0.01, 0.015, 0.02]
