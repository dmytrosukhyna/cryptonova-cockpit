"""09.11 (owner: «есть ли на споте защитные ордера от резкого падения?»): the model's non-core spot stops sit on OKX."""
import okx_fake as F
from cnq.execution import okx_autopilot as ap
from cnq.execution import okx_spot_stops as ss

NOW = F.NOW


def pf(stop_aaa=8.0, extra=()):
    pos = [{"symbol": "BTC", "bucket": "core", "weight": 0.4, "levels": {"stop": None}},
           {"symbol": "AAA", "bucket": "mid", "weight": 0.075, "entry": 10.0, "levels": {"stop": stop_aaa}},
           {"symbol": "DOGE", "bucket": "mid", "role": "small", "weight": 0.025, "entry": 0.1, "fixes": 1, "high": 0.28, "levels": {"stop": None}}]
    return {"portfolios": {"ideas": {"positions": pos + list(extra)}}}


def test_stops_placed_for_non_core_only_resynced_and_fired_coins_put_on_hold():
    fk = F.FakeOkx(balances={"USDT": 1000.0, "AAA": 50.0, "DOGE": 1000.0, "BTC": 0.01})
    cl = ap.connect(F.env(), fk, {}, F.nosleep)
    st, logs = {}, []
    r = ss.sync(cl, pf(), st, NOW, logs.append, avail={"AAA": 50.0, "DOGE": 1000.0, "BTC": 0.01})
    got = {b["instId"]: b for b in fk.algo_posts}
    assert set(got) == {"AAA-USDT", "DOGE-USDT"}                                   # BTC (core) has no stop
    assert got["AAA-USDT"]["slTriggerPx"].startswith("8") and got["AAA-USDT"]["side"] == "sell" and got["AAA-USDT"]["tdMode"] == "cash"
    assert got["AAA-USDT"]["ordType"] == "conditional" and got["AAA-USDT"]["slOrdPx"] == "-1"
    assert abs(float(got["DOGE-USDT"]["slTriggerPx"]) - 0.28 * 0.65) < 1e-6                 # trailing 35 % under the high after ×2
    assert {x["ccy"] for x in r["set"]} == {"AAA", "DOGE"} and any(e["ev"] == "spot_stop_set" for e in logs)
    n = len(fk.algo_posts)
    ss.sync(cl, pf(), st, NOW + 1, logs.append, avail={"AAA": 50.0, "DOGE": 1000.0})
    assert len(fk.algo_posts) == n                                                 # in sync → nothing re-placed
    ss.sync(cl, pf(stop_aaa=9.0), st, NOW + 2, logs.append, avail={"AAA": 50.0, "DOGE": 1000.0})
    assert fk.algo_posts[-1]["instId"] == "AAA-USDT" and fk.algo_posts[-1]["slTriggerPx"].startswith("9")   # moved stop → replaced
    # AAA's stop fires on OKX (it disappears without our cancel) → the coin is held off the rebalance's buys
    fk.algos = [a for a in fk.algos if not a["instId"].startswith("AAA")]
    r = ss.sync(cl, pf(stop_aaa=9.0), st, NOW + 3, logs.append, avail={"DOGE": 1000.0})
    assert r["fired"] == ["AAA"] and "AAA" in ss.held_off(st, NOW + 4) and any(e["ev"] == "spot_stop_fired" for e in logs)
    # the model no longer holds DOGE → its stop is cancelled
    p = pf(stop_aaa=9.0)
    p["portfolios"]["ideas"]["positions"] = [x for x in p["portfolios"]["ideas"]["positions"] if x["symbol"] != "DOGE"]
    r = ss.sync(cl, p, st, NOW + 5, logs.append, avail={})
    assert "DOGE" in r["cancelled"] and not [a for a in fk.algos if a["instId"].startswith("DOGE")]


