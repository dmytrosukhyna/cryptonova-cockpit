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
5. Eligible (04.11 17:46, owner + docs/research/whale-rules-2026-11-04.md) = over 60 days ≥ 20 decisions (parts within 4 h = one),
   ≥ 70 % in profit and PF ≥ 2 counting open positions at the mark (hidden OKX positions in loss = losing decisions), median
   hold ≥ 4 h, 60-day P&L > 0, open losses ≤ 20 % of the closed profit. Rank = PF incl. open, then 60-day P&L. The desk follows
   the best 5 whose 30-min-delay copy test is positive and whose positions are visible. Closed trades are kept daily in
   data/whale_trades/<day>.jsonl (archive_trades) so the selection can be re-tested on 6+ months of our own history.
6. Copy desk (every run): $10 000 paper account follows the top 5 — a NEW position of a followed whale (seen appear
   between two runs) is opened at the current mid, margin 10 % of equity, leverage = whale's but ≤ 5×, at most 8 at
   once, one per coin; closed when the whale closes or flips. 04.11 17:37 (owner): a position he already holds is joined too
   while our price is ≤ 1 % worse and ≤ 3 % better than his own entry (after a fresh look; the first / a stale look only sets the baseline).

Honesty: whales hedge across venues, the server sees changes only every ~30 minutes, and a past month is a small
sample — the copy test says what the delay costs; the desk is an experiment until it has its own record.
Output: web/whales_top.json. Stdlib only.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics as st
import time
import urllib.request

from ..execution import owner_overrides as ov
from . import whale_sources as WS
from . import vip_whales as _vw

SEEN_MAX_MS = 2 * 3_600_000      # 04.11: a whale's last look older than this → baseline only (no copies from it)
# 04.11 17:37 (owner, real money: «Входить и в их открытые»): the desk also joins a followed whale's ALREADY OPEN position, but
# only while the price is no worse than JOIN_MAX from the whale's own entry (long: px ≤ entry × 1.01; short: px ≥ entry × 0.99)
JOIN_MAX = 0.01
# 04.11 17:41 (owner: «бывает, цена откатилась в другую сторону — возможно, тренд уже изменился»): the window is two-sided — a
# price more than JOIN_BETTER better than his entry means the whale is down > 3 % on price → no join, wait for his next trade
JOIN_BETTER = 0.03
JOIN_REDO_MS = 7 * 86_400_000    # a copy of this whale's position stopped out → that same position is not re-joined for 7 days
GOOD_WIN, GOOD_PF = 0.70, 1.5    # several candidates at once → whales with a 30-day record of ≥ 70 % wins and PF ≥ 1.5 go first
LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
HL = "https://api.hyperliquid.xyz/info"
M15, H1, DAY = 900_000, 3_600_000, 86_400_000
# 04.11 17:46 (owner: «отбери по всему миру лучших китов — у кого хорошая статистика за долгий период, это подтверждается»), research
# docs/research/whale-rules-2026-11-04.md (blind test: picked on 60 days, copied the next 45 → 12 of 12 in profit): a whale is followed only
# with ≥ 20 decisions in 60 days, ≥ 70 % of them in profit and PF ≥ 2 COUNTING OPEN POSITIONS at the mark (hidden OKX positions in loss
# = losing decisions), median hold ≥ 4 h, and open losses ≤ 20 % of the closed profit (whales hide losers in open positions:
# AGD88888 90 % closed wins → 62 % with the open ones). Rank: PF incl. open, then 60-day P&L.
WINDOW = 60 * DAY
DECISION_MS = 4 * 3_600_000            # parts of one coin + side opened within 4 h of the first = one decision
MIN_DECISIONS, MIN_WIN, OPEN_LOSS_MAX = 20, 0.70, 0.20
CANDLE_DAYS = 50                       # Hyperliquid serves the newest 5000 candles (52 days of 15m) — the copy test covers 50 days
PRE_N = 30
PRE_M = 20                            # 04.11: + the best 20 by 30-day P&L that the all-time top misses
TOP_N = 10                            # «топ-10 трейдеров» on the panel
# 05.11 09:52 (owner: «если топ-10 все шикарные, то можно и все 10»): every whale that passes the full bar and the delayed-copy test
# is followed, up to 10; the next ones are the bench (whales_top.json «bench», shown on the panel)
FOLLOW_N = 10
BENCH_N = 10
# 06.11 (owner: «надо взять топ лучших и с ними работать … хотя бы 3 дня понаблюдать; если кит допустит ошибку, это не значит, что
# надо сразу его вычёркивать»; chose «7 китов на 3 дня»): the list was rebuilt from zero every run (PF moves with the whales' open
# positions, a whale not reached by this run's budget fell out as «не изучен») — 10+ changes a day. Now it is FIXED for LOCK_MS: at a
# review the incumbents that still pass stay, free places go to whales that passed the bar for PROVEN_MS in a row (the first lock:
# the best now). Between reviews a whale leaves only for a hard reason (lock_hard): hides his positions, 60 days went negative or no
# data for a day. A whale who leaves the list goes to the bench / follow_dropped to be watched; his new decisions are not copied.
# 09.11 (owner, explicit yes, backlog 117: «не надо выбрасывать … сделки должны дойти до своих значений»): (1) the «copies lost more
# than LOCK_LOSS of his share» reason is OFF (LOCK_LOSS_ON) — a whale changes only at the review; (2) copies a leaver already holds
# are NOT closed: they run to his own exit (or our stop / day stop). Only a blind copy is closed: he hides his positions or we have
# not seen them for UNFOLLOW_BLIND_MS (desk_step close_unfollowed).
LOCK_N = 7
LOCK_MS = 3 * 86_400_000
PROVEN_MS = 3 * 86_400_000
LOCK_LOSS = 0.10
LOCK_LOSS_ON = False                  # 09.11 owner: the loss reason is off (kept for the record / research)
UNFOLLOW_BLIND_MS = 3_600_000         # 09.11: a leaver's copy we cannot see for 1 h is closed (cannot see his exit)
LOCK_RULE = "lock-v1"
MIN_AV, MIN_ALL = 300_000.0, 500_000.0
MAX_VLM_X, MAX_ROI_M = 400.0, 10.0
FETCH_WINDOW = 75 * DAY               # fills fetched for 75 days so positions opened before the 60-day window are known
FILLS_PAGES = 5                       # up to 10000 fills (Hyperliquid keeps no more); a full set = high-frequency
FILLS_V = 3                           # 04.11: 3 = the 60-day window — every HL / OKX history is fetched again once
OKX_HIST_PAGES = 12                   # 100 closed sub-positions per page
FILLS_PAGE = 2000
# 04.11 22:35 (owner): the median-hold ≥ 4 h rule is dropped — short-hold whales (招财猫13, 1.4 h) are followed too, because the
# server now copies within ~1–2 minutes (cnq/execution/whale_live.py); the 2-minute delayed-copy test decides who is copyable
MIN_TRIPS, MIN_HOLD_H, MIN_PF = MIN_DECISIONS, 0.0, 2.0
DELAYS_MIN = (2, 15, 30, 60)
COPY_DELAY = 2                        # minutes: what the server's live copy loop achieves (poll every ~60 s + the order)
COST_PCT = 0.11                       # taker 0.045 % × 2 + slippage 0.02 %
REFRESH_MS = DAY                      # study data per whale refreshed once a day
# 05.11 09:52 (owner: «Какое у них плечо, такое и у нас»): the whale's own leverage (hidden → the median of his record), 20× backstop,
# never so high that liquidation comes before our stop (LIQ_ROOM ÷ stop distance)
DESK_START, DESK_MARGIN, DESK_LEV_CAP, DESK_MAX_OPEN = 10_000.0, 0.10, 20.0, 15
LIQ_ROOM = 0.8
# 04.11 (owner, real money: «стол копирования китов … срочно внедрять; повторяй их плечи; логику применять, но себя не ужимать»):
# the desk now also trades the way the real OKX sleeve can follow it — every copy has a protective stop (the whale's own stop
# when he has one on the exchange, else −8 % from our entry) and one whale never takes more than 30 % of the desk's equity as margin
DESK_STOP = 0.16                      # protective stop, fraction of the entry price. 05.11 (owner «проверь, исследуй и сделай как можно
#   лучше»): −8 % → −16 %. 9 110 copied decisions of 56 OKX/HL whales, +2 min, costs + funding, risk-sized (result in R): −16 % +0.036 R
#   (t 8.6, worst run −30 R) vs −8 % +0.029 R (t 3.8, −90 R) vs −4 % (= the whale's 20×) +0.010 R (t 0.75, −222 R); both halves and
#   walk-forward-selected whales agree (docs/research/whale-stops-2026-11-05.md). Liquidation stays behind the stop → leverage ≤ 5×.
# 04.11 22:35 / 22:38 (owner: «пересмотреть, чтобы мы могли подключаться и зарабатывать вместе с ними»; «в основном бюджет должен
# работать»): no fixed 30 % / 10 % — a new copy takes the FREE margin ÷ the slots still expected today (the followed whales' own
# signal frequency, at least 2), and one whale's copies hold at most WHALE_CAP (a soft cap, so an active whale may use more than 1/N
# while the others are quiet). Every real trade keeps its own caps in okx_futures (2 % risk, 3 % day stop, 5×, margin ≤ budget).
WHALE_CAP = 0.50
MIN_SLOTS, MIN_COPY_MARGIN = 2, 0.02
STOP_MIN, STOP_MAX = 0.002, 0.15      # the whale's own stop is used only when it sits 0.2–15 % away on the protective side
UA = {"Content-Type": "application/json", "User-Agent": "cryptonova-whale-study/1.0"}
OKX = "https://www.okx.com"
CAND_V = 2                            # 04.11 21:14: a new candidate pool (wider OKX / Binance) is read at once, not the next day
OKX_PRE_N = 60                        # 04.11 21:14 (owner: more whales, same bar): the whole public pool is ~220 (11 pages × 20)
OKX_PRE_PNL, OKX_WIN_MIN = 40, 0.50
# 04.11 22:35 (owner named them): always studied when they are in OKX's public list, whatever their rank
OKX_NAMED = ("bestmax", "queenstowntraders")   # 40 best by P&L + the best by OKX's own win ratio; < 50 % OKX win ratio cannot reach 70 % decisions
SRC_RU = {"hl": "Hyperliquid · сделки в блокчейне", "okx": "OKX · лид-трейдер копитрейдинга"}
SRC_RU.update({k: v["ru"] for k, v in WS.SOURCES.items()})   # 04.11: Binance lead portfolios (cnq.agents.whale_sources)

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
    why["passed"] = len(rows)
    # 04.11 (owner: «китов находить … и добавлять к нам»): the top by all-time P&L misses this month's best traders — the top
    # PRE_M by 30-day P&L that are not already in are studied too (same filters; the record test decides, not the leaderboard)
    by_all = sorted(rows, key=lambda r: -r["pnl_all"])[:n]
    have = {r["addr"] for r in by_all}
    by_month = [dict(r, pick="month") for r in sorted(rows, key=lambda r: -r["pnl_m"]) if r["addr"] not in have][:PRE_M]
    why["month_slice"] = len(by_month)
    return by_all + by_month, why


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


