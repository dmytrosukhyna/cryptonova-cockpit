"""Backlog 87 (owner's decision 05.11): the weekly auto futures share."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from cnq.execution import auto_alloc as aa, okx_alloc as al  # noqa: E402

DAY = 86_400_000
T0 = aa.WHALE_ERA_MS + DAY


def _rows(pnls, t0=T0):
    return [{"pnl_usd": p, "opened": t0 + i * 3_600_000, "closed": t0 + i * 3_600_000 + 60_000} for i, p in enumerate(pnls)]


def test_first_run_starts_clock_no_change(tmp_path):
    env = {"CNQ_AUTO_ALLOC_STATE": str(tmp_path / "a.json")}
    st = aa.update(_rows([-10] * 30), 10_000, T0 + 2 * DAY, env=env)
    assert st["futures"] == 0.25 and st["next"] == T0 + 2 * DAY + aa.WEEK_MS and not st["log"]
    assert json.load(open(env["CNQ_AUTO_ALLOC_STATE"]))["futures"] == 0.25


def test_minus_steps_down_after_a_week_floor(tmp_path):
    env = {"CNQ_AUTO_ALLOC_STATE": str(tmp_path / "a.json")}
    st = aa.update([], 10_000, T0, env=env)
    st = aa.update(_rows([-10, 5] * 12), 10_000, T0 + 8 * DAY, env=env)
    assert abs(st["futures"] - 0.15) < 1e-9 and st["log"][-1]["n"] == 24
    st["futures"], st["next"] = 0.10, 0
    st = aa.update(_rows([-10, 5] * 12), 10_000, T0 + 9 * DAY, st=st, env=env)
    assert st["futures"] == 0.10                     # floor


def test_few_trades_no_change(tmp_path):
    env = {"CNQ_AUTO_ALLOC_STATE": str(tmp_path / "a.json")}
    aa.update([], 10_000, T0, env=env)
    st = aa.update(_rows([-10] * 19), 10_000, T0 + 8 * DAY, env=env)
    assert st["futures"] == 0.25 and "мало сделок" in st["log"][-1]["why_ru"]


def test_good_steps_up_ceiling_and_dd_blocks():
    s = aa.stats(_rows([30, -10] * 12), T0 + 3 * DAY, 2500)
    assert s["pf"] == 3.0 and s["dd"] < 0.10
    assert abs(aa.decide(0.25, s)[0] - 0.35) < 1e-9 and aa.decide(0.5, s)[0] == 0.5
    bad = aa.stats(_rows([30] * 12 + [-100] * 3 + [40] * 9), T0 + 3 * DAY, 2500)   # dd 300/2500 = 12 %
    assert bad["pnl"] > 0 and aa.decide(0.25, bad)[0] == 0.25


def test_pre_whale_era_and_old_rows_ignored():
    old = _rows([-50] * 30, t0=aa.WHALE_ERA_MS - 10 * DAY)
    assert aa.stats(old, T0, 2500)["n"] == 0
    assert aa.stats(_rows([5] * 25), T0 + 40 * DAY, 2500)["n"] == 0      # out of the 30-day window


def test_apply_moves_spot_and_keeps_spot_core_min():
    w = al.shares({})
    assert aa.apply(w, None) == w
    up = aa.apply(w, 0.5)
    assert abs(up["futures"] - 0.5) < 1e-9 and abs(up["spot"] - 0.4) < 1e-9 and up["spot"] + up["core"] >= 0.3
    dn = aa.apply(w, 0.15)
    assert abs(dn["spot"] - 0.75) < 1e-9 and abs(sum(dn[k] for k in ("spot", "core", "futures", "active", "reserve")) - 1) < 1e-6


def test_shares_reads_state_env_wins(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"futures": 0.35, "next": 0}))
    w = al.shares({"CNQ_AUTO_ALLOC_STATE": str(p)})
    assert abs(w["futures"] - 0.35) < 1e-9 and abs(w["spot"] - 0.55) < 1e-9
    w2 = al.shares({"CNQ_AUTO_ALLOC_STATE": str(p), "OKX_FUT_SHARE": "0.2"})
    assert abs(w2["futures"] - 0.2) < 1e-9
    p.write_text(json.dumps({"futures": 0.9}))                          # out of range → ignored
    assert abs(al.shares({"CNQ_AUTO_ALLOC_STATE": str(p)})["futures"] - 0.25) < 1e-9
