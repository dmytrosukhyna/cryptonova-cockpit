"""21.11 (owner, explicit): the public track record of the real OKX account restarts after his deposit — behind the switch
config/owner_overrides.json «restart_21_11» {"armed": false}. Activation: the new start = the equity then, «since» = then, the deposit
is not a result, the old history is archived owner-only and leaves every public output; the label «… · с 21.11.2026»; the futures
budget counts from the new start; the model's meme sleeve (≤ 5 %) trades only while armed. Made-up data; OKX / GitHub never called."""
import json
import os
import sqlite3
import stat
import tempfile

import okx_fake as F
import test_myspot_sub as TS
from cnq.agents import spot_profiles as sp
from cnq.execution import myspot_sub as sub
from cnq.execution import okx_alloc as al
from cnq.execution import okx_restart as R
from cnq.execution import public_real as PR

NOW = F.NOW
T0 = 1_790_000_000_000


def _env(armed: bool | None, min_usd=None):
    e, fk, _log = TS._setup()
    tmp = tempfile.mkdtemp()
    ov = os.path.join(tmp, "ov.json")
    cfg = {} if armed is None else {"restart_21_11": {"armed": armed, "id": "restart2111", "label_ru": R.LABEL_RU,
                                                       **({"min_usd": min_usd} if min_usd else {})}}
    with open(ov, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return {**e, "OKX_PUBLIC_STATE": os.path.join(tmp, "pr.json"), "CNQ_OWNER_OVERRIDES": ov}, fk, ov


def _arm(ov, **kw):
    with open(ov, "w", encoding="utf-8") as f:
        json.dump({"restart_21_11": {"armed": True, "id": "restart2111", "label_ru": R.LABEL_RU, **kw}}, f)


def _run(e, fk, t, st):
    return PR.run(e, TS._fetch(fk, []), t, put=lambda d: True, state=st)


def test_disarmed_switch_changes_nothing():
    e, fk, _ov = _env(False)
    st = {}
    d = _run(e, fk, NOW + 60_000, st)
    assert d["label_ru"] is None and d["since"] == sub.start(e)["t"] and sub.start(e)["src"] != "restart"
    assert not os.path.exists(R.state_path(e)) and not os.path.exists(R.archive_path(e))
    assert R.public_start(e) is None and not sp.meme_sleeve_on(e)
    rc = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "owner_overrides.json")))
    assert isinstance(rc["restart_21_11"].get("armed"), bool) and rc["restart_21_11"]["label_ru"] == R.LABEL_RU


