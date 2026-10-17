"""Macro watch (web/macro.json): offline, fake Yahoo / Treasury / CBOE / ECB responses."""
import json
import os
import tempfile

from cnq.agents import macro_watch as mw

DAY = 86_400
NOW = 1_760_600_000          # 2025-10-16


def yahoo_json(closes, t0=1_760_000_000):
    ts = [t0 + i * DAY for i in range(len(closes))]
    return json.dumps({"chart": {"result": [{"meta": {"regularMarketPrice": closes[-1]}, "timestamp": ts,
                                             "indicators": {"quote": [{"close": closes}]}}]}}).encode()


def fake(yahoo=None, treasury=None, cboe=None, ecb=None, yahoo_down=False):
    yahoo, cboe = yahoo or {}, cboe or {}

    def f(url, timeout=None):
        if "finance.yahoo.com" in url:
            if yahoo_down:
                raise OSError("HTTP 429 Too Many Requests")
            for sym, closes in yahoo.items():
                if f"/chart/{mw.urllib.parse.quote(sym, safe='')}?" in url:
                    return yahoo_json(closes)
            raise OSError("404")
        if "home.treasury.gov" in url and treasury:
            month = url.split("field_tdr_date_value_month=")[1][:6]
            rows = [r for r in treasury if r[0][6:] + r[0][:2] == month]
            body = 'Date,"3 Mo","2 Yr","10 Yr"\n' + "\n".join(f"{d},4.0,{y2},{y10}" for d, y2, y10 in rows)
            return body.encode()
        if "cdn.cboe.com" in url:
            for sym, (cur, prev) in cboe.items():
                if f"/{sym}.json" in url:
                    return json.dumps({"data": {"current_price": cur, "prev_day_close": prev}}).encode()
        if "frankfurter" in url and ecb:
            return json.dumps({"rates": {d: {"JPY": v} for d, v in ecb}}).encode()
        raise OSError("unreachable " + url)
    return f


def item(d, key):
    return next(i for i in d["items"] if i["key"] == key)


def test_changes_pct_and_bp_and_signals():
    nq = [100.0, 101, 102, 103, 104, 105, 102.9]                    # −2 % today; vs 5 sessions back (101): +1.88 %
    tnx = [4.00, 4.05, 4.10, 4.12, 4.15, 4.20, 4.35]                # +15 bp today, +30 bp over 5 sessions
    d = mw.run(fetch=fake(yahoo={"NQ=F": nq, "^TNX": tnx, "^N225": [100.0, 98.0]}), now=NOW)
    n = item(d, "NDX_FUT")
    assert n["value"] == 102.9 and n["chg_1d"] == -2.0 and n["chg_5d"] == round((102.9 / 101 - 1) * 100, 2) and n["source"] == "Yahoo Finance"
    assert n["signal_ru"].startswith("Акции США резко падают")
    t = item(d, "US10Y")
    assert t["chg_1d"] == 15.0 and t["chg_5d"] == 30.0 and t["chg_unit"] == "бп" and t["signal_ru"] == ""   # no robust link → no note
    k = item(d, "NIKKEI")
    assert k["chg_1d"] == -2.0 and "чаще рос" in k["signal_ru"] and "2021–2022" in k["signal_ru"]


def test_old_tnx_convention_scaled():
    t = item(mw.run(fetch=fake(yahoo={"^TNX": [42.0, 42.5]}), now=NOW), "US10Y")
    assert t["value"] == 4.25 and t["chg_1d"] == 5.0


def test_fallbacks_when_yahoo_down():
    treasury = [("09/30/2025", 3.60, 4.15), ("10/14/2025", 3.50, 4.02), ("10/15/2025", 3.55, 4.10)]
    d = mw.run(fetch=fake(treasury=treasury, cboe={"_VIX": (25.0, 20.0), "_NDX": (24500.0, 25000.0)},
                          ecb=[("2025-10-14", 151.0), ("2025-10-15", 149.5)], yahoo_down=True), now=NOW)
    t = item(d, "US10Y")
    assert t["source"].startswith("US Treasury") and t["value"] == 4.1 and t["chg_1d"] == 8.0 and t["asof"] == "2025-10-15"
    assert t["chg_5d"] is None                                       # only 3 rows → no 5-session change
    v = item(d, "VIX")
    assert v["source"].startswith("CBOE") and v["chg_1d"] == 25.0 and v["signal_ru"].startswith("Страх на рынке акций")
    n = item(d, "NDX_FUT")
    assert n["name_ru"] == "Nasdaq-100 (индекс)" and n["chg_1d"] == -2.0
    j = item(d, "USDJPY")
    assert j["source"].startswith("ЕЦБ") and j["chg_1d"] == round((149.5 / 151 - 1) * 100, 2)
    g = item(d, "GOLD")                                               # no free fallback → empty item, error listed
    assert g["value"] is None and g["source"] is None and any(e.startswith("GOLD") for e in d["errors"])


def test_write_json_shape():
    with tempfile.TemporaryDirectory() as tmp:
        d = mw.write(tmp, fetch=fake(yahoo={s: [1.0, 1.0] for _, _, _, s, _, _ in mw.ITEMS}), now=NOW)
        x = json.load(open(os.path.join(tmp, "macro.json"), encoding="utf-8"))
    assert set(x) >= {"asof", "items", "notes"} and len(x["items"]) == len(mw.ITEMS) == 11
    assert {"key", "name_ru", "value", "chg_1d", "chg_5d", "unit", "source", "signal_ru"} <= set(x["items"][0])
    assert x["asof"] == d["asof"] == NOW * 1000 and not x["errors"] and all(isinstance(n, str) for n in x["notes"])
