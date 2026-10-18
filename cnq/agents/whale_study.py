"""Whale study — the few Hyperliquid traders worth following, how they make money, whether copying them works,
and a paper copy desk «Киты» that follows them live.

Why: the old whales list (cnq.agents.whales) ranks the leaderboard by 30-day ROI, which puts deposit artefacts on top
(+3 724 031 % «ROI» = money moved in, not trading). Here a trader earns a place only by his own trade history.

Pipeline (Hyperliquid public info API, no keys):
1. Leaderboard (stats-data) → pre-filter: account ≥ $300k, all-time P&L ≥ $500k, month P&L > 0, monthly volume not of a
   market maker (≤ 1000× the account), month ROI ≤ 1000 % (bigger = deposit artefact). Top PRE_N by all-time P&L.
2. Per candidate (budgeted, resumable across runs, refreshed once a day): fills of the last 30 days
   (userFillsByTime, aggregated by order; a full page of 2000 = high-frequency trader → excluded as not copyable),
   portfolio (all-time P&L curve → drawdown, share of positive months), current positions (clearinghouseState).
3. Fills → round trips per coin (position from 0 back to 0, a flip closes one and opens the next): hold time, side,
   size, result (closedPnl − fees). Spot and builder-dex markets are skipped.
4. Copy test on 15m candles: enter at the price D minutes after the whale opened, exit D minutes after he closed
   (D = 15 / 30 / 60 min; trades held shorter than D are missed), costs 0.11 % per round trip.
5. Eligible = ≥ 8 closed round trips in 30 days, median hold ≥ 30 min, 30-day P&L > 0, profit factor ≥ 1.2.
   Rank = what copying with a 30-minute delay would have earned (mean / sd × √n). Top 5 → profiles in plain Russian.
6. Copy desk (every run): $10 000 paper account follows the top 5 — a NEW position of a followed whale (seen appear
   between two runs) is opened at the current mid, margin 10 % of equity, leverage = whale's but ≤ 5×, at most 8 at
   once, one per coin; closed when the whale closes or flips. Positions a whale already had when we started watching
   him are not copied (we did not see the entry).

Honesty: whales hedge across venues, the server sees changes only every ~30 minutes, and a past month is a small
sample — the copy test says what the delay costs; the desk is an experiment until it has its own record.
Output: web/whales_top.json. Stdlib only.
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics as st
import time
import urllib.request

LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
HL = "https://api.hyperliquid.xyz/info"
M15, H1, DAY = 900_000, 3_600_000, 86_400_000
WINDOW = 30 * DAY
PRE_N = 30
TOP_N = 10                            # «топ-10 трейдеров» on the panel
FOLLOW_N = 5                          # the copy desk follows the best 5 whose copy test is positive
MIN_AV, MIN_ALL = 300_000.0, 500_000.0
MAX_VLM_X, MAX_ROI_M = 400.0, 10.0
FETCH_WINDOW = 45 * DAY               # fills fetched for 45 days so positions opened before the 30-day window are known
FILLS_PAGES = 4                       # up to 8000 fills (big traders slice orders); more than that = high-frequency
FILLS_V = 2
FILLS_PAGE = 2000
MIN_TRIPS, MIN_HOLD_H, MIN_PF = 8, 0.5, 1.2
DELAYS_MIN = (15, 30, 60)
COST_PCT = 0.11                       # taker 0.045 % × 2 + slippage 0.02 %
REFRESH_MS = DAY                      # study data per whale refreshed once a day
DESK_START, DESK_MARGIN, DESK_LEV_CAP, DESK_MAX_OPEN = 10_000.0, 0.10, 5.0, 8
UA = {"Content-Type": "application/json", "User-Agent": "cryptonova-whale-study/1.0"}
OKX = "https://www.okx.com"
OKX_PRE_N = 20
SRC_RU = {"hl": "Hyperliquid · сделки в блокчейне", "okx": "OKX · лид-трейдер копитрейдинга"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ws_fills (addr TEXT, tid TEXT, t INTEGER, coin TEXT, side TEXT, px REAL, sz REAL, dir TEXT,
  start_pos REAL, closed_pnl REAL, fee REAL, PRIMARY KEY (addr, tid));
CREATE TABLE IF NOT EXISTS ws_whale (addr TEXT PRIMARY KEY, lb TEXT, fills_t INTEGER, fills_full INTEGER DEFAULT 0,
  portfolio TEXT, portfolio_t INTEGER, state TEXT, state_t INTEGER);
CREATE TABLE IF NOT EXISTS ws_candles (coin TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, PRIMARY KEY (coin, t));
CREATE TABLE IF NOT EXISTS ws_candle_fetch (coin TEXT PRIMARY KEY, until INTEGER);
CREATE TABLE IF NOT EXISTS ws_kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS ws_trips (src TEXT, trader TEXT, id TEXT, coin TEXT, side TEXT, open_t INTEGER, close_t INTEGER, entry REAL,
  exit REAL, pnl REAL, notional REAL, lever REAL, PRIMARY KEY (src, trader, id));
"""


# ------------------------------------------------------------------------------------------------------------ http
class Budget:
    def __init__(self, n: int, seconds: float):
        self.n, self.until = n, time.time() + seconds

    def take(self) -> bool:
        if self.n <= 0 or time.time() > self.until:
            return False
        self.n -= 1
        return True


