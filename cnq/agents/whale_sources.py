"""More whale sources for the «Киты» study and copy desk (04.11.2026, owner: «китов находить и на Bybit, и на других
платформах, брать их стратегии, копировать и добавлять к нам»).

Each source gives the same three things the Hyperliquid / OKX readers in whale_study give:
  candidates(get)                         → (rows, why): who to study (row: src, addr "<src>:<id>", name, pnl_all, pnl_m, …)
  refresh(conn, c, now, budget, get, notes) → closed positions of the last ~90 days into ws_trips (src, trader=addr) and the
                                            current positions into ws_whale.state, at most once a day / every 6 h
  current(get, c) → raw state; positions(state) → [{coin, side, size, notional, entry, upnl, lev}] (coin "" = hidden)
  open_trips(state, now) → the open positions as trips (so a trader sitting on losers does not look like a winner)
and record_gate(p, n90) → extra reasons a trader of this source is not followed (positions hidden). Since 04.11 17:46 the record bar
(60 days, ≥ 20 decisions, ≥ 70 % wins and PF ≥ 2 incl. open positions, hold ≥ 4 h, open losses ≤ 20 % of closed profit) is the same
for every source and lives in whale_study.profile; the copy test and ranking stay whale_study's too.

Sources (checked from a GitHub runner 04.11 with scripts/whale_sources_probe.py; runners are in the US):
  Binance copy trading — lead portfolios: public web endpoints of binance.com/copy-trading (no login): leaderboard, 30/90-day
    performance (win rate, orders), current positions and closed-position history (the «friendly» path; the «public» one answers
    empty) of portfolios whose owner shows them — 04.11 only 7 of the top 32 by P&L did (positionShow).
  Bybit «Master traders», Bitget, Gate: 403 / 404 from the runners (geo-block or login) — not connected.
Polite: ≤ 1 request per 0.5 s per source, inside whale_study's request budget. Stdlib only.
"""
from __future__ import annotations

import json
import time
import urllib.request

DAY, H1 = 86_400_000, 3_600_000
WIN_MIN = 0.70                        # pre-filter on Binance's own win rate (the real bar: whale_study.profile, 60 days incl. open)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Content-Type": "application/json", "clienttype": "web", "lang": "en"}


def _f(x, d=0.0):
    try:
        return float(x)
    except Exception:  # noqa: BLE001
        return d


# ------------------------------------------------------------------------------------------------------------ Binance
BN = "https://www.binance.com/bapi/futures"
# 04.11 21:14 (owner: more whales, same bar): ~9 000 public portfolios — read the top by 30/90-day P&L AND by ROI, 5 pages each
BN_PRE_N = 35
BN_PAGES = 5
BN_SORTS = ("PNL", "ROI")
BN_DETAIL_MAX = 90                    # detail pages checked per day (is the portfolio showing its positions?)
BN_HIST_PAGES = 4                     # 50 closed positions per page


def bn_get(path: str, body: dict | None = None, timeout: float = 20):
    """GET (body None) or POST a Binance copy-trading web endpoint → data (raises on a non-success answer)."""
    req = urllib.request.Request(BN + path, data=json.dumps(body).encode() if body is not None else None, headers=UA,
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode())
    if str(j.get("code")) != "000000" or j.get("success") is False:
        raise RuntimeError(f"Binance {j.get('code')}: {j.get('message')}")
    return j.get("data")


def bn_coin(symbol: str) -> str:
    s = str(symbol or "").upper()
    for q in ("USDT", "USDC", "BUSD"):
        if s.endswith(q):
            return s[: -len(q)]
    return s


