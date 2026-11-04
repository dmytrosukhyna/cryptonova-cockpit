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
    rows = []
    for rng in ("30D", "90D"):
        d = probe("binance.list", f"{bn}/v1/friendly/future/copy-trade/home-page/query-list",
                  {"pageNumber": 1, "pageSize": 20, "timeRange": rng, "dataType": "PNL", "favoriteOnly": False, "hideFull": False,
                   "nickname": "", "order": "DESC", "userAsset": 0, "portfolioType": "PUBLIC"})
        rows += [r for r in (first_list(d) or []) if r.get("leadPortfolioId") not in {x.get("leadPortfolioId") for x in rows}]
    OUT.pop("binance.list", None)
    summary, visible = [], []
    for r in rows[:40]:
        pid = r.get("leadPortfolioId")
        s_, txt = call(f"{bn}/v1/friendly/future/copy-trade/lead-portfolio/detail?portfolioId={pid}")
        try:
            det = json.loads(txt).get("data") or {}
        except Exception:  # noqa: BLE001
            det = {}
        show = det.get("positionShow")
        summary.append(f"{pid}|{str(r.get('nickname'))[:14]}|pnl {round(r.get('pnl') or 0)}|win {r.get('winRate')}|show {show}")
        if show:
            visible.append(pid)
    OUT["binance.summary"] = {"rows": summary, "visible": len(visible), "of": len(summary)}
    for i, pid in enumerate(visible[:3]):
        pos = dig(probe(f"binance.pos{i}", f"{bn}/v1/friendly/future/copy-trade/lead-data/positions?portfolioId={pid}"), "data") or []
        OUT[f"binance.pos{i}"]["rows"] = [p for p in pos if float(p.get("positionAmount") or 0)][:2]
        for nm, (u, body) in {"ph_open": ("/v1/public/future/copy-trade/lead-portfolio/position-history", {"pageNumber": 1, "pageSize": 10, "portfolioId": pid, "sort": "OPENING"}),
                              "ph_plain": ("/v1/public/future/copy-trade/lead-portfolio/position-history", {"pageNumber": 1, "pageSize": 10, "portfolioId": pid}),
                              "ph_friendly": ("/v1/friendly/future/copy-trade/lead-portfolio/position-history", {"pageNumber": 1, "pageSize": 10, "portfolioId": pid, "sort": "OPENING"}),
                              "th": ("/v1/public/future/copy-trade/lead-portfolio/trade-history", {"pageNumber": 1, "pageSize": 10, "portfolioId": pid}),
                              "perf90": (f"/v1/public/future/copy-trade/lead-portfolio/performance?portfolioId={pid}&timeRange=90D", None)}.items():
            probe(f"binance.{nm}{i}", bn + u, body)
            if OUT[f"binance.{nm}{i}"].get("rows"):
                OUT[f"binance.{nm}{i}"]["rows"] = OUT[f"binance.{nm}{i}"]["rows"][:2]


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
        base = "https://futures.mexc.com/copyFutures/api/v1"
        for nm, u in {"h1": f"{base}/trader/orders/v2?limit=20&page=1&uid={uid}&orderListType=2",
                      "h2": f"{base}/trader/historyOrders/v2?limit=20&page=1&uid={uid}",
                      "h3": f"{base}/trader/orders/history/v2?limit=20&page=1&uid={uid}",
                      "h4": f"{base}/trader/closedOrders?limit=20&page=1&uid={uid}",
                      "detail": f"{base}/trader/detail/v2?uid={uid}",
                      "stats": f"{base}/trader/statistics/v2?uid={uid}"}.items():
            probe(f"mexc.{nm}", u)


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


# ------------------------------------------------------------------------------------------------ round 4 (04.11 21:14, owner: more sources)
def first_id(d, *keys):
    for r in first_list(d) or []:
        for k in keys:
            if r.get(k):
                return r.get(k)
    return None