def test_activation_after_the_deposit_restarts_every_public_number_and_archives_the_old_history():
    e, fk, ov = _env(False)
    st = {}
    t = NOW + 60_000
    d0 = _run(e, fk, t, st)
    for i in range(1, 4):                                   # some old history: curve points since the old start
        fk.bal["USDT"] -= 50.0
        d0 = _run(e, fk, t + i * PR.STEP_MS, st)
    old_since, old_curve = d0["since"], list(st["curve"])
    assert len(old_curve) >= 3 and d0["pnl_usd"] < 0
    # armed before the money arrived: the activation waits (equity < min_usd)
    _arm(ov, min_usd=12_000)
    t1 = t + 5 * PR.STEP_MS
    d1 = _run(e, fk, t1, st)
    assert d1["since"] == old_since and d1["label_ru"] is None and not os.path.exists(R.state_path(e))
    # the owner's BTC deposit lands (a funding bill BEFORE the activation) → armed pass with the equity ≥ min_usd activates
    fk.bal["USDT"] += 3000.0
    t2 = t1 + PR.STEP_MS
    fund = [{"billId": "D1", "type": "1", "ccy": "USDT", "balChg": "3000", "ts": str(t2 - 120_000)}]
    fetch = __import__("test_public_real")._flow_fetch(fk, fund, [], candle=1.0)
    d2 = PR.run(e, fetch, t2, put=lambda d: True, state=st)
    assert d2["since"] == t2 and d2["start_usd"] == d2["equity_usd"] >= 12_000 and d2["pnl_usd"] == 0.0
    assert d2["label_ru"] == R.LABEL_RU and d2["curve"] == [[t2, 0.0]] and d2["fut_curve"] == [[t2, 0.0]]
    assert d2["start_label_ru"] == "стартовый капитал ≈ $" + f"{round(d2['start_usd'], -3):,.0f}".replace(",", " ")
    assert d2["flows"]["n"] == 0 and d2["flows"]["net_usd"] == 0.0       # the deposit is inside the start, never a result
    assert d2["closed"] == [] and d2["closed_n"] == 0 and all(a["t"] >= t2 for a in d2["actions"])
    assert all(p[0] >= t2 for p in st["curve"]) and "last" in st and st["last"]["since"] == t2
    assert not any(str(old_since) in json.dumps(v) for v in (d2["curve"], d2["fut_curve"], d2["since"]))
    # the old history: owner-only archive, mode 600, with the old curve and start
    a = json.load(open(R.archive_path(e), encoding="utf-8"))
    assert a["curve"][:len(old_curve)] == old_curve and a["old_start"]["t"] == old_since and a["last_public"]["since"] == old_since
    assert stat.S_IMODE(os.stat(R.archive_path(e)).st_mode) == 0o600 and stat.S_IMODE(os.stat(R.state_path(e)).st_mode) == 0o600
    assert not str(R.archive_path(e)).startswith(os.path.abspath("web"))
    # the new start is the start everywhere (the futures budget's own capital too); a second armed pass does not move it
    s0 = sub.start(e)
    assert {k: s0[k] for k in ("usd", "t", "src")} == {"usd": d2["start_usd"], "t": t2, "src": "restart"} and s0["label_ru"] == R.LABEL_RU
    fk.bal["USDT"] += 100.0                                 # a real gain after the restart
    d3 = _run(e, fk, t2 + PR.STEP_MS, st)
    assert d3["since"] == t2 and abs(d3["pnl_usd"] - 100.0) < 0.02 and d3["curve"][0] == [t2, 0.0] and d3["label_ru"] == R.LABEL_RU
    assert PR.clean({**d3, "label_ru": "x\n" * 50})["label_ru"] is None
    # disarmed again: the old start comes back for the numbers (the archive keeps the old curve)
    with open(ov, "w", encoding="utf-8") as f:
        json.dump({"restart_21_11": {"armed": False}}, f)
    assert sub.start(e)["t"] == old_since and R.label(e) is None


def test_futures_positions_open_at_the_restart_count_only_their_pl_after_it():
    e, fk, ov = _env(True, min_usd=1000)
    st = {}
    d = _run(e, fk, NOW + 60_000, st)
    assert d["since"] == NOW + 60_000
    rec = R.load(e)
    assert "upl0" in rec and d["fut_pnl_usd"] is not None and abs(d["fut_pnl_usd"]) < 0.02