def decisions(trips: list[dict]) -> list[dict]:
    """One decision = one entry (research 04.11): trips of the same coin + side opened within 4 h of the first one are merged
    (entry / exit / return weighted by size, P&L summed, open if any part is still open). Hidden positions (no coin) stay apart."""
    by: dict[tuple, list] = {}
    for t in trips:
        by.setdefault((t["coin"], t["side"]), []).append(t)
    out = []
    for ts in by.values():
        ts.sort(key=lambda x: x["open_t"])
        grp: list = []
        for t in ts:
            if grp and t["open_t"] - grp[0]["open_t"] <= DECISION_MS:
                grp.append(t)
            else:
                if grp:
                    out.append(grp)
                grp = [t]
        if grp:
            out.append(grp)
    res = []
    for g in out:
        if len(g) == 1:
            res.append(dict(g[0], parts=1))
            continue
        w = [max(_f(x.get("notional")), 1e-9) for x in g]
        W_ = sum(w)
        rets = [(x["ret_pct"], wi) for x, wi in zip(g, w) if x.get("ret_pct") is not None]
        ot, ct = min(x["open_t"] for x in g), max(x["close_t"] for x in g)
        res.append({**g[0], "open_t": ot, "close_t": ct, "hold_h": round((ct - ot) / H1, 2),
                    "entry": sum(_f(x["entry"]) * wi for x, wi in zip(g, w)) / W_, "exit": sum(_f(x["exit"]) * wi for x, wi in zip(g, w)) / W_,
                    "ret_pct": round(sum(r * wi for r, wi in rets) / sum(wi for _, wi in rets), 3) if rets else None,
                    "notional": round(W_, 2), "pnl": round(sum(x["pnl"] for x in g), 2), "fills": sum(x.get("fills") or 1 for x in g),
                    "open": any(x.get("open") for x in g), "parts": len(g)})
    res.sort(key=lambda t: t["open_t"])
    return res


def _wl(xs: list[dict]) -> tuple[float | None, float | None, float, float]:
    """(win share, profit factor, gross profit, gross loss) of decisions"""
    gp = sum(t["pnl"] for t in xs if t["pnl"] > 0); gl = -sum(t["pnl"] for t in xs if t["pnl"] <= 0)
    win = round(sum(1 for t in xs if t["pnl"] > 0) / len(xs), 3) if xs else None
    pf = round(gp / gl, 2) if gl > 0 else (None if not gp else 99.0)
    return win, pf, gp, gl


def profile(c: dict, trips: list, total: float, pf_stats: dict, positions: list, candles: dict, now: int, too_active: bool) -> dict:
    """60-day record of one whale in DECISIONS, open positions counted at the mark (04.11, owner + research): trips → decisions
    closed in the window or still open; hidden open positions (OKX shows only their P&L) in loss = losing decisions."""
    since = now - WINDOW
    hid = [t for t in trips if t.get("hidden")]
    dec = [t for t in decisions([t for t in trips if not t.get("hidden")]) if t.get("open") or t["close_t"] >= since]
    hid_loss = [t for t in hid if t["pnl"] < 0]
    allx = dec + hid_loss
    n = len(allx)
    closed = [t for t in dec if not t.get("open")]
    open_tr = [t for t in dec if t.get("open")] + hid
    open_upnl = sum(t["pnl"] for t in open_tr)
    open_loss = -sum(t["pnl"] for t in open_tr if t["pnl"] < 0)
    closed_pnl = sum(t["pnl"] for t in closed)
    total = total + open_upnl
    pnl_30 = sum(t["pnl"] for t in closed if t["close_t"] >= now - 30 * DAY) + open_upnl
    win, pf, gp, gl = _wl(allx)
    win_c, pf_c, _, _ = _wl(closed)
    holds = [t["hold_h"] for t in dec]
    hold_med = round(st.median(holds), 2) if holds else None
    days = max(1.0, WINDOW / DAY)
    by_coin: dict[str, dict] = {}
    for t in dec:
        b = by_coin.setdefault(t["coin"], {"coin": t["coin"], "n": 0, "pnl": 0.0, "long": 0, "wins": 0})
        b["n"] += 1; b["pnl"] += t["pnl"]; b["long"] += t["side"] == "LONG"; b["wins"] += t["pnl"] > 0
    coins = sorted(by_coin.values(), key=lambda b: -b["pnl"])
    for b in coins:
        b["pnl"] = round(b["pnl"], 2)
    nd = len(dec)
    long_n = sum(1 for t in dec if t["side"] == "LONG")
    pnl_long = sum(t["pnl"] for t in dec if t["side"] == "LONG"); pnl_short = sum(t["pnl"] for t in dec if t["side"] == "SHORT")
    best = max(dec, key=lambda t: t["pnl"]) if dec else None
    worst = min(dec, key=lambda t: t["pnl"]) if dec else None
    copies = {d: copy_test(dec, candles, d) for d in DELAYS_MIN}
    levs = [p["lev"] for p in positions if p.get("lev")]
    hist_levs = [_f(t.get("lev")) for t in trips if _f(t.get("lev"))] + [_f(x) for x in levs if _f(x)]
    src = c.get("src", "hl")
    p = {"src": src, "src_ru": SRC_RU.get(src, src), "addr": c["addr"], "short": c.get("short") or (c["addr"][:6] + "…" + c["addr"][-4:]), "name": c.get("name") or "",
         "account": round(c.get("account") or 0), "pnl_all": round(c.get("pnl_all") or 0), "pnl_m_lb": round(c.get("pnl_m") or 0),
         "roi_m": round((c.get("roi_m") or 0) * 100, 1), "pnl_w": round(c.get("pnl_w") or 0),
         "window_days": int(WINDOW / DAY), "pnl_60d": round(total, 2), "pnl_30d": round(pnl_30, 2), "closed_pnl": round(closed_pnl, 2),
         "trips": n, "decisions": n, "wins": sum(1 for t in allx if t["pnl"] > 0), "win": win, "pf": pf,
         "win_closed": win_c, "pf_closed": pf_c, "gross_profit": round(gp), "gross_loss": round(gl),
         "hold_med_h": hold_med, "per_day": round(nd / days, 2), "style_ru": style_ru(hold_med, nd / days),
         "long_share": round(long_n / nd, 2) if nd else None, "pnl_long": round(pnl_long), "pnl_short": round(pnl_short),
         "coins": coins[:6], "n_coins": len(coins), "best": best, "worst": worst,
         "best_share": round(best["pnl"] / gp, 2) if best and gp > 0 else None,
         "lev_now": round(st.median(levs), 1) if levs else None, "positions": positions[:8],
         "lev_med": round(st.median(hist_levs), 1) if hist_levs else None,
         "recent": sorted(dec, key=lambda t: -t["close_t"])[:12], "copy": {str(d): v for d, v in copies.items()},
         "portfolio": pf_stats, "too_active": too_active, "open_trips": len(open_tr), "open_upnl": round(open_upnl, 2),
         "open_loss": round(open_loss, 2), "hidden_open": len(hid), "hidden_loss_n": len(hid_loss),
         "max_dd_usd": _vw.max_dd(allx)}         # 04.11: VIP «Киты» watchlist (display only, not a selection rule)
    why = []
    if too_active:
        why.append("слишком частые сделки (больше 10000 исполнений за 75 дней) — скопировать нельзя")
    if n < MIN_DECISIONS:
        why.append(f"мало решений за 60 дней ({n}, нужно {MIN_DECISIONS})")
    if hold_med is not None and hold_med < MIN_HOLD_H:
        why.append(f"держит слишком коротко (медиана {hold_med * 60:.0f} мин, нужно от {MIN_HOLD_H:g} ч)" if hold_med < 1 else
                   f"держит слишком коротко (медиана {hold_med:.1f} ч, нужно от {MIN_HOLD_H:g} ч)")
    if total <= 0:
        why.append("за 60 дней в минусе (с учётом открытых позиций)")
    if open_loss > 0 and open_loss > OPEN_LOSS_MAX * max(closed_pnl, 0.0):
        why.append(f"прячет убытки в открытых позициях ({_usd(-open_loss)} — больше {OPEN_LOSS_MAX * 100:.0f}% прибыли закрытых {_usd(closed_pnl)})")
    if win is None or win < MIN_WIN:
        why.append(f"прибыльных решений {(win or 0) * 100:.0f}% (нужно от {MIN_WIN * 100:.0f}%, с открытыми)")
    if pf is None or pf < MIN_PF:
        why.append(f"прибыль/убыток {pf if pf is not None else '—'} (нужно от {MIN_PF:g}, с открытыми)")
    p["eligible"] = not why
    p["excluded_ru"] = why
    p["score"] = pf if pf is not None else -99.0          # 04.11: rank = PF incl. open, then 60-day P&L
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
        parts.append(f"Больше всего зарабатывает на {top['coin']}: {_usd(top['pnl'])} за 60 дней из {_usd(p['pnl_60d'])} всего"
                     + (f", дальше {', '.join(c['coin'] for c in p['coins'][1:3] if c['pnl'] > 0)}" if any(c['pnl'] > 0 for c in p['coins'][1:3]) else "") + ".")
    if p["long_share"] is not None:
        side = "чаще в лонг" if p["long_share"] >= 0.6 else "чаще в шорт" if p["long_share"] <= 0.4 else "в обе стороны"
        parts.append(f"Торгует {side} ({p['long_share'] * 100:.0f}% сделок — лонги); на лонгах {_usd(p['pnl_long'])}, на шортах {_usd(p['pnl_short'])}.")
    if p["win"] is not None:
        pf_s = "убыточных решений не было" if (p["pf"] or 0) >= 99 else f"прибыль к убытку {p['pf']}"
        parts.append(f"За 60 дней {p.get('decisions', p['trips'])} решений, в плюсе {p['win'] * 100:.0f}%, {pf_s} — с открытыми позициями по текущей цене"
                     + (f" (по одним закрытым {p['win_closed'] * 100:.0f}%)" if p.get("win_closed") is not None and abs(p["win_closed"] - p["win"]) >= 0.05 else "")
                     + "." + (f" Плечо сейчас ~{p['lev_now']:.0f}×." if p["lev_now"] else ""))
    if p.get("open_trips"):
        parts.append(f"Сейчас открыто {p['open_trips']} позиций, по ним {_usd(p['open_upnl'])} (уже учтено в результате).")
    if p["best_share"] is not None and p["best_share"] >= 0.5:
        parts.append(f"Осторожно: половина прибыли — одна сделка ({p['best']['coin']} {_usd(p['best']['pnl'])}).")
    c = p["copy"].get(str(COPY_DELAY)) or p["copy"].get("30") or {}
    if c.get("n"):
        parts.append(f"Если копировать с опозданием {c.get('delay_min', 30)} мин: {c['avg_pct']:+.2f}% на сделку ({c['n']} сделок, в плюсе {c['win'] * 100:.0f}%)"
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
    start = max((until or 0) + M15, now - CANDLE_DAYS * DAY)
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


def okx_candidates(get=_okx_get, pages: int = 12) -> tuple[list[dict], dict]:
    """OKX public lead traders (perpetual swaps) leading ≥ 90 days (minLeadDays=3) with P&L > 0 and OKX win ratio ≥ 50 %.
    04.11 21:14: the whole pool by P&L (all pages, ~220) + the win-ratio sort; studied = the best 40 by P&L + the best of the
    rest by win ratio, OKX_PRE_N in all — the 60-day record bar (profile) decides, not this list."""
    rows, why, seen = [], {"rows": 0, "short_record": 0, "loss": 0, "win": 0}, set()
    for sort, npg in (("pnl", pages), ("win_ratio", min(pages, 5))):
        for page in range(1, npg + 1):
            data = get("/api/v5/copytrading/public-lead-traders", {"instType": "SWAP", "sortType": sort, "minLeadDays": 3, "page": page, "limit": 20})
            d0 = data[0] if data else {}
            ranks = d0.get("ranks") or []
            for x in ranks:
                code = x.get("uniqueCode")
                if not code or code in seen:
                    continue
                seen.add(code)
                why["rows"] += 1
                pnl = _f(x.get("pnl"))
                if pnl <= 0:
                    why["loss"] += 1; continue
                if x.get("winRatio") not in (None, "") and _f(x.get("winRatio")) < OKX_WIN_MIN:
                    why["win"] += 1; continue
                rows.append({"src": "okx", "addr": f"okx:{code}", "code": code, "name": x.get("nickName") or "", "short": (x.get("nickName") or code)[:18],
                             "account": _f(x.get("aum")), "pnl_all": pnl, "pnl_m": None, "roi_m": None, "pnl_ratio": _f(x.get("pnlRatio")),
                             "win_ratio": _f(x.get("winRatio")), "followers": int(_f(x.get("copyTraderNum"))), "lead_days": int(_f(x.get("leadDays"))),
                             "insts": x.get("traderInsts") or [], "pick": sort})
            if len(ranks) < 20 or page >= int(_f(d0.get("totalPage"), npg)):
                break
            time.sleep(0.25)
    norm = lambda x: "".join(ch for ch in str(x or "").lower() if ch.isalnum())          # noqa: E731
    named = [dict(r, pick="owner") for r in rows if norm(r["name"]) in OKX_NAMED]
    why["named"] = [r["name"] for r in named]
    by_pnl = sorted((r for r in rows if norm(r["name"]) not in OKX_NAMED), key=lambda r: -r["pnl_all"])
    pick = named + by_pnl[:OKX_PRE_PNL]
    have = {r["addr"] for r in pick}
    pick += sorted((r for r in rows if r["addr"] not in have), key=lambda r: (-r["win_ratio"], -r["pnl_all"]))[:OKX_PRE_N - len(pick)]
    why["passed"] = len(rows)
    why["studied"] = len(pick)
    return pick, why


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
        # newest first: a first look reads back to the start of the 60-day window, a daily refresh only past the previous look
        stop_at = max(now - WINDOW - DAY, (hist_t - 2 * DAY) if hist_t else 0)
        for _ in range(OKX_HIST_PAGES):
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
            if rows and min(x[6] for x in rows) < stop_at:
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
        if _f(x.get("slTriggerPx")) and not a.get("sl"):          # the lead trader's own stop on OKX (04.11), when he set one
            a["sl"] = _f(x.get("slTriggerPx"))
    return sorted(({**v, "notional": round(v["notional"]), "upnl": round(v["upnl"])} for v in agg.values()), key=lambda x: -x["notional"])


def okx_trips(conn, addr: str, since: int) -> tuple[list[dict], float]:
    out, tot = [], 0.0
    for coin, side, ot, ct, e, ex, pnl, notional, lev in conn.execute(
            "SELECT coin, side, open_t, close_t, entry, exit, pnl, notional, lever FROM ws_trips WHERE trader=? AND close_t>=? ORDER BY open_t",
            (addr, since)):
        s = 1 if side == "LONG" else -1
        out.append({"coin": coin, "side": side, "open_t": ot, "close_t": ct, "hold_h": round((ct - ot) / H1, 2), "entry": e, "exit": ex,
                    "ret_pct": round(s * (ex / e - 1) * 100, 3) if e else None, "notional": round(notional, 2), "pnl": round(pnl, 2), "fills": 1,
                    "lev": lev})
        if ct >= since:
            tot += pnl
    return out, tot


def okx_open_trips(state: dict, now: int) -> list[dict]:
    """current OKX sub-positions as open trips at the mark; a sub-position whose instrument / price OKX hides is kept with only its
    P&L and «hidden» (04.11: a hidden one in loss counts as a losing decision — the trader cannot look better by hiding it)"""
    out = []
    for x in (state or {}).get("okx") or []:
        try:
            side = (x.get("posSide") or "").lower()
            if side == "net":
                side = "long" if _f(x.get("subPos")) > 0 else "short"
            e_, m_ = _f(x.get("openAvgPx")), _f(x.get("markPx"))
            ot = int(_f(x.get("openTime"))) or now
            sg = 1 if side == "long" else -1
            hid = not (x.get("instId") and e_ and m_)
            out.append({"coin": "" if hid else _okx_coin(x.get("instId")), "side": "LONG" if side == "long" else "SHORT", "open_t": min(ot, now),
                        "close_t": now, "hold_h": round((now - min(ot, now)) / H1, 2), "entry": e_, "exit": m_,
                        "ret_pct": None if hid else round(sg * (m_ / e_ - 1) * 100, 3), "notional": round(_f(x.get("margin")) * _f(x.get("lever"), 1.0), 2),
                        "pnl": round(_f(x.get("upl")), 2), "fills": 1, "open": True, **({"hidden": True} if hid else {})})
        except Exception:  # noqa: BLE001
            continue
    return out


# ------------------------------------------------------------------------------------------------------------ copy desk
def stop_for(side: str, entry: float, whale_sl: float | None = None) -> tuple[float, str]:
    """(stop price, where it comes from): the whale's own stop when it is on the protective side 0.2–15 % away, else −16 % from
    our entry (04.11 owner; −8 % → −16 % on 05.11, see DESK_STOP)"""
    s = 1 if side == "LONG" else -1
    w = _f(whale_sl)
    if w and entry:
        away = s * (1 - w / entry)                       # > 0 = below a long's entry / above a short's
        if STOP_MIN <= away <= STOP_MAX:
            return w, "стоп кита"
    return entry * (1 - s * DESK_STOP), f"защитный стоп −{DESK_STOP * 100:g}%"


def hidden(pos: list) -> bool:
    """a whale whose open positions come without the instrument (OKX lead traders may hide their positions: instId, size and
    entry are empty, only margin / leverage / P&L are shown) — 30.10 the top-7 OKX traders turned it on; copying them is blind"""
    return any(not (p or {}).get("coin") for p in pos or [])


def day_key(t: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(t) / 1000))