def test_a_stop_already_above_the_price_is_not_placed():
    fk = F.FakeOkx(balances={"USDT": 1000.0, "AAA": 50.0})
    cl = ap.connect(F.env(), fk, {}, F.nosleep)
    r = ss.sync(cl, pf(stop_aaa=12.0), {}, NOW, lambda e: None, avail={"AAA": 50.0})       # AAA trades at 10
    assert not [b for b in fk.algo_posts if b["instId"] == "AAA-USDT"] and "ниже стопа" in r["skipped"][0]["why"]


def test_a_coin_traded_through_usdc_gets_its_stop_on_the_usdc_pair():
    """11.11 (MORPHO / LIT bought, no stop for 35+ min, nothing logged): a coin with no USDT pair trades on {COIN}-USDC — its stop goes there"""
    fk = F.FakeOkx(balances={"USDT": 1000.0, "AAA": 50.0}, usdc_only=("AAA",))
    cl = ap.connect(F.env(), fk, {}, F.nosleep)
    st, logs = {}, []
    r = ss.sync(cl, pf(), st, NOW, logs.append, avail={"AAA": 50.0})
    assert [b["instId"] for b in fk.algo_posts if b["instId"].startswith("AAA")] == ["AAA-USDC"]
    assert "AAA" in {x["ccy"] for x in r["set"]} and st["spot_stops"]["AAA"]["inst"] == "AAA-USDC" and "AAA" not in st["spot_stop_missing"]


def test_a_missing_stop_is_never_silent():
    """every wanted stop that is not on the exchange: a «spot_stop_skip» log line once per reason + state «spot_stop_missing» → the
    public file and the owner's health row"""
    from cnq.agents import owner_health as oh
    fk = F.FakeOkx(balances={"USDT": 1000.0})
    cl = ap.connect(F.env(), fk, {}, F.nosleep)
    st, logs = {}, []
    extra = [{"symbol": "NOPAIR", "bucket": "mid", "weight": 0.01, "entry": 1.0, "levels": {"stop": 0.8}}]
    r = ss.sync(cl, pf(extra=extra), st, NOW, logs.append, avail={"AAA": 0.0})
    why = {x["ccy"]: x["why"] for x in r["skipped"]}
    assert "нет пары" in why["NOPAIR"] and "монеты нет" in why["AAA"]
    assert st["spot_stop_missing"]["AAA"]["t"] == NOW and sum(1 for e in logs if e["ev"] == "spot_stop_skip") == 3      # AAA, DOGE, NOPAIR
    ss.sync(cl, pf(extra=extra), st, NOW + 60_000, logs.append, avail={"AAA": 0.0})
    assert sum(1 for e in logs if e["ev"] == "spot_stop_skip") == 3 and st["spot_stop_missing"]["AAA"]["t"] == NOW       # once; since kept
    ss.sync(cl, pf(), st, NOW + 120_000, logs.append, avail={"AAA": 50.0, "DOGE": 1000.0})
    assert st["spot_stop_missing"] == {}                                                                               # placed → cleared


def test_health_row_is_yellow_when_a_stop_is_missing_over_15_minutes(tmp_path):
    import json
    from cnq.agents import owner_health as oh
    now = NOW
    doc = {"asof": now - 60_000, "spot_stops": [{"sym": "VVV", "ok": True, "px": 15.18, "since": None, "why": None},
                                               {"sym": "LIT", "ok": False, "px": None, "since": now - 20 * 60_000, "why": "на OKX нет пары LIT-USDT"}]}
    (tmp_path / "public_real.json").write_text(json.dumps(doc))
    row = next(r for r in oh.build(str(tmp_path), None, now)["rows"] if r["key"] == "spot_stops")
    assert row["status"] == oh.YELLOW and "LIT — на OKX нет пары" in row["note"]
    doc["spot_stops"][1]["since"] = now - 5 * 60_000
    (tmp_path / "public_real.json").write_text(json.dumps(doc))
    row = next(r for r in oh.build(str(tmp_path), None, now)["rows"] if r["key"] == "spot_stops")
    assert row["status"] != oh.YELLOW and "VVV" in row["note"]
