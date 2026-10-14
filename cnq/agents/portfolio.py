"""Spot portfolio department — a deterministic analyst/consultant for three horizons.

Sources (no keys): CoinGecko /coins/markets (top-150: cap, volume, 7d/30d/200d/1y change, ATH drawdown),
Hyperliquid info API (spot tokens + perp universe: 24h volume, funding, open interest, prev-day price).
Rules only — the LLM (editor) writes the daily note strictly from this JSON and never invents numbers.

Horizons
  short  1–4 weeks   cross-sectional momentum (7d/30d rank) + volume expansion; long only in BTC bull regime
  mid    1–6 months  30d & 200d trend, drawdown not blown, liquidity
  long   6–24 months size/liquidity/age proxies, 1y trend, Hyperliquid listing as a plus (user focus)

Model portfolio (state in data/agents.sqlite, table spot_portfolio)
  core   BTC 35 % / ETH 15 % (bear: 25 / 10)
  mid    up to 5 × 8 % from mid+long picks
  short  up to 3 × 5 % from short picks, bull only
  cash   the rest (never below 10 %)
Actions: buy / add / trim (+40 % → sell half) / sell (−15 % from entry or score lost) / hold / wait,
rebalance once a week (Monday) or when a rule fires. Everything is explained in Russian for the panel.
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

CG = "https://api.coingecko.com/api/v3"
HL = "https://api.hyperliquid.xyz/info"
STABLE = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "USDS", "PYUSD", "USD1", "BUSD", "USDD", "FRAX", "LUSD", "GUSD", "USDP", "EURC", "USDL", "RLUSD"}
WRAPPED = {"WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "RETH", "WBETH", "LBTC", "SOLVBTC", "TBTC", "BSC-USD", "CBETH", "EZETH", "RSETH", "METH", "SUSDE", "SUSDS", "BUIDL", "XAUT", "PAXG", "LEO", "BGB", "OKB", "GT", "KCS", "CRO", "WHYPE"}
HORIZONS = ("short", "mid", "long")
CORE = {"BTC": 0.35, "ETH": 0.15}
CORE_BEAR = {"BTC": 0.25, "ETH": 0.10}


def _get(url: str, data: dict | None = None, timeout: float = 25):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cnq/1.0", "Content-Type": "application/json"},
                                     data=json.dumps(data).encode() if data is not None else None)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------------- data
def coingecko_markets(pages: int = 2) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        rows = _get(f"{CG}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page={page}"
                    "&price_change_percentage=24h,7d,30d,200d,1y&sparkline=false")
        if not rows:
            break
        for r in rows:
            sym = (r.get("symbol") or "").upper()
            out.append({"symbol": sym, "name": r.get("name"), "id": r.get("id"), "price": r.get("current_price"),
                        "mcap": r.get("market_cap"), "rank": r.get("market_cap_rank"), "volume": r.get("total_volume"),
                        "chg24": r.get("price_change_percentage_24h_in_currency"), "chg7": r.get("price_change_percentage_7d_in_currency"),
                        "chg30": r.get("price_change_percentage_30d_in_currency"), "chg200": r.get("price_change_percentage_200d_in_currency"),
                        "chg1y": r.get("price_change_percentage_1y_in_currency"), "ath_chg": r.get("ath_change_percentage"),
                        "ath_date": r.get("ath_date"), "atl_date": r.get("atl_date")})
        time.sleep(1.5)
    return out


def hyperliquid_universe() -> dict:
    """Perp universe (coin → 24h volume, funding, OI, prev-day price) and spot tokens listed on Hyperliquid."""
    perps, spot = {}, {}
    m = _get(HL, {"type": "metaAndAssetCtxs"})
    if m and len(m) == 2:
        for u, c in zip(m[0].get("universe", []), m[1]):
            try:
                perps[u["name"].upper()] = {"vol24": float(c.get("dayNtlVlm", 0)), "funding": float(c.get("funding", 0)),
                                            "oi": float(c.get("openInterest", 0)) * float(c.get("markPx", 0)),
                                            "chg24": (float(c["markPx"]) / float(c["prevDayPx"]) - 1) * 100 if float(c.get("prevDayPx", 0)) else None}
            except (KeyError, ValueError, TypeError):
                continue
    s = _get(HL, {"type": "spotMetaAndAssetCtxs"})
    if s and len(s) == 2:
        tokens = {t["index"]: t["name"] for t in s[0].get("tokens", [])}
        pairs = s[0].get("universe", [])
        for pr, c in zip(pairs, s[1]):
            try:
                base = tokens.get(pr["tokens"][0], pr["name"]).upper()
                spot[base] = {"vol24": float(c.get("dayNtlVlm", 0)), "price": float(c.get("markPx", 0)),
                              "chg24": (float(c["markPx"]) / float(c["prevDayPx"]) - 1) * 100 if float(c.get("prevDayPx", 0)) else None}
            except (KeyError, ValueError, TypeError, IndexError):
                continue
    return {"perps": perps, "spot": spot, "available": bool(perps or spot)}


# ----------------------------------------------------------------------------- scoring
def _rank_pct(values: list[float | None]) -> list[float | None]:
    idx = [i for i, v in enumerate(values) if v is not None]
    order = sorted(idx, key=lambda i: values[i])
    out: list[float | None] = [None] * len(values)
    for k, i in enumerate(order):
        out[i] = k / max(1, len(order) - 1)
    return out


def score_universe(markets: list[dict], hl: dict, btc_bull: bool) -> list[dict]:
    rows = [r for r in markets if r["symbol"] not in STABLE and r["symbol"] not in WRAPPED and r.get("mcap") and r.get("volume")
            and r["mcap"] >= 1e8 and r["volume"] >= 5e6]
    for r in rows:
        r["hl_perp"] = r["symbol"] in hl.get("perps", {})
        r["hl_spot"] = r["symbol"] in hl.get("spot", {})
        r["vol_mcap"] = r["volume"] / r["mcap"]
        p = hl.get("perps", {}).get(r["symbol"]) or {}
        r["funding"] = p.get("funding")
        r["oi_usd"] = p.get("oi")
        age_days = None
        if r.get("atl_date"):
            try:
                age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(r["atl_date"].replace("Z", "+00:00"))).days
            except ValueError:
                pass
        r["age_days"] = age_days
    p7 = _rank_pct([r.get("chg7") for r in rows]); p30 = _rank_pct([r.get("chg30") for r in rows])
    p200 = _rank_pct([r.get("chg200") for r in rows]); p1y = _rank_pct([r.get("chg1y") for r in rows])
    pv = _rank_pct([r["vol_mcap"] for r in rows])
    for i, r in enumerate(rows):
        hlb = 8 if r["hl_spot"] else (4 if r["hl_perp"] else 0)
        dd = r.get("ath_chg") if r.get("ath_chg") is not None else -50
        blown = dd < -85
        s_short = 100 * (0.45 * (p7[i] or 0) + 0.35 * (p30[i] or 0) + 0.2 * (pv[i] or 0))
        if not btc_bull:
            s_short *= 0.5
        if r.get("funding") is not None and r["funding"] > 0.0005:   # crowded longs (>0.05 %/h)
            s_short -= 10
        s_mid = 100 * (0.4 * (p30[i] or 0) + 0.4 * (p200[i] or 0) + 0.2 * (pv[i] or 0)) + hlb
        if (r.get("chg30") or 0) < 0 and (r.get("chg200") or 0) < 0:
            s_mid -= 25
        if blown:
            s_mid -= 20
        s_long = 100 * (0.35 * (p1y[i] or 0) + 0.25 * (p200[i] or 0) + 0.4 * (1 - min(1, (r["rank"] or 150) / 150))) + hlb
        if r["mcap"] < 3e8:
            s_long -= 20
        if r.get("age_days") is not None and r["age_days"] < 365:
            s_long -= 15
        if blown:
            s_long -= 30
        r["score"] = {"short": round(max(0, min(100, s_short))), "mid": round(max(0, min(100, s_mid))), "long": round(max(0, min(100, s_long)))}
        r["why"] = {
            "short": f"за 7 дней {r.get('chg7', 0) or 0:+.1f}%, за 30 дней {r.get('chg30', 0) or 0:+.1f}%, объём/капитализация {r['vol_mcap']*100:.0f}%"
                     + ("" if btc_bull else "; BTC в медвежьем режиме — короткие лонги вполовину"),
            "mid": f"за 30 дней {r.get('chg30', 0) or 0:+.1f}%, за 200 дней {r.get('chg200', 0) or 0:+.1f}%, от максимума {dd:+.0f}%"
                   + (", есть на Hyperliquid" if r["hl_spot"] or r["hl_perp"] else ""),
            "long": f"ранг по капитализации {r['rank']}, за год {r.get('chg1y', 0) or 0:+.0f}%, капитализация ${r['mcap']/1e9:.1f} млрд"
                    + (", торгуется на Hyperliquid" if r["hl_spot"] or r["hl_perp"] else ""),
        }
    return rows


def picks(rows: list[dict], n: int = 8) -> dict[str, list[dict]]:
    out = {}
    for h in HORIZONS:
        pool = [r for r in rows if r["symbol"] not in CORE]
        if h == "mid":
            pool = [r for r in pool if r["mcap"] >= 3e8 and (r.get("age_days") is None or r["age_days"] >= 180)]
        if h == "long":
            pool = [r for r in pool if r["mcap"] >= 1e9]
        pool.sort(key=lambda r: -r["score"][h])
        out[h] = [{"symbol": r["symbol"], "name": r["name"], "price": r["price"], "score": r["score"][h], "why": r["why"][h],
                   "mcap": r["mcap"], "chg7": r.get("chg7"), "chg30": r.get("chg30"), "hl": r["hl_spot"] or r["hl_perp"]} for r in pool[:n]]
    return out


# ----------------------------------------------------------------------------- portfolio state machine
class SpotPortfolio:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.execute("""CREATE TABLE IF NOT EXISTS spot_portfolio(symbol TEXT PRIMARY KEY, bucket TEXT, weight REAL, entry REAL,
                        entry_time INTEGER, high REAL, status TEXT, reason TEXT)""")
        conn.execute("CREATE TABLE IF NOT EXISTS spot_actions(time INTEGER, symbol TEXT, action TEXT, weight REAL, price REAL, reason TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS spot_value(time INTEGER PRIMARY KEY, index_value REAL)")
        conn.commit()

    def positions(self) -> list[dict]:
        keys = ("symbol", "bucket", "weight", "entry", "entry_time", "high", "status", "reason")
        return [dict(zip(keys, r)) for r in self.conn.execute("SELECT symbol,bucket,weight,entry,entry_time,high,status,reason FROM spot_portfolio WHERE status='OPEN'")]

    def _act(self, now: int, sym: str, action: str, weight: float, price: float, reason: str, bucket: str | None = None):
        self.conn.execute("INSERT INTO spot_actions VALUES(?,?,?,?,?,?)", (now, sym, action, weight, price, reason))
        if action in ("BUY", "ADD"):
            self.conn.execute("INSERT INTO spot_portfolio VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET weight=excluded.weight, reason=excluded.reason, status='OPEN'",
                              (sym, bucket, weight, price, now, price, "OPEN", reason))
        elif action == "TRIM":
            self.conn.execute("UPDATE spot_portfolio SET weight=?, reason=? WHERE symbol=?", (weight, reason, sym))
        elif action == "SELL":
            self.conn.execute("UPDATE spot_portfolio SET status='CLOSED', weight=0, reason=? WHERE symbol=?", (reason, sym))
        return {"time": now, "symbol": sym, "action": action, "weight": round(weight, 3), "price": price, "reason": reason}

    def step(self, rows: list[dict], pk: dict, btc_bull: bool, now: int | None = None) -> dict:
        now = now or int(time.time() * 1000)
        price = {r["symbol"]: r["price"] for r in rows}
        score = {r["symbol"]: r["score"] for r in rows}
        pos = {p["symbol"]: p for p in self.positions()}
        actions: list[dict] = []
        weekday = datetime.fromtimestamp(now / 1000, tz=timezone.utc).weekday()
        last_act = self.conn.execute("SELECT MAX(time) FROM spot_actions").fetchone()[0] or 0
        rebalance = weekday == 0 and now - last_act > 20 * 3_600_000 or not pos
        core = CORE if btc_bull else CORE_BEAR
        # --- core
        for sym, w in core.items():
            if sym not in price:
                continue
            p = pos.get(sym)
            if not p:
                actions.append(self._act(now, sym, "BUY", w, price[sym], f"Ядро портфеля: {sym} {w*100:.0f}% — держим всегда, доля зависит от режима BTC.", "core"))
            elif abs(p["weight"] - w) > 0.02 and rebalance:
                actions.append(self._act(now, sym, "ADD" if w > p["weight"] else "TRIM", w, price[sym],
                                         f"Ребаланс ядра под режим BTC ({'бычий' if btc_bull else 'медвежий'}): {sym} → {w*100:.0f}%.", "core"))
        # --- update highs, stops, trims for satellites
        for sym, p in list(pos.items()):
            if p["bucket"] == "core" or sym not in price:
                continue
            px = price[sym]
            high = max(p["high"] or px, px)
            self.conn.execute("UPDATE spot_portfolio SET high=? WHERE symbol=?", (high, sym))
            ret = px / p["entry"] - 1
            sc = score.get(sym, {}).get("mid" if p["bucket"] == "mid" else "short", 0)
            if ret <= -0.15:
                actions.append(self._act(now, sym, "SELL", 0, px, f"Стоп по позиции: {ret*100:+.0f}% от входа {p['entry']:.4g}. Правило −15% защищает портфель от затяжного падения."))
            elif p["bucket"] == "short" and (sc < 40 or not btc_bull):
                actions.append(self._act(now, sym, "SELL", 0, px, f"Короткая идея отработала/выдохлась: оценка {sc}/100{'' if btc_bull else ', BTC в медвежьем режиме'} — выходим ({ret*100:+.0f}%)."))
            elif ret >= 0.40 and p["weight"] > 0.03:
                actions.append(self._act(now, sym, "TRIM", round(p["weight"] / 2, 3), px, f"Фиксируем половину: {ret*100:+.0f}% от входа. Остаток едет бесплатно, стоп переносим на вход."))
            elif p["bucket"] == "mid" and sc < 35 and rebalance:
                actions.append(self._act(now, sym, "SELL", 0, px, f"Среднесрочная оценка упала до {sc}/100 — тренд сломан, освобождаем место ({ret*100:+.0f}%)."))
            elif px < high * 0.75 and ret > 0.2:
                actions.append(self._act(now, sym, "SELL", 0, px, f"Откат 25% от максимума позиции при прибыли {ret*100:+.0f}% — забираем, перезайдём по новому сигналу."))
        pos = {p["symbol"]: p for p in self.positions()}
        # --- fill satellites (weekly / when empty)
        if rebalance:
            invested = sum(p["weight"] for p in pos.values())
            held = set(pos)
            mids = [x for x in pk["mid"] if x["score"] >= 60 and x["symbol"] not in held]
            n_mid = len([p for p in pos.values() if p["bucket"] == "mid"])
            for x in mids[: max(0, 5 - n_mid)]:
                if invested + 0.08 > 0.90:
                    break
                actions.append(self._act(now, x["symbol"], "BUY", 0.08, x["price"], f"Среднесрочная идея (1–6 мес), оценка {x['score']}/100: {x['why']}. Стоп −15%, фиксация половины на +40%.", "mid"))
                invested += 0.08; held.add(x["symbol"])
            if btc_bull:
                shorts = [x for x in pk["short"] if x["score"] >= 70 and x["symbol"] not in held]
                n_s = len([p for p in pos.values() if p["bucket"] == "short"])
                for x in shorts[: max(0, 3 - n_s)]:
                    if invested + 0.05 > 0.90:
                        break
                    actions.append(self._act(now, x["symbol"], "BUY", 0.05, x["price"], f"Короткая идея (1–4 нед), импульс {x['score']}/100: {x['why']}. Выход при потере импульса или −15%.", "short"))
                    invested += 0.05; held.add(x["symbol"])
        self.conn.commit()
        # --- valuation index (100 at start)
        pos = self.positions()
        invested = sum(p["weight"] for p in pos)
        cash = max(0.1, 1 - invested)
        pnl = sum(p["weight"] * (price.get(p["symbol"], p["entry"]) / p["entry"] - 1) for p in pos if p["entry"])
        idx = round(100 * (1 + pnl), 2)   # open-position P&L vs entries, weight-adjusted (100 = start)
        self.conn.execute("INSERT OR REPLACE INTO spot_value VALUES(?,?)", (now, idx))
        self.conn.commit()
        hist = [{"time": t, "v": v} for t, v in self.conn.execute("SELECT time,index_value FROM spot_value ORDER BY time DESC LIMIT 60").fetchall()][::-1]
        return {"positions": [dict(p, price=price.get(p["symbol"]), pnl_pct=round((price.get(p["symbol"], p["entry"]) / p["entry"] - 1) * 100, 2) if p["entry"] else None,
                                   score=score.get(p["symbol"])) for p in pos],
                "cash_pct": round(cash * 100), "invested_pct": round(invested * 100), "index": idx, "history": hist,
                "actions": actions, "rebalance_day": rebalance,
                "recent_actions": [dict(zip(("time", "symbol", "action", "weight", "price", "reason"), r)) for r in
                                   self.conn.execute("SELECT time,symbol,action,weight,price,reason FROM spot_actions ORDER BY time DESC LIMIT 20").fetchall()]}


# ----------------------------------------------------------------------------- daily note (rules) + run
def market_note(btc_bull: bool, btc: dict | None, pk: dict, state: dict) -> str:
    lines = []
    if btc:
        lines.append(f"BTC {'в бычьем режиме' if btc_bull else 'в медвежьем режиме'}: за 7 дней {btc.get('chg7', 0) or 0:+.1f}%, за 30 дней {btc.get('chg30', 0) or 0:+.1f}%, от максимума {btc.get('ath_chg', 0) or 0:+.0f}%.")
    lines.append(f"Портфель: инвестировано {state['invested_pct']}%, в кэше {state['cash_pct']}%, индекс {state['index']:.1f} (старт 100).")
    if state["actions"]:
        ru = {"BUY": "купить", "ADD": "добавить", "TRIM": "зафиксировать часть", "SELL": "продать"}
        lines.append("Сегодня: " + "; ".join(f"{ru.get(a['action'], a['action'])} {a['symbol']}" + (f" ({a['weight']*100:.0f}%)" if a['weight'] else "") for a in state["actions"][:6]) + ".")
    else:
        lines.append("Сегодня действий нет — правила не сработали, держим.")
    if pk["short"]:
        lines.append("Короткий срок (1–4 нед), лучшие по импульсу: " + ", ".join(f"{x['symbol']} {x['score']}" for x in pk["short"][:5]) + ".")
    if pk["mid"]:
        lines.append("Средний срок (1–6 мес): " + ", ".join(f"{x['symbol']} {x['score']}" for x in pk["mid"][:5]) + ".")
    if pk["long"]:
        lines.append("Долгий срок (6–24 мес): " + ", ".join(f"{x['symbol']} {x['score']}" for x in pk["long"][:5]) + ".")
    lines.append("Правила: ядро BTC/ETH всегда; сателлиты — стоп −15%, фиксация половины на +40%; короткие идеи только при бычьем BTC; кэш не ниже 10%.")
    return "\n".join(lines)


def run(conn: sqlite3.Connection, btc_bull: bool | None = None, llm=None, now: int | None = None) -> dict:
    now = now or int(time.time() * 1000)
    markets = coingecko_markets()
    hl = hyperliquid_universe()
    if not markets:
        return {"asof": now, "available": False, "error": "CoinGecko недоступен", "hyperliquid": hl.get("available", False)}
    btc = next((r for r in markets if r["symbol"] == "BTC"), None)
    if btc_bull is None:
        btc_bull = bool(btc and (btc.get("chg200") or 0) > 0)
    rows = score_universe(markets, hl, btc_bull)
    pk = picks(rows)
    state = SpotPortfolio(conn).step(rows, pk, btc_bull, now)
    note = market_note(btc_bull, btc, pk, state)
    llm_note = ""
    if llm is not None and bool(getattr(llm, "available", False)):
        try:
            llm_note = llm.complete(PORTFOLIO_SYSTEM, json.dumps({"btc_bull": btc_bull, "btc": btc, "picks": {h: pk[h][:5] for h in HORIZONS},
                                                                  "portfolio": {k: state[k] for k in ("positions", "cash_pct", "invested_pct", "index", "actions")}},
                                                                 ensure_ascii=False, default=str)).strip()
        except Exception:  # noqa: BLE001
            llm_note = ""
    hl_spot_list = sorted(hl.get("spot", {}).items(), key=lambda kv: -kv[1]["vol24"])[:15]
    return {"asof": now, "available": True, "btc_bull": btc_bull, "universe": len(rows), "hyperliquid": hl.get("available", False),
            "picks": pk, "portfolio": state, "note": llm_note or note, "note_source": "llm" if llm_note else "rules",
            "hl_spot_top": [{"symbol": k, "vol24": v["vol24"], "price": v["price"], "chg24": v["chg24"],
                             "score": next((r["score"] for r in rows if r["symbol"] == k), None)} for k, v in hl_spot_list],
            "sources": ["CoinGecko /coins/markets", "Hyperliquid info API"]}


PORTFOLIO_SYSTEM = """Ты — портфельный консультант CRYPTO NOVΛ по спотовому рынку. Пиши по-русски, просто и конкретно, 6–10 предложений.
Используй ТОЛЬКО числа и факты из JSON: режим BTC, оценки монет, позиции, действия и их причины. Ничего не выдумывай и не добавляй
монеты, которых нет в данных. Структура: 1) что с рынком сегодня, 2) что делаем с портфелем и почему, 3) какие идеи на короткий,
средний и долгий срок, 4) чего ждём и что отменит план. Без эмодзи и без обещаний доходности."""
