"""«Киты · крипто» watchlist for the VIP panel (04.11.2026, owner: «Будем отслеживать топов»).

Built in the same step as web/whales_top.json (cli «whalestudy») from the whale study's own profiles — every studied candidate of
every source it uses (Hyperliquid, OKX lead traders, Binance lead portfolios) — so nothing is fetched again and the selection rules
stay the study's (cnq/agents/whale_study.py profile / run). Per whale: source, name, link, status (копируем / кандидат / исключён)
with the reason, the 60-day record (win % and PF counting open positions, decisions, median hold, max drawdown), open positions,
the last 10 trades and our «Киты» desk's copies of this whale with their result. → web/whale_watch.json. Information only.
"""
from __future__ import annotations

SRC_RU = {"hl": "Hyperliquid", "okx": "OKX", "bn": "Binance"}
SRC_LONG_RU = {"hl": "Hyperliquid · сделки в блокчейне", "okx": "OKX · лид-трейдер копитрейдинга", "bn": "Binance · лид-портфель копитрейдинга"}
STATUS_RU = {"followed": "копируем", "candidate": "кандидат", "dropped": "исключён", "failed": "не прошёл"}
# 04.11 22:35 (owner: «−108k и Bobby Big Size −198k показаны кандидатами»): a «candidate» is only a trader who passes the bar or comes
# close to it; a minus over 60 days, no decisions or a record far from the bar → «не прошёл» (folded on the panel, with the reason)
NEAR = {"decisions": 10, "win": 0.60, "pf": 1.5}


def near_bar(p: dict) -> bool:
    """passes the 60-day bar, or is close: in profit, ≥ 10 decisions, ≥ 60 % wins and PF ≥ 1.5 (incl. open positions)"""
    if p.get("eligible"):
        return True
    pnl, n = p.get("pnl_60d"), p.get("decisions", p.get("trips"))
    return (pnl is not None and pnl > 0 and (n or 0) >= NEAR["decisions"] and (p.get("win") or 0) >= NEAR["win"]
            and (p.get("pf") or 0) >= NEAR["pf"])


def src_of(addr: str, src: str | None = None) -> str:
    if src:
        return src
    a = str(addr or "")
    return "okx" if a.startswith("okx:") else "bn" if a.startswith("bn:") else "hl"


def link_of(addr: str, src: str) -> str | None:
    a = str(addr or "")
    if src == "hl" and a.startswith("0x"):
        return f"https://app.hyperliquid.xyz/explorer/address/{a}"
    if src == "okx" and ":" in a:
        return f"https://www.okx.com/copy-trading/account/{a.split(':', 1)[1]}?tab=swap"
    if src == "bn" and ":" in a:
        return f"https://www.binance.com/en/copy-trading/lead-details/{a.split(':', 1)[1]}"
    return None


def max_dd(trips: list[dict]) -> float | None:
    """the deepest fall of the cumulative P&L ($) over decisions in close order (open ones at the mark, at the end)"""
    if not trips:
        return None
    eq, peak, dd = 0.0, 0.0, 0.0
    for t in sorted(trips, key=lambda t: (bool(t.get("open")), t.get("close_t") or 0)):
        eq += float(t.get("pnl") or 0)
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return round(dd, 2)


def _trade(t: dict) -> dict:
    return {k: t.get(k) for k in ("coin", "side", "open_t", "close_t", "entry", "exit", "ret_pct", "pnl", "hold_h", "open", "lev", "parts")}


def _pos(p: dict) -> dict:
    return {"coin": p.get("coin") or "", "side": p.get("side") or "", "entry": p.get("entry"), "lev": p.get("lev"), "upnl": p.get("upnl"),
            "notional": p.get("notional"), "hidden": not p.get("coin")}


def _copies(desk: dict | None, addr: str) -> dict:
    d = desk or {}
    op = [o for o in d.get("open") or [] if o.get("whale") == addr]
    cl = [c for c in d.get("closed") or [] if c.get("whale") == addr]
    bw = next((w for w in d.get("by_whale") or [] if w.get("whale") == addr), None)
    return {"trades": (bw or {}).get("trades", len(cl)), "wins": (bw or {}).get("wins", sum(1 for c in cl if (c.get("pnl") or 0) > 0)),
            "pnl_usd": (bw or {}).get("pnl_usd", round(sum(c.get("pnl") or 0 for c in cl), 2)),
            "open_upnl": round(sum(o.get("upnl") or 0 for o in op), 2),
            "open": [{k: o.get(k) for k in ("coin", "side", "entry", "price", "lev", "margin", "upnl", "opened_t", "stop", "join")} for o in op],
            "closed": [{k: c.get(k) for k in ("coin", "side", "entry", "exit", "lev", "pnl", "pnl_pct", "opened_t", "closed_t", "why")} for c in cl][:10]}


