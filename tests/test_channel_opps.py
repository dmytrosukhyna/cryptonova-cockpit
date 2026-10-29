"""«Из каналов» (29.10): the classifier, the record (no post text), fresh-only pricing, 1 / 7 / 30-day results vs BTC, the run that
feeds both channel_watch.json and channel_opps.json from ONE read of each channel, and the VIP opportunities block."""
import json
import sqlite3

from cnq.agents import channel_opps as O
from cnq.agents import channel_watch as W
from cnq.agents import vip_feeds as vf

H = 3_600_000
DAY = 86_400_000
T0 = 1_792_000_000_000
KNOWN = {"BTC", "ETH", "SOL", "XPL", "LINEA", "WLD", "ARB", "HYPE", "NOT", "LINK"}
OKX = {"BTC", "ETH", "SOL", "XPL", "HYPE"}


def test_categories_and_fields():
    e = O.extract("Binance Launchpool: новый проект Plasma (XPL), фармим с BNB", (), KNOWN, OKX)
    assert e["cat"] == "launchpad" and e["ticker"] == "XPL" and e["project"] == "Plasma" and e["venue"] == "binance" and e["okx"] is True
    e = O.extract("Bybit залистит #LINEA на споте завтра", (), KNOWN, OKX)
    assert e["cat"] == "listing" and e["ticker"] == "LINEA" and e["venue"] == "bybit" and e["okx"] is False
    e = O.extract("Новый тестнет от Monad — фармим поинты, возможен ретродроп", (), KNOWN, OKX)
    assert e["cat"] == "airdrop" and e["ticker"] is None and e["project"] == "Monad"
    assert O.extract("Проект привлёк $25M от a16z и Paradigm, токена пока нет", (), KNOWN, OKX)["cat"] == "early"
    assert O.extract("SEC одобрила ETF, новости по #SOL", (), KNOWN, OKX)["cat"] == "news"
    ad = O.extract("Регистрируйся и забирай бонус до 5000$", ("https://partner.bybit.com/b/abc",), KNOWN, OKX)
    assert ad["cat"] is None and ad["promo"] and ad["ref_link"]
    # a launchpad post with a referral link stays a launchpad, flagged as promo
    lp = O.extract("OKX Jumpstart: Pump.fun (PUMP)", ("https://www.okx.com/join/12345",), KNOWN | {"PUMP"}, OKX)
    assert lp["cat"] == "launchpad" and lp["promo"] and lp["ticker"] == "PUMP"


def test_tickers_skip_words_stables_and_btc_for_alts():
    assert O.tickers("NOT going to happen, but #NOT and LINK", KNOWN) == ["NOT", "LINK"]       # bare NOT is a word, #NOT is a tag
    assert O.tickers("#BTCUSDT и USDT, ETF, SEC", KNOWN) == ["BTC"]
    assert O.extract("#BTC и #ETH", (), KNOWN, OKX)["ticker"] == "ETH"                        # BTC is the yardstick, not a mention
    assert O.tickers("#новости #крипта", KNOWN) == []


def _posts(*rows):
    return [{"id": i, "text": tx, "t": t} for i, tx, t in rows]


