"""«Золото» — a demo test desk on gold (owner 30.10.2026: «по золоту надо, чтобы тест работал, находил идеальную систему; пока в
CRYPTO NOVA VIP; если будет давать хорошие результаты — потом в приложение»).

One $10 000 demo account that trades gold by ONE rule — the one picked by the backtest of scripts/gold_desk_study.py
(docs/research/gold-desk-2026-11-04.md). The SAME Sim class below runs the backtest and the live desk, so the desk does exactly
what the history measured.

Rules — 6, one parameter set each, fixed before the backtest (the task of 04.11 + docs/research/gold.md):
  ema_lo        day close above EMA 50 and EMA 50 above EMA 200 → long; otherwise flat                      (trend, long only)
  ema_ls        the same long; day close below EMA 50 and EMA 50 below EMA 200 → short                      (trend, long / short)
  breakout      day close above the 20-day high → long, below the 20-day low → short; out on a close beyond the opposite
                10-day extreme; stop 2 daily ATR                                                             (N-day range breakout)
  pullback      daily uptrend (close > EMA 200, EMA 50 > EMA 200): a 4-hour bar dips to the 4-hour EMA 50 and closes above it →
                long; stop 1,5 ATR (4 h), target 3 R, out after 30 bars (5 days)                             (pullback in an uptrend)
  sma100        day close above SMA 100 → long, else flat — the best rule of docs/research/gold.md            (gold-specific 1)
  weekend_fade  PAXG / XAUT trade at the weekend while the gold market is shut; at the Sunday 20:00 UTC close, a move of ≥ 0,7 %
                from the Friday 20:00 close is faded before COMEX reopens; target = the Friday close, stop 1,5 %, out after
                24 hours                                                                                       (gold-specific 2)
Every position: notional = 1× equity (no leverage), a stop (the trend rules: a disaster stop 8 %). Signals on the last CLOSED
bar, fills at the next bar's open. Costs 0,05 % a side (0,1 % a round trip). Funding is not charged: longs are spot XAUT, a
short would be the perp (gold perp shorts RECEIVE funding — docs/research/tradfi_perps.md), so the demo is conservative.
Live candles: OKX public API, XAUT-USDT spot first, then PAXG-USDT, then XAU-USDT-SWAP (`inst_used` says which).
Output web/gold_desk.json — the shape desk_select.stage_source reads (since, closed[{closed, net_usd}], stats{trades,
max_dd_pct}, equity_history[{time, equity}], equity_now / equity, start_equity, name, eligible_real_why) with «eligible_real»:
false — real money on gold needs the owner's separate OK. The previous file is the state. Stdlib only. No orders anywhere."""
from __future__ import annotations

import bisect
import json
import os
import time
import urllib.request

H4 = 4 * 3_600_000
DAY = 86_400_000
START = 10_000.0
FEE = 0.0005                                  # a side: 0,1 % a round trip (task of 04.11)
DISASTER_STOP = 0.08
INSTS = ("XAUT-USDT", "PAXG-USDT", "XAU-USDT-SWAP")
OKX = "https://www.okx.com"
FEED = "gold_desk.json"
LOG = os.path.join("data", "gold_desk.jsonl")
NAME_RU = "Золото · тест"

RULES = ("ema_lo", "ema_ls", "breakout", "pullback", "sma100", "weekend_fade")
PARAMS = {"ema_lo": {"fast": 50, "slow": 200}, "ema_ls": {"fast": 50, "slow": 200}, "breakout": {"n": 20, "x": 10, "atr": 2.0},
          "pullback": {"ema": 50, "atr": 1.5, "r": 3.0, "bars": 30}, "sma100": {"n": 100},
          "weekend_fade": {"move": 0.007, "stop": 0.015, "bars": 6}}
DAILY = {"ema_lo", "ema_ls", "breakout", "sma100"}
# the live rule — chosen by scripts/gold_desk_study.py (docs/research/gold-desk-2026-11-04.md)
RULE = {"name": "sma100", **PARAMS["sma100"]}
ABOUT_RU = {
    "ema_lo": "дневная цена выше EMA {fast} и EMA {fast} выше EMA {slow} — держим лонг, иначе вне рынка; страховочный стоп −8%",
    "ema_ls": "выше EMA {fast} и EMA {fast} выше EMA {slow} — лонг; ниже EMA {fast} и EMA {fast} ниже EMA {slow} — шорт; стоп 8%",
    "breakout": "дневное закрытие выше максимума {n} дней — лонг, ниже минимума — шорт; выход за противоположным экстремумом {x} дней; стоп {atr} ATR",
    "pullback": "в дневном росте (выше EMA 200) откат 4-часовой свечи к EMA {ema} и закрытие выше — лонг; стоп {atr} ATR, цель {r}R, не дольше 5 дней",
    "sma100": "дневная цена выше средней за {n} дней — держим лонг, ниже — вне рынка; страховочный стоп −8%",
    "weekend_fade": "в воскресенье 20:00 UTC, если золото-токен за выходные ушёл от пятничной цены на 0,7% и больше, — сделка против движения до пятничной цены; стоп 1,5%, не дольше суток",
}


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def ema_series(xs: list, n: int) -> list:
    out, a, e = [], 2 / (n + 1), None
    for x in xs:
        e = x if e is None else e + a * (x - e)
        out.append(e)
    return out


