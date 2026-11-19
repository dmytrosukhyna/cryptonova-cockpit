"""«Ручной стол (Дмитрий)» stage 2 (owner 19.11.2026 23:13): the market data of one trading screen — OKX PUBLIC endpoints only (no
key, no account, no order), read by the server and cached 30 s (candles 60 s), so the owner's page asks only our API.

  candles(sym, bar)  1H / 4H / 1D candles + auto levels: today's / yesterday's / this week's high-low, H1 / H4 swing highs and lows
                     (fractals, 2 candles each side), round numbers around the price
  flow(sym)          «Покупатели против продавцов»: order-book depth within ±1 % and ±2 % of the mid ($, bid / ask and the
                     imbalance), taker buy vs sell volume over the last 1 h and 24 h, the long / short account ratio, funding (now and
                     the next time), open interest now and its change over 24 h
  side(sym)          volume 24 h vs the 7-day average, large liquidations of the last 24 h (≥ $10 000), coin facts
                     (web/coin_profiles.json), the coin's and the market's news (web/decision.json news.impacts — the system's own
                     news desk, last 48 h) and the event calendar of the next 48 h (web/calendar.json)
Every block says «нет данных» rather than inventing a number when OKX does not answer."""
from __future__ import annotations

import json
import os
import threading
import time

from cnq.agents import ivan_study as IS

