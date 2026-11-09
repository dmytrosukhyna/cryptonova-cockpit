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
