"""Paper desk — three $10 000 demo futures accounts (Осторожная / Сбалансированная / Агрессивная, see PROFILES) that trade the
panel's signals automatically, 24/7 on the server, so the strategies can be compared on the same market.

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

# Three strategies = three demo accounts running in parallel on the same signals. One formula, three settings:
#   classes  — which signal classes the account trades (A gap fade, B cascade, R retest, C level-limit ≈ 0 after costs)
#   confirm  — R longs need a second breakout after the retest (history: +0.085R/trade vs +0.04R, half the trades)
#   risk     — share of equity risked per trade;  max_open — positions + pending limits
#   tgt_r    — R-class target in R;  riskoff — what is allowed while ≥70 % of coins fall: 'none' | 'shorts'
PROFILES = {   # one strategy — "Автопилот" — at three risk levels. Rules: B cascade buyback, E panic-climax basket, F open-interest flush
               # (+A FX gap). Retest (R) is not traded: whole-portfolio backtests (scripts/autopilot_backtest.py, scripts/autopilot_v2.py)
               # showed it adds drawdown (−57 %) without adding profit. Numbers below: 18 coins, 2023-01 → 2026-09, costs included.
    "cons": {"name": "Автопилот · осторожно", "classes": {"A", "B", "E", "F", "D"}, "confirm": True, "risk": 0.005, "max_open": 2, "tgt_r": 1.5, "riskoff": "shorts",
             "e_share": 0.10, "f_share": 0.15, "f_open": 2, "d_share": 0.10, "d_open": 2, "prefix": "paper_cons_",
             "about": "Откуп каскада ликвидаций, откуп паники, откуп сброса позиций и (с 17.10, испытание) откуп дисконта фьючерса; риск 0.5% на сделку, малые доли счёта. История 2023–2026: ≈ +19% в год, просадка до −9%, все годы в плюсе."},
    "bal":  {"name": "Автопилот · норма", "classes": {"A", "B", "E", "F", "D"}, "confirm": False, "risk": 0.01, "max_open": 4, "tgt_r": 2.0, "riskoff": "shorts",
             "e_share": 0.20, "f_share": 0.25, "f_open": 3, "d_share": 0.15, "d_open": 3, "prefix": "paper_bal_",
             "about": "Откуп каскада ликвидаций, откуп паники (корзина 20% счёта), откуп сброса позиций (25% счёта, стоп −6%, до 3) и с 17.10 откуп дисконта фьючерса (15% счёта, до 3); риск 1%. История 2023–2026: ≈ +50% в год, просадка до −18%, все годы в плюсе."},
    "aggr": {"name": "Автопилот · агрессивно", "classes": {"A", "B", "E", "F", "D"}, "confirm": False, "risk": 0.015, "max_open": 6, "tgt_r": 2.0, "riskoff": "shorts",
             "e_share": 0.30, "f_share": 0.30, "f_open": 3, "d_share": 0.20, "d_open": 3, "prefix": "paper_",
             "about": "Те же правила, больше газа: риск 1.5%, корзина паники 30%, сброс позиций 30% счёта на сделку. История 2023–2026: ≈ +75% в год, просадка до −29%, все годы в плюсе."},
    # 16.10: experimental account — more trades per day, separate $10 000, not mirrored to Bybit. docs/research/swing.md
    "exp":  {"name": "Эксперимент · свинг", "classes": {"S", "G"}, "confirm": False, "risk": 0.01, "max_open": 0, "tgt_r": 2.0, "riskoff": "shorts",
             "s_share": 0.10, "s_open": 10, "g_share": 0.10, "g_open": 5, "prefix": "paper_exp_",
             "about": "Проверка новых правил на живом рынке: свинг-пробой (выше максимума 5 дней в росте, выход ниже вчерашнего минимума) и быстрый откуп резкой свечи. 10% счёта на сделку, до 15 позиций. История: +0.31% и +0.41% на сделку, но в портфеле ≈ 0 — поэтому отдельный счёт."},
}
RULE_RU = {"A": "откуп гэпа", "B": "откуп каскада", "R": "ретест уровня", "C": "наблюдение у уровня", "E": "откуп паники", "F": "откуп сброса позиций",
           "S": "свинг-пробой", "G": "быстрый откуп", "D": "откуп дисконта фьючерса"}
SHARE_RULES = {"E", "F", "S", "G", "D"}   # sized as a share of equity, own position caps
DEFAULT_PROFILE = "bal"
FEE = 0.0005
SLIP = 0.0002
HOLD_C_H = 48
HOLD_B_H = 8
HOLD_R_H = 192
HOLD_E_H = 24          # class E: liquidation-climax long inside a broad sell-off, time exit 24 h, catastrophe stop −12 % (docs/research/exhaustion_long.md)
E_DROP = -0.015
E_VOL = 2.0
E_STOP = 0.12
E_BREADTH = 0.4       # 16.10: was 0.7 — share of coins down >3 %/24h needed for E; 0.3–0.4 best on history, 0.2 worse (docs/research/panic_breadth.md)
E_BASKET_H = 10        # 16.10: was 20 — one panic basket per 10 h
HOLD_F_H = 24          # class F: open-interest flush long — OI −12 % and price −5 % over 4 h (docs/research/oi_flush.md)
F_OI = -0.12
F_PX = -0.05
HOLD_S_H = 14 * 24     # class S: swing breakout — close above the 5-day closing high while above the 20-day mean; trailing exit (docs/research/swing.md)
S_BRK = 96 * 5
S_TREND = 96 * 20
S_TRAIL = 96
S_STOP = 0.08
HOLD_G_H = 8           # class G: fast buyback — 15m candle ≤ −3σ with a long lower wick, 8 h, stop −3 %
G_SIG = 3.0
G_STOP = 0.03
HOLD_D_H = 24          # class D (17.10): perp ≥ 0.20 % below its spot index on a 1-hour average → long 24 h, stop −12 % (docs/research/new_signals.md)
D_BASIS = -0.0020
D_STOP = 0.12
F_STOP = 0.06          # 16.10: −6 % instead of −12 % — every year positive (2025 −0.29 % → +0.66 % per trade), size raised so risk per trade stays ~1.5 %
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
        conf = None
        if ret is not None:
            ext = b4[ret][2] if side == "LONG" else b4[ret][3]
            for q in range(ret + 1, min(n, ret + 7)):
                if (side == "LONG" and b4[q][4] < lvl - 0.5 * a) or (side == "SHORT" and b4[q][4] > lvl + 0.5 * a):
                    return None
                if (side == "LONG" and b4[q][4] > ext) or (side == "SHORT" and b4[q][4] < ext):
                    conf = q; break
        return {"side": side, "level": lvl, "atr": a, "i_break": i, "i_retest": ret, "bars_since_retest": None if ret is None else n - 1 - ret,
                "i_confirm": conf, "bars_since_confirm": None if conf is None else n - 1 - conf}
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
    def __init__(self, conn: sqlite3.Connection, profile: str = "aggr", classes: set | None = None):
        self.conn = conn
        self.profile = profile
        self.P = dict(PROFILES[profile])
        if classes is not None:        # research/tests: trade an explicit class set (e.g. class C) on this desk
            self.P["classes"] = set(classes)
        px = self.P["prefix"]
        self.T_ORD, self.T_TR, self.T_EQ = px + "orders", px + "trades", px + "equity"
        conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS {self.T_ORD}(id TEXT PRIMARY KEY, symbol TEXT, side TEXT, grade TEXT, kind TEXT, entry REAL, stop REAL,
            tps TEXT, parts TEXT, qty REAL, risk_usd REAL, created INTEGER, expires INTEGER, status TEXT, reason TEXT);
        CREATE TABLE IF NOT EXISTS {self.T_TR}(id TEXT PRIMARY KEY, symbol TEXT, side TEXT, grade TEXT, kind TEXT, entry REAL, stop REAL, stop_now REAL,
            tps TEXT, parts TEXT, qty REAL, qty_left REAL, risk_usd REAL, opened INTEGER, expires INTEGER, closed INTEGER, exit_kind TEXT,
            pnl_usd REAL, pnl_r REAL, tps_hit INTEGER, reason TEXT, last_bar INTEGER);
        CREATE TABLE IF NOT EXISTS {self.T_EQ}(time INTEGER PRIMARY KEY, equity REAL);
        
        CREATE TABLE IF NOT EXISTS paper_meta(k TEXT PRIMARY KEY, v TEXT);""")
        conn.commit()

    # ------------------------------------------------------------------ helpers
    def equity(self) -> float:
        row = self.conn.execute(f"SELECT equity FROM {self.T_EQ} ORDER BY time DESC LIMIT 1").fetchone()
        return row[0] if row else START_EQUITY

    def _open(self) -> list[dict]:
        keys = ("id", "symbol", "side", "grade", "kind", "entry", "stop", "stop_now", "tps", "parts", "qty", "qty_left", "risk_usd", "opened",
                "expires", "closed", "exit_kind", "pnl_usd", "pnl_r", "tps_hit", "reason", "last_bar")
        rows = self.conn.execute(f"SELECT * FROM {self.T_TR} WHERE closed IS NULL").fetchall()
        return [dict(zip(keys, r)) for r in rows]

    def _pending(self) -> list[dict]:
        keys = ("id", "symbol", "side", "grade", "kind", "entry", "stop", "tps", "parts", "qty", "risk_usd", "created", "expires", "status", "reason")
        return [dict(zip(keys, r)) for r in self.conn.execute(f"SELECT * FROM {self.T_ORD} WHERE status='PENDING'").fetchall()]

    # ------------------------------------------------------------------ signals (same rules as the panel)
    def signals(self, coin: dict, btc_bull: bool, now: int) -> list[dict]:
        c15 = coin["candles"]
        if len(c15) < 200:
            return []
        if coin.get("b_only"):   # extra coins trade rule B only
            return [s for s in self.signals({k: v for k, v in coin.items() if k != "b_only"} | {"levels": [], "candles_long": None, "oi_chg4h": None}, btc_bull, now) if s["grade"] == "B"]
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
        confirm = self.P["confirm"]
        fire = rt and ((not confirm and rt["i_retest"] is not None and rt["bars_since_retest"] == 0) or
                       (confirm and rt["i_confirm"] is not None and rt["bars_since_confirm"] == 0))
        if fire:
            side = rt["side"]; sgn = 1 if side == "LONG" else -1
            if not (side == "LONG" and not btc_bull and base != "BTC"):
                entry = price
                stop = rt["level"] - sgn * rt["atr"]
                r = sgn * (entry - stop)
                tgt = self.P["tgt_r"]
                if r > 0 and r / entry <= 0.08:
                    out.append({"id": f"R-{base}-{b4[rt['i_retest']][0]}", "symbol": base, "side": side, "grade": "R", "kind": "RETEST",
                                "entry": entry, "stop": stop, "tps": [entry + sgn * tgt * r] * 3, "parts": [1.0, 0.0, 0.0],
                                "order": "MARKET", "hold_h": HOLD_R_H,
                                "reason": f"Пробой {'сопротивления' if side == 'LONG' else 'поддержки'} {rt['level']:.6g} на объёме и ретест: свеча 4ч вернулась к уровню и закрылась "
                                          f"{'выше' if side == 'LONG' else 'ниже'}{' , затем повторный пробой (подтверждение)' if confirm else ''}. Вход по рынку, стоп {stop:.6g}, цель {tgt:g}R, до 8 дней."})
        # E: liquidation climax inside a broad sell-off — last closed 15m candle ≤ −1.5 % on ≥ 3× its 24h average volume
        if getattr(self, "_stress", 0.0) >= E_BREADTH and len(c15) >= 100:
            last = c15[-2]
            vavg = sum(k[5] for k in c15[-98:-2]) / 96 if len(c15[-98:-2]) == 96 else 0
            ret_oc = last[4] / last[1] - 1 if last[1] else 0
            if vavg and ret_oc <= E_DROP and last[5] >= E_VOL * vavg:
                entry = c15[-1][1]
                out.append({"id": f"E-{base}-{last[0]}", "symbol": base, "side": "LONG", "grade": "E", "kind": "CLIMAX_LONG",
                            "entry": entry, "stop": entry * (1 - E_STOP), "tps": [entry * 1.5] * 3, "parts": [1.0, 0.0, 0.0],
                            "order": "MARKET", "hold_h": HOLD_E_H,
                            "reason": f"Откуп климакса: в широкой распродаже свеча {ret_oc*100:.1f}% на объёме {last[5]/vavg:.1f}× — на истории такие свечи выкупают в течение суток (+2% в среднем). "
                                      f"Выход по времени через 24 ч, катастрофический стоп −{E_STOP*100:.0f}%, размер — доля счёта, не риск по стопу."})
        # F: open-interest flush — positions forcibly closed (OI −12 % in 4 h) while price fell ≥ 5 % → long, 24 h, catastrophe stop −12 %
        oi = coin.get("oi_chg4h")
        if oi is not None and len(c15) >= 18 and c15[-17][4]:
            px4 = c15[-1][4] / c15[-17][4] - 1
            if oi <= F_OI and px4 <= F_PX:
                entry = c15[-1][4]
                out.append({"id": f"F-{base}-{c15[-1][0] // 3_600_000}", "symbol": base, "side": "LONG", "grade": "F", "kind": "OI_FLUSH",
                            "entry": entry, "stop": entry * (1 - F_STOP), "tps": [entry * 1.5] * 3, "parts": [1.0, 0.0, 0.0],
                            "order": "MARKET", "hold_h": HOLD_F_H,
                            "reason": f"Сброс позиций: открытый интерес за 4 ч {oi*100:.0f}%, цена {px4*100:.1f}% — рынок принудительно вытряс плечи. "
                                      f"На истории (18 монет, 2023–2026) после такого откуп +1.8% в среднем за сутки, 62% сделок в плюсе. "
                                      f"Выход через 24 ч, стоп −{F_STOP*100:.0f}%, размер — доля счёта."})
        # S: swing breakout on closed 15m bars since the previous run (one per coin per day)
        cl = coin.get("candles_long") or c15
        if "S" in self.P["classes"] and len(cl) >= S_TREND + 3:
            closes = [k[4] for k in cl]
            for j in range(len(cl) - 4, len(cl) - 1):          # the last three closed bars (runs are ~30 min apart)
                hh = max(closes[j - S_BRK:j]); mean = sum(closes[j - S_TREND + 1:j + 1]) / S_TREND
                if closes[j] > hh and closes[j] > mean:
                    entry = c15[-1][4]
                    out.append({"id": f"S-{base}-{now // 86_400_000}", "symbol": base, "side": "LONG", "grade": "S", "kind": "SWING_BREAKOUT",
                                "entry": entry, "stop": entry * (1 - S_STOP), "tps": [entry * 3] * 3, "parts": [1.0, 0.0, 0.0],
                                "order": "MARKET", "hold_h": HOLD_S_H,
                                "reason": f"Свинг-пробой: цена закрылась выше максимума 5 дней ({hh:.6g}) и выше средней за 20 дней — монета в росте. "
                                          f"Держим, пока цена не закроется ниже минимума прошлых суток; стоп −{S_STOP*100:.0f}%, максимум 14 дней."})
                    break
        # D: perpetual at a deep discount to its spot index (1-hour average, recorded by basis_watch)
        bz = coin.get("basis_1h")
        if bz is not None and bz <= D_BASIS:
            entry = c15[-1][4]
            out.append({"id": f"D-{base}-{now // 3_600_000}", "symbol": base, "side": "LONG", "grade": "D", "kind": "BASIS_DISCOUNT",
                        "entry": entry, "stop": entry * (1 - D_STOP), "tps": [entry * 1.5] * 3, "parts": [1.0, 0.0, 0.0],
                        "order": "MARKET", "hold_h": HOLD_D_H,
                        "reason": f"Дисконт фьючерса: фьючерс торгуется на {bz*100:.2f}% ниже спота (среднее за час) — продавцы фьючерсов перегнули. "
                                  f"На истории (18 монет, 2023–2026) после такого откуп +1.8% в среднем за сутки, все годы в плюсе. "
                                  f"Выход через 24 ч, стоп −{D_STOP*100:.0f}%, размер — доля счёта."})
        # cascade long: last closed 15m candle
        rets = [c15[i][4] / c15[i - 1][4] - 1 for i in range(max(1, len(c15) - 96 * 30), len(c15) - 1)]
        sig = math.sqrt(sum(x * x for x in rets) / len(rets)) if rets else 0
        last = c15[-2]
        ret = last[4] / c15[-3][4] - 1
        rng = last[2] - last[3]
        if "G" in self.P["classes"] and sig and rng > 0 and ret <= -G_SIG * sig and (min(last[1], last[4]) - last[3]) / rng >= 0.5:
            entry = c15[-1][1]
            out.append({"id": f"G-{base}-{last[0]}", "symbol": base, "side": "LONG", "grade": "G", "kind": "FAST_BUYBACK",
                        "entry": entry, "stop": entry * (1 - G_STOP), "tps": [entry * 1.5] * 3, "parts": [1.0, 0.0, 0.0],
                        "order": "MARKET", "hold_h": HOLD_G_H,
                        "reason": f"Быстрый откуп: свеча {ret*100:.1f}% ({ret/sig:.1f}σ) с длинным хвостом снизу — покупатели выкупили. Выход через 8 ч, стоп −{G_STOP*100:.0f}%."})
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
            if c.get("b_only"):
                continue
            cs = c.get("candles") or []
            if len(cs) > 97:
                xs.append(cs[-1][4] / cs[-97][4] - 1)
        return len([x for x in xs if x < -0.03]) / len(xs) if xs else 0.0

    def step(self, coins: list[dict], btc_bull: bool, now: int | None = None) -> dict:
        now = now or int(time.time() * 1000)
        by_sym = {c["symbol"].replace("-USDT", "").replace("USDT", ""): c for c in coins}
        self._stress = self.stress(coins)
        risk_off = self._stress >= 0.7
        events: list[str] = []
        eq = self.equity()
        # class E basket: all climax signals of this step share e_share of equity; one basket per E_BASKET_H hours
        e_sigs = {}
        if "E" in self.P["classes"]:
            for base, coin in by_sym.items():
                for s in self.signals(coin, btc_bull, now):
                    if s["grade"] == "E":
                        e_sigs[base] = s
        e_recent = [t for t in self._open() if t["grade"] == "E" and now - t["opened"] < E_BASKET_H * 3_600_000]
        e_blocked = bool(e_recent) and any(t["opened"] != now for t in e_recent)
        # 1. new signals → orders / market fills
        known = {r[0] for r in self.conn.execute(f"SELECT id FROM {self.T_ORD}")} | {r[0] for r in self.conn.execute(f"SELECT id FROM {self.T_TR}")}
        open_syms = {t["symbol"] for t in self._open()} | {o["symbol"] for o in self._pending()}
        for base, coin in by_sym.items():
            for s in self.signals(coin, btc_bull, now):
                if s["id"] in known or base in open_syms or s["grade"] not in self.P["classes"]:
                    continue
                if s["grade"] == "E" and (e_blocked or base in {t["symbol"] for t in self._open()}):
                    continue
                if risk_off and s["grade"] == "R" and s["side"] == "SHORT":   # history: retest shorts inside a broad sell-off ≈ 0 (+0.013R, n=839) vs +0.155R in calm markets
                    events.append(f"пропуск шорта {base}: ретест внутри широкой распродажи на истории ≈ 0 — ждём спокойного рынка")
                    continue
                if risk_off and ((s["side"] == "LONG" and s["grade"] not in ("B", "E", "F", "G", "D")) or self.P["riskoff"] == "none"):   # B = cascade fade, tested in crashes
                    events.append(f"пропуск {'лонга' if s['side'] == 'LONG' else 'шорта'} {base}: рынок падает широким фронтом (режим риска; на истории покупка дна со стопом −0.20R, шорт после падения −0.04R — не торгуем)")
                    continue
                if s["grade"] == "F" and sum(1 for t in self._open() if t["grade"] == "F") >= self.P.get("f_open", 3):
                    continue
                if s["grade"] in ("S", "G", "D") and sum(1 for t in self._open() if t["grade"] == s["grade"]) >= self.P.get(s["grade"].lower() + "_open", 0):
                    continue
                if s["grade"] not in SHARE_RULES and sum(1 for t in self._open() if t["grade"] not in SHARE_RULES) + len(self._pending()) >= self.P["max_open"]:
                    continue
                if s["grade"] == "E":
                    notional = eq * self.P.get("e_share", 0.2) / max(1, len(e_sigs))
                    qty = notional / s["entry"]
                    risk_usd = notional * E_STOP
                elif s["grade"] == "F":
                    notional = eq * self.P.get("f_share", 0.15)
                    qty = notional / s["entry"]
                    risk_usd = notional * F_STOP
                elif s["grade"] in ("S", "G", "D"):
                    notional = eq * self.P.get(s["grade"].lower() + "_share", 0.1)
                    qty = notional / s["entry"]
                    risk_usd = notional * {"S": S_STOP, "G": G_STOP, "D": D_STOP}[s["grade"]]
                else:
                    risk_usd = eq * self.P["risk"]
                    r = abs(s["entry"] - s["stop"])
                    qty = risk_usd / (r + s["entry"] * (2 * FEE + SLIP))
                if s["order"] == "MARKET":
                    fill = s["entry"] * (1 + SLIP)
                    self.conn.execute(f"INSERT INTO {self.T_TR} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (s["id"], base, s["side"], s["grade"], s["kind"], fill, s["stop"], s["stop"], json.dumps(s["tps"]), json.dumps(s["parts"]),
                                       qty, qty, risk_usd, now, now + s["hold_h"] * 3_600_000, None, None, 0.0, 0.0, 0, s["reason"], coin["candles"][-1][0]))
                    events.append(f"открыт {s['side']} {base} по рынку {fill:.6g} ({RULE_RU.get(s['grade'], s['grade'])})")
                else:
                    self.conn.execute(f"INSERT INTO {self.T_ORD} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (s["id"], base, s["side"], s["grade"], s["kind"], s["entry"], s["stop"], json.dumps(s["tps"]), json.dumps(s["parts"]),
                                       qty, risk_usd, now, now + ORDER_TTL_H * 3_600_000, "PENDING", s["reason"]))
                    events.append(f"лимитка {s['side']} {base} @ {s['entry']:.6g} ({RULE_RU.get(s['grade'], s['grade'])})")
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
                hold = {"C": HOLD_C_H, "B": HOLD_B_H, "R": HOLD_R_H, "E": HOLD_E_H, "F": HOLD_F_H, "S": HOLD_S_H, "G": HOLD_G_H, "D": HOLD_D_H}.get(o["grade"], HOLD_C_H)
                # conservative: if the fill candle also reached the stop, count it as stopped in the same candle
                stopped = (o["side"] == "LONG" and filled[3] <= o["stop"]) or (o["side"] == "SHORT" and filled[2] >= o["stop"])
                sgn = 1 if o["side"] == "LONG" else -1
                pnl = (sgn * (o["stop"] * (1 - sgn * SLIP) - o["entry"]) * o["qty"] - o["stop"] * o["qty"] * FEE) if stopped else 0.0
                pnl_r = (pnl - o["entry"] * o["qty"] * FEE) / o["risk_usd"] if stopped else 0.0
                self.conn.execute(f"INSERT INTO {self.T_TR} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (o["id"], o["symbol"], o["side"], o["grade"], o["kind"], o["entry"], o["stop"], o["stop"], o["tps"], o["parts"],
                                   o["qty"], 0.0 if stopped else o["qty"], o["risk_usd"], filled[0], filled[0] + hold * 3_600_000,
                                   filled[0] if stopped else None, "STOP" if stopped else None, pnl, pnl_r, 0, o["reason"], filled[0]))
                self.conn.execute(f"UPDATE {self.T_ORD} SET status='FILLED' WHERE id=?", (o["id"],))
                events.append(f"исполнена лимитка {o['side']} {o['symbol']} @ {o['entry']:.6g}" + (" и выбита стопом в той же свече" if stopped else ""))
            elif now > o["expires"]:
                self.conn.execute(f"UPDATE {self.T_ORD} SET status='EXPIRED' WHERE id=?", (o["id"],))
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
            cands = (coin.get("candles_long") or coin["candles"]) if t["grade"] == "S" else coin["candles"]
            for ci, c in enumerate(cands):
                if c[0] <= t["last_bar"] or c[0] + 900_000 > now:
                    continue
                last_bar = c[0]
                if t["grade"] == "S" and c[0] >= t["opened"] + 3_600_000 and ci >= S_TRAIL:
                    low_1d = min(k[4] for k in cands[ci - S_TRAIL:ci])
                    if c[4] < low_1d and c[4] > stop_now:
                        px = c[4] * (1 - SLIP)
                        pnl += (px - t["entry"]) * qty_left - px * qty_left * FEE
                        qty_left = 0; closed = c[0]; exit_kind = "TRAIL"
                        break
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
            self.conn.execute(f"UPDATE {self.T_TR} SET qty_left=?, stop_now=?, tps_hit=?, pnl_usd=?, pnl_r=?, closed=?, exit_kind=?, last_bar=? WHERE id=?",
                              (qty_left, stop_now, hit, pnl, pnl_r, closed, exit_kind, last_bar, t["id"]))
            if closed:
                realized += pnl - entry_fee
                events.append(f"закрыт {t['side']} {t['symbol']}: {exit_kind}, {pnl - entry_fee:+.2f}$ ({pnl_r:+.2f}R)")
        self.conn.commit()
        # 4. equity = start + realized (closed) ; open P&L marked separately
        realized_total = sum((r[0] or 0) - (r[1] or 0) * (r[2] or 0) * FEE for r in
                             self.conn.execute(f"SELECT pnl_usd, entry, qty FROM {self.T_TR} WHERE closed IS NOT NULL").fetchall())
        equity = round(START_EQUITY + realized_total, 2)
        # the curve is mark-to-market (open P&L included) — drawdowns are visible, not hidden until the stop
        price_now = {k: v["candles"][-1][4] for k, v in by_sym.items()}
        open_pnl = 0.0
        for t in self._open():
            px = price_now.get(t["symbol"], t["entry"]); sgn = 1 if t["side"] == "LONG" else -1
            open_pnl += sgn * (px - t["entry"]) * t["qty_left"] + t["pnl_usd"]
        try:
            self.conn.execute(f"ALTER TABLE {self.T_EQ} ADD COLUMN mtm REAL")
        except Exception:  # noqa: BLE001
            pass
        self.conn.execute(f"INSERT OR REPLACE INTO {self.T_EQ}(time, equity, mtm) VALUES(?,?,?)", (now, equity, round(equity + open_pnl, 2)))
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
        closed = [dict(zip(keys, r)) for r in self.conn.execute(f"SELECT * FROM {self.T_TR} WHERE closed IS NOT NULL ORDER BY closed DESC LIMIT 100").fetchall()]
        for t in closed:
            t["tps"] = json.loads(t["tps"]); t["parts"] = json.loads(t["parts"]); t["net_usd"] = round(t["pnl_usd"] - t["entry"] * t["qty"] * FEE, 2)
        n = len(closed); wins = [t for t in closed if t["net_usd"] > 0]
        try:
            eqh = [{"time": a, "equity": (m if m is not None else b)} for a, b, m in self.conn.execute(f"SELECT time,equity,mtm FROM {self.T_EQ} ORDER BY time").fetchall()]
        except Exception:  # noqa: BLE001
            eqh = [{"time": a, "equity": b} for a, b in self.conn.execute(f"SELECT time,equity FROM {self.T_EQ} ORDER BY time").fetchall()]
        peak, dd = START_EQUITY, 0.0
        for e in eqh:
            peak = max(peak, e["equity"]); dd = max(dd, (peak - e["equity"]) / peak)
        by_grade = {}
        for g in ("A", "B", "R", "C", "E", "F", "S", "G", "D"):
            ts = [t for t in closed if t["grade"] == g]
            if ts:
                by_grade[g] = {"n": len(ts), "winrate": round(len([t for t in ts if t["net_usd"] > 0]) / len(ts), 3), "avg_r": round(sum(t["pnl_r"] for t in ts) / len(ts), 3),
                               "usd": round(sum(t["net_usd"] for t in ts), 2)}
        eq = self.equity()
        return {"asof": now, "profile": self.profile, "name": self.P["name"], "about": self.P["about"], "start_equity": START_EQUITY, "risk_off": self.stress(list(by_sym.values())) >= 0.7, "stress": round(self.stress(list(by_sym.values())), 3), "equity": eq, "pnl_usd": round(eq - START_EQUITY, 2), "pnl_pct": round((eq / START_EQUITY - 1) * 100, 2),
                "open_pnl_usd": round(sum(o["open_pnl_usd"] for o in opens), 2), "open": opens, "pending": self._pending(), "closed": closed[:50],
                "stats": {"trades": n, "winrate": round(len(wins) / n, 3) if n else None, "avg_r": round(sum(t["pnl_r"] for t in closed) / n, 3) if n else None,
                          "best": max((t["net_usd"] for t in closed), default=0), "worst": min((t["net_usd"] for t in closed), default=0), "max_dd_pct": round(dd * 100, 2),
                          "by_grade": by_grade},
                "equity_history": eqh[-500:], "events": events, "rules": f"риск {self.P['risk']*100:g}% на сделку, правила: {', '.join(RULE_RU[g] for g in sorted(self.P['classes']))}, до {self.P['max_open']} позиций, комиссия 0.05%/сторона + проскальзывание 0.02%, стоп первым внутри свечи, без заглядывания вперёд"}