def test_meme_sleeve_only_liquid_okx_memes_after_a_dip_capped_and_only_while_armed():
    rows = [{"symbol": "BTC", "name": "Bitcoin", "kind": "core", "status": "buy", "status_ru": "можно брать", "why_ru": "ядро", "price": 80000}]
    memes = {"PEPE": {"symbol": "PEPE", "status": "small", "score": 82, "vol24": 3.2e8, "mcap": 1.4e9, "price": 3.2e-6,
                      "why_ru": "просадка −42% от максимума за 30 дней, проверки пройдены"},
             "DOGE": {"symbol": "DOGE", "status": "small", "score": 87, "vol24": 7.5e8, "mcap": 8.1e9, "price": 0.05},
             "BONK": {"symbol": "BONK", "status": "small", "score": 59, "vol24": 3.5e7, "mcap": 1.75e8, "price": 2e-6},
             "FLOKI": {"symbol": "FLOKI", "status": "small", "score": 75, "vol24": 1.3e7, "mcap": 1.5e8, "price": 1.5e-5},   # thin
             "TRUMP": {"symbol": "TRUMP", "status": "avoid", "score": 31, "vol24": 1.3e8, "mcap": 2.3e8, "price": 0.8,
                       "why_ru": "красный флаг: в обращении только 28% токенов"},
             "NOOKX": {"symbol": "NOOKX", "status": "small", "score": 90, "vol24": 9e8, "mcap": 9e9, "price": 1.0}}       # not on OKX
    okx = {"BTC", "ETH", "SOL", "PEPE", "DOGE", "BONK", "FLOKI", "TRUMP"}
    c = {x["symbol"]: x for x in sp.meme_candidates(memes, okx)}
    assert {s for s, x in c.items() if x["ok"]} == {"PEPE", "DOGE", "BONK"}
    assert not c["FLOKI"]["ok"] and not c["TRUMP"]["ok"] and "нет спот-пары на OKX" in c["NOOKX"]["why_ru"]
    src = {"cg": {"BTC": 80000}, "meme": {"PEPE": 3.2e-6, "DOGE": 0.05, "BONK": 2e-6}, "hl": {}}
    sup = {"sma200": {}, "walls": {}, "btc": {"price": 80000, "ll20": 70000, "mid": "LONG"}}
    ideas = {"asof": T0, "available": True, "rows": rows}
    off = sp.IdeasPortfolio(sqlite3.connect(":memory:")).step(src, ideas, T0, tradeable=okx, support=sup, memes=memes, meme_on=False)
    assert not [p for p in off["positions"] if p["bucket"] == "msleeve"] and off["meme_sleeve"]["on"] is False
    conn = sqlite3.connect(":memory:")
    on = sp.IdeasPortfolio(conn).step(src, ideas, T0, tradeable=okx, support=sup, memes=memes, meme_on=True)
    mm = [p for p in on["positions"] if p["bucket"] == "msleeve"]
    assert [p["symbol"] for p in mm] == ["DOGE", "PEPE"]                  # best quality first, at most 2
    assert sum(p["weight"] for p in mm) <= sp.MEME["max"] + 1e-6 and on["meme_sleeve"]["now_pct"] <= 5.0
    for p in mm:                                                          # the exchange stop follows the model's stop (−30 %)
        assert abs(p["levels"]["stop"] - p["entry"] * 0.70) < 1e-9 * p["entry"] + 1e-12 and p["levels"]["take_half"] == p["entry"] * 2
        assert p["target_weight"] == sp.MEME["w"] and p["horizon"] == "short"
    # «no chase»: BTC breaking down → no meme bought
    brk = {**sup, "btc": {"price": 65000, "ll20": 70000, "mid": "SHORT"}}
    st = sp.IdeasPortfolio(sqlite3.connect(":memory:")).step(src, ideas, T0, tradeable=okx, support=brk, memes=memes, meme_on=True)
    assert not [p for p in st["positions"] if p["bucket"] == "msleeve"]
    # the stop −30 % sells; a red flag sells at once
    p2 = {**src, "meme": {"PEPE": 3.2e-6 * 0.69, "DOGE": 0.05, "BONK": 2e-6}}
    nxt = sp.IdeasPortfolio(conn).step(p2, ideas, T0 + 3_600_000, tradeable=okx, support=sup, memes=memes, meme_on=True)
    assert any(a["symbol"] == "PEPE" and a["action"] == "SELL" and "стоп" in a["reason"] for a in nxt["actions"])
    red = {**memes, "DOGE": {**memes["DOGE"], "status": "avoid", "why_ru": "красный флаг: могут допечатать токены"}}
    nx2 = sp.IdeasPortfolio(conn).step(p2, ideas, T0 + 7_200_000, tradeable=okx, support=sup, memes=red, meme_on=True)
    assert any(a["symbol"] == "DOGE" and a["action"] == "SELL" and "красный флаг" in a["reason"] for a in nx2["actions"])