def bn_candidates(get=bn_get, pages: int = BN_PAGES) -> tuple[list[dict], dict]:
    """Public lead portfolios by 30-day (then 90-day) P&L: P&L > 0, win rate ≥ 70 % (Binance's own number for the period), active and
    showing its positions. PF and trades in 90 days need the portfolio's own pages → refresh + record_gate."""
    rows, why, seen = [], {"rows": 0, "loss": 0, "win": 0, "hidden": 0}, set()
    for rng, dtype in [(r_, t_) for t_ in BN_SORTS for r_ in ("30D", "90D")]:
        for page in range(1, pages + 1):
            data = get("/v1/friendly/future/copy-trade/home-page/query-list",
                       {"pageNumber": page, "pageSize": 20, "timeRange": rng, "dataType": dtype, "favoriteOnly": False, "hideFull": False,
                        "nickname": "", "order": "DESC", "userAsset": 0, "portfolioType": "PUBLIC"}) or {}
            lst = data.get("list") if isinstance(data, dict) else data
            for x in lst or []:
                pid = str(x.get("leadPortfolioId") or "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                why["rows"] += 1
                if _f(x.get("pnl")) <= 0:
                    why["loss"] += 1; continue
                win = _f(x.get("winRate")) / 100.0
                if win < WIN_MIN:
                    why["win"] += 1; continue
                rows.append({"src": "bn", "addr": f"bn:{pid}", "code": pid, "name": x.get("nickname") or "", "short": (x.get("nickname") or pid)[:18],
                             "account": _f(x.get("aum")), "pnl_all": _f(x.get("pnl")), "pnl_m": _f(x.get("pnl")) if rng == "30D" else None,
                             "roi_m": _f(x.get("roi")) / 100.0 if rng == "30D" else None, "win_ratio": win, "mdd": _f(x.get("mdd")),
                             "followers": int(_f(x.get("currentCopyCount"))), "copier_pnl": _f(x.get("copierPnl")), "range": rng})
            if len(lst or []) < 20:
                break
            time.sleep(0.5)
    rows.sort(key=lambda r: (r["range"] != "30D", -r["pnl_all"]))
    # positions visible? (lead-portfolio detail «positionShow»; 04.11 most top portfolios hide them — copying those is blind)
    out = []
    for r in rows[:BN_DETAIL_MAX]:
        try:
            det = get(f"/v1/friendly/future/copy-trade/lead-portfolio/detail?portfolioId={r['code']}") or {}
        except Exception:  # noqa: BLE001
            det = {}
        time.sleep(0.5)
        if not isinstance(det, dict) or det.get("positionShow") is not True or str(det.get("status") or "ACTIVE") != "ACTIVE":
            why["hidden"] += 1
            continue
        out.append({**r, "lead_days": int((time.time() * 1000 - _f(det.get("startTime"), time.time() * 1000)) / DAY)})
        if len(out) >= BN_PRE_N:
            break
    why["passed"] = len(out)
    return out, why


def bn_current(get, c: dict) -> dict:
    """{"bn": [rows]} or {"bn": None, "hidden": True} when the portfolio does not show its positions."""
    det = {}
    try:
        det = get(f"/v1/friendly/future/copy-trade/lead-portfolio/detail?portfolioId={c['code']}") or {}
    except Exception:  # noqa: BLE001
        det = {}
    if isinstance(det, dict) and det.get("positionShow") is False:
        return {"bn": None, "hidden": True}
    rows = get(f"/v1/friendly/future/copy-trade/lead-data/positions?portfolioId={c['code']}") or []
    return {"bn": [x for x in rows if isinstance(x, dict) and _f(x.get("positionAmount"))]}


def bn_positions(state: dict) -> list[dict]:
    st = state or {}
    if st.get("hidden"):
        return [{"coin": "", "side": "", "size": 0, "notional": 0, "entry": 0, "upnl": 0, "lev": 0}]
    out = {}
    for x in st.get("bn") or []:
        amt = _f(x.get("positionAmount"))
        if not amt:
            continue
        ps = str(x.get("positionSide") or "BOTH").upper()
        side = ps if ps in ("LONG", "SHORT") else ("LONG" if amt > 0 else "SHORT")
        coin = bn_coin(x.get("symbol"))
        e, mk = _f(x.get("entryPrice")), _f(x.get("markPrice"))
        a = out.setdefault((coin, side), {"coin": coin, "side": side, "size": 0.0, "notional": 0.0, "entry": e, "upnl": 0.0, "lev": _f(x.get("leverage"), 1.0)})
        a["size"] += abs(amt); a["notional"] += abs(_f(x.get("notionalValue")) or abs(amt) * (mk or e)); a["upnl"] += _f(x.get("unrealizedProfit"))
    return sorted(({**v, "notional": round(v["notional"]), "upnl": round(v["upnl"])} for v in out.values()), key=lambda p: -p["notional"])


def bn_open_trips(state: dict, now: int) -> list[dict]:
    out = []
    for x in (state or {}).get("bn") or []:
        amt = _f(x.get("positionAmount"))
        e, mk = _f(x.get("entryPrice")), _f(x.get("markPrice"))
        if not amt or not e:
            continue
        ps = str(x.get("positionSide") or "BOTH").upper()
        side = ps if ps in ("LONG", "SHORT") else ("LONG" if amt > 0 else "SHORT")
        s = 1 if side == "LONG" else -1
        ot = int(_f(x.get("updateTime"), now)) or now
        out.append({"coin": bn_coin(x.get("symbol")), "side": side, "open_t": min(ot, now), "close_t": now, "hold_h": round((now - min(ot, now)) / H1, 2),
                    "entry": e, "exit": mk or e, "ret_pct": round(s * ((mk or e) / e - 1) * 100, 3), "notional": round(abs(amt) * e, 2),
                    "pnl": round(_f(x.get("unrealizedProfit")), 2), "fills": 1, "open": True})
    return out


def bn_refresh(conn, c: dict, now: int, budget, get=bn_get, notes=None, refresh_ms: int = DAY) -> bool:
    """30/90-day performance + closed positions (≤ 200, newest first) once a day; current positions every 6 hours."""
    addr = c["addr"]
    r = conn.execute("SELECT fills_t, state_t FROM ws_whale WHERE addr=?", (addr,)).fetchone()
    if not r:
        conn.execute("INSERT INTO ws_whale (addr, lb) VALUES (?,?)", (addr, json.dumps(c)))
        r = (None, None)
    hist_t, st_t = r
    if hist_t is None or now - hist_t >= refresh_ms:
        perf = {}
        for rng in ("30D", "90D"):
            if not budget.take():
                return False
            try:
                perf[rng] = get(f"/v1/public/future/copy-trade/lead-portfolio/performance?portfolioId={c['code']}&timeRange={rng}") or {}
            except Exception as e:  # noqa: BLE001
                if notes is not None:
                    notes.append(f"Binance {c['name'][:12]} {rng}: {str(e)[:100]}")
            time.sleep(0.5)
        c = {**c, "perf": {k: {kk: v.get(kk) for kk in ("roi", "pnl", "mdd", "winRate", "winOrders", "totalOrder", "copierPnl", "sharpRatio")}
                           for k, v in perf.items() if isinstance(v, dict)}}
        rows = []
        stop_at = max(now - 92 * DAY, (hist_t - 2 * DAY) if hist_t else 0)     # 04.11: after the first look only what is new
        for page in range(1, BN_HIST_PAGES + 1):
            if not budget.take():
                return False
            try:
                data = get("/v1/friendly/future/copy-trade/lead-portfolio/position-history",
                           {"pageNumber": page, "pageSize": 50, "portfolioId": c["code"], "sort": "OPENING"}) or {}
            except Exception as e:  # noqa: BLE001
                if notes is not None:
                    notes.append(f"Binance история {c['name'][:12]}: {str(e)[:100]}")
                break
            lst = data.get("list") or []
            for x in lst:
                try:
                    ot, ct = int(_f(x.get("opened"))), int(_f(x.get("closed")))
                    if not ct or str(x.get("status") or "").lower().startswith("partial"):
                        continue
                    side = "LONG" if str(x.get("side") or "").lower().startswith("l") else "SHORT"
                    e, ex = _f(x.get("avgCost")), _f(x.get("avgClosePrice"))
                    qty = abs(_f(x.get("maxOpenInterest")) or _f(x.get("closedVolume")))
                    rows.append(("bn", addr, str(x.get("id") or f"{x.get('symbol')}-{ot}"), bn_coin(x.get("symbol")), side, ot, ct, e, ex,
                                 _f(x.get("closingPnl")), qty * e, _f(x.get("leverage"), 0.0)))
                except Exception:  # noqa: BLE001
                    continue
            if len(lst) < 50 or (rows and min(x[6] for x in rows) < stop_at):
                break
            time.sleep(0.5)
        conn.executemany("INSERT OR REPLACE INTO ws_trips VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("UPDATE ws_whale SET fills_t=?, lb=? WHERE addr=?", (now, json.dumps(c), addr))
    if st_t is None or now - st_t >= 6 * H1:
        if not budget.take():
            return False
        try:
            conn.execute("UPDATE ws_whale SET state=?, state_t=? WHERE addr=?", (json.dumps(bn_current(get, c)), now, addr))
        except Exception as e:  # noqa: BLE001
            if notes is not None:
                notes.append(f"Binance позиции {c['name'][:12]}: {str(e)[:100]}")
    conn.commit()
    time.sleep(0.5)
    return True


# ------------------------------------------------------------------------------------------------------------ registry
SOURCES = {
    "bn": {"ru": "Binance · лид-трейдер копитрейдинга", "get": bn_get, "candidates": bn_candidates, "refresh": bn_refresh,
           "current": bn_current, "positions": bn_positions, "open_trips": bn_open_trips},
}


# 04.11 19:16–19:19 UTC, scripts/whale_sources_probe.py rounds 4–5 from a GitHub runner (US) — copy-trading sites checked and NOT connected:
SOURCES_OFF = {
    "bybit": "Bybit (мастер-трейдеры): 403 Access Denied со всех адресов (api2.bybit.com, bybitglobal, .eu, .nl, .kz) — гео-блок для серверов в США",
    "bitget": "Bitget: открытые страницы копитрейдинга отвечают 404 или пусто, официальный API копитрейдинга — только с ключом своего счёта",
    "gate": "Gate: 403 Access Denied (gate.io и gate.com) — гео-блок",
    "mexc": "MEXC: список трейдеров и их открытые позиции есть, но закрытых сделок (истории) публично нет — 60-дневную статистику не посчитать",
    "bingx": "BingX: копитрейдинг отвечает «нужна подпись / вход» (100003 / 100400)",
}


def src_of(addr: str) -> str | None:
    p = str(addr or "").split(":", 1)[0]
    return p if p in SOURCES and ":" in str(addr) else None


def lb_of(conn, addr: str) -> dict:
    r = conn.execute("SELECT lb FROM ws_whale WHERE addr=?", (addr,)).fetchone()
    try:
        return json.loads(r[0]) if r and r[0] else {}
    except Exception:  # noqa: BLE001
        return {}


def trades_90d(conn, addr: str, now: int, lb: dict | None = None) -> int:
    perf = ((lb or {}).get("perf") or {}).get("90D") or {}
    if perf.get("totalOrder") is not None:
        return int(_f(perf.get("totalOrder")))
    r = conn.execute("SELECT COUNT(*) FROM ws_trips WHERE trader=? AND close_t>=?", (addr, now - 90 * DAY)).fetchone()
    return int(r[0] if r else 0)


def record_gate(p: dict, n90: int) -> list[str]:
    """what an added source adds to the common 60-day bar (whale_study.profile, 04.11 17:46): open positions must be visible —
    reasons in plain Russian, empty = passes. n90 (the site's own 90-day trade count) is shown, not gated."""
    why = []
    if any(not (x or {}).get("coin") for x in p.get("positions") or []):
        why.append("скрывает открытые позиции")
    return why
