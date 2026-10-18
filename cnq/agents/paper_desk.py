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

State: data/agents.sqlite (paper_orders, paper_trades, paper_equity; one-time resets: migrations + paper_archive, see reset_desks).
Output: web/paper.json for the panel (per desk `since` and, after a reset, `reset` = {time, archived_pnl_usd, note_ru}).
"""
from __future__ import annotations

import datetime as dt
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
    "cons": {"name": "Консервативная", "classes": {"A", "B", "E", "F", "D"}, "confirm": True, "risk": 0.005, "max_open": 2, "tgt_r": 1.5, "riskoff": "shorts",
             "e_share": 0.10, "f_share": 0.15, "f_open": 2, "d_share": 0.10, "d_open": 2, "prefix": "paper_cons_",
             "about": "Откуп каскада ликвидаций, откуп паники, откуп сброса позиций и (с 17.10, испытание) откуп дисконта фьючерса; риск 0.5% на сделку, малые доли счёта. История 2023–2026: ≈ +19% в год, просадка до −9%, все годы в плюсе."},
    "bal":  {"name": "Сбалансированная", "classes": {"A", "B", "E", "F", "D"}, "confirm": False, "risk": 0.01, "max_open": 4, "tgt_r": 2.0, "riskoff": "shorts",
             "e_share": 0.20, "f_share": 0.25, "f_open": 3, "d_share": 0.15, "d_open": 3, "prefix": "paper_bal_",
             "about": "Откуп каскада ликвидаций, откуп паники (корзина 20% счёта), откуп сброса позиций (25% счёта, стоп −6%, до 3) и с 17.10 откуп дисконта фьючерса (15% счёта, до 3); риск 1%. История 2023–2026: ≈ +50% в год, просадка до −18%, все годы в плюсе."},
    "aggr": {"name": "Агрессивная", "classes": {"A", "B", "E", "F", "D"}, "confirm": False, "risk": 0.015, "max_open": 6, "tgt_r": 2.0, "riskoff": "shorts",
             "e_share": 0.30, "f_share": 0.30, "f_open": 3, "d_share": 0.20, "d_open": 3, "prefix": "paper_",
             "about": "Те же правила, больше газа: риск 1.5%, корзина паники 30%, сброс позиций 30% счёта на сделку. История 2023–2026: ≈ +75% в год, просадка до −29%, все годы в плюсе."},
    # 17.10: «Тест новых идей» (key `lab`, formerly «Лаборатория») — aggressive range/level rules the owner asked to try live although history is ≈0 or negative
    # (docs/research/range.md). Limit entries only, filled only when price trades through. Separate $10 000, not mirrored.
    "lab":  {"name": "Тест новых идей", "classes": {"L", "W", "X", "Y", "J"}, "confirm": False, "risk": 0.005, "max_open": 12, "tgt_r": 1.5, "riskoff": "shorts",
             "caps": {"L": 7, "W": 4}, "max_notional": 0.10, "x_share": 0.05, "x_open": 4, "y_share": 0.15, "y_open": 3, "j_share": 0.15, "j_open": 1, "prefix": "paper_lab_",
             "about": "Эксперименты вживую (на истории не доказаны): L — покупка у низа 14-дневного диапазона лимиткой (история +0.30% на сделку, 2025–26 в минусе), W — сделка по сильной новости, X — откуп паники на 36 ликвидных альткоинах (+4% в год вслепую), Y — откуп дисконта фьючерса на альткоинах (+12% в год вслепую, просадка −37%). 17.10 убраны N/Q — на истории они обнуляли счёт. Не больше 10% счёта на сделку."},
    # 16.10: experimental account — more trades per day, separate $10 000, not mirrored to Bybit. docs/research/swing.md
    # 18.10: first «Лаборатория» rule that passed the strict check (scripts/lab/fam_trend.py, idea 6) — its own $10 000, not mirrored.
    # 5 slots × 20 % of the account = exactly the 5-slot account the lab judged (2025–26: +19.7 %, max drawdown −49 %).
    "brk":  {"name": "Пробой", "classes": {"T"}, "confirm": False, "risk": 0.01, "max_open": 0, "tgt_r": 2.0, "riskoff": "shorts",
             "t_share": 0.20, "t_open": 5, "prefix": "paper_brk_",
             "about": "Из «Лаборатории», прошла строгую проверку: лонг на пробое максимума 5 дней с объёмом ×2, только когда BTC выше своей 50-дневной средней; стоп 8 ATR (15м), выход через 2 дня. До 5 позиций по 20% счёта. История 2025–2026: +0.75% на сделку, счёт +20%, просадка до −49%."},
    # 18.10: the rule that passed the lab's strictest check (2025 and 2026 each positive, also without the 3 best days) on the
    # point-in-time top-50 AND on the 18 coins: 2025–26 account +34…+43 %, max drawdown −12 % (top-50, 2023+) / −67 % (18 coins incl. 2022).
    "imp":  {"name": "Импульс BTC", "classes": {"K"}, "confirm": False, "risk": 0.01, "max_open": 0, "tgt_r": 2.0, "riskoff": "shorts",
             "k_share": 0.20, "k_open": 5, "prefix": "paper_imp_",
             "about": "Из «Лаборатории», прошла самую строгую проверку: когда BTC за час вырос на 2.5% и больше, покупаем самые ликвидные альткоины — они догоняют. Выход через 12 ч, широкий стоп 8 ATR, до 5 позиций по 20% счёта. История: 2025 и 2026 в плюсе, счёт +34% за 2025–2026, просадка −12% (топ-50, с 2023); в обвальном 2022 — минус."},
    "exp":  {"name": "Свинг", "classes": {"S", "G"}, "confirm": False, "risk": 0.01, "max_open": 0, "tgt_r": 2.0, "riskoff": "shorts",
             "s_share": 0.10, "s_open": 10, "g_share": 0.10, "g_open": 5, "prefix": "paper_exp_",
             "about": "Проверка новых правил на живом рынке: свинг-пробой (выше максимума 5 дней в росте, выход ниже вчерашнего минимума) и быстрый откуп резкой свечи. 10% счёта на сделку, до 15 позиций. История: +0.31% и +0.41% на сделку, но в портфеле ≈ 0 — поэтому отдельный счёт."},
}
RULE_RU = {"A": "откуп гэпа", "B": "откуп каскада", "R": "ретест уровня", "C": "наблюдение у уровня", "E": "откуп паники", "F": "откуп сброса позиций",
           "S": "свинг-пробой", "G": "быстрый откуп", "D": "откуп дисконта фьючерса", "T": "пробой 5 дней при росте BTC", "K": "альт догоняет рывок BTC",
           "L": "покупка у низа диапазона", "N": "отскок от часовой поддержки", "Q": "шорт от часового сопротивления",
           "W": "сделка по сильной новости", "X": "откуп паники на альткоине", "Y": "откуп дисконта на альткоине", "J": "разворот после Никкей"}
THROUGH = {"L", "N", "Q"}        # lab limits fill only when price trades through the level by ≥ 0.01 %
HOLD_L_H, HOLD_N_H = 72, 24
SHARE_RULES = {"E", "F", "S", "G", "D", "X", "Y", "J", "T", "K"}   # sized as a share of equity, own position caps
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
HOLD_K_H = 12          # class K (18.10, lab): BTC +2.5 % in one hour → long the most traded alts, 12 h, stop 8 ATR(15m) (scripts/lab, «Альт догоняет BTC»)
K_BTC = 0.025
K_ATR = 8.0
HOLD_T_H = 48          # class T (18.10, lab): close above the 5-day high on 2× volume while BTC > its 50-day EMA → long 48 h, stop 8 ATR(15m)
T_BRK = 96 * 5
T_ATR = 8.0
T_VOL = 2.0
F_STOP = 0.06          # 16.10: −6 % instead of −12 % — every year positive (2025 −0.29 % → +0.66 % per trade), size raised so risk per trade stays ~1.5 %
ORDER_TTL_H = 24
MAX_OPEN = 6


def agg1h(c15: list[list], now: int | None = None) -> list[list]:
    """15m → closed 1h candles [t,o,h,l,c,v] (the running hour is dropped)."""
    out = []; cur = None
    for k in c15:
        t0 = k[0] - (k[0] % 3_600_000)
        if cur is None or cur[0] != t0:
            if cur: out.append(cur)
            cur = [t0, k[1], k[2], k[3], k[4], k[5]]
        else:
            cur[2] = max(cur[2], k[2]); cur[3] = min(cur[3], k[3]); cur[4] = k[4]; cur[5] += k[5]
    if cur and (now is None or cur[0] + 3_600_000 <= now): out.append(cur)
    return out


def fractals(b: list[list], side: str, look: int = 48, w: int = 2) -> list[float]:
    """Swing lows (side='low') or highs over the last `look` closed bars: a bar beyond its w neighbours on both sides."""
    xs = []; n = len(b)
    for i in range(max(w, n - look), n - w):
        v = b[i][3] if side == "low" else b[i][2]
        nb = [b[j][3] if side == "low" else b[j][2] for j in range(i - w, i + w + 1) if j != i]
        if (side == "low" and all(v < x for x in nb)) or (side == "high" and all(v > x for x in nb)):
            xs.append(v)
    return xs


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

    def _archives(self) -> list[dict]:
        """This desk's archived states (one-time resets, see reset_desks), oldest first."""
        try:
            rows = self.conn.execute("SELECT json FROM paper_archive WHERE profile=? ORDER BY time", (self.profile,)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [json.loads(r[0]) for r in rows]

    def reset_info(self) -> dict | None:
        a = self._archives()
        return a[-1].get("reset") if a else None

    def _archived_ids(self) -> set:
        """Signal ids already traded before a reset — never re-opened (the ids are deterministic per bar/hour/day)."""
        out = set()
        for a in self._archives():
            out |= {t["id"] for t in a.get("trades", [])} | {o["id"] for o in a.get("orders", [])}
        return out

    # ------------------------------------------------------------------ signals (same rules as the panel)
    def signals(self, coin: dict, btc_bull: bool, now: int) -> list[dict]:
        c15 = coin["candles"]
        if len(c15) < 200:
            return []
        if coin.get("b_only"):   # extra alt coins: lab experiments only (17.10: B on extras switched off — no gain without hindsight)
            sub = self.signals({k: v for k, v in coin.items() if k != "b_only"} | {"levels": [], "candles_long": None, "oi_chg4h": None, "_alt": True}, btc_bull, now)
            return [s for s in sub if s["grade"] in ("L", "W", "X", "Y", "K")]
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
        # T (lab, 18.10): close above the highest high of the previous 5 days on > 2× the 24-hour average volume, BTC above its 50-day EMA
        if "T" in self.P["classes"] and getattr(self, "_btc50", None) and self._btc50.get("bull") and len(cl) >= T_BRK + 100:
            for j in range(len(cl) - 4, len(cl) - 1):          # the last three closed bars (runs are ~30 min apart)
                hh = max(k[2] for k in cl[j - T_BRK:j]); vavg = sum(k[5] for k in cl[j - 96:j]) / 96
                if cl[j][4] > hh and vavg > 0 and cl[j][5] > T_VOL * vavg:
                    entry = c15[-1][4]
                    at = _atr(cl[j - 30:j + 1]) or entry * 0.003
                    out.append({"id": f"T-{base}-{cl[j][0] // 86_400_000}", "symbol": base, "side": "LONG", "grade": "T", "kind": "BREAKOUT_5D",
                                "entry": entry, "stop": entry - T_ATR * at, "tps": [entry * 3] * 3, "parts": [1.0, 0.0, 0.0],
                                "order": "MARKET", "hold_h": HOLD_T_H,
                                "reason": f"Пробой 5 дней: закрытие {cl[j][4]:.6g} выше максимума 5 дней ({hh:.6g}) на объёме {cl[j][5] / vavg:.1f}× среднего за сутки; "
                                          f"BTC выше своей 50-дневной средней. Из «Лаборатории»: на 2025–2026 +0.75% на сделку, но прибыль неровная. "
                                          f"Выход через 48 ч, широкий стоп {T_ATR:g} ATR ({(T_ATR * at / entry) * 100:.1f}%), 20% счёта."})
                    break
        # K (lab, 18.10): BTC rose ≥ 2.5 % over the last hour (4 closed 15m bars) → long this alt; one per coin per 4 h
        kb = getattr(self, "_btc_jump", None)
        if "K" in self.P["classes"] and kb and base != "BTC" and len(c15) >= 30:
            entry = c15[-1][4]
            at = _atr(c15[-31:-1]) or entry * 0.003
            out.append({"id": f"K-{base}-{kb['bar'] // 14_400_000}", "symbol": base, "side": "LONG", "grade": "K", "kind": "BTC_IMPULSE",
                        "entry": entry, "stop": entry - K_ATR * at, "tps": [entry * 3] * 3, "parts": [1.0, 0.0, 0.0],
                        "order": "MARKET", "hold_h": HOLD_K_H,
                        "reason": f"Рывок BTC: +{kb['ret'] * 100:.1f}% за час — ликвидные альткоины обычно догоняют. Из «Лаборатории»: на 2025–2026 "
                                  f"счёт +34%, просадка −12%. Выход через 12 ч, широкий стоп {K_ATR:g} ATR ({K_ATR * at / entry * 100:.1f}%), 20% счёта."})
        # lab rules (L/N/Q): limit orders inside the range, valid ~1 h, re-placed every run
        if self.P["classes"] & {"L", "N", "Q"}:
            h1 = agg1h(coin.get("candles_long") or c15, now)
            price = c15[-1][4]; slot = now // 3_600_000
            if "L" in self.P["classes"] and len(h1) >= 14 * 24:
                lo = min(x[3] for x in h1[-336:]); hi = max(x[2] for x in h1[-336:]); wdt = hi - lo
                ent = lo + 0.30 * wdt
                if wdt > 0 and ent < price <= lo + 0.55 * wdt:
                    out.append({"id": f"L-{base}-{slot}", "symbol": base, "side": "LONG", "grade": "L", "kind": "RANGE_LOW_LIMIT",
                                "entry": ent, "stop": lo - 0.25 * wdt, "tps": [lo + 0.80 * wdt] * 3, "parts": [1.0, 0.0, 0.0], "order": "LIMIT",
                                "hold_h": HOLD_L_H, "ttl_h": 1,
                                "reason": f"Тест новых идей: диапазон 14 дней {lo:.6g}–{hi:.6g}. Лимитка на 30% от низа ({ent:.6g}), цель — 80% диапазона, стоп под низом. Эксперимент."})
            if ("N" in self.P["classes"] or "Q" in self.P["classes"]) and len(h1) >= 60:
                trs = [max(h1[i][2] - h1[i][3], abs(h1[i][2] - h1[i - 1][4]), abs(h1[i][3] - h1[i - 1][4])) for i in range(len(h1) - 14, len(h1))]
                atr1 = sum(trs) / len(trs)
                lows = [x for x in fractals(h1, "low") if price * 0.98 <= x < price * 0.999]
                highs = [x for x in fractals(h1, "high") if price * 1.001 < x <= price * 1.02]
                if "N" in self.P["classes"] and lows and atr1 > 0:
                    lv = max(lows); st = lv - 0.6 * atr1; up = [x for x in fractals(h1, "high") if x > price]
                    tg = min(up) if up else lv + 1.5 * (lv - st)
                    out.append({"id": f"N-{base}-{slot}", "symbol": base, "side": "LONG", "grade": "N", "kind": "H1_SUPPORT_LIMIT",
                                "entry": lv, "stop": st, "tps": [tg] * 3, "parts": [1.0, 0.0, 0.0], "order": "LIMIT", "hold_h": HOLD_N_H, "ttl_h": 1,
                                "reason": f"Тест новых идей: часовая поддержка {lv:.6g} (локальный минимум). Лимитка на уровне, стоп {st:.6g}, цель {tg:.6g}. Эксперимент: на истории ≈ −0.1% на сделку."})
                if "Q" in self.P["classes"] and highs and atr1 > 0:
                    lv = min(highs); st = lv + 0.6 * atr1; dn = [x for x in fractals(h1, "low") if x < price]
                    tg = max(dn) if dn else lv - 1.5 * (st - lv)
                    out.append({"id": f"Q-{base}-{slot}", "symbol": base, "side": "SHORT", "grade": "Q", "kind": "H1_RESIST_LIMIT",
                                "entry": lv, "stop": st, "tps": [tg] * 3, "parts": [1.0, 0.0, 0.0], "order": "LIMIT", "hold_h": HOLD_N_H, "ttl_h": 1,
                                "reason": f"Тест новых идей: часовое сопротивление {lv:.6g} (локальный максимум). Лимитка на продажу, стоп {st:.6g}, цель {tg:.6g}. Эксперимент: шорты на истории слабые."})
        # W (lab): strong fresh news about this coin (or the whole market → BTC), rated by the news analyst 4–5/5, confidence ≥ 0.6
        if "W" in self.P["classes"]:
            try:
                rows = self.conn.execute("SELECT news_id, asset, direction, horizon, magnitude, confidence, COALESCE(NULLIF(title_ru,''), '') FROM news_impact "
                                         "WHERE created >= ? AND magnitude >= 4 AND confidence >= 0.6 AND direction IN ('bull','bear')",
                                         (now - 45 * 60_000,)).fetchall()
            except Exception:  # noqa: BLE001
                rows = []
            for nid, asset, d, hz, mag, conf, title in rows:
                if (asset if asset != "MARKET" else "BTC") != base:
                    continue
                side = "LONG" if d == "bull" else "SHORT"; sg = 1 if side == "LONG" else -1; entry = c15[-1][4]
                hold = 6 if hz == "hours" else 48
                out.append({"id": f"W-{base}-{nid}", "symbol": base, "side": side, "grade": "W", "kind": "NEWS",
                            "entry": entry, "stop": entry * (1 - sg * 0.03), "tps": [entry * (1 + sg * 0.06)] * 3, "parts": [1.0, 0.0, 0.0],
                            "order": "MARKET", "hold_h": hold,
                            "reason": f"Тест новых идей: сильная новость ({mag}/5, уверенность {conf:.0%}){': ' + title if title else ''}. "
                                      f"{'Покупка' if side == 'LONG' else 'Продажа'} по рынку, стоп −3%, цель +6%, выход через {hold} ч. Эксперимент без истории."})
                break
        # X / Y (lab, alt coins only — docs/research/alt100.md, aggressive and unproven): panic candle in a broad sell-off of the core
        # coins, and a deep futures discount (≤ −0.25 %, 1-hour average)
        if coin.get("_alt") and "X" in self.P["classes"] and getattr(self, "_stress", 0.0) >= E_BREADTH and len(c15) >= 100:
            last = c15[-2]
            vavg = sum(k[5] for k in c15[-98:-2]) / 96 if len(c15[-98:-2]) == 96 else 0
            ret_oc = last[4] / last[1] - 1 if last[1] else 0
            if vavg and ret_oc <= E_DROP and last[5] >= E_VOL * vavg:
                entry = c15[-1][1]
                out.append({"id": f"X-{base}-{last[0]}", "symbol": base, "side": "LONG", "grade": "X", "kind": "ALT_PANIC",
                            "entry": entry, "stop": entry * (1 - E_STOP), "tps": [entry * 1.5] * 3, "parts": [1.0, 0.0, 0.0], "order": "MARKET", "hold_h": HOLD_E_H,
                            "reason": f"Тест новых идей: откуп паники на альткоине — свеча {ret_oc*100:.1f}% на объёме {last[5]/vavg:.1f}×, пока падает {getattr(self, '_stress', 0)*100:.0f}% крупных монет. "
                                      f"Выход через 24 ч, стоп −12%. История на альтах без подглядывания: +4% в год, 2026 в минусе — эксперимент."})
        bz_alt = coin.get("basis_1h")
        if coin.get("_alt") and "Y" in self.P["classes"] and bz_alt is not None and bz_alt <= -0.0025:
            entry = c15[-1][4]
            out.append({"id": f"Y-{base}-{now // 86_400_000}", "symbol": base, "side": "LONG", "grade": "Y", "kind": "ALT_BASIS",
                        "entry": entry, "stop": entry * (1 - D_STOP), "tps": [entry * 1.5] * 3, "parts": [1.0, 0.0, 0.0], "order": "MARKET", "hold_h": HOLD_D_H,
                        "reason": f"Тест новых идей: фьючерс альткоина на {bz_alt*100:.2f}% ниже спота (среднее за час). Выход через 24 ч, стоп −12%. "
                                  f"История на альтах: +12% в год вслепую, но просадка −37% и 2026 в минусе — эксперимент."})
        # J (lab): Nikkei reversal — after the Tokyo close, trade BTC against a Nikkei day move of ±1.5 %, hold 24 h (docs/research/macro_factors.md:
        # +0.76 % per trade 2023–26, every year positive, beats shifted calendars; but 2021–22 ≈ 0 → experiment)
        nk = getattr(self, "_nikkei", None)
        if base == "BTC" and "J" in self.P["classes"] and nk and abs(nk["chg"]) >= 1.5:
            t = dt.datetime.fromtimestamp(now / 1000, dt.timezone.utc)
            if nk["date"] == t.strftime("%Y-%m-%d") and (6, 45) <= (t.hour, t.minute) < (12, 0):
                side = "LONG" if nk["chg"] < 0 else "SHORT"; sg = 1 if side == "LONG" else -1; entry = c15[-1][4]
                out.append({"id": f"J-BTC-{nk['date']}", "symbol": base, "side": side, "grade": "J", "kind": "NIKKEI_REVERSAL",
                            "entry": entry, "stop": entry * (1 - sg * 0.20), "tps": [entry * (1 + sg * 0.5)] * 3, "parts": [1.0, 0.0, 0.0],
                            "order": "MARKET", "hold_h": 24,
                            "reason": f"Тест новых идей: Никкей закрылся {nk['chg']:+.1f}% — на истории 2023–2026 биткоин в следующие сутки чаще шёл в обратную сторону "
                                      f"(+0.76% на сделку, все годы в плюсе, но в 2021–22 эффекта не было). {'Покупка' if side == 'LONG' else 'Продажа'} на 24 ч, 15% счёта. Эксперимент."})
        # D: perpetual at a deep discount to its spot index (1-hour average, recorded by basis_watch)
        bz = coin.get("basis_1h")
        if bz is not None and bz <= D_BASIS and not coin.get("_alt"):
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
        self._btc_jump = None
        if "K" in self.P["classes"]:
            bc = (by_sym.get("BTC") or {}).get("candles") or []
            for j in range(len(bc) - 4, len(bc) - 1):           # the last closed bars since the previous run
                if j >= 4 and bc[j - 4][4] and bc[j][4] / bc[j - 4][4] - 1 >= K_BTC:
                    self._btc_jump = {"bar": bc[j][0], "ret": bc[j][4] / bc[j - 4][4] - 1}
            # the 5 slots go to the most traded alts first (24 h turnover from the 15m candles)
            turn = lambda c: sum(k[4] * k[5] for k in (c.get("candles") or [])[-97:-1])
            by_sym = dict(sorted(by_sym.items(), key=lambda kv: -turn(kv[1])))
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
        known = {r[0] for r in self.conn.execute(f"SELECT id FROM {self.T_ORD}")} | {r[0] for r in self.conn.execute(f"SELECT id FROM {self.T_TR}")} | self._archived_ids()
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
                if risk_off and ((s["side"] == "LONG" and s["grade"] not in ("B", "E", "F", "G", "D", "J", "K")) or self.P["riskoff"] == "none"):   # B = cascade fade, tested in crashes
                    events.append(f"пропуск {'лонга' if s['side'] == 'LONG' else 'шорта'} {base}: рынок падает широким фронтом (режим риска; на истории покупка дна со стопом −0.20R, шорт после падения −0.04R — не торгуем)")
                    continue
                if s["grade"] == "F" and sum(1 for t in self._open() if t["grade"] == "F") >= self.P.get("f_open", 3):
                    continue
                if s["grade"] in ("S", "G", "D", "X", "Y", "J", "T", "K") and sum(1 for t in self._open() if t["grade"] == s["grade"]) >= self.P.get(s["grade"].lower() + "_open", 0):
                    continue
                if s["grade"] not in SHARE_RULES and sum(1 for t in self._open() if t["grade"] not in SHARE_RULES) + len(self._pending()) >= self.P["max_open"]:
                    continue
                cap = self.P.get("caps", {}).get(s["grade"])
                if cap is not None and sum(1 for t in self._open() if t["grade"] == s["grade"]) + sum(1 for o in self._pending() if o["grade"] == s["grade"]) >= cap:
                    continue
                if s["grade"] == "E":
                    notional = eq * self.P.get("e_share", 0.2) / max(1, len(e_sigs))
                    qty = notional / s["entry"]
                    risk_usd = notional * E_STOP
                elif s["grade"] == "F":
                    notional = eq * self.P.get("f_share", 0.15)
                    qty = notional / s["entry"]
                    risk_usd = notional * F_STOP
                elif s["grade"] in ("S", "G", "D", "X", "Y", "J", "T", "K"):
                    if s["grade"] == "X" and any(t["grade"] == "X" and now - t["opened"] < E_BASKET_H * 3_600_000 and t["opened"] != now for t in self._open()):
                        continue   # one alt panic basket per 10 h
                    notional = eq * self.P.get(s["grade"].lower() + "_share", 0.1)
                    qty = notional / s["entry"]
                    risk_usd = (s["entry"] - s["stop"]) * qty if s["grade"] in ("T", "K") else notional * {"S": S_STOP, "G": G_STOP, "D": D_STOP, "X": E_STOP, "Y": D_STOP, "J": 0.20}[s["grade"]]
                else:
                    risk_usd = eq * self.P["risk"]
                    r = abs(s["entry"] - s["stop"])
                    qty = risk_usd / (r + s["entry"] * (2 * FEE + SLIP))
                    cap_qty = eq * self.P.get("max_notional", 3.0) / s["entry"]   # 17.10: tight stops must not mean 60 % of the account in one trade
                    if qty > cap_qty:
                        qty = cap_qty; risk_usd = qty * (r + s["entry"] * (2 * FEE + SLIP))
                if s["order"] == "MARKET":
                    fill = s["entry"] * (1 + SLIP)
                    self.conn.execute(f"INSERT INTO {self.T_TR} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (s["id"], base, s["side"], s["grade"], s["kind"], fill, s["stop"], s["stop"], json.dumps(s["tps"]), json.dumps(s["parts"]),
                                       qty, qty, risk_usd, now, now + s["hold_h"] * 3_600_000, None, None, 0.0, 0.0, 0, s["reason"], coin["candles"][-1][0]))
                    events.append(f"открыт {s['side']} {base} по рынку {fill:.6g} ({RULE_RU.get(s['grade'], s['grade'])})")
                else:
                    self.conn.execute(f"INSERT INTO {self.T_ORD} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (s["id"], base, s["side"], s["grade"], s["kind"], s["entry"], s["stop"], json.dumps(s["tps"]), json.dumps(s["parts"]),
                                       qty, risk_usd, now, now + s.get("ttl_h", ORDER_TTL_H) * 3_600_000, "PENDING", s["reason"]))
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
                thr = 0.0001 if o["grade"] in THROUGH else 0.0
                if (o["side"] == "LONG" and c[3] <= o["entry"] * (1 - thr)) or (o["side"] == "SHORT" and c[2] >= o["entry"] * (1 + thr)):
                    filled = c
                    break
            if filled:
                hold = {"C": HOLD_C_H, "B": HOLD_B_H, "R": HOLD_R_H, "E": HOLD_E_H, "F": HOLD_F_H, "S": HOLD_S_H, "G": HOLD_G_H, "D": HOLD_D_H, "L": HOLD_L_H, "N": HOLD_N_H, "Q": HOLD_N_H, "W": 48, "X": HOLD_E_H, "Y": HOLD_D_H, "J": 24, "T": HOLD_T_H, "K": HOLD_K_H}.get(o["grade"], HOLD_C_H)
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
        for g in ("A", "B", "R", "C", "E", "F", "S", "G", "D", "L", "N", "Q", "W", "X", "Y", "J", "T", "K"):
            ts = [t for t in closed if t["grade"] == g]
            if ts:
                by_grade[g] = {"n": len(ts), "winrate": round(len([t for t in ts if t["net_usd"] > 0]) / len(ts), 3), "avg_r": round(sum(t["pnl_r"] for t in ts) / len(ts), 3),
                               "usd": round(sum(t["net_usd"] for t in ts), 2)}
        eq = self.equity()
        reset = self.reset_info()
        return {"asof": now, "profile": self.profile, "name": self.P["name"], "about": self.P["about"], "start_equity": START_EQUITY,
                "since": reset["time"] if reset else (eqh[0]["time"] if eqh else now), "reset": reset, "risk_off":self.stress(list(by_sym.values())) >= 0.7, "stress": round(self.stress(list(by_sym.values())), 3), "equity": eq, "pnl_usd": round(eq - START_EQUITY, 2), "pnl_pct": round((eq / START_EQUITY - 1) * 100, 2),
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


# ── one-time reset (owner, 17.10.2026): «Фьючерсы, автопилот, норма — результат обнули: эти три шорта портят всю картину».
# The three main autopilot desks restart from $10 000 together; their whole past state is archived in `paper_archive`
# (one row per desk, id "<migration>:<profile>"), the migration id goes to `migrations`, so it runs exactly once per database.
# Lab / experiment desks are not touched.
RESET_ID = "reset-2026-10-17"
RESET_PROFILES = ("cons", "bal", "aggr")
RESET_DAY_RU = "17.10"
RESET_UNTIL = 1_793_491_200_000          # 2026-11-01 UTC: a database that meets this migration later is not wiped (marked «skipped»)
RULE_OFF_RU = {"R": "выключено 16.10"}


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _usd_ru(x: float) -> str:
    v = round(x)
    return ("−" if v < 0 else "+" if v > 0 else "") + f"${abs(v):,}".replace(",", " ")


def reset_note(trades: list[dict], pnl: float, classes: set) -> str:
    """«Счёт обнулён 17.10 по решению владельца: прошлые 3 сделки (3 шорта по правилу «ретест уровня», выключено 16.10) — в архиве, итог −$299»."""
    if not trades:
        return f"Счёт обнулён {RESET_DAY_RU} по решению владельца вместе с остальными автопилотами: сделок не было, итог $0 — старт с $10 000"
    groups = {}
    for t in trades:
        groups.setdefault(t["grade"], []).append(t)
    parts = []
    for g, ts in sorted(groups.items(), key=lambda kv: (kv[0] not in RULE_OFF_RU, -len(kv[1]), kv[0])):
        n, longs = len(ts), sum(1 for t in ts if t["side"] == "LONG")
        rule = f"по правилу «{RULE_RU.get(g, g)}»"
        if longs == n:
            s = f"{n} {_plural(n, 'лонг', 'лонга', 'лонгов')} {rule}"
        elif longs == 0:
            s = f"{n} {_plural(n, 'шорт', 'шорта', 'шортов')} {rule}"
        else:
            s = f"{longs} {_plural(longs, 'лонг', 'лонга', 'лонгов')} и {n - longs} {_plural(n - longs, 'шорт', 'шорта', 'шортов')} {rule}"
        if g in RULE_OFF_RU:
            s += f", {RULE_OFF_RU[g]}"
        elif g not in classes:
            s += ", сейчас не торгуется"
        parts.append(s)
    n = len(trades)
    return (f"Счёт обнулён {RESET_DAY_RU} по решению владельца: {_plural(n, 'прошлая', 'прошлые', 'прошлые')} {n} "
            f"{_plural(n, 'сделка', 'сделки', 'сделок')} ({'; '.join(parts)}) — в архиве, итог {_usd_ru(pnl)}")


def reset_desks(conn: sqlite3.Connection, now: int, prices: dict | None = None, mig_id: str = RESET_ID,
                profiles=RESET_PROFILES, until: int = RESET_UNTIL) -> dict:
    """Idempotent one-time reset → {profile: reset_info} for the desks reset by THIS call ({} when already applied).
    Open positions (none on 17.10) are closed at the last price (`prices` {BASE: px}, else at entry) into the archive,
    pending limits are cancelled; then trades/orders/equity are emptied and the curve restarts at $10 000 at `now`.
    A desk with no history at all (fresh database) is not «reset» — the migration is only marked as applied."""
    conn.execute("CREATE TABLE IF NOT EXISTS migrations(id TEXT PRIMARY KEY, time INTEGER, note TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS paper_archive(id TEXT PRIMARY KEY, profile TEXT, time INTEGER, json TEXT)")
    conn.commit()
    if conn.execute("SELECT 1 FROM migrations WHERE id=?", (mig_id,)).fetchone():
        return {}
    if now > until:
        conn.execute("INSERT INTO migrations VALUES(?,?,?)", (mig_id, now, "skipped: too late"))
        conn.commit()
        return {}
    desks = {k: PaperDesk(conn, k) for k in profiles}
    for d in desks.values():
        try:
            conn.execute(f"ALTER TABLE {d.T_EQ} ADD COLUMN mtm REAL")
        except sqlite3.OperationalError:
            pass
    prices = prices or {}
    tkeys = ("id", "symbol", "side", "grade", "kind", "entry", "stop", "stop_now", "tps", "parts", "qty", "qty_left", "risk_usd", "opened",
             "expires", "closed", "exit_kind", "pnl_usd", "pnl_r", "tps_hit", "reason", "last_bar")
    okeys = ("id", "symbol", "side", "grade", "kind", "entry", "stop", "tps", "parts", "qty", "risk_usd", "created", "expires", "status", "reason")
    done: dict = {}
    try:
        for k, d in desks.items():
            n_hist = sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in (d.T_TR, d.T_ORD, d.T_EQ))
            if not n_hist:
                continue
            equity_before = d.equity()
            closed_at_reset = []
            for t in d._open():                      # none on 17.10; if any — closed at the last price, not silently dropped
                sgn = 1 if t["side"] == "LONG" else -1
                last = prices.get(t["symbol"]) or t["entry"]
                px = last * (1 - sgn * SLIP)
                pnl = t["pnl_usd"] + sgn * (px - t["entry"]) * t["qty_left"] - px * t["qty_left"] * FEE
                pnl_r = (pnl - t["entry"] * t["qty"] * FEE) / t["risk_usd"] if t["risk_usd"] else 0.0
                conn.execute(f"UPDATE {d.T_TR} SET qty_left=0, pnl_usd=?, pnl_r=?, closed=?, exit_kind='RESET' WHERE id=?", (pnl, pnl_r, now, t["id"]))
                closed_at_reset.append({"id": t["id"], "symbol": t["symbol"], "side": t["side"], "price": last, "priced": t["symbol"] in prices})
            cancelled = [o["id"] for o in d._pending()]
            conn.execute(f"UPDATE {d.T_ORD} SET status='CANCELLED' WHERE status='PENDING'")
            trades = [dict(zip(tkeys, r)) for r in conn.execute(f"SELECT * FROM {d.T_TR} ORDER BY opened").fetchall()]
            net = {t["id"]: (t["pnl_usd"] or 0) - (t["entry"] or 0) * (t["qty"] or 0) * FEE for t in trades}
            for t in trades:
                t["net_usd"] = round(net[t["id"]], 2)
            orders = [dict(zip(okeys, r)) for r in conn.execute(f"SELECT * FROM {d.T_ORD} ORDER BY created").fetchall()]
            eqh = [{"time": a, "equity": b, "mtm": m} for a, b, m in conn.execute(f"SELECT time, equity, mtm FROM {d.T_EQ} ORDER BY time").fetchall()]
            closed = [t for t in trades if t["closed"] is not None]
            pnl = round(float(sum(net[t["id"]] for t in closed)), 2)       # = equity − $10 000, same formula as step()
            by_grade = {}
            for t in closed:
                g = by_grade.setdefault(t["grade"], {"n": 0, "usd": 0.0, "sum_r": 0.0})
                g["n"] += 1; g["usd"] = round(g["usd"] + t["net_usd"], 2); g["sum_r"] = round(g["sum_r"] + (t["pnl_r"] or 0), 3)
            peak, dd = START_EQUITY, 0.0
            for e in eqh:
                v = e["mtm"] if e["mtm"] is not None else e["equity"]
                peak = max(peak, v); dd = max(dd, (peak - v) / peak)
            note = reset_note(closed, pnl, d.P["classes"])
            info = {"time": now, "archived_pnl_usd": pnl, "note_ru": note, "archived_trades": len(closed), "migration": mig_id,
                    "since_before": eqh[0]["time"] if eqh else None}
            arch = {"profile": k, "name": d.P["name"], "migration": mig_id, "time": now, "start_equity": START_EQUITY,
                    "equity_before": equity_before, "equity_at_reset": round(START_EQUITY + pnl, 2), "reset": info,
                    "stats": {"trades": len(closed), "wins": sum(1 for t in closed if t["net_usd"] > 0), "usd": pnl,
                              "max_dd_pct": round(dd * 100, 2), "by_grade": by_grade},
                    "closed_at_reset": closed_at_reset, "cancelled_orders": cancelled,
                    "trades": trades, "orders": orders, "equity_history": eqh}
            conn.execute("INSERT INTO paper_archive VALUES(?,?,?,?)", (f"{mig_id}:{k}", k, now, json.dumps(arch, ensure_ascii=False)))
            for t in (d.T_TR, d.T_ORD, d.T_EQ):
                conn.execute(f"DELETE FROM {t}")
            conn.execute(f"INSERT INTO {d.T_EQ}(time, equity, mtm) VALUES(?,?,?)", (now, START_EQUITY, START_EQUITY))
            done[k] = info
        conn.execute("INSERT INTO migrations VALUES(?,?,?)", (mig_id, now, "desks reset: " + (", ".join(f"{k} {v['archived_pnl_usd']:+.2f}" for k, v in done.items()) or "none (no history)")))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return done