def scan(coins: list[dict]) -> dict:
    """How close every rule is to firing, per coin — for the panel radar («почему сделок нет»). Same formulas as signals()."""
    rows = []
    for c in coins:
        c15 = c.get("candles") or []
        if len(c15) < 200:
            continue
        base = c["symbol"].replace("-USDT", "").replace("USDT", "")
        rets = [c15[i][4] / c15[i - 1][4] - 1 for i in range(max(1, len(c15) - 96 * 30), len(c15) - 1)]
        sig = math.sqrt(sum(x * x for x in rets) / len(rets)) if rets else 0
        last = c15[-2]; ret = last[4] / c15[-3][4] - 1
        px4 = c15[-1][4] / c15[-17][4] - 1 if c15[-17][4] else None
        rows.append({"coin": base, "b_only": bool(c.get("b_only")), "b_sigma": round(ret / sig, 2) if sig else None,
                     "px4": round(px4, 4) if px4 is not None else None, "oi4": None if c.get("oi_chg4h") is None else round(c["oi_chg4h"], 4),
                     "ret_last": round(ret, 4), "basis": None if c.get("basis_1h") is None else round(c["basis_1h"], 5)})
    return {"breadth": round(PaperDesk.stress(coins), 3), "need_breadth": E_BREADTH, "d_need_basis": D_BASIS, "b_need_sigma": -3.5, "f_need_oi": F_OI, "f_need_px": F_PX,
            "e_need_drop": E_DROP, "rows": rows}


