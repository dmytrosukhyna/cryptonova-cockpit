"""«Настроение опционов» (cnq/agents/options_mood.py, owner 30.10.2026) — DVOL + put/call as information only: no desk, no trade."""
import json
import os
import pathlib
import time

from cnq.agents import options_mood as om

NOW = 1_793_400_000_000
DAY = om.DAY_MS


def _deribit_get(now, dvol=55.0, hist_days=300, hist_lo=40.0, hist_hi=80.0, puts=60_000.0, calls=100_000.0, hist_fail=False):
    def get(url):
        if "get_index_price" in url:
            return {"result": {"index_price": dvol, "estimated_delivery_price": dvol}}
        if "get_volatility_index_data" in url:
            if hist_fail:
                raise OSError("timeout")
            step = (hist_hi - hist_lo) / max(hist_days - 1, 1)
            return {"result": {"data": [[now - (hist_days - i) * DAY, 0, 0, 0, hist_lo + i * step] for i in range(hist_days)]}}
        if "get_book_summary_by_currency" in url:
            return {"result": [{"instrument_name": "BTC-26DEC26-80000-P", "open_interest": puts / 2},
                               {"instrument_name": "BTC-26DEC26-70000-P", "open_interest": puts / 2},
                               {"instrument_name": "BTC-26DEC26-90000-C", "open_interest": calls},
                               {"instrument_name": "BTC-26DEC26-95000-X", "open_interest": 1e9}]}
        raise AssertionError(url)
    return get


def test_deribit_reading_and_percentile():
    d = om.fetch_deribit(NOW, _deribit_get(NOW))
    assert d["source"] == "deribit" and d["dvol"] == 55.0 and d["puts"] == 60_000 and d["calls"] == 100_000 and len(d["hist"]) == 300
    doc = om.build(NOW, d, {}, None)
    assert doc["put_call_oi"] == 0.6 and doc["dvol"] == 55.0 and doc["source"] == "deribit"
    assert 35 <= doc["dvol_pct_1y"] <= 40                      # 55 sits ≈ 37 % up the 40…80 ramp
    assert doc["label"] == "tense" and doc["label_ru"] == "напряжённо" and doc["label_en"] == "tense"
    assert doc["note_ru"] == "информация, не сигнал" and "не сигнал" in doc["text_ru"] and "опционами не торгует" in doc["text_ru"]
    assert doc["dvol_hist_days"] == 301 and doc["lesson"] == "l4-options" and doc["error"] is None


def test_labels():
    assert om.label(42.0, 10, 0.5) == "calm"
    assert om.label(42.0, 10, 0.75) == "tense"              # calm vol, but puts piling up
    assert om.label(70.0, 90, 0.5) == "fear"
    assert om.label(50.0, 50, 0.95) == "fear"               # puts clearly outnumber calls
    assert om.label(50.0, 50, 0.5) == "tense"
    assert om.label(40.0, None, 0.5) == "calm" and om.label(70.0, None, 0.5) == "fear" and om.label(55.0, None, 0.5) == "tense"
    assert om.label(None, None, 0.5) == "calm" and om.label(None, None, 0.8) == "tense"


def test_percentile_needs_history():
    assert om.percentile(50.0, [40.0] * 10) is None
    assert om.percentile(50.0, [40.0] * 100) == 100 and om.percentile(30.0, [40.0] * 100) == 0
    assert om.percentile(40.0, [40.0] * 100) == 50