def run(conn: sqlite3.Connection, report: dict, now: int | None = None) -> dict:
    """Runs the three strategy accounts; the top level of the result is the default (balanced) account,
    `profiles` holds all three so the panel can switch between them."""
    btc = next((c for c in report["coins"] if c["symbol"].startswith("BTC")), None)
    btc_bull = bool(btc and btc.get("regime", {}).get("long") == "BULL")
    now = now or int(time.time() * 1000)
    just_reset = {}
    try:   # one-time reset of the three autopilot desks (owner's decision 17.10) — no-op once applied
        last_px = {c["symbol"].replace("-USDT", "").replace("USDT", ""): c["candles"][-1][4] for c in report["coins"] if c.get("candles")}
        just_reset = reset_desks(conn, now, last_px)
    except Exception as e:  # noqa: BLE001
        print("paper reset failed (will retry next run):", e)
    try:   # 17.10: «Лаборатория» is called «Тест новых идей» for people — stored reasons of the lab desk follow (idempotent)
        lab = PaperDesk(conn, "lab")
        for t in (lab.T_TR, lab.T_ORD):
            conn.execute(f"UPDATE {t} SET reason = 'Тест новых идей:' || substr(reason, 13) WHERE reason LIKE 'Лаборатория:%'")
        conn.commit()
    except Exception as e:  # noqa: BLE001
        print("lab rename:", e)
    try:   # rule F needs open-interest history: record this run, attach the 4-hour change to every coin
        from cnq.agents import oi_watch
        bases = [c["symbol"].replace("-USDT", "").replace("USDT", "") for c in report["coins"] if not c.get("b_only")]
        if not report.get("_no_network"):
            oi_watch.record(conn, bases, now)
        for c, b in zip([c for c in report["coins"] if not c.get("b_only")], bases):
            c["oi_chg4h"] = oi_watch.change(conn, b, now)
    except Exception:  # noqa: BLE001
        pass
    try:   # rules D (core) and Y (lab alts) need the perp-vs-index basis: record this run, attach the 1-hour average
        from cnq.agents import basis_watch
        bases = [c["symbol"].replace("-USDT", "").replace("USDT", "") for c in report["coins"]]
        if not report.get("_no_network"):
            basis_watch.record(conn, bases, now)
        for c, b in zip(report["coins"], bases):
            c["basis_1h"] = basis_watch.avg_1h(conn, b, now)
    except Exception:  # noqa: BLE001
        pass
    nikkei = None
    try:
        it = next(i for i in (report.get("macro") or {}).get("items", []) if i.get("key") == "NIKKEI")
        if it.get("chg_1d") is not None and it.get("asof"):
            nikkei = {"chg": float(it["chg_1d"]), "date": str(it["asof"])[:10]}
    except Exception:  # noqa: BLE001
        pass
    btc50 = report.get("btc_ema50d")       # class T: BTC vs its 50-day EMA (computed from the full 15m store in cli.py)
    snaps = {}
    for k in PROFILES:
        d = PaperDesk(conn, k); d._nikkei = nikkei; d._btc50 = btc50
        snaps[k] = d.step(report["coins"], btc_bull, now)
        if k in just_reset:
            snaps[k]["events"].insert(0, just_reset[k]["note_ru"])
    out_scan = scan(report["coins"])
    out = dict(snaps[DEFAULT_PROFILE])
    out["profiles"] = {k: {kk: vv for kk, vv in v.items() if kk != "equity_history"} | {"equity_history": v["equity_history"][-300:]} for k, v in snaps.items()}
    out["scan"] = out_scan
    out["profile_list"] = [{"key": k, "name": p["name"], "about": p["about"], "risk": p["risk"], "classes": sorted(p["classes"]), "max_open": p["max_open"]} for k, p in PROFILES.items()]
    return out
