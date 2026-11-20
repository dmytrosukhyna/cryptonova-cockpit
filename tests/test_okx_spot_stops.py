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
    """the public file carries no reason; the owner's health row names the coin, and the server adds the reason from its own state"""
    import json
    from cnq.agents import owner_health as oh
    now = NOW
    doc = {"asof": now - 60_000, "spot_stops": [{"sym": "VVV", "ok": True, "px": 15.18, "since": None},
                                               {"sym": "LIT", "ok": False, "px": None, "since": now - 20 * 60_000}]}
    (tmp_path / "public_real.json").write_text(json.dumps(doc))
    h = oh.build(str(tmp_path), None, now)
    row = next(r for r in h["rows"] if r["key"] == "spot_stops")
    assert row["status"] == oh.YELLOW and "LIT" in row["note"] and "OKX" not in row["note"]
    state = {"spot_stop_missing": {"LIT": {"t": now - 20 * 60_000, "why": "стоп не поставился: OKX /api/v5/trade/order-algo: 1 [sCode 51008] balance"}}}
    row = next(r for r in oh.with_stop_reasons(h, state)["rows"] if r["key"] == "spot_stops")
    assert "LIT — стоп не поставился" in row["note"] and "51008" in row["note"] and row["reasons"][0]["sym"] == "LIT"
    assert oh.with_stop_reasons(h, {}) is h
    doc["spot_stops"][1]["since"] = now - 5 * 60_000
    (tmp_path / "public_real.json").write_text(json.dumps(doc))
    row = next(r for r in oh.build(str(tmp_path), None, now)["rows"] if r["key"] == "spot_stops")
    assert row["status"] != oh.YELLOW and "VVV" in row["note"]


def test_the_public_stop_summary_has_no_reason_text():
    from cnq.execution import public_real as PR
    st = {"spot_stops": {"VVV": {"px": 15.18}}, "spot_stop_missing": {"LIT": {"t": 5, "why": "raw OKX error"}}}
    import cnq.execution.okx_autopilot as AP
    orig = AP.load_state
    AP.load_state = lambda env=None: st
    try:
        out = PR.spot_stops()
    finally:
        AP.load_state = orig
    assert out == [{"sym": "VVV", "ok": True, "px": 15.18, "since": None}, {"sym": "LIT", "ok": False, "px": None, "since": 5, "code": None}]
    import json
    assert "raw OKX" not in json.dumps(out) and "why" not in json.dumps(out)


def _lead(fk):
    out = {}
    for x in fk.lead_subs:
        a = out.setdefault(x["instId"].split("-")[0], {"qty": 0.0, "avail": 0.0, "insts": [x["instId"]], "subs": []})
        a["qty"] += x["subPos"]; a["avail"] += x["subPos"]
        a["subs"].append((x["subPosId"], x["instId"], x["subPos"]))
    return out


def test_lead_sub_positions_keep_their_stops_through_new_tranches_and_a_moved_stop():
    """11.11: a coin held as spot LEAD sub-positions gets its stop on each of them. 16.11 (LIT / MORPHO lost their stops after the
    2nd tranche): a new buy = a new sub-position and a moved stop; OKX refuses a second stop on a sub-position that has one — the
    old one is cancelled and set again, each sub-position on its own, and the coin never ends up «no stop»"""
    fk = F.FakeOkx(balances={"USDT": 1000.0, "AAA": 30.0}, lead_subs=[{"subPosId": "sp1", "instId": "AAA-USDT", "subPos": 30.0, "openAvgPx": 10.0}])
    cl = ap.connect(F.env(), fk, {}, F.nosleep)
    st, logs = {}, []
    r = ss.sync(cl, pf(), st, NOW, logs.append, avail={"AAA": 30.0}, lead=_lead(fk))
    assert [(b["subPosId"], b["instType"]) for k, b in fk.lead_calls if k == "spot_stop"] == [("sp1", "SPOT")]
    assert not [b for b in fk.algo_posts if b["instId"].startswith("AAA")]           # no ordinary order on a balance that is not available
    assert st["spot_stops"]["AAA"]["lead"] == {"sp1": 8.0} and "AAA" not in st["spot_stop_missing"]
    n = len(fk.lead_calls)
    ss.sync(cl, pf(), st, NOW + 1, logs.append, avail={"AAA": 30.0}, lead=_lead(fk))
    assert len(fk.lead_calls) == n                                                   # in sync → nothing re-sent
    # tranche 2: a new sub-position, and the averaged entry moves the model's stop 8.0 → 7.5
    fk.lead_subs.append({"subPosId": "sp2", "instId": "AAA-USDT", "subPos": 20.0, "openAvgPx": 9.0})
    ss.sync(cl, pf(stop_aaa=7.5), st, NOW + 2, logs.append, avail={"AAA": 50.0}, lead=_lead(fk))
    assert {x["subPosId"]: float(x["sl"]) for x in fk.lead_subs} == {"sp1": 7.5, "sp2": 7.5}
    assert any(c.get("algoId") == "LSsp1" for c in fk.cancels)                       # the old stop was cancelled first
    assert st["spot_stops"]["AAA"]["lead"] == {"sp1": 7.5, "sp2": 7.5} and "AAA" not in st["spot_stop_missing"]
    # a state that forgot everything (the old bug's situation): nothing is re-sent, the stops OKX has are recognised
    n = len(fk.lead_calls)
    st2 = {}
    ss.sync(cl, pf(stop_aaa=7.5), st2, NOW + 3, logs.append, avail={"AAA": 50.0}, lead=_lead(fk))
    assert len(fk.lead_calls) == n and st2["spot_stops"]["AAA"]["lead"] == {"sp1": 7.5, "sp2": 7.5} and "AAA" not in st2["spot_stop_missing"]
    # the sub-positions and the coin are gone → the stop fired
    fk.lead_subs.clear()
    r = ss.sync(cl, pf(stop_aaa=7.5), st, NOW + 4, logs.append, avail={}, lead={})
    assert r["fired"] == ["AAA"] and "AAA" in ss.held_off(st, NOW + 5)