def join_gap(side: str, px: float, whale_entry) -> float | None:
    """how much worse our price is than the whale's own entry, as a fraction (> 0 = worse: above a long's entry, below a
    short's); None when the whale's entry is unknown"""
    e = _f(whale_entry)
    if not e or not px:
        return None
    return (px / e - 1) if side == "LONG" else (1 - px / e)


def whale_desk_pnl(desk: dict | None, addr: str, since: int) -> float:
    """the desk's result on one whale since a moment: copies closed after it + the open copies at their last mark (upnl)"""
    d = desk or {}
    pnl = sum(_f(c.get("pnl")) for c in d.get("closed") or [] if c.get("whale") == addr and int(_f(c.get("closed_t"))) >= since)
    return pnl + sum(_f(o.get("upnl")) for o in d.get("open") or [] if o.get("whale") == addr)


def lock_hard(a: str, q: dict | None, passing: dict, desk: dict | None, since: int, share: float, now: int) -> list[str]:
    """06.11: the hard reasons a locked whale leaves the list before the review ([] = he stays)"""
    if q is not None:
        if hidden(q.get("positions") or []):
            return ["скрыл открытые позиции"]
        if _f(q.get("pnl_60d")) < 0:
            return ["за 60 дней ушёл в минус"]
    elif now - int(_f((passing.get(a) or [0, 0])[1])) > 86_400_000:
        return ["нет данных о ките больше суток"]
    loss = whale_desk_pnl(desk, a, since)
    if LOCK_LOSS_ON and share > 0 and loss <= -LOCK_LOSS * share:
        return [f"на столе −${-loss:,.0f} с входа в список — больше {LOCK_LOSS * 100:.0f}% его доли ${share:,.0f}".replace(",", " ")]
    return []


def lock_follow(study: dict, ranked: list[dict], byaddr: dict, desk: dict | None, now: int) -> tuple[list, dict, int, dict]:
    """06.11 (owner): the fixed list. ranked = whales passing the full bar now (eligible, copy test positive, positions visible), best
    first; byaddr = every profile studied this run. → (top, why_dropped {addr: [why]}, lock_t, passing {addr: [first_t, last_t]})"""
    passing = {a: list(v) for a, v in (study.get("passing") or {}).items() if isinstance(v, (list, tuple)) and len(v) == 2}
    ok = {x["addr"]: x for x in ranked}
    for a in ok:
        passing[a] = [(passing.get(a) or [now])[0], now]
    for a in list(passing):
        if (a in byaddr and a not in ok) or now - int(_f(passing[a][1])) > 7 * 86_400_000:
            passing.pop(a)                                    # studied and failed (or not seen for a week) → the streak starts again
    old = list(study.get("top") or []) if study.get("rule") == LOCK_RULE else []
    lock_t = int(_f(study.get("lock_t"))) if study.get("rule") == LOCK_RULE else 0
    joined = dict(study.get("joined") or {}) if study.get("rule") == LOCK_RULE else {}
    share = _f((desk or {}).get("equity") or DESK_START) / LOCK_N
    why: dict = {}
    if not lock_t:                                            # the first lock: the best now
        top = ranked[:LOCK_N]
        lock_t = now
    elif now - lock_t >= LOCK_MS:                             # the review
        keep = []
        for x in old:
            a = x["addr"]
            if a in ok or (a not in byaddr and now - int(_f((passing.get(a) or [0, 0])[1])) <= 86_400_000):
                h = lock_hard(a, byaddr.get(a), passing, desk, int(_f(joined.get(a))) or lock_t, share, now)
                if h:
                    why[a] = h
                else:
                    keep.append(ok.get(a, x))
        keep.sort(key=lambda x: next((i for i, r in enumerate(ranked) if r["addr"] == x["addr"]), len(ranked)))
        ks = {x["addr"] for x in keep}
        fill = [x for x in ranked if x["addr"] not in ks and now - int(_f(passing[x["addr"]][0])) >= PROVEN_MS]
        top = (keep + fill)[:LOCK_N]
        lock_t = now
    else:                                                     # between reviews: only hard reasons
        top = []
        for x in old:
            a = x["addr"]
            h = lock_hard(a, byaddr.get(a), passing, desk, int(_f(joined.get(a))) or lock_t, share, now)
            if h:
                why[a] = h
            else:
                top.append(ok.get(a, x))
    return top, why, lock_t, passing


# 08.11 (owner via the coordinator): a whale dropped from the locked list by OUR bug (not by the rules) is reinstated once — only if he
# passes the full bar in this run (studied by address), into a free place of the lock, with his original join date (no newcomer wait).
# config/whale_reinstate.json {"id", "addrs": {addr: {"joined": ms, "name"}}, "why_ru"}; applied ids are kept in ws_kv «reinstated».
REINSTATE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config", "whale_reinstate.json")


