"""Which copy-trading sources answer from a GitHub runner, with LIVE positions and history? (04.11.2026, owner: «китов находить
и на Bybit, и на других платформах, брать их стратегии, копировать»). Tries the public, no-login endpoints the exchanges' own
copy-trading pages use (leaderboard → one trader's current positions → his closed positions / performance) and writes the
answers (status, keys, two sample rows) to data/research/whale_sources_probe.json. Runner IPs are in the US: a 403 / 451 is a
geo-block. Stdlib only, ~1 request per second."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Content-Type": "application/json", "clienttype": "web", "lang": "en"}
OUT = {}


def call(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=UA,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:200]
    finally:
        time.sleep(1.0)


def first_list(x, depth=0):
    if isinstance(x, list) and x and isinstance(x[0], dict):
        return x
    if isinstance(x, dict) and depth < 5:
        for v in x.values():
            r = first_list(v, depth + 1)
            if r:
                return r
    return None


def probe(name, url, body=None):
    s, txt = call(url, body)
    rec = {"url": url, "body": body, "status": s}
    try:
        d = json.loads(txt)
    except Exception:  # noqa: BLE001
        rec["text"] = txt[:300]
        OUT[name] = rec
        print(name, s, txt[:120], flush=True)
        return None
    rows = first_list(d)
    rec.update(top=list(d.keys())[:15] if isinstance(d, dict) else type(d).__name__,
               code=d.get("code") if isinstance(d, dict) else None,
               msg=str((d.get("msg") or d.get("message") or d.get("retMsg") or "") if isinstance(d, dict) else "")[:120],
               n_rows=len(rows) if rows else 0, rows=rows[:2] if rows else None,
               raw=(json.dumps(d)[:1500] if not rows else None))
    OUT[name] = rec
    print(name, s, rec.get("code"), rec["n_rows"], flush=True)
    return d


def dig(d, *keys):
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        elif isinstance(d, list) and isinstance(k, int) and len(d) > k:
            d = d[k]
        else:
            return None
    return d


def binance():
    bn = "https://www.binance.com/bapi/futures"
    d = probe("binance.list", f"{bn}/v1/friendly/future/copy-trade/home-page/query-list",
              {"pageNumber": 1, "pageSize": 20, "timeRange": "30D", "dataType": "PNL", "favoriteOnly": False, "hideFull": False,
               "nickname": "", "order": "DESC", "userAsset": 0, "portfolioType": "PUBLIC"})
    rows = first_list(d) or []
    pids = [r.get("leadPortfolioId") or r.get("portfolioId") for r in rows if (r.get("leadPortfolioId") or r.get("portfolioId"))]
    shown = 0
    for pid in pids[:6]:
        pos = probe(f"binance.positions.{pid}", f"{bn}/v1/friendly/future/copy-trade/lead-data/positions?portfolioId={pid}")
        if shown == 0:
            probe("binance.detail", f"{bn}/v1/friendly/future/copy-trade/lead-portfolio/detail?portfolioId={pid}")
            probe("binance.performance", f"{bn}/v1/public/future/copy-trade/lead-portfolio/performance?portfolioId={pid}&timeRange=30D")
            probe("binance.position_history", f"{bn}/v1/public/future/copy-trade/lead-portfolio/position-history",
                  {"pageNumber": 1, "pageSize": 50, "portfolioId": pid, "sort": "OPENING"})
            probe("binance.trade_history", f"{bn}/v1/public/future/copy-trade/lead-portfolio/trade-history",
                  {"pageNumber": 1, "pageSize": 50, "portfolioId": pid})
        live = [p for p in (dig(pos, "data") or []) if isinstance(p, dict) and float(p.get("positionAmount") or 0)]
        OUT[f"binance.positions.{pid}"]["live"] = len(live)
        OUT[f"binance.positions.{pid}"]["rows"] = live[:2]
        shown += 1


def bybit():
    paths = {"list": "/fapi/beehive/public/v1/common/dynamic-leader-list?pageNo=1&pageSize=16&dataDuration=DATA_DURATION_THIRTY_DAY"
                     "&sortField=LEADER_SORT_FIELD_SORT_PNL"}
    for host in ("https://api2.bybit.com", "https://api2.bybitglobal.com", "https://www.bybit.com/x-api", "https://www.bybitglobal.com/x-api",
                 "https://api2.bybit.eu", "https://api2.bybit.nl"):
        d = probe(f"bybit.list.{host}", host + paths["list"])
        rows = first_list(d) or []
        mark = next((r.get("leaderMark") for r in rows if r.get("leaderMark")), None)
        if mark:
            from urllib.parse import quote
            m = quote(mark, safe="")
            probe("bybit.income", f"{host}/fapi/beehive/public/v1/common/leader-income?leaderMark={m}")
            probe("bybit.positions", f"{host}/fapi/beehive/public/v1/common/position/list?leaderMark={m}")
            probe("bybit.history", f"{host}/fapi/beehive/public/v1/common/leader-history?leaderMark={m}&pageSize=20&page=1")
            break


def bitget():
    probe("bitget.rank.v1", "https://www.bitget.com/v1/trigger/trace/public/traderRankList",
          {"pageNo": 1, "pageSize": 20, "sortRule": 2, "sortFlag": "desc", "languageType": 0})
    probe("bitget.list.v1copy", "https://www.bitget.com/v1/copy/mix/public/trader/list",
          {"pageNo": 1, "pageSize": 20, "sortRule": 2, "sortFlag": "desc", "languageType": 0})
    probe("bitget.api.v2.traders", "https://api.bitget.com/api/v2/copy/mix-trader/query-traders?limit=5")
    probe("bitget.api.v1.traders", "https://api.bitget.com/api/mix/v1/trace/traderList?pageNo=1&pageSize=10")


def mexc_gate():
    d = probe("mexc.list", "https://futures.mexc.com/copyFutures/api/v1/traders/v2?condition=%5B%5D&limit=10&orderBy=PNL&page=1")
    uid = next((r.get("uid") for r in (first_list(d) or []) if r.get("uid")), None)
    if uid:
        probe("mexc.orders", f"https://futures.mexc.com/copyFutures/api/v1/trader/orders/v2?limit=20&page=1&uid={uid}")
        probe("mexc.history", f"https://futures.mexc.com/copyFutures/api/v1/trader/orders/v2?limit=20&page=1&uid={uid}&orderListType=HISTORY")
    probe("gate.list", "https://www.gate.io/apiw/v2/futures/copy/leader/list?page=1&page_size=10&order_by=profit&sort_by=desc&cycle=month")


def hyperliquid():
    s, txt = call("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard")
    rec = {"status": s}
    try:
        rows = json.loads(txt).get("leaderboardRows") or []
        av = lambda x: float(x.get("accountValue") or 0)  # noqa: E731
        mon = lambda x: float(dict(x.get("windowPerformances") or []).get("month", {}).get("pnl") or 0)  # noqa: E731
        rec.update(rows=len(rows), av_300k=sum(1 for x in rows if av(x) >= 3e5), month_pos_300k=sum(1 for x in rows if av(x) >= 3e5 and mon(x) > 0),
                   month_pnl_1m=sum(1 for x in rows if mon(x) >= 1e6), month_pnl_250k=sum(1 for x in rows if mon(x) >= 2.5e5))
    except Exception as e:  # noqa: BLE001
        rec["err"] = str(e)[:200]
    OUT["hyperliquid.leaderboard"] = rec
    print("hl", rec, flush=True)


def main():
    for f in (hyperliquid, binance, bybit, bitget, mexc_gate):
        try:
            f()
        except Exception as e:  # noqa: BLE001
            OUT[f.__name__ + ".error"] = str(e)[:300]
    OUT["_asof"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    out = sys.argv[1] if len(sys.argv) > 1 else "data/research/whale_sources_probe.json"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(OUT, fh, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