def sma_series(xs: list, n: int) -> list:
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / n if i + 1 >= n else None)
    return out


def atr_series(bars: list, n: int = 14) -> list:
    tr = []
    for i, b in enumerate(bars):
        pc = bars[i - 1]["c"] if i else b["c"]
        tr.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    return ema_series(tr, n)


def daily_from_4h(bars: list) -> list:
    """4-hour bars → UTC daily bars [{t, o, h, l, c, v}]"""
    out = []
    for b in bars:
        d = b["t"] - b["t"] % DAY
        if out and out[-1]["t"] == d:
            x = out[-1]
            x["h"], x["l"], x["c"], x["v"] = max(x["h"], b["h"]), min(x["l"], b["l"]), b["c"], x["v"] + b["v"]
        else:
            out.append({"t": d, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
    return out


class Sim:
    """the rule as a state machine over closed 4h bars; the same object runs the backtest and (with the saved state) the live desk"""

    def __init__(self, rule: dict, equity: float = START, state: dict | None = None):
        self.rule = dict(rule)
        self.name = rule["name"]
        self.p = {**PARAMS.get(self.name, {}), **{k: v for k, v in rule.items() if k != "name"}}
        st = state or {}
        self.equity = _f(st.get("equity"), equity)
        self.open = st.get("open")[0] if isinstance(st.get("open"), list) and st.get("open") else None
        self.closed = list(st.get("closed") or [])
        self.pending = st.get("pending")[0] if isinstance(st.get("pending"), list) and st.get("pending") else None
        self.history = list(st.get("equity_history") or [])
        self.days, self._dt, self.ind = [], [], {}
        self._b4 = None

    # ── indicators ────────────────────────────────────────────────────────────────────────────────────────────────
    def set_daily(self, days: list) -> None:
        """the complete UTC daily bars the rule may read, and their indicators"""
        self.days = days
        self._dt = [d["t"] for d in days]
        c = [d["c"] for d in days]
        p = self.p
        self.ind = {"ema_f": ema_series(c, p.get("fast", 50)), "ema_s": ema_series(c, p.get("slow", 200)),
                    "ema50": ema_series(c, 50), "ema200": ema_series(c, 200), "sma": sma_series(c, p.get("n", 100)),
                    "atr": atr_series(days, 14)}

    def day_idx(self, t_close: int) -> int:
        """index of the last daily bar closed at or before t_close; −1 if none"""
        return bisect.bisect_right(self._dt, t_close - DAY) - 1

    def _bars4(self, bars: list) -> dict:
        key = (id(bars), len(bars))
        if not self._b4 or self._b4[0] != key:
            self._b4 = (key, {"ema": ema_series([b["c"] for b in bars], self.p.get("ema", 50)), "atr": atr_series(bars, 14),
                              "pos": {b["t"]: i for i, b in enumerate(bars)}})
        return self._b4[1]

    def daily_side(self, j: int, holding: int = 0) -> int | None:
        """the daily rules' wanted position after day j closed: +1 / −1 / 0; None = not enough history"""
        n = self.name
        c = self.days[j]["c"]
        if n in ("ema_lo", "ema_ls"):
            if j < self.p["slow"]:
                return None
            f, s = self.ind["ema_f"][j], self.ind["ema_s"][j]
            if c > f and f > s:
                return 1
            if n == "ema_ls" and c < f and f < s:
                return -1
            return 0
        if n == "sma100":
            v = self.ind["sma"][j]
            return None if v is None else (1 if c > v else 0)
        if n == "breakout":
            N, X = self.p["n"], self.p["x"]
            if j < N:
                return None
            prev = self.days[j - N:j]
            hi, lo = max(d["h"] for d in prev), min(d["l"] for d in prev)
            if c > hi:
                return 1
            if c < lo:
                return -1
            if holding:                                         # hold until the close beyond the opposite X-day extreme
                px = self.days[j - X:j]
                if holding == 1 and c < min(d["l"] for d in px):
                    return 0
                if holding == -1 and c > max(d["h"] for d in px):
                    return 0
                return holding
            return 0
        return 0

    # ── one closed bar ────────────────────────────────────────────────────────────────────────────────────────────
    def step(self, bars: list, i: int) -> list:
        """apply bar i (just closed): fill the pending order at its open, manage the open position inside it, then decide the
        next order from its close. Returns the events of this bar."""
        b, ev = bars[i], []
        if self.pending:
            p, self.pending = self.pending, None
            px = b["o"]
            notional = self.equity
            qty = notional / px
            stop = px * (1 - p["stop_pct"]) if p["side"] == "LONG" else px * (1 + p["stop_pct"])
            tgt = p.get("target")
            if p.get("target_r"):
                d = abs(px - stop)
                tgt = px + p["target_r"] * d if p["side"] == "LONG" else px - p["target_r"] * d
            self.open = {"id": f"G-{p['side'][0]}-{b['t']}", "symbol": "XAU", "side": p["side"], "entry": px, "stop": round(stop, 2),
                         "stop_now": round(stop, 2), "tps": [round(tgt, 2)] if tgt else [], "qty": qty, "qty_left": qty,
                         "notional": round(notional, 2), "risk_usd": round(abs(px - stop) * qty, 2), "opened": b["t"],
                         "expires": b["t"] + p["bars"] * H4 if p.get("bars") else None, "fee_usd": round(notional * FEE, 2),
                         "reason": p["why"], "pnl_usd": 0.0}
            self.equity -= self.open["fee_usd"]
            ev.append({"t": b["t"], "kind": "open", "side": p["side"], "px": px, "notional": round(notional, 2)})
        o = self.open
        if o:
            long = o["side"] == "LONG"
            hit_stop = b["l"] <= o["stop_now"] if long else b["h"] >= o["stop_now"]
            tgt = o["tps"][0] if o["tps"] else None
            hit_tgt = tgt is not None and (b["h"] >= tgt if long else b["l"] <= tgt)
            if hit_stop:                                             # inside a bar the stop counts before the target
                gap = (b["o"] < o["stop_now"]) if long else (b["o"] > o["stop_now"])
                self._close(o, b["o"] if gap else o["stop_now"], b, "STOP", ev)
            elif hit_tgt:
                self._close(o, tgt, b, "TP", ev)
            else:
                why = self._rule_exit(bars, i, o)
                if why:
                    self._close(o, b["c"], b, why, ev)
        if not self.open and not self.pending:
            self.pending = self._entry(bars, i)
        if self.open:
            o = self.open
            o["price"] = b["c"]
            o["pnl_usd"] = round((b["c"] - o["entry"]) * o["qty"] * (1 if o["side"] == "LONG" else -1), 2)
        self.history.append({"time": b["t"] + H4, "equity": round(self.equity + (self.open["pnl_usd"] if self.open else 0.0), 2)})
        return ev

    def _close(self, o: dict, px: float, b: dict, why: str, ev: list) -> None:
        long = o["side"] == "LONG"
        gross = (px - o["entry"]) * o["qty"] * (1 if long else -1)
        fee_out = o["qty"] * px * FEE
        self.equity += gross - fee_out
        net = gross - fee_out - o["fee_usd"]
        c = {**o, "closed": b["t"] + H4, "exit": round(px, 2), "exit_kind": why, "pnl_usd": round(gross, 2), "net_usd": round(net, 2),
             "pnl_r": round(gross / o["risk_usd"], 3) if o["risk_usd"] else None,
             "pct": round(net / (o["entry"] * o["qty"]) * 100, 3), "qty_left": 0.0}
        c.pop("price", None)
        self.closed.append(c)
        self.open = None
        ev.append({"t": b["t"] + H4, "kind": "close", "why": why, "px": round(px, 2), "net_usd": c["net_usd"]})

    def _is_day_end(self, b: dict) -> bool:
        return b["t"] % DAY == DAY - H4

    def _rule_exit(self, bars: list, i: int, o: dict) -> str | None:
        b, long = bars[i], o["side"] == "LONG"
        if o.get("expires") and b["t"] + H4 >= o["expires"]:
            return "TIME"
        if self.name in DAILY:
            if not self._is_day_end(b):
                return None
            j = self.day_idx(b["t"] + H4)
            if j < 0:
                return None
            side = self.daily_side(j, 1 if long else -1)
            return None if side is None or side == (1 if long else -1) else "RULE"
        if self.name == "pullback" and self._is_day_end(b):
            j = self.day_idx(b["t"] + H4)
            if j >= 0 and self.days[j]["c"] < self.ind["ema200"][j]:
                return "RULE"
        return None

    def _entry(self, bars: list, i: int, force: bool = False) -> dict | None:
        """the next order from bar i's close; force = decide a daily rule now even off the day end (a fresh live start)"""
        b, n, p = bars[i], self.name, self.p
        why = ABOUT_RU[n].format(**{**p, "move": p.get("move", 0) * 100})
        if n in DAILY:
            if not (force or self._is_day_end(b)):
                return None
            j = self.day_idx(b["t"] + H4)
            side = self.daily_side(j) if j >= 0 else None
            if not side:
                return None
            if n == "breakout":
                stop_pct = min(0.2, max(0.005, p["atr"] * self.ind["atr"][j] / self.days[j]["c"]))
            else:
                stop_pct = DISASTER_STOP
            return {"side": "LONG" if side == 1 else "SHORT", "stop_pct": stop_pct, "why": why}
        if n == "pullback":
            j = self.day_idx(b["t"] + H4)
            if j < 200:
                return None
            d = self.days[j]["c"]
            if not (d > self.ind["ema200"][j] and self.ind["ema50"][j] > self.ind["ema200"][j]):
                return None
            B = self._bars4(bars)
            e, a = B["ema"][i], B["atr"][i]
            if i < 60 or not (b["l"] <= e < b["c"]) or a <= 0:
                return None
            return {"side": "LONG", "stop_pct": p["atr"] * a / b["c"], "target_r": p["r"], "bars": p["bars"], "why": why}
        if n == "weekend_fade":
            if time.gmtime(b["t"] / 1000).tm_wday != 6 or (b["t"] % DAY) != 16 * 3_600_000:     # the bar Sun 16:00–20:00 UTC
                return None
            B = self._bars4(bars)
            k = B["pos"].get(b["t"] - 44 * 3_600_000)                                         # the bar Fri 20:00–24:00 UTC
            if k is None:
                return None
            ref = bars[k]["c"]
            mv = b["c"] / ref - 1
            if abs(mv) < p["move"]:
                return None
            return {"side": "SHORT" if mv > 0 else "LONG", "stop_pct": p["stop"], "target": ref, "bars": p["bars"], "why": why}
        return None

    def stats(self) -> dict:
        cl = self.closed
        n = len(cl)
        wins = [c["net_usd"] for c in cl if c["net_usd"] > 0]
        losses = [-c["net_usd"] for c in cl if c["net_usd"] < 0]
        pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (99.0 if wins else None)
        peak, dd = START, 0.0
        for e in self.history:
            peak = max(peak, e["equity"])
            dd = max(dd, (peak - e["equity"]) / peak * 100 if peak else 0.0)
        pcts = [c["pct"] for c in cl if c.get("pct") is not None]
        return {"trades": n, "winrate": round(len(wins) / n, 3) if n else None, "pf": round(pf, 2) if pf is not None else None,
                "max_dd_pct": round(dd, 2), "avg_trade_pct": round(sum(pcts) / len(pcts), 3) if pcts else None,
                "best": max((c["net_usd"] for c in cl), default=None), "worst": min((c["net_usd"] for c in cl), default=None)}


def backtest(bars: list, rule: dict, equity: float = START, days: list | None = None) -> Sim:
    s = Sim(rule, equity)
    d = days if days is not None else daily_from_4h(bars)
    s.set_daily([x for x in d if x["t"] + DAY <= bars[-1]["t"] + H4])
    for i in range(len(bars)):
        s.step(bars, i)
    return s


# ── the live desk ────────────────────────────────────────────────────────────────────────────────────────────────────
def okx_candles(inst: str, bar: str, limit: int = 300, get=None) -> list:
    """OKX public candles, oldest first, closed bars only; pages back with «history-candles». [{t,o,h,l,c,v}]"""
    get = get or _get
    rows, after = [], None
    for _ in range(8):
        path = "/api/v5/market/candles" if after is None else "/api/v5/market/history-candles"
        q = f"instId={inst}&bar={bar}&limit=100" + (f"&after={after}" if after else "")
        d = get(f"{OKX}{path}?{q}")
        data = (d or {}).get("data") or []
        if not data:
            break
        rows += data
        after = data[-1][0]
        if len(rows) >= limit:
            break
    out = {}
    for r in rows:
        if str(r[8] if len(r) > 8 else "1") != "1":
            continue
        out[int(r[0])] = {"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
                          "v": float(r[7]) if len(r) > 7 and r[7] else float(r[5])}
    return [out[k] for k in sorted(out)][-limit:]


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "cnq-gold-desk/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def public(sim: Sim, inst: str | None, now: int, note: str | None = None, prev: dict | None = None, last_bar: int | None = None) -> dict:
    st = sim.stats()
    o = sim.open
    eq_now = sim.equity + (o["pnl_usd"] if o else 0.0)
    since = int((prev or {}).get("since") or now)
    hist = sim.history[-2000:] or [{"time": since, "equity": START}]
    return {"profile": "gold", "name": NAME_RU, "name_en": "Gold · test", "about": ABOUT_RU[RULE["name"]].format(**sim.p),
            "rule": dict(RULE), "inst_used": inst, "asof": now, "since": since, "start_equity": START, "equity": round(sim.equity, 2),
            "equity_now": round(eq_now, 2), "pnl_usd": round(eq_now - START, 2), "pnl_pct": round((eq_now / START - 1) * 100, 2),
            "open_pnl_usd": round(o["pnl_usd"], 2) if o else 0.0, "open": [o] if o else [], "pending": [sim.pending] if sim.pending else [],
            "closed": sim.closed[-200:], "equity_history": hist, "stats": st,
            "eligible_real": False, "real": False,
            "eligible_real_why": "золото на реальный счёт — только отдельным решением владельца (30.10); демо-стол идёт по ступеням, деньги не получает",
            "costs_ru": "0,05% за сторону (0,1% за круг); лонг — спот XAUT, фандинг в демо не начисляется",
            "backtest_ru": "docs/research/gold-desk-2026-11-04.md",
            "status_ru": (f"в работе · {('лонг' if o['side'] == 'LONG' else 'шорт')} с {time.strftime('%d.%m', time.gmtime(o['opened'] / 1000))}"
                          if o else ("ждёт исполнения на открытии следующей свечи" if sim.pending else "вне рынка · ждёт сигнал правила")),
            "note": note, "last_bar": last_bar if last_bar is not None else (prev or {}).get("last_bar")}


def run(out: str = "web", now: int | None = None, get=None, log_path: str = LOG) -> dict:
    """one engine step: fetch the closed bars, apply every 4h bar newer than the saved state, write web/gold_desk.json.
    A fresh start (no file, or a changed rule) begins flat with $10 000 at the last closed bar — no backfilled trades."""
    now = int(now or time.time() * 1000)
    prev = _load(os.path.join(out, FEED)) or {}
    state = prev if prev.get("rule") == RULE and prev.get("last_bar") else {}
    inst, bars, days, err = None, [], [], None
    order = ([prev["inst_used"]] if prev.get("inst_used") in INSTS else []) + [x for x in INSTS if x != prev.get("inst_used")]
    for cand in order:
        try:
            bars = okx_candles(cand, "4H", 300, get)
            days = okx_candles(cand, "1Dutc", 500, get)
            if len(bars) >= 100 and len(days) >= 210:
                inst = cand
                break
        except Exception as e:  # noqa: BLE001
            err = f"{cand}: {str(e)[:80]}"
    if not inst:
        base = public(Sim(RULE, START, state), None, now, None, state or None) if state else public(Sim(RULE), None, now)
        doc = {**base, "asof": now, "note": f"свечи OKX не прочитались ({err or 'мало истории'}) — стол стоит"}
        if prev.get("since"):
            doc["since"] = prev["since"]
        _write(out, doc)
        return doc
    days = [d for d in days if d["t"] + DAY <= now]
    sim = Sim(RULE, START, state)
    sim.set_daily(days)
    evs = []
    if not state:
        i = len(bars) - 1
        sim.pending = sim._entry(bars, i, force=True)
        last = bars[i]["t"] + H4
        prev = {}
    else:
        last = int(_f(state.get("last_bar"), 0))
        for i, b in enumerate(bars):
            if b["t"] + H4 <= last:
                continue
            evs += sim.step(bars, i)
            last = b["t"] + H4
    doc = public(sim, inst, now, None, prev, last)
    if evs:
        try:
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                for e in evs:
                    f.write(json.dumps({**e, "inst": inst, "rule": RULE["name"]}, ensure_ascii=False) + "\n")
        except OSError:
            pass
    _write(out, doc)
    return doc


def _write(out: str, doc: dict) -> None:
    os.makedirs(out, exist_ok=True)
    tmp = os.path.join(out, FEED + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(out, FEED))
