"""«Золото» — a demo test desk on gold (30.10.2026, owner: «по золоту надо, чтобы тест работал, пока в CRYPTO NOVA VIP»).

One $10 000 demo account that trades gold by ONE rule picked by the honest backtest of scripts/gold_desk_study.py
(docs/research/gold-desk-2026-10-30.md): 2023–2024 to choose the variant, 2025–2026 to check it; the same code below runs the
backtest and the live desk, so the desk does exactly what the history measured. Candles: OKX public API (XAUT-USDT spot first,
then PAXG-USDT, then XAU-USDT-SWAP — the first instrument OKX serves; `inst_used` says which); 4-hour and 1-day bars, the rule
acts on the last CLOSED 4-hour bar, fills at the next bar's open with the engine's costs (spot 0,12 % a side for longs, perp
0,06 % a side for shorts; funding is not charged in the demo — a short on a gold perp receives it, so the demo is conservative).
Every position has a stop; notional ≤ 2× equity; risk 1 % of equity per trade on the 4-hour rules, 1× equity on the trend
rules with a disaster stop. Long and short.

Rules (the four families of the system's desk framework and docs/research/gold.md, fixed before the backtest):
  trend      daily close above SMA n → long (spot), else flat; disaster stop −8 %                               (gold.md: «тренд 100»)
  trend_fund the same long; short the perp when the daily close is below SMA n AND below SMA 20 — the short receives funding
             (docs/research/tradfi_perps.md: longs of XAU perps pay 7–13 %/yr, shorts get paid); stop +8 %
  pullback   4h, in the daily regime (close vs SMA 50 and SMA 50 rising/falling over 10 days): a bar that touches the 4h EMA 20
             and closes back on the trend side on volume ≥ k× the 30-bar median → enter; stop s× ATR 14 (4h), target 2R, out
             after 12 bars                                                                      (levels + volume regime, long & short)
  breakout   4h close beyond the 30-bar extreme in the regime direction on volume ≥ k× median → enter; stop s× ATR; exit on a
             close beyond the 10-bar opposite extreme or after 30 bars                            (trend breakout with volume)
Output web/gold_desk.json — the same shape as a web/paper.json profile (open / closed / equity_history / stats) + «rule»,
«inst_used», «eligible_real»: false (real money on gold needs the owner's separate OK — the desk says so itself). The previous
file is the state. Stdlib only."""
from __future__ import annotations

import bisect
import json
import os
import time
import urllib.request

H4 = 4 * 3_600_000
DAY = 86_400_000
START = 10_000.0
FEE_SPOT, FEE_PERP = 0.0012, 0.0006           # a side (taker + slippage), the engine's numbers (docs/research/gold.md)
RISK = 0.01                                   # of equity per trade on the 4h rules
MAX_NOTIONAL_X = 2.0                          # notional ≤ 2× equity (the owner's leverage limit)
DISASTER_STOP = 0.08                          # the trend rules: a stop 8 % away (every position has a stop)
INSTS = ("XAUT-USDT", "PAXG-USDT", "XAU-USDT-SWAP")
OKX = "https://www.okx.com"
FEED = "gold_desk.json"
LOG = os.path.join("data", "gold_desk.jsonl")
NAME_RU = "Золото · тест"

