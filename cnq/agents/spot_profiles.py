"""Two more model spot portfolios ($10 000 each) next to the moderate one in portfolio.py:

  cons  Консервативный — BTC 50 % / ETH 25 % / SOL 15 % / cash 10 %. No stops, monthly rebalance back to the weights.
  high  Высокий риск   — up to 10 × 5 % in memes / fresh Hyperliquid movers from the research cards (risk class "high",
                         rating ≥ 55, still moving: +7d), cash ≥ 50 %. Rules: stop −30 %; at ×2 sell half ("забрать тело");
                         after that every +50 % from the last fix sell half of the remainder; 35 % pullback from the
                         position high with profit → sell all; max hold 30 days; weekly refresh replaces the ones that stopped moving.

Deterministic, rule-based; prices come from the CoinGecko top-100 snapshot (+ Hyperliquid spot). Tables are prefixed per profile.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

PROFILES = {
    "cons": {"name": "Консервативный", "prefix": "spot_cons_", "core": {"BTC": 0.50, "ETH": 0.25, "SOL": 0.15}, "cash": 0.10,
             "about": "BTC 50 %, ETH 25 %, SOL 15 %, кэш 10 %. Без стопов — ядро держим через циклы, раз в месяц возвращаем доли."},
    "high": {"name": "Высокий риск", "prefix": "spot_high_", "core": {}, "cash": 0.50, "slots": 10, "slot_w": 0.05,
             "about": "До 10 мемов и свежих движений с Hyperliquid по 5 %, кэш ≥ 50 %. Стоп −30 %; при удвоении забираем тело, дальше каждые +50 % — половину остатка; держим не дольше 30 дней."},
}
DAY = 86400_000


class RiskPortfolio:
    def __init__(self, conn: sqlite3.Connection, profile: str):
        self.conn = conn
        self.key = profile
        self.P = PROFILES[profile]
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

    def positions(self) -> list[dict]:
        keys = ("symbol", "bucket", "weight", "entry", "entry_time", "high", "status", "reason", "fixes", "last_fix")
        return [dict(zip(keys, r)) for r in self.conn.execute(f"SELECT {','.join(keys)} FROM {self.T_POS} WHERE status='OPEN'")]

    def _act(self, now, sym, action, weight, price, reason, bucket=None, fixes=None, last_fix=None):
        """Cash ledger in units of the starting capital: a weight w bought at `entry` is worth w·px/entry now."""
        self.conn.execute(f"INSERT INTO {self.T_ACT} VALUES(?,?,?,?,?,?)", (now, sym, action, weight, price, reason))
        row = self.conn.execute(f"SELECT weight, entry, status FROM {self.T_POS} WHERE symbol=?", (sym,)).fetchone()
        w_before = row[0] if row and row[2] == "OPEN" else 0.0
        entry = row[1] if row and row[2] == "OPEN" else price
        cash = self.cash()
        if action in ("BUY", "ADD"):
            if row and row[2] == "OPEN" and weight > w_before:   # ADD: new entry = weighted average, cash pays the difference
                add = weight - w_before
                new_entry = (w_before * entry + add * price) / weight
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

    def step(self, price: dict, cards: list[dict], hl_spot: list[dict], now: int | None = None) -> dict:
        now = now or int(time.time() * 1000)
        pos = {p["symbol"]: p for p in self.positions()}
        actions = []
        last_act = self.conn.execute(f"SELECT MAX(time) FROM {self.T_ACT}").fetchone()[0] or 0
        d = datetime.fromtimestamp(now / 1000, tz=timezone.utc)
        if self.key == "cons":
            monthly = (d.day <= 3 and now - last_act > 20 * DAY) or not pos
            for sym, w in self.P["core"].items():
                if sym not in price:
                    continue
                p = pos.get(sym)
                if not p:
                    actions.append(self._act(now, sym, "BUY", w, price[sym], f"Ядро консервативного портфеля: {sym} {w*100:.0f}% — держим годами, без стопа.", "core"))
                elif monthly and abs(p["weight"] - w) > 0.05:
                    actions.append(self._act(now, sym, "ADD" if w > p["weight"] else "TRIM", w, price[sym], f"Месячный ребаланс: {sym} → {w*100:.0f}%.", "core"))
        else:
            weekly = (d.weekday() == 0 and now - last_act > 20 * DAY) or not pos
            by_sym = {c["symbol"]: c for c in cards}
            # --- manage open high-risk positions
            for sym, p in list(pos.items()):
                if sym not in price:
                    continue
                px = price[sym]; high = max(p["high"] or px, px)
                self.conn.execute(f"UPDATE {self.T_POS} SET high=? WHERE symbol=?", (high, sym))
                ret = px / p["entry"] - 1
                age = (now - p["entry_time"]) / DAY
                fixes = p["fixes"] or 0; last_fix = p["last_fix"] or p["entry"]
                c = by_sym.get(sym)
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
                elif weekly and c and (c["rating"] < 45 or (c.get("chg7") or 0) < -15):
                    actions.append(self._act(now, sym, "SELL", 0, px, f"Недельный пересмотр: рейтинг {c['rating']}, за неделю {c.get('chg7') or 0:+.0f}% — движение ушло, освобождаем место."))
            pos = {p["symbol"]: p for p in self.positions()}
            # --- fill slots: research high-risk cards that are moving + Hyperliquid spot movers
            if weekly:
                cands = [c for c in cards if c["risk"] == "high" and c["rating"] >= 55 and (c.get("chg7") or 0) > 0 and c["symbol"] in price and c["symbol"] not in pos]
                cands.sort(key=lambda c: -(c["rating"] + min(30, (c.get("chg7") or 0) / 2)))
                hl_m = [h for h in hl_spot if (h.get("chg24") or 0) > 0 and (h.get("vol24") or 0) >= 5e6 and h["symbol"] not in pos and h["symbol"] not in {c["symbol"] for c in cands}]
                invested = sum(p["weight"] for p in pos.values())
                for c in cands:
                    if len(pos) >= self.P["slots"] or invested + self.P["slot_w"] > 1 - self.P["cash"]:
                        break
                    actions.append(self._act(now, c["symbol"], "BUY", self.P["slot_w"], price[c["symbol"]],
                                             f"Высокий риск, рейтинг {c['rating']}/100, за неделю {c.get('chg7') or 0:+.0f}%{', Hyperliquid' if c.get('on_hl') else ''}: 5% на движение. Стоп −30%, тело забираем при удвоении.", "high"))
                    pos[c["symbol"]] = {"weight": self.P["slot_w"]}; invested += self.P["slot_w"]
                for h in hl_m[:3]:
                    if len(pos) >= self.P["slots"] or invested + self.P["slot_w"] > 1 - self.P["cash"]:
                        break
                    price[h["symbol"]] = h["price"]
                    actions.append(self._act(now, h["symbol"], "BUY", self.P["slot_w"], h["price"],
                                             f"Спот Hyperliquid: оборот ${h['vol24']/1e6:.0f}M за сутки, {h.get('chg24') or 0:+.0f}% — свежее движение, 5%. Стоп −30%, тело при удвоении.", "high"))
                    pos[h["symbol"]] = {"weight": self.P["slot_w"]}; invested += self.P["slot_w"]
        self.conn.commit()
        pos = self.positions()
        invested = sum(p["weight"] for p in pos)
        value = self.cash() + sum(p["weight"] * (price.get(p["symbol"], p["entry"]) / p["entry"]) for p in pos if p["entry"])
        idx = round(100 * value, 2)
        self.conn.execute(f"INSERT OR REPLACE INTO {self.T_VAL} VALUES(?,?)", (now, idx))
        self.conn.commit()
        hist = [{"time": t, "v": v} for t, v in self.conn.execute(f"SELECT time,index_value FROM {self.T_VAL} ORDER BY time DESC LIMIT 60").fetchall()][::-1]
        return {"key": self.key, "name": self.P["name"], "about": self.P["about"],
                "positions": [dict(p, price=price.get(p["symbol"]), pnl_pct=round((price.get(p["symbol"], p["entry"]) / p["entry"] - 1) * 100, 2) if p["entry"] and price.get(p["symbol"]) else None) for p in pos],
                "cash_pct": round(self.cash() / value * 100) if value else 0, "invested_pct": round((value - self.cash()) / value * 100) if value else 0, "index": idx, "history": hist, "actions": actions,
                "recent_actions": [dict(zip(("time", "symbol", "action", "weight", "price", "reason"), r)) for r in
                                   self.conn.execute(f"SELECT time,symbol,action,weight,price,reason FROM {self.T_ACT} ORDER BY time DESC LIMIT 20").fetchall()]}