def reinstate_cfg(path: str | None = None) -> dict:
    try:
        with open(path or REINSTATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("id") and isinstance(d.get("addrs"), dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def reinstate_pending(conn, cfg: dict) -> dict:
    done = (kv_get(conn, "reinstated") or {}).get(cfg.get("id") or "", {}) if cfg else {}
    return {a: v for a, v in (cfg.get("addrs") or {}).items() if a not in done}


def locked_rows(conn, study: dict, have: list[dict], extra: dict | None = None) -> tuple[list[dict], list[dict]]:
    """08.11 (backlog 111): a locked whale who slipped out of the day's leaderboard slice (Binance: 35 of ~260 by 30-day P&L) was
    not studied, and after 24 h the lock dropped him as «нет данных о ките больше суток» and closed his copies (07.11 乾坤策略量化),
    although his record is one request away. Such whales are studied by address from their stored row → (okx rows, added-source rows).
    Only data coverage: the bar and the lock's hard reasons are unchanged."""
    if study.get("rule") != LOCK_RULE:
        return [], []
    seen = {r.get("addr") for r in have}
    lo, le = [], []
    for x in list(study.get("top") or []) + [{"addr": a, "name": (v or {}).get("name")} for a, v in (extra or {}).items()]:
        a = str((x or {}).get("addr") or "")
        if not a or a in seen or ":" not in a:
            continue
        k = WS.src_of(a)
        if not k and not a.startswith("okx:"):
            continue
        lb = WS.lb_of(conn, a)
        nm = lb.get("name") or x.get("name") or x.get("short") or a
        row = {**lb, "src": k or "okx", "addr": a, "code": lb.get("code") or a.split(":", 1)[1], "name": nm,
               "short": lb.get("short") or x.get("short") or nm[:18], "locked": True}
        (le if k else lo).append(row)
        seen.add(a)
    return lo, le


def copy_ok(p: dict) -> bool:
    """04.11 22:35: copyable = the delayed-copy test at the server's real delay (2 min) is positive; a profile from before the 2-minute
    test → the 30-minute one"""
    c = (p.get("copy") or {}).get(str(COPY_DELAY)) or (p.get("copy") or {}).get("30") or {}
    return bool(c.get("n")) and (c.get("avg_pct") if c.get("avg_pct") is not None else -1) > 0


# 05.11 09:52 (owner: «деньги должны работать … где-то лояльнее, где-то скругляем углы»): how much of the desk's equity may sit in
# copies — the rolling 14-day record decides: PF > 1.5 and drawdown < 5 % → up to 90 %; PF ≥ 1 and drawdown < 10 % (or too few trades
# to judge) → 75 %; worse → 50 %. The per-trade caps of the real account (2 % risk, 3 % day stop, stops at OKX) never change.
UTIL_GOOD, UTIL_BASE, UTIL_BAD = 0.90, 0.75, 0.50
# 05.11 14:12 (owner: «$3 000 лежит, а копий нет»): a copy is sized like the REAL account sizes it — risk COPY_RISK of the equity from
# the stop distance (okx_futures.size: 2 % of the budget), notional = risk ÷ stop distance, margin = notional ÷ leverage. Utilisation =
# that margin ÷ equity, so «97 %» can no longer block new copies while the real futures budget is 77 % idle. Older copies count with
# min(their recorded margin, this risk margin) — what the real account really holds for them.
COPY_RISK = 0.02


def risk_margin(o: dict, eq: float) -> float:
    """the margin a copy really needs at COPY_RISK of the equity (≤ its recorded margin)"""
    e, stp, lev = _f(o.get("entry")), _f(o.get("stop")), max(1.0, _f(o.get("lev"), 1.0))
    m = _f(o.get("margin"))
    if not e or not stp or e == stp:
        return m
    return min(m, COPY_RISK * eq / (abs(e - stp) / e) / lev)
UTIL_DAYS, UTIL_MIN_TRADES = 14, 5


def util_cap(d: dict, now: int) -> tuple[float, dict]:
    """(share of equity the copies may hold as margin, the 14-day record behind it)"""
    since = now - UTIL_DAYS * DAY
    cl = [c for c in d.get("closed") or [] if int(_f(c.get("closed_t"))) >= since]
    gp = sum(_f(c.get("pnl")) for c in cl if _f(c.get("pnl")) > 0)
    gl = -sum(_f(c.get("pnl")) for c in cl if _f(c.get("pnl")) < 0)
    pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else None)
    vals = [v for t, v in (d.get("history") or []) if t >= since] or [d.get("equity") or d.get("start") or DESK_START]
    peak, dd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak if peak else 0.0)
    rec = {"trades": len(cl), "pf": None if pf is None else round(pf, 2), "dd_pct": round(dd * 100, 2), "days": UTIL_DAYS}
    if len(cl) < UTIL_MIN_TRADES:
        return UTIL_BASE, {**rec, "why_ru": f"мало сделок за {UTIL_DAYS} дней ({len(cl)}) — базовая загрузка {UTIL_BASE * 100:.0f}%"}
    if pf is not None and pf > 1.5 and dd < 0.05:
        return UTIL_GOOD, {**rec, "why_ru": f"за {UTIL_DAYS} дней PF {pf:.2f} > 1,5 и просадка {dd * 100:.1f}% < 5% — загрузка до {UTIL_GOOD * 100:.0f}%"}
    if pf is not None and pf >= 1.0 and dd < 0.10:
        return UTIL_BASE, {**rec, "why_ru": f"за {UTIL_DAYS} дней PF {pf:.2f}, просадка {dd * 100:.1f}% — загрузка до {UTIL_BASE * 100:.0f}%"}
    return UTIL_BAD, {**rec, "why_ru": f"за {UTIL_DAYS} дней PF {pf if pf is None else round(pf, 2)}, просадка {dd * 100:.1f}% — осторожно, загрузка до {UTIL_BAD * 100:.0f}%"}


def expected_slots(quality: dict | None, follow: list, now: int) -> int:
    """copies still expected today: the followed whales' decisions per day × the share of the UTC day left, at least MIN_SLOTS"""
    left = 1 - (now % DAY) / DAY
    exp = sum(_f(((quality or {}).get(a) or {}).get("per_day")) for a in follow) * left
    return max(MIN_SLOTS, int(math.ceil(exp)))


def good_whale(q: dict | None) -> bool:
    q = q or {}
    return _f(q.get("win")) >= GOOD_WIN and _f(q.get("pf")) >= GOOD_PF