# the live rule — set from the backtest (docs/research/gold-desk-2026-10-30.md); params are the variant chosen on 2023–2024
RULE = {"name": "trend", "n": 100}
VARIANTS = {
    "trend": [{"n": n} for n in (50, 100, 200)],
    "trend_fund": [{"n": n} for n in (50, 100, 200)],
    "pullback": [{"k": k, "s": s} for k in (1.2, 1.5) for s in (1.0, 1.5)],
    "breakout": [{"k": k, "s": s} for k in (1.2, 1.5) for s in (1.0, 1.5)],
}
ABOUT_RU = {
    "trend": "дневная цена выше средней за {n} дней — держим лонг (спот), ниже — вне рынка; страховочный стоп −8%",
    "trend_fund": "выше средней за {n} дней — лонг (спот); ниже неё и ниже средней за 20 дней — шорт бессрочного (шорт получает фандинг); стоп 8%",
    "pullback": "4 ч: откат к EMA 20 по дневному режиму (цена к средней 50 и её наклон), возврат на объёме ≥ {k}× медианы; стоп {s} ATR, цель 2R, выход через 12 свечей",
    "breakout": "4 ч: закрытие за экстремумом 30 свечей по дневному режиму на объёме ≥ {k}× медианы; стоп {s} ATR, выход за экстремумом 10 свечей или через 30 свечей",
}


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def sma(xs: list, n: int, i: int) -> float | None:
    return sum(xs[i - n + 1:i + 1]) / n if i + 1 >= n else None


def ema_series(xs: list, n: int) -> list:
    out, a, e = [], 2 / (n + 1), None
    for x in xs:
        e = x if e is None else e + a * (x - e)
        out.append(e)
    return out


def atr_series(bars: list, n: int = 14) -> list:
    tr = []
    for i, b in enumerate(bars):
        pc = bars[i - 1]["c"] if i else b["c"]
        tr.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    return ema_series(tr, n)


