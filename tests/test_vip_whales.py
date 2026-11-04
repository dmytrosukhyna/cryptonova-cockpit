"""VIP «Киты» (04.11): crypto watchlist from the whale study's profiles, SEC 13F / ARK / Congress stock whales and the on-chain
«Куда инвестируют киты» — parsers and rules on recorded-shape answers, graceful «источник недоступен», hourly / daily cadence."""
import json
import os
import pathlib
import sqlite3

from cnq.agents import smart_money as SM
from cnq.agents import stock_whales as SW
from cnq.agents import vip_whales as VW

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = 1793810000000
DAY, H = 86_400_000, 3_600_000
SW._sleep = SM._sleep = lambda s: None

INFOTABLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip><value>1000000</value>
    <shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><investmentDiscretion>SOLE</investmentDiscretion></infoTable>
  <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip><value>500000</value>
    <shrsOrPrnAmt><sshPrnamt>2500</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><investmentDiscretion>DFND</investmentDiscretion></infoTable>
  <infoTable><nameOfIssuer>TESLA INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>88160R101</cusip><value>300000</value>
    <shrsOrPrnAmt><sshPrnamt>1000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><putCall>Put</putCall></infoTable>
</informationTable>"""


# ------------------------------------------------------------------------------------------------------------ 13F
def test_infotable_is_parsed_with_namespace_and_aggregated_per_cusip_without_options():
    rows = SW.parse_infotable(INFOTABLE)
    assert len(rows) == 3 and rows[2]["put_call"] == "Put"
    agg = SW.aggregate(rows)
    assert agg == {"037833100": ["APPLE INC", "COM", 1500000, 7500.0]}
    assert SW.options_count(rows) == 1
    assert SW.parse_infotable(INFOTABLE, dollars=False)[0]["value"] == 1e9          # before 2023: thousands of dollars


def test_filings_newest_originals_one_per_quarter():
    sub = {"filings": {"recent": {"form": ["13F-HR", "13F-HR/A", "4", "13F-HR", "13F-HR", "13F-HR"],
                                  "accessionNumber": ["a1", "a2", "x", "a3", "a4", "a5"],
                                  "filingDate": ["2026-11-01", "2026-11-02", "2026-10-01", "2026-08-14", "2026-05-15", "2026-02-14"],
                                  "reportDate": ["2026-09-30", "2026-09-30", "", "2026-06-30", "2026-03-31", "2025-12-31"]}}}
    fl = SW.filings_13f(sub)
    assert [f["acc"] for f in fl] == ["a1", "a3", "a4"]


def test_infotable_name_prefers_info_table_xml():
    idx = {"directory": {"item": [{"name": "primary_doc.xml", "size": "9000"}, {"name": "50240.xml", "size": "80000"},
                                  {"name": "0001.txt"}]}}
    assert SW.infotable_name(idx) == "50240.xml"
    idx["directory"]["item"].append({"name": "form13fInfoTable.xml", "size": "100"})
    assert SW.infotable_name(idx) == "form13fInfoTable.xml"
    assert SW.infotable_name({"directory": {"item": [{"name": "primary_doc.xml"}]}}) is None


def test_quarter_diff_new_add_cut_exit_and_consensus():
    prev = {"A": ["AAA", "COM", 100, 10], "B": ["BBB", "COM", 100, 10], "C": ["CCC", "COM", 100, 10], "D": ["DDD", "COM", 50, 5]}
    cur = {"A": ["AAA", "COM", 200, 20], "B": ["BBB", "COM", 50, 5], "C": ["CCC", "COM", 102, 10.2], "N": ["NNN", "COM", 70, 7]}
    d = SW.diff(cur, prev)
    assert [r["cusip"] for r in d["new"]] == ["N"]
    assert [r["cusip"] for r in d["add"]] == ["A"] and d["add"][0]["chg_pct"] == 100.0 and d["add"][0]["usd"] == 100
    assert [r["cusip"] for r in d["cut"]] == ["B"]
    assert [r["cusip"] for r in d["exit"]] == ["D"]                                  # C moved 2 % — no move
    top, total = SW.top_holdings(cur, prev, 2)
    assert total == 422 and top[0]["cusip"] == "A" and top[0]["chg_pct"] == 100.0
    funds = [{"key": "f1", "name_ru": "F1", "diffs": {"2026-09-30": d}},
             {"key": "f2", "name_ru": "F2", "diffs": {"2026-09-30": {"new": [{"cusip": "N", "name": "NNN", "usd": 10}], "add": [], "cut": [], "exit": []}}},
             {"key": "f3", "name_ru": "F3", "diffs": {"2026-06-30": {"new": [{"cusip": "A", "name": "AAA", "usd": 5}], "add": [], "cut": [], "exit": []}}}]
    c = SW.consensus(funds, "2026-09-30")
    assert [(x["cusip"], x["n"]) for x in c] == [("N", 2)]
    assert SW.period_label("2026-09-30") == "3 кв. 2026"


def _sec_get(fail_cik=None):
    sub = {"name": "TEST FUND", "filings": {"recent": {"form": ["13F-HR", "13F-HR"], "accessionNumber": ["0001-26-000002", "0001-26-000001"],
                                                         "filingDate": ["2026-11-01", "2026-08-10"], "reportDate": ["2026-09-30", "2026-06-30"]}}}
    calls = []

    def get(url, body=None, headers=None, timeout=30):
        calls.append(url)
        if fail_cik and f"CIK{fail_cik:010d}" in url:
            raise OSError("HTTP Error 403: Forbidden")
        if "submissions" in url:                           # accession numbers are unique per filer
            cik = url.split("CIK")[1][:10]
            s_ = json.loads(json.dumps(sub))
            s_["filings"]["recent"]["accessionNumber"] = [f"{cik}-26-000002", f"{cik}-26-000001"]
            return json.dumps(s_).encode()
        if url.endswith("index.json"):
            return json.dumps({"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "info.xml", "size": "10"}]}}).encode()
        if url.endswith("info.xml"):
            return INFOTABLE if "000002" in url else INFOTABLE.replace(b"<value>500000</value>", b"<value>1</value>").replace(b"<sshPrnamt>2500</sshPrnamt>", b"<sshPrnamt>1</sshPrnamt>")
        if "openfigi" in url:
            return json.dumps([{"data": [{"ticker": "AAPL", "exchCode": "US"}]}] * len(json.loads(body))).encode()
        if "ark-funds" in url or "stock-watcher" in url:
            raise OSError("HTTP Error 403: Forbidden")
        raise AssertionError(url)
    get.calls = calls
    return get


def test_stock_run_builds_funds_consensus_tickers_and_degrades_per_source():
    conn = sqlite3.connect(":memory:")
    get = _sec_get(fail_cik=SW.FUNDS[1]["cik"])
    d = SW.run(conn, None, NOW, get)
    by = {f["key"]: f for f in d["funds"]}
    b = by["berkshire"]
    assert b["period"] == "2026-09-30" and b["prev_period"] == "2026-06-30" and b["top"][0]["ticker"] == "AAPL"
    assert b["moves_n"]["add"] == 1 and b["total_value"] == 1500000
    assert by["scion"].get("period") is None and "источник недоступен" in by["scion"]["error_ru"]
    assert d["consensus"] and d["consensus"][0]["items"][0]["n"] == 9                    # 9 funds «added» the same stock
    assert d["sources"]["sec"]["ok"] and d["sources"]["ark"]["ok"] is False and d["congress"]["available"] is False
    assert "45 дней" in d["delay_ru"]
    json.dumps(d)
    # the next run within the day: nothing due → None (the old file stays); a day later only the submissions are fetched again
    assert SW.run(conn, d, NOW + H, get) is None
    n0 = len(get.calls)
    d2 = SW.run(conn, d, NOW + DAY, get)
    assert d2 is not None and not any(u.endswith("info.xml") for u in get.calls[n0:])


def test_sec_user_agent_is_generic():
    assert "@" not in SW.UA and SW.UA.startswith("CryptoNovaResearch/1.0")


# ------------------------------------------------------------------------------------------------------------ ARK / Congress
ARK_CSV = """date,fund,company,ticker,cusip,shares,"market value ($)","weight (%)"
11/03/2026,ARKK,TESLA INC,TSLA,88160R101,"1,000,000","$300,000,000.00",10.00%
11/03/2026,ARKK,ROKU INC,ROKU,77543R102,"2,000,000","$100,000,000.00",5.00%
11/03/2026,ARKK,COINBASE,COIN,19260Q107,"500,000","$100,000,000.00",5.00%
11/03/2026,ARKK,NEWCO,NEW,000000000,"10,000","$1,000,000.00",0.10%
"Investors should carefully consider..."
"""


def test_ark_csv_and_daily_moves_net_of_fund_flows():
    date, rows = SW.parse_ark_csv(ARK_CSV.encode())
    assert date == "2026-11-03" and len(rows) == 4 and rows[0]["shares"] == 1_000_000 and rows[0]["weight"] == 10.0
    prev = [dict(r) for r in rows if r["ticker"] != "NEW"] + [{"ticker": "OLD", "company": "OLDCO", "cusip": "1", "shares": 100, "value": 5000, "weight": 0.1}]
    for r in prev:                         # the whole fund was 1 % smaller yesterday; ROKU additionally +10 % today
        r["shares"] = r["shares"] / 1.01 if r["ticker"] != "ROKU" else r["shares"] / 1.01 / 1.10
    mv = {m["ticker"]: m for m in SW.ark_moves(rows, prev, "ARKK")}
    assert set(mv) == {"ROKU", "NEW", "OLD"}
    assert mv["ROKU"]["action"] == "buy" and abs(mv["ROKU"]["chg_pct"] - 10.0) < 0.2
    assert mv["NEW"]["action"] == "new" and mv["OLD"]["action"] == "exit"


def test_congress_down_is_reported_not_raised():
    def get(url, **kw):
        raise OSError("HTTP Error 403: Forbidden")
    r = SW.congress_refresh(get, NOW)
    assert r["available"] is False and "не отвечает" in r["note_ru"]
    rows = SW.parse_congress([{"disclosure_date": "11/01/2026", "ticker": "NVDA", "representative": "Rep. X", "type": "purchase",
                               "amount": "$1,001 - $15,000"}, {"disclosure_date": "01/01/2020", "ticker": "OLD"}], "house", NOW)
    assert [r_["ticker"] for r_ in rows] == ["NVDA"]


# ------------------------------------------------------------------------------------------------------------ smart money
W1, W2, W3 = "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40
TOK, SPAM = "0x" + "a" * 40, "0x" + "b" * 40
EX = "0x" + "e" * 40


def _iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _tr(frm, to, tok, amount, t, sym="EARLY"):
    return {"from": {"hash": frm}, "to": {"hash": to}, "timestamp": _iso(t), "total": {"decimals": "18", "value": str(int(amount * 1e18))},
            "token": {"address_hash": tok, "symbol": sym, "name": sym, "decimals": "18", "type": "ERC-20"}}


CFG = {"wallets": [{"label": "Fund One", "kind": "фонд", "address": W1, "chains": ["eth"], "source": "https://etherscan.io/address/" + W1},
                   {"label": "Trader Two", "kind": "известный трейдер", "address": W2, "chains": ["eth"], "source": "x"},
                   {"label": "MM Three", "kind": "маркетмейкер", "address": W3, "chains": ["eth"], "source": "x"}],
       "exchanges": {"eth": [{"label": "Binance 14", "address": EX}], "btc": [{"label": "b", "address": "bc1x"}], "sol": [{"label": "s", "address": "So1"}]}}


def _sm_get(down=()):
    def get(url, body=None, headers=None, timeout=20):
        for d in down:
            if d in url:
                raise OSError("HTTP Error 503")
        if "blockscout.com/api/v2/addresses/" in url and "token-transfers" in url:
            w = url.split("/addresses/")[1].split("/")[0]
            t = NOW - 2 * DAY
            items = {W1: [_tr(EX, W1, TOK, 1000, t), _tr("0xpool", W1, SPAM, 1e6, t, "SCAM")],
                     W2: [_tr("0xpool", W2, TOK, 600, t + H), _tr(W2, "0xdead", TOK, 100, t + 2 * H)],
                     W3: [_tr("0xpool", W3, TOK, 5000, t), _tr(W3, "0xother", TOK, 5000, t + H)]}.get(w, [])
            return {"items": items, "next_page_params": None}
        if "coin-balance-history-by-day" in url:
            return {"items": [{"date": "2026-11-04", "value": str(int(1000e18))}, {"date": "2026-11-03", "value": str(int(1100e18))},
                              {"date": "2026-10-28", "value": str(int(900e18))}]}
        if "blockscout.com/api/v2/addresses/" in url:
            return {"public_tags": [{"display_name": "Fund One: Hot"}], "ens_domain_name": None}
        if "dexscreener" in url:
            assert "/tokens/v1/ethereum/" in url
            return ([{"chainId": "ethereum", "baseToken": {"address": TOK}, "priceUsd": "10", "liquidity": {"usd": 80000}, "fdv": 50e6,
                               "marketCap": 20e6, "pairAddress": "0xpair", "pairCreatedAt": NOW - 10 * DAY, "url": "https://dexscreener.com/x"},
                              {"chainId": "ethereum", "baseToken": {"address": SPAM}, "priceUsd": "1", "liquidity": {"usd": 50}}])
        if "gopluslabs" in url:
            return {"result": {TOK: {"is_honeypot": "0", "buy_tax": "0", "sell_tax": "0.1", "holder_count": "500",
                                     "holders": [{"percent": "0.4", "is_locked": 0}, {"percent": "0.2", "is_locked": 1}, {"percent": "0.15"}]}}}
        if "geckoterminal" in url:
            ts = (NOW - 2 * DAY) // 1000
            return {"data": {"attributes": {"ohlcv_list": [[ts + 3600, 8, 9, 7, 8.5, 1], [ts - 3600, 4, 5, 4, 5, 1]]}}}
        if "hyperliquid" in url:
            if body and body.get("type") == "allMids":
                return {"BTC": "80000", "ETH": "2500", "SOL": "100"}
            return [{"tokens": [{"name": "USDC", "index": 0}], "universe": []}, []]
        if "mempool.space" in url or "blockstream" in url:
            return {"chain_stats": {"funded_txo_sum": 300_000_000_000, "spent_txo_sum": 100_000_000_000}}
        if "solana" in url:
            return {"result": {"value": [{"lamports": 5_000_000_000_000}]}}
        raise AssertionError(url)
    return get


def test_smart_money_signal_needs_two_wallets_net_inflow_and_filters_spam(tmp_path):
    conn = sqlite3.connect(":memory:")
    d = SM.run(conn, CFG, None, None, NOW, _sm_get(), data_dir=str(tmp_path))
    assert [s["symbol"] for s in d["signals"]] == ["EARLY"]
    s = d["signals"][0]
    assert s["n"] == 2 and {b["label"] for b in s["buyers"]} == {"Fund One", "Trader Two"}      # MM Three: net 0 → not a buyer
    fund = next(b for b in s["buyers"] if b["label"] == "Fund One")
    assert fund["usd"] == 10000 and fund["from_exchange"] is True
    assert s["chg_since_pct"] == 100.0                     # price 10 now, 5 at the first buy (candle before it)
    fl = " ".join(s["flags"])
    assert "мало ликвидности" in fl and "новый токен" in fl and "налог" in fl and "топ-10" in fl
    assert s["early"] is True and d["dropped"]["spam"] == 1
    assert d["flows"]["ETH"]["d1"] == -100 and d["flows"]["ETH"]["d7"] == 100 and d["flows"]["BTC"]["total"] == 2000
    assert d["flows"]["BTC"]["d1"] is None                 # BTC/SOL history starts with the first snapshot
    assert "не финансовый совет" in d["risk_ru"].lower()
    sig = SM.signal_feed(d)
    assert sig["info_only"] is True and sig["real_money"] is False and sig["tokens"][0]["symbol"] == "EARLY"
    json.dumps(d)
    lines = (tmp_path / "signals.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["key"] == "eth:" + TOK
    assert SM.run(conn, CFG, None, d, NOW + 10 * 60_000, _sm_get(), data_dir=str(tmp_path)) is None      # hourly


def test_smart_money_sources_down_show_unavailable_and_never_raise(tmp_path):
    conn = sqlite3.connect(":memory:")
    d = SM.run(conn, CFG, None, None, NOW, _sm_get(down=("blockscout", "mempool", "blockstream", "solana")), data_dir=str(tmp_path))
    assert d["signals"] == [] and d["sources"]["blockscout"]["status_ru"] == "источник недоступен"
    assert d["flows"]["BTC"]["error"] == "источник недоступен" and d["flows"]["SOL"]["error"] == "источник недоступен"
    assert all(c.get("error") == "источник недоступен" for w in d["wallets"] for c in w["chains"])


def test_forward_returns_recorded_once_per_horizon(tmp_path):
    SM.append_jsonl(str(tmp_path / "signals.jsonl"), [{"day": "2026-10-01", "t": NOW - 8 * DAY, "key": "eth:x", "src": "evm", "symbol": "X", "price": 2.0}])
    assert SM.record_outcomes(str(tmp_path), NOW, {"eth:x": 3.0}) == 2                    # 1 d and 7 d due, 30 d not yet
    assert SM.record_outcomes(str(tmp_path), NOW, {"eth:x": 3.0}) == 0
    tr = SM.track(str(tmp_path))
    assert tr["stats"]["7"]["n"] == 1 and tr["stats"]["7"]["avg"] == 50.0 and tr["stats"]["30"]["n"] == 0
    # 1 d was measured 7 days late — kept in the file, left out of the statistics
    assert tr["stats"]["1"]["n"] == 0


def test_hl_spot_accumulation_needs_a_baseline_and_two_whales():
    conn = sqlite3.connect(":memory:")
    SM.ensure_schema(conn)
    a, b, c = "0xa", "0xb", "0xc"
    t0 = NOW - 2 * DAY
    conn.executemany("INSERT INTO sm_hl VALUES (?,?,?,?,?)", [(a, "USDC", t0, 1, 0), (b, "HYPE", t0, 100, 3000), (c, "USDC", t0, 1, 0)])
    conn.executemany("INSERT INTO sm_hl VALUES (?,?,?,?,?)", [(a, "HYPE", NOW, 1000, 30000), (b, "HYPE", NOW, 500, 15000), (c, "PURR", NOW, 1e6, 1e5)])
    acc, held = SM.hl_accum(conn, NOW, {"HYPE": 40.0, "PURR": 0.2, "USDC": 1.0}, {a: "A", b: "B"})
    assert [(x["coin"], x["n"]) for x in acc] == [("HYPE", 2)]
    assert {x["label"]: x["new"] for x in acc[0]["buyers"]} == {"A": True, "B": False}
    assert acc[0]["buyers"][0]["chg_pct"] == 33.3
    assert [h["coin"] for h in held] == ["HYPE"]
    assert SM.hl_prices([{"tokens": [{"name": "USDC", "index": 0}, {"name": "HYPE", "index": 150}],
                          "universe": [{"tokens": [150, 0], "index": 0, "name": "@107"}]}, [{"markPx": "41.5"}]]) == {"USDC": 1.0, "HYPE": 41.5}


def test_wallet_config_entries_cite_a_public_source():
    cfg = json.loads((ROOT / "config" / "smart_money_wallets.json").read_text(encoding="utf-8"))
    for w in cfg["wallets"]:
        assert w["source"].startswith("https://") and w["source_note"] and w["kind"]
        assert w["address"] == w["address"].lower() and len(w["address"]) == 42
    for chain in ("eth", "btc", "sol"):
        assert all(e["source"].startswith("https://") for e in cfg["exchanges"][chain])
    assert SM.confirmed("Jump Trading", ["Jump Trading 4"]) and not SM.confirmed("Jump Trading", ["Binance 8"])


# ------------------------------------------------------------------------------------------------------------ crypto watchlist
def _prof(addr, src, eligible, why=(), pf=3.0):
    return {"addr": addr, "src": src, "name": addr[:6], "short": addr[:6], "eligible": eligible, "excluded_ru": list(why), "win": 0.75, "pf": pf,
            "decisions": 30, "hold_med_h": 12, "max_dd_usd": 1000, "pnl_60d": 5000, "pnl_30d": 2000,
            "positions": [{"coin": "BTC", "side": "LONG", "entry": 80000, "lev": 10, "upnl": 50, "notional": 1000}, {"coin": "", "upnl": -5}],
            "recent": [{"coin": "ETH", "side": "SHORT", "open_t": 1, "close_t": 2, "pnl": 10, "ret_pct": 1.0}] * 12}


def test_crypto_watch_status_reasons_copies_and_links():
    profs = [_prof("okx:AAA", "okx", True), _prof("0x" + "c" * 40, "hl", False, ["мало решений за 60 дней (7, нужно 20)"]),
             _prof("bn:123", "bn", True, pf=2.5)]
    top = {"asof": NOW, "following": [{"addr": "okx:AAA"}], "follow_dropped": [{"addr": "bn:999", "name": "Gone", "src": "bn", "why_ru": ["держит слишком коротко"]}],
           "top": [{"addr": "okx:AAA", "rank": 1}, {"addr": "bn:123", "rank": 2}],
           "desk": {"pnl_usd": 12.5, "open": [{"whale": "okx:AAA", "coin": "BTC", "side": "LONG", "upnl": 3}],
                    "closed": [{"whale": "okx:AAA", "coin": "SOL", "pnl": 7.0}], "by_whale": [{"whale": "okx:AAA", "trades": 1, "wins": 1, "pnl_usd": 7.0}]}}
    d = VW.crypto_watch(profs, top)
    by = {w["addr"]: w for w in d["whales"]}
    assert d["counts"] == {"followed": 1, "candidate": 2, "dropped": 1}
    assert [w["status"] for w in d["whales"]] == ["followed", "candidate", "candidate", "dropped"]
    assert by["okx:AAA"]["copies"]["trades"] == 1 and by["okx:AAA"]["copies"]["open"][0]["coin"] == "BTC"
    assert by["okx:AAA"]["link"].startswith("https://www.okx.com/copy-trading/account/AAA")
    assert by["bn:123"]["link"].endswith("/lead-details/123") and "место 2" in by["bn:123"]["why_ru"][0]
    assert by["0x" + "c" * 40]["why_ru"] == ["мало решений за 60 дней (7, нужно 20)"]
    assert by["bn:999"]["not_studied"] and by["bn:999"]["why_ru"] == ["держит слишком коротко"]
    assert len(by["okx:AAA"]["recent"]) == 10 and by["okx:AAA"]["positions_hidden"] == 1


def test_max_drawdown_of_decisions():
    tr = [{"pnl": 100, "close_t": 1}, {"pnl": -30, "close_t": 2}, {"pnl": -50, "close_t": 3}, {"pnl": 200, "close_t": 4}, {"pnl": -10, "close_t": 0, "open": True}]
    assert VW.max_dd(tr) == 80
    assert VW.max_dd([]) is None


# ------------------------------------------------------------------------------------------------------------ wiring
def test_pipeline_runs_and_commits_the_new_feeds():
    wf = (ROOT / ".github" / "workflows" / "site.yml").read_text(encoding="utf-8")
    assert "python -m cnq.cli stockwhales --out web" in wf and "python -m cnq.cli smartmoney --out web" in wf
    for f in ("web/whale_watch.json", "web/stock_whales.json", "web/smart_money.json", "web/smart_money_signal.json"):
        assert f in wf
    assert "data/whales_ext.sqlite" in wf and "git add data/smart_money" in wf
    assert "!data/smart_money/" in (ROOT / ".gitignore").read_text()


def test_panel_has_the_whales_route_and_the_spot_card():
    html = (ROOT / "web" / "vip.html").read_text(encoding="utf-8")
    assert 'src="vip/whales.js"' in html and 'id="i-whales"' in html
    core = (ROOT / "web" / "vip" / "core.js").read_text(encoding="utf-8")
    assert "id: 'whales'" in core
    js = (ROOT / "web" / "vip" / "whales.js").read_text(encoding="utf-8")
    assert "whale_watch.json" in js and "stock_whales.json" in js and "smart_money.json" in js
    assert "не финансовый совет, высокий риск" in js and "источник недоступен" in js
    assert "V.smartMoneyCard" in (ROOT / "web" / "vip" / "spot.js").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------------------ token watchlist (04.11 22:38)
from cnq.agents import smart_watch as SWT


def _wl_get(items_by_wallet=None, price="10", liq=80000, mine=None, gp_tax="0.1"):
    base = _sm_get()

    def get(url, body=None, headers=None, timeout=20):
        if "token-transfers" in url and items_by_wallet is not None:
            w = url.split("/addresses/")[1].split("/")[0]
            return {"items": items_by_wallet.get(w, []), "next_page_params": None}
        if "/api/v2/tokens/" in url and "/transfers" in url:
            if mine is None:
                raise OSError("HTTP Error 503")
            return {"items": mine, "next_page_params": None}
        if "dexscreener" in url:
            return [{"chainId": "ethereum", "baseToken": {"address": TOK}, "priceUsd": price, "liquidity": {"usd": liq}, "fdv": 50e6,
                     "marketCap": 20e6, "pairAddress": "0xpair", "pairCreatedAt": NOW - 60 * DAY, "url": "https://dexscreener.com/x"}]
        if "gopluslabs" in url:
            return {"result": {TOK: {"is_honeypot": "0", "buy_tax": "0", "sell_tax": gp_tax, "holders": []}}}
        return base(url, body, headers, timeout)
    return get


def _run(conn, tmp_path, now, get):
    return SM.run(conn, CFG, None, None, now, get, data_dir=str(tmp_path), force=True)


def test_every_bought_token_enters_the_watchlist_even_when_it_fails_the_filters(tmp_path):
    conn = sqlite3.connect(":memory:")
    d = _run(conn, tmp_path, NOW, _sm_get())
    w = {t["symbol"]: t for t in d["watch"]["tokens"]}
    assert set(w) == {"EARLY", "SCAM"}                                # SCAM has no pool — still on the list
    assert w["EARLY"]["status"] == "накопление" and w["EARLY"]["n"] == 2
    assert {b["label"] for b in w["EARLY"]["buyers"]} == {"Fund One", "Trader Two"}
    assert SWT.NO_POOL in w["SCAM"]["flags"] and w["SCAM"]["status"] in ("новая", "под наблюдением")
    assert w["EARLY"]["first_price"] == 5 and w["EARLY"]["chg_pct"] == 100.0
    assert any("добавлена" in e["text"] for e in w["EARLY"]["events"])
    assert (tmp_path / "watchlist").is_dir() and len(list((tmp_path / "watchlist").iterdir())) == 1
    json.dumps(d)


def test_seller_status_and_change_history(tmp_path):
    conn = sqlite3.connect(":memory:")
    _run(conn, tmp_path, NOW, _sm_get())
    t = NOW + 3 * H
    items = {W1: [_tr(EX, W1, TOK, 1000, NOW - 2 * DAY)], W2: [_tr("0xpool", W2, TOK, 100, NOW - 2 * DAY), _tr(W2, "0xpool", TOK, 600, t - H)]}
    d = _run(conn, tmp_path, t, _wl_get(items))
    e = next(x for x in d["watch"]["tokens"] if x["symbol"] == "EARLY")
    assert e["status"] == "продают" and e["n"] == 1
    txt = [ev["text"] for ev in e["events"]]
    assert "продаёт: Trader Two" in txt and "статус: накопление → продают" in txt


def test_risk_clears_later_and_is_recorded(tmp_path):
    conn = sqlite3.connect(":memory:")
    one = {W1: [_tr(EX, W1, TOK, 1000, NOW - 2 * DAY)]}
    d = _run(conn, tmp_path, NOW, _wl_get(one, liq=30000))
    e = next(x for x in d["watch"]["tokens"] if x["symbol"] == "EARLY")
    assert any("мало ликвидности" in f for f in e["flags"])
    d = _run(conn, tmp_path, NOW + 2 * DAY, _wl_get(one, liq=500000, gp_tax="0"))
    e = next(x for x in d["watch"]["tokens"] if x["symbol"] == "EARLY")
    assert e["flags"] == [] and e["status"] == "риски снялись"
    txt = [ev["text"] for ev in e["events"]]
    assert any(x.startswith("риск снят: мало ликвидности") for x in txt) and any("риски снялись" in x for x in txt)


def test_forward_returns_and_patterns_from_the_first_smart_buy(tmp_path):
    conn = sqlite3.connect(":memory:")
    _run(conn, tmp_path, NOW, _sm_get())
    d = _run(conn, tmp_path, NOW + 6 * DAY, _wl_get(price="15"))
    e = next(x for x in d["watch"]["tokens"] if x["symbol"] == "EARLY")
    assert e["ret"] == {"1": 100.0, "7": 200.0}                        # first smart buy NOW−2 d at $5: 1 d measured at $10, 7 d at $15
    rows = SM.read_jsonl(str(tmp_path / "token_outcomes.jsonl"))
    assert sorted(r["h"] for r in rows if r["symbol"] == "EARLY") == [1, 7]
    pats = d["watch"]["patterns"]
    g = next(p for p in pats if p["kind"] == "Сколько умных кошельков")
    assert g["h"]["7"]["n"] == 1 and g["h"]["7"]["small"] is True and g["h"]["7"]["hit"] == 1.0


def test_wallet_discovery_needs_two_early_winners():
    conn = sqlite3.connect(":memory:")
    SWT.ensure_schema(conn)
    iso = SM.iso_ms
    a, b = "0x" + "9" * 40, "0x" + "8" * 40
    items = [{"timestamp": _iso(NOW - 3 * DAY), "to": {"hash": a, "is_contract": False}},
             {"timestamp": _iso(NOW - 3 * DAY), "to": {"hash": b, "is_contract": True}},
             {"timestamp": _iso(NOW + H), "to": {"hash": "0x" + "7" * 40, "is_contract": False}}]
    assert SWT.early_buyers(items, NOW - 14 * DAY, NOW, set(), iso) == {a}
    assert SWT.status_of({"first_t": NOW - 5 * DAY, "buyers": {"x": {"sold": True, "sold_t": NOW - 8 * DAY}}, "m": {"pool": "p"}, "last_act": NOW - 8 * DAY}, NOW) == "выбыла"
    assert SWT.status_of({"first_t": NOW - 5 * DAY, "buyers": {"x": {}}, "m": {}, "last_act": NOW}, NOW) == "выбыла"   # no pool for 3+ days
    assert SWT.status_of({"first_t": NOW - H, "buyers": {"x": {}}, "m": {"pool": "p"}, "last_act": NOW}, NOW) == "новая"


def test_auto_found_wallet_is_scanned_and_marked(tmp_path):
    conn = sqlite3.connect(":memory:")
    SWT.ensure_schema(conn)
    a = "0x" + "9" * 40
    conn.execute("INSERT INTO sm_wallet VALUES (?,?,?,?,?,?)", (a, "eth", NOW, 2, json.dumps(["AAA", "BBB"]), "кандидат"))
    early = [{"timestamp": _iso(NOW - 5 * DAY), "to": {"hash": a, "is_contract": False}}]
    d = _run(conn, tmp_path, NOW, _wl_get(mine=early))
    row = next(w for w in d["wallets"] if w["address"] == a)
    assert row["auto_found"] is True and row["score"] == 2 and row["chains"]
    # EARLY rose 100 % from the first smart buy → its early buyers are mined; a is known now, so nothing new from it
    assert conn.execute("SELECT mined FROM sm_tok WHERE symbol='EARLY'").fetchone()[0] == 1


def test_panel_shows_the_token_watchlist_and_patterns():
    js = (ROOT / "web" / "vip" / "whales.js").read_text(encoding="utf-8")
    assert "Список токенов китов" in js and "Что срабатывало" in js and "data-wlst" in js and "мало данных" in js
    for s in SWT.STATUS_ORDER:
        assert s in js