def desk_step(desk: dict | None, follow: list[str], states: dict, mids: dict, now: int, owner: dict | None = None,
              quality: dict | None = None, close_unfollowed: bool = False) -> dict:
    """One run of the paper copy desk. states = {addr: [positions]} for the whales fetched this run (missing addr = not
    fetched → nothing changes for him). desk['seen'][addr] = {coin: side} from the previous run.
    04.11: every copy carries a stop (the whale's, else −8 %) checked every run at the current mid; one whale's copies never hold
    more than 30 % of the equity as margin; every close adds to the whale's daily record (desk['by_whale_day']).
    04.11 17:37 (owner): a followed whale's ALREADY OPEN position is joined too while our price is ≤ 1 % worse than his entry
    (JOIN_MAX) and he is not down more than 3 % on price (JOIN_BETTER, 17:41), only on a fresh look (a stale / first look is a baseline). quality = {addr: {win, pf}} (30 days): when several
    positions are candidates, whales with ≥ 70 % wins and PF ≥ 1.5 go first, then new positions, then the smallest gap."""
    d = desk or {"start": DESK_START, "cash": DESK_START, "open": [], "closed": [], "seen": {}, "history": [], "started": now}
    d.setdefault("events", [])
    d.setdefault("by_whale_day", {})
    seen = d["seen"]
    cands: list[tuple] = []

    def mark(o):
        px = _f(mids.get(o["coin"]))
        if not px:
            return 0.0
        s = 1 if o["side"] == "LONG" else -1
        return o["notional"] * s * (px / o["entry"] - 1)

    def close(o, why, px=None):
        px = px or _f(mids.get(o["coin"]))
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
        day = d["by_whale_day"].setdefault(day_key(now), {})
        w = day.setdefault(o["whale"], {"n": 0, "wins": 0, "pnl": 0.0})
        w["n"] += 1; w["wins"] += 1 if pnl > 0 else 0; w["pnl"] = round(w["pnl"] + pnl, 2)
        return True

    # stops first (04.11): a copy whose price crossed its stop is closed at the current mid, whatever the whale does
    for o in list(d["open"]):
        px = _f(mids.get(o["coin"]))
        stp = _f(o.get("stop"))
        if px and stp and ((o["side"] == "LONG" and px <= stp) or (o["side"] == "SHORT" and px >= stp)):
            close(o, f"стоп ({o.get('stop_src') or 'защитный'})", px)
    # 04.11 (owner, config/owner_overrides.json): a copy the owner closed / a side he forbade is closed now and not re-opened
    if owner:
        gone = set(owner.get("closed_ids") or ())
        for o in list(d["open"]):
            if paper_trade(o)["id"] in gone:          # 05.11: closed by the owner by hand on OKX → closed here too, never re-joined
                close(o, "закрыта владельцем вручную на OKX")
                d.setdefault("owner_closed", {})[f"{o['whale']}|{o['coin']}|{o['side']}"] = now
        for o in list(d["open"]):
            w = ov.blocked(owner, o["coin"], o["side"], o.get("opened_t"))
            if w:
                close(o, w)
    # 06.11 (owner) the copies of a whale who left the fixed list were closed at once. 09.11 (owner, explicit yes, backlog 117: on
    # history such copies ended better with the whale's own exit in 4 of 4 samples): they are held to his exit / our stop; only a
    # BLIND copy is closed — he hides his positions, or we have not seen them for UNFOLLOW_BLIND_MS (never on an empty list)
    if close_unfollowed and follow:
        fs = set(follow)
        for o in list(d["open"]):
            if o["whale"] in fs:
                continue
            pos = states.get(o["whale"])
            if pos is not None and hidden(pos):
                close(o, "кит убран из списка и скрыл позиции")
            elif pos is None and now - int((d.get("seen_t") or {}).get(o["whale"]) or 0) > UNFOLLOW_BLIND_MS:
                close(o, "кит убран из списка, его позиций не видно больше часа")
    for addr, pos in states.items():
        if hidden(pos):                    # 04.11: OKX hides this lead trader's instruments — we cannot tell what he holds: no
            d["events"].append({"t": now, "kind": "hidden", "whale": addr})     # close and no open for him this run (stops still work)
            d["events"] = d["events"][-200:]
            continue
        cur = {p["coin"]: p for p in pos}
        prev = seen.get(addr)
        # 04.11 bug fix: a whale looked at long ago (e.g. dropped from the follow list, then back) has a stale «seen» — every position
        # he opened in between looked «new» and was copied late (HYPE/ZRO shorts of SilkBtc, 04.11 06:33 UTC, his entries far from
        # ours). A look older than SEEN_MAX_MS is only a new baseline: nothing is opened from it.
        if now - int((d.get("seen_t") or {}).get(addr) or 0) > SEEN_MAX_MS:
            prev = None
        # closes: our copies of this whale whose coin is gone or flipped
        for o in [o for o in d["open"] if o["whale"] == addr]:
            p = cur.get(o["coin"])
            if p is None:
                close(o, "кит закрыл")
            elif p["side"] != o["side"]:
                close(o, "кит развернулся")
            elif p.get("sl"):                                  # the whale moved his stop → ours follows when it is usable
                ns, src = stop_for(o["side"], o["entry"], p.get("sl"))
                if src == "стоп кита":
                    o["stop"], o["stop_src"] = round(ns, 8), src
        # opens: positions that appeared since the previous look (new) and, 04.11 17:37, positions he already held (join) —
        # only on a fresh look and only for whales we follow; collected here, opened below in order of preference
        if prev is not None and addr in follow:
            for coin, p in cur.items():
                kind = "join" if prev.get(coin) == p["side"] else "new"
                cands.append((addr, coin, p, kind))
        seen[addr] = {c: p["side"] for c, p in cur.items()}
        # 06.11 (owner, Ivan: «never copy his adds / averaging-down»): an owner pick's entry is FIXED at first sight — his later
        # averaging moves his avgPrice, not our join window (one copy per position, never added to)
        if addr.startswith("ivan:"):
            fe = d.setdefault("first_entry", {})
            keep = {f"{addr}|{c}|{p['side']}" for c, p in cur.items()}
            for k_ in [k_ for k_ in fe if k_.startswith(addr + "|") and k_ not in keep]:
                fe.pop(k_)
            for c, p in cur.items():
                fe.setdefault(f"{addr}|{c}|{p['side']}", p.get("entry"))
        d.setdefault("seen_t", {})[addr] = now
    wait_prev, wait_now = d.get("join_wait") or {}, {}

    def note_wait(key, ev):                  # a join that is not possible now is logged once, not on every run
        wait_now[key] = ev.get("gap_pct")
        if key not in wait_prev:
            d["events"].append(ev)

    rank = []
    for addr, coin, p, kind in cands:
        px = _f(mids.get(coin))
        w_entry = (d.get("first_entry") or {}).get(f"{addr}|{coin}|{p['side']}") or p.get("entry")
        gap = join_gap(p["side"], px, w_entry)
        rank.append((0 if good_whale((quality or {}).get(addr)) else 1, 0 if kind == "new" else 1, gap if gap is not None else 9.0,
                     addr, coin, p, kind, px, gap))
    rank.sort(key=lambda r: r[:3])
    for _q, _k, _g, addr, coin, p, kind, px, gap in rank:
        key = f"{addr}|{coin}|{p['side']}"
        if any(o["coin"] == coin for o in d["open"]) or len(d["open"]) >= DESK_MAX_OPEN:
            continue
        if not px:
            continue
        gap_pct = round(gap * 100, 2) if gap is not None else None
        if kind == "join":
            if gap is None:
                continue                                     # the whale's entry is unknown — cannot measure the gap
            if gap > JOIN_MAX:
                note_wait(key, {"t": now, "kind": "join_wait", "coin": coin, "side": p["side"], "whale": addr, "gap_pct": gap_pct,
                                "why": f"цена на {gap_pct:.2f}% хуже входа кита (предел {JOIN_MAX * 100:g}%) — ждём его следующую сделку"})
                continue
            if gap < -JOIN_BETTER:
                note_wait(key, {"t": now, "kind": "join_wait", "coin": coin, "side": p["side"], "whale": addr, "gap_pct": gap_pct,
                                "why": f"кит в минусе на {-gap_pct:.2f}% по цене (предел {JOIN_BETTER * 100:g}%) — возможно, тренд сменился; "
                                       f"ждём его следующую сделку"})
                continue
            if f"{addr}|{coin}|{p['side']}" in (d.get("owner_closed") or {}):
                continue                                     # the owner closed our copy of this very position by hand
            if any(c["whale"] == addr and c["coin"] == coin and c["side"] == p["side"] and str(c.get("why", "")).startswith("стоп")
                   and now - int(c.get("closed_t") or 0) < JOIN_REDO_MS for c in d["closed"]):
                continue                                     # our copy of this very position was stopped out — no re-entry
        if owner and ov.blocked(owner, coin, p["side"], now):
            ev = {"t": now, "kind": "skip", "coin": coin, "side": p["side"], "whale": addr, "why": ov.blocked(owner, coin, p["side"], now)}
            if kind == "join":
                note_wait(key, ev)
            else:
                d["events"].append(ev)
            continue
        eq = d["cash"] + sum(mark(o) for o in d["open"])
        used = sum(risk_margin(o, eq) for o in d["open"] if o["whale"] == addr)
        ucap, _rec = util_cap(d, now)
        free = ucap * eq - sum(risk_margin(o, eq) for o in d["open"])         # 05.11: up to the adaptive utilisation target
        # 04.11 22:38 (owner): free margin ÷ the copies still expected today, never past the soft per-whale cap
        margin = round(min(free / expected_slots(quality, follow, now), WHALE_CAP * eq - used), 2)
        if margin < MIN_COPY_MARGIN * eq:
            ev = {"t": now, "kind": "skip", "coin": coin, "side": p["side"], "whale": addr,
                  "why": (f"у этого кита уже {used / eq * 100:.0f}% счёта — предел {WHALE_CAP * 100:.0f}% на кита" if used + MIN_COPY_MARGIN * eq > WHALE_CAP * eq
                          else f"в позициях уже {ucap * 100 - free / eq * 100:.0f}% счёта — предел загрузки сейчас {ucap * 100:.0f}% ({_rec['why_ru']})")}
            if kind == "join":
                note_wait(key, ev)
            else:
                d["events"].append(ev)
            continue
        stp, src = stop_for(p["side"], px, p.get("sl"))
        w_lev = _f(p.get("lev")) or _f(((quality or {}).get(addr) or {}).get("lev_med")) or 1.0
        lev = min(max(1.0, w_lev), DESK_LEV_CAP)
        liq_lev = max(1.0, float(int(LIQ_ROOM / (abs(px - stp) / px) + 1e-9))) if px and stp else lev
        lev_note = None
        if lev > liq_lev:
            lev_note = f"плечо кита {lev:g}× → {liq_lev:g}×: при {lev:g}× ликвидация наступила бы раньше нашего стопа"
            lev = liq_lev
        # 05.11 14:12: the notional is what COPY_RISK of the equity allows from the stop distance (the real account's sizing)
        margin = round(min(margin, COPY_RISK * eq / (abs(px - stp) / px) / lev), 2) if px and stp and px != stp else margin
        o = {"whale": addr, "coin": coin, "side": p["side"], "entry": px, "opened_t": now, "margin": margin, "lev": lev,
             "notional": round(margin * lev, 2),
             "whale_entry": (d.get("first_entry") or {}).get(f"{addr}|{coin}|{p['side']}") or p.get("entry"), "whale_notional": p.get("notional"),
             "whale_lev": p.get("lev"), "lev_src": "кит" if _f(p.get("lev")) else "медиана кита" if w_lev != 1.0 else "нет данных",
             "lev_note": lev_note, "stop": round(stp, 8), "stop_src": src, "join": kind == "join", "gap_pct": gap_pct}
        d["open"].append(o)
        d["events"].append({"t": now, "kind": "join" if kind == "join" else "open", "coin": coin, "side": p["side"], "whale": addr,
                            "px": px, "whale_entry": p.get("entry"), "gap_pct": gap_pct, "lev": lev, "lev_note": lev_note})
    d["join_wait"] = wait_now
    for o in d["open"]:                                        # copies opened before 04.11 get their protective stop now
        if not o.get("stop"):
            stp, src = stop_for(o["side"], o["entry"])
            o["stop"], o["stop_src"] = round(stp, 8), src
    eq = d["cash"] + sum(mark(o) for o in d["open"])
    for o in d["open"]:
        o["upnl"] = round(mark(o), 2)
        o["price"] = _f(mids.get(o["coin"])) or None
    d["equity"] = round(eq, 2)
    # 04.11 22:38: how much of the whales budget works — margin in positions ÷ equity
    d["util_pct"] = round(sum(risk_margin(o, eq) for o in d["open"]) / eq * 100, 1) if eq > 0 else 0.0
    uc, ur = util_cap(d, now)
    d["util_cap_pct"], d["util_rule"] = round(uc * 100), ur
    d["history"].append([now, round(eq, 2)])
    if len(d["history"]) > 2000:
        d["history"] = d["history"][::2]
    d["closed"] = d["closed"][-300:]
    d["events"] = d["events"][-200:]
    d["by_whale_day"] = dict(sorted(d["by_whale_day"].items())[-60:])
    return d


def okx_symbol(coin: str) -> str:
    """the desk's coin (a Hyperliquid name) → the ticker the OKX sleeve maps (okx_alloc.okx_base): kPEPE → 1000PEPE"""
    c = str(coin or "")
    return "1000" + c[1:] if len(c) > 2 and c[0] == "k" and c[1:].isupper() else c.upper()


def paper_trade(o: dict, closed: bool = False) -> dict:
    """one copy in the shape of a web/paper.json desk trade — what the real OKX sleeve follows (okx_futures.want_map / size):
    id, symbol, side, entry, stop, qty, risk_usd, opened; «copy_lev» = the whale's leverage capped at 5× (owner: «повторяй их плечи»)"""
    e = _f(o.get("entry"))
    stp = _f(o.get("stop")) or stop_for(o["side"], e)[0]
    qty = _f(o.get("notional")) / e if e else 0.0
    t = {"id": f"W-{okx_symbol(o['coin'])}-{o['side'][0]}-{int(_f(o.get('opened_t')))}", "symbol": okx_symbol(o["coin"]), "side": o["side"],
         "grade": "W", "kind": "WHALE_COPY", "entry": e, "stop": round(stp, 8), "stop_now": round(stp, 8), "stop_src": o.get("stop_src"),
         "tps": [], "parts": [], "qty": qty, "qty_left": qty, "risk_usd": round(qty * abs(e - stp), 2), "opened": int(_f(o.get("opened_t"))),
         "copy_lev": min(DESK_LEV_CAP, max(1.0, _f(o.get("lev"), 1.0))), "whale": o.get("whale"), "notional": o.get("notional"),
         "whale_entry": o.get("whale_entry"), "join": bool(o.get("join")), "gap_pct": o.get("gap_pct")}
    if str(o.get("whale") or "").startswith("ivan:"):              # 06.11: Ivan's copies — the panel attributes them by «sig» ivan:…
        t["sig"] = f"ivan:{t['id']}"
    if closed:
        t.update(closed=int(_f(o.get("closed_t"))), exit=o.get("exit"), net_usd=o.get("pnl"), exit_kind=o.get("why"))
    return t


def by_whale(d: dict, names: dict) -> list[dict]:
    """per whale: all closes on record (desk['closed'], last 300) and the last 14 UTC days of the daily record"""
    lab = lambda a: names.get(a) or (a[:6] + "…" + a[-4:])          # noqa: E731
    out = {}
    for c in d.get("closed") or []:
        w = out.setdefault(c["whale"], {"whale": c["whale"], "whale_ru": lab(c["whale"]), "trades": 0, "wins": 0, "pnl_usd": 0.0, "open": 0})
        w["trades"] += 1; w["wins"] += 1 if c["pnl"] > 0 else 0; w["pnl_usd"] = round(w["pnl_usd"] + c["pnl"], 2)
    for o in d.get("open") or []:
        out.setdefault(o["whale"], {"whale": o["whale"], "whale_ru": lab(o["whale"]), "trades": 0, "wins": 0, "pnl_usd": 0.0, "open": 0})["open"] += 1
    days = sorted((d.get("by_whale_day") or {}).items())[-14:]
    for w in out.values():
        w["days"] = [{"day": k, **v[w["whale"]]} for k, v in days if w["whale"] in v]
    return sorted(out.values(), key=lambda w: -w["pnl_usd"])


