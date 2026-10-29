import os, sys, json, sqlite3, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cnq.agents.llm import MockLLM, parse_json
from cnq.agents.news import NewsCollector, NewsAnalyst, ImpactLedger
from cnq.agents.risk import RiskOfficer
from cnq.agents.fundamentals import assess

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>SEC approves spot Solana ETF applications</title><link>https://x/1</link><pubDate>Mon, 12 Oct 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;The SEC approved...&lt;/p&gt;</description></item>
<item><title>Major exchange hack drains $200M, Ethereum bridge exploited</title><link>https://x/2</link><pubDate>Mon, 12 Oct 2026 11:00:00 GMT</pubDate><description>hack</description></item>
<item><title>Analyst says Dogecoin could rally</title><link>https://x/3</link><pubDate>Mon, 12 Oct 2026 12:00:00 GMT</pubDate><description>opinion</description></item>
</channel></rss>"""

def test_rss_parse_and_store():
    conn = sqlite3.connect(":memory:")
    col = NewsCollector(conn)
    items = col.parse_rss(RSS, "test")
    assert len(items) == 3 and "SOL" in items[0]["assets_hint"] and "ETH" in items[1]["assets_hint"]
    assert len(col.store(items)) == 3 and len(col.store(items)) == 0   # dedupe
    assert items[0]["published"] == 1791799200000

def test_analyst_ledger_and_calibration():
    conn = sqlite3.connect(":memory:")
    col = NewsCollector(conn); items = col.parse_rss(RSS, "test"); col.store(items)
    an = NewsAnalyst(MockLLM(), conn)
    imps = an.analyze(items, {"BTC": 100.0, "SOL": 10.0, "ETH": 50.0, "DOGE": 0.1})
    assert any(i["category"] == "etf_flows" and i["direction"] == "bull" for i in imps)
    assert any(i["category"] == "hack" and i["direction"] == "bear" for i in imps)
    led = ImpactLedger(conn)
    later = int(time.time() * 1000) + 3 * 86_400_000
    n = led.score_due({"BTC": 101.0, "SOL": 12.0, "ETH": 45.0, "DOGE": 0.1}, later)
    assert n == len(imps)
    cal = {c["category"]: c for c in led.calibration()}
    assert cal["etf_flows"]["directional_hit_rate"] == 1.0 and cal["hack"]["directional_hit_rate"] == 1.0

def test_risk_officer_blackout_and_regime():
    now = int(time.time() * 1000)
    report = {"asof": now - 60_000, "portfolio": {"btc_bull": False},
              "coins": [{"symbol": "BTCUSDT", "derivatives": {"available": False}}, {"symbol": "SOLUSDT", "derivatives": {"available": True, "funding_rank90d": 0.97}}]}
    impacts = [{"asset": "SOL", "magnitude": 4, "confidence": 0.8, "created": now - 3_600_000, "category": "hack", "direction": "bear"}]
    r = RiskOfficer().review(report, impacts, {"SOL": {"grade": "amber", "flags": []}}, [], now_ms=now)
    v = {x["symbol"]: x for x in r["verdicts"]}
    assert v["SOLUSDT"]["allow_long"] is False and v["SOLUSDT"]["allow_short"] is False     # blackout
    assert v["BTCUSDT"]["allow_long"] is True                                           # BTC itself allowed
    r2 = RiskOfficer().review({**report, "portfolio": {"btc_bull": False}}, [], {}, [], now_ms=now)
    assert {x["symbol"]: x for x in r2["verdicts"]}["SOLUSDT"]["allow_long"] is False   # alts blocked below SMA200
    stale = RiskOfficer().review({**report, "asof": now - 3 * 3_600_000}, [], {}, [], now_ms=now)
    assert stale["global_block"]

def test_fundamentals_flags():
    f = assess({"circulating": 100, "total_supply": 300, "mcap_usd": 1e9, "fdv_usd": 3e9, "volume_24h_usd": 1e7, "ath_change_pct": -95})
    assert f["grade"] == "red" and f["unlock_overhang"] == 2.0 and f["vol_mcap"] == 0.01

def test_parse_json_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1} and parse_json("x [1,2] y") == [1, 2]


def test_bingx_signing_matches_reference():
    import hmac, hashlib
    from cnq.execution.bingx_trade import BingXTrader
    t = BingXTrader(api_key="k", secret="SECRET_KEY", live=False)
    # reference string from the official docs example
    qs = "recvWindow=0&symbol=BTC-USDT&timestamp=1696751141337"
    assert hmac.new(b"SECRET_KEY", qs.encode(), hashlib.sha256).hexdigest() == hmac.new(t.secret, qs.encode(), hashlib.sha256).hexdigest()
    assert t.base.startswith("https://open-api-vst") and t.mode == "VST-DEMO"


def test_executor_rejects_bad_key_and_missing_fields():
    import json, threading, urllib.request, time
    from http.server import HTTPServer
    import cnq.execution.server as srv
    srv.KEY = "s3cret"
    httpd = HTTPServer(("127.0.0.1", 0), srv.H); port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True); th.start()
    def post(body, key):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/execute", data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-CNQ-Key": key}, method="POST")
        try:
            with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
    assert post({}, "wrong")[0] == 401
    code, res = post({"symbol": "BTC-USDT"}, "s3cret")
    assert code == 400 and "missing" in res["error"]
    httpd.shutdown()


def test_bybit_signing_and_rounding():
    from cnq.execution.bybit_trade import BybitTrader
    t = BybitTrader(api_key="k", secret="s", live=False)
    assert t.mode == "BYBIT-DEMO" and t.base.startswith("https://api-demo")
    import hashlib, hmac
    assert t.sign("1700000000000", '{"a":1}') == hmac.new(b"s", b'1700000000000k5000{"a":1}', hashlib.sha256).hexdigest()
    assert t._round_step(0.12345, 0.001) == 0.123 and t._round_step(81917.37, 0.1) == 81917.4


def test_voice_brief_says_what_the_panel_shows(tmp_path):
    # 21.10 (owner: «голос робота снова»): the panel plays the server's neural audio only when the brief's keys match its own
    # (fut 'nova' = the AVERAGE of the mix, spot 'ideas', desks mode, total = futures + spot); funding/arb/tri are not in the keys' parts
    import json, time
    from cnq.agents import voice, panel_header as ph
    now = int(time.time() * 1000)
    hist = lambda t0, eq: [{"time": t0, "equity": 10_000.0}, {"time": t0 + 3_600_000, "equity": eq}]
    # 28.10: the main desk is whatever web/desk_select.json chose (none → the default) — the brief follows it, not one fixed key
    for sel, main in ((None, ph.NOVA_BASE[0]), ({"current": "bal"}, "bal"), ({"current": "cons"}, "cons")):
        d = tmp_path / main
        d.mkdir()
        other = {"equity": 9_000.0, "start_equity": 10_000.0, "open": [{"open_pnl_usd": 1.0}], "closed": [],
                 "equity_history": hist(now - 4 * 86400_000, 9_000.0)}          # a family desk that is NOT the main one: never counted
        paper = {"profiles": {main: {"equity": 10_200.0, "start_equity": 10_000.0, "open": [], "closed": [], "equity_history": hist(now - 4 * 86400_000, 10_200.0)},
                              "imp": {"equity": 10_000.0, "start_equity": 10_000.0, "open": [], "closed": [], "equity_history": hist(now - 3 * 86400_000, 10_000.0)},
                              **{k: other for k in ph.FAMILY if k != main}}}
        (d / "paper.json").write_text(json.dumps(paper))
        if sel:
            (d / "desk_select.json").write_text(json.dumps(sel))
        (d / "portfolio.json").write_text(json.dumps({"portfolios": {"ideas": {"index": 103.0, "start_usd": 10_000}}}))
        (d / "capital.json").write_text(json.dumps({"value_usd": 10_216, "pnl_usd": 216}))       # present, but the brief no longer switches to it
        (d / "arb.json").write_text(json.dumps({"week": {"virtual_pnl_usd": 33.27}}))
        text = voice.compose(str(d))
        k = voice.compose.keys
        assert k["fut"] == "nova" and k["spot"] == "ideas" and k["mode"] == "desks" and k["fut_open"] == 0
        assert k["parts"] == {"fut": 100, "spot": 300} and k["total"] == 400      # (200 + 0) / 20 000 × 10 000 = 100, not the sum 200
        assert "arb" not in k["parts"] and k["probation"]["arb"] == 33.27
        assert "в плюсе на 100 доллар" in text and "Итого по демо — плюс 400" in text
        # final QA 28.10: the desks are labelled demo, never «CRYPTO NOVA — …» as the system's result; no real file → no real line
        assert "Демо-фьючерсы, отобранные сигналы: с" in text and "CRYPTO NOVA — отобранные" not in text and "Реальный счёт" not in text
    # the real account first: web/public_real.json (value, result since 22.10, its futures)
    (d / "public_real.json").write_text(json.dumps({"since": 1792669322166, "equity_usd": 9877.17, "pnl_usd": -147.23, "futures": [],
                                                    "closed_n": 1, "fut_pnl_usd": 0.27, "stale": False}))
    text = voice.compose(str(d))
    assert text.index("Реальный счёт CRYPTO NOVA на OKX — 9877 долларов: с 22.10 в минусе на 147 долларов.") < text.index("Демо-фьючерсы")
    assert "Фьючерсы реального счёта: открытых сделок нет; закрыто с начала: 1; итог около нуля." in text
    assert voice.compose.keys["parts"] == {"fut": 100, "spot": 300}          # the panel's keys are unchanged (it still plays the audio)
    assert voice.spoken("CRYPTO NOVA портфель") == "Крипто Но́ва портфель"


def test_voice_brief_neural_voices_in_order_and_a_failure_keeps_the_last_mp3(tmp_path, monkeypatch):
    # 29.10: the panel's spoken brief (web/brief.mp3) is read by Dmitry, then by the API's Russian fallbacks in order (owner 15:42: a
    # good Microsoft neural voice of either gender — Svetlana, then Brian Multilingual); Google's gTTS is gone. A failure of every
    # voice leaves the previous brief.mp3 untouched, and brief.json carries the new text with audio null (the panel never plays
    # numbers that differ)
    import json, sys, types
    from cnq.agents import voice
    from cnq.api import tts
    fbs = tts.FALLBACK["ru"]
    sv, br = fbs[:2]
    assert voice.brief_voices() == ["ru-RU-DmitryNeural", *fbs] and all(v.endswith("Neural") for v in voice.brief_voices())
    calls, refuse = [], {"ru-RU-DmitryNeural"}

    class Communicate:
        def __init__(self, text, voice_, rate=None):
            self.voice = voice_
            calls.append(voice_)

        async def save(self, path):
            if self.voice in refuse:
                open(path, "wb").write(b"")                    # a refused call may leave an empty file behind
                raise RuntimeError("No audio was received.")
            open(path, "wb").write(b"ID3" + self.voice.encode() * 200)
    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=Communicate))
    monkeypatch.setattr(voice, "RETRY_PAUSE_S", (0, 0))
    d = str(tmp_path)
    r = voice.run(d)
    assert calls == ["ru-RU-DmitryNeural"] * 2 + [sv] and r["audio"] == "brief.mp3" and r["voice"] == sv
    first = (tmp_path / "brief.mp3").read_bytes()
    assert first.startswith(b"ID3") and sv.encode() in first and not (tmp_path / "brief.mp3.tmp").exists()
    refuse.add(sv)
    calls.clear()
    r = voice.run(d)
    assert calls == ["ru-RU-DmitryNeural"] * 2 + [sv] * 2 + [br] and r["voice"] == br
    first = (tmp_path / "brief.mp3").read_bytes()
    assert not any("google" in v for v in calls)
    # every voice refuses → the previous mp3 stays, the text updates, audio null; no gTTS
    refuse.update(fbs)
    calls.clear()
    r2 = voice.run(d)
    assert calls == ["ru-RU-DmitryNeural"] * 2 + [v for v in fbs for _ in (0, 1)] and r2["audio"] is None and r2["voice"] is None
    assert (tmp_path / "brief.mp3").read_bytes() == first and not (tmp_path / "brief.mp3.tmp").exists()
    j = json.load(open(tmp_path / "brief.json", encoding="utf-8"))
    assert j["text"] == r2["text"] and j["audio"] is None and "No audio" in j["error"]