def _post(body: dict, timeout: float = 30):
    for k in range(3):
        try:
            req = urllib.request.Request(HL, data=json.dumps(body).encode(), headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            if "429" in str(e) and k < 2:
                time.sleep(3 + 5 * k)
                continue
            raise
    return None


def _get_lb(timeout: float = 60):
    req = urllib.request.Request(LB, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _f(x, d=0.0):
    try:
        return float(x)
    except Exception:  # noqa: BLE001
        return d


def _perp(coin: str | None) -> bool:
    return bool(coin) and not coin.startswith("@") and "/" not in coin and ":" not in coin


# ------------------------------------------------------------------------------------------------------------ leaderboard
def candidates(lb_json: dict, n: int = PRE_N) -> tuple[list[dict], dict]:
    rows, why = [], {"rows": 0, "small": 0, "alltime": 0, "month": 0, "mm": 0, "roi_artefact": 0}
    for x in (lb_json or {}).get("leaderboardRows", []) or []:
        why["rows"] += 1
        m = {k: v for k, v in (x.get("windowPerformances") or [])}
        av = _f(x.get("accountValue"))
        mo, wk, al, dy = (m.get("month") or {}), (m.get("week") or {}), (m.get("allTime") or {}), (m.get("day") or {})
        pnl_m, roi_m, vlm_m, pnl_all = _f(mo.get("pnl")), _f(mo.get("roi")), _f(mo.get("vlm")), _f(al.get("pnl"))
        if av < MIN_AV:
            why["small"] += 1; continue
        if pnl_all < MIN_ALL:
            why["alltime"] += 1; continue
        if pnl_m <= 0:
            why["month"] += 1; continue
        if vlm_m > MAX_VLM_X * av:
            why["mm"] += 1; continue
        if roi_m > MAX_ROI_M:
            why["roi_artefact"] += 1; continue
        rows.append({"addr": (x.get("ethAddress") or "").lower(), "name": x.get("displayName") or "", "account": av,
                     "pnl_all": pnl_all, "pnl_m": pnl_m, "roi_m": roi_m, "vlm_m": vlm_m,
                     "pnl_w": _f(wk.get("pnl")), "roi_w": _f(wk.get("roi")), "pnl_d": _f(dy.get("pnl"))})
    rows = [r for r in rows if r["addr"].startswith("0x")]
    rows.sort(key=lambda r: -r["pnl_all"])
    why["passed"] = len(rows)
    return rows[:n], why


# ------------------------------------------------------------------------------------------------------------ storage
def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def kv_get(conn, k, default=None):
    r = conn.execute("SELECT v FROM ws_kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def kv_set(conn, k, v) -> None:
    conn.execute("INSERT OR REPLACE INTO ws_kv VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))


def store_fills(conn, addr: str, fills: list) -> int:
    rows = []
    for f in fills or []:
        coin = f.get("coin")
        if not _perp(coin):
            continue
        tid = str(f.get("tid") or f.get("hash") or f"{f.get('time')}-{f.get('px')}-{f.get('sz')}")
        rows.append((addr, tid, int(f.get("time") or 0), coin, "B" if f.get("side") == "B" else "A", _f(f.get("px")), _f(f.get("sz")),
                     f.get("dir") or "", _f(f.get("startPosition")), _f(f.get("closedPnl")), _f(f.get("fee"))))
    conn.executemany("INSERT OR REPLACE INTO ws_fills VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def load_fills(conn, addr: str, since: int) -> list[dict]:
    cols = ("t", "coin", "side", "px", "sz", "dir", "start_pos", "closed_pnl", "fee")
    return [dict(zip(cols, r)) for r in conn.execute(
        "SELECT t, coin, side, px, sz, dir, start_pos, closed_pnl, fee FROM ws_fills WHERE addr=? AND t>=? ORDER BY t, tid",
        (addr, since))]


# ------------------------------------------------------------------------------------------------------------ analysis
def round_trips(fills: list[dict], since: int = 0, marks: dict | None = None, now: int | None = None) -> tuple[list[dict], float]:
    """Fills (time-sorted) → round trips per coin. A trip opens when the position leaves 0 and closes when it returns to 0
    (a flip closes one trip and opens the next at the same fill). Trips already open at the first fill of the window are
    skipped (entry unknown). → (complete trips, total P&L of all fills in the window incl. partial trips)."""
    by: dict[str, list] = {}
    total = 0.0
    for f in fills:
        by.setdefault(f["coin"], []).append(f)
        if f["t"] >= since:
            total += f["closed_pnl"] - f["fee"]
    trips = []
    for coin, fs in by.items():
        cur = None
        known = None                    # position is "known" only after we saw it at 0
        for f in fs:
            start = f["start_pos"]
            d = f["sz"] if f["side"] == "B" else -f["sz"]
            new = start + d
            eps = max(1e-9, abs(f["sz"]) * 1e-6)
            if known is None:
                known = abs(start) <= eps
            if not known:
                if abs(new) <= eps or start * new < 0:
                    known = True
                    if start * new < 0:
                        cur = _open(coin, f, new, 0.0)
                continue
            if cur is None:
                if abs(new) > eps:
                    cur = _open(coin, f, new, f["fee"])
                continue
            s = 1 if cur["side"] == "LONG" else -1
            cur["pnl"] += f["closed_pnl"] - f["fee"]
            if s * d > 0:                                       # adding
                cur["in_usd"] += f["px"] * f["sz"]; cur["in_sz"] += f["sz"]
            else:                                               # reducing (part of the fill may flip)
                red = min(f["sz"], abs(start))
                cur["out_usd"] += f["px"] * red; cur["out_sz"] += red
            cur["max_sz"] = max(cur["max_sz"], abs(new) if start * new > 0 else abs(start))
            cur["fills"] += 1
            if abs(new) <= eps or start * new < 0:
                trips.append(_close(cur, f))
                cur = _open(coin, f, new, 0.0) if start * new < 0 and abs(new) > eps else None
        # a position still open now counts at the current price — a trader who sits on losers must not look like a winner
        mk = _f((marks or {}).get(coin)) if marks else 0.0
        if cur is not None and mk > 0 and now:
            entry = cur["in_usd"] / cur["in_sz"] if cur["in_sz"] else cur["first_px"]
            left = max(0.0, cur["in_sz"] - cur["out_sz"])
            sg = 1 if cur["side"] == "LONG" else -1
            trips.append({"coin": coin, "side": cur["side"], "open_t": cur["open_t"], "close_t": now, "hold_h": round((now - cur["open_t"]) / H1, 2),
                          "entry": entry, "exit": mk, "ret_pct": round(sg * (mk / entry - 1) * 100, 3) if entry else None,
                          "notional": round(cur["max_sz"] * entry, 2), "pnl": round(cur["pnl"] + sg * (mk - entry) * left, 2),
                          "fills": cur["fills"], "open": True})
    trips.sort(key=lambda t: t["open_t"])
    return trips, total


def _open(coin, f, new, fee):
    sz = abs(new)
    return {"coin": coin, "side": "LONG" if new > 0 else "SHORT", "open_t": f["t"], "in_usd": f["px"] * sz, "in_sz": sz,
            "out_usd": 0.0, "out_sz": 0.0, "max_sz": sz, "pnl": -fee, "fills": 1, "first_px": f["px"]}


def _close(cur, f):
    entry = cur["in_usd"] / cur["in_sz"] if cur["in_sz"] else cur["first_px"]
    exit_ = cur["out_usd"] / cur["out_sz"] if cur["out_sz"] else f["px"]
    s = 1 if cur["side"] == "LONG" else -1
    return {"coin": cur["coin"], "side": cur["side"], "open_t": cur["open_t"], "close_t": f["t"],
            "hold_h": round((f["t"] - cur["open_t"]) / H1, 2), "entry": entry, "exit": exit_,
            "ret_pct": round(s * (exit_ / entry - 1) * 100, 3) if entry else None,
            "notional": round(cur["max_sz"] * entry, 2), "pnl": round(cur["pnl"], 2), "fills": cur["fills"]}


def style_ru(hold_h: float | None, per_day: float) -> str:
    if hold_h is None:
        return "—"
    if hold_h < 1:
        return "скальпер"
    if hold_h < 24:
        return "интрадей"
    if hold_h < 7 * 24:
        return "свинг"
    return "позиционный"


def portfolio_stats(pf) -> dict:
    """portfolio → all-time P&L curve: drawdown, share of positive 30-day steps, track length."""
    out = {}
    try:
        w = {k: v for k, v in pf}
    except Exception:  # noqa: BLE001
        return out
    for key in ("perpAllTime", "allTime"):
        h = (w.get(key) or {}).get("pnlHistory") or []
        if len(h) >= 3:
            pts = [(int(t), _f(v)) for t, v in h]
            peak, dd = -1e30, 0.0
            for _, v in pts:
                peak = max(peak, v); dd = max(dd, peak - v)
            months, j = [], 0
            t0 = pts[0][0]
            k = t0
            while k + 30 * DAY <= pts[-1][0]:
                a = _at(pts, k); b = _at(pts, k + 30 * DAY)
                months.append(b - a); k += 30 * DAY
            out.update(src=key, since=pts[0][0], pnl_curve_last=pts[-1][1], max_dd_usd=round(dd, 2),
                       recovery=round(pts[-1][1] / dd, 2) if dd > 0 else None,
                       months=len(months), months_pos=sum(1 for m in months if m > 0))
            break
    av = (w.get("allTime") or {}).get("accountValueHistory") or []
    if av:
        out["account_now"] = _f(av[-1][1])
    return out


def _at(pts, t):
    prev = pts[0][1]
    for tt, v in pts:
        if tt > t:
            return prev
        prev = v
    return prev


def price_at(candles: list, t: int) -> float | None:
    """Price at time t inside a 15m candle: linear from open (bar start) to close (bar end). candles sorted [t,o,h,l,c]."""
    lo, hi = 0, len(candles) - 1
    if not candles or t < candles[0][0] or t >= candles[-1][0] + M15:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if candles[mid][0] <= t:
            lo = mid
        else:
            hi = mid - 1
    c = candles[lo]
    if t >= c[0] + M15:
        return None                      # hole in the data
    fr = (t - c[0]) / M15
    return c[1] + (c[4] - c[1]) * fr


def copy_test(trips: list[dict], candles: dict, delay_min: int) -> dict:
    D = delay_min * 60_000
    rs, own, missed, nodata = [], [], 0, 0
    for tr in trips:
        if tr["close_t"] - tr["open_t"] < D:
            missed += 1
            continue
        cs = candles.get(tr["coin"])
        if not cs:
            nodata += 1
            continue
        a = price_at(cs, tr["open_t"] + D)
        b = cs[-1][4] if tr.get("open") else price_at(cs, tr["close_t"] + D)
        if not a or not b:
            nodata += 1
            continue
        s = 1 if tr["side"] == "LONG" else -1
        rs.append(s * (b / a - 1) * 100 - COST_PCT)
        if tr.get("ret_pct") is not None:
            own.append(tr["ret_pct"] - COST_PCT)
    out = {"delay_min": delay_min, "n": len(rs), "missed": missed, "no_data": nodata}
    if rs:
        sd = st.pstdev(rs) if len(rs) > 1 else 0.0
        out.update(avg_pct=round(st.mean(rs), 3), sum_pct=round(sum(rs), 2), win=round(sum(1 for x in rs if x > 0) / len(rs), 3),
                   t=round(st.mean(rs) / sd * math.sqrt(len(rs)), 2) if sd > 0 else None,
                   own_avg_pct=round(st.mean(own), 3) if own else None)
    return out


def profile(c: dict, trips: list, total: float, pf_stats: dict, positions: list, candles: dict, now: int, too_active: bool) -> dict:
    trips = [t for t in trips if t["open_t"] >= now - WINDOW or t.get("open")]
    n = len(trips)
    open_tr = [t for t in trips if t.get("open")]
    open_upnl = sum(t["pnl"] for t in open_tr)
    total = total + sum(t["pnl"] for t in open_tr)
    wins = [t for t in trips if t["pnl"] > 0]; losses = [t for t in trips if t["pnl"] <= 0]
    gp, gl = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    holds = [t["hold_h"] for t in trips]
    hold_med = round(st.median(holds), 2) if holds else None
    days = max(1.0, WINDOW / DAY)
    by_coin: dict[str, dict] = {}
    for t in trips:
        b = by_coin.setdefault(t["coin"], {"coin": t["coin"], "n": 0, "pnl": 0.0, "long": 0, "wins": 0})
        b["n"] += 1; b["pnl"] += t["pnl"]; b["long"] += t["side"] == "LONG"; b["wins"] += t["pnl"] > 0
    coins = sorted(by_coin.values(), key=lambda b: -b["pnl"])
    for b in coins:
        b["pnl"] = round(b["pnl"], 2)
    long_n = sum(1 for t in trips if t["side"] == "LONG")
    pnl_long = sum(t["pnl"] for t in trips if t["side"] == "LONG"); pnl_short = sum(t["pnl"] for t in trips if t["side"] == "SHORT")
    best = max(trips, key=lambda t: t["pnl"]) if trips else None
    worst = min(trips, key=lambda t: t["pnl"]) if trips else None
    copies = {d: copy_test(trips, candles, d) for d in DELAYS_MIN}
    levs = [p["lev"] for p in positions if p.get("lev")]
    src = c.get("src", "hl")
    p = {"src": src, "src_ru": SRC_RU.get(src, src), "addr": c["addr"], "short": c.get("short") or (c["addr"][:6] + "…" + c["addr"][-4:]), "name": c.get("name") or "",
         "account": round(c.get("account") or 0), "pnl_all": round(c.get("pnl_all") or 0), "pnl_m_lb": round(c.get("pnl_m") or 0),
         "roi_m": round((c.get("roi_m") or 0) * 100, 1), "pnl_w": round(c.get("pnl_w") or 0),
         "pnl_30d": round(total, 2), "trips": n, "wins": len(wins), "win": round(len(wins) / n, 3) if n else None,
         "pf": round(gp / gl, 2) if gl > 0 else (None if not gp else 99.0), "gross_profit": round(gp), "gross_loss": round(gl),
         "hold_med_h": hold_med, "per_day": round(n / days, 2), "style_ru": style_ru(hold_med, n / days),
         "long_share": round(long_n / n, 2) if n else None, "pnl_long": round(pnl_long), "pnl_short": round(pnl_short),
         "coins": coins[:6], "n_coins": len(coins), "best": best, "worst": worst,
         "best_share": round(best["pnl"] / gp, 2) if best and gp > 0 else None,
         "lev_now": round(st.median(levs), 1) if levs else None, "positions": positions[:8],
         "recent": sorted(trips, key=lambda t: -t["close_t"])[:12], "copy": {str(d): v for d, v in copies.items()},
         "portfolio": pf_stats, "too_active": too_active, "open_trips": len(open_tr), "open_upnl": round(open_upnl, 2)}
    why = []
    if too_active:
        why.append("слишком частые сделки (больше 8000 исполнений за 45 дней) — скопировать нельзя")
    if n < MIN_TRIPS:
        why.append(f"мало закрытых сделок за 30 дней ({n})")
    if hold_med is not None and hold_med < MIN_HOLD_H:
        why.append(f"держит слишком коротко (медиана {hold_med * 60:.0f} мин)")
    if total <= 0:
        why.append("за 30 дней в минусе (с учётом открытых позиций)")
    elif open_upnl < 0 and -open_upnl > 0.5 * (total - open_upnl):
        why.append(f"держит открытые позиции в минусе ({_usd(open_upnl)}) — прибыль закрытых сделок обманчива")
    if p["pf"] is not None and p["pf"] < MIN_PF:
        why.append(f"прибыль/убыток {p['pf']} < {MIN_PF}")
    p["eligible"] = not why
    p["excluded_ru"] = why
    c30 = copies[30]
    p["score"] = c30.get("t") if c30.get("t") is not None else -99.0
    return p


def describe(p: dict) -> str:
    parts = []
    hold = p["hold_med_h"]
    hold_s = (f"{hold * 60:.0f} мин" if hold < 1 else f"{hold:.0f} ч" if hold < 48 else f"{hold / 24:.0f} дн") if hold is not None else "—"
    parts.append(f"{p['style_ru'].capitalize()}: держит позицию обычно {hold_s}, сделок ~{p['per_day']:.1f} в день.")
    if p.get("src") == "okx" and p.get("followers"):
        parts.append(f"Лид-трейдер копитрейдинга OKX: за ним копируют {p['followers']} человек, ведёт сделки {p.get('lead_days') or '—'} дн.")
    if p["coins"]:
        top = p["coins"][0]
        parts.append(f"Больше всего зарабатывает на {top['coin']}: {_usd(top['pnl'])} за 30 дней из {_usd(p['pnl_30d'])} всего"
                     + (f", дальше {', '.join(c['coin'] for c in p['coins'][1:3] if c['pnl'] > 0)}" if any(c['pnl'] > 0 for c in p['coins'][1:3]) else "") + ".")
    if p["long_share"] is not None:
        side = "чаще в лонг" if p["long_share"] >= 0.6 else "чаще в шорт" if p["long_share"] <= 0.4 else "в обе стороны"
        parts.append(f"Торгует {side} ({p['long_share'] * 100:.0f}% сделок — лонги); на лонгах {_usd(p['pnl_long'])}, на шортах {_usd(p['pnl_short'])}.")
    if p["win"] is not None:
        pf_s = "убыточных сделок не было" if (p["pf"] or 0) >= 99 else f"прибыль к убытку {p['pf']}"
        parts.append(f"Прибыльных сделок {p['win'] * 100:.0f}%, {pf_s}." + (f" Плечо сейчас ~{p['lev_now']:.0f}×." if p["lev_now"] else ""))
    if p.get("open_trips"):
        parts.append(f"Сейчас открыто {p['open_trips']} позиций, по ним {_usd(p['open_upnl'])} (уже учтено в результате).")
    if p["best_share"] is not None and p["best_share"] >= 0.5:
        parts.append(f"Осторожно: половина прибыли — одна сделка ({p['best']['coin']} {_usd(p['best']['pnl'])}).")
    c = p["copy"].get("30") or {}
    if c.get("n"):
        parts.append(f"Если копировать с опозданием 30 мин: {c['avg_pct']:+.2f}% на сделку ({c['n']} сделок, в плюсе {c['win'] * 100:.0f}%)"
                     + (f"; у самого кита {c['own_avg_pct']:+.2f}%" if c.get("own_avg_pct") is not None else "") + ".")
    pf = p.get("portfolio") or {}
    if pf.get("months"):
        parts.append(f"За всю историю: плюсовых месяцев {pf['months_pos']} из {pf['months']}, максимальная просадка {_usd(-pf['max_dd_usd'])}.")
    return " ".join(parts)


def _usd(v) -> str:
    v = float(v or 0)
    a = abs(v)
    s = f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}k" if a >= 1e3 else f"${a:.0f}"
    return ("+" if v > 0 else "−" if v < 0 else "") + s


def norm_positions(state: dict) -> list[dict]:
    out = []
    for ap in (state or {}).get("assetPositions", []) or []:
        p = ap.get("position") or {}
        szi = _f(p.get("szi"))
        if not szi or not _perp(p.get("coin")):
            continue
        out.append({"coin": p.get("coin"), "side": "LONG" if szi > 0 else "SHORT", "size": abs(szi),
                    "notional": round(abs(_f(p.get("positionValue")))), "entry": _f(p.get("entryPx")),
                    "upnl": round(_f(p.get("unrealizedPnl"))), "lev": _f((p.get("leverage") or {}).get("value"))})
    return sorted(out, key=lambda x: -x["notional"])


# ------------------------------------------------------------------------------------------------------------ fetching
def refresh_whale(conn, c: dict, now: int, budget: Budget, post=_post, notes=None) -> bool:
    """Fills (incremental), portfolio and positions of one candidate, at most once a day. → True if fully fresh."""
    addr = c["addr"]
    r = conn.execute("SELECT fills_t, fills_full, portfolio_t, state_t FROM ws_whale WHERE addr=?", (addr,)).fetchone()
    if not r:
        conn.execute("INSERT INTO ws_whale (addr, lb) VALUES (?,?)", (addr, json.dumps(c)))
        r = (None, 0, None, None)
    else:
        conn.execute("UPDATE ws_whale SET lb=? WHERE addr=?", (json.dumps(c), addr))
    fills_t, full, pf_t, st_t = r
    ok = True
    if fills_t is None or now - fills_t >= REFRESH_MS:
        if not budget.take():
            return False
        start = max((fills_t or 0) + 1, now - FETCH_WINDOW)
        try:
            pages, full = 0, 0
            while True:
                page = post({"type": "userFillsByTime", "user": addr, "startTime": start, "aggregateByTime": True}) or []
                store_fills(conn, addr, page)
                pages += 1
                if len(page) < FILLS_PAGE:
                    break
                last = max(int(f.get("time") or 0) for f in page)
                if pages >= FILLS_PAGES or last <= start:
                    full = 1 if pages >= FILLS_PAGES else 0
                    break
                if not budget.take():
                    conn.commit()
                    return False                     # the next run continues (stored fills are kept, duplicates ignored)
                start = last + 1
                time.sleep(0.3)
            conn.execute("UPDATE ws_whale SET fills_t=?, fills_full=? WHERE addr=?", (now, full, addr))
        except Exception as e:  # noqa: BLE001
            ok = False
            if notes is not None:
                notes.append(f"сделки {addr[:8]}: {str(e)[:100]}")
    if pf_t is None or now - pf_t >= REFRESH_MS:
        if not budget.take():
            return False
        try:
            pf = post({"type": "portfolio", "user": addr})
            conn.execute("UPDATE ws_whale SET portfolio=?, portfolio_t=? WHERE addr=?", (json.dumps(pf), now, addr))
        except Exception as e:  # noqa: BLE001
            ok = False
            if notes is not None:
                notes.append(f"портфель {addr[:8]}: {str(e)[:100]}")
    if st_t is None or now - st_t >= 6 * H1:
        if not budget.take():
            return False
        try:
            s = post({"type": "clearinghouseState", "user": addr})
            conn.execute("UPDATE ws_whale SET state=?, state_t=? WHERE addr=?", (json.dumps(s), now, addr))
        except Exception as e:  # noqa: BLE001
            ok = False
    conn.commit()
    return ok


def refresh_candles(conn, coin: str, now: int, budget: Budget, post=_post) -> bool:
    r = conn.execute("SELECT until FROM ws_candle_fetch WHERE coin=?", (coin,)).fetchone()
    until = r[0] if r else None
    last_closed = now // M15 * M15 - M15
    if until is not None and now - until < 12 * H1:
        return True
    start = max((until or 0) + M15, now - WINDOW - 2 * DAY)
    if start > last_closed:
        return True
    if not budget.take():
        return False
    rows = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "15m", "startTime": start, "endTime": last_closed + M15 - 1}}) or []
    data = []
    for x in rows:
        try:
            t = int(x["t"])
            if t + M15 <= now:
                data.append((coin, t, _f(x["o"]), _f(x["h"]), _f(x["l"]), _f(x["c"])))
        except Exception:  # noqa: BLE001
            continue
    conn.executemany("INSERT OR REPLACE INTO ws_candles VALUES (?,?,?,?,?,?)", data)
    got = max((d[1] for d in data), default=None)
    # 5000 candles max per answer (52 days of 15m) — a 32-day window always fits; record coverage to the last closed bar
    conn.execute("INSERT OR REPLACE INTO ws_candle_fetch VALUES (?,?)", (coin, max(got or 0, last_closed) if isinstance(rows, list) else (until or start - M15)))
    conn.commit()
    return True


def candles_of(conn, coin: str, since: int) -> list:
    return [list(r) for r in conn.execute("SELECT t, o, h, l, c FROM ws_candles WHERE coin=? AND t>=? ORDER BY t", (coin, since))]


# ------------------------------------------------------------------------------------------------------------ OKX lead traders
def _okx_get(path: str, params: dict, timeout: float = 20):
    q = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    req = urllib.request.Request(f"{OKX}{path}?{q}", headers={"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode())
    if str(j.get("code")) not in ("0", ""):
        raise RuntimeError(f"OKX {j.get('code')}: {j.get('msg')}")
    return j.get("data") or []


def okx_candidates(get=_okx_get, pages: int = 3) -> tuple[list[dict], dict]:
    """OKX public lead traders (perpetual swaps), sorted by P&L; keep those leading ≥ 90 days (minLeadDays=3) with P&L > 0."""
    rows, why = [], {"rows": 0, "short_record": 0, "loss": 0}
    for page in range(1, pages + 1):
        data = get("/api/v5/copytrading/public-lead-traders", {"instType": "SWAP", "sortType": "pnl", "minLeadDays": 3, "page": page, "limit": 20})
        ranks = (data[0] if data else {}).get("ranks") or []
        for x in ranks:
            why["rows"] += 1
            pnl = _f(x.get("pnl"))
            if pnl <= 0:
                why["loss"] += 1; continue
            code = x.get("uniqueCode")
            if not code:
                continue
            rows.append({"src": "okx", "addr": f"okx:{code}", "code": code, "name": x.get("nickName") or "", "short": (x.get("nickName") or code)[:18],
                         "account": _f(x.get("aum")), "pnl_all": pnl, "pnl_m": None, "roi_m": None, "pnl_ratio": _f(x.get("pnlRatio")),
                         "win_ratio": _f(x.get("winRatio")), "followers": int(_f(x.get("copyTraderNum"))), "lead_days": int(_f(x.get("leadDays"))),
                         "insts": x.get("traderInsts") or []})
        if len(ranks) < 20:
            break
    rows.sort(key=lambda r: -r["pnl_all"])
    why["passed"] = len(rows)
    return rows[:OKX_PRE_N], why


def _okx_coin(inst: str) -> str:
    return (inst or "").split("-")[0]


def refresh_okx(conn, c: dict, now: int, budget: Budget, get=_okx_get, notes=None) -> bool:
    """Closed sub-positions (last 3 months, newest first, 100 per page) and current ones of one OKX lead trader, once a day."""
    addr = c["addr"]
    r = conn.execute("SELECT fills_t, state_t FROM ws_whale WHERE addr=?", (addr,)).fetchone()
    if not r:
        conn.execute("INSERT INTO ws_whale (addr, lb) VALUES (?,?)", (addr, json.dumps(c)))
        r = (None, None)
    else:
        conn.execute("UPDATE ws_whale SET lb=? WHERE addr=?", (json.dumps(c), addr))
    hist_t, st_t = r
    if hist_t is None or now - hist_t >= REFRESH_MS:
        after, got = None, 0
        for _ in range(6):
            if not budget.take():
                return False
            try:
                data = get("/api/v5/copytrading/public-subpositions-history", {"instType": "SWAP", "uniqueCode": c["code"], "limit": 100, "after": after})
            except Exception as e:  # noqa: BLE001
                if notes is not None:
                    notes.append(f"OKX история {c['name'][:12]}: {str(e)[:100]}")
                break
            rows = []
            for x in data:
                try:
                    ot, ct = int(x["openTime"]), int(x["closeTime"])
                    side = (x.get("posSide") or "").lower()
                    if side == "net":
                        side = "long" if _f(x.get("subPos")) > 0 else "short"
                    e, ex = _f(x.get("openAvgPx")), _f(x.get("closeAvgPx"))
                    lev = _f(x.get("lever"), 1.0)
                    rows.append(("okx", addr, str(x.get("subPosId")), _okx_coin(x.get("instId")), "LONG" if side == "long" else "SHORT",
                                 ot, ct, e, ex, _f(x.get("pnl")), _f(x.get("margin")) * lev, lev))
                except Exception:  # noqa: BLE001
                    continue
            conn.executemany("INSERT OR REPLACE INTO ws_trips VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            got += len(rows)
            if len(data) < 100 or not data:
                break
            after = data[-1].get("subPosId")
            if rows and min(x[6] for x in rows) < now - WINDOW - DAY:
                break
            time.sleep(0.45)
        conn.execute("UPDATE ws_whale SET fills_t=? WHERE addr=?", (now, addr))
    if st_t is None or now - st_t >= 6 * H1:
        if not budget.take():
            return False
        try:
            cur = get("/api/v5/copytrading/public-current-subpositions", {"instType": "SWAP", "uniqueCode": c["code"], "limit": 100})
            conn.execute("UPDATE ws_whale SET state=?, state_t=? WHERE addr=?", (json.dumps({"okx": cur}), now, addr))
        except Exception as e:  # noqa: BLE001
            if notes is not None:
                notes.append(f"OKX позиции {c['name'][:12]}: {str(e)[:100]}")
    conn.commit()
    time.sleep(0.45)
    return True


def okx_positions(state: dict) -> list[dict]:
    """Current OKX sub-positions merged per coin (a lead trader may hold several sub-positions of one coin)."""
    agg: dict[tuple, dict] = {}
    for x in (state or {}).get("okx") or []:
        coin = _okx_coin(x.get("instId"))
        side = (x.get("posSide") or "").lower()
        if side == "net":
            side = "long" if _f(x.get("subPos")) > 0 else "short"
        side = "LONG" if side == "long" else "SHORT"
        lev = _f(x.get("lever"), 1.0)
        a = agg.setdefault((coin, side), {"coin": coin, "side": side, "size": 0.0, "notional": 0.0, "entry": _f(x.get("openAvgPx")),
                                          "upnl": 0.0, "lev": lev})
        a["notional"] += _f(x.get("margin")) * lev; a["upnl"] += _f(x.get("upl")); a["size"] += abs(_f(x.get("subPos")))
    return sorted(({**v, "notional": round(v["notional"]), "upnl": round(v["upnl"])} for v in agg.values()), key=lambda x: -x["notional"])


def okx_trips(conn, addr: str, since: int) -> tuple[list[dict], float]:
    out, tot = [], 0.0
    for coin, side, ot, ct, e, ex, pnl, notional, lev in conn.execute(
            "SELECT coin, side, open_t, close_t, entry, exit, pnl, notional, lever FROM ws_trips WHERE src='okx' AND trader=? AND close_t>=? ORDER BY open_t",
            (addr, since)):
        s = 1 if side == "LONG" else -1
        out.append({"coin": coin, "side": side, "open_t": ot, "close_t": ct, "hold_h": round((ct - ot) / H1, 2), "entry": e, "exit": ex,
                    "ret_pct": round(s * (ex / e - 1) * 100, 3) if e else None, "notional": round(notional, 2), "pnl": round(pnl, 2), "fills": 1,
                    "lev": lev})
        if ct >= since:
            tot += pnl
    return out, tot


# ------------------------------------------------------------------------------------------------------------ copy desk
def desk_step(desk: dict | None, follow: list[str], states: dict, mids: dict, now: int) -> dict:
    """One run of the paper copy desk. states = {addr: [positions]} for the whales fetched this run (missing addr = not
    fetched → nothing changes for him). desk['seen'][addr] = {coin: side} from the previous run."""
    d = desk or {"start": DESK_START, "cash": DESK_START, "open": [], "closed": [], "seen": {}, "history": [], "started": now}
    d.setdefault("events", [])
    seen = d["seen"]

    def mark(o):
        px = _f(mids.get(o["coin"]))
        if not px:
            return 0.0
        s = 1 if o["side"] == "LONG" else -1
        return o["notional"] * s * (px / o["entry"] - 1)

    def close(o, why):
        px = _f(mids.get(o["coin"]))
        if not px:
            return False
        s = 1 if o["side"] == "LONG" else -1
        gross = o["notional"] * s * (px / o["entry"] - 1)
        cost = o["notional"] * COST_PCT / 100
        pnl = max(gross - cost, -o["margin"])
        d["cash"] += pnl
        d["open"].remove(o)
        d["closed"].append({**o, "exit": px, "closed_t": now, "pnl": round(pnl, 2), "pnl_pct": round(pnl / o["margin"] * 100, 2), "why": why})
        d["events"].append({"t": now, "kind": "close", "coin": o["coin"], "side": o["side"], "whale": o["whale"], "pnl": round(pnl, 2), "why": why})
        return True

    for addr, pos in states.items():
        cur = {p["coin"]: p for p in pos}
        prev = seen.get(addr)
        # closes: our copies of this whale whose coin is gone or flipped
        for o in [o for o in d["open"] if o["whale"] == addr]:
            p = cur.get(o["coin"])
            if p is None:
                close(o, "кит закрыл")
            elif p["side"] != o["side"]:
                close(o, "кит развернулся")
        # opens: only positions that appeared since the previous look at this whale, and only for whales we follow
        if prev is not None and addr in follow:
            for coin, p in cur.items():
                if prev.get(coin) == p["side"]:
                    continue
                if any(o["coin"] == coin for o in d["open"]) or len(d["open"]) >= DESK_MAX_OPEN:
                    continue
                px = _f(mids.get(coin))
                if not px:
                    continue
                eq = d["cash"] + sum(mark(o) for o in d["open"])
                margin = round(eq * DESK_MARGIN, 2)
                lev = min(max(1.0, p.get("lev") or 1.0), DESK_LEV_CAP)
                o = {"whale": addr, "coin": coin, "side": p["side"], "entry": px, "opened_t": now, "margin": margin, "lev": lev,
                     "notional": round(margin * lev, 2), "whale_entry": p.get("entry"), "whale_notional": p.get("notional")}
                d["open"].append(o)
                d["events"].append({"t": now, "kind": "open", "coin": coin, "side": p["side"], "whale": addr, "px": px})
        seen[addr] = {c: p["side"] for c, p in cur.items()}
    eq = d["cash"] + sum(mark(o) for o in d["open"])
    for o in d["open"]:
        o["upnl"] = round(mark(o), 2)
        o["price"] = _f(mids.get(o["coin"])) or None
    d["equity"] = round(eq, 2)
    d["history"].append([now, round(eq, 2)])
    if len(d["history"]) > 2000:
        d["history"] = d["history"][::2]
    d["closed"] = d["closed"][-300:]
    d["events"] = d["events"][-200:]
    return d


def desk_public(d: dict, names: dict) -> dict:
    cl = d.get("closed") or []
    wins = sum(1 for c in cl if c["pnl"] > 0)
    peak, dd = d["start"], 0.0
    for _, v in d.get("history") or []:
        peak = max(peak, v); dd = max(dd, (peak - v) / peak * 100 if peak else 0)
    lab = lambda a: names.get(a) or (a[:6] + "…" + a[-4:])
    return {"name_ru": "Киты · копирование топ-5", "start": d["start"], "started": d.get("started"), "equity": d.get("equity", d["start"]),
            "pnl_usd": round(d.get("equity", d["start"]) - d["start"], 2), "pnl_pct": round((d.get("equity", d["start"]) / d["start"] - 1) * 100, 2),
            "trades": len(cl), "wins": wins, "winrate": round(wins / len(cl), 3) if cl else None, "max_dd_pct": round(dd, 2),
            "open": [{**o, "whale_ru": lab(o["whale"])} for o in d.get("open") or []],
            "closed": [{**c, "whale_ru": lab(c["whale"])} for c in sorted(cl, key=lambda c: -c["closed_t"])[:20]],
            "history": d.get("history", [])[-500:],
            "rules_ru": f"Старт $10 000. Повторяем только новые позиции топ-5 китов, замеченные между двумя проверками (сервер смотрит раз в ~30 мин): "
                        f"вход по текущей цене, маржа {DESK_MARGIN * 100:.0f}% счёта, плечо как у кита, но не больше {DESK_LEV_CAP:.0f}×, не больше "
                        f"{DESK_MAX_OPEN} позиций и одна на монету. Выходим, когда кит закрыл или развернул позицию. Комиссии {COST_PCT}% за сделку."}


# ------------------------------------------------------------------------------------------------------------ entry point
def hl_name(coin: str, mids: dict) -> str | None:
    """Hyperliquid name of a coin (1000× tokens are kPEPE, kSHIB, …)."""
    if not mids:
        return coin
    if coin in mids:
        return coin
    if "k" + coin in mids:
        return "k" + coin
    if coin.startswith("1000") and "k" + coin[4:] in mids:
        return "k" + coin[4:]
    return None


def run(conn: sqlite3.Connection, now: int | None = None, post=_post, get_lb=_get_lb, okx_get=_okx_get,
        max_requests: int = 90, budget_s: float = 170) -> dict:
    now = int(now if now is not None else time.time() * 1000)
    ensure_schema(conn)
    notes: list[str] = []
    budget = Budget(max_requests, budget_s)
    since = now - WINDOW
    # 1. candidates from both sources, once a day (kept between runs)
    cand = kv_get(conn, "candidates") or {}
    if not cand or now - cand.get("t", 0) >= REFRESH_MS:
        new = {"t": now, "rows": [], "why": {}, "okx_rows": [], "okx_why": {}}
        try:
            rows, why = candidates(get_lb())
            new.update(rows=rows, why=why)
        except Exception as e:  # noqa: BLE001
            notes.append(f"лидерборд Hyperliquid: {str(e)[:120]}")
            new.update(rows=cand.get("rows") or [], why=cand.get("why") or {})
        try:
            orows, owhy = okx_candidates(okx_get)
            new.update(okx_rows=orows, okx_why=owhy)
        except Exception as e:  # noqa: BLE001
            notes.append(f"OKX лид-трейдеры: {str(e)[:120]}")
            new.update(okx_rows=cand.get("okx_rows") or [], okx_why=cand.get("okx_why") or {})
        if new["rows"] or new["okx_rows"]:
            cand = new
            kv_set(conn, "candidates", cand)
    rows, orows = cand.get("rows") or [], cand.get("okx_rows") or []
    # 2. prices (names for mapping, marks for the desk)
    mids = {}
    try:
        if budget.take():
            mids = post({"type": "allMids"}) or {}
    except Exception as e:  # noqa: BLE001
        notes.append(f"цены Hyperliquid: {str(e)[:100]}")
    # 3. copy desk (every run): fresh positions of followed traders + traders we still hold copies of
    desk = kv_get(conn, "desk")
    study = kv_get(conn, "study") or {}
    follow = [p["addr"] for p in study.get("top", [])]
    watch = list(dict.fromkeys(follow + [o["whale"] for o in (desk or {}).get("open", [])]))
    states = {}
    if watch and mids:
        okx_codes = {r["addr"]: r["code"] for r in orows}
        for a in watch:
            if not budget.take():
                break
            try:
                if a.startswith("okx:"):
                    code = okx_codes.get(a) or a[4:]
                    cur = okx_get("/api/v5/copytrading/public-current-subpositions", {"instType": "SWAP", "uniqueCode": code, "limit": 100})
                    pos = okx_positions({"okx": cur})
                    conn.execute("UPDATE ws_whale SET state=?, state_t=? WHERE addr=?", (json.dumps({"okx": cur}), now, a))
                else:
                    stt = post({"type": "clearinghouseState", "user": a})
                    pos = norm_positions(stt)
                    conn.execute("UPDATE ws_whale SET state=?, state_t=? WHERE addr=?", (json.dumps(stt), now, a))
                states[a] = [{**x, "coin": hl_name(x["coin"], mids) or x["coin"]} for x in pos]
            except Exception as e:  # noqa: BLE001
                notes.append(f"позиции {a[:12]}: {str(e)[:100]}")
            time.sleep(0.25)
        desk = desk_step(desk, follow, states, mids, now)
        kv_set(conn, "desk", desk)
        conn.commit()
    # 4. study data, budgeted and resumable; both sources take turns so neither waits for the other
    if kv_get(conn, "fills_v") != FILLS_V:
        conn.execute("UPDATE ws_whale SET fills_t=NULL WHERE addr NOT LIKE 'okx:%'")
        kv_set(conn, "fills_v", FILLS_V)
    fresh = ofresh = 0
    stop_hl = stop_okx = False
    for n in range(max(len(rows), len(orows))):
        if n < len(rows) and not stop_hl:
            if refresh_whale(conn, rows[n], now, budget, post, notes):
                fresh += 1
            else:
                stop_hl = True
            time.sleep(0.2)
        if n < len(orows) and not stop_okx:
            if refresh_okx(conn, orows[n], now, budget, okx_get, notes):
                ofresh += 1
            else:
                stop_okx = True
        if stop_hl and stop_okx:
            break
    need = []
    for c in rows:
        for (coin,) in conn.execute("SELECT DISTINCT coin FROM ws_fills WHERE addr=? AND t>=?", (c["addr"], since)):
            if coin not in need:
                need.append(coin)
    for c in orows:
        for (coin,) in conn.execute("SELECT DISTINCT coin FROM ws_trips WHERE trader=? AND close_t>=?", (c["addr"], since)):
            hn = hl_name(coin, mids)
            if hn and hn not in need:
                need.append(hn)
    cdone = 0
    for coin in need:
        try:
            if not refresh_candles(conn, coin, now, budget, post):
                break
            cdone += 1
        except Exception as e:  # noqa: BLE001
            notes.append(f"свечи {coin}: {str(e)[:100]}")
            cdone += 1
        time.sleep(0.2)
    complete = fresh == len(rows) and ofresh == len(orows) and cdone == len(need)
    # 5. profiles (from cached data, every run)
    profs = []
    for c in rows + orows:
        r = conn.execute("SELECT fills_t, fills_full, portfolio, state FROM ws_whale WHERE addr=?", (c["addr"],)).fetchone()
        if not r or r[0] is None:
            continue
        if c.get("src") == "okx":
            trips, total = okx_trips(conn, c["addr"], since)
            st_ = json.loads(r[3]) if r[3] else {}
            for x in st_.get("okx") or []:            # open sub-positions at the mark price
                try:
                    side = (x.get("posSide") or "").lower()
                    if side == "net":
                        side = "long" if _f(x.get("subPos")) > 0 else "short"
                    e_, m_ = _f(x.get("openAvgPx")), _f(x.get("markPx"))
                    sg = 1 if side == "long" else -1
                    trips.append({"coin": _okx_coin(x.get("instId")), "side": "LONG" if side == "long" else "SHORT", "open_t": int(x.get("openTime")),
                                  "close_t": now, "hold_h": round((now - int(x.get("openTime"))) / H1, 2), "entry": e_, "exit": m_,
                                  "ret_pct": round(sg * (m_ / e_ - 1) * 100, 3) if e_ and m_ else None, "notional": round(_f(x.get("margin")) * _f(x.get("lever"), 1.0), 2),
                                  "pnl": round(_f(x.get("upl")), 2), "fills": 1, "open": True})
                except Exception:  # noqa: BLE001
                    continue
            for t in trips:
                t["coin"] = hl_name(t["coin"], mids) or t["coin"]
            pos = okx_positions(json.loads(r[3])) if r[3] else []
            pfs = {}
        else:
            trips, total = round_trips(load_fills(conn, c["addr"], now - FETCH_WINDOW), since, marks=mids, now=now)
            pos = norm_positions(json.loads(r[3])) if r[3] else []
            pfs = portfolio_stats(json.loads(r[2])) if r[2] else {}
        if c["addr"] in states:
            pos = states[c["addr"]]
        cs = {coin: candles_of(conn, coin, since - DAY) for coin in {t["coin"] for t in trips}}
        p = profile(c, trips, total, pfs, pos, cs, now, bool(r[1]) and c.get("src") != "okx")
        if c.get("src") == "okx":
            p.update(followers=c.get("followers"), lead_days=c.get("lead_days"), okx_pnl_all=c.get("pnl_all"), okx_win=c.get("win_ratio"))
        profs.append(p)
    elig = sorted([p for p in profs if p["eligible"]], key=lambda p: (-(p["score"] if p["score"] is not None else -99), -p["pnl_30d"]))
    top = elig[:TOP_N]
    for i, p in enumerate(top):
        p["rank"] = i + 1
        p["desc_ru"] = describe(p)
        p["copy_ok"] = (p["copy"].get("30") or {}).get("avg_pct", -1) > 0
    follow_new = [{"addr": p["addr"], "short": p["short"], "name": p["name"]} for p in top if p["copy_ok"]][:FOLLOW_N]
    if follow_new and (complete or not study.get("top")):
        kv_set(conn, "study", {"t": now, "top": follow_new, "partial": not complete})
    conn.commit()
    names = {p["addr"]: (p["name"] or p["short"]) for p in profs}
    excluded = {}
    for p in profs:
        for w in p["excluded_ru"]:
            k = w.split(" (")[0]
            excluded[k] = excluded.get(k, 0) + 1
    st_now = kv_get(conn, "study") or {}
    out = {"asof": now, "complete": complete, "study_t": st_now.get("t"), "following": st_now.get("top", []),
           "funnel": {"hl_leaderboard": cand.get("why", {}).get("rows"), "hl_after_filters": cand.get("why", {}).get("passed"),
                      "hl_dropped": cand.get("why"), "okx_lead_traders": cand.get("okx_why", {}).get("rows"),
                      "okx_after_filters": cand.get("okx_why", {}).get("passed"),
                      "studied": len(profs), "eligible": len(elig), "top": len(top), "excluded": excluded,
                      "waiting": (len(rows) - fresh) + (len(orows) - ofresh), "candles_waiting": len(need) - cdone},
           "top": top,
           "others": [{k: p.get(k) for k in ("src", "addr", "short", "name", "pnl_30d", "trips", "win", "pf", "hold_med_h", "style_ru", "excluded_ru", "score")}
                      for p in sorted(profs, key=lambda p: -p["pnl_30d"]) if p not in top][:30],
           "desk": desk_public(desk, names) if desk else None,
           "not_covered_ru": "Binance и Bybit копитрейдинг пока не подключены: их данные с сервера в США закрыты. Чемпионаты (Bybit WSOT и другие) "
                             "публикуют только итоговые проценты без сделок — повторить такую торговлю нельзя, поэтому в рейтинг они не входят.",
           "method_ru": [
               "Источники с открытой историей сделок: Hyperliquid (каждая сделка в блокчейне) и лид-трейдеры копитрейдинга OKX (официальные открытые данные биржи).",
               "Hyperliquid: счёт от $300k, за всё время от +$500k, за месяц в плюсе, не маркетмейкер (оборот ≤ 1000× счёта), без «доходности» больше 1000% за месяц "
               "(это пополнения, а не торговля); 30 крупнейших по прибыли за всё время. OKX: лид-трейдеры с историей от 90 дней и прибылью; 20 крупнейших по прибыли.",
               "По каждому — реальные сделки за 30 дней: каждая позиция от открытия до закрытия, результат, время удержания, монеты, сторона.",
               "В топ попадает только тот, у кого ≥ 8 закрытых сделок, держит позицию не меньше 30 минут (иначе не успеем повторить), за 30 дней в плюсе, прибыль к убытку ≥ 1.2.",
               "Порядок — по тому, что дало бы копирование с опозданием 30 минут (так видит сервер): средний итог на сделку с учётом разброса и числа сделок.",
               "Это прошлый месяц, а не обещание: киты хеджируются на других биржах, а сервер видит их сделки с опозданием. Стратегия «Киты» — эксперимент на демо."],
           "notes_ru": notes[:20]}
    return out