def desk_public(d: dict, names: dict) -> dict:
    cl = d.get("closed") or []
    wins = sum(1 for c in cl if c["pnl"] > 0)
    peak, dd = d["start"], 0.0
    for _, v in d.get("history") or []:
        peak = max(peak, v); dd = max(dd, (peak - v) / peak * 100 if peak else 0)
    lab = lambda a: names.get(a) or (a[:6] + "…" + a[-4:])
    return {"name_ru": "Киты · копирование топ-5", "start": d["start"], "started": d.get("started"), "equity": d.get("equity", d["start"]),
            "util_pct": d.get("util_pct"), "util_cap_pct": d.get("util_cap_pct"), "util_rule": d.get("util_rule"), "live": d.get("live"),
            "pnl_usd": round(d.get("equity", d["start"]) - d["start"], 2), "pnl_pct": round((d.get("equity", d["start"]) / d["start"] - 1) * 100, 2),
            "trades": len(cl), "wins": wins, "winrate": round(wins / len(cl), 3) if cl else None, "max_dd_pct": round(dd, 2),
            "open": [{**o, "whale_ru": lab(o["whale"])} for o in d.get("open") or []],
            "closed": [{**c, "whale_ru": lab(c["whale"])} for c in sorted(cl, key=lambda c: -c["closed_t"])[:20]],
            "history": d.get("history", [])[-500:],
            "by_whale": by_whale(d, names),
            # 04.11 17:37: every join of an already open whale position (and every one still waiting) with its gap to the whale's entry
            "joins": [{**e, "whale_ru": lab(e["whale"])} for e in (d.get("events") or []) if e.get("kind") in ("join", "join_wait")][-20:],
            # 04.11: the same copies in the shape of a web/paper.json desk — the real OKX sleeve follows this at the desk's stage share
            "paper": {"name": "Киты · копирование топ-5", "start_equity": d["start"], "equity": d.get("equity", d["start"]),
                      "since": d.get("started"), "open": [paper_trade(o) for o in d.get("open") or []],
                      "closed": [paper_trade(c, True) for c in sorted(cl, key=lambda c: -c["closed_t"])[:20]]},
            "rules_ru": f"Старт $10 000. Повторяем новые позиции топ-5 китов, замеченные между двумя проверками (сервер смотрит раз в ~30 мин), "
                        f"и входим в их уже открытые позиции, если цена не хуже входа кита больше чем на {JOIN_MAX * 100:g}% и кит не в минусе "
                        f"больше чем на {JOIN_BETTER * 100:g}% по цене (иначе ждём его следующую сделку); первыми — киты с ≥ {GOOD_WIN * 100:.0f}% прибыльных решений и PF ≥ {GOOD_PF:g} за 60 дней (с открытыми позициями): "
                        f"вход по текущей цене, маржа {DESK_MARGIN * 100:.0f}% счёта, плечо как у кита (скрыто — его обычное), не больше {DESK_LEV_CAP:.0f}× и так, чтобы ликвидация была дальше стопа, не больше "
                        f"{DESK_MAX_OPEN} позиций и одна на монету, на одного кита — не больше {WHALE_CAP * 100:.0f}% счёта. Стоп у каждой копии: стоп кита, "
                        f"если он стоит на бирже, иначе −{DESK_STOP * 100:g}% от входа. Выходим, когда кит закрыл или развернул позицию, или по стопу. "
                        f"Комиссии {COST_PCT}% за сделку."}


# ------------------------------------------------------------------------------------------------------------ own history
ARCHIVE_KEEP_IDS_MS = 10 * DAY
ARCHIVE_V = 2                          # 04.11: v1's first file never reached git (data/* ignored) — a new version writes all again