def bybit4():
    from urllib.parse import quote
    for host in ("https://api2.bybit.com", "https://api2.bybitglobal.com", "https://www.bybit.com/x-api", "https://www.bybitglobal.com/x-api",
                 "https://api2.bybit.eu", "https://api2.bybit.nl", "https://api2.bybit.kz", "https://www.bybit.com/fapi"):
        lst = host + ("/beehive/public/v1/common/dynamic-leader-list" if host.endswith("/fapi") else "/fapi/beehive/public/v1/common/dynamic-leader-list")
        d = probe(f"bybit.list.{host.split('//')[1][:22]}", lst + "?pageNo=1&pageSize=16&dataDuration=DATA_DURATION_NINETY_DAY&sortField=LEADER_SORT_FIELD_SORT_PNL")
        mark = first_id(d, "leaderMark")
        if not mark:
            continue
        m = quote(mark, safe="")
        b = host + "/fapi/beehive/public/v1/common"
        for nm, u in {"income": f"{b}/leader-income?leaderMark={m}", "pos": f"{b}/position/list?leaderMark={m}",
                      "hist": f"{b}/leader-history?leaderMark={m}&pageSize=20&page=1",
                      "hist2": f"{b}/order/list-detail?leaderMark={m}&pageSize=20&page=1",
                      "hist3": f"{b}/leader-closed-position-list?leaderMark={m}&pageSize=20&pageNo=1"}.items():
            probe(f"bybit.{nm}", u)
        break


def bitget4():
    W = "https://www.bitget.com"
    d = None
    for nm, u, body in [("rank.v1", "/v1/trigger/trace/public/traderRankList", {"pageNo": 1, "pageSize": 20, "sortRule": 2, "sortFlag": "desc", "languageType": 0}),
                        ("rank.v2", "/v1/trigger/trace/public/traderRankListV2", {"pageNo": 1, "pageSize": 20, "sortRule": 2, "sortFlag": "desc", "languageType": 0}),
                        ("list.copy", "/v1/copy/mix/public/trader/list", {"pageNo": 1, "pageSize": 20, "sortRule": 2, "sortFlag": "desc", "languageType": 0}),
                        ("list.trace", "/v1/trace/public/traderList", {"pageNo": 1, "pageSize": 20, "languageType": 0}),
                        ("api.v2", "https://api.bitget.com/api/v2/copy/mix-trader/query-traders?limit=5", None)]:
        r = probe(f"bitget.{nm}", u if u.startswith("http") else W + u, body)
        if first_id(r, "traderUid", "traderId", "uid"):
            d = r
    uid = first_id(d, "traderUid", "traderId", "uid")
    if uid:
        for nm, u, body in [("cur", "/v1/trigger/trace/public/currentTrack", {"traderUid": uid, "pageNo": 1, "pageSize": 20, "languageType": 0}),
                            ("hist", "/v1/trigger/trace/public/historyTrack", {"traderUid": uid, "pageNo": 1, "pageSize": 20, "languageType": 0}),
                            ("cur2", "/v1/trigger/trace/public/getCurrentOrders", {"traderUid": uid, "pageNo": 1, "pageSize": 20}),
                            ("hist2", "/v1/trigger/trace/public/getHistoryOrders", {"traderUid": uid, "pageNo": 1, "pageSize": 20}),
                            ("detail", "/v1/trigger/trace/public/traderDetailPageV2", {"traderUid": uid, "languageType": 0})]:
            probe(f"bitget.{nm}", W + u, body)