def test_first_run_lists_but_prices_only_fresh_posts_then_resolves_vs_btc():
    conn = sqlite3.connect(":memory:")
    titles = {"chan": "Chan"}
    px = {"BTC": 100_000.0, "XPL": 1.0, "SOL": 200.0}
    page1 = _posts((1, "Binance Launchpool: Plasma (XPL)", T0 - H))
    d = O.step(conn, {"chan": page1}, titles, px, OKX, KNOWN, T0)
    assert len(d["items"]) == 1 and d["items"][0]["tracked"] is False          # first run: seen too late to price
    page2 = page1 + _posts((2, "Bybit залистит #SOL", T0 + 2 * H - 10 * 60_000), (3, "Аирдроп от #XPL — чекер", T0 - 5 * H),
                           (4, "Регистрируйся по ссылке, бонус до 1000$", T0 + 2 * H))
    d = O.step(conn, {"chan": page2}, titles, px, OKX, KNOWN, T0 + 2 * H)
    it = {x["url"].rsplit("/", 1)[1]: x for x in d["items"]}
    assert set(it) == {"1", "2", "3"}                                            # the ad is counted, not listed
    assert it["2"]["tracked"] is True and it["3"]["tracked"] is False           # post 3 was 7 h old when first seen
    assert all("text" not in x for x in d["items"])
    # 1 day later: SOL +10 %, BTC +2 % → +8 % vs BTC
    d = O.step(conn, {"chan": page2}, titles, {"BTC": 102_000.0, "XPL": 1.0, "SOL": 220.0}, OKX, KNOWN, T0 + 2 * H + DAY)
    x = next(x for x in d["items"] if x["url"].endswith("/2"))
    assert x["r1_pct"] == 10.0 and x["vs_btc_1d_pct"] == 8.0 and x["vs_btc_7d_pct"] is None
    ch = d["channels"]["chan"]
    assert ch["tracked"] == 1 and ch["vs_btc"][0]["days"] == 1 and ch["vs_btc"][0]["median_vs_btc_pct"] == 8.0 and ch["vs_btc"][0]["beat_btc_pct"] == 100
    assert ch["posts_30d"] == 4 and ch["promo_pct"] == 25
    # 7 and 30 days
    d = O.step(conn, {"chan": page2}, titles, {"BTC": 90_000.0, "SOL": 180.0}, OKX, KNOWN, T0 + 2 * H + 7 * DAY)
    x = next(x for x in d["items"] if x["url"].endswith("/2"))
    assert x["r7_pct"] == -10.0 and x["vs_btc_7d_pct"] == 0.0
    d = O.step(conn, {"chan": page2}, titles, {"BTC": 90_000.0, "SOL": 300.0}, OKX, KNOWN, T0 + 2 * H + 30 * DAY)
    x = next(x for x in d["items"] if x["url"].endswith("/2"))
    assert x["r30_pct"] == 50.0 and x["vs_btc_30d_pct"] == 60.0
    assert d["label_ru"].startswith("Информация, не сигнал")


def test_run_reads_each_channel_once_and_writes_both_files(tmp_path):
    (tmp_path / "markets.json").write_text(json.dumps({"top": [{"symbol": s, "price": p} for s, p in
                                                              (("BTC", 100_000), ("ETH", 2_500), ("SOL", 200), ("XRP", 2), ("DOGE", 0.2), ("ADA", 0.5))]}))
    calls = []

    def fetch(ch):
        calls.append(ch)
        return [{"id": 5, "text": "OKX Jumpstart: Plasma (XPL)", "t": T0}]
    conn = sqlite3.connect(":memory:")
    d = W.run(conn, str(tmp_path), T0, fetch, prices={"BTC": 100_000.0, "XPL": 1.0}, okx={"XPL", "BTC"})
    assert sorted(calls) == sorted(W.CHANNELS) and set(W.CHANNELS) >= {"Mr_Trader_Pro", "doubletop", "trade001k", "trade_ultra"}
    assert "opps_error" not in d
    o = json.loads((tmp_path / "channel_opps.json").read_text())
    assert len(o["items"]) == len(W.CHANNELS) and {x["cat"] for x in o["items"]} == {"launchpad"}
    assert "text" not in json.dumps(o["items"]) and json.loads((tmp_path / "channel_watch.json").read_text())["channels"]


def test_vip_opportunities_carry_the_channels_block_as_information():
    chop = {"asof": T0, "channels": {"doubletop": {"title": "DoubleTop", "link": "https://t.me/doubletop", "per_day": 12.5, "posts_30d": 300,
                                                   "promo_pct": 20, "tracked": 3, "vs_btc": [{"days": 7, "n": 3, "median_vs_btc_pct": -1.0}],
                                                   "audit": {"summary_ru": "за 200 дней преимущества нет"}}},
            "items": [{"t": T0, "channel": "doubletop", "title": "DoubleTop", "url": "https://t.me/doubletop/7", "cat": "listing",
                       "project": None, "ticker": "SOL", "venue": "bybit", "okx": True, "promo": False, "vs_btc_7d_pct": None}]}
    chw = {"channels": {"doubletop": {"forecasts": 4, "open": 1, "hit_rate_pct": 50, "always_up_pct": 75}}}
    b = vf.channels_block(chop, chw, T0 + H)
    assert b["label_ru"] == "Информация, не сигнал; ранние проекты — высокий риск."
    lst = next(c for c in b["by_cat"] if c["cat"] == "listing")
    assert lst["n_7d"] == 1 and lst["items"][0]["ticker"] == "SOL"
    tr = b["track"][0]
    assert tr["live_forecasts"]["hit_rate_pct"] == 50 and tr["live_forecasts"]["always_up_pct"] == 75 and tr["audit"]["summary_ru"]
    assert vf.channels_block({}, {}, T0) is None
    d = vf.opportunities({}, {}, {}, {}, {}, {}, {}, {}, T0, b)
    assert d["from_channels"] is b and all(i.get("key") != "channels" for i in d["items"])      # never one of the earning methods
