"""Model spot portfolios ($10 000 each) next to the moderate one in portfolio.py:

  cons  Консервативный — BTC 50 % / ETH 25 % / SOL 15 % / cash 10 %. No stops. Monthly (1st–3rd) check of the MARKET weights
                         (value now ÷ portfolio value — 17.10 fix: it compared cost weights before, so a coin that doubled never
                         looked overweight); if any coin is off its target by more than 5 p.p., all three go back to the targets.
  high  Высокий риск   — up to 10 × 5 %, cash ≥ 50 %. Non-memes: research cards of risk class "high" (rating ≥ 55, still moving:
                         +7d) and fresh Hyperliquid spot movers — memes are never taken through these two doors.
                         Memes (17.10, owner: «с учётом наших мемкоинов»): only when the spot meme status (meme_spot.status_of) is
                         «small» — safety checks passed AND the price ≥ 30 % below the 30-day high (the only entry our meme research
                         supported, docs/research/memes.md); 5 % per meme (never above 6 % of the portfolio), all memes together
                         ≤ 25 % of the portfolio; a meme whose status turns «не брать» is sold (red flag — at once, otherwise at the
                         weekly review); no re-buy of a coin sold in the last 30 days.
                         Exits for everything: stop −30 %; at ×2 sell half ("забрать тело"); after that every +50 % from the last fix
                         sell half of the remainder; 35 % pullback from the position high with profit → sell all; max hold 30 days;
                         weekly (Monday) refresh replaces the ones that stopped moving.

Deterministic, rule-based; prices come from the CoinGecko top snapshot (+ CoinGecko meme list, + Hyperliquid spot); a held coin that
left the snapshot keeps its last seen price (`px:SYM` in the meta table). Tables are prefixed per profile.

  ideas По рекомендациям (IdeasPortfolio below, 17.10, owner: «если мы рекомендуем, что взять на спот, то почему мы сами не
                         берём?») — an EXPERIMENT that buys what the spot ideas table (web/ideas.json, spot_ideas.py) says, to test the
                         table with money. It reads the PREVIOUS server run's ideas.json (the ideas step runs after the portfolio step);
                         missing or older than 3 h → no buys, holdings are kept, priced and only the price rules work.
                         Entry: «buy»/«add» 10 % each (≤ 8 coins), «small» 2.5 % each (≤ 4), table order, one buy per coin (no top-ups),
                         cash ≥ 10 % after the buy, fee 0.1 % per trade (the 10 % / 2.5 % include it). Price: a live price of the run
                         (CoinGecko top → meme list → Hyperliquid spot) within 25 % of the table's price (same coin, not a namesake);
                         the source is remembered per coin (meta `src:SYM`), no reliable price → the coin is skipped («SKIP» in actions).
                         Exits: table status «avoid» → sold on the next run with the table's reason; stop −20 % for buy/add coins except
                         core (BTC/ETH/SOL — the table's «ядро» — no stop); «small»: stop −35 %, at ×2 sell half (body back), the rest
                         trails 35 % from the peak; weekly review (Monday, once): table status «wait» and P&L ≤ −10 % → sold; a coin
                         sold in the last 14 days is not bought again. Statuses come from ideas.json `statuses` (every evaluated coin,
                         not only the 30 shown rows); spot_ideas.py does not count this portfolio's holdings (no feedback loop).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

PROFILES = {
    # 18.10 (owner: «всё больше верю в биткоин — самый надёжный актив»): BTC 60 / ETH 20 / SOL 10 (was 50 / 25 / 15). Test 2023-06 →
    # 2026-10 (scripts/lab, daily): held as is, BTC-heavier = smaller drawdown (−57 % vs −60 %) and better 2025 (−8 % vs −10 %) and
    # 2026 (−10 % vs −11 %); with the BTC-regime switch of Портфолио all mixes end close (+184…+201 %, drawdown −29…−30 %).
    "cons": {"name": "Консервативный", "prefix": "spot_cons_", "core": {"BTC": 0.60, "ETH": 0.20, "SOL": 0.10}, "cash": 0.10, "band": 0.05,
             "about": "BTC 60 %, ETH 20 %, SOL 10 %, кэш 10 %. Без стопов — ядро держим через циклы. Раз в месяц (1–3 числа) смотрим "
                      "рыночные доли: если какая-то ушла от плана больше чем на 5 п.п., возвращаем все доли к плану."},
    "high": {"name": "Высокий риск", "prefix": "spot_high_", "core": {}, "cash": 0.50, "slots": 10, "slot_w": 0.05,
             "meme_w": 0.05, "meme_max": 0.06, "meme_cap": 0.25, "cooldown_d": 30,
             "about": "До 10 позиций по 5 %, кэш ≥ 50 %. Альты высокого риска из анкеты и свежие движения спота Hyperliquid — без мемов. "
                      "Мемы — только со статусом «можно на небольшую сумму» из нашего исследования (проверки безопасности пройдены и цена "
                      "на 30 %+ ниже максимума за 30 дней): 5 % на мем, все мемы вместе не больше 25 % портфеля. Стоп −30 %; при удвоении "
                      "забираем тело, дальше каждые +50 % — половину остатка; откат 35 % от пика при прибыли — выход; держим не дольше 30 дней."},
}
DAY = 86400_000
HIST_ALL_MAX = 240


def value_history(conn: sqlite3.Connection, table: str, since: int | None = None, max_points: int = HIST_ALL_MAX) -> list[dict]:
    """22.10 (owner: «два числа спота не сходятся»): `history` keeps only the last 60 runs (≈ 10 h), but the panel drew it as «с начала
    ведения» and dated results by its first point. This is the whole value history from the start (`since`), thinned evenly to at
    most `max_points` points; the first and the last point are always kept, nothing is interpolated."""
    q = f"SELECT time,index_value FROM {table}" + (" WHERE time >= ?" if since else "") + " ORDER BY time"
    rows = conn.execute(q, (int(since),) if since else ()).fetchall()
    if len(rows) > max_points:
        step = (len(rows) - 1) / (max_points - 1)
        rows = [rows[round(i * step)] for i in range(max_points)]
    return [{"time": t, "v": v} for t, v in rows]


def _hl_alias(sym: str) -> set[str]:
    """Plausible underlying tickers of a Hyperliquid spot token: UPUMP / UZEC (Unit-bridged), ZEC1 / XMR1, ONEAR, FXMR …"""
    s = sym.upper()
    out = {s}
    if len(s) > 2 and s[0] in "UOFK":
        out.add(s[1:])
    if len(s) > 2 and s[-1].isdigit():
        out.add(s[:-1])
    return out


def _hl_wrapped(sym: str, price: dict) -> bool:
    """A bridged / wrapped copy of a coin we already price (USOL, UZEC …) or a stablecoin — not a «fresh mover»."""
    s = sym.upper()
    return s.startswith("USD") or any(a in price for a in _hl_alias(s) - {s})


class RiskPortfolio:
    def __init__(self, conn: sqlite3.Connection, profile: str, P: dict | None = None):
        self.conn = conn
        self.key = profile
        self.P = P or PROFILES[profile]
        px = self.P["prefix"]
        self.T_POS, self.T_ACT, self.T_VAL = px + "portfolio", px + "actions", px + "value"
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {self.T_POS}(symbol TEXT PRIMARY KEY, bucket TEXT, weight REAL, entry REAL, entry_time INTEGER,
                        high REAL, status TEXT, reason TEXT, fixes INTEGER DEFAULT 0, last_fix REAL)""")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {self.T_ACT}(time INTEGER, symbol TEXT, action TEXT, weight REAL, price REAL, reason TEXT)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {self.T_VAL}(time INTEGER PRIMARY KEY, index_value REAL)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {px}meta(k TEXT PRIMARY KEY, v REAL)")
        conn.commit()
        self.T_META = px + "meta"

    def cash(self) -> float:
        row = self.conn.execute(f"SELECT v FROM {self.T_META} WHERE k='cash'").fetchone()
        return row[0] if row else 1.0

    def _set_cash(self, v: float):
        self.conn.execute(f"INSERT OR REPLACE INTO {self.T_META} VALUES('cash',?)", (max(0.0, v),))

    def _meta(self, k: str):
        row = self.conn.execute(f"SELECT v FROM {self.T_META} WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, k: str, v: float):
        self.conn.execute(f"INSERT OR REPLACE INTO {self.T_META} VALUES(?,?)", (k, float(v)))

    def positions(self) -> list[dict]:
        keys = ("symbol", "bucket", "weight", "entry", "entry_time", "high", "status", "reason", "fixes", "last_fix")
        return [dict(zip(keys, r)) for r in self.conn.execute(f"SELECT {','.join(keys)} FROM {self.T_POS} WHERE status='OPEN'")]

    @staticmethod
    def _mv(p: dict, price: dict) -> float:
        """Market value of a position in units of the $10 000 start (weight is the cost share at entry)."""
        return p["weight"] * price.get(p["symbol"], p["entry"]) / p["entry"] if p.get("entry") else 0.0

    def value(self, price: dict) -> float:
        return self.cash() + sum(self._mv(p, price) for p in self.positions())

    def _act(self, now, sym, action, weight, price, reason, bucket=None, fixes=None, last_fix=None):
        """Cash ledger in units of the starting capital: a weight w bought at `entry` is worth w·px/entry now."""
        self.conn.execute(f"INSERT INTO {self.T_ACT} VALUES(?,?,?,?,?,?)", (now, sym, action, weight, price, reason))
        row = self.conn.execute(f"SELECT weight, entry, status FROM {self.T_POS} WHERE symbol=?", (sym,)).fetchone()
        w_before = row[0] if row and row[2] == "OPEN" else 0.0
        entry = row[1] if row and row[2] == "OPEN" else price
        cash = self.cash()
        if action in ("BUY", "ADD"):
            if row and row[2] == "OPEN" and weight > w_before:   # ADD: cash pays `add`; new entry keeps the coin amount exact
                add = weight - w_before
                # 17.10 fix: coins held = w/entry + add/price, so the entry is the HARMONIC weighted mean (the arithmetic mean
                # used before understated the position's value after an ADD at a lower price)
                new_entry = weight / (w_before / entry + add / price) if entry and price else price
                self._set_cash(cash - add)
                self.conn.execute(f"UPDATE {self.T_POS} SET weight=?, entry=?, reason=?, status='OPEN' WHERE symbol=?", (weight, new_entry, reason, sym))
            else:
                self._set_cash(cash - weight)
                self.conn.execute(f"INSERT OR REPLACE INTO {self.T_POS} VALUES(?,?,?,?,?,?,?,?,?,?)", (sym, bucket, weight, price, now, price, "OPEN", reason, 0, None))
        elif action == "TRIM":
            sold = max(0.0, w_before - weight)
            self._set_cash(cash + sold * (price / entry if entry else 1))
            self.conn.execute(f"UPDATE {self.T_POS} SET weight=?, reason=?, fixes=COALESCE(?,fixes), last_fix=COALESCE(?,last_fix) WHERE symbol=?", (weight, reason, fixes, last_fix, sym))
        elif action == "SELL":
            self._set_cash(cash + w_before * (price / entry if entry else 1))
            self.conn.execute(f"UPDATE {self.T_POS} SET status='CLOSED', weight=0, reason=? WHERE symbol=?", (reason, sym))
        return {"time": now, "symbol": sym, "action": action, "weight": round(weight, 3), "price": price, "reason": reason}

    def sold_recently(self, now: int, days: int) -> set[str]:
        return {s for (s,) in self.conn.execute(f"SELECT DISTINCT symbol FROM {self.T_ACT} WHERE action='SELL' AND time>=?", (now - days * DAY,))}

    # ------------------------------------------------------------------ conservative: monthly rebalance on MARKET weights
    def _step_cons(self, price: dict, pos: dict, now: int, d: datetime, last_act: int) -> list[dict]:
        actions = []
        core = self.P["core"]
        monthly = (d.day <= 3 and now - last_act > 20 * DAY) or not pos
        V = self.value(price)
        for sym, w in core.items():
            if sym in price and sym not in pos:
                actions.append(self._act(now, sym, "BUY", round(w * V, 6), price[sym], f"Ядро консервативного портфеля: {sym} {w*100:.0f}% — держим годами, без стопа.", "core"))
        if not monthly or not pos or V <= 0:
            return actions
        mw = {s: self._mv(p, price) / V for s, p in pos.items()}
        off = [s for s, w in core.items() if s in pos and s in price and abs(mw[s] - w) > self.P.get("band", 0.05)]
        if not off:
            return actions
        why = ", ".join(f"{s} {mw[s]*100:.0f}% при плане {core[s]*100:.0f}%" for s in off)
        tol = 0.002 * V
        for sym, w in core.items():                  # sells first: the cash from trims pays for the adds
            p = pos.get(sym)
            if p and sym in price and self._mv(p, price) - w * V > tol:
                actions.append(self._act(now, sym, "TRIM", round(p["weight"] * w * V / self._mv(p, price), 8), price[sym],
                                         f"Месячный ребаланс по рыночным долям ({why}): {sym} {mw[sym]*100:.0f}% → {w*100:.0f}%.", "core"))
        for p in self.positions():
            sym = p["symbol"]; w = core.get(sym)
            if w is None or sym not in price:
                continue
            gap = min(w * V - self._mv(p, price), self.cash())
            if gap > tol:
                actions.append(self._act(now, sym, "ADD", round(p["weight"] + gap, 8), price[sym],
                                         f"Месячный ребаланс по рыночным долям ({why}): {sym} {mw[sym]*100:.0f}% → {w*100:.0f}%.", "core"))
        return actions

    # ------------------------------------------------------------------ high risk: movers + memes by the meme research rules
    def _step_high(self, price: dict, src_px: dict, pos: dict, cards: list[dict], hl_spot: list[dict], now: int, d: datetime,
                   memes: set[str], mstat: dict) -> list[dict]:
        P = self.P
        actions = []
        last_weekly = self._meta("last_weekly") or 0
        weekly = (d.weekday() == 0 and now - last_weekly > 6 * DAY) or not pos
        by_sym = {c["symbol"]: c for c in cards}
        # --- manage open positions
        for sym, p in list(pos.items()):
            if sym not in price:
                continue
            px = price[sym]; high = max(p["high"] or px, px)
            self.conn.execute(f"UPDATE {self.T_POS} SET high=? WHERE symbol=?", (high, sym))
            ret = px / p["entry"] - 1
            age = (now - p["entry_time"]) / DAY
            fixes = p["fixes"] or 0; last_fix = p["last_fix"] or p["entry"]
            c = by_sym.get(sym)
            ms = mstat.get(sym) or {}
            if ret <= -0.30:
                actions.append(self._act(now, sym, "SELL", 0, px, f"Стоп −30%: {ret*100:+.0f}% от входа. В высоком риске убыток режем сразу."))
            elif fixes == 0 and ret >= 1.0:
                actions.append(self._act(now, sym, "TRIM", round(p["weight"] / 2, 4), px, f"Удвоение ({ret*100:+.0f}%): забираем тело — продаём половину, остаток едет бесплатно.", fixes=1, last_fix=px))
            elif fixes >= 1 and px >= last_fix * 1.5:
                actions.append(self._act(now, sym, "TRIM", round(p["weight"] / 2, 4), px, f"Ещё +50% от последней фиксации ({ret*100:+.0f}% от входа): продаём половину остатка.", fixes=fixes + 1, last_fix=px))
            elif px < high * 0.65 and ret > 0.3:
                actions.append(self._act(now, sym, "SELL", 0, px, f"Откат 35% от максимума при прибыли {ret*100:+.0f}% — движение закончилось, выходим полностью."))
            elif age > 30:
                actions.append(self._act(now, sym, "SELL", 0, px, f"30 дней прошло ({ret*100:+.0f}%): в высоком риске долго не сидим."))
            elif sym in memes and ms.get("status") == "avoid" and (weekly or str(ms.get("why_ru", "")).startswith("красный флаг")):
                actions.append(self._act(now, sym, "SELL", 0, px, f"Мем получил статус «не брать» ({ms.get('why_ru')}) — выходим ({ret*100:+.0f}%)."))
            elif weekly and c and ((c.get("rating") or 0) < 45 or (c.get("chg7") or 0) < -15) and sym not in memes:
                actions.append(self._act(now, sym, "SELL", 0, px, f"Недельный пересмотр: рейтинг {c.get('rating')}, за неделю {c.get('chg7') or 0:+.0f}% — движение ушло, освобождаем место."))
        pos = {p["symbol"]: p for p in self.positions()}
        V = self.value(price)
        min_cash = P["cash"] * V
        slot = P["slot_w"] * V

        def room(w: float) -> bool:
            return len(pos) < P["slots"] and self.cash() - w >= min_cash - 1e-9

        # --- weekly: research high-risk cards that are moving + Hyperliquid spot movers (never memes, never stablecoins)
        if weekly:
            self._set_meta("last_weekly", now)
            cands = [c for c in cards if c.get("risk") == "high" and (c.get("rating") or 0) >= 55 and (c.get("chg7") or 0) > 0 and c["symbol"] in price
                     and c["symbol"] not in pos and c["symbol"] not in memes and not c.get("stable")]
            cands.sort(key=lambda c: -(c["rating"] + min(30, (c.get("chg7") or 0) / 2)))
            hl_m = [h for h in hl_spot if (h.get("chg24") or 0) > 0 and (h.get("vol24") or 0) >= 5e6 and h["symbol"] not in pos and not _hl_alias(h["symbol"]) & memes
                    and not _hl_wrapped(h["symbol"], price) and h["symbol"] not in {c["symbol"] for c in cands} and h.get("price")]
            for c in cands:
                if not room(slot):
                    break
                actions.append(self._act(now, c["symbol"], "BUY", round(slot, 6), price[c["symbol"]],
                                         f"Высокий риск, рейтинг {c['rating']}/100, за неделю {c.get('chg7') or 0:+.0f}%{', Hyperliquid' if c.get('on_hl') else ''}: 5% на движение. Стоп −30%, тело забираем при удвоении.", "high"))
                pos[c["symbol"]] = {"weight": slot}
            for h in hl_m[:3]:
                if not room(slot):
                    break
                price[h["symbol"]] = src_px[h["symbol"]] = h["price"]
                actions.append(self._act(now, h["symbol"], "BUY", round(slot, 6), h["price"],
                                         f"Спот Hyperliquid: оборот ${h['vol24']/1e6:.0f}M за сутки, {h.get('chg24') or 0:+.0f}% — свежее движение, 5%. Стоп −30%, тело при удвоении.", "high"))
                pos[h["symbol"]] = {"weight": slot}
        # --- memes (every run — the dip status is a timing signal): status «small» only, 5 % each (≤ 6 %), all memes ≤ 25 %
        cool = self.sold_recently(now, P.get("cooldown_d", 30))
        meme_mv = sum(self._mv(p, price) for s, p in pos.items() if s in memes and "entry" in p)
        meme_mv += sum(p["weight"] for s, p in pos.items() if s in memes and "entry" not in p)
        w = min(P.get("meme_w", P["slot_w"]), P.get("meme_max", 0.06)) * V
        small = sorted([r for s, r in mstat.items() if r.get("status") == "small" and s not in pos and s not in cool and (price.get(s) or r.get("price"))],
                       key=lambda r: (-(r.get("score") or 0), r.get("from_high30_pct") or 0))
        for r in small:
            s = r["symbol"].upper()
            if meme_mv + w > P.get("meme_cap", 0.25) * V + 1e-9 or not room(w):
                break
            px = price.get(s) or r["price"]
            price[s] = src_px[s] = px
            actions.append(self._act(now, s, "BUY", round(w, 6), px,
                                     f"Мем со статусом «можно на небольшую сумму»: {r.get('why_ru') or 'проверки пройдены, просадка от 30%'} "
                                     f"(балл качества {r.get('score')}/100). {w / V * 100:.0f}% — все мемы вместе не больше "
                                     f"{P.get('meme_cap', 0.25)*100:.0f}%. Стоп −30%, на ×2 забираем тело, держим не дольше 30 дней.", "meme"))
            pos[s] = {"weight": w}; meme_mv += w
        return actions

    def step(self, price: dict, cards: list[dict], hl_spot: list[dict], now: int | None = None,
             memes: set[str] | None = None, meme_status: dict | None = None) -> dict:
        """`memes` — meme tickers (default: portfolio.meme_symbols(cards)); `meme_status` — {SYM: spot meme row with status /
        why_ru / score / price} from memes.json → spot_memes (high risk only; without it no meme is bought)."""
        now = now or int(time.time() * 1000)
        price = {k: v for k, v in price.items() if v}
        src_px = dict(price)                         # prices seen in this run's sources (the rest are last seen → price_live False)
        pos = {p["symbol"]: p for p in self.positions()}
        for s in pos:                                # a held coin that left the snapshot keeps its last seen price
            if s in price:
                self._set_meta(f"px:{s}", price[s])
            elif self._meta(f"px:{s}"):
                price[s] = self._meta(f"px:{s}")
        last_act = self.conn.execute(f"SELECT MAX(time) FROM {self.T_ACT}").fetchone()[0] or 0
        d = datetime.fromtimestamp(now / 1000, tz=timezone.utc)
        mstat = {k.upper(): v for k, v in (meme_status or {}).items()}
        if self.key == "cons":
            actions = self._step_cons(price, pos, now, d, last_act)
        else:
            if memes is None:
                from .portfolio import meme_symbols
                memes = meme_symbols(cards)
            memes = {m.upper() for m in memes} | set(mstat)
            actions = self._step_high(price, src_px, pos, cards, hl_spot, now, d, memes, mstat)
        self.conn.commit()
        pos = self.positions()
        for p in pos:
            if p["symbol"] in src_px:
                self._set_meta(f"px:{p['symbol']}", src_px[p["symbol"]])
        value = self.cash() + sum(self._mv(p, price) for p in pos)
        idx = round(100 * value, 2)
        self.conn.execute(f"INSERT OR REPLACE INTO {self.T_VAL} VALUES(?,?)", (now, idx))
        self.conn.commit()
        hist = [{"time": t, "v": v} for t, v in self.conn.execute(f"SELECT time,index_value FROM {self.T_VAL} ORDER BY time DESC LIMIT 60").fetchall()][::-1]
        out_pos = []
        for p in pos:
            s = p["symbol"]
            row = dict(p, price=price.get(s), price_live=s in src_px,
                       pnl_pct=round((price[s] / p["entry"] - 1) * 100, 2) if p["entry"] and price.get(s) else None)
            if s in mstat:
                row["meme_status"] = mstat[s].get("status"); row["meme_status_ru"] = mstat[s].get("status_ru")
            out_pos.append(row)
        return {"key": self.key, "name": self.P["name"], "about": self.P["about"],
                "positions": out_pos,
                "cash_pct": round(self.cash() / value * 100) if value else 0, "invested_pct": round((value - self.cash()) / value * 100) if value else 0, "index": idx, "history": hist,
                "history_all": value_history(self.conn, self.T_VAL),      # a reset empties the value table, so «all» = since the (re)start
                "actions": actions,
                "recent_actions": [dict(zip(("time", "symbol", "action", "weight", "price", "reason"), r)) for r in
                                   self.conn.execute(f"SELECT time,symbol,action,weight,price,reason FROM {self.T_ACT} ORDER BY time DESC LIMIT 20").fetchall()]}


# ----------------------------------------------------------------------------- 6th profile: «По рекомендациям» (follows web/ideas.json)
IDEAS = {
    "key": "ideas", "name": "CRYPTO NOVA портфель", "prefix": "spot_ideas_", "status": "наш",
    # 18.10 (owner: «наш портфель … обязательно эфириум, солана … и высокорисковые»; lab: BTC+ETH+SOL +239 % vs top-50 alts −57 %
    # 2023-03 → 2026-10): core BTC / ETH / SOL 15 % each (topped back up when the market moves one under 11 %), other
    # «можно брать / добрать» coins 7.5 % each, high-risk «на небольшую сумму» 2.5 % each, at most 3 → 45 + 37.5 + 7.5 = 90 % + cash 10 %
    "buy_w": 0.075, "buy_n": 8,           # table status «можно брать» / «можно добрать»: at most 8 coins (the core included)
    # 18.10 evening (owner: «всё больше верю в биткоин»): the core is BTC-heavy — BTC 30 / ETH 10 / SOL 5 (45 % as before); a core
    # coin is topped up when it drifts under ¾ of its plan and, at the Monday review, trimmed back when it grew past 1.5× its plan
    "core_w": {"BTC": 0.30, "ETH": 0.10, "SOL": 0.05}, "core_floor": 0.73, "core_trim": 1.5,
    "small_w": 0.025, "small_n": 3,       # table status «можно на небольшую сумму»: 2.5 % each, at most 3 coins
    # 22.10 (owner chose «Б», confirmed 14:28): coins from the owner's trust list bought as «небольшая сумма» (2.5 %, stop −35 %,
    # ×2 → half) even while the table says «ждём» because of the questionnaire or token dilution — only if the consultant,
    # overheating, ATH, trend, turnover and regime checks pass, the coin is not «не брать» and OKX lists it. Kept through the
    # Monday «ждём» review.
    "trusted_small": ("HYPE",),
    "min_cash": 0.10, "fee": 0.001,       # cash ≥ 10 % after a buy; 0.1 % of every traded notional (the 10 % / 2.5 % include it)
    "stop_buy": -0.20,                    # buy/add coins except core (the table's «ядро» BTC/ETH/SOL — no stop)
    "stop_small": -0.35, "body_x": 2.0, "trail": 0.35,   # «small»: stop, ×2 → sell half (body back), the rest trails 35 % from the peak
    "review_pnl": -0.10,                  # weekly (Monday) review: table «wait» and P&L ≤ −10 % → sold
    "cooldown_d": 14,                     # a sold coin is not bought again for 14 days
    "stale_h": 3,                         # ideas.json older than this → no buys
    "px_tol": 0.25,                       # a live price within 25 % of the table's (or the last seen) price = the same coin, not a namesake
    # 28.10 (owner's decisions on the data agent's proposals, real money):
    # 1) idle cash → core: spot cash above the 10 % minimum for 3 full days with no coin eligible to buy → the cash above 10 %
    #    goes to the core BTC / ETH / SOL in the plan proportions (30 / 10 / 5, like the 22.10 swap); a newly eligible coin is paid
    #    by selling that top-up first, then from cash. Logged «Кэш → ядро (3 дня без подходящих монет)» and the reverse.
    "idle_days": 3, "idle_min": 0.005,    # 3 full days; less than 0.5 % of the account above the floor is not «idle cash»
    # 2) another portfolio's stop («Наш портфель «Умеренный» у стопа — не добираем») means «не добирать» here: no buy, no sale;
    #    only the coin's own stop, its own take-profit rule and the Monday review sell. A coin the table sold («не брать») whose
    #    status is «можно брать / добрать» again inside the 14-day pause gets the pause lifted — at most once per pause.
}
IDEAS_ABOUT = ("Наш портфель CRYPTO NOVA: сам покупает то, что советует таблица «Что брать на спот». Старт $10 000. С 22.10 — только монеты со спот-парой на OKX: его повторяет реальный счёт CRYPTO NOVA. Ядро — BTC 30%, ETH 10%, "
               "SOL 5% (доливаем, если доля упала ниже ¾ плана; продаём лишнее, если выросла больше чем в 1.5 раза), остальные «можно брать» и «можно добрать» — по 7.5%, всего не больше 8 монет вместе с ядром; «можно на небольшую сумму» — по 2.5%, "
               "не больше 3; кэш не меньше 10%, комиссия 0.1% с каждой сделки. Продаём, если таблица поставила «не брать»; стоп −20% от "
               "входа (ядро BTC, ETH, SOL — без стопа); для небольшой суммы стоп −35%, на ×2 продаём половину, остаток — до отката 35% от "
               "пика; по понедельникам продаём монеты со статусом «ждём» и минусом 10% и больше. Проданную монету 14 дней не берём. "
               "С 28.10 (решение владельца): чужой стоп — «не добирать», а не продавать; кэш выше 10% три дня без подходящих монет — "
               "в ядро BTC / ETH / SOL. Результат не обещаем — может быть и минус.")
IDEAS_RULES = [
    "Что покупаем: строки таблицы «Что брать на спот» с прошлого прогона сервера (таблица считается сразу после портфелей). "
    "Ядро — BTC 30%, ETH 10%, SOL 5% счёта (биткоин — главный: в 2025–2026 он падал меньше ETH и SOL); остальные «можно брать» и "
    "«можно добрать» — по 7.5%; всего не больше 8 монет вместе с ядром; «можно на небольшую сумму» — по 2.5%, не больше 3. Порядок — "
    "как в таблице. Одна покупка на монету: не доливаем и не усредняем, кроме ядра — его доливаем до плана, если доля упала ниже ¾ "
    "плана, а по понедельникам продаём лишнее, если доля выросла больше чем в 1.5 раза от плана.",
    "Докупка ядра на просадке (с 20.10): раз в сутки, если BTC, ETH или SOL на 10% ниже средней цены покупки, в таблице «можно брать» "
    "или «можно добрать» и перспективы в порядке (оценка консультанта от 60 или цена выше 200-дневной средней) — докупаем треть "
    "плановой доли. Не больше 2 раз за 30 дней, доля не выше 1.5 плана, кэш не меньше 10%. Альты на просадке не докупаем: на "
    "истории 2023–2026 это дало +17% против +39% без докупок и просадку −68% против −58%.",
    "Кэш не меньше 10%: покупку, после которой кэша стало бы меньше, пропускаем. Комиссия 0.1% с каждой покупки и продажи "
    "вычитается из счёта (10% и 2.5% — вместе с комиссией).",
    "Цена: живая цена сервера (CoinGecko, список мемов, спот Hyperliquid), и она отличается от цены в таблице не больше чем на 25% — "
    "иначе это может быть другой токен с тем же тикером. Нет надёжной цены — монету не покупаем и пишем об этом в действиях.",
    "Продажа по таблице: монета получила «не брать» — продаём на следующем прогоне, причина берётся из таблицы.",
    "Стоп −20% от входа для «можно брать / добрать». Ядро (BTC, ETH, SOL) держим без стопа.",
    "Небольшая сумма (мемы и высокий риск): стоп −35%; на ×2 продаём половину — вложенное вернулось, остаток держим до отката "
    "35% от пика.",
    "Раз в неделю, в понедельник: если у монеты в таблице «ждём» и позиция в минусе на 10% и больше — продаём.",
    "Проданную монету не покупаем снова 14 дней. Исключение (решение владельца 28.10): монету продали по «не брать» таблицы, а таблица "
    "снова пишет «можно брать / добрать» — пауза снимается (не чаще раза за паузу).",
    "Чужой стоп (решение владельца 28.10): если «не брать» пришло из-за другого нашего портфеля у стопа, это значит «не добирать» — "
    "не покупаем, но и не продаём; продают только свой стоп −20%, свои правила фиксации и понедельничный пересмотр.",
    "Кэш → ядро (решение владельца 28.10): если кэш выше 10% три полных дня подряд и ни одна монета не проходит правила покупки, "
    "кэш сверх 10% идёт в ядро BTC / ETH / SOL в пропорции плана 30 / 10 / 5. Когда появляется подходящая монета, её покупку "
    "оплачиваем сначала продажей этой доливки ядра, потом из кэша.",
    "Если таблица не обновлялась больше 3 часов (или её нет) — новых покупок нет, позиции держим и оцениваем; работают только "
    "ценовые правила (стопы, ×2, трейлинг).",
    "Это проверка таблицы на учебном счёте, а не совет и не прогноз: результат может оказаться минусом.",
]
# 18.10 (owner: «на спот краткосрочные, среднесрочные и долгосрочные стратегии — каких целей мы ожидаем, на какой период»).
# Every holding belongs to one horizon by its role; the rules above ARE the horizon's plan. Next to it — the market's base rate for
# that class and period (scripts/lab/horizons.py → horizons.json: what the AVERAGE coin of the class did, 2023-06 → 2026-10, by the
# BTC regime). It is a yardstick, not a forecast: our picks have to beat it — that is the target.
HORIZONS = {
    "long": {"name_ru": "Долгосрочная", "period_ru": "6–12 месяцев", "days": (180, 365), "who_ru": "ядро: BTC, ETH, SOL",
             "plan_ru": "Держим через циклы, без стопа: BTC 30%, ETH 10%, SOL 5%; доливаем до плана, если доля упала ниже ¾ плана, лишнее продаём по понедельникам. Сколько всего денег в споте, решает "
                        "Портфолио по режиму BTC: выше 200-дневной средней — больше, ниже — меньше."},
    "mid": {"name_ru": "Среднесрочная", "period_ru": "1–3 месяца", "days": (30, 90), "who_ru": "крупные альты «можно брать / добрать»",
            "plan_ru": "Стоп −20% от входа; продаём, если таблица поставила «не брать»; по понедельникам продаём «ждём» с минусом 10% и больше."},
    "short": {"name_ru": "Краткосрочная", "period_ru": "2–4 недели", "days": (14, 30), "who_ru": "небольшие суммы: мемы и высокий риск",
              "plan_ru": "Стоп −35%; на ×2 продаём половину — вложенное вернулось; остаток держим до отката 35% от пика."},
}


def _load_hz() -> dict:
    try:
        return json.load(open(os.path.join(os.path.dirname(__file__), "horizons.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


HZ = _load_hz()


def core_target(sym: str) -> float:
    """The plan weight of a core coin in the CRYPTO NOVA портфель (BTC 30 % / ETH 10 % / SOL 5 %; another core coin 10 %)."""
    cw = IDEAS["core_w"]
    return cw.get(sym, 0.10) if isinstance(cw, dict) else float(cw)


def horizon_of(kind: str, est: str) -> str:
    return "long" if kind == "core" else "short" if est == "small" else "mid"


def horizon_base(h: str, btc_bull) -> dict | None:
    """The market's yardstick for horizon h in the current BTC regime (unknown regime → «рост»)."""
    st, cls = HZ.get("stats") or {}, (HZ.get("classes") or {}).get(h) or {}
    reg = "off" if btc_bull is False else "on"
    a, rc = st.get(f"{h}_{reg}_all"), st.get(f"{h}_{reg}_recent")
    if not a or not cls:
        return None
    days = cls["days"]
    def sg(x: float) -> str:
        return f"{x:+.0f}%".replace("-", "−")
    ru = (f"Ориентир рынка — средняя монета этого класса в 2023–2026 при {'росте BTC' if reg == 'on' else 'BTC ниже 200-дневной средней'}: "
          f"за {days} дней в половине случаев от {sg(a['p25'])} до {sg(a['p75'])}, середина {sg(a['p50'])}, в плюсе {a['pos']:.0f}% случаев")
    if rc:
        ru += f"; в 2025–2026 середина {sg(rc['p50'])}, в плюсе {rc['pos']:.0f}%"
    return {"days": days, "regime": reg, "all": a, "recent": rc, "ru": ru + ". Наша цель — быть лучше этого ориентира."}


IDEAS_SRC = ("cg", "meme", "hl")          # price sources of portfolio.run in priority order
IDEAS_SRC_RU = {"cg": "CoinGecko", "meme": "список мемов", "hl": "спот Hyperliquid", "ideas": "цена из таблицы",
                "last": "последняя известная цена"}
_SRC_CODE = {"cg": 1.0, "meme": 2.0, "hl": 3.0}      # meta `src:SYM` (REAL column)
IDEAS_ROLE_RU = {"buy": "из таблицы: можно брать", "add": "из таблицы: можно добрать", "small": "на небольшую сумму"}
KIND_RU = {"core": "ядро", "mid": "крупный альт", "high": "высокий риск", "meme": "мем"}


def _pc(x: float) -> str:
    v = round(x * 100)
    return "0%" if v == 0 else f"{v:+d}%".replace("-", "−")


def _fp(x) -> str:
    return f"{x:.4g}" if x else "—"


def _posnum(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def load_ideas(path: str = "web/ideas.json") -> dict | None:
    """web/ideas.json as the PREVIOUS server run published it (the ideas step runs after the portfolio step); None if missing."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def ideas_freshness(d: dict | None, now: int) -> tuple[bool, str | None]:
    """(fresh, note_ru): fresh = the table exists, has rows and is at most IDEAS['stale_h'] hours old."""
    if not d or not d.get("asof"):
        return False, "Таблицы «Что брать на спот» нет — новых покупок нет, позиции держим и оцениваем по ценовым правилам."
    age_h = (now - int(d["asof"])) / 3_600_000
    if age_h > IDEAS["stale_h"]:
        return False, (f"Таблица «Что брать на спот» не обновлялась {age_h:.1f} ч — новых покупок нет, позиции держим и оцениваем; "
                       "работают только ценовые правила (стопы, ×2, трейлинг).")
    if not d.get("available", True) or not d.get("rows"):
        return False, "Таблица «Что брать на спот» пустая — новых покупок нет, позиции держим и оцениваем по ценовым правилам."
    return True, None


def ideas_statuses(d: dict | None) -> dict:
    """{SYM: {status, status_ru, why_ru, kind, price}} for every coin the table evaluated (`statuses`, 17.10) — the shown rows win."""
    out = {str(s).upper(): v for s, v in ((d or {}).get("statuses") or {}).items() if isinstance(v, dict)}
    for r in (d or {}).get("rows") or []:
        if r.get("symbol"):
            out[r["symbol"].upper()] = r
    return out


_CROSS_STOP = re.compile(r"^Наш портфель «[^»]+» у стопа")


def cross_stop(row: dict | None) -> bool:
    """28.10: the table's «не брать» came from ANOTHER of our portfolios sitting at its stop (spot_ideas dkey «stop») — for the
    real-money model that means «не добирать» (no buy, no sale). `decided` is written since 28.10; the reason text for older tables."""
    r = row or {}
    return r.get("status") == "avoid" and (r.get("decided") == "stop" or bool(_CROSS_STOP.match(str(r.get("why_ru") or ""))))


def ideas_reco(kind: str, est: str, p: dict, px: float | None, row: dict | None, fresh: bool) -> dict:
    """Server recommendation {k: hold|add|trim|sell, t, why} for one position of «По рекомендациям»."""
    P = IDEAS
    if not px or not p.get("entry"):
        return {"k": "hold", "t": "Нет цены", "why": "Свежей цены нет — показываем последнюю известную."}
    ret = px / p["entry"] - 1
    st = (row or {}).get("status")
    tab = (f"в таблице «{row['status_ru']}»" if row and row.get("status_ru") else "монеты сейчас нет в таблице") if fresh \
        else "таблица устарела — работают только ценовые правила"
    if fresh and st == "avoid" and cross_stop(row):
        return {"k": "hold", "t": "Не добирать", "why": f"Таблица: {row.get('why_ru') or 'чужой стоп'} — это «не добирать», не продажа "
                "(решение владельца 28.10); продаст только свой стоп, своя фиксация или понедельничный пересмотр."}
    if fresh and st == "avoid":
        return {"k": "sell", "t": "Продать — «не брать»", "why": f"Таблица: {row.get('why_ru') or 'не брать'}."}
    if est == "small":
        if not p.get("fixes"):
            if ret <= P["stop_small"] + 0.07:
                return {"k": "sell", "t": "У стопа −35%", "why": f"Сейчас {_pc(ret)} от входа; на −35% продаём целиком."}
            if px >= p["entry"] * (P["body_x"] - 0.15):
                return {"k": "trim", "t": "Скоро ×2", "why": f"Сейчас {_pc(ret)}: на ×2 продаём половину — вложенное вернётся."}
        else:
            dd = 1 - px / max(p.get("high") or px, px)
            if dd >= P["trail"] - 0.07:
                return {"k": "sell", "t": "У трейлинга", "why": f"{dd*100:.0f}% ниже пика; на откате 35% остаток продаём."}
            return {"k": "hold", "t": "Едет бесплатно", "why": f"Тело забрано; остаток держим до отката 35% от пика; {tab}."}
    elif kind != "core" and ret <= P["stop_buy"] + 0.05:
        return {"k": "sell", "t": "У стопа −20%", "why": f"Сейчас {_pc(ret)} от входа; на −20% продаём."}
    if fresh and st == "wait" and ret <= P["review_pnl"]:
        return {"k": "sell", "t": "Продать в понедельник",
                "why": f"В таблице «ждём» ({row.get('why_ru') or '—'}), позиция {_pc(ret)} — на недельном пересмотре продаём."}
    rule = ("Небольшая сумма: стоп −35%, на ×2 забираем тело" if est == "small"
            else "Ядро без стопа: продаём, только если таблица скажет «не брать» или на пересмотре («ждём» и −10%)" if kind == "core"
            else "Стоп −20% от входа, продаём и при «не брать»")
    if fresh and st == "add":
        rule += "; таблица пишет «можно добрать», но портфель не доливает — одна покупка на монету"
    return {"k": "hold", "t": "Держать", "why": f"{rule}; {tab}."}


class IdeasPortfolio:
    """«По рекомендациям»: buys what web/ideas.json says (rules in IDEAS / IDEAS_RULES and the module docstring). Ledger =
    RiskPortfolio (tables spot_ideas_*, cash and weights in units of the $10 000 start); bucket column = "<kind>:<entry status>"."""

    def __init__(self, conn: sqlite3.Connection):
        self.rp = RiskPortfolio(conn, IDEAS["key"], {**IDEAS, "about": IDEAS_ABOUT})
        self.conn = conn

    @staticmethod
    def _split(bucket: str | None) -> tuple[str, str]:
        kind, _, est = (bucket or "high:buy").partition(":")
        return kind or "high", est or "buy"

    @staticmethod
    def _mv(p: dict, px: float | None) -> float:
        return p["weight"] * (px or p["entry"]) / p["entry"] if p.get("entry") else 0.0

    def value(self, price: dict) -> float:
        return self.rp.cash() + sum(self._mv(p, price.get(p["symbol"])) for p in self.rp.positions())

    def _price_held(self, s: str, p: dict, src: dict, smap: dict) -> tuple[float, str, bool]:
        """(price, source, live): the coin's own source first; another live source or the table's price only within px_tol of the
        last seen price (a namesake token in another list is not taken for ours); otherwise the last seen price (not live)."""
        rp, tol = self.rp, IDEAS["px_tol"]
        last = rp._meta(f"px:{s}") or p["entry"]
        code = rp._meta(f"src:{s}")
        own = next((k for k, c in _SRC_CODE.items() if c == code), None)
        for k in ([own] if own else []) + [k for k in IDEAS_SRC if k != own]:
            v = src[k].get(s)
            if v and (k == own or abs(v / last - 1) <= tol):
                if k != own:
                    rp._set_meta(f"src:{s}", _SRC_CODE[k])
                return v, k, True
        v = (smap.get(s) or {}).get("price")
        if _posnum(v) and abs(v / last - 1) <= tol:
            return float(v), "ideas", False
        return last, "last", False

    @staticmethod
    def _entry_price(s: str, row: dict, src: dict) -> tuple[float | None, str | None, str | None]:
        """(price, source, why_not): a live price of this run that agrees with the table's price within px_tol."""
        ref = row.get("price") if _posnum(row.get("price")) else None
        seen = []
        for k in IDEAS_SRC:
            v = src[k].get(s)
            if not v:
                continue
            if ref and abs(v / ref - 1) <= IDEAS["px_tol"]:
                return v, k, None
            seen.append((k, v))
        if not ref:
            return None, None, "в таблице нет цены — не с чем сверить живую цену"
        if seen:
            k, v = seen[0]
            return None, None, (f"цена {IDEAS_SRC_RU[k]} {_fp(v)} расходится с ценой таблицы {_fp(ref)} больше чем на "
                                f"{IDEAS['px_tol']*100:.0f}% — возможно, другой токен с тем же тикером")
        return None, None, "нет живой цены ни в CoinGecko, ни в списке мемов, ни на споте Hyperliquid — позицию потом нечем оценивать"

    def _sell(self, now: int, s: str, p: dict, px: float, reason: str, actions: list):
        mv = self._mv(p, px)
        actions.append(self.rp._act(now, s, "SELL", 0, px, reason))
        self.rp._set_cash(self.rp.cash() - mv * IDEAS["fee"])

    def _trim_half(self, now: int, s: str, p: dict, px: float, reason: str, actions: list):
        half = p["weight"] / 2
        actions.append(self.rp._act(now, s, "TRIM", half, px, reason, fixes=(p["fixes"] or 0) + 1, last_fix=px))
        self.rp._set_cash(self.rp.cash() - half * px / p["entry"] * IDEAS["fee"])

    def step(self, sources: dict, ideas: dict | None, now: int | None = None, sma200: dict | None = None,
             tradeable: set | None = None) -> dict:
        """`sources` — this run's live prices {"cg": {SYM: px} (CoinGecko top), "meme": {…} (CoinGecko meme list + spot meme rows),
        "hl": {…} (Hyperliquid spot)}; `ideas` — web/ideas.json of the previous run (load_ideas()); `sma200` (20.10) —
        {BTC/ETH/SOL: 200-day average} for the D10 core top-up (portfolio.sma200_map). `tradeable` (22.10, owner) — coins with an
        OKX spot pair: the owner's real OKX account repeats this portfolio, so a coin OKX does not list is not bought, and a held one
        is sold (None — the list is unknown this run: nothing is filtered)."""
        okx = {str(c).upper() for c in tradeable} if tradeable else None
        now = now or int(time.time() * 1000)
        P, rp = IDEAS, self.rp
        src = {k: {str(s).upper(): float(v) for s, v in ((sources or {}).get(k) or {}).items() if _posnum(v)} for k in IDEAS_SRC}
        fresh, note = ideas_freshness(ideas, now)
        smap = ideas_statuses(ideas) if fresh else {}
        rows = ((ideas or {}).get("rows") or []) if fresh else []
        if rp._meta("started") is None:
            rp._set_meta("started", now)
        actions: list[dict] = []
        # --- 1. price the holdings (own source → another agreeing source → the table's price → last seen)
        price, live, psrc = {}, set(), {}
        for p in rp.positions():
            s = p["symbol"]
            px, k, is_live = self._price_held(s, p, src, smap)
            price[s], psrc[s] = px, k
            if is_live:
                live.add(s)
                rp._set_meta(f"px:{s}", px)
        # --- 2. exits: table «avoid» (fresh table only), price rules (always), weekly review (Monday, fresh table only)
        d = datetime.fromtimestamp(now / 1000, tz=timezone.utc)
        weekly = fresh and d.weekday() == 0 and now - (rp._meta("last_weekly") or 0) > 6 * DAY
        if weekly:
            rp._set_meta("last_weekly", now)
        for p in rp.positions():
            s = p["symbol"]
            px = price.get(s)
            if not px or not p["entry"]:
                continue
            kind, est = self._split(p["bucket"])
            high = max(p["high"] or px, px)
            self.conn.execute(f"UPDATE {rp.T_POS} SET high=? WHERE symbol=?", (high, s))
            ret = px / p["entry"] - 1
            fixes = p["fixes"] or 0
            r = smap.get(s) or {}
            st = r.get("status")
            res = f"{_pc(ret)} от входа" + ("" if s in live else f", цена — {IDEAS_SRC_RU[psrc[s]]}")
            if okx is not None and s not in okx and kind != "core":
                rp._set_meta("okx_swap", (rp._meta("okx_swap") or 0.0) + self._mv(p, px))
                self._sell(now, s, p, px, f"Правило с 22.10: портфель держит только монеты со спот-парой на OKX — его повторяет реальный "
                           f"счёт CRYPTO NOVA. {s} на споте OKX нет — продаём, итог {res}; деньги — в ядро, пока таблица не "
                           "предложит монету с OKX.", actions)
            elif st == "avoid" and not cross_stop(r):
                self._sell(now, s, p, px, f"Таблица поставила «не брать»: {r.get('why_ru') or 'проверки не пройдены'}. Продаём, итог {res}.", actions)
            elif est == "small" and not fixes and ret <= P["stop_small"]:
                self._sell(now, s, p, px, f"Стоп {_pc(P['stop_small'])}: {s} {res} — небольшую сумму режем целиком.", actions)
            elif est == "small" and not fixes and px >= p["entry"] * P["body_x"]:
                self._trim_half(now, s, p, px, f"×{P['body_x']:.0f} от входа ({res}): продаём половину — вложенное вернулось. "
                                f"Остаток держим до отката {P['trail']*100:.0f}% от пика.", actions)
            elif est == "small" and fixes and px <= high * (1 - P["trail"]):
                self._sell(now, s, p, px, f"Трейлинг: {s} на {(1 - px / high) * 100:.0f}% ниже пика {_fp(high)} — остаток продаём ({res}).", actions)
            elif est != "small" and kind != "core" and ret <= P["stop_buy"]:
                self._sell(now, s, p, px, f"Стоп {_pc(P['stop_buy'])}: {s} {res}. Убыток режем; {P['cooldown_d']} дней монету не берём.", actions)
            elif weekly and (st == "wait" or cross_stop(r)) and ret <= P["review_pnl"] and s not in P["trusted_small"]:
                self._sell(now, s, p, px, f"Недельный пересмотр: в таблице «ждём» ({r.get('why_ru') or '—'}), позиция {res} — "
                           "освобождаем место.", actions)
        # --- 3. buys: table order, buy/add 10 % (≤ 8), small 2.5 % (≤ 4), cash ≥ 10 %, 14-day cooldown, a reliable live price
        not_bought: list[dict] = []
        if fresh:
            self._idle_housekeeping()
            eligible_seen = False                 # 28.10: a coin passed every rule of the portfolio this run (bought or cash-blocked)
            held = {p["symbol"]: p for p in rp.positions()}
            n_buy = sum(1 for p in held.values() if self._split(p["bucket"])[1] != "small")
            n_small = len(held) - n_buy
            V = self.value(price)
            cool = rp.sold_recently(now, P["cooldown_d"])
            for r in rows:
                st, s = r.get("status"), (r.get("symbol") or "").upper()
                trusted = (st == "wait" and s in P["trusted_small"]
                           and not any(c.get("ok") is False for c in (r.get("checks") or [])
                                       if c.get("key") in ("consultant", "overheat", "ath", "trend200", "vol", "regime")))
                if (st not in ("buy", "add", "small") and not trusted) or not s or s in held:
                    continue
                small = st == "small" or trusted
                w = P["small_w"] if small else core_target(s) if (r.get("kind") == "core") else P["buy_w"]
                st_ru = r.get("status_ru") or st
                px = k = why_not = None
                lifted = None
                if okx is not None and s not in okx:
                    why_not = "на споте OKX нет пары — портфель повторяет реальный счёт CRYPTO NOVA на OKX"
                elif s in cool and not (lifted := self._cooldown_lift(now, s, st)):
                    t = self.conn.execute(f"SELECT MAX(time) FROM {rp.T_ACT} WHERE symbol=? AND action='SELL'", (s,)).fetchone()[0] or now
                    dd = lambda x: datetime.fromtimestamp(x / 1000, tz=timezone.utc).strftime("%d.%m")   # noqa: E731
                    why_not = f"продана {dd(t)} — снова не раньше {dd(t + P['cooldown_d'] * DAY)}"
                elif (n_small >= P["small_n"]) if small else (n_buy >= P["buy_n"]):
                    n = P["small_n"] if small else P["buy_n"]
                    why_not = f"все {n} {'места' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'мест'} для «{st_ru}» заняты"
                elif rp.cash() - max(0.0, w * V - self._idle_value(price, live)) < P["min_cash"] * V - 1e-9:
                    why_not = f"кэш не опускаем ниже {P['min_cash']*100:.0f}%"
                    eligible_seen = True          # 28.10: an eligible coin blocked only by the cash floor — not «idle cash»
                else:
                    px, k, why_not = self._entry_price(s, r, src)
                    if px is None:
                        actions.append({"time": now, "symbol": s, "action": "SKIP", "weight": 0, "price": r.get("price"),
                                        "reason": f"Не покупаем {s} («{st_ru}»): {why_not}."})
                if px is None:
                    not_bought.append({"symbol": s, "status": st, "why_ru": why_not})
                    continue
                kind = r.get("kind") or "high"
                eligible_seen = True
                # 28.10 (owner): the idle-cash top-up of the core pays for a newly eligible coin first, then the cash
                self._idle_release(now, w * V / (1 - P["fee"]), price, live, actions, s)   # the sale's own fee included
                if lifted:
                    rp._set_meta(f"cool_lift:{s}", now)
                cost = w * V / (1 + P["fee"])
                rule = (f"стоп {_pc(P['stop_small'])}, на ×{P['body_x']:.0f} продаём половину, остаток — до отката "
                        f"{P['trail']*100:.0f}% от пика" if small else "ядро — без стопа" if kind == "core" else f"стоп {_pc(P['stop_buy'])} от входа")
                head = (f"Исключение владельца (22.10): {s} из списка доверия — на небольшую сумму, хотя таблица пишет «{r.get('why_ru') or 'ждём'}»"
                        if trusted else f"Из таблицы «{st_ru}»: {r.get('why_ru') or '—'}")
                actions.append(rp._act(now, s, "BUY", cost, px, f"{head}. {w*100:g}% счёта, {rule}; "
                                       "продаём и при статусе «не брать»." + (f" {lifted}" if lifted else ""), f"{kind}:{'small' if trusted else st}"))
                rp._set_cash(rp.cash() - cost * P["fee"])
                rp._set_meta(f"src:{s}", _SRC_CODE[k])
                rp._set_meta(f"px:{s}", px)
                price[s], psrc[s] = px, k
                live.add(s)
                held[s] = {"symbol": s}
                n_small, n_buy = (n_small + 1, n_buy) if small else (n_small, n_buy + 1)
            # 19.10 (owner: «сделки почти все в минус … нужна понятная модель»): the portfolio had drifted ALT-heavy — 5 alts bought at
            # 10 % each before the 7.5 % plan, BTC 13 % of its 30 % plan, no free cash to top it up. At the Monday review (and once on
            # the first run of this rule) a non-core «можно брать» coin above 1.2× its plan is trimmed back to the plan; the cash goes
            # to the core top-up just below (lab 2023–2026: BTC+ETH+SOL +239 % vs a broad alt basket −57 %; BTC fell least in 2025–26)
            once = not rp._meta("plan_trim_v1")
            if weekly or once:
                rp._set_meta("plan_trim_v1", now)
                V = self.value(price)
                for p in rp.positions():
                    s = p["symbol"]
                    kind, est = self._split(p["bucket"])
                    if kind == "core" or est == "small" or s not in price or not V or not p["entry"]:
                        continue
                    mw = p["weight"] * price[s] / p["entry"] / V
                    if mw > P["buy_w"] * 1.2:
                        neww = P["buy_w"] * V * p["entry"] / price[s]
                        actions.append(rp._act(now, s, "TRIM", neww, price[s],
                                               f"План: {s} {mw*100:.1f}% счёта — больше плановых {P['buy_w']*100:.1f}%; лишнее продаём, "
                                               "деньги идут в ядро — BTC главная монета портфеля.", None))
                        rp._set_cash(rp.cash() - (p["weight"] - neww) * price[s] / p["entry"] * P["fee"])
            # core: trimmed back to plan at the Monday review when it grew past 1.5× the plan; topped up when under ¾ of it.
            # 20.10: while a D10 top-up is active (topup_extra:SYM) the plan is raised by it, so the trim goes back to the raised plan
            self._topup_housekeeping(price, (ideas or {}).get("btc_bull"))
            V = self.value(price)
            for p in rp.positions():
                s = p["symbol"]
                kind, _ = self._split(p["bucket"])
                if kind != "core" or s not in price or not V or not p["entry"]:
                    continue
                T = core_target(s)
                mw = p["weight"] * price[s] / p["entry"] / V
                ish = (rp._meta(f"idle_q:{s}") or 0.0) * price[s] / V      # 28.10: the idle-cash top-up is not «лишнее» — never trimmed back
                if weekly and mw > T * P["core_trim"] + ish:
                    te = min(1.5 * T, T + (rp._meta(f"topup_extra:{s}") or 0.0)) + ish
                    neww = te * V * p["entry"] / price[s]
                    actions.append(rp._act(now, s, "TRIM", neww, price[s],
                                           f"Ядро: {s} {mw*100:.0f}% счёта — больше плана {T*100:.0f}% в {mw / T:.1f} раза; лишнее продаём "
                                           "(недельный пересмотр).", None))
                    rp._set_cash(rp.cash() - (p["weight"] - neww) * price[s] / p["entry"] * P["fee"])
                    continue
                st = (smap.get(s) or {}).get("status")
                if st not in ("buy", "add") or mw >= T * P["core_floor"]:
                    continue
                add = min((T - mw) * V, rp.cash() - P["min_cash"] * V) / (1 + P["fee"])
                if add < 0.005:                       # less than 0.5 % of the account free above the cash floor → wait
                    continue
                actions.append(rp._act(now, s, "ADD", p["weight"] + add, price[s],
                                       f"Ядро: {s} {mw*100:.0f}% счёта — доливаем до {T*100:.0f}% (ядро держим крупным, BTC — больше всех: "
                                       "в 2025–2026 он падал меньше ETH и SOL; на 2023–2026 BTC+ETH+SOL +239%, широкая корзина альтов −57%).", p["bucket"]))
                rp._set_cash(rp.cash() - add * P["fee"])
            # 20.10 D10 — «умное усреднение» ядра (portfolio.TOPUP, docs/research/spot_averaging.md): once a UTC day a core coin
            # (BTC / ETH / SOL) 10 % below its average entry, table «можно брать / добрать», prospects OK (consultant mid ≥ 60 or price
            # above its 200-day average) gets + 1/3 of its plan; ≤ 2 per 30 days, ≤ 1.5 × plan, cash ≥ 10 %. Alts are NEVER averaged
            # down here («одна покупка на монету»): on 2023–2026 averaging alts gave +17 % vs +39 %, drawdown −68 % vs −58 %.
            actions += self._topups(now, price, smap, sma200 or {}, live)
            actions += self._okx_swap(now, price, okx, src)
            actions += self._idle_cash(now, price, live, eligible_seen)
        self.conn.commit()
        return self.snapshot(price, live, psrc, smap, fresh, note, actions, not_bought, now, ideas)

    def _okx_swap(self, now: int, price: dict, okx: set | None, src: dict | None = None) -> list[dict]:
        """22.10 (owner: «если монеты нет на бирже — заменить тем, что приносит деньги»): the money of a coin sold because OKX has no
        spot pair for it does not wait in cash — it goes to the core BTC / ETH / SOL in the plan's proportion (30 / 10 / 5), above the
        10 % cash floor, after this run's table buys (a coin the table offers and OKX lists comes first). Once, for the sales made
        before this step existed: the cash above the 15 % it held before the rule."""
        rp, P = self.rp, IDEAS
        if okx is None:
            return []
        V = self.value(price)
        if not V:
            return []
        if rp._meta("okx_swap_v1") is None:
            rp._set_meta("okx_swap_v1", now)
            sold = self.conn.execute(f"SELECT COUNT(*) FROM {rp.T_ACT} WHERE action='SELL' AND reason LIKE '%спот-парой на OKX%'").fetchone()[0]
            if sold and not rp._meta("okx_swap"):
                rp._set_meta("okx_swap", max(0.0, rp.cash() - 0.15 * V))
        left = rp._meta("okx_swap") or 0.0
        room = rp.cash() - P["min_cash"] * V
        amt = min(left, room)
        rp._set_meta("okx_swap", 0.0)       # what the table's buys took this run is already replaced; nothing waits for later runs
        if amt < 0.005 * V:
            return []
        core = {c: core_target(c) for c in ("BTC", "ETH", "SOL")}
        tot = sum(core.values())
        held = {p["symbol"]: p for p in rp.positions()}
        out = []
        used = 0.0
        for c, t in core.items():
            px = price.get(c) or ((src or {}).get("cg") or {}).get(c)
            if not px:
                continue
            add = amt * t / tot / (1 + P["fee"])
            p = held.get(c)
            why = (f"Замена: монет без спот-пары на OKX в портфеле больше нет — их деньги в ядро по плану BTC 30 / ETH 10 / SOL 5, "
                   f"{c} +{add / V * 100:.1f}% счёта. Монету с OKX из таблицы портфель купит по своим правилам.")
            if p and p.get("entry"):
                out.append(rp._act(now, c, "ADD", p["weight"] + add, px, why, p["bucket"]))
            else:
                out.append(rp._act(now, c, "BUY", add, px, why, "core:buy"))
                rp._set_meta(f"px:{c}", px)
            rp._set_cash(rp.cash() - add * P["fee"])
            used += add * (1 + P["fee"])
        return out

    # ── 28.10 (owner): idle cash → core, and the way back ────────────────────────────────────────────────────────────
    IDLE_CORE = ("BTC", "ETH", "SOL")

    def _idle_housekeeping(self) -> None:
        """the idle top-up of a core coin (meta idle_q:SYM, coins in ledger units: Σ added cost ÷ price) never exceeds what is held;
        a core coin sold by its own rules takes its top-up with it"""
        rp = self.rp
        held = {p["symbol"]: p for p in rp.positions()}
        for c in self.IDLE_CORE:
            q = rp._meta(f"idle_q:{c}")
            if not q:
                continue
            p = held.get(c)
            qh = p["weight"] / p["entry"] if p and p.get("entry") else 0.0
            if qh <= 0:
                self.conn.execute(f"DELETE FROM {rp.T_META} WHERE k=?", (f"idle_q:{c}",))
            elif q > qh:
                rp._set_meta(f"idle_q:{c}", qh)

    def _idle_value(self, price: dict, live: set | None = None) -> float:
        """the idle top-up of the core at today's prices (units of the $10 000 start); with `live` only what can be sold this run"""
        rp = self.rp
        return sum((rp._meta(f"idle_q:{c}") or 0.0) * (price.get(c) or 0.0) for c in self.IDLE_CORE if live is None or c in live)

    def _idle_release(self, now: int, need: float, price: dict, live: set, actions: list, for_sym: str) -> float:
        """sell up to `need` (units) of the idle top-up — from each core coin in proportion to its part — before a buy"""
        rp, P = self.rp, IDEAS
        parts = {c: (rp._meta(f"idle_q:{c}") or 0.0) * price[c] for c in self.IDLE_CORE
                 if (rp._meta(f"idle_q:{c}") or 0.0) > 0 and price.get(c) and c in live}
        tot = sum(parts.values())
        S = min(need, tot)
        if S <= 0:
            return 0.0
        V = self.value(price) or 1.0
        held = {p["symbol"]: p for p in rp.positions()}
        done = 0.0
        for c, v in parts.items():
            p = held.get(c)
            if not p or not p.get("entry"):
                continue
            sc = S * v / tot
            neww = max(0.0, p["weight"] - sc * p["entry"] / price[c])
            actions.append(rp._act(now, c, "TRIM", neww, price[c],
                                   f"Ядро → кэш (обратно, для покупки {for_sym}): продаём часть доливки «кэш → ядро (3 дня без подходящих "
                                   f"монет)» — {c} −{sc / V * 100:.1f}% счёта (решение владельца 28.10).", None))
            rp._set_cash(rp.cash() - sc * P["fee"])
            rp._set_meta(f"idle_q:{c}", max(0.0, (rp._meta(f"idle_q:{c}") or 0.0) - sc / price[c]))
            done += sc
        return done

    def _idle_cash(self, now: int, price: dict, live: set, eligible_seen: bool) -> list[dict]:
        """the 3-day clock (meta idle_since) and the move of the cash above the floor into the core in the plan proportions"""
        rp, P = self.rp, IDEAS
        V = self.value(price)
        if not V:
            return []
        excess = rp.cash() - P["min_cash"] * V
        since = rp._meta("idle_since") or 0.0
        if eligible_seen or excess < P["idle_min"] * V:
            if since:
                rp._set_meta("idle_since", 0.0)
            return []
        if not since:
            rp._set_meta("idle_since", now)       # the clock starts at the first run that sees idle cash (no back-dating)
            return []
        if now - since < P["idle_days"] * DAY or any(c not in live or not price.get(c) for c in self.IDLE_CORE):
            return []
        core = {c: core_target(c) for c in self.IDLE_CORE}
        tot = sum(core.values())
        held = {p["symbol"]: p for p in rp.positions()}
        out = []
        days = (now - since) / DAY
        for c, t in core.items():
            add = excess * t / tot / (1 + P["fee"])
            px = price[c]
            why = (f"Кэш → ядро (3 дня без подходящих монет): кэш выше {P['min_cash'] * 100:.0f}% уже {days:.1f} дн., ни одна монета не "
                   f"проходит правила покупки — {c} +{add / V * 100:.1f}% счёта по плану BTC 30 / ETH 10 / SOL 5 (решение владельца 28.10). "
                   "Подходящую монету портфель оплатит сначала этой доливкой.")
            p = held.get(c)
            if p and p.get("entry"):
                out.append(rp._act(now, c, "ADD", p["weight"] + add, px, why, p["bucket"]))
            else:
                out.append(rp._act(now, c, "BUY", add, px, why, "core:buy"))
                rp._set_meta(f"px:{c}", px)
            rp._set_cash(rp.cash() - add * P["fee"])
            rp._set_meta(f"idle_q:{c}", (rp._meta(f"idle_q:{c}") or 0.0) + add / px)
        rp._set_meta("idle_since", 0.0)
        return out

    def _cooldown_lift(self, now: int, s: str, st: str | None) -> str | None:
        """28.10 (owner): the 14-day pause after a TABLE sale («Таблица поставила «не брать»») is lifted when the table says «можно
        брать / добрать» again — at most once per pause. Returns the line for the buy's reason, or None (the pause stays)."""
        rp, P = self.rp, IDEAS
        if st not in ("buy", "add"):
            return None
        row = self.conn.execute(f"SELECT time, reason FROM {rp.T_ACT} WHERE symbol=? AND action='SELL' ORDER BY time DESC LIMIT 1",
                                (s,)).fetchone()
        if not row or not str(row[1] or "").startswith("Таблица поставила «не брать»"):
            return None
        if (rp._meta(f"cool_lift:{s}") or 0.0) >= row[0]:
            return None                           # already lifted once after this sale
        d = datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).strftime("%d.%m")
        return (f"14-дневная пауза снята: монету продали {d} по «не брать» таблицы, а таблица снова пишет «можно брать» "
                "(решение владельца 28.10).")

    def _topup_housekeeping(self, price: dict, btc_bull) -> None:
        """The raised plan of a topped-up core coin ends when its price is back at the average entry or the BTC regime flips."""
        rp = self.rp
        flip = False
        if btc_bull is not None:
            b = 1.0 if btc_bull else 0.0
            flip = rp._meta("topup_bull") not in (None, b)
            rp._set_meta("topup_bull", b)
        held = {p["symbol"]: p for p in rp.positions()}
        for (k,) in self.conn.execute(f"SELECT k FROM {rp.T_META} WHERE k LIKE 'topup_extra:%'").fetchall():
            p = held.get(k.split(":", 1)[1])
            if flip or not p or not p["entry"] or (price.get(p["symbol"]) or 0) >= p["entry"]:
                self.conn.execute(f"DELETE FROM {rp.T_META} WHERE k=?", (k,))

    def _topups(self, now: int, price: dict, smap: dict, sma200: dict, live: set) -> list[dict]:
        """D10 for the core (BTC / ETH / SOL) — only on a live price of this run, never on a last-seen one."""
        from .portfolio import DAY_MS, TOPUP, topup_decision
        rp, P = self.rp, IDEAS
        day = float(now // DAY_MS)
        if rp._meta("topup_day") == day:
            return []
        rp._set_meta("topup_day", day)
        out = []
        for p in rp.positions():
            s = p["symbol"]
            kind, _ = self._split(p["bucket"])
            row = smap.get(s) or {}
            if (kind != "core" or s not in TOPUP["coins"] or s not in price or s not in live or not p["entry"]
                    or row.get("status") not in ("buy", "add")):
                continue
            V = self.value(price)
            n = self.conn.execute(f"SELECT COUNT(*) FROM {rp.T_ACT} WHERE symbol=? AND action='ADD' AND reason LIKE ? AND time>=?",
                                  (s, TOPUP["prefix"] + "%", now - TOPUP["window_d"] * DAY_MS)).fetchone()[0]
            dec = topup_decision(s, price[s], p["entry"], core_target(s), self._mv(p, price[s]), V, rp.cash(), n,
                                 (row.get("consultant") or {}).get("mid"), sma200.get(s), fee=P["fee"])
            if dec["add"] <= 0:
                continue
            out.append(rp._act(now, s, "ADD", p["weight"] + dec["add"], price[s], dec["reason"], p["bucket"]))
            rp._set_cash(rp.cash() - dec["add"] * P["fee"])
            rp._set_meta(f"topup_extra:{s}", (rp._meta(f"topup_extra:{s}") or 0.0) + dec["add"] / V)
        return out

    def snapshot(self, price, live, psrc, smap, fresh, note, actions, not_bought, now, ideas) -> dict:
        rp, P = self.rp, IDEAS
        V = self.value(price) or 1.0
        idx = round(100 * V, 2)
        self.conn.execute(f"INSERT OR REPLACE INTO {rp.T_VAL} VALUES(?,?)", (now, idx))
        self.conn.commit()
        hist = [{"time": t, "v": v} for t, v in self.conn.execute(f"SELECT time,index_value FROM {rp.T_VAL} ORDER BY time DESC LIMIT 60").fetchall()][::-1]
        names = {(r.get("symbol") or "").upper(): r.get("name") for r in (ideas or {}).get("rows") or []}
        out, sl = [], {"buy": 0.0, "small": 0.0}
        for p in rp.positions():
            s = p["symbol"]
            kind, est = self._split(p["bucket"])
            px = price.get(s)
            mv = self._mv(p, px)
            sl["small" if est == "small" else "buy"] += mv / V
            r = smap.get(s)
            ret = px / p["entry"] - 1 if px and p["entry"] else None
            h = horizon_of(kind, est)
            held = (now - p["entry_time"]) / DAY if p.get("entry_time") else None
            hz = {"horizon": h, "horizon_ru": f"{HORIZONS[h]['name_ru']} · {HORIZONS[h]['period_ru']}", "horizon_days": list(HORIZONS[h]["days"]),
                  "held_days": round(held, 1) if held is not None else None,
                  "horizon_over": bool(held is not None and held > HORIZONS[h]["days"][1]),
                  "levels": {"stop": round(p["entry"] * (1 + (P["stop_small"] if h == "short" else P["stop_buy"])), 8) if h != "long" and p.get("entry") else None,
                             "take_half": round(p["entry"] * P["body_x"], 8) if h == "short" and p.get("entry") and not p.get("fixes") else None}}
            out.append({**p, **hz, "bucket": kind, "kind_ru": KIND_RU.get(kind, kind), "role": est, "role_ru": IDEAS_ROLE_RU.get(est, est),
                        "name": names.get(s) or s, "weight": round(mv / V, 4), "cost_weight": round(p["weight"], 6),
                        "target_weight": P["small_w"] if est == "small" else core_target(s) if kind == "core" else P["buy_w"],
                        "price": px, "price_live": s in live, "price_src": psrc.get(s),
                        "pnl_pct": round(ret * 100, 2) if ret is not None else None,
                        "pnl_usd": round(10_000 * (mv - p["weight"]), 2), "value_usd": round(10_000 * mv, 2),
                        "table_status": (r or {}).get("status"), "table_status_ru": (r or {}).get("status_ru"),
                        "table_why_ru": (r or {}).get("why_ru"), "reco": ideas_reco(kind, est, p, px, r, fresh)})
        cash = rp.cash() / V
        started, last_weekly = rp._meta("started"), rp._meta("last_weekly")
        bull = (ideas or {}).get("btc_bull")
        horizons = {h: {**{k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()},
                        "share_pct": round(sum(x["weight"] for x in out if x["horizon"] == h) * 100, 1),
                        "n": sum(1 for x in out if x["horizon"] == h),
                        "pnl_usd": round(sum(x["pnl_usd"] for x in out if x["horizon"] == h), 2),
                        "base": horizon_base(h, bull)} for h, d in HORIZONS.items()}
        return {"key": P["key"], "name": P["name"], "about": IDEAS_ABOUT, "status": P["status"], "experiment": False, "ours": True,
                "positions": out, "cash_pct": round(cash * 100), "invested_pct": round((1 - cash) * 100), "index": idx,
                "equity_usd": round(10_000 * V, 2), "start_usd": 10_000, "history": hist, "actions": actions,
                "history_all": value_history(self.conn, rp.T_VAL, int(started) if started else None),
                "recent_actions": [dict(zip(("time", "symbol", "action", "weight", "price", "reason"), r)) for r in
                                   self.conn.execute(f"SELECT time,symbol,action,weight,price,reason FROM {rp.T_ACT} ORDER BY time DESC LIMIT 20").fetchall()],
                "sleeves": {"buy": {"label": "ядро и «можно брать / добрать»", "target_pct": round((sum(P["core_w"].values()) + (P["buy_n"] - 3) * P["buy_w"]) * 100, 1), "now_pct": round(sl["buy"] * 100, 1)},
                            "small": {"label": "на небольшую сумму", "target_pct": round(P["small_n"] * P["small_w"] * 100, 1), "now_pct": round(sl["small"] * 100, 1)},
                            "cash": {"label": "кэш", "target_pct": round(P["min_cash"] * 100, 1), "now_pct": round(cash * 100, 1)}},
                "rules_ru": IDEAS_RULES, "horizons": horizons,
                "ideas": {"asof": (ideas or {}).get("asof"), "fresh": fresh, "note_ru": note,
                          "summary_ru": (ideas or {}).get("summary_ru") if fresh else None},
                "not_bought": not_bought,
                "idle_cash": self._idle_info(price, V, now),
                "started": int(started) if started else None, "last_weekly": int(last_weekly) if last_weekly else None}

    def _idle_info(self, price: dict, V: float, now: int) -> dict:
        """28.10: the idle-cash rule's state for the panel — since when the cash is idle, when the move is due, the core top-up now"""
        rp, P = self.rp, IDEAS
        since = rp._meta("idle_since") or 0.0
        extra = {c: round((rp._meta(f"idle_q:{c}") or 0.0) * (price.get(c) or 0.0) / V * 100, 2) for c in self.IDLE_CORE
                 if rp._meta(f"idle_q:{c}")}
        return {"since": int(since) if since else None, "due": int(since + P["idle_days"] * DAY) if since else None,
                "core_extra_pct": extra, "core_extra_total_pct": round(sum(extra.values()), 2),
                "rule_ru": "Кэш выше 10% три полных дня без подходящих монет → кэш сверх 10% в ядро BTC / ETH / SOL (30 / 10 / 5); "
                           "новая подходящая монета оплачивается сначала этой доливкой (решение владельца 28.10)."}