def archive_trades(conn, now: int, cands: list[dict], out_dir: str) -> dict | None:
    """04.11 (research: OKX shows only 3 months — to test the selection on 6+ months we keep our own copy): once per UTC day append
    to <out_dir>/<YYYY-MM-DD>.jsonl every closed whale trade not written before (first day: all we have) and a snapshot of each
    candidate's open positions. One JSON object per line: kind «closed» | «open». Readers dedupe closed trades by (src, trader, id)."""
    day = day_key(now)
    if kv_get(conn, "archive_day") != day and os.path.exists(os.path.join(out_dir, f"{day}.jsonl")) and kv_get(conn, "archive_v") == ARCHIVE_V:
        # 04.11: today's file is already in the repository (written by a newer state than this database) — mark the day done only
        kv_set(conn, "archive_day", day)
        kv_set(conn, "archive_t", now)
        conn.commit()
        return None
    if kv_get(conn, "archive_v") != ARCHIVE_V:
        for k in ("archive_day", "archive_t", "archive_ids"):
            conn.execute("DELETE FROM ws_kv WHERE k=?", (k,))
        kv_set(conn, "archive_v", ARCHIVE_V)
    if kv_get(conn, "archive_day") == day:
        return None
    last_t = int(kv_get(conn, "archive_t") or 0)
    done = {k: v for k, v in (kv_get(conn, "archive_ids") or {}).items() if v >= now - ARCHIVE_KEEP_IDS_MS}
    cutoff = last_t - 3 * DAY if last_t else 0            # late-arriving history (refreshed once a day) is caught on the next dump
    names = {c["addr"]: c.get("name") or c.get("short") or "" for c in cands}
    lines, n_closed, n_open = [], 0, 0
    for src, trader, tid, coin, side, ot, ct, e, ex, pnl, notional, lev in conn.execute(
            "SELECT src, trader, id, coin, side, open_t, close_t, entry, exit, pnl, notional, lever FROM ws_trips WHERE close_t>=? ORDER BY close_t",
            (cutoff,)):
        k = f"{src}|{trader}|{tid}"
        if k in done:
            continue
        done[k] = ct
        lines.append({"kind": "closed", "src": src, "trader": trader, "name": names.get(trader, ""), "id": tid, "coin": coin, "side": side,
                      "open_t": ot, "close_t": ct, "entry": e, "exit": ex, "pnl": round(_f(pnl), 2), "notional": round(_f(notional), 2), "lev": lev})
        n_closed += 1
    for c in cands:
        a = c["addr"]
        r = conn.execute("SELECT state FROM ws_whale WHERE addr=?", (a,)).fetchone()
        ek = WS.src_of(a)
        if not ek and not a.startswith("okx:"):
            for t in round_trips(load_fills(conn, a, cutoff - FETCH_WINDOW if cutoff else 0))[0]:
                k = f"hl|{a}|{t['coin']}-{t['open_t']}"
                if t["close_t"] < cutoff or k in done:
                    continue
                done[k] = t["close_t"]
                lines.append({"kind": "closed", "src": "hl", "trader": a, "name": names.get(a, ""), "id": f"{t['coin']}-{t['open_t']}", "coin": t["coin"],
                              "side": t["side"], "open_t": t["open_t"], "close_t": t["close_t"], "entry": t["entry"], "exit": t["exit"],
                              "pnl": t["pnl"], "notional": t["notional"], "lev": None})
                n_closed += 1
        if not r or not r[0]:
            continue
        try:
            stt = json.loads(r[0])
            pos = WS.SOURCES[ek]["positions"](stt) if ek else okx_positions(stt) if a.startswith("okx:") else norm_positions(stt)
        except Exception:  # noqa: BLE001
            continue
        for x in pos:
            lines.append({"kind": "open", "asof": now, "src": ek or ("okx" if a.startswith("okx:") else "hl"), "trader": a, "name": names.get(a, ""),
                          "coin": x.get("coin") or "", "side": x.get("side") or "", "entry": x.get("entry"), "notional": x.get("notional"),
                          "upnl": x.get("upnl"), "lev": x.get("lev"), "hidden": not x.get("coin")})
            n_open += 1
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{day}.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        for x in lines:
            fh.write(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n")
    kv_set(conn, "archive_day", day)
    kv_set(conn, "archive_t", now)
    kv_set(conn, "archive_ids", {k: v for k, v in done.items() if v >= now - ARCHIVE_KEEP_IDS_MS})
    conn.commit()
    return {"path": path, "closed": n_closed, "open": n_open}


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


# 06.11 17:33 (owner, «по ивану да»): fixed owner picks followed outside the selection and the 3-day lock (config/whale_owner_picks.json)
PICKS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config", "whale_owner_picks.json")


def owner_picks(path: str | None = None) -> list[dict]:
    try:
        with open(path or PICKS, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:  # noqa: BLE001
        return []
    return [{**x, "owner_pick": True} for x in (d.get("picks") or []) if isinstance(x, dict) and x.get("addr") and x.get("on", True)]


LIVE_FRESH_MS = 15 * 60_000


def desk_from_public(wtop: dict, now: int) -> dict:
    """the published desk (whales_top.json «desk», desk_public shape) → a desk_step state with the same copies (same ids)"""
    pub = (wtop or {}).get("desk") or {}
    strip = lambda x: {k: v for k, v in x.items() if k != "whale_ru"}           # noqa: E731
    opn = [strip(o) for o in pub.get("open") or [] if isinstance(o, dict) and o.get("whale")]
    start = _f(pub.get("start"), DESK_START)
    eq = _f(pub.get("equity"), start)
    return {"start": start, "cash": round(eq - sum(_f(o.get("upnl")) for o in opn), 2), "open": opn,
            "closed": [strip(c) for c in pub.get("closed") or [] if isinstance(c, dict)], "seen": {}, "seen_t": {},
            "history": [list(x) for x in pub.get("history") or [] if isinstance(x, (list, tuple))][-500:], "started": pub.get("started") or now,
            "events": [], "by_whale_day": {}, "seeded_from": (wtop or {}).get("asof")}


def desk_t(desk: dict | None) -> int:
    """when a desk state was last moved: its last equity point (0 = none)"""
    h = (desk or {}).get("history") or []
    try:
        return int(h[-1][0]) if h else 0
    except Exception:  # noqa: BLE001
        return 0


def load_published(path: str | None) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def never_backwards(desk: dict | None, study: dict, pub: dict, now: int) -> tuple[dict | None, dict, list[str]]:
    """04.11 (a lost Actions cache → a 15-day-old database republished an old desk and follow list that the REAL account follows):
    the database's desk and follow list are used only when they are at least as new as what is already published
    (web/whales_top.json); otherwise the published ones are taken back into the database. → (desk, study, notes)"""
    notes = []
    if pub.get("desk") and desk_t(pub.get("desk")) > desk_t(desk):
        desk = desk_from_public(pub, now)
        notes.append("стол «Киты» в базе старее опубликованного — взят опубликованный (база была откачена)")
    st_pub = int(_f(pub.get("study_t")))
    if pub.get("following") is not None and st_pub > int(_f((study or {}).get("t"))):
        study = {"t": st_pub, "top": list(pub.get("following") or []), "dropped": list(pub.get("follow_dropped") or []),
                 "partial": True, "from_published": True}
        lk = pub.get("follow_lock") or {}
        if lk.get("rule") == LOCK_RULE and lk.get("t"):               # 06.11: the fixed list survives a rolled-back database
            study.update(rule=LOCK_RULE, lock_t=lk["t"], passing=lk.get("passing") or {}, joined=lk.get("joined") or {})
        notes.append("список копируемых китов в базе старее опубликованного — взят опубликованный")
    return desk, study, notes


def live_desk(path: str | None, now: int) -> dict | None:
    """the server's live desk (web/whales_live.json «desk») when it is at most LIVE_FRESH_MS old, else None"""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        d = doc.get("desk")
        if isinstance(d, dict) and isinstance(d.get("open"), list) and now - int(_f(doc.get("asof"))) <= LIVE_FRESH_MS:
            return {**d, "seen": {}, "live": {**(d.get("live") or {}), "source": "server"}}
    except Exception:  # noqa: BLE001
        return None
    return None


def run(conn: sqlite3.Connection, now: int | None = None, post=_post, get_lb=_get_lb, okx_get=_okx_get,
        max_requests: int = 90, budget_s: float = 170, src_get: dict | None = None, archive_dir: str | None = None,
        live_path: str | None = None, pub_path: str | None = None) -> dict:
    now = int(now if now is not None else time.time() * 1000)
    ensure_schema(conn)
    notes: list[str] = []
    budget = Budget(max_requests, budget_s)
    since = now - WINDOW
    sget = {k: (src_get or {}).get(k) or v["get"] for k, v in WS.SOURCES.items()}     # 04.11: added sources (Binance …)
    # 1. candidates from all sources, once a day (kept between runs)
    cand = kv_get(conn, "candidates") or {}
    if not cand or now - cand.get("t", 0) >= REFRESH_MS or "ext" not in cand or cand.get("v") != CAND_V:
        new = {"t": now, "v": CAND_V, "rows": [], "why": {}, "okx_rows": [], "okx_why": {}}
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
        new["ext"] = {}
        for k, v in WS.SOURCES.items():
            try:
                erows, ewhy = v["candidates"](sget[k])
                new["ext"][k] = {"rows": erows, "why": ewhy}
            except Exception as e:  # noqa: BLE001
                notes.append(f"{v['ru'].split(' ·')[0]}: {str(e)[:120]}")
                new["ext"][k] = (cand.get("ext") or {}).get(k) or {"rows": [], "why": {}}
        if new["rows"] or new["okx_rows"] or any(x["rows"] for x in new["ext"].values()):
            cand = new
            kv_set(conn, "candidates", cand)
    rows, orows = cand.get("rows") or [], cand.get("okx_rows") or []
    erows = [r for x in (cand.get("ext") or {}).values() for r in x.get("rows") or [] if WS.src_of(r.get("addr"))]
    rcfg = reinstate_cfg()
    rpend = reinstate_pending(conn, rcfg)
    lo, le = locked_rows(conn, kv_get(conn, "study") or {}, rows + orows + erows, rpend)
    orows, erows = lo + orows, le + erows                     # locked whales first: the budget reaches them
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
    desk, study2, back = never_backwards(desk, study, load_published(pub_path), now)
    if back:
        notes.extend(back)
        kv_set(conn, "desk", desk)
        if study2 is not study:
            kv_set(conn, "study", study2)
        study = study2
    follow = [p["addr"] for p in study.get("top", [])]
    # 04.11 22:35: the server copies the followed whales every ~60 s (cnq/execution/whale_live.py → web/whales_live.json). While that
    # desk is fresh it IS the desk: published here as it is, no second desk_step (one desk for the panel, desk_select and the account)
    live = live_desk(live_path, now)
    if live is not None:
        desk = live
        kv_set(conn, "desk", desk)
    watch = list(dict.fromkeys(follow + [o["whale"] for o in (desk or {}).get("open", [])])) if live is None else []
    states = {}
    if watch and mids:
        okx_codes = {r["addr"]: r["code"] for r in orows}
        for a in watch:
            if not budget.take():
                break
            try:
                if WS.src_of(a):
                    sv = WS.SOURCES[WS.src_of(a)]
                    cur = sv["current"](sget[WS.src_of(a)], {**WS.lb_of(conn, a), "addr": a, "code": a.split(":", 1)[1]})
                    pos = sv["positions"](cur)
                    conn.execute("UPDATE ws_whale SET state=?, state_t=? WHERE addr=?", (json.dumps(cur), now, a))
                elif a.startswith("okx:"):
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
        desk = desk_step(desk, follow, states, mids, now, ov.active(ov.load(), now), kv_get(conn, "quality") or {},
                         close_unfollowed=study.get("rule") == LOCK_RULE)
        kv_set(conn, "desk", desk)
        conn.commit()
    # 4. study data, budgeted and resumable; both sources take turns so neither waits for the other
    if kv_get(conn, "fills_v") != FILLS_V:
        # 04.11 (v3): the window grew to 60 days — Hyperliquid fills and OKX histories are read again once (Binance keeps 90 days already)
        conn.execute("UPDATE ws_whale SET fills_t=NULL WHERE addr NOT LIKE 'bn:%'")
        kv_set(conn, "fills_v", FILLS_V)
    fresh = ofresh = efresh = 0
    stop_hl = stop_okx = False
    stop_ext: set = set()
    for n in range(max(len(rows), len(orows), len(erows))):
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
        if n < len(erows):
            k = WS.src_of(erows[n]["addr"])
            if k not in stop_ext:
                if WS.SOURCES[k]["refresh"](conn, erows[n], now, budget, sget[k], notes):
                    efresh += 1
                else:
                    stop_ext.add(k)
        if stop_hl and stop_okx and (stop_ext >= {WS.src_of(r["addr"]) for r in erows}):
            break
    need = []
    for c in rows:
        for (coin,) in conn.execute("SELECT DISTINCT coin FROM ws_fills WHERE addr=? AND t>=?", (c["addr"], since)):
            if coin not in need:
                need.append(coin)
    for c in orows + erows:
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
    complete = fresh == len(rows) and ofresh == len(orows) and efresh == len(erows) and cdone == len(need)
    # 5. profiles (from cached data, every run)
    profs = []
    for c in rows + orows + erows:
        r = conn.execute("SELECT fills_t, fills_full, portfolio, state FROM ws_whale WHERE addr=?", (c["addr"],)).fetchone()
        if not r or r[0] is None:
            continue
        ek = WS.src_of(c["addr"])
        if ek:
            sv = WS.SOURCES[ek]
            trips, total = okx_trips(conn, c["addr"], since)            # ws_trips rows of any source share one shape
            st_ = json.loads(r[3]) if r[3] else {}
            trips += sv["open_trips"](st_, now)
            for t in trips:
                t["coin"] = hl_name(t["coin"], mids) or t["coin"]
            pos = [{**x, "coin": hl_name(x["coin"], mids) or x["coin"]} if x.get("coin") else x for x in sv["positions"](st_)]
            pfs = {}
        elif c.get("src") == "okx":
            trips, total = okx_trips(conn, c["addr"], since)
            st_ = json.loads(r[3]) if r[3] else {}
            trips += okx_open_trips(st_, now)          # open sub-positions at the mark price (hidden ones: P&L only)
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
            p.update(followers=c.get("followers"), lead_days=c.get("lead_days"), okx_pnl_all=c.get("pnl_all"), okx_win=c.get("win_ratio"),
                     pick=c.get("pick"))
        if ek:                     # an added source's whale: the same 60-day bar as everyone (profile) + positions visible
            lb_ = WS.lb_of(conn, c["addr"])
            n90 = WS.trades_90d(conn, c["addr"], now, lb_)
            p.update(followers=c.get("followers"), site_win=c.get("win_ratio"), site_pnl_30d=c.get("pnl_m"), trades_90d=n90,
                     site_perf=lb_.get("perf"))
            p["excluded_ru"] = p["excluded_ru"] + WS.record_gate(p, n90)
            p["eligible"] = not p["excluded_ru"]
        profs.append(p)
    if profs:                                  # the 60-day record per whale (incl. open), read by the next run's desk (preference order)
        kv_set(conn, "quality", {p["addr"]: {"win": p.get("win"), "pf": p.get("pf"), "pnl_30d": p.get("pnl_30d"), "pnl_60d": p.get("pnl_60d"),
                                             "per_day": p.get("per_day"), "lev_med": p.get("lev_med")} for p in profs})
    # 04.11 (owner + research): rank by PF counting open positions, then by 60-day P&L
    elig = sorted([p for p in profs if p["eligible"]], key=lambda p: (-(p["pf"] if p["pf"] is not None else -99), -p["pnl_60d"]))
    top = elig[:TOP_N]
    for i, p in enumerate(top):
        p["rank"] = i + 1
        p["desc_ru"] = describe(p)
        p["copy_ok"] = copy_ok(p)
        p["hidden"] = hidden(p.get("positions") or [])          # 04.11: positions without instruments cannot be copied
    # 04.11: the desk follows the best 5 whose copy test is positive AND whose open positions are visible (a hidden trader's
    # entries cannot be seen, so «following» him only produced false «кит закрыл» exits on 30.10)
    # 04.11 22:35: + src / record / signals a day for the server's live copy loop (whale_live) and its sizing
    follow_new = [{"addr": p["addr"], "short": p["short"], "name": p["name"], "src": p["src"], "win": p.get("win"), "pf": p.get("pf"),
                   "decisions": p.get("decisions"), "hold_med_h": p.get("hold_med_h"), "per_day": p.get("per_day"), "pnl_60d": p.get("pnl_60d"),
                   "lev_med": p.get("lev_med")}
                  for p in elig if p.get("copy_ok", copy_ok(p)) and not hidden(p.get("positions") or [])]
    # 06.11 (owner): the fixed list — LOCK_N whales, reviewed every LOCK_MS (lock_follow); a partial run keeps the list as it is
    byaddr = {p["addr"]: p for p in profs}
    hard_why: dict = {}
    lock_t, passing, joined = study.get("lock_t"), study.get("passing"), dict(study.get("joined") or {})
    reinstate_out = {}
    if complete and profs:
        ranked0 = list(follow_new)
        follow_new, hard_why, lock_t, passing = lock_follow(study, follow_new, byaddr, desk, now)
        prev = {x["addr"] for x in study.get("top") or []} if study.get("rule") == LOCK_RULE else set()
        joined = {a: (joined.get(a) if a in prev else now) for a in (x["addr"] for x in follow_new)}
        # 08.11: a whale dropped by our bug comes back once, only if he passes the full bar now and a place is free
        for a, v in rpend.items():
            r = next((x for x in ranked0 if x["addr"] == a), None)
            q = byaddr.get(a)
            if r is None:
                reinstate_out[a] = {"ok": False, "why_ru": (q or {}).get("excluded_ru") or (["не изучен в этом прогоне"] if not q else
                                                                                         ["не проходит тест копирования или скрывает позиции"])}
                continue
            if a in {x["addr"] for x in follow_new}:
                reinstate_out[a] = {"ok": True, "why_ru": ["уже в списке"]}
            elif len(follow_new) >= LOCK_N:
                reinstate_out[a] = {"ok": False, "why_ru": [f"в закреплённом списке нет места ({LOCK_N})"]}
                continue
            else:
                follow_new.append(r)
                j0 = int(_f((v or {}).get("joined"))) or int(_f(((study.get("passing") or {}).get(a) or [now])[0])) or now
                joined[a] = j0
                passing = {**(passing or {}), a: [j0, now]}
                reinstate_out[a] = {"ok": True, "why_ru": [rcfg.get("why_ru") or "возвращён: выбыл из-за ошибки системы"], "joined": j0}
            rd = kv_get(conn, "reinstated") or {}
            rd.setdefault(rcfg["id"], {})[a] = now
            kv_set(conn, "reinstated", rd)
    else:
        follow_new = follow_new[:LOCK_N]
    fset = {x["addr"] for x in follow_new}
    bench = [{"addr": p["addr"], "name": p["name"] or p["short"], "src": p["src"], "win": p.get("win"), "pf": p.get("pf"), "decisions": p.get("decisions"),
              "hold_med_h": p.get("hold_med_h"), "pnl_60d": p.get("pnl_60d"),
              "why_ru": ("скрывает открытые позиции" if hidden(p.get("positions") or []) else
                         f"тест копирования с опозданием {COPY_DELAY} мин в минусе" if not p.get("copy_ok", copy_ok(p)) else
                         "ждёт пересмотра списка (раз в 3 дня; новичок — после 3 дней подряд в отборе)")}
             for p in elig if p["addr"] not in fset][:BENCH_N]
    # 04.11: a complete run always sets the list — also when the stricter bar leaves fewer (or no) whales; a partial run only
    # fills an empty list. Who left the list and why is kept for the panel and the report.
    if (complete and profs) or (follow_new and not study.get("top")):        # a run that studied nobody never empties the list
        old = {x["addr"]: x for x in study.get("top") or []}
        byaddr = {p["addr"]: p for p in profs}
        new_set = {x["addr"] for x in follow_new}
        dropped = []
        for a, x in old.items():
            if a in new_set:
                continue
            q = byaddr.get(a)
            why = hard_why.get(a) or (q or {}).get("excluded_ru") or (["не изучен в этом прогоне"] if not q else
                                                   [f"тест копирования с опозданием {COPY_DELAY} мин в минусе"] if not q.get("copy_ok", copy_ok(q)) else
                                                   ["скрывает открытые позиции"] if hidden(q.get("positions") or []) else
                                                   [f"ниже других по PF (место {next((i + 1 for i, e in enumerate(elig) if e['addr'] == a), '—')})"])
            dropped.append({"addr": a, "name": x.get("name") or x.get("short"), "src": (q or {}).get("src"), "why_ru": why, "t": now})
        prev_dropped = [x for x in study.get("dropped") or [] if x["addr"] not in new_set and x["addr"] not in {d_["addr"] for d_ in dropped}]
        kv_set(conn, "study", {"t": now, "top": follow_new, "partial": not complete, "dropped": (dropped + prev_dropped)[:20],
                               "rule": LOCK_RULE if lock_t else "60d-v1", "lock_t": lock_t, "passing": passing or {}, "joined": joined})
    conn.commit()
    arch = None
    if archive_dir and complete:               # 04.11: our own whale-trade history, one dated file a day (data/whale_trades), from a full look
        try:
            arch = archive_trades(conn, now, rows + orows + erows, archive_dir)
        except Exception as e:  # noqa: BLE001
            notes.append(f"архив сделок китов: {str(e)[:100]}")
    names = {p["addr"]: (p["name"] or p["short"]) for p in profs}
    excluded = {}
    for p in profs:
        for w in p["excluded_ru"]:
            k = w.split(" (")[0]
            excluded[k] = excluded.get(k, 0) + 1
    st_now = kv_get(conn, "study") or {}
    out = {"asof": now, "complete": complete, "study_t": st_now.get("t"), "following": st_now.get("top", []) + owner_picks(),
           "follow_dropped": st_now.get("dropped", []), "archive": arch, "bench": bench,
           "reinstate": reinstate_out,          # 08.11: one-time reinstatement of a whale dropped by our bug (config/whale_reinstate.json)
           # 06.11 (owner): the list is fixed between reviews; 09.11: a leaver's open copies run to his exit (only blind ones close)
           "follow_lock": ({"rule": LOCK_RULE, "t": st_now.get("lock_t"), "next_t": int(st_now["lock_t"]) + LOCK_MS, "n": LOCK_N,
                            "passing": st_now.get("passing") or {}, "joined": st_now.get("joined") or {},
                            "ru": f"Список закреплён: {LOCK_N} китов, пересмотр раз в {LOCK_MS // 86_400_000} дня. Между пересмотрами кит "
                                  f"уходит только если скрыл позиции, 60 дней ушли в минус или данных нет больше суток. Новичок входит на пересмотре, "
                                  f"если {PROVEN_MS // 86_400_000} дня подряд проходил отбор. Новые сделки ушедшего кита не копируются, "
                                  f"а открытые копии доживают до его выхода (или нашего стопа)."}
                           if st_now.get("rule") == LOCK_RULE and st_now.get("lock_t") else None),
           "close_unfollowed": st_now.get("rule") == LOCK_RULE,
           # 04.11 21:14: per source — candidates studied, passed the 60-day bar, followed (+ sources checked and not connectable)
           "by_source": {k: {"studied": sum(1 for p in profs if p["src"] == k), "passed": sum(1 for p in elig if p["src"] == k),
                             "following": sum(1 for x in st_now.get("top", []) if (WS.src_of(x["addr"]) or ("okx" if x["addr"].startswith("okx:") else "hl")) == k)}
                         for k in ["hl", "okx"] + list(WS.SOURCES)},
           "sources_off": WS.SOURCES_OFF,
           "follow_rule_ru": f"За {int(WINDOW / DAY)} дней: ≥ {MIN_DECISIONS} решений (части одной сделки в пределах 4 ч — одно решение), "
                             f"≥ {MIN_WIN * 100:.0f}% решений в плюсе и прибыль/убыток ≥ {MIN_PF:g} с учётом открытых позиций по текущей цене "
                             f"(скрытые позиции OKX в минусе — убыточные решения), за 60 дней в плюсе, убыток открытых позиций "
                             f"не больше {OPEN_LOSS_MAX * 100:.0f}% прибыли закрытых. Срок удержания не ограничен: сервер копирует за 1–2 минуты. "
                             f"Порядок — по прибыли/убытку, потом по прибыли за 60 дней; копируем лучших {LOCK_N} (список закреплён на {LOCK_MS // 86_400_000} дня), у кого тест копирования "
                             f"с опозданием {COPY_DELAY} мин в плюсе и позиции видны. Деньги стола делятся по ходу дня: новая копия берёт свободную "
                             f"маржу ÷ число сделок, которых ещё ждём сегодня (не меньше {MIN_SLOTS}), на одного кита — не больше {WHALE_CAP * 100:.0f}%.",
           "funnel": {"hl_leaderboard": cand.get("why", {}).get("rows"), "hl_after_filters": cand.get("why", {}).get("passed"),
                      "hl_dropped": cand.get("why"), "okx_lead_traders": cand.get("okx_why", {}).get("rows"),
                      "okx_after_filters": cand.get("okx_why", {}).get("passed"),
                      "ext": {k: {"listed": (x.get("why") or {}).get("rows"), "after_filters": (x.get("why") or {}).get("passed"), "dropped": x.get("why")}
                              for k, x in (cand.get("ext") or {}).items()},
                      "studied": len(profs), "eligible": len(elig), "top": len(top), "excluded": excluded,
                      "waiting": (len(rows) - fresh) + (len(orows) - ofresh) + (len(erows) - efresh), "candles_waiting": len(need) - cdone},
           "top": top,
           "others": [{**{k: p.get(k) for k in ("src", "addr", "short", "name", "pnl_30d", "trips", "win", "pf", "hold_med_h", "style_ru", "excluded_ru", "score",
                                              "pnl_60d", "decisions", "win_closed", "pf_closed", "open_loss", "open_trips", "per_day")},
                       "copy2": (p.get("copy") or {}).get(str(COPY_DELAY))}
                      for p in sorted(profs, key=lambda p: (-(p.get("pick") == "owner"), -p["pnl_30d"])) if p not in top][:30],
           "desk": desk_public(desk, names) if desk else None,
           "hidden": [{"addr": p["addr"], "name": p["name"] or p["short"], "rank": p.get("rank")} for p in top if p.get("hidden")],
           "hidden_ru": "OKX скрывает открытые позиции этих лид-трейдеров (нет монеты, размера и цены входа — только маржа, плечо и "
                        "результат): повторить их сделки вовремя нельзя, стол «Киты» их не копирует.",
           "not_covered_ru": "Bybit (мастер-трейдеры), Bitget и Gate не подключены: с сервера в США их страницы копитрейдинга закрыты (403/404). "
                             "MEXC показывает только открытые позиции без истории сделок, BingX требует вход — их тоже не берём. "
                             "Binance — подключён: лид-портфели, которые показывают свои позиции. Чемпионаты (Bybit WSOT и другие) "
                             "публикуют только итоговые проценты без сделок — повторить такую торговлю нельзя, поэтому в рейтинг они не входят.",
           "method_ru": [
               "Источники с открытой историей сделок: Hyperliquid (каждая сделка в блокчейне) и лид-трейдеры копитрейдинга OKX (официальные открытые данные биржи).",
               "Hyperliquid: счёт от $300k, за всё время от +$500k, за месяц в плюсе, не маркетмейкер (оборот ≤ 1000× счёта), без «доходности» больше 1000% за месяц "
               "(это пополнения, а не торговля); 30 крупнейших по прибыли за всё время. OKX: лид-трейдеры с историей от 90 дней и прибылью; 20 крупнейших по прибыли.",
               "Binance (с 04.11): открытые лид-портфели копитрейдинга с прибылью за 30/90 дней и ≥ 70% прибыльных сделок; копируем только тех, кто "
               "показывает позиции. Hyperliquid: плюс 20 лучших по прибыли за 30 дней.",
               "По каждому — реальные сделки за 60 дней: каждая позиция от открытия до закрытия, результат, время удержания, монеты, сторона. "
               "Части одной сделки (та же монета и сторона в пределах 4 ч) — одно решение. Открытые позиции считаются по текущей цене: киты часто "
               "закрывают прибыль, а убыточные позиции держат открытыми.",
               "В топ попадает только тот, у кого за 60 дней ≥ 20 решений, ≥ 70% из них в плюсе и прибыль к убытку ≥ 2 — с открытыми позициями, "
               "за 60 дней в плюсе и убыток открытых позиций не больше 20% прибыли закрытых (04.11, проверка вслепую: так отобранные 12 китов "
               "из 12 были в плюсе следующие 45 дней). С 04.11 22:35 срок удержания не ограничиваем — сервер видит сделки китов раз в минуту.",
               "Порядок — по прибыли к убытку с открытыми позициями, потом по прибыли за 60 дней. Копируем того, у кого повтор сделок "
               "с опозданием 2 минуты в плюсе.",
               "Это прошлые 60 дней, а не обещание: киты хеджируются на других биржах, а сервер видит их сделки с опозданием. Сделки китов мы сохраняем "
               "каждый день (data/whale_trades), чтобы через полгода проверить отбор на своей истории."],
           "notes_ru": notes[:20],
           # 04.11: every studied profile for the VIP «Киты» watchlist (cli pops it into web/whale_watch.json, not whales_top.json)
           "profiles": profs}
    return out
