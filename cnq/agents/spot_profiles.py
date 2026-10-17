"""Two more model spot portfolios ($10 000 each) next to the moderate one in portfolio.py:

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
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

PROFILES = {
    "cons": {"name": "Консервативный", "prefix": "spot_cons_", "core": {"BTC": 0.50, "ETH": 0.25, "SOL": 0.15}, "cash": 0.10, "band": 0.05,
             "about": "BTC 50 %, ETH 25 %, SOL 15 %, кэш 10 %. Без стопов — ядро держим через циклы. Раз в месяц (1–3 числа) смотрим "
                      "рыночные доли: если какая-то ушла от плана больше чем на 5 п.п., возвращаем все доли к плану."},
    "high": {"name": "Высокий риск", "prefix": "spot_high_", "core": {}, "cash": 0.50, "slots": 10, "slot_w": 0.05,
             "meme_w": 0.05, "meme_max": 0.06, "meme_cap": 0.25, "cooldown_d": 30,
             "about": "До 10 позиций по 5 %, кэш ≥ 50 %. Альты высокого риска из анкеты и свежие движения спота Hyperliquid — без мемов. "
                      "Мемы — только со статусом «можно на небольшую сумму» из нашего исследования (проверки безопасности пройдены и цена "
                      "на 30 %+ ниже максимума за 30 дней): 5 % на мем, все мемы вместе не больше 25 % портфеля. Стоп −30 %; при удвоении "
                      "забираем тело, дальше каждые +50 % — половину остатка; откат 35 % от пика при прибыли — выход; держим не дольше 30 дней."},
}
DAY = 86400_000


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
                "cash_pct": round(self.cash() / value * 100) if value else 0, "invested_pct": round((value - self.cash()) / value * 100) if value else 0, "index": idx, "history": hist, "actions": actions,
                "recent_actions": [dict(zip(("time", "symbol", "action", "weight", "price", "reason"), r)) for r in
                                   self.conn.execute(f"SELECT time,symbol,action,weight,price,reason FROM {self.T_ACT} ORDER BY time DESC LIMIT 20").fetchall()]}