def gate4():
    d = None
    for host in ("https://www.gate.io", "https://www.gate.com"):
        for nm, u in [("l1", "/apiw/v2/copy/leader/list?page=1&page_size=20&order_by=profit&sort_by=desc&cycle=month"),
                      ("l2", "/apiw/v2/futures/copy/leader/list?page=1&page_size=20"),
                      ("l3", "/api/copytrade/copy_trading/trader/list?page=1&page_size=20&order_by=profit")]:
            r = probe(f"gate.{host[12:16]}.{nm}", host + u)
            if first_id(r, "leader_id", "user_id", "uid", "id"):
                d = (host, r)
        if d:
            break
    if d:
        host, r = d
        lid = first_id(r, "leader_id", "user_id", "uid", "id")
        for nm, u in [("pos", f"/apiw/v2/copy/leader/position?leader_id={lid}"), ("pos2", f"/apiw/v2/copy/leader/current_position?leader_id={lid}"),
                      ("hist", f"/apiw/v2/copy/leader/position_history?leader_id={lid}&page=1&page_size=20"),
                      ("hist2", f"/apiw/v2/copy/leader/history_position?leader_id={lid}&page=1&page_size=20")]:
            probe(f"gate.{nm}", host + u)


def mexc4():
    base = "https://futures.mexc.com/copyFutures/api/v1"
    d = probe("mexc.list", f"{base}/traders/v2?condition=%5B%5D&limit=10&orderBy=PNL&page=1")
    uid = first_id(d, "uid")
    if not uid:
        return
    OUT["mexc.list"]["rows"] = (OUT["mexc.list"].get("rows") or [])[:1]
    for nm, u in {"o_t1": f"{base}/trader/orders/v2?limit=20&page=1&uid={uid}&orderListType=1",
                  "o_open": f"{base}/trader/orders/v2?limit=20&page=1&uid={uid}&type=open",
                  "o_hist": f"{base}/trader/orders/v2?limit=20&page=1&uid={uid}&type=history",
                  "cur": f"{base}/trader/currentOrders?uid={uid}&limit=20&page=1",
                  "cur2": f"{base}/trader/positions?uid={uid}",
                  "hist": f"{base}/trader/historyOrders?uid={uid}&limit=20&page=1",
                  "hist3": f"{base}/trader/orders/history?uid={uid}&limit=20&page=1",
                  "info": f"{base}/trader/info?uid={uid}", "info2": f"{base}/traders/detail?uid={uid}"}.items():
        probe(f"mexc.{nm}", u)


def bingx4():
    for nm, u, body in [("l1", "https://bingx.com/api/copytrade/v1/public/trader/list?pageId=0&pageSize=20", None),
                        ("l2", "https://api-app.qq-os.com/api/copy-trade/v1/public/trader/rank?pageId=0&pageSize=20", None),
                        ("l3", "https://open-api.bingx.com/openApi/copyTrading/v1/public/traders?pageIndex=1&pageSize=20", None)]:
        probe(f"bingx.{nm}", u, body)


def okx_wide():
    """how big the OKX public lead-trader pool is per sort (perpetuals, ≥ 90 days of leading)"""
    for sort in ("overview", "pnl", "aum", "win_ratio", "pnl_ratio"):
        s_, txt = call(f"https://www.okx.com/api/v5/copytrading/public-lead-traders?instType=SWAP&sortType={sort}&minLeadDays=3&page=1&limit=20")
        try:
            d = (json.loads(txt).get("data") or [{}])[0]
            OUT[f"okx.{sort}"] = {"status": s_, "totalPage": d.get("totalPage"), "n": len(d.get("ranks") or []),
                                  "first": [(r.get("nickName"), r.get("pnl"), r.get("winRatio")) for r in (d.get("ranks") or [])[:3]]}
        except Exception as e:  # noqa: BLE001
            OUT[f"okx.{sort}"] = {"status": s_, "err": str(e)[:100], "text": txt[:200]}


def binance_wide():
    bn = "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/home-page/query-list"
    for dt in ("PNL", "ROI", "COPIER_PNL", "SHARP_RATIO", "MDD", "AUM"):
        for rng in ("30D", "90D"):
            s_, txt = call(bn, {"pageNumber": 1, "pageSize": 20, "timeRange": rng, "dataType": dt, "favoriteOnly": False, "hideFull": False,
                                "nickname": "", "order": "DESC", "userAsset": 0, "portfolioType": "PUBLIC"})
            try:
                d = json.loads(txt)
                OUT[f"binance.{dt}.{rng}"] = {"status": s_, "code": d.get("code"), "total": (d.get("data") or {}).get("total"),
                                               "n": len((d.get("data") or {}).get("list") or [])}
            except Exception as e:  # noqa: BLE001
                OUT[f"binance.{dt}.{rng}"] = {"status": s_, "err": str(e)[:100]}