DAY, H = 86_400_000, 3_600_000
TTL = {"candles": 60, "flow": 30, "side": 60}
BIG_LIQ = 10_000.0


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def auto_levels(h1: list, h4: list, d1: list, px: float, now: int) -> list:
    """[{px, kind, label}] — the levels drawn on the chart and used by the TP rule «по уровням» (closed candles only)"""
    out = []
    day0 = now - now % DAY
    today = [c for c in h1 if c[0] >= day0]
    if today:
        out += [{"px": max(c[2] for c in today), "kind": "day", "label": "макс. дня"}, {"px": min(c[3] for c in today), "kind": "day", "label": "мин. дня"}]
    prev = [c for c in d1 if day0 - DAY <= c[0] < day0]
    if prev:
        out += [{"px": prev[-1][2], "kind": "prev", "label": "макс. вчера"}, {"px": prev[-1][3], "kind": "prev", "label": "мин. вчера"}]
    wk0 = day0 - ((now // DAY + 3) % 7) * DAY                    # Monday 00:00 UTC
    week = [c for c in d1 if c[0] >= wk0] + today
    if week:
        out += [{"px": max(c[2] for c in week), "kind": "week", "label": "макс. недели"}, {"px": min(c[3] for c in week), "kind": "week", "label": "мин. недели"}]
    for cs, tf in ((h1, "H1"), (h4, "H4")):
        sw = IS.swings(cs, 2, 4)
        out += [{"px": p, "kind": "swing", "label": f"вершина {tf}"} for _, p in sw["highs"]]
        out += [{"px": p, "kind": "swing", "label": f"дно {tf}"} for _, p in sw["lows"]]
    r = IS.round_levels(px) or {}
    half = r.get("half") or {}
    for k in ("below", "above"):
        if half.get(k):
            out.append({"px": half[k], "kind": "round", "label": "круглый уровень"})
    by: dict = {}                                                  # one line per price; its labels joined («мин. недели · дно H1»)
    for x in sorted(out, key=lambda x: x["px"]):
        key = round(x["px"], 12)
        if x["px"] <= 0:
            continue
        if key in by:
            if x["label"] not in by[key]["label"]:
                by[key]["label"] += " · " + x["label"]
        else:
            by[key] = {**x, "dist_pct": round((x["px"] / px - 1) * 100, 2) if px else None}
    return list(by.values())


def tps_by_levels(side: str, entry: float, stop: float, levels: list, n: int, r_fallback: list, min_r: float = 0.8) -> list:
    """take-profits at the nearest levels beyond the entry in the trade's direction, each at least min_r R from the entry and further
    than the previous one; the rest (when there are not enough levels) at the R-multiples → [(px, why)]"""
    r1 = abs(entry - stop)
    long_ = side == "long"
    cand = sorted({round(x["px"], 12): x for x in levels if (x["px"] > entry if long_ else x["px"] < entry)}.values(),
                  key=lambda x: x["px"], reverse=not long_)
    out, last = [], entry
    for x in cand:
        if len(out) >= n:
            break
        if abs(x["px"] - entry) >= min_r * r1 and (x["px"] > last if long_ else x["px"] < last):
            out.append((x["px"], x["label"]))
            last = x["px"]
    for r in r_fallback:
        if len(out) >= n:
            break
        p = entry + r * r1 if long_ else entry - r * r1
        if (p > last if long_ else p < last):
            out.append((p, f"{r:g}R (уровня нет)"))
            last = p
    return out


class Market:
    def __init__(self, desk, web_dir: str | None):
        self.desk, self.web = desk, web_dir
        self.cache: dict = {}
        self.lock = threading.Lock()

    def _cached(self, key: tuple, ttl: int, fn):
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        val = fn()
        with self.lock:
            self.cache[key] = (time.time(), val)
        return val

    def _web(self, name: str):
        try:
            with open(os.path.join(self.web or "", name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _sym(self, form: dict):
        code, r = self.desk.resolve(str(form.get("sym") or ""))
        return (r, None) if code == 200 else (None, (code, r))

    # ------------------------------------------------------------------ chart
    def candles(self, form: dict) -> tuple[int, dict]:
        r, err = self._sym(form)
        if err:
            return err
        bar = {"1h": "1H", "4h": "4H", "1d": "1D"}.get(str(form.get("bar") or "1h").lower(), "1H")
        from .manual_desk import candles as okx_candles

        def load():
            g = self.desk.get
            h1, h4, d1 = okx_candles(g, r["inst"], "1H", 300), okx_candles(g, r["inst"], "4H", 300), okx_candles(g, r["inst"], "1D", 200)
            last = self.desk._last(r["inst"]) or (h1[-1][4] if h1 else None)
            now = self.desk.now()
            shown = {"1H": h1, "4H": h4, "1D": d1}[bar]
            return {"sym": r["sym"], "inst": r["inst"], "bar": bar, "last": last, "candles": shown[-200:],
                    "levels": auto_levels(h1, h4, d1, last, now) if last else [], "own": self.desk.own_levels(r["sym"]), "asof": now}
        try:
            d = self._cached(("candles", r["inst"], bar), TTL["candles"], load)
        except Exception:  # noqa: BLE001
            return 502, {"error": "okx", "message_ru": "OKX не отдал свечи — попробуйте ещё раз."}
        return 200, {**d, "own": self.desk.own_levels(r["sym"])}

    # ------------------------------------------------------------------ buyers vs sellers
    def flow(self, form: dict) -> tuple[int, dict]:
        r, err = self._sym(form)
        if err:
            return err
        return 200, self._cached(("flow", r["inst"]), TTL["flow"], lambda: self._flow(r))

    def _try(self, fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None

    def _flow(self, r: dict) -> dict:
        g, inst = self.desk.get, r["inst"]
        ct = self.desk.ct_val(inst) or 1.0
        out = {"sym": r["sym"], "asof": self.desk.now()}
        book = self._try(lambda: g("/api/v5/market/books", instId=inst, sz="400"))
        if book:
            b = book[0]
            bids = [(float(x[0]), float(x[1]) * ct) for x in b.get("bids") or []]
            asks = [(float(x[0]), float(x[1]) * ct) for x in b.get("asks") or []]
            if bids and asks:
                mid = (bids[0][0] + asks[0][0]) / 2
                dep = {}
                for w in (1, 2):
                    bu = sum(p * q for p, q in bids if p >= mid * (1 - w / 100))
                    au = sum(p * q for p, q in asks if p <= mid * (1 + w / 100))
                    dep[f"{w}pct"] = {"bid_usd": round(bu), "ask_usd": round(au), "imbalance": round((bu - au) / (bu + au) * 100, 1) if bu + au else None,
                                      "complete": (bids[-1][0] < mid * (1 - w / 100)) and (asks[-1][0] > mid * (1 + w / 100))}
                out["depth"] = {"mid": mid, **dep}
        tv = self._try(lambda: g("/api/v5/rubik/stat/taker-volume-contract", instId=inst, period="1H", unit="2", limit="24"))
        if tv:
            rows = sorted(([int(x[0]), _f(x[1], 0.0), _f(x[2], 0.0)] for x in tv), key=lambda x: x[0])     # ts, sell, buy (USDT)
            last = rows[-1]
            s24, b24 = sum(x[1] for x in rows), sum(x[2] for x in rows)
            out["taker"] = {"h1": {"buy_usd": round(last[2]), "sell_usd": round(last[1])}, "h24": {"buy_usd": round(b24), "sell_usd": round(s24)},
                            "h24_hours": len(rows)}
        ls = self._try(lambda: g("/api/v5/rubik/stat/contracts/long-short-account-ratio-contract", instId=inst, period="1H", limit="2"))
        if ls:
            row = max(ls, key=lambda x: int(x[0]))
            out["long_short"] = {"ratio": _f(row[1]), "t": int(row[0])}
        fr = self._try(lambda: g("/api/v5/public/funding-rate", instId=inst))
        if fr:
            out["funding"] = {"rate_pct": round(_f(fr[0].get("fundingRate"), 0) * 100, 4), "next_t": int(_f(fr[0].get("nextFundingTime") or fr[0].get("fundingTime"), 0))}
        oi = self._try(lambda: g("/api/v5/rubik/stat/contracts/open-interest-history", instId=inst, period="1H", limit="25"))
        if oi:
            rows = sorted(([int(x[0]), _f(x[3])] for x in oi if len(x) > 3), key=lambda x: x[0])          # ts, oiUsd
            if rows:
                now_, first = rows[-1][1], rows[0][1]
                out["oi"] = {"usd": now_, "chg_24h_pct": round((now_ / first - 1) * 100, 2) if first else None, "hours": len(rows) - 1}
        return out

    # ------------------------------------------------------------------ right column
    def side(self, form: dict) -> tuple[int, dict]:
        r, err = self._sym(form)
        if err:
            return err
        return 200, self._cached(("side", r["inst"]), TTL["side"], lambda: self._side(r))

    def _side(self, r: dict) -> dict:
        g, inst, now = self.desk.get, r["inst"], self.desk.now()
        from .manual_desk import candles as okx_candles
        out = {"sym": r["sym"], "asof": now}
        d1 = self._try(lambda: g("/api/v5/market/candles", instId=inst, bar="1D", limit="9"))
        if d1:
            rows = sorted(([int(x[0]), _f(x[7], 0.0), str(x[8])] for x in d1 if len(x) > 8), key=lambda x: x[0])   # volCcyQuote (USDT)
            closed = [x[1] for x in rows if x[2] == "1"][-7:]
            tk = self._try(lambda: g("/api/v5/market/ticker", instId=inst))
            v24 = None
            if tk:
                t0 = tk[0]
                v24 = _f(t0.get("volCcy24h"), 0) * _f(t0.get("last"), 0)          # SWAP: volCcy24h in the coin
            avg = sum(closed) / len(closed) if closed else None
            out["volume"] = {"usd_24h": round(v24) if v24 else None, "avg_7d_usd": round(avg) if avg else None,
                             "vs_avg_pct": round((v24 / avg - 1) * 100, 1) if v24 and avg else None, "days": len(closed)}
        ct = self.desk.ct_val(inst) or 1.0
        lq = self._try(lambda: g("/api/v5/public/liquidation-orders", instType="SWAP", uly=inst.replace("-SWAP", ""), state="filled", limit="100"))
        if lq is not None:
            big = []
            for blk in lq:
                for x in blk.get("details") or []:
                    t, px, sz = int(_f(x.get("ts"), 0)), _f(x.get("bkPx"), 0), _f(x.get("sz"), 0)
                    usd = px * sz * ct
                    if now - t <= DAY and usd >= BIG_LIQ:
                        big.append({"t": t, "px": px, "usd": round(usd), "who": "лонг" if x.get("posSide") == "long" or x.get("side") == "sell" else "шорт"})
            out["liquidations"] = sorted(big, key=lambda x: -x["t"])[:10]
        prof = ((self._web("coin_profiles.json") or {}).get("coins") or {}).get(r["sym"])
        if isinstance(prof, dict):
            out["facts"] = {k: prof.get(k) for k in ("name", "category", "about_ru", "desc_ru", "rank", "mcap_usd", "launched", "chain") if prof.get(k) is not None}
        im = ((self._web("decision.json") or {}).get("news") or {}).get("impacts") or []
        fresh = [x for x in im if isinstance(x, dict) and now - int(_f(x.get("created"), 0)) <= 2 * DAY]
        mine = [x for x in fresh if str(x.get("asset") or "").upper() == r["sym"]]
        mkt = [x for x in fresh if str(x.get("asset") or "").upper() in ("MARKET", "BTC") and x not in mine]
        pick = lambda xs: [{k: x.get(k) for k in ("created", "asset", "direction", "magnitude", "title", "reasoning", "link", "source")}   # noqa: E731
                           for x in sorted(xs, key=lambda x: (-(_f(x.get("magnitude"), 0)), -int(_f(x.get("created"), 0))))[:4]]
        out["news"] = {"coin": pick(mine), "market": pick(mkt)}
        cal = (self._web("calendar.json") or {}).get("events") or []
        out["calendar"] = [{k: e.get(k) for k in ("time", "name", "importance", "what")} for e in cal
                           if isinstance(e, dict) and 0 <= int(_f(e.get("time"), 0)) - now <= 2 * DAY][:8]
        return out