def test_a_sub_position_whose_stop_cannot_be_moved_keeps_the_old_one_and_one_without_a_stop_is_a_miss(monkeypatch):
    fk = F.FakeOkx(balances={"USDT": 1000.0, "AAA": 50.0}, lead_subs=[{"subPosId": "sp1", "instId": "AAA-USDT", "subPos": 30.0, "openAvgPx": 10.0,
                                                                     "sl": "8", "sl_algo": ""},       # has a stop, no algo id to cancel
                                                                    {"subPosId": "sp2", "instId": "AAA-USDT", "subPos": 20.0, "openAvgPx": 9.0}])
    cl = ap.connect(F.env(), fk, {}, F.nosleep)
    st, logs = {}, []
    ss.sync(cl, pf(stop_aaa=7.5), st, NOW, logs.append, avail={"AAA": 50.0}, lead=_lead(fk))
    assert st["spot_stops"]["AAA"]["lead"] == {"sp1": 8.0, "sp2": 7.5} and "AAA" not in st["spot_stop_missing"]   # protected, old price noted
    assert any(e["ev"] == "spot_stop_skip" and "старым стопом" in e["why"] for e in logs)
    fk.lead_subs.append({"subPosId": "sp3", "instId": "AAA-USDT", "subPos": 5.0, "openAvgPx": 9.0})
    orig = cl.call

    def call(method, path, params=None, body=None, signed=True):
        if path == ss.LEAD_ALGO and (body or {}).get("subPosId") == "sp3":
            raise RuntimeError("OKX: 1 [sCode 59999] refused")
        return orig(method, path, params, body, signed)
    monkeypatch.setattr(cl, "call", call)
    ss.sync(cl, pf(stop_aaa=7.5), st, NOW + 1, logs.append, avail={"AAA": 55.0}, lead=_lead(fk))
    assert "без стопа" in st["spot_stop_missing"]["AAA"]["why"] and "59999" in st["spot_stop_missing"]["AAA"]["why"]
    assert set(st["spot_stops"]["AAA"]["lead"]) == {"sp1", "sp2"}                    # the others keep theirs


def test_no_buy_back_at_the_model_stop_morpho_2011():
    """20.11 (MORPHO): the lead stop fired, the drift pass bought the coin back right above its stop, the stop fired again — twice.
    A non-core coin at (or within 1 % above) the model's stop is never bought; far from it, it is."""
    import copy
    e = F.env()
    p = copy.deepcopy(F.portfolio())
    for x in p["portfolios"]["ideas"]["positions"]:
        if x["symbol"] == "AAA":
            x["levels"]["stop"] = 9.95                       # price 10.0 → 0.5 % above the stop
    b = ap.build(ap.connect(e, F.FakeOkx(), {}, F.nosleep), e, p)
    assert "AAA" not in {o["ccy"] for o in b["buys"]} and any("AAA" in d and "у стопа модели" in d for d in b["diffs"])
    assert "BTC" in {o["ccy"] for o in b["buys"]}             # the core is untouched by the guard
    b = ap.build(ap.connect(e, F.FakeOkx(), {}, F.nosleep), e, F.portfolio())   # stop 8.0, 20 % below → bought
    assert "AAA" in {o["ccy"] for o in b["buys"]}