def run(conn: sqlite3.Connection, report: dict, now: int | None = None) -> dict:
    """Runs the three strategy accounts; the top level of the result is the default (balanced) account,
    `profiles` holds all three so the panel can switch between them."""
    btc = next((c for c in report["coins"] if c["symbol"].startswith("BTC")), None)
    btc_bull = bool(btc and btc.get("regime", {}).get("long") == "BULL")
    now = now or int(time.time() * 1000)
    try:   # rule F needs open-interest history: record this run, attach the 4-hour change to every coin
        from cnq.agents import oi_watch
        bases = [c["symbol"].replace("-USDT", "").replace("USDT", "") for c in report["coins"] if not c.get("b_only")]
        if not report.get("_no_network"):
            oi_watch.record(conn, bases, now)
        for c, b in zip([c for c in report["coins"] if not c.get("b_only")], bases):
            c["oi_chg4h"] = oi_watch.change(conn, b, now)
    except Exception:  # noqa: BLE001
        pass
    try:   # rule D needs the perp-vs-index basis: record this run, attach the 1-hour average
        from cnq.agents import basis_watch
        bases = [c["symbol"].replace("-USDT", "").replace("USDT", "") for c in report["coins"] if not c.get("b_only")]
        if not report.get("_no_network"):
            basis_watch.record(conn, bases, now)
        for c, b in zip([c for c in report["coins"] if not c.get("b_only")], bases):
            c["basis_1h"] = basis_watch.avg_1h(conn, b, now)
    except Exception:  # noqa: BLE001
        pass
    snaps = {k: PaperDesk(conn, k).step(report["coins"], btc_bull, now) for k in PROFILES}
    out_scan = scan(report["coins"])
    out = dict(snaps[DEFAULT_PROFILE])
    out["profiles"] = {k: {kk: vv for kk, vv in v.items() if kk != "equity_history"} | {"equity_history": v["equity_history"][-300:]} for k, v in snaps.items()}
    out["scan"] = out_scan
    out["profile_list"] = [{"key": k, "name": p["name"], "about": p["about"], "risk": p["risk"], "classes": sorted(p["classes"]), "max_open": p["max_open"]} for k, p in PROFILES.items()]
    return out
