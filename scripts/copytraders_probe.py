"""Which copy-trading and trading-bot leaderboards can we read from a GitHub runner? (19.10.2026, owner: «не только OKX — Binance,
Bybit и другие платформы … весь крипторынок нужно исследовать»). Tries the public web endpoints the exchanges' own leaderboard
pages use (no keys, no login) and prints, per exchange: HTTP status, the top-level keys and one row's keys — enough to build the
analysis on what answers. Runner IPs are in the US; a 403 / 451 here means a geo-block, not a missing endpoint. Stdlib only."""
import json
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Content-Type": "application/json", "clienttype": "web", "lang": "en"}


def note(title, msg):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::notice title={title}::{m}", flush=True)


def call(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=UA,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            txt = r.read().decode("utf-8", "replace")
            return r.status, txt
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:200]


def shape(txt):
    try:
        d = json.loads(txt)
    except Exception:  # noqa: BLE001
        return {"not_json": txt[:160]}

    def first_list(x, depth=0):
        if isinstance(x, list) and x and isinstance(x[0], dict):
            return x
        if isinstance(x, dict) and depth < 4:
            for v in x.values():
                r = first_list(v, depth + 1)
                if r:
                    return r
        return None
    rows = first_list(d)
    return {"top": sorted(d.keys())[:12] if isinstance(d, dict) else type(d).__name__, "code": d.get("code") if isinstance(d, dict) else None,
            "msg": (d.get("msg") or d.get("message") or "")[:80] if isinstance(d, dict) else None,
            "rows": len(rows) if rows else 0, "row_keys": sorted(rows[0].keys())[:40] if rows else None,
            "row0": {k: rows[0][k] for k in list(rows[0])[:14]} if rows else None}


T = int(time.time() * 1000)
CANDIDATES = {
    "binance copy-trading list": ("https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/home-page/query-list",
                                  {"pageNumber": 1, "pageSize": 18, "timeRange": "90D", "dataType": "ROI", "favoriteOnly": False,
                                   "hideFull": False, "nickname": "", "order": "DESC", "userAsset": 0, "portfolioType": "PUBLIC"}),
    "binance bots (strategy square)": ("https://www.binance.com/bapi/futures/v1/public/future/common/strategy/landing-page/queryTopStrategy",
                                       {"page": 1, "rows": 20, "direction": "", "strategyType": 2, "symbol": "", "zone": "", "runningTime": "",
                                        "sort": "roi"}),
    "bybit copy-trading list": (f"https://api2.bybit.com/fapi/beehive/public/v1/common/dynamic-leader-list?timeStamp={T}&pageNo=1&pageSize=16"
                                "&dataDuration=DATA_DURATION_NINETY_DAY&sortField=LEADER_SORT_FIELD_SORT_ROI", None),
    "bitget copy-trading list": ("https://www.bitget.com/v1/trigger/trace/public/traderRankList",
                                 {"pageNo": 1, "pageSize": 20, "sortRule": 2, "sortFlag": "desc", "languageType": 0}),
    "bitget copy-trading v2": ("https://www.bitget.com/v1/copy/public/trader/list",
                               {"pageNo": 1, "pageSize": 20, "sortRule": 1, "languageType": 0}),
    "mexc copy-trading list": ("https://futures.mexc.com/copyFutures/api/v1/traders/v2?condition=%5B%5D&limit=20&orderBy=ROI&page=1", None),
    "bingx copy-trading list": ("https://bingx.com/api/copytrade/v1/trader/rank?pageId=0&pageSize=20&sortType=1", None),
    "okx copy-trading (official)": ("https://www.okx.com/api/v5/copytrading/public-lead-traders?instType=SWAP&limit=5", None),
    "okx eea host": ("https://my.okx.com/api/v5/copytrading/public-lead-traders?instType=SWAP&limit=5", None),
    "okx grid bots (official)": ("https://www.okx.com/api/v5/tradingBot/public/rubik-stat?algoOrdType=contract_grid", None),
}


def main():
    res = {}
    for name, (url, body) in CANDIDATES.items():
        s, txt = call(url, body)
        res[name] = {"status": s, **(shape(txt) if s == 200 else {"body": txt[:160]})}
        time.sleep(0.6)
    items = list(res.items())
    for i in range(0, len(items), 2):
        note(f"probe · part {i // 2 + 1}", json.dumps(dict(items[i:i + 2]), ensure_ascii=False)[:6000])


if __name__ == "__main__":
    main()
