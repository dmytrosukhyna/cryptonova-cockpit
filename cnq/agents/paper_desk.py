"""Paper desk — a $10 000 demo futures account that trades the panel's signals automatically, 24/7 on the server.

Mirrors the panel's signal classes so what you see is what is tracked:
  C  LEVEL_LIMIT        limit at the edge of a tested 4h zone (≤ 0.3 ATR away), stop beyond zone + 0.25 ATR,
                        targets 1R/2R/3R (40/30/30 %), stop → entry after T1, limit expires in 24 h, trade expires in 48 h
  B  LIQ_CASCADE_LONG   market long after a −3.5σ 15m candle with a long lower wick, stop = low − 0.5 ATR, target 1R, 8 h
  R  RETEST             4h breakout on volume (close beyond 30-bar extreme + 0.3 ATR, vol ≥ 1.3× avg20), then a return to the
                        level that closes back beyond it (role reversal): market entry, stop = level ∓ 1 ATR(4h), one target 2R,
                        8 days. History 2020–26: long +0.04R, short +0.07R after costs (random −0.07R). scripts/retest_test.py
Risk 1 % of equity per trade, fees 0.05 % per side + 0.02 % slippage. Long alts only when BTC is above its 200-day mean.
Stop-first rule inside a bar (if both stop and target are inside one candle, the stop counts). Deterministic; no look-ahead:
only candles that closed after the signal are used.

State: data/agents.sqlite (paper_orders, paper_trades, paper_equity). Output: web/paper.json for the panel.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time

START_EQUITY = 10_000.0
RISK = 0.01
FEE = 0.0005
SLIP = 0.0002
HOLD_C_H = 48
HOLD_B_H = 8
HOLD_R_H = 192
ORDER_TTL_H = 24
MAX_OPEN = 6


def agg4h(c15: list[list]) -> list[list]:
    """15m → 4h candles [t,o,h,l,c,v], aligned to 4h boundaries (UTC)."""
    out = []; cur = None
    for k in c15:
        t0 = k[0] - (k[0] % 14_400_000)
        if cur is None or cur[0] != t0:
            if cur: out.append(cur)
            cur = [t0, k[1], k[2], k[3], k[4], k[5]]
        else:
            cur[2] = max(cur[2], k[2]); cur[3] = min(cur[3], k[3]); cur[4] = k[4]; cur[5] += k[5]
    if cur: out.append(cur)
    return out


def retest_scan(b4: list[list], n_break: int = 30, wait: int = 24) -> dict | None:
    """Same rule as the panel's retestScan(): returns the live breakout→retest state on CLOSED 4h bars, or None.
    b4 must contain only closed bars. Result: side, level, atr, i_break, i_retest (None while waiting)."""
    n = len(b4)
    if n < n_break + 22:
        return None
    a_all = _atr_series(b4)
    for i in range(n - 1, max(n_break + 20, n - 1 - wait) - 1, -1):
        a = a_all[i]
        if not a:
            continue
        hi_n = max(x[2] for x in b4[i - n_break:i]); lo_n = min(x[3] for x in b4[i - n_break:i])
        v20 = sum(x[5] for x in b4[i - 20:i]) / 20
        volok = b4[i][5] >= 1.3 * v20
        if b4[i][4] > hi_n + 0.3 * a and volok:
            side, lvl = "LONG", hi_n
        elif b4[i][4] < lo_n - 0.3 * a and volok:
            side, lvl = "SHORT", lo_n
        else:
            continue
        ret = None; failed = False
        for j in range(i + 1, n):
            bj = b4[j]
            if side == "LONG":
                if bj[4] < lvl - 0.5 * a: failed = True; break
                if bj[3] <= lvl + 0.5 * a and bj[4] > lvl: ret = j; break
            else:
                if bj[4] > lvl + 0.5 * a: failed = True; break
                if bj[2] >= lvl - 0.5 * a and bj[4] < lvl: ret = j; break
        if failed:
            return None
        return {"side": side, "level": lvl, "atr": a, "i_break": i, "i_retest": ret, "bars_since_retest": None if ret is None else n - 1 - ret}
    return None


def _atr_series(b: list[list], n: int = 14) -> list[float]:
    out = [0.0] * len(b)
    trs = []
    for i in range(1, len(b)):
        trs.append(max(b[i][2] - b[i][3], abs(b[i][2] - b[i - 1][4]), abs(b[i][3] - b[i - 1][4])))
        if len(trs) >= n:
            out[i] = sum(trs[-n:]) / n
    return out


def _atr(c: list[list], n: int = 14) -> float:
    tr = []
    for i in range(1, len(c)):
        h, l, pc = c[i][2], c[i][3], c[i - 1][4]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    tail = tr[-n:]
    return sum(tail) / len(tail) if tail else 0.0


class PaperDesk:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS paper_orders(id TEXT PRIMARY KEY, symbol TEXT, side TEXT, grade TEXT, kind TEXT, entry REAL, stop REAL,
            tps TEXT, parts TEXT, qty REAL, risk_usd REAL, created INTEGER, expires INTEGER, status TEXT, reason TEXT);
        CREATE TABLE IF NOT EXISTS paper_trades(id TEXT PRIMARY KEY, symbol TEXT, side TEXT, grade TEXT, kind TEXT, entry REAL, stop REAL, stop_now REAL,
            tps TEXT, parts TEXT, qty REAL, qty_left REAL, risk_usd REAL, opened INTEGER, expires INTEGER, closed INTEGER, exit_kind TEXT,
            pnl_usd REAL, pnl_r REAL, tps_hit INTEGER, reason TEXT, last_bar INTEGER);
        CREATE TABLE IF NOT EXISTS paper_equity(time INTEGER PRIMARY KEY, equity REAL);
        CREATE TABLE IF NOT EXISTS paper_meta(k TEXT PRIMARY KEY, v TEXT);""")
        conn.commit()

    # ------------------------------------------------------------------ helpers
    def equity(self) -> float:
        row = self.conn.execute("SELECT equity FROM paper_equity ORDER BY time DESC LIMIT 1").fetchone()
        return row[0] if row else START_EQUITY

    def _open(self) -> list[dict]:
        keys = ("id", "symbol", "side", "grade", "kind", "entry", "stop", "stop_now", "tps", "parts", "qty", "qty_left", "risk_usd", "opened",
                "expires", "closed", "exit_kind", "pnl_usd", "pnl_r", "tps_hit", "reason", "last_bar")
        rows = self.conn.execute("SELECT * FROM paper_trades WHERE closed IS NULL").fetchall()
        return [dict(zip(keys, r)) for r in rows]

    def _pending(self) -> list[dict]:
        keys = ("id", "symbol", "side", "grade", "kind", "entry", "stop", "tps", "parts", "qty", "risk_usd", "created", "expires", "status", "reason")
        return [dict(zip(keys, r)) for r in self.conn.execute("SELECT * FROM paper_orders WHERE status='PENDING'").fetchall()]

    # ------------------------------------------------------------------ signals (same rules as the panel)
    def signals(self, coin: dict, btc_bull: bool, now: int) -> list[dict]:
        c15 = coin["candles"]
        if len(c15) < 200:
            return []
        price = c15[-1][4]
        a = _atr(c15) or price * 0.003
        base = coin["symbol"].replace("-USDT", "").replace("USDT", "")
        out = []
        for L in coin.get("levels", [])[:6]:
            if L.get("status") not in (None, "ACTIVE") or L.get("touches", 0) < 2:
                continue
            sup = L["center"] < price
            dist = abs(L["center"] - price) / a
            if dist > 0.3 + (L["hi"] - L["lo"]) / a:
                continue
            side = "LONG" if sup else "SHORT"
            if side == "LONG" and not btc_bull and base != "BTC":
                continue
            entry = L["hi"] if sup else L["lo"]
            stop = L["lo"] - 0.25 * a if sup else L["hi"] + 0.25 * a
            r = abs(entry - stop)
            if r <= 0 or r / entry > 0.05:
                continue
            sgn = 1 if sup else -1
            out.append({"id": f"C-{base}-{L.get('id', int(L['center']))}-{now // 3_600_000}", "symbol": base, "side": side, "grade": "C", "kind": "LEVEL_LIMIT",
                        "entry": entry, "stop": stop, "tps": [entry + sgn * k * r for k in (1, 2, 3)], "parts": [0.4, 0.3, 0.3],
                        "order": "LIMIT", "hold_h": HOLD_C_H,
                        "reason": f"Цена у зоны {L['lo']:.6g}–{L['hi']:.6g} ({L['touches']}× касаний). Лимитка на краю, стоп за зоной + 0.25 ATR."})
        # R: breakout → retest on closed 4h bars (the last 15m candle may be inside an unfinished 4h bar → drop it)
        b4_all = agg4h(coin.get("candles_long") or c15)
        cutoff = now - (now % 14_400_000)
        b4 = [x for x in b4_all if x[0] < cutoff]
        rt = retest_scan(b4)
        if rt and rt["i_retest"] is not None and rt["bars_since_retest"] == 0:
            side = rt["side"]; sgn = 1 if side == "LONG" else -1
            if not (side == "LONG" and not btc_bull and base != "BTC"):
                entry = price
                stop = rt["level"] - sgn * rt["atr"]
                r = sgn * (entry - stop)
                if r > 0 and r / entry <= 0.08:
                    out.append({"id": f"R-{base}-{b4[rt['i_retest']][0]}", "symbol": base, "side": side, "grade": "R", "kind": "RETEST",
                                "entry": entry, "stop": stop, "tps": [entry + sgn * 2 * r] * 3, "parts": [1.0, 0.0, 0.0],
                                "order": "MARKET", "hold_h": HOLD_R_H,
                                "reason": f"Пробой {'сопротивления' if side == 'LONG' else 'поддержки'} {rt['level']:.6g} на объёме и ретест: свеча 4ч вернулась к уровню и закрылась "
                                          f"{'выше' if side == 'LONG' else 'ниже'}. Вход по рынку, стоп {stop:.6g}, цель 2R, до 8 дней."})
        # cascade long: last closed 15m candle
        rets = [c15[i][4] / c15[i - 1][4] - 1 for i in range(max(1, len(c15) - 96 * 30), len(c15) - 1)]
        sig = math.sqrt(sum(x * x for x in rets) / len(rets)) if rets else 0
        last = c15[-2]
        ret = last[4] / c15[-3][4] - 1
        rng = last[2] - last[3]
        if sig and rng > 0 and ret <= -3.5 * sig and (min(last[1], last[4]) - last[3]) / rng >= 0.5:
            entry = c15[-1][1]
            stop = last[3] - 0.5 * a
            r = entry - stop
            if r > 0:
                out.append({"id": f"B-{base}-{last[0]}", "symbol": base, "side": "LONG", "grade": "B", "kind": "LIQ_CASCADE_LONG",
                            "entry": entry, "stop": stop, "tps": [entry + r, entry + r, entry + r], "parts": [1.0, 0.0, 0.0],
                            "order": "MARKET", "hold_h": HOLD_B_H,
                            "reason": f"Каскад ликвидаций: свеча {ret*100:.1f}% ({ret/sig:.1f}σ) с длинным хвостом снизу — откуп, цель 1R, до 8 ч."})
        return out

    # ------------------------------------------------------------------ main step
    @staticmethod
    def stress(coins: list[dict]) -> float:
        """Share of coins down more than 3 % over the last 24 h (96 × 15m). ≥ 0.7 = broad sell-off → no new level longs."""
        xs = []
        for c in coins:
            cs = c.get("candles") or []
            if len(cs) > 97:
                xs.append(cs[-1][4] / cs[-97][4] - 1)
        return len([x for x in xs if x < -0.03]) / len(xs) if xs else 0.0

    def step(self, coins: list[dict], btc_bull: bool, now: int | None = None) -> dict:
        now = now or int(time.time() * 1000)
        by_sym = {c["symbol"].replace("-USDT", "").replace("USDT", ""): c for c in coins}
        risk_off = self.stress(coins) >= 0.7
        events: list[str] = []
        eq = self.equity()
        # 1. new signals → orders / market fills
        known = {r[0] for r in self.conn.execute("SELECT id FROM paper_orders")} | {r[0] for r in self.conn.execute("SELECT id FROM paper_trades")}
        open_syms = {t["symbol"] for t in self._open()} | {o["symbol"] for o in self._pending()}
        for base, coin in by_sym.items():
            for s in self.signals(coin, btc_bull, now):
                if s["id"] in known or base in open_syms:
                    continue
                if risk_off and s["side"] == "LONG" and s["grade"] == "C":
                    events.append(f"пропуск лонга {base}: рынок падает широким фронтом (режим риска; на истории покупка дна со стопом −0.20R, шорт вдогонку −0.04R — не торгуем)")
                    continue
                if len(self._open()) + len(self._pending()) >= MAX_OPEN:
                    break
                risk_usd = eq * RISK
                r = abs(s["entry"] - s["stop"])
                qty = risk_usd / (r + s["entry"] * (2 * FEE + SLIP))
                if s["order"] == "MARKET":
                    fill = s["entry"] * (1 + SLIP)
                    self.conn.execute("INSERT INTO paper_trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (s["id"], base, s["side"], s["grade"], s["kind"], fill, s["stop"], s["stop"], json.dumps(s["tps"]), json.dumps(s["parts"]),
                                       qty, qty, risk_usd, now, now + s["hold_h"] * 3_600_000, None, None, 0.0, 0.0, 0, s["reason"], coin["candles"][-1][0]))
                    events.append(f"открыт {s['side']} {base} по рынку {fill:.6g} (класс {s['grade']})")
                else:
                    self.conn.execute("INSERT INTO paper_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (s["id"], base, s["side"], s["grade"], s["kind"], s["entry"], s["stop"], json.dumps(s["tps"]), json.dumps(s["parts"]),
                                       qty, risk_usd, now, now + ORDER_TTL_H * 3_600_000, "PENDING", s["reason"]))
                    events.append(f"лимитка {s['side']} {base} @ {s['entry']:.6g} (класс {s['grade']})")
                open_syms.add(base)
        self.conn.commit()
        # 2. pending limits: fill on candles that closed after creation
        for o in self._pending():
            coin = by_sym.get(o["symbol"])
            if not coin:
                continue
            filled = None
            for c in coin["candles"]:
                if c[0] + 900_000 <= o["created"] or c[0] + 900_000 > now:   # candles that closed after the order was placed
                    continue
                if c[0] > o["expires"]:
                    break
                if (o["side"] == "LONG" and c[3] <= o["entry"]) or (o["side"] == "SHORT" and c[2] >= o["entry"]):
                    filled = c
                    break
            if filled:
                hold = {"C": HOLD_C_H, "B": HOLD_B_H, "R": HOLD_R_H}.get(o["grade"], HOLD_C_H)
                # conservative: if the fill candle also reached the stop, count it as stopped in the same candle
                stopped = (o["side"] == "LONG" and filled[3] <= o["stop"]) or (o["side"] == "SHORT" and filled[2] >= o["stop"])
                sgn = 1 if o["side"] == "LONG" else -1
                pnl = (sgn * (o["stop"] * (1 - sgn * SLIP) - o["entry"]) * o["qty"] - o["stop"] * o["qty"] * FEE) if stopped else 0.0
                pnl_r = (pnl - o["entry"] * o["qty"] * FEE) / o["risk_usd"] if stopped else 0.0
                self.conn.execute("INSERT INTO paper_trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (o["id"], o["symbol"], o["side"], o["grade"], o["kind"], o["entry"], o["stop"], o["stop"], o["tps"], o["parts"],
                                   o["qty"], 0.0 if stopped else o["qty"], o["risk_usd"], filled[0], filled[0] + hold * 3_600_000,
                                   filled[0] if stopped else None, "STOP" if stopped else None, pnl, pnl_r, 0, o["reason"], filled[0]))
                self.conn.execute("UPDATE paper_orders SET status='FILLED' WHERE id=?", (o["id"],))
                events.append(f"исполнена лимитка {o['side']} {o['symbol']} @ {o['entry']:.6g}" + (" и выбита стопом в той же свече" if stopped else ""))
            elif now > o["expires"]:
                self.conn.execute("UPDATE paper_orders SET status='EXPIRED' WHERE id=?", (o["id"],))
                events.append(f"лимитка {o['symbol']} истекла без исполнения")
        self.conn.commit()
        # 3. manage open trades bar by bar (stop first)
        realized = 0.0
        for t in self._open():
            coin = by_sym.get(t["symbol"])
            if not coin:
                continue
            tps, parts = json.loads(t["tps"]), json.loads(t["parts"])
            sgn = 1 if t["side"] == "LONG" else -1
            r = abs(t["entry"] - t["stop"])
            qty_left, stop_now, hit, pnl = t["qty_left"], t["stop_now"], t["tps_hit"], t["pnl_usd"]
            closed, exit_kind, last_bar = None, None, t["last_bar"]
            for c in coin["candles"]:
                if c[0] <= t["last_bar"] or c[0] + 900_000 > now:
                    continue
                last_bar = c[0]
                hi, lo = c[2], c[3]
                stop_hit = lo <= stop_now if sgn == 1 else hi >= stop_now
                if stop_hit:
                    px = stop_now * (1 - sgn * SLIP)
                    pnl += sgn * (px - t["entry"]) * qty_left - px * qty_left * FEE
                    qty_left = 0; closed = c[0]; exit_kind = "STOP" if hit == 0 else "BREAKEVEN"
                    break
                while hit < len(tps) and parts[hit] > 0 and ((sgn == 1 and hi >= tps[hit]) or (sgn == -1 and lo <= tps[hit])):
                    part_qty = t["qty"] * parts[hit]
                    px = tps[hit]
                    pnl += sgn * (px - t["entry"]) * part_qty - px * part_qty * FEE
                    qty_left -= part_qty; hit += 1
                    if hit == 1:
                        stop_now = t["entry"]
                if hit >= len(tps) or qty_left <= 1e-12 or sum(parts[hit:]) <= 0:
                    qty_left = 0; closed = c[0]; exit_kind = "TARGET"
                    break
                if c[0] >= t["expires"]:
                    px = c[4] * (1 - sgn * SLIP)
                    pnl += sgn * (px - t["entry"]) * qty_left - px * qty_left * FEE
                    qty_left = 0; closed = c[0]; exit_kind = "TIME"
                    break
            entry_fee = t["entry"] * t["qty"] * FEE
            pnl_r = (pnl - entry_fee) / t["risk_usd"] if t["risk_usd"] else 0
            self.conn.execute("UPDATE paper_trades SET qty_left=?, stop_now=?, tps_hit=?, pnl_usd=?, pnl_r=?, closed=?, exit_kind=?, last_bar=? WHERE id=?",
                              (qty_left, stop_now, hit, pnl, pnl_r, closed, exit_kind, last_bar, t["id"]))
            if closed:
                realized += pnl - entry_fee
                events.append(f"закрыт {t['side']} {t['symbol']}: {exit_kind}, {pnl - entry_fee:+.2f}$ ({pnl_r:+.2f}R)")
        self.conn.commit()
        # 4. equity = start + realized (closed) ; open P&L marked separately
        realized_total = sum((r[0] or 0) - (r[1] or 0) * (r[2] or 0) * FEE for r in
                             self.conn.execute("SELECT pnl_usd, entry, qty FROM paper_trades WHERE closed IS NOT NULL").fetchall())
        equity = round(START_EQUITY + realized_total, 2)
        self.conn.execute("INSERT OR REPLACE INTO paper_equity VALUES(?,?)", (now, equity))
        self.conn.commit()
        return self.snapshot(by_sym, now, events)

    # ------------------------------------------------------------------ report
    def snapshot(self, by_sym: dict, now: int, events: list[str]) -> dict:
        price = {k: v["candles"][-1][4] for k, v in by_sym.items()}
        opens = []
        for t in self._open():
            px = price.get(t["symbol"], t["entry"])
            sgn = 1 if t["side"] == "LONG" else -1
            r = abs(t["entry"] - t["stop"]) or 1
            opens.append({**t, "tps": json.loads(t["tps"]), "parts": json.loads(t["parts"]), "price": px,
                          "open_pnl_usd": round(sgn * (px - t["entry"]) * t["qty_left"] + t["pnl_usd"], 2), "moved_r": round(sgn * (px - t["entry"]) / r, 2)})
        keys = ("id", "symbol", "side", "grade", "kind", "entry", "stop", "stop_now", "tps", "parts", "qty", "qty_left", "risk_usd", "opened",
                "expires", "closed", "exit_kind", "pnl_usd", "pnl_r", "tps_hit", "reason", "last_bar")
        closed = [dict(zip(keys, r)) for r in self.conn.execute("SELECT * FROM paper_trades WHERE closed IS NOT NULL ORDER BY closed DESC LIMIT 100").fetchall()]
        for t in closed:
            t["tps"] = json.loads(t["tps"]); t["parts"] = json.loads(t["parts"]); t["net_usd"] = round(t["pnl_usd"] - t["entry"] * t["qty"] * FEE, 2)
        n = len(closed); wins = [t for t in closed if t["net_usd"] > 0]
        eqh = [{"time": a, "equity": b} for a, b in self.conn.execute("SELECT time,equity FROM paper_equity ORDER BY time").fetchall()]
        peak, dd = START_EQUITY, 0.0
        for e in eqh:
            peak = max(peak, e["equity"]); dd = max(dd, (peak - e["equity"]) / peak)
        by_grade = {}
        for g in ("A", "B", "C"):
            ts = [t for t in closed if t["grade"] == g]
            if ts:
                by_grade[g] = {"n": len(ts), "winrate": round(len([t for t in ts if t["net_usd"] > 0]) / len(ts), 3), "avg_r": round(sum(t["pnl_r"] for t in ts) / len(ts), 3),
                               "usd": round(sum(t["net_usd"] for t in ts), 2)}
        eq = self.equity()
        return {"asof": now, "start_equity": START_EQUITY, "risk_off": self.stress(list(by_sym.values())) >= 0.7, "stress": round(self.stress(list(by_sym.values())), 3), "equity": eq, "pnl_usd": round(eq - START_EQUITY, 2), "pnl_pct": round((eq / START_EQUITY - 1) * 100, 2),
                "open_pnl_usd": round(sum(o["open_pnl_usd"] for o in opens), 2), "open": opens, "pending": self._pending(), "closed": closed[:50],
                "stats": {"trades": n, "winrate": round(len(wins) / n, 3) if n else None, "avg_r": round(sum(t["pnl_r"] for t in closed) / n, 3) if n else None,
                          "best": max((t["net_usd"] for t in closed), default=0), "worst": min((t["net_usd"] for t in closed), default=0), "max_dd_pct": round(dd * 100, 2),
                          "by_grade": by_grade},
                "equity_history": eqh[-500:], "events": events, "rules": "риск 1% на сделку, комиссия 0.05%/сторона + проскальзывание 0.02%, стоп первым внутри свечи, без заглядывания вперёд"}


def run(conn: sqlite3.Connection, report: dict, now: int | None = None) -> dict:
    btc = next((c for c in report["coins"] if c["symbol"].startswith("BTC")), None)
    btc_bull = bool(btc and btc.get("regime", {}).get("long") == "BULL")
    return PaperDesk(conn).step(report["coins"], btc_bull, now)
