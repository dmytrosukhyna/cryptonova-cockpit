"""23.11 (owner): no system spot sells — only a sound swap into a better coin in the same pass; exchange stops stay."""
import json
import os

from cnq.execution import spot_sell_guard as SG

C = {"on": True, "rating_gap": 15, "day_cap_pct": 0.03}
R = {"HYPE": 60, "UNI": 50, "TRX": 77, "BTC": 90, "ETH": 85, "ARB": 40}


def S(ccy, usd, act="sell"):
    return {"ccy": ccy, "usd": usd, "act": act, "sz": "1", "lot": "0.001", "min": "0.001"}


def test_defensive_sells_are_blocked_and_logged():
    kept, notes = SG.apply([S("HYPE", 200)], [], 10_000, C, R, lambda c: True, 0, False)
    assert kept == [] and "HYPE: продажа заблокирована владельцем" in notes[0]
    kept, _ = SG.apply([S("HYPE", 200)], [{"ccy": "UNI", "usd": 200}], 10_000, C, R, lambda c: True, 0, False)
    assert kept == []                                                   # UNI is not better rated
    kept, _ = SG.apply([S("UNI", 200)], [{"ccy": "TRX", "usd": 200}], 10_000, C, R, lambda c: False, 0, False)
    assert kept == []                                                   # TRX falling and BTC not bull


def test_a_sound_swap_is_allowed_within_the_day_cap():
    kept, notes = SG.apply([S("UNI", 500)], [{"ccy": "TRX", "usd": 400}], 10_000, C, R, lambda c: True, 0, False)
    (o,) = kept
    assert o["swap_to"] == "TRX" and o["usd"] == 300 and abs(o["_scale"] - 0.6) < 1e-9 and notes[0].startswith("замена: UNI → TRX")
    kept, notes = SG.apply([S("UNI", 500)], [{"ccy": "TRX", "usd": 400}], 10_000, C, R, lambda c: True, 295, False)
    assert kept == [] and "дневной предел" in notes[0]
    kept, _ = SG.apply([S("BTC", 100, "trim")], [{"ccy": "ETH", "usd": 100}], 10_000, C, R, lambda c: True, 0, True)
    assert kept and kept[0]["swap_to"] == "ETH"                         # core ↔ core: pairing only


def test_live_config_and_hooks():
    import inspect
    from cnq.execution import okx_autopilot as ap
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    c = json.load(open(os.path.join(root, "config", "owner_overrides.json")))[SG.KEY]
    assert c["on"] and c["tp_off"] and c["stops_keep"] and c["rating_gap"] == 15 and c["day_cap_pct"] == 0.03
    assert "SG.apply(" in inspect.getsource(ap.build) and "SG.cfg(" in inspect.getsource(ap.spot_stops)