def test_account_side_caps_memes_at_5_percent():
    pos = {"BTC": {"w": 0.5, "bucket": "core"}, "PEPE": {"w": 0.05, "bucket": "meme"}, "DOGE": {"w": 0.05, "bucket": "meme"}}
    t = al.targets({"spot": 1.0}, pos)
    assert abs(t["PEPE"] + t["DOGE"] - al.MEME_ACCOUNT_MAX) < 1e-9 and t["BTC"] == 0.5
    t2 = al.targets({"spot": 0.75}, {"PEPE": {"w": 0.025, "bucket": "meme"}})
    assert abs(t2["PEPE"] - 0.01875) < 1e-12


def _ov(e, doc):
    with open(e["CNQ_OWNER_OVERRIDES"], "w", encoding="utf-8") as f:
        json.dump(doc, f)


def test_deposit_coins_kept_with_stops_sold_slowly_or_held():
    from cnq.execution import okx_autopilot as ap
    import test_okx_autopilot as TA
    e = F.env()
    fk = TA.live_fake(demo_key=True)
    fk.bal["DOGE"] = 5000.0
    sells = lambda b: [o for o in b["sells"] if o["ccy"] == "DOGE"]       # noqa: E731
    b0 = ap.build(ap.connect(e, fk, {}, F.nosleep), e, F.portfolio())
    assert sells(b0) and sum(o["usd"] for o in sells(b0)) > 100           # without a rule: sold at once (not in the model)
    _ov(e, {"restart_21_11": {"armed": False, "deposit": {"sell": {"DOGE": 50}}}})
    b1 = ap.build(ap.connect(e, fk, {}, F.nosleep), e, F.portfolio())
    (o,) = sells(b1)
    assert o["usd"] <= 61 and any("частями" in n for n in b1["notes"])     # a part per pass, a limit order
    _ov(e, {"restart_21_11": {"armed": False, "deposit": {"keep": {"DOGE": 0.08}, "hold": ["XYZ"]}}})
    b2 = ap.build(ap.connect(e, fk, {}, F.nosleep), e, F.portfolio())
    assert not sells(b2) and {"DOGE", "XYZ"} <= set(b2["keep"])
    assert R.keep_stops(e) == {"DOGE": 0.08} and R.hold_coins(e) == {"DOGE", "XYZ"}
    from cnq.execution import okx_spot_stops as ss
    import inspect
    assert "extra" in inspect.signature(ss.sync).parameters and "okx_restart.keep_stops(env)" in inspect.getsource(ap.spot_stops) and "extra=xs" in inspect.getsource(ap.spot_stops)


def test_usdt_buffer_holds_buys_while_btc_is_not_bull():
    from cnq.execution import okx_autopilot as ap
    from cnq.execution import okx_futures as fu
    import test_okx_autopilot as TA
    e = F.env()
    fk = TA.live_fake(demo_key=True)
    b0 = ap.build(ap.connect(e, fk, {}, F.nosleep), e, F.portfolio())
    assert b0["buys"]
    fk.bal["DOGE"] = 40000.0                                                    # a big kept coin: the floor counts the whole account
    _ov(e, {"restart_21_11": {"armed": False, "usdt_min_pct": 0.45, "deposit": {"keep": {"DOGE": 0.01}}}})
    old = fu.btc_bull
    try:
        fu.btc_bull = lambda path=None: False
        b1 = ap.build(ap.connect(e, fk, {}, F.nosleep), e, F.portfolio())
        assert len(b1["buys"]) < len(b0["buys"]) and any("USDT держим" in d for d in b1["diffs"])
        fu.btc_bull = lambda path=None: True
        b2 = ap.build(ap.connect(e, fk, {}, F.nosleep), e, F.portfolio())
        assert len(b2["buys"]) > len(b1["buys"]) and not any("USDT держим" in d for d in b2["diffs"])                              # BTC in its bull regime: no buffer rule
    finally:
        fu.btc_bull = old