def test_history_survives_api_hiccup(tmp_path):
    out, data = str(tmp_path / "web"), str(tmp_path / "data")
    f1 = lambda now: om.fetch_deribit(now, _deribit_get(now))
    a = om.run(out, data, NOW, fetchers=(f1,))
    assert a["dvol_pct_1y"] is not None and os.path.exists(os.path.join(out, "options_mood.json"))
    f2 = lambda now: om.fetch_deribit(now, _deribit_get(now, dvol=85.0, hist_fail=True))
    b = om.run(out, data, NOW + DAY, fetchers=(f2,), force=True)
    assert b["dvol"] == 85.0 and b["dvol_pct_1y"] is not None and b["dvol_pct_1y"] >= 98 and b["label"] == "fear"
    st = json.load(open(os.path.join(data, om.STATE_FILE), encoding="utf-8"))
    assert len(st["hist"]) >= 300 and all(int(k) >= NOW + DAY - om.YEAR_DAYS * DAY for k in st["hist"])


def test_cache_and_stale(tmp_path):
    out, data = str(tmp_path / "web"), str(tmp_path / "data")
    calls = []
    def f(now):
        calls.append(now)
        return om.fetch_deribit(now, _deribit_get(now))
    a = om.run(out, data, NOW, fetchers=(f,))
    b = om.run(out, data, NOW + 10 * 60_000, fetchers=(f,))          # 10 min later: the cached answer, no request
    assert b is not None and b["asof"] == a["asof"] and len(calls) == 1
    def down(now):
        raise OSError("blocked")
    c = om.run(out, data, NOW + 40 * 60_000, fetchers=(down,))
    assert c["stale"] is True and c["dvol"] == 55.0 and "blocked" in c["error"] and c["label"] == a["label"]
    w = json.load(open(os.path.join(out, "options_mood.json"), encoding="utf-8"))
    assert w["stale"] is True


def test_okx_fallback(tmp_path):
    def okx_get(url):
        if "opt-summary" in url:
            return {"data": [{"instId": "BTC-USD-261127-80000-C", "delta": "0.52", "markVol": "0.61"},
                             {"instId": "BTC-USD-261127-80000-P", "delta": "-0.48", "markVol": "0.63"},
                             {"instId": "BTC-USD-261127-120000-C", "delta": "0.05", "markVol": "0.95"},      # far from the money
                             {"instId": "BTC-USD-271231-80000-C", "delta": "0.55", "markVol": "0.70"}]}     # far expiry
        if "open-interest" in url:
            return {"data": [{"instId": "BTC-USD-261127-80000-C", "oi": "1000"}, {"instId": "BTC-USD-261127-70000-P", "oi": "950"}]}
        raise AssertionError(url)
    now = 1_793_400_000_000                                            # 30.10.2026: 27.11 is 28 days out
    d = om.fetch_okx(now, okx_get)
    assert d["source"] == "okx" and d["dvol"] == 62.0 and d["puts"] == 950 and d["calls"] == 1000
    def deribit_down(now):
        raise OSError("403")
    doc = om.run(str(tmp_path / "web"), str(tmp_path / "data"), now, fetchers=(deribit_down, lambda n: om.fetch_okx(n, okx_get)))
    assert doc["source"] == "okx" and doc["dvol_pct_1y"] is None and doc["put_call_oi"] == 0.95 and doc["label"] == "fear"
    assert "deribit_down" in doc["error"] and "OKX" in doc["text_ru"]


def test_pipeline_and_panel_wiring():
    root = pathlib.Path(__file__).resolve().parents[1]
    wf = (root / ".github" / "workflows" / "site.yml").read_text(encoding="utf-8")
    assert "cnq.cli optmood" in wf and "web/options_mood.json" in wf[wf.index("- name: Commit refreshed data"):]
    app = (root / "web" / "app.html").read_text(encoding="utf-8")
    assert "options_mood.json" in app and "Настроение опционов" in app and "информация, не сигнал" in app
    vip = (root / "web" / "vip" / "overview.js").read_text(encoding="utf-8")
    assert "options_mood.json" in vip and "Настроение опционов" in vip and "не сигнал" in vip
    assert "optmood" in (root / "cnq" / "cli.py").read_text(encoding="utf-8")
    assert not any(k in (root / "cnq" / "agents" / "options_mood.py").read_text(encoding="utf-8") for k in ("place_order", "okx_autopilot", "paper_trades"))