def mexc5():
    """round 5: which MEXC copy-trading call gives CLOSED positions (close price / realised P&L) — compact summary per variant"""
    base = "https://futures.mexc.com/copyFutures/api/v1"
    s_, txt = call(f"{base}/traders/v2?condition=%5B%5D&limit=20&orderBy=PNL&page=1")
    rows = (json.loads(txt).get("data") or {}).get("content") or first_list(json.loads(txt)) or []
    uids = [r.get("uid") for r in rows if r.get("uid")][:3]
    OUT["mexc5.list_keys"] = {"n": len(rows), "keys": sorted((rows[0] if rows else {}).keys()), "data_keys": list((json.loads(txt).get("data") or {}).keys())
                              if isinstance(json.loads(txt).get("data"), dict) else type(json.loads(txt).get("data")).__name__}
    for uid in uids[:2]:
        for nm, u in {"t_hist": f"orders/v2?limit=50&page=1&uid={uid}&type=history", "t_close": f"orders/v2?limit=50&page=1&uid={uid}&type=close",
                      "t_closed": f"orders/v2?limit=50&page=1&uid={uid}&type=closed", "olt0": f"orders/v2?limit=50&page=1&uid={uid}&orderListType=0",
                      "olt_h": f"orders/v2?limit=50&page=1&uid={uid}&orderListType=HISTORY", "st2": f"orders/v2?limit=50&page=1&uid={uid}&status=2",
                      "p2": f"orders/v2?limit=50&page=2&uid={uid}", "plain": f"orders/v2?limit=50&page=1&uid={uid}",
                      "hist_v2": f"historyOrders/v2?limit=50&page=1&uid={uid}", "closeOrders": f"closeOrders/v2?limit=50&page=1&uid={uid}",
                      "hpos": f"historyPositions?uid={uid}&limit=50&page=1", "hpos2": f"positions/history?uid={uid}&limit=50&page=1",
                      "hpos3": f"historyPositions/v2?uid={uid}&limit=50&page=1", "pos": f"positions?uid={uid}"}.items():
            s_, txt = call(f"{base}/trader/{nm and u}")
            rec = {"status": s_}
            try:
                d = json.loads(txt)
                rr = first_list(d) or []
                rec.update(code=d.get("code"), n=len(rr), closed=sum(1 for r in rr if float(r.get("closeAvgPrice") or 0) > 0),
                           closing=sum(1 for r in rr if r.get("closing")), keys=sorted(rr[0].keys())[:40] if rr else None,
                           ot_min=min((r.get("openTime") or 0 for r in rr), default=None),
                           sample=next((r for r in rr if float(r.get("closeAvgPrice") or 0) > 0), None))
            except Exception as e:  # noqa: BLE001
                rec["err"] = txt[:100]
            OUT[f"mexc5.{uid}.{nm}"] = rec
            print(f"mexc5.{uid}.{nm}", json.dumps({k: v for k, v in rec.items() if k not in ("keys", "sample")}), flush=True)


def main():
    for f in (mexc5,):
        try:
            f()
        except Exception as e:  # noqa: BLE001
            OUT[f.__name__ + ".error"] = str(e)[:300]
    OUT["_asof"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    out = sys.argv[1] if len(sys.argv) > 1 else "data/research/whale_sources_probe.json"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(OUT, fh, ensure_ascii=False, indent=1, default=str)
    # annotations are readable from a session without a token (the check-runs API): one per source, compact
    for name, rec in OUT.items():
        if not isinstance(rec, dict):
            continue
        m = json.dumps(rec, ensure_ascii=False, default=str)[:6000].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::notice title={name[:60]}::{m}", flush=True)


if __name__ == "__main__":
    main()