def test_careful_override_lowers_risk_and_caps_net_long():
    from cnq.execution import okx_futures as fu
    e = F.env()
    assert fu.caps(e)["risk"] == 0.03
    _ov(e, {"careful_21_11": {"risk": 0.02, "long_cap_x": 1.0}})
    assert fu.caps(e)["risk"] == 0.02
    _ov(e, {"careful_21_11": {"risk": 0.05}})                                   # never looser than the hard cap
    assert fu.caps(e)["risk"] == 0.03
    _ov(e, {"careful_21_11": {"risk": 0.02, "long_cap_x": 1.0}})
    pos = {"A": {"long": True, "notional": 700.0}, "B": {"long": False, "notional": 200.0}}
    assert fu.long_room(e, 1000.0, pos, False) == 500.0 and fu.long_room(e, 1000.0, pos, None) == 500.0
    assert fu.long_room(e, 1000.0, pos, True) is None
    import inspect
    assert "лимит лонгов" in inspect.getsource(fu.decide)
    cfgd = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "owner_overrides.json")))
    assert cfgd["careful_21_11"]["risk"] <= 0.02 and cfgd["careful_21_11"]["long_cap_x"] <= 1.0


def test_closed_total_after_the_restart_counts_only_swaps_closed_since_the_start(tmp_path):
    """21.11 fix: the feed showed 1 closed swap (SOL −$2.09) but «итог закрытых +$63.36 / +4.11 %» — fut_pnl_usd − open P/L also
    held the restart's own correction (upl0) of the positions opened before it. The closed total is now OKX's realized P/L of the
    swaps closed since «since» only (public_real fut_realized_usd), in public_real and app_home."""
    import shutil
    import test_public_real as TP
    from cnq.agents import app_home
    since = 1795240455789
    s = {"total_usd": 10300.0, "start": {"usd": 10276.07, "t": since, "src": "restart", "label_ru": R.LABEL_RU, "upl0": -65.45},
         "futures": {"positions": [{"side": "LONG", "upl": -50.0, "mark": 1.0, "entry": 1.1}]}, "spot": {}, "cash": {"usd": 1000.0}}
    st = {}
    rows = [{"inst": "SOL-USDT-SWAP", "side": "long", "pnl_usd": -2.09, "opened": since - 8 * 3_600_000, "closed": since + 1_600_000}]
    d = PR.build(s, st, since + 2_000_000, 1, -2.09, rows, [])
    assert d["fut_realized_usd"] == -2.09 and abs(d["fut_pnl_usd"] - (-2.09 - 50.0 + 65.45)) < 0.011
    doc = PR.clean(d)
    assert doc["fut_realized_usd"] == -2.09
    real = json.loads(json.dumps(doc))
    real["closed"] = [{**r, "open_px": 68.88, "close_px": 65.91, "move_pct": -4.31, "lev": 4.0, "partial": False} for r in rows]
    real["alloc"] = {**real["alloc"], "spot_coins_usd": 9000.0, "spot_cash_usd": 100.0, "fut_budget_usd": 1100.0, "fut_margin_usd": 90.0,
                     "fut_reserve_usd": 1010.0, "active_coins_usd": 0.0, "active_wait_usd": 0.0, "other_usd": 0.0, "spot_pct": 75.0,
                     "fut_pct": 15.0, "active_pct": 0.0}
    real["spot"] = [{"sym": "BTC", "share": 87.4, "usd": 9000.0}]
    _pp = TP._pp_files
    _pp(str(tmp_path), {**real, "asof": since + 2_000_000})
    out = app_home.run(sqlite3.connect(":memory:"), str(tmp_path), now=since + 2_100_000)
    stt = out["public_portfolio"]["futures"]["stats"]
    fb = out["public_portfolio"]["futures"]["start_usd"]
    assert stt["n"] == 1 and stt["realized_usd"] == -2.09 and stt["losses"] == 1
    assert stt["pct_base_usd"] == fb and abs(fb - 0.15 * 10276.07) < 1.0              # the futures part's base, not the account
    assert stt["realized_pct_start"] == round(-2.09 / fb * 100, 2) == -0.14
    shutil.rmtree(str(tmp_path), ignore_errors=True)


