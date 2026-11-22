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