def crypto_watch(profs: list[dict], top: dict) -> dict:
    """whale study profiles + its output (following, follow_dropped, desk) → the watchlist document"""
    following = {x["addr"]: x for x in top.get("following") or []}
    dropped = {x["addr"]: x for x in top.get("follow_dropped") or []}
    desk = top.get("desk") or {}
    rank = {p["addr"]: p.get("rank") for p in top.get("top") or []}
    rows, seen = [], set()
    for p in profs or []:
        a = p.get("addr")
        if not a or a in seen:
            continue
        seen.add(a)
        src = src_of(a, p.get("src"))
        if a in following:
            status, why = "followed", ["проходит отбор за 60 дней, тест копирования в плюсе — стол «Киты» повторяет его новые сделки"]
        elif a in dropped:
            status, why = "dropped", list(dropped[a].get("why_ru") or []) or ["снят с копирования"]
        else:
            status = "candidate" if near_bar(p) else "failed"
            why = list(p.get("excluded_ru") or [])
            if not why:
                if rank.get(a) and p.get("hidden"):
                    why = ["проходит отбор, но скрывает открытые позиции — повторить вовремя нельзя"]
                elif rank.get(a):
                    why = [f"проходит отбор (место {rank[a]}), но не в пятёрке копируемых или тест копирования в минусе"]
                else:
                    why = ["проходит отбор, ниже других по прибыли/убытку"]
        pos = [_pos(x) for x in p.get("positions") or []][:8]
        rows.append({"addr": a, "src": src, "src_ru": SRC_RU.get(src, src), "src_long_ru": SRC_LONG_RU.get(src, src),
                     "name": p.get("name") or p.get("short") or a, "short": p.get("short"), "link": link_of(a, src),
                     "status": status, "status_ru": STATUS_RU[status], "why_ru": why, "eligible": bool(p.get("eligible")), "rank": rank.get(a),
                     "win": p.get("win"), "pf": p.get("pf"), "win_closed": p.get("win_closed"), "pf_closed": p.get("pf_closed"),
                     "decisions": p.get("decisions", p.get("trips")), "hold_med_h": p.get("hold_med_h"), "style_ru": p.get("style_ru"),
                     "max_dd_usd": p.get("max_dd_usd"), "max_dd_all_usd": ((p.get("portfolio") or {}).get("max_dd_usd")),
                     "pnl_60d": p.get("pnl_60d"), "pnl_30d": p.get("pnl_30d"), "account": p.get("account"), "followers": p.get("followers"),
                     "lev_now": p.get("lev_now"), "open_trips": p.get("open_trips"), "open_upnl": p.get("open_upnl"),
                     "hidden_open": p.get("hidden_open"), "positions": pos, "positions_hidden": sum(1 for x in pos if x["hidden"]),
                     "recent": [_trade(t) for t in (p.get("recent") or [])[:10]], "copy30": (p.get("copy") or {}).get("30"),
                     "copies": _copies(desk, a), "desc_ru": p.get("desc_ru")})
    # whales the desk dropped earlier but not studied in this run: shown with what we know
    for a, x in dropped.items():
        if a in seen:
            continue
        src = src_of(a, x.get("src"))
        rows.append({"addr": a, "src": src, "src_ru": SRC_RU.get(src, src), "src_long_ru": SRC_LONG_RU.get(src, src), "name": x.get("name") or a,
                     "link": link_of(a, src), "status": "dropped", "status_ru": STATUS_RU["dropped"], "why_ru": list(x.get("why_ru") or []),
                     "positions": [], "recent": [], "copies": _copies(desk, a), "not_studied": True})
    order = {"followed": 0, "candidate": 1, "dropped": 2, "failed": 3}
    rows.sort(key=lambda r: (order[r["status"]], not r.get("eligible"), -(r.get("pf") or -99) if r.get("eligible") else 0, -(r.get("pnl_60d") or 0)))
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in order}
    by_src = {}
    for r in rows:
        by_src[r["src"]] = by_src.get(r["src"], 0) + 1
    dk = {k: desk.get(k) for k in ("name_ru", "start", "started", "equity", "pnl_usd", "pnl_pct", "trades", "wins", "winrate", "max_dd_pct",
                                   "util_pct", "live")} if desk else None
    return {"asof": top.get("asof"), "complete": top.get("complete"), "counts": counts, "by_src": by_src, "n": len(rows), "whales": rows,
            "desk": dk, "rule_ru": top.get("follow_rule_ru"), "not_covered_ru": top.get("not_covered_ru"), "hidden_ru": top.get("hidden_ru"),
            "funnel": top.get("funnel"),
            # 05.11 (owner): the next whales after the followed ten — who would come in when one drops out
            "bench": [{**b, "src_ru": SRC_RU.get(b.get("src"), b.get("src")), "link": link_of(b.get("addr"), b.get("src"))} for b in top.get("bench") or []],
            "about_ru": "Все киты, которых система изучает: из лидерборда Hyperliquid, лид-трейдеры копитрейдинга OKX и открытые лид-портфели "
                        "Binance. «Копируем» — стол «Киты» повторяет их новые сделки на демо-счёте; «кандидат» — изучаем, в копирование не взяли "
                        "(причина рядом), показываем только тех, кто проходит отбор или близок к нему; «исключён» — раньше копировали, сняли; «не прошли» — "
                        "в минусе за 60 дней, без сделок или далеко от отбора (свёрнуто, с причиной). Процент прибыльных и PF считаются за 60 дней вместе с "
                        "открытыми позициями по текущей цене. Только информация, не финансовый совет."}
