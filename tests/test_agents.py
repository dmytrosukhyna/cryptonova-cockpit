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