def test_core_is_never_sold_for_memes_and_meme_stops_follow_the_model():
    """21.11 07:06 UTC: the model paid PEPE / SHIB by trimming its core, the account sold BTC $88 while the meme buys waited behind
    the USDT buffer. Now (1) the meme sleeve is paid only from cash above the floor, (2) the rebalancer never trims the core in the
    careful regime, (3) a held meme-sleeve coin gets its −30 % exchange stop from the model's levels."""
    from cnq.execution import okx_autopilot as ap
    from cnq.execution import okx_futures as fu
    from cnq.execution import okx_spot_stops as ss
    import test_okx_autopilot as TA
    memes = {"PEPE": {"symbol": "PEPE", "status": "small", "score": 82, "vol24": 3.2e8, "mcap": 1.4e9, "price": 3.2e-6}}
    rows = [{"symbol": "BTC", "name": "Bitcoin", "kind": "core", "status": "buy", "status_ru": "можно брать", "why_ru": "ядро", "price": 80000}]
    src = {"cg": {"BTC": 80000}, "meme": {"PEPE": 3.2e-6}, "hl": {}}
    sup = {"sma200": {}, "walls": {}, "btc": {"price": 80000, "ll20": 70000, "mid": "LONG"}}
    ideas = {"asof": T0, "available": True, "rows": rows}
    port = sp.IdeasPortfolio(sqlite3.connect(":memory:"))
    port.step(src, ideas, T0, tradeable={"BTC", "PEPE"}, support=sup, memes=memes, meme_on=False)
    mv = port.value({"BTC": 80000}) - port.rp.cash()
    port.rp._set_cash(0.10 / 0.90 * mv + 0.001)                             # cash just above the 10 % floor: no room for a 2.5 % meme
    st = port.step(src, ideas, T0 + 60_000, tradeable={"BTC", "PEPE"}, support=sup, memes=memes, meme_on=True)
    assert not [p for p in st["positions"] if p["bucket"] == "msleeve"]
    assert not [a for a in st["actions"] if a["action"] == "TRIM" and "мем" in a["reason"].lower()]
    assert "ядро ради мемов не продаём" in " ".join(st["meme_sleeve"]["candidates"][0]["why_ru"])
    # (2) the rebalancer: BTC over its target → a trim normally; in the careful regime the core stays
    e = F.env()
    fk = TA.live_fake(demo_key=True)
    fk.bal["BTC"] = 0.2
    trims = lambda b: [o for o in b["sells"] if o["ccy"] == "BTC" and o["act"] == "trim"]    # noqa: E731
    assert trims(ap.build(ap.connect(e, fk, {}, F.nosleep), e, F.portfolio()))
    _ov(e, {"restart_21_11": {"armed": False, "usdt_min_pct": 0.15}})
    old = fu.btc_bull
    try:
        fu.btc_bull = lambda path=None: False
        b = ap.build(ap.connect(e, fk, {}, F.nosleep), e, F.portfolio())
        assert not trims(b) and any("ядро не урезаю" in d for d in b["diffs"])
    finally:
        fu.btc_bull = old
    # (3) the meme sleeve's stop goes to the exchange like every non-core coin
    pf = {"portfolios": {"ideas": {"positions": [{"symbol": "PEPE", "bucket": "msleeve", "levels": {"stop": 2.33e-06}},
                                                 {"symbol": "BTC", "bucket": "core", "levels": {"stop": None}}]}}}
    assert ss.wanted(pf) == {"PEPE": 2.33e-06}