def daily_from_4h(bars: list) -> list:
    """4-hour bars → UTC daily bars [{t, o, h, l, c, v, last}] (last = index of the day's last 4h bar); only complete days count
    when the 4h bars are complete (the day's last bar closes at 00:00 UTC)"""
    out = []
    for i, b in enumerate(bars):
        d = b["t"] - b["t"] % DAY
        if out and out[-1]["t"] == d:
            x = out[-1]
            x["h"], x["l"], x["c"], x["v"], x["last"] = max(x["h"], b["h"]), min(x["l"], b["l"]), b["c"], x["v"] + b["v"], i
        else:
            out.append({"t": d, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"], "last": i})
    return out


class Sim:
    """the rule as a state machine over closed 4h bars; the same object runs the backtest and (with the saved state) the live desk"""

    def __init__(self, rule: dict, equity: float = START, state: dict | None = None):
        self.rule = rule
        self.name = rule["name"]
        st = state or {}
        self.equity = _f(st.get("equity"), equity)
        self.open = st.get("open")                                 # one position or None
        self.closed = list(st.get("closed") or [])
        self.pending = st.get("pending")                            # a fill at the next bar's open
        self.history = list(st.get("equity_history") or [])
        self.funding_h = {}                                         # ms-hour → rate per hour (backtest only)
        self.days, self._day_t = [], []                             # complete daily bars (set_daily) and their open times

    def set_daily(self, days: list) -> None:
        """the complete UTC daily bars the rule may read (the backtest derives them from the 4h bars, the live desk reads OKX 1Dutc)"""
        self.days = days
        self._day_t = [d["t"] for d in days]

    def days_at(self, t_close: int) -> list:
        """the daily bars closed at or before t_close (a 4h bar's close time)"""
        return self.days[:bisect.bisect_right(self._day_t, t_close - DAY)]

    # ── indicators on the window ──────────────────────────────────────────────────────────────────────────────────
    def regime(self, bars: list, i: int) -> int:
        """+1 up / −1 down / 0 flat by the daily bars closed at or before 4h bar i (close vs SMA 50 and the SMA's 10-day slope)"""
        days = self.days_at(bars[i]["t"] + H4)
        if len(days) < 61:
            return 0
        c = [d["c"] for d in days]
        j = len(c) - 1
        s, s10 = sma(c, 50, j), sma(c, 50, j - 10)
        if c[j] > s and s > s10:
            return 1
        if c[j] < s and s < s10:
            return -1
        return 0

    def trend_side(self, bars: list, i: int) -> int | None:
        """the trend rules: decided only on the bar that completes a day; +1 long, −1 short (trend_fund), 0 flat, None = not a day end"""
        if bars[i]["t"] % DAY != DAY - H4:
            return None
        days = self.days_at(bars[i]["t"] + H4)
        n = self.rule["n"]
        if len(days) < max(n, 20) + 1:
            return 0
        c = [d["c"] for d in days]
        j = len(c) - 1
        if c[j] > sma(c, n, j):
            return 1
        if self.name == "trend_fund" and c[j] < sma(c, 20, j):
            return -1
        return 0

    # ── one closed bar ────────────────────────────────────────────────────────────────────────────────────────────
    def step(self, bars: list, i: int) -> list:
        """apply bar i (just closed): fill the pending order at its open, manage the open position inside it, then decide the
        next order from its close. Returns the events of this bar."""
        b, ev = bars[i], []
        # 1. the fill decided on the previous bar
        if self.pending:
            p, self.pending = self.pending, None
            px = b["o"]
            fee = FEE_SPOT if p["side"] == "LONG" else FEE_PERP
            qty = p["notional"] / px
            self.open = {"id": f"G-{p['side'][0]}-{b['t']}", "symbol": "XAU", "side": p["side"], "entry": px, "stop": p["stop"],
                         "stop_now": p["stop"], "tps": [p.get("target")] if p.get("target") else [], "parts": [1.0],
                         "qty": qty, "qty_left": qty, "risk_usd": round(abs(px - p["stop"]) * qty, 2), "opened": b["t"],
                         "expires": b["t"] + p["max_bars"] * H4 if p.get("max_bars") else None, "bar0": i,
                         "fee_usd": round(p["notional"] * fee, 2), "reason": p["why"], "pnl_usd": 0.0}
            self.equity -= self.open["fee_usd"]
            ev.append({"t": b["t"], "kind": "open", "side": p["side"], "px": px, "notional": round(p["notional"], 2)})
        # 2. the open position inside this bar: stop first, then target, then the rule's exits at the close
        o = self.open
        if o:
            long = o["side"] == "LONG"
            hit_stop = b["l"] <= o["stop_now"] if long else b["h"] >= o["stop_now"]
            tgt = o["tps"][0] if o["tps"] else None
            hit_tgt = tgt is not None and (b["h"] >= tgt if long else b["l"] <= tgt)
            if hit_stop:
                self._close(o, o["stop_now"], b, "STOP", ev)
            elif hit_tgt:
                self._close(o, tgt, b, "TP", ev)
            else:
                why = self._rule_exit(bars, i, o)
                if why:
                    self.pending = None
                    self._close(o, b["c"], b, why, ev)               # fills at the close ≈ next open (the engine's market exit)
                else:
                    hrs = H4 // 3_600_000
                    if not long and self.funding_h:                   # backtest: the short receives the perp's funding
                        rate = sum(self.funding_h.get(b["t"] + k * 3_600_000, 0.0) for k in range(hrs))
                        self.equity += o["qty"] * b["c"] * rate
        # 3. the next order from this bar's close
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
        fee = FEE_SPOT if long else FEE_PERP
        gross = (px - o["entry"]) * o["qty"] * (1 if long else -1)
        net = gross - o["qty"] * px * fee
        self.equity += net
        c = {**o, "closed": b["t"] + H4, "exit": px, "exit_kind": why, "pnl_usd": round(gross, 2),
             "net_usd": round(net - o["fee_usd"], 2), "pnl_r": round(gross / o["risk_usd"], 3) if o["risk_usd"] else None,
             "pct": round((net - o["fee_usd"]) / (o["entry"] * o["qty"]) * 100, 3), "qty_left": 0.0}
        c.pop("price", None)
        self.closed.append(c)
        self.open = None
        ev.append({"t": b["t"] + H4, "kind": "close", "why": why, "px": px, "net_usd": c["net_usd"]})

    def _rule_exit(self, bars: list, i: int, o: dict) -> str | None:
        b, long = bars[i], o["side"] == "LONG"
        if self.name in ("trend", "trend_fund"):
            side = self.trend_side(bars, i)
            if side is None:
                return None
            return None if side == (1 if long else -1) else "TREND"
        if o.get("expires") and b["t"] + H4 >= o["expires"]:
            return "TIME"
        if self.name == "breakout":
            lo = min(x["l"] for x in bars[max(0, i - 10):i]) if i else b["l"]
            hi = max(x["h"] for x in bars[max(0, i - 10):i]) if i else b["h"]
            if (long and b["c"] < lo) or (not long and b["c"] > hi):
                return "TRAIL"
        return None

    def _entry(self, bars: list, i: int) -> dict | None:
        b = bars[i]
        if self.name in ("trend", "trend_fund"):
            side = self.trend_side(bars, i)
            if not side:
                return None
            notional = self.equity * 1.0
            stop = b["c"] * (1 - DISASTER_STOP) if side == 1 else b["c"] * (1 + DISASTER_STOP)
            return {"side": "LONG" if side == 1 else "SHORT", "notional": notional, "stop": stop, "target": None, "max_bars": None,
                    "why": ABOUT_RU[self.name].format(**self.rule)}
        if i < 40:
            return None
        reg = self.regime(bars, i)
        if not reg:
            return None
        win = bars[i - 29:i + 1]
        vols = sorted(x["v"] for x in win)
        med = vols[len(vols) // 2]
        if med <= 0 or b["v"] < self.rule["k"] * med:
            return None
        atr = atr_series(bars[max(0, i - 60):i + 1], 14)[-1]
        if atr <= 0:
            return None
        long = reg == 1
        if self.name == "pullback":
            e = ema_series([x["c"] for x in bars[max(0, i - 120):i + 1]], 20)[-1]
            ok = (b["l"] <= e and b["c"] > e) if long else (b["h"] >= e and b["c"] < e)
            if not ok:
                return None
            stop = b["c"] - self.rule["s"] * atr if long else b["c"] + self.rule["s"] * atr
            target = b["c"] + 2 * (b["c"] - stop) if long else b["c"] - 2 * (stop - b["c"])
            max_bars = 12
        else:
            prev = bars[i - 30:i]
            ok = b["c"] > max(x["h"] for x in prev) if long else b["c"] < min(x["l"] for x in prev)
            if not ok:
                return None
            stop = b["c"] - self.rule["s"] * atr if long else b["c"] + self.rule["s"] * atr
            target, max_bars = None, 30
        dist = abs(b["c"] - stop) / b["c"]
        if dist <= 0.001:
            return None
        notional = min(self.equity * RISK / dist, self.equity * MAX_NOTIONAL_X)
        return {"side": "LONG" if long else "SHORT", "notional": notional, "stop": stop, "target": target, "max_bars": max_bars,
                "why": ABOUT_RU[self.name].format(**self.rule)}

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


def backtest(bars: list, rule: dict, funding_h: dict | None = None, equity: float = START) -> Sim:
    s = Sim(rule, equity)
    s.funding_h = funding_h or {}
    days = daily_from_4h(bars)
    s.set_daily([d for d in days if d["t"] + DAY <= bars[-1]["t"] + H4])
    for i in range(len(bars)):
        s.step(bars, i)
    return s


# ── the live desk ────────────────────────────────────────────────────────────────────────────────────────────────────
def okx_candles(inst: str, bar: str, limit: int = 300, get=None) -> list:
    """OKX public candles, oldest first; «history-candles» pages back when «candles» gives fewer than asked. [{t,o,h,l,c,v}]"""
    get = get or _get
    rows, after = [], None
    for _ in range(6):
        path = "/api/v5/market/candles" if after is None else "/api/v5/market/history-candles"
        q = f"instId={inst}&bar={bar}&limit=100" + (f"&after={after}" if after else "")
        d = get(f"{OKX}{path}?{q}")
        data = (d or {}).get("data") or []
        if not data:
            break
        rows += data
        after = data[-1][0]
        if len(rows) >= limit or len(data) < 100:
            break
    out = {}
    for r in rows:
        if str(r[8] if len(r) > 8 else "1") != "1":          # confirm flag: only closed bars
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


def public(sim: Sim, inst: str | None, now: int, note: str | None = None, prev: dict | None = None) -> dict:
    st = sim.stats()
    o = sim.open
    eq_now = sim.equity + (o["pnl_usd"] if o else 0.0)
    since = int((prev or {}).get("since") or now)
    return {"profile": "gold", "name": NAME_RU, "name_en": "Gold · test", "about": ABOUT_RU[RULE["name"]].format(**RULE),
            "rule": dict(RULE), "inst_used": inst, "asof": now, "since": since, "start_equity": START, "equity": round(sim.equity, 2),
            "equity_now": round(eq_now, 2), "pnl_usd": round(eq_now - START, 2), "pnl_pct": round((eq_now / START - 1) * 100, 2),
            "open_pnl_usd": round(o["pnl_usd"], 2) if o else 0.0, "open": [o] if o else [], "pending": [sim.pending] if sim.pending else [],
            "closed": sim.closed[-100:], "equity_history": sim.history[-2000:], "stats": st,
            "eligible_real": False,
            "eligible_real_why": "золото на реальный счёт — только отдельным решением владельца (30.10); демо-стол идёт по ступеням, деньги не получает",
            "costs_ru": "лонг — спот 0,12% за сторону, шорт — бессрочный 0,06% за сторону; фандинг в демо не начисляется",
            "status_ru": (f"в работе · {('лонг' if o['side'] == 'LONG' else 'шорт')} с {time.strftime('%d.%m', time.gmtime(o['opened'] / 1000))}"
                          if o else "вне рынка · ждёт сигнал правила"),
            "note": note, "last_bar": sim.history[-1]["time"] if sim.history else None}


def run(out: str = "web", now: int | None = None, get=None, log_path: str = LOG) -> dict:
    """one engine step: fetch the closed 4h bars, apply every bar newer than the saved state, write web/gold_desk.json"""
    now = int(now or time.time() * 1000)
    prev = _load(os.path.join(out, FEED)) or {}
    state = prev if prev.get("rule") == RULE else {}                  # a changed rule starts the desk over (its closed trades stay in the log)
    inst, bars, err = None, [], None
    days = []
    for cand in ([prev["inst_used"]] if prev.get("inst_used") in INSTS else []) + [x for x in INSTS if x != prev.get("inst_used")]:
        try:
            bars = okx_candles(cand, "4H", 400, get)
            days = okx_candles(cand, "1Dutc", 300, get)
            if len(bars) >= 100 and len(days) >= 30:
                inst = cand
                break
        except Exception as e:  # noqa: BLE001
            err = f"{cand}: {str(e)[:80]}"
    if not inst:
        doc = {**(prev or public(Sim(RULE), None, now)), "asof": now, "note": f"свечи OKX не прочитались ({err or 'нет инструмента'}) — стол стоит"}
        _write(out, doc)
        return doc
    sim = Sim(RULE, START, state)
    sim.set_daily(days)
    last = int(_f(state.get("last_bar"), 0))                           # the close time of the last applied bar
    evs = []
    for i, b in enumerate(bars):
        if b["t"] + H4 <= last:
            continue
        if i < 40 and not state:
            continue                                                   # warm-up on a fresh start: no trades before the indicators exist
        evs += sim.step(bars, i)
    doc = public(sim, inst, now, None, prev)
    if evs:
        try:
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                for e in evs:
                    f.write(json.dumps({**e, "inst": inst}, ensure_ascii=False) + "\n")
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
