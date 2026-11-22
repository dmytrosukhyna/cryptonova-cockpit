"""web/real_why.json (cnq/agents/real_why.py): why each real futures position is open — made-up data, no network."""
from cnq.agents import real_why as R
from cnq.execution import owner_overrides as ov

T = 1_800_000_000_000
H = 3_600_000


def fake_post(body):
    if body["type"] == "candleSnapshot":
        c = body["req"]["coin"]
        base = 100.0 if c == "BTC" else 10.0
        out = []
        for i in range(24 * 30):
            t = T - (24 * 30 - i) * H
            px = base * (1 + 0.05 * ((i // 24) % 5) / 5) * (1 + 0.001 * i / 720)
            out.append({"t": t, "o": px, "h": px * 1.01, "l": px * 0.99, "c": px, "v": 1000, "n": 10})
        return out
    if body["type"] == "fundingHistory":
        return [{"fundingRate": "0.0000125", "time": T - H}]
    raise AssertionError(body)


def test_whale_copy_gets_whale_record_stop_target_market_and_owner_note():
    wtop = {"top": [{"addr": "0xabc", "pnl_30d": 1000.0, "trips": 12, "win": 0.75, "pf": 2.0, "hold_med_h": 40, "style_ru": "свинг", "rank": 3}],
            "desk": {"open": [{"whale": "0xabc", "whale_ru": "Кит", "coin": "HYPE", "side": "SHORT", "opened_t": T - 10 * H,
                               "whale_entry": 90.0, "whale_lev": 8.0, "whale_notional": 1e6, "stop_src": "защитный стоп −8%"},
                              {"whale": "0xabc", "whale_ru": "Кит", "coin": "kPEPE", "side": "LONG", "opened_t": T - 10 * H,
                               "whale_entry": 0.005, "whale_lev": 10.0, "whale_notional": 200}], "closed": []}}
    pub = {"futures": [{"inst": "HYPE-USDT-SWAP", "side": "short", "entry": 85.0, "stop": 91.8, "opened": T, "sig": f"whales:W-HYPE-S-{T - 10 * H}"},
                       {"inst": "PEPE-USDT-SWAP", "side": "long", "entry": 0.0000050, "stop": 0.0000046, "opened": T, "sig": f"whales:W-1000PEPE-L-{T - 10 * H}"}],
           "closed": [], "actions": []}
    a = ov.active({"set": T + 1, "until": T + 99 * H, "close": ["HYPE"], "no_short": ["HYPE"], "note_ru": "тест"}, T + 2)
    d = R.build(pub, wtop, None, fake_post, T + 2, a)
    w = d["by_sig"][f"whales:W-HYPE-S-{T - 10 * H}"]
    assert w["whale"]["source"] == "hyperliquid" and w["whale"]["entry"] == 90.0 and w["whale"]["lev"] == 8.0
    assert w["whale"]["record_30d"]["trades"] == 12 and w["copy"]["lev"] == 5.0 and "вместе с китом" in w["copy"]["exit_ru"]
    assert abs(w["target"]["ref_px"] - (85.0 - 2 * 6.8)) < 1e-6 and "решению владельца" in w["owner_ru"]
    assert w["market"]["btc_24h_pct"] is not None and w["market"]["funding_8h_pct"] == 0.01 and w["market"]["oi_4h_pct"] is None
    assert w["text_ru"].count("\n") == 1
    p = d["by_sig"][f"whales:W-1000PEPE-L-{T - 10 * H}"]
    assert abs(p["whale"]["entry"] - 0.000005) < 1e-12                       # kPEPE per 1000 → per coin
    # kept: the market context is not fetched again for a known trade
    d2 = R.build(pub, wtop, {"by_sig": d["memory"]}, lambda b: (_ for _ in ()).throw(AssertionError("refetch")), T + 3, a)
    assert d2["by_sig"][f"whales:W-HYPE-S-{T - 10 * H}"]["market"] == w["market"]


def test_closed_trades_keep_their_desk_id_after_the_open_leaves_the_action_list():
    """22.11 (post-mortem: 27 closes without a desk-trade id): the closed row finds its sig by the close action, by the ledger of every
    open ever seen (public_real lists only the 30 newest actions) and by the 04.11–05.11 backfill."""
    sig = f"whales:W-SOL-S-{T - 5 * H}"
    pub1 = {"futures": [], "closed": [], "actions": [{"t": T - 4 * H, "kind": "fut_open", "sym": "SOL", "side": "short", "sig": sig}]}
    d1 = R.build(pub1, {}, None, fake_post, T, {})
    assert any(r["sig"] == sig and r["src"] == "action" for r in d1["ledger"])
    # later: the open is no longer among the actions (restart / the 30 newest); the closed row still gets its sig from the ledger
    row = {"inst": "SOL-USDT-SWAP", "side": "short", "open_px": 100.0, "opened": T - 4 * H + 3 * 60_000, "closed": T}
    d2 = R.build({"futures": [], "closed": [row], "actions": []}, {}, {"by_sig": d1["memory"], "ledger": d1["ledger"]}, fake_post, T + 1, {})
    assert d2["closed"][0]["sig"] == sig and d2["closed"][0]["src"] == "action" and d2["closed"][0]["why"]["desk"] == "whales"
    # the close action carries the sig (public_real pairs it with the open) — it wins even with no ledger
    sig2 = "ivan2:X-SOL-1"
    d3 = R.build({"futures": [], "closed": [row], "actions": [{"t": T + 60_000, "kind": "fut_close", "sym": "SOL", "sig": sig2}]},
                 {}, None, fake_post, T + 2, {})
    assert d3["closed"][0]["sig"] == sig2 and d3["closed"][0]["src"] == "close"
    # the backfill: a 05.11 copy no public record kept
    bf = R.backfill()
    assert len(bf) == 30 and all(b["sig"].startswith("whales:BF-") for b in bf)
    fil = next(b for b in bf if b["sym"] == "FIL")
    old = {"inst": "FIL-USDT-SWAP", "side": "long", "open_px": 0.86, "opened": fil["t"] + 40_000, "closed": fil["t"] + 2 * H}
    d4 = R.build({"futures": [], "closed": [old], "actions": []}, {}, None, fake_post, T + 3, {})
    assert d4["closed"][0]["sig"] == fil["sig"] and d4["closed"][0]["src"] == "backfill"
    # nothing within ± 10 min → no guess
    far = {**row, "opened": T - 9 * H, "closed": T - 8 * H}
    assert R.build({"futures": [], "closed": [far], "actions": []}, {}, None, fake_post, T + 4, {})["closed"][0]["sig"] is None
