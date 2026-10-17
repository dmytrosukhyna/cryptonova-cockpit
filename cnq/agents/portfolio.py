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

Six demo accounts of $10 000 go to the panel under `portfolios` (order in `portfolio_order`; 6th `ideas` «По рекомендациям» —
spot_profiles.IdeasPortfolio, buys what web/ideas.json says, see the spot_profiles docstring):
  cons / high — spot_profiles.RiskPortfolio;  mod — SpotPortfolio above (17.10: holds no memes; closed P&L counted in the index);
  cons / mod / high were reset and rebuilt from $10 000 once on 17.10 (reset_spot, archive in spot_archive);
  meme — the meme desk's paper account (memes.py, Hyperliquid meme perps, 1× notional) mapped into the same schema by
         `meme_profile` — read-only, the meme rules are not touched here;
  bal  — «Сбалансированный · максимум потенциала» (BalancedPortfolio below): an EXPERIMENT built for a rising market, with a
         written protection rule for a falling one. Sleeves (bull targets): core BTC 22 / ETH 14 / SOL 9 = 45 %; five large
         established alts in their own uptrend × 6 % = 30 %; high risk 20 % = HYPE 6 % + two sector leaders × 4 % + two liquid
         memes × 3 % (memes only after a ≥ 30 % dip from the 30-day high — the only entry our meme research supported);
         cash 5 %. Selection, rebalancing, take-profit, stops and the bear switch are explicit rules (BAL, bal_* functions).
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


def overheated(chg7: float | None, chg30: float | None) -> bool:
    """«Рывок вверх» — +40 % or more in 7 days or +120 % or more in 30 days: the mod portfolio does not chase it (17.10; same
    thresholds as the spot ideas table, spot_ideas.OVERHEAT_7D / OVERHEAT_30D)."""
    from .spot_ideas import OVERHEAT_7D, OVERHEAT_30D
    return (chg7 is not None and chg7 >= OVERHEAT_7D) or (chg30 is not None and chg30 >= OVERHEAT_30D)


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
        # 17.10 fix: the context list is longer than the pair list (1117 vs 332) — match by the pair name in ctx["coin"], never by position
        ctx = {c.get("coin"): c for c in s[1] if isinstance(c, dict)}
        for pr in pairs:
            c = ctx.get(pr.get("name"))
            if c is None:
                continue
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


def preferences() -> dict:
    """config/preferred.json — the owner's trusted / avoided coins. Preferred get +8 and go first among qualified; avoided are skipped."""
    try:
        with open("config/preferred.json", encoding="utf-8") as f:
            d = json.load(f)
        return {"preferred": [x.upper() for x in d.get("preferred", [])], "avoid": [x.upper() for x in d.get("avoid", [])]}
    except Exception:
        return {"preferred": [], "avoid": []}


def picks(rows: list[dict], n: int = 8) -> dict[str, list[dict]]:
    out = {}
    pref = preferences()
    for h in HORIZONS:
        pool = [r for r in rows if r["symbol"] not in CORE and r["symbol"] not in pref["avoid"]]
        for r in pool:
            if r["symbol"] in pref["preferred"] and not r.get("_pref_applied"):
                for hh in HORIZONS:
                    r["score"][hh] = min(100, r["score"][hh] + 8)
                    r["why"][hh] = (r["why"][hh] + "; в списке доверия") if r["why"].get(hh) else "в списке доверия"
                r["_pref_applied"] = True
        if h == "mid":
            pool = [r for r in pool if r["mcap"] >= 3e8 and (r.get("age_days") is None or r["age_days"] >= 180)]
        if h == "long":
            pool = [r for r in pool if r["mcap"] >= 1e9]
        pool.sort(key=lambda r: (-(1 if r["symbol"] in pref["preferred"] and r["score"][h] >= 55 else 0), -r["score"][h]))
        out[h] = [{"symbol": r["symbol"], "name": r["name"], "price": r["price"], "score": r["score"][h], "why": r["why"][h],
                   "mcap": r["mcap"], "chg7": r.get("chg7"), "chg30": r.get("chg30"), "hl": r["hl_spot"] or r["hl_perp"],
                   "preferred": r["symbol"] in pref["preferred"]} for r in pool[:n]]
    return out


# ----------------------------------------------------------------------------- portfolio state machine
class SpotPortfolio:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.execute("""CREATE TABLE IF NOT EXISTS spot_portfolio(symbol TEXT PRIMARY KEY, bucket TEXT, weight REAL, entry REAL,
                        entry_time INTEGER, high REAL, status TEXT, reason TEXT)""")
        conn.execute("CREATE TABLE IF NOT EXISTS spot_actions(time INTEGER, symbol TEXT, action TEXT, weight REAL, price REAL, reason TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS spot_value(time INTEGER PRIMARY KEY, index_value REAL)")
        conn.execute("CREATE TABLE IF NOT EXISTS spot_mod_meta(k TEXT PRIMARY KEY, v REAL)")
        conn.commit()

    def positions(self) -> list[dict]:
        keys = ("symbol", "bucket", "weight", "entry", "entry_time", "high", "status", "reason")
        return [dict(zip(keys, r)) for r in self.conn.execute("SELECT symbol,bucket,weight,entry,entry_time,high,status,reason FROM spot_portfolio WHERE status='OPEN'")]

    def realized(self) -> float:
        """Closed P&L in units of the $10 000 start (17.10: sells and trims used to drop out of the index)."""
        r = self.conn.execute("SELECT v FROM spot_mod_meta WHERE k='realized'").fetchone()
        return r[0] if r else 0.0

    def _act(self, now: int, sym: str, action: str, weight: float, price: float, reason: str, bucket: str | None = None):
        self.conn.execute("INSERT INTO spot_actions VALUES(?,?,?,?,?,?)", (now, sym, action, weight, price, reason))
        row = self.conn.execute("SELECT weight, entry, status FROM spot_portfolio WHERE symbol=?", (sym,)).fetchone()
        is_open = bool(row and row[2] == "OPEN")
        w0, e0 = (row[0], row[1]) if is_open else (0.0, price)
        gain = 0.0
        if action in ("BUY", "ADD"):
            if is_open and weight > w0 and e0 and price:      # ADD: harmonic entry keeps the coin amount exact
                self.conn.execute("UPDATE spot_portfolio SET weight=?, entry=?, reason=? WHERE symbol=?",
                                  (weight, weight / (w0 / e0 + (weight - w0) / price), reason, sym))
            elif is_open:
                self.conn.execute("UPDATE spot_portfolio SET weight=?, reason=? WHERE symbol=?", (weight, reason, sym))
            else:     # new position or a re-buy of a closed one: fresh entry, time and high (17.10: a re-buy kept the old entry)
                self.conn.execute("INSERT OR REPLACE INTO spot_portfolio VALUES(?,?,?,?,?,?,?,?)", (sym, bucket, weight, price, now, price, "OPEN", reason))
        elif action == "TRIM":
            gain = max(0.0, w0 - weight) * (price / e0 - 1) if is_open and e0 else 0.0
            self.conn.execute("UPDATE spot_portfolio SET weight=?, reason=? WHERE symbol=?", (weight, reason, sym))
        elif action == "SELL":
            gain = w0 * (price / e0 - 1) if is_open and e0 else 0.0
            self.conn.execute("UPDATE spot_portfolio SET status='CLOSED', weight=0, reason=? WHERE symbol=?", (reason, sym))
        if gain:
            self.conn.execute("INSERT OR REPLACE INTO spot_mod_meta VALUES('realized', ?)", (self.realized() + gain,))
        return {"time": now, "symbol": sym, "action": action, "weight": round(weight, 3), "price": price, "reason": reason}

    def step(self, rows: list[dict], pk: dict, btc_bull: bool, now: int | None = None, memes: set[str] | None = None) -> dict:
        """`memes` (17.10): meme tickers — never bought here and sold if held (memes live in «Высокий риск» / «Мем-портфель»)."""
        now = now or int(time.time() * 1000)
        memes = {m.upper() for m in memes or ()}
        # 17.10: no memes, and no coin after a spike (+40 % in 7 days / +120 % in 30 days) is bought here
        pk = {h: [x for x in v if x["symbol"] not in memes and not overheated(x.get("chg7"), x.get("chg30"))] for h, v in pk.items()}
        price = {r["symbol"]: r["price"] for r in rows}
        score = {r["symbol"]: r["score"] for r in rows}
        names = {r["symbol"]: r.get("name", r["symbol"]) for r in rows}
        whys = {r["symbol"]: (r.get("why") or {}).get("mid") for r in rows}
        pos = {p["symbol"]: p for p in self.positions()}
        actions: list[dict] = []
        weekday = datetime.fromtimestamp(now / 1000, tz=timezone.utc).weekday()
        last_act = self.conn.execute("SELECT MAX(time) FROM spot_actions").fetchone()[0] or 0
        rebalance = weekday == 0 and now - last_act > 20 * 3_600_000 or not pos
        core = CORE if btc_bull else CORE_BEAR
        for sym, p in list(pos.items()):           # memes are not for the moderate portfolio
            if sym in memes and p["bucket"] != "core" and price.get(sym):
                ret = price[sym] / p["entry"] - 1 if p["entry"] else 0.0
                actions.append(self._act(now, sym, "SELL", 0, price[sym], f"Мемы в умеренном портфеле не держим: {sym} — мем (для мемов есть «Высокий риск» "
                                                                          f"по статусу безопасности и «Мем-портфель»). Выходим ({ret*100:+.0f}%)."))
                pos.pop(sym)
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
        # --- owner's trust list: on rebalance a preferred mid idea (score ≥ 60) replaces the weakest non-preferred satellite
        #     when it beats it by ≥ 10 points — the rules (stop −15 %, caps) stay the same, the owner only sets priorities
        if rebalance:
            pref = preferences()["preferred"]
            held = set(pos)
            for x in [x for x in pk["mid"] if x["symbol"] in pref and x["score"] >= 60 and x["symbol"] not in held]:
                mids_held = [p for p in pos.values() if p["bucket"] == "mid" and p["symbol"] not in pref]
                if not mids_held:
                    break
                weakest = min(mids_held, key=lambda p: score.get(p["symbol"], {}).get("mid", 0))
                w_score = score.get(weakest["symbol"], {}).get("mid", 0)
                if x["score"] >= w_score + 10 and weakest["symbol"] in price:
                    actions.append(self._act(now, weakest["symbol"], "SELL", 0, price[weakest["symbol"]],
                                             f"Замена: {x['symbol']} из списка доверия сильнее ({x['score']} против {w_score}) — освобождаем место."))
                    pos.pop(weakest["symbol"], None)
                    actions.append(self._act(now, x["symbol"], "BUY", 0.08, x["price"], f"Из списка доверия, оценка {x['score']}/100: {x['why']}. Стоп −15%, фиксация половины на +40%.", "mid"))
                    pos[x["symbol"]] = {"symbol": x["symbol"], "bucket": "mid", "weight": 0.08}
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
        idx = round(100 * (1 + self.realized() + pnl), 2)   # closed + open P&L vs entries, weight-adjusted (100 = start)
        self.conn.execute("INSERT OR REPLACE INTO spot_value VALUES(?,?)", (now, idx))
        self.conn.commit()
        hist = [{"time": t, "v": v} for t, v in self.conn.execute("SELECT time,index_value FROM spot_value ORDER BY time DESC LIMIT 60").fetchall()][::-1]
        return {"positions": [dict(p, price=price.get(p["symbol"]), pnl_pct=round((price.get(p["symbol"], p["entry"]) / p["entry"] - 1) * 100, 2) if p["entry"] else None,
                                   score=score.get(p["symbol"]), name=names.get(p["symbol"], p["symbol"]), why=whys.get(p["symbol"])) for p in pos],
                "cash_pct": round(cash * 100), "invested_pct": round(invested * 100), "index": idx, "history": hist,
                "actions": actions, "rebalance_day": rebalance,
                "recent_actions": [dict(zip(("time", "symbol", "action", "weight", "price", "reason"), r)) for r in
                                   self.conn.execute("SELECT time,symbol,action,weight,price,reason FROM spot_actions ORDER BY time DESC LIMIT 20").fetchall()]}


# ----------------------------------------------------------------------------- 5th profile: «Сбалансированный · максимум потенциала»
DAY_MS = 86_400_000
BAL = {
    "key": "bal", "name": "Сбалансированный · максимум потенциала", "prefix": "spot_bal_", "status": "эксперимент",
    "core": {"BTC": 0.22, "ETH": 0.14, "SOL": 0.09},                      # core sleeve 45 %
    "mid_n": 5, "mid_w": 0.06,                                              # mid sleeve 30 %
    "anchor": "HYPE", "anchor_w": 0.06, "lead_n": 2, "lead_w": 0.04,       # high sleeve 20 % = 6 + 2 × 4 + 2 × 3
    "meme_n": 2, "meme_w": 0.03,
    "bear_mult": {"core": 2 / 3, "mid": 0.5, "high": 0.0},                  # protection: cash ≈ 55 %
    "drift": 1 / 3,                     # rebalance when a sleeve is off its target by more than a third of the target
    "rebalance_days": 20,               # monthly: first run on the 1st–3rd of a month, ≥ 20 days after the last rebalance
    "drift_gap_days": 7,                # a drift-triggered rebalance at most once a week
    "regime_confirm_h": 72,             # the BTC regime must hold 3 days before the mode switches: on 2021–26 daily data the
                                        # raw flag flipped 62 times; 1 / 3 / 7-day windows: ×1.69 / ×1.86 / ×1.96, worst DD
                                        # −51 / −50 / −50 %, protection trades 201 / 98 / 64 (scripts/balanced_backtest.py). 3 days =
                                        # compromise between reacting to a crash and whipsaw; longer looked better in-sample only
    "mid_stop": -0.30, "high_stop": -0.35, "body_x": 2.0, "trail": 0.35,
    "mid_min_vol": 5e7, "lead_min_mcap": 3e8, "lead_min_vol": 2e7, "meme_min_mcap": 1e9, "meme_min_vol": 5e7,
    "dead_ath": -90.0,                  # more than −90 % from the all-time high = «мёртвая» монета, not bought
    "fee": 0.001,                       # 0.1 % per traded notional (spot taker), charged to cash
}
CEX_TOKENS = {"BNB", "LEO", "OKB", "BGB", "GT", "KCS", "CRO", "HTX", "WBT", "MX"}
SECTORS = ("ИИ", "RWA", "DeFi", "L2", "оракул", "инфраструктура", "игры", "приватность", "платежи", "L1", "платформа")
SLEEVE_RU = {"core": "ядро", "mid": "крупные альты", "high": "высокий риск"}
ROLE_RU = {"core": "ядро", "mid": "крупный альт", "anchor": "HYPE — якорь высокого риска", "lead": "лидер сектора", "meme": "мем"}
BAL_ABOUT = ("Эксперимент, без обещаний доходности. Собран под рост рынка; если рынок развернётся вниз — правило защиты: когда BTC "
             "3 дня подряд в медвежьем режиме, продаём высокий риск, альты режем вдвое, ядро до 2/3, кэш ≈55%; полные доли — когда "
             "BTC снова 3 дня в бычьем. Состав: ядро BTC/ETH/SOL 45%, пять крупных альтов в своём тренде по 6%, высокий риск 20% (HYPE 6%, "
             "два лидера секторов по 4%, два мема по 3% — только после просадки 30% и проверки мемов), кэш 5%. Ребаланс раз в месяц или когда доля "
             "корзины уходит от плана на треть. Высокий риск: на ×2 забираем тело, остаток — до отката 35% от пика, стоп −35%; "
             "альты — стоп −30%.")
BAL_RULES = [
    "Ядро 45%: BTC 22%, ETH 14%, SOL 9%. Без стопа, раз в месяц возвращаем доли.",
    "Крупные альты 30% (5 × 6%): класс «умеренный» в анкете (топ-60, капитализация от $1 млрд, на рынке больше года, не мем), "
    "не токен централизованной биржи, оборот от $50M в сутки, есть на Hyperliquid, монета выше, чем 200 дней назад, "
    "и не ниже −90% от максимума. Из подходящих — пять с лучшей среднесрочной оценкой (тренд 30/200 дней, оборот; список доверия +8).",
    "Высокий риск 20%: HYPE 6% (выбор владельца; если HYPE ниже, чем 200 дней назад, его место берёт следующий лидер), "
    "два лидера секторов по 4% (капитализация от $300M, оборот от $20M, есть на Hyperliquid, рост за 30 и 200 дней, лучший по "
    "оценке в своём секторе, по одному на сектор), два самых ликвидных мема по 3% (капитализация от $1 млрд, оборот от $50M) — "
    "покупаем только когда мем на 30%+ ниже максимума за 30 дней и проверка мемов даёт «можно на небольшую сумму»; до этого их доля "
    "ждёт в кэше. Если проверка мемов показала «не брать» — продаём: при красном флаге сразу, иначе на недельной проверке.",
    "Ребаланс: 1–3 числа месяца или когда доля корзины ушла от плана больше чем на треть (ядро вне 30–60%, альты вне 20–40%, "
    "высокий риск выше 26.7%), но по перекосу — не чаще раза в неделю. Ядро и альты возвращаем к плановым долям, выбывших из правил "
    "меняем; если подходящих альтов меньше пяти, их доля ждёт в кэше. Высокий риск не доливаем.",
    "Фиксация в высоком риске: на ×2 от входа продаём половину — тело вернулось; остаток держим без потолка и продаём при откате "
    "35% от пика. Стоп −35% от входа, пока тело не взято. Альты: стоп −30% от входа, место ждёт ребаланса.",
    "Защита: если BTC 3 дня подряд в медвежьем режиме (тот же флаг, что у остальных портфелей), продаём весь высокий риск, "
    "альты режем вдвое, ядро до 2/3 (BTC ≈15%, ETH ≈9%, SOL 6%), кэш ≈55%. Полные доли возвращаем, когда BTC 3 дня снова в бычьем. "
    "3 дня — чтобы не дёргаться: на истории 2021–2026 флаг BTC переключался 62 раза, часто на 1–2 дня.",
    "Комиссия 0.1% с каждой сделки вычитается из счёта. Это учебный счёт, а не совет; рынок может пойти против плана.",
]
# Sanity check of the rules on the only long history in the repo — `python scripts/balanced_backtest.py` reproduces these numbers.
BAL_BACKTEST = {
    "script": "scripts/balanced_backtest.py", "period": "2021-04-03 → 2026-09-09",
    "data": "data/daily_closes18.json — 18 крупных монет, дневные закрытия Binance",
    "result": {"bal": {"final_x": 1.86, "cagr_pct": 12.1, "max_dd_pct": -50.0},
               "bal_no_protection": {"final_x": 1.97, "cagr_pct": 13.3, "max_dd_pct": -59.7},
               "btc_hold": {"final_x": 1.37, "cagr_pct": 5.9, "max_dd_pct": -76.7},
               "cons_50_25_15": {"final_x": 2.45, "cagr_pct": 17.9, "max_dd_pct": -76.2},
               "equal_weight_18": {"final_x": 1.61, "cagr_pct": 9.2, "max_dd_pct": -76.6}},
    "bal_by_year_pct": {"2021": 46.9, "2022": -41.2, "2023": 98.7, "2024": 49.2, "2025": -23.2, "2026": -5.3},
    "text_ru": ("Проверка правил, а не прогноз. История 04.2021 → 09.2026 на 18 крупных монетах (это сегодняшние выжившие — результат "
                "завышен), без HYPE и мелких лидеров, мем только DOGE, дневные закрытия, комиссия 0.1%. Итог ×1.86 (12% в год), "
                "худшая просадка −50%. Для сравнения: BTC ×1.37 и −77%; BTC/ETH/SOL 50/25/15 ×2.45 и −76% — доходнее, но падал глубже. "
                "Правило защиты стоило части дохода (без него ×1.97), но срезало просадку с −60% до −50%. 2022 год: −41%, 2025: −23%."),
}


def _mpct(x: float) -> str:
    return f"{x * 100:.0f}%".replace("-", "−")


def bal_targets(mode: str) -> dict:
    m = BAL["bear_mult"] if mode == "bear" else {"core": 1.0, "mid": 1.0, "high": 1.0}
    core = sum(BAL["core"].values()) * m["core"]
    mid = BAL["mid_n"] * BAL["mid_w"] * m["mid"]
    high = (BAL["anchor_w"] + BAL["lead_n"] * BAL["lead_w"] + BAL["meme_n"] * BAL["meme_w"]) * m["high"]
    return {"core": core, "mid": mid, "high": high, "cash": 1 - core - mid - high}


def meme_symbols(cards: list[dict] | None = None, extra=()) -> set[str]:
    """Meme tickers: the meme desk's HL list (×1000 «k» prefix stripped) + research cards tagged «мем» + `extra`."""
    from .memes import MEME_COINS
    s = {c[1:] if c.startswith("k") and c[1:].isupper() else c for c in MEME_COINS}
    s |= {c["symbol"] for c in cards or [] if "мем" in (c.get("tags") or [])}
    return {x.upper() for x in s | set(extra)}


def _is_cex(sym: str, card: dict | None) -> bool:
    tags = (card or {}).get("tags") or []
    return sym in CEX_TOKENS or sym in WRAPPED or ("биржевой токен" in tags and "DeFi" not in tags)


def _sector(card: dict | None) -> str:
    tags = (card or {}).get("tags") or []
    return next((t for t in SECTORS if t in tags), "прочее")


def _trend_fail(r: dict, min_vol: float, need30: bool = False) -> str | None:
    """None if the coin passes the common filters, else a short Russian reason."""
    if (r.get("volume") or 0) < min_vol:
        return f"оборот ${(r.get('volume') or 0) / 1e6:.0f}M < ${min_vol / 1e6:.0f}M"
    if not (r.get("hl_perp") or r.get("hl_spot")):
        return "нет на Hyperliquid"
    if (r.get("chg200") or 0) <= 0:
        return f"ниже, чем 200 дней назад ({r.get('chg200') or 0:+.0f}%)"
    if need30 and (r.get("chg30") or 0) <= 0:
        return f"за 30 дней {r.get('chg30') or 0:+.0f}%"
    if (r.get("ath_chg") if r.get("ath_chg") is not None else -50) <= BAL["dead_ath"]:
        return f"{r.get('ath_chg'):.0f}% от максимума"
    return None


def _sc(r: dict, h: str = "mid") -> float:
    return ((r.get("score") or {}).get(h) or 0) if isinstance(r.get("score"), dict) else 0


def bal_mid_candidates(rows: list[dict], cards: list[dict], memes: set[str], exclude=()) -> list[dict]:
    cm = {c["symbol"]: c for c in cards or []}
    out = []
    for r in rows:
        s, c = r["symbol"], cm.get(r["symbol"])
        if s in BAL["core"] or s == BAL["anchor"] or s in exclude or s in memes or s in STABLE or _is_cex(s, c):
            continue
        if cards:
            if not c or c.get("risk") != "mod":
                continue
        elif not ((r.get("rank") or 999) <= 60 and (r.get("mcap") or 0) >= 1e9):
            continue
        if _trend_fail(r, BAL["mid_min_vol"]) is None:
            out.append({"symbol": s, "score": _sc(r), "chg30": r.get("chg30"), "chg200": r.get("chg200"), "sector": _sector(c),
                        "why": (r.get("why") or {}).get("mid")})
    return sorted(out, key=lambda x: -x["score"])


def bal_leader_candidates(rows: list[dict], cards: list[dict], memes: set[str], exclude=()) -> list[dict]:
    cm = {c["symbol"]: c for c in cards or []}
    out, seen = [], set()
    for r in sorted(rows, key=lambda r: -_sc(r)):
        s, c = r["symbol"], cm.get(r["symbol"])
        if s in BAL["core"] or s == BAL["anchor"] or s in exclude or s in memes or s in STABLE or _is_cex(s, c):
            continue
        if (r.get("mcap") or 0) < BAL["lead_min_mcap"] or _trend_fail(r, BAL["lead_min_vol"], need30=True) is not None:
            continue
        sec = _sector(c)
        if sec != "прочее" and sec in seen:
            continue
        seen.add(sec)
        out.append({"symbol": s, "score": _sc(r), "sector": sec, "chg30": r.get("chg30"), "chg200": r.get("chg200"),
                    "why": (r.get("why") or {}).get("mid")})
    return out


def bal_meme_candidates(rows: list[dict], memes: set[str], conn: sqlite3.Connection | None, now: int) -> list[dict]:
    """The most liquid big memes with their distance from the 30-day high (same estimate as the spot meme status)."""
    from . import meme_spot as ms
    if conn is not None:
        ms.ensure(conn)
    out = []
    for r in sorted(rows, key=lambda r: -(r.get("volume") or 0)):
        if r["symbol"] not in memes or (r.get("mcap") or 0) < BAL["meme_min_mcap"] or (r.get("volume") or 0) < BAL["meme_min_vol"]:
            continue
        own = hl = None
        if conn is not None:
            own = {int(a): b for a, b in ((ms._cache_any(conn, f"hi:{r['symbol']}")[1] or {}).get("d") or {}).items()}
            hl = ms._cache_any(conn, f"hlhi:{r['symbol']}")[1]
        hi, basis = ms.high30(r.get("price"), [r.get("chg24"), r.get("chg7"), r.get("chg30")], own, hl, now)
        dd = round((r["price"] / hi - 1) * 100, 1) if hi and r.get("price") else None
        out.append({"symbol": r["symbol"], "from_high30_pct": dd, "dip": dd is not None and dd <= ms.DIP_30D * 100,
                    "volume": r.get("volume"), "mcap": r.get("mcap"), "basis": basis})
    return out


class BalancedPortfolio:
    """Ledger of spot_profiles.RiskPortfolio (tables spot_bal_*, cash in units of the $10 000 start) + sleeve rules above."""

    def __init__(self, conn: sqlite3.Connection):
        from .spot_profiles import RiskPortfolio
        self.rp = RiskPortfolio(conn, "bal", BAL)
        self.conn = conn

    # -- meta (REAL values)
    def _m(self, k: str):
        r = self.conn.execute(f"SELECT v FROM {self.rp.T_META} WHERE k=?", (k,)).fetchone()
        return r[0] if r else None

    def _set(self, k: str, v):
        if v is None:
            self.conn.execute(f"DELETE FROM {self.rp.T_META} WHERE k=?", (k,))
        else:
            self.conn.execute(f"INSERT OR REPLACE INTO {self.rp.T_META} VALUES(?,?)", (k, float(v)))

    @staticmethod
    def _role(p: dict) -> str:
        b = p.get("bucket") or ""
        return b.split(":", 1)[1] if b.startswith("high:") else b

    def _mv(self, p: dict, px: float | None) -> float:
        return p["weight"] * (px or p["entry"]) / p["entry"] if p["entry"] else 0.0

    def _trade(self, now, sym, target_mv, px, reason, bucket=None, actions=None, tol=0.005):
        """Bring the position's market value to `target_mv` (units of the start capital); fee 0.1 % of the traded notional."""
        row = {p["symbol"]: p for p in self.rp.positions()}.get(sym)
        mv = self._mv(row, px) if row else 0.0
        diff = target_mv - mv
        if row and abs(diff) < tol and target_mv > 0:
            return
        if not row and target_mv <= 0:
            return
        if target_mv <= 0:
            a = self.rp._act(now, sym, "SELL", 0, px, reason)
        elif not row:
            diff = min(diff, max(0.0, self.rp.cash() / (1 + BAL["fee"])))
            if diff < tol:
                return
            a = self.rp._act(now, sym, "BUY", diff, px, reason, bucket)
        elif diff < 0:
            a = self.rp._act(now, sym, "TRIM", row["weight"] * target_mv / mv, px, reason)
        else:
            diff = min(diff, max(0.0, self.rp.cash() / (1 + BAL["fee"])))
            if diff < tol:
                return
            a = self.rp._act(now, sym, "ADD", row["weight"] + diff, px, reason)
        self.rp._set_cash(self.rp.cash() - abs(diff) * BAL["fee"])
        if actions is not None:
            actions.append(a)

    def value(self, price: dict) -> float:
        return self.rp.cash() + sum(self._mv(p, price.get(p["symbol"])) for p in self.rp.positions())

    def sleeves_now(self, price: dict) -> dict:
        V = self.value(price) or 1.0
        out = {"core": 0.0, "mid": 0.0, "high": 0.0}
        for p in self.rp.positions():
            sleeve = (p["bucket"] or "high").split(":")[0]
            out[sleeve] = out.get(sleeve, 0.0) + self._mv(p, price.get(p["symbol"])) / V
        out["cash"] = self.rp.cash() / V
        return out

    def stopped_recently(self, now: int, days: int = 30) -> set[str]:
        """Coins sold by a stop or a trailing exit in the last `days` — not bought again until then (no re-entry right after a stop)."""
        return {s for (s,) in self.conn.execute(f"SELECT DISTINCT symbol FROM {self.rp.T_ACT} WHERE action='SELL' AND time>=? "
                                                f"AND (reason LIKE 'Стоп%' OR reason LIKE 'Трейлинг%')", (now - days * DAY_MS,))}

    def step(self, rows: list[dict], cards: list[dict], btc_bull: bool, memes: set[str] | None = None, now: int | None = None,
             meme_status: dict | None = None) -> dict:
        """`meme_status` (17.10): {SYM: spot meme row} from memes.json → spot_memes — the same check «Высокий риск» uses. A meme slot is
        bought only with status «small»; without status data no meme is bought. A held meme whose status is «avoid» is sold: with a red
        flag on the next run, otherwise at the weekly (Monday) meme check."""
        now = now or int(time.time() * 1000)
        memes = memes if memes is not None else meme_symbols(cards)
        mstat = {k.upper(): v for k, v in (meme_status or {}).items()}
        d = datetime.fromtimestamp(now / 1000, tz=timezone.utc)
        meme_week = d.weekday() == 0 and now - (self._m("meme_review") or 0) > 6 * DAY_MS
        if meme_week:
            self._set("meme_review", now)
        if rows and not any(r.get("hl_perp") or r.get("hl_spot") for r in rows):
            # Hyperliquid API did not answer this run: skip the «есть на Hyperliquid» filter instead of selling every alt
            rows = [{**r, "hl_perp": True} for r in rows]
        byrow = {r["symbol"]: r for r in rows}
        live = {r["symbol"]: r["price"] for r in rows if r.get("price")}
        price = dict(live)
        for p in self.rp.positions():                   # a held coin that left the snapshot keeps its last seen price
            s = p["symbol"]
            if s in live:
                self._set(f"px:{s}", live[s])
            elif self._m(f"px:{s}"):
                price[s] = self._m(f"px:{s}")
        actions: list[dict] = []
        # --- regime with a confirmation window (BAL['regime_confirm_h'])
        seen = 1.0 if btc_bull else 0.0
        mode = self._m("mode")
        mode_changed = False
        if mode is None:
            mode = seen; self._set("mode", mode)
        if seen != mode:
            since = self._m("flip_since")
            if since is None:
                self._set("flip_since", now); since = now
            if now - since >= BAL["regime_confirm_h"] * 3_600_000:
                mode = seen; self._set("mode", mode); self._set("flip_since", None); mode_changed = True
        else:
            self._set("flip_since", None)
        mode_s = "bull" if mode == 1.0 else "bear"
        T = bal_targets(mode_s)
        mult = BAL["bear_mult"] if mode_s == "bear" else {"core": 1.0, "mid": 1.0, "high": 1.0}
        # --- 1. exits by rule (every run)
        for p in self.rp.positions():
            s, role = p["symbol"], self._role(p)
            px = price.get(s)
            if not px or not p["entry"]:
                continue
            high = max(p["high"] or px, px)
            self.conn.execute(f"UPDATE {self.rp.T_POS} SET high=? WHERE symbol=?", (high, s))
            ret = px / p["entry"] - 1
            fixes = p["fixes"] or 0
            if role in ("anchor", "lead", "meme"):
                if not fixes and ret <= BAL["high_stop"]:
                    self._trade(now, s, 0, px, f"Стоп {_mpct(BAL['high_stop'])}: {s} {ret*100:+.0f}% от входа — в высоком риске убыток режем.", actions=actions)
                elif not fixes and px >= p["entry"] * BAL["body_x"]:
                    a = self.rp._act(now, s, "TRIM", round(p["weight"] / 2, 6), px, f"×{BAL['body_x']:.0f} от входа ({ret*100:+.0f}%): продаём половину — "
                                     f"тело вернулось. Остаток держим без потолка, выход при откате {BAL['trail']*100:.0f}% от пика.", fixes=1, last_fix=px)
                    self.rp._set_cash(self.rp.cash() - p["weight"] / 2 * px / p["entry"] * BAL["fee"])
                    actions.append(a)
                elif fixes and px <= high * (1 - BAL["trail"]):
                    self._trade(now, s, 0, px, f"Трейлинг: {s} на {(1 - px / high)*100:.0f}% ниже пика {high:.4g} — остаток продаём ({ret*100:+.0f}% от входа).", actions=actions)
            elif role == "mid" and ret <= BAL["mid_stop"]:
                self._trade(now, s, 0, px, f"Стоп {_mpct(BAL['mid_stop'])}: {s} {ret*100:+.0f}% от входа. Место ждёт ребаланса.", actions=actions)
            if role == "meme" and (mstat.get(s) or {}).get("status") == "avoid" and {p["symbol"] for p in self.rp.positions()} >= {s}:
                why = str(mstat[s].get("why_ru") or "статус «не брать»")
                v = round(ret * 100)
                res = "итог " + ("0%" if v == 0 else f"{v:+d}%".replace("-", "−")) + " от входа"
                if why.startswith("красный флаг"):
                    self._trade(now, s, 0, px, f"{s} продан: проверка мемов — красный флаг ({why.split(':', 1)[1].strip() if ':' in why else why}), {res}.", actions=actions)
                elif meme_week:
                    self._trade(now, s, 0, px, f"{s} продан на недельной проверке мемов: статус «не брать» — {why}, {res}.", actions=actions)
        # --- 2. rebalance: first run, monthly, mode switch, drift
        last_reb = self._m("last_rebalance") or 0
        first = self._m("started") is None
        monthly = d.day <= 3 and now - last_reb > BAL["rebalance_days"] * DAY_MS
        W = self.sleeves_now(price)
        dr = BAL["drift"]
        drift = [k for k in ("core", "mid") if T[k] > 0 and abs(W[k] - T[k]) > T[k] * dr] + (["high"] if W["high"] > T["high"] * (1 + dr) + 1e-9 else [])
        if now - last_reb < BAL["drift_gap_days"] * DAY_MS:
            drift = []        # a drift rebalance at most once a week (if fewer than 5 alts qualify, the mid gap cannot be closed anyway)
        cool = self.stopped_recently(now)
        why_reb = "старт" if first else ("смена режима BTC" if mode_changed else ("месячный ребаланс" if monthly else (f"перекос: {', '.join(SLEEVE_RU[k] for k in drift)}" if drift else None)))
        mid_c = bal_mid_candidates(rows, cards, memes, exclude=cool)
        lead_c = []
        if why_reb:
            V = self.value(price)
            held = {p["symbol"]: p for p in self.rp.positions()}
            ok_mid = {c["symbol"] for c in mid_c}
            # review: coins that no longer pass the rules
            for s, p in list(held.items()):
                role, r = self._role(p), byrow.get(s)
                if s not in price:
                    continue
                fail = None
                if role == "mid" and s not in ok_mid:
                    fail = "больше не проходит правила крупных альтов" + (f" ({_trend_fail(r, BAL['mid_min_vol'])})" if r and _trend_fail(r, BAL["mid_min_vol"]) else "")
                elif role in ("anchor", "lead") and not p["fixes"] and r and (r.get("chg200") or 0) <= 0 and (r.get("chg30") or 0) <= 0:
                    fail = f"тренд сломан (30 дней {r.get('chg30') or 0:+.0f}%, 200 дней {r.get('chg200') or 0:+.0f}%)"
                elif role in ("anchor", "lead", "meme") and mode_s == "bear":
                    fail = "защита: BTC в медвежьем режиме — высокий риск продаём"
                if fail:
                    self._trade(now, s, 0, price[s], f"Пересмотр ({why_reb}): {s} — {fail}.", actions=actions)
                    held.pop(s)
            # targets: core + mid back to plan, missing slots filled from the candidates
            want: list[tuple[str, float, str, str]] = []          # (symbol, target share, bucket, reason)
            for s, w in BAL["core"].items():
                if s in price:
                    want.append((s, w * mult["core"], "core", f"Ядро: {s} {w*mult['core']*100:.0f}% — держим без стопа, доля возвращается раз в месяц."))
            mids = [s for s, p in held.items() if self._role(p) == "mid"]
            for c in mid_c:
                if len(mids) >= BAL["mid_n"]:
                    break
                if c["symbol"] not in mids:
                    mids.append(c["symbol"])
            for s in mids:
                c = next((x for x in mid_c if x["symbol"] == s), None)
                want.append((s, BAL["mid_w"] * mult["mid"], "mid", f"Крупный альт в своём тренде, оценка {c['score'] if c else '—'}/100"
                             + (f": {c['why']}" if c and c.get("why") else "") + f". {BAL['mid_w']*mult['mid']*100:.0f}%, стоп −30%, ребаланс раз в месяц."))
            if mode_s == "bull":
                highs = {s: p for s, p in held.items() if self._role(p) in ("anchor", "lead")}
                anchor_r = byrow.get(BAL["anchor"])
                anchor_ok = anchor_r is not None and BAL["anchor"] not in cool and _trend_fail(anchor_r, BAL["lead_min_vol"]) is None
                if anchor_ok and BAL["anchor"] not in held:
                    want.append((BAL["anchor"], BAL["anchor_w"], "high:anchor", f"{BAL['anchor']} — якорь высокого риска (выбор владельца, крупнейший токен "
                                 f"Hyperliquid): {BAL['anchor_w']*100:.0f}%. Стоп −35%, на ×2 забираем тело."))
                n_lead = BAL["lead_n"] + (0 if anchor_ok or BAL["anchor"] in held else 1)
                lead_c = bal_leader_candidates(rows, cards, memes, exclude=set(mids) | set(held) | cool)
                have = [s for s, p in highs.items() if self._role(p) == "lead"]
                for c in lead_c:
                    if len(have) >= n_lead:
                        break
                    w = BAL["lead_w"] if len(have) < BAL["lead_n"] else BAL["anchor_w"]
                    want.append((c["symbol"], w, "high:lead", f"Лидер сектора «{c['sector']}» по тренду, оценка {c['score']}/100, за 30 дней "
                                 f"{c['chg30'] or 0:+.0f}%, за 200 дней {c['chg200'] or 0:+.0f}%: {w*100:.0f}%. Стоп −35%, на ×2 забираем тело."))
                    have.append(c["symbol"])
                if W["high"] > T["high"] * (1 + dr):         # high sleeve grew past its band → trim it pro rata back to the target
                    k = T["high"] / W["high"]
                    for s, p in held.items():
                        if self._role(p) in ("anchor", "lead", "meme"):
                            self._trade(now, s, self._mv(p, price.get(s)) * k, price[s], f"Ребаланс: высокий риск {W['high']*100:.0f}% при плане "
                                        f"{T['high']*100:.0f}% — срезаем пропорционально.", actions=actions)
            # execute: sells/trims first, then buys/adds
            V = self.value(price)
            plan = [(s, t * V, b, why) for s, t, b, why in want]
            cur = {p["symbol"]: self._mv(p, price.get(p["symbol"])) for p in self.rp.positions()}
            for s, tv, b, why in sorted(plan, key=lambda x: (x[1] - cur.get(x[0], 0.0))):
                if tv < cur.get(s, 0.0):
                    self._trade(now, s, tv, price[s], f"Ребаланс ({why_reb}): {s} → {tv / V * 100:.0f}%.", actions=actions)
            for s, p in {p["symbol"]: p for p in self.rp.positions()}.items():     # core/mid holdings outside the plan (e.g. bear: fewer slots)
                if self._role(p) in ("core", "mid") and s not in {x[0] for x in plan} and s in price:
                    self._trade(now, s, 0, price[s], f"Ребаланс ({why_reb}): {s} вне плана — продаём.", actions=actions)
            for s, tv, b, why in sorted(plan, key=lambda x: -(x[1] - cur.get(x[0], 0.0))):
                if tv > cur.get(s, 0.0):
                    self._trade(now, s, tv, price[s], why if s not in cur else f"Ребаланс ({why_reb}): {s} → {tv / V * 100:.0f}%.", bucket=b, actions=actions)
            self._set("last_rebalance", now)
            if first:
                self._set("started", now)
        # --- 3. meme slots: every run, bull mode only, buy only after a ≥ 30 % dip from the 30-day high AND with the spot meme status «small»
        meme_c = bal_meme_candidates(rows, memes, self.conn, now)
        for c in meme_c:
            c["status"] = (mstat.get(c["symbol"]) or {}).get("status"); c["status_ru"] = (mstat.get(c["symbol"]) or {}).get("status_ru")
        if mode_s == "bull" and self._m("started") is not None:
            held = {p["symbol"]: p for p in self.rp.positions()}
            n_meme = sum(1 for p in held.values() if self._role(p) == "meme")
            V = self.value(price)
            for c in sorted([c for c in meme_c if c["dip"] and c["status"] == "small" and c["symbol"] not in held and c["symbol"] not in cool],
                            key=lambda c: c["from_high30_pct"]):
                if n_meme >= BAL["meme_n"]:
                    break
                self._trade(now, c["symbol"], BAL["meme_w"] * V, price[c["symbol"]], f"Мем на просадке: {c['symbol']} {c['from_high30_pct']:.0f}% от максимума "
                            f"за 30 дней, проверка мемов — «можно на небольшую сумму» — {BAL['meme_w']*100:.0f}%. Стоп −35%, на ×2 забираем тело, остаток до отката 35% от пика.", bucket="high:meme", actions=actions)
                n_meme += 1
        self.conn.commit()
        return self.snapshot(price, live, byrow, mode_s, actions, now, {"mid": mid_c[:8], "leaders": lead_c[:5] or bal_leader_candidates(rows, cards, memes, exclude={c["symbol"] for c in mid_c[:BAL["mid_n"]]})[:5], "memes": meme_c[:4]})

    def snapshot(self, price, live, byrow, mode_s, actions, now, cands) -> dict:
        rp = self.rp
        pos = rp.positions()
        V = self.value(price) or 1.0
        T = bal_targets(mode_s)
        idx = round(100 * V, 2)
        rp.conn.execute(f"INSERT OR REPLACE INTO {rp.T_VAL} VALUES(?,?)", (now, idx))
        rp.conn.commit()
        hist = [{"time": t, "v": v} for t, v in rp.conn.execute(f"SELECT time,index_value FROM {rp.T_VAL} ORDER BY time DESC LIMIT 60").fetchall()][::-1]
        mult = BAL["bear_mult"] if mode_s == "bear" else {"core": 1.0, "mid": 1.0, "high": 1.0}
        out_pos = []
        for p in pos:
            s, role = p["symbol"], self._role(p)
            px = price.get(s)
            mv = self._mv(p, px)
            sleeve = (p["bucket"] or "").split(":")[0]
            tw = (BAL["core"].get(s, 0) * mult["core"] if role == "core" else BAL["mid_w"] * mult["mid"] if role == "mid"
                  else (BAL["anchor_w"] if role == "anchor" else BAL["meme_w"] if role == "meme" else BAL["lead_w"]) * mult["high"])
            ret = px / p["entry"] - 1 if px and p["entry"] else None
            r = byrow.get(s) or {}
            out_pos.append({**p, "bucket": sleeve, "role": role, "sleeve": SLEEVE_RU.get(sleeve, sleeve), "role_ru": ROLE_RU.get(role, role),
                            "name": r.get("name", s), "weight": round(mv / V, 4), "cost_weight": round(p["weight"], 6), "target_weight": round(tw, 4),
                            "price": px, "price_live": s in live, "pnl_pct": round(ret * 100, 2) if ret is not None else None,
                            "pnl_usd": round(10_000 * (mv - p["weight"]), 2), "value_usd": round(10_000 * mv, 2),
                            "score": r.get("score"), "why": (r.get("why") or {}).get("mid"),
                            "reco": bal_reco(role, p, px, mv / V, tw)})
        W = self.sleeves_now(price)
        pending = self._m("flip_since")
        return {"key": BAL["key"], "name": BAL["name"], "about": BAL_ABOUT, "status": BAL["status"], "experiment": True,
                "positions": out_pos, "cash_pct": round(rp.cash() / V * 100), "invested_pct": round((V - rp.cash()) / V * 100),
                "index": idx, "equity_usd": round(10_000 * V, 2), "start_usd": 10_000, "history": hist, "actions": actions,
                "recent_actions": [dict(zip(("time", "symbol", "action", "weight", "price", "reason"), r)) for r in
                                   rp.conn.execute(f"SELECT time,symbol,action,weight,price,reason FROM {rp.T_ACT} ORDER BY time DESC LIMIT 20").fetchall()],
                "mode": mode_s, "mode_ru": "рост рынка — полные доли" if mode_s == "bull" else "защита — BTC в медвежьем режиме",
                "mode_pending": {"to": "bear" if mode_s == "bull" else "bull", "since": int(pending),
                                 "confirm_at": int(pending + BAL["regime_confirm_h"] * 3_600_000)} if pending else None,
                "sleeves": {k: {"label": SLEEVE_RU.get(k, "кэш"), "target_pct": round(T[k] * 100, 1), "now_pct": round(W.get(k, 0) * 100, 1)} for k in ("core", "mid", "high", "cash")},
                "started": int(self._m("started")) if self._m("started") else None, "last_rebalance": int(self._m("last_rebalance")) if self._m("last_rebalance") else None,
                "rules_ru": BAL_RULES, "candidates": cands, "backtest": BAL_BACKTEST}


def bal_reco(role: str, p: dict, px: float | None, w_now: float, w_target: float) -> dict:
    """Server-side recommendation in the panel's format {k: hold|add|trim|sell, t, why} (the panel's spotReco has no rules for this profile)."""
    if not px or not p.get("entry"):
        return {"k": "hold", "t": "Нет цены", "why": "Монеты нет в свежем снимке CoinGecko — показываем последнюю известную цену."}
    ret = px / p["entry"] - 1
    high = max(p.get("high") or px, px)
    off = w_target > 0 and abs(w_now - w_target) > w_target * BAL["drift"]
    if role == "core":
        if off:
            return {"k": "trim" if w_now > w_target else "add", "t": "Ребаланс", "why": f"Доля {w_now*100:.0f}% при плане {w_target*100:.0f}% — на ребалансе вернём."}
        return {"k": "hold", "t": "Держать", "why": f"Ядро без стопа: держим, раз в месяц возвращаем долю {w_target*100:.0f}%."}
    if role == "mid":
        if ret <= BAL["mid_stop"] + 0.05:
            return {"k": "sell", "t": "У стопа −30%", "why": f"Сейчас {ret*100:+.0f}% от входа; на −30% продаём, место ждёт ребаланса."}
        if off:
            return {"k": "trim" if w_now > w_target else "add", "t": "Ребаланс", "why": f"Доля {w_now*100:.0f}% при плане {w_target*100:.0f}% — вернём на ребалансе."}
        return {"k": "hold", "t": "Держать", "why": "Крупный альт в тренде: стоп −30% от входа, раз в месяц возвращаем 6%."}
    if not p.get("fixes"):
        if ret <= BAL["high_stop"] + 0.07:
            return {"k": "sell", "t": "У стопа −35%", "why": f"Сейчас {ret*100:+.0f}% от входа; на −35% продаём целиком."}
        if px >= p["entry"] * (BAL["body_x"] - 0.15):
            return {"k": "trim", "t": "Скоро ×2", "why": f"Сейчас {ret*100:+.0f}%: на ×2 продаём половину — возвращаем тело."}
        return {"k": "hold", "t": "Держать", "why": "Высокий риск: стоп −35%, на ×2 забираем тело."}
    dd = 1 - px / high
    if dd >= BAL["trail"] - 0.07:
        return {"k": "sell", "t": "У трейлинга", "why": f"{dd*100:.0f}% ниже пика; на откате 35% остаток продаём."}
    return {"k": "hold", "t": "Едет бесплатно", "why": "Тело забрано; остаток без потолка, выход при откате 35% от пика."}


# ----------------------------------------------------------------------------- 4th profile: the meme desk's paper account in this schema
MEME_ABOUT = ("Бумажный счёт $10 000 мем-деска: фьючерсы мемов на Hyperliquid без плеча (по риску как спот). Эксперимент «откуп "
              "обвала»: покупаем после падения на 25% от максимума за сутки, 10% счёта на сделку, до 6 сделок. На +50% продаём 67% — "
              "тело вернулось; остаток — до отката 35% от пика по 4-часовым свечам, без потолка, не дольше 60 дней. Тело не взято за "
              "3 дня — выходим. Стоп −25%. Комиссии и фандинг учтены.")


def _meme_sym(coin: str) -> tuple[str, float]:
    return (coin[1:], 1000.0) if coin.startswith("k") and coin[1:].isupper() else (coin, 1.0)


def meme_profile(m: dict | None, now: int | None = None) -> dict:
    """The «Мем-портфель» (memes.json → account) in the spot-profile schema. Prices of ×1000 contracts (kPEPE …) are per one token."""
    import re
    now = now or int(time.time() * 1000)
    base = {"key": "meme", "name": "Мем-портфель", "about": MEME_ABOUT, "status": "эксперимент", "experiment": True, "kind": "paper_perps",
            "venue": "Hyperliquid, фьючерсы без плеча", "start_usd": 10_000, "source": "web/memes.json → account"}
    a = (m or {}).get("account")
    if not a:
        return {**base, "error": "мем-деск ещё не отработал", "positions": [], "index": 100.0, "cash_pct": 100, "invested_pct": 0,
                "history": [], "actions": [], "recent_actions": []}
    start = a.get("start_equity") or 10_000.0
    eq = a.get("equity") or start
    lim = a.get("limits") or {}
    pos = []
    for p in a.get("positions") or []:
        sym, k = _meme_sym(p["coin"])
        px, entry = (p.get("px") or p["entry_px"]) / k, p["entry_px"] / k
        body = bool(p.get("body_taken") or p.get("tp_done"))
        ret = px / entry - 1 if entry else 0.0
        if not body:
            if ret <= (lim.get("stop_pct", -25) / 100) + 0.05:
                reco = {"k": "sell", "t": "У стопа −25%", "why": f"Сейчас {ret*100:+.0f}% от входа; стоп сработает автоматически."}
            elif ret >= (lim.get("take_profit_pct", 50) / 100) - 0.1:
                reco = {"k": "trim", "t": "Скоро тело", "why": "На +50% счёт сам продаст 67% — вложенное вернётся."}
            else:
                left = ((p.get("entry_time") or now) + (lim.get("pre_body_limit_h") or 72) * 3_600_000 - now) / 3_600_000
                reco = {"k": "hold", "t": "Держать", "why": f"Ждём +50% для тела; если не будет ещё {max(0, left):.0f} ч — выход целиком."}
        else:
            reco = {"k": "hold", "t": "Едет бесплатно", "why": "Тело забрано; остаток продаём при откате 35% от пика (4-часовые свечи)."}
        pos.append({"symbol": sym, "coin": p["coin"], "contract_mult": k, "name": sym, "bucket": "meme", "role": "meme", "sleeve": "мемы",
                    "weight": round((p.get("value") or 0) / eq, 4) if eq else 0, "entry": entry, "entry_time": p.get("entry_time"),
                    "high": (p.get("peak_px") or p["entry_px"]) / k, "status": "OPEN", "reason": p.get("why"), "price": px,
                    "price_live": p.get("price_live"), "pnl_pct": p.get("pnl_pct"), "pnl_usd": p.get("pnl_usd"), "value_usd": p.get("value"),
                    "fixes": 1 if body else 0, "stop_px": (p["stop_px"] / k) if p.get("stop_px") else None,
                    "body_px": (p["body_px"] / k) if p.get("body_px") else None, "trail_px": (p["trail_px"] / k) if p.get("trail_px") else None,
                    "exit_by": p.get("exit_by"), "remaining_pct": p.get("remaining_pct"), "reco": reco})
    acts = []
    for p in a.get("positions") or []:
        sym, k = _meme_sym(p["coin"])
        acts.append({"time": p.get("entry_time"), "symbol": sym, "action": "BUY", "weight": lim.get("size_pct", 10) / 100, "price": p["entry_px"] / k, "reason": p.get("why")})
    for c in a.get("closed") or []:
        sym, k = _meme_sym(c["coin"])
        acts.append({"time": c.get("entry_time"), "symbol": sym, "action": "BUY", "weight": lim.get("size_pct", 10) / 100, "price": c["entry_px"] / k,
                     "reason": "вход по правилу «откуп обвала мема»"})
        acts.append({"time": c.get("exit_time"), "symbol": sym, "action": "SELL", "weight": 0, "price": c["exit_px"] / k,
                     "reason": f"{c.get('why')}; итог сделки {c.get('pnl_pct', 0):+.1f}% (${c.get('pnl_usd', 0):+,.0f})"})
    for e in a.get("events") or []:
        mt = re.match(r"Вернули вложенное по (\S+): продали (\d+)% по \$([\d.,eE+-]+)", e.get("text") or "")
        if mt:
            sym, k = _meme_sym(mt.group(1))
            try:
                px = float(mt.group(3).replace(",", "")) / k
            except ValueError:
                px = None
            acts.append({"time": e["time"], "symbol": sym, "action": "TRIM", "weight": None, "price": px, "reason": e["text"]})
    acts = sorted([x for x in acts if x.get("time")], key=lambda x: -x["time"])[:20]
    asof = (m or {}).get("asof") or now
    hist = [{"time": t, "v": round(100 * v / start, 2)} for t, v in (a.get("history") or [])][-60:]
    cash = a.get("cash", eq)
    return {**base, "asof": asof, "stale": now - asof > 3 * 3_600_000, "positions": pos,
            "cash_pct": round(cash / eq * 100) if eq else 100, "invested_pct": round((eq - cash) / eq * 100) if eq else 0,
            "index": round(100 * eq / start, 2), "equity_usd": round(eq, 2), "history": hist,
            "actions": [x for x in acts if x["time"] >= asof], "recent_actions": acts,
            "stats": a.get("stats"), "rules_ru": [a.get("rules")] if a.get("rules") else [], "limits": lim}


def load_meme_account(conn: sqlite3.Connection, now: int, path: str = "web/memes.json") -> dict | None:
    """memes.json written minutes ago by the same server run; if it is missing or older than 3 h — the account from the DB tables
    (positions priced at entry, flagged stale)."""
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        if m.get("account") and now - (m.get("asof") or 0) < 3 * 3_600_000:
            return m
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import memes
        memes._ensure(conn)
        return memes.snapshot(conn, now, {}, {}, [], [], [], {})
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------------- one-time reset + rebuild (owner, 17.10.2026)
# «Да и обнови просто споты тоже. Пересобери, пересмотри разные портфели с учётом наших мемкоинов»: cons / mod / high restart from
# $10 000 under the rules of 17.10 (cons — market-weight rebalance; mod — no memes; high — memes only by the spot meme status, capped).
# The old state of each goes to `spot_archive` (id "<migration>:<profile>"), the migration id to `migrations` → runs exactly once.
# «bal» is not reset: it started on 17.10 16:29 UTC, already under its written rules.
SPOT_RESET_ID = "spot-reset-2026-10-17"
SPOT_RESET_PROFILES = ("cons", "mod", "high")
SPOT_RESET_UNTIL = 1_793_491_200_000          # 2026-11-01 UTC — later the migration is only marked «skipped»
SPOT_RESET_NEW_RU = {
    "cons": "ребаланс теперь по рыночным долям (раньше сравнивались доли по цене покупки)",
    "mod": "те же правила отбора, но без мемов и без монет после рывка (+40% за 7 дней или +120% за 30)",
    "high": "мемы — только со статусом «можно на небольшую сумму» (проверки пройдены, цена на 30%+ ниже максимума за 30 дней), "
            "5% на мем, все мемы вместе не больше 25%; тело забираем на ×2",
}


def _spot_tables(conn: sqlite3.Connection, key: str) -> dict:
    if key == "mod":
        SpotPortfolio(conn)
        return {"pos": "spot_portfolio", "act": "spot_actions", "val": "spot_value", "meta": "spot_mod_meta"}
    from .spot_profiles import RiskPortfolio
    rp = RiskPortfolio(conn, key)
    return {"pos": rp.T_POS, "act": rp.T_ACT, "val": rp.T_VAL, "meta": rp.T_META}


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict]:
    cur = conn.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def reset_spot(conn: sqlite3.Connection, now: int, price: dict, memes=(), mig_id: str = SPOT_RESET_ID,
               profiles=SPOT_RESET_PROFILES, until: int = SPOT_RESET_UNTIL) -> dict:
    """Idempotent one-time reset → {profile: reset_info} for the portfolios reset by THIS call ({} once applied).
    Open positions are valued at `price` (the run's snapshot; if a held coin has no price — the last recorded index) and archived
    with their result; then positions, actions, value history and meta (cash, realized) are emptied, so the next step rebuilds the
    portfolio from $10 000 under the current rules. A profile with no history (fresh database) is skipped."""
    from .paper_desk import RESET_DAY_RU, _usd_ru
    conn.execute("CREATE TABLE IF NOT EXISTS migrations(id TEXT PRIMARY KEY, time INTEGER, note TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS spot_archive(id TEXT PRIMARY KEY, profile TEXT, time INTEGER, json TEXT)")
    conn.commit()
    if conn.execute("SELECT 1 FROM migrations WHERE id=?", (mig_id,)).fetchone():
        return {}
    if now > until:
        conn.execute("INSERT INTO migrations VALUES(?,?,?)", (mig_id, now, "skipped: too late"))
        conn.commit()
        return {}
    tables = {k: _spot_tables(conn, k) for k in profiles}
    memes = {m.upper() for m in memes}
    done: dict = {}
    try:
        for k, T in tables.items():
            if not sum(conn.execute(f"SELECT COUNT(*) FROM {T[x]}").fetchone()[0] for x in ("pos", "act", "val")):
                continue
            pos = _rows(conn, f"SELECT * FROM {T['pos']}")
            acts = _rows(conn, f"SELECT * FROM {T['act']} ORDER BY time")
            vals = conn.execute(f"SELECT time, index_value FROM {T['val']} ORDER BY time").fetchall()
            meta = {a: b for a, b in conn.execute(f"SELECT k, v FROM {T['meta']}").fetchall()}
            held = [p for p in pos if p["status"] == "OPEN"]
            for p in held:
                px = price.get(p["symbol"])
                p["price_at_reset"] = px
                p["pnl_pct"] = round((px / p["entry"] - 1) * 100, 2) if px and p["entry"] else None
                p["value_usd"] = round(10_000 * p["weight"] * px / p["entry"], 2) if px and p["entry"] else None
            if k == "mod":
                value = 1 + (meta.get("realized") or 0.0) + sum(p["weight"] * (price[p["symbol"]] / p["entry"] - 1) for p in held if price.get(p["symbol"]) and p["entry"])
            else:
                value = (meta["cash"] if "cash" in meta else 1.0) + sum(p["weight"] * price[p["symbol"]] / p["entry"] for p in held if price.get(p["symbol"]) and p["entry"])
            basis = "цены запуска"
            if any(not price.get(p["symbol"]) for p in held) and vals:
                value, basis = vals[-1][1] / 100, "последняя оценка портфеля"
            pnl = round(10_000 * (value - 1), 2)
            started = acts[0]["time"] if acts else (vals[0][0] if vals else now)
            syms = [p["symbol"] for p in held]
            new = SPOT_RESET_NEW_RU.get(k, "текущие правила")
            if k == "mod" and [s for s in syms if s in memes]:
                new += f" (в прошлом составе мем — {', '.join(s for s in syms if s in memes)})"
            d0 = datetime.fromtimestamp(started / 1000, tz=timezone.utc).strftime("%d.%m")
            note = (f"Портфель пересобран {RESET_DAY_RU} по решению владельца: прошлый счёт с {d0}"
                    + (f" ({', '.join(syms)})" if syms else "") + f" — в архиве, итог {_usd_ru(pnl)}. Новый старт $10 000: {new}.")
            info = {"time": now, "archived_pnl_usd": pnl, "note_ru": note, "migration": mig_id, "since_before": started}
            arch = {"profile": k, "migration": mig_id, "time": now, "start_usd": 10_000, "value_at_reset": round(value, 6), "value_basis": basis,
                    "reset": info, "positions": pos, "actions": acts, "history": [{"time": t, "v": v} for t, v in vals], "meta": meta}
            conn.execute("INSERT INTO spot_archive VALUES(?,?,?,?)", (f"{mig_id}:{k}", k, now, json.dumps(arch, ensure_ascii=False)))
            for x in ("pos", "act", "val", "meta"):
                conn.execute(f"DELETE FROM {T[x]}")
            done[k] = info
        conn.execute("INSERT INTO migrations VALUES(?,?,?)", (mig_id, now, "spot reset: " + (", ".join(f"{k} {v['archived_pnl_usd']:+.2f}" for k, v in done.items()) or "none (no history)")))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return done


def spot_reset_info(conn: sqlite3.Connection, key: str) -> dict | None:
    try:
        row = conn.execute("SELECT json FROM spot_archive WHERE profile=? ORDER BY time DESC LIMIT 1", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return json.loads(row[0]).get("reset") if row else None


def load_spot_memes(now: int, path: str = "web/memes.json") -> dict:
    """{SYM: spot meme row (status / status_ru / why_ru / score / price / from_high30_pct)} written by the meme desk minutes ago in
    the same server run; {} if missing or older than 3 h — then «Высокий риск» buys no memes."""
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        if now - (m.get("asof") or 0) < 3 * 3_600_000:
            return {(r.get("symbol") or "").upper(): r for r in m.get("spot_memes") or [] if r.get("symbol")}
    except Exception:  # noqa: BLE001
        pass
    return {}


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
    markets = []
    try:   # the research department fetched the same snapshot minutes ago — reuse it instead of hitting CoinGecko twice (rate limits)
        mk = json.load(open("web/markets.json", encoding="utf-8"))
        if mk.get("available") and now - mk.get("asof", 0) < 25 * 60_000:
            markets = [dict(m) for m in mk["top"] if not m.get("stable") and not m.get("wrapped")]
    except Exception:  # noqa: BLE001
        markets = []
    if not markets:
        markets = coingecko_markets()
    hl = hyperliquid_universe()
    if not markets:
        return {"asof": now, "available": False, "error": "CoinGecko недоступен", "hyperliquid": hl.get("available", False)}
    btc = next((r for r in markets if r["symbol"] == "BTC"), None)
    if btc_bull is None:
        btc_bull = bool(btc and (btc.get("chg200") or 0) > 0)
    rows = score_universe(markets, hl, btc_bull)
    pk = picks(rows)
    cards = []
    try:
        with open("web/research.json", encoding="utf-8") as f:
            cards = json.load(f).get("cards") or []
    except Exception:  # noqa: BLE001
        cards = []
    # memes: the meme desk's HL list + research cards tagged «мем» + CoinGecko's meme category (cached by meme_spot) + spot meme rows
    from . import meme_spot
    meme_spot.ensure(conn)
    cg_t, cg_rows = meme_spot._cache_any(conn, "cg_markets")
    cg_rows = cg_rows or []
    spot_memes = load_spot_memes(now)
    memes = meme_symbols(cards, [(x.get("symbol") or "").upper() for x in cg_rows]) | set(spot_memes)
    price = {r["symbol"]: r["price"] for r in markets if r.get("price")}
    # prices for coins outside the CoinGecko top-200: memes (CoinGecko meme list ≤ 6 h old, spot meme rows), then Hyperliquid spot
    meme_px = {(x.get("symbol") or "").upper(): x.get("current_price") for x in (cg_rows if cg_t and now - cg_t < 6 * 3_600_000 else []) if x.get("current_price")}
    meme_px.update({s: r["price"] for s, r in spot_memes.items() if r.get("price")})
    hl_px = {k: v["price"] for k, v in (hl.get("spot") or {}).items() if v.get("price")}
    px_all = {**hl_px, **meme_px, **price}
    try:      # one-time reset + rebuild of cons / mod / high (owner, 17.10) — no-op once applied
        reset_spot(conn, now, px_all, memes)
    except Exception as e:  # noqa: BLE001
        print("spot reset failed (will retry next run):", e)
    state = SpotPortfolio(conn).step(rows, picks([r for r in rows if r["symbol"] not in memes and not overheated(r.get("chg7"), r.get("chg30"))]),
                                 btc_bull, now, memes=memes)
    state["reset"] = spot_reset_info(conn, "mod")
    note = market_note(btc_bull, btc, pk, state)
    llm_note = ""
    # 17.10: this call ran every 30 min and alone used up Groq's free 200k tokens/day (channel fact-check then failed with 429).
    # Now at most once per 6 hours; in between the last note is reused.
    conn.execute("CREATE TABLE IF NOT EXISTS portfolio_llm(k TEXT PRIMARY KEY, t INTEGER, note TEXT)")
    cached = conn.execute("SELECT t, note FROM portfolio_llm WHERE k='note'").fetchone()
    t_now = now or int(time.time() * 1000)
    if cached and t_now - cached[0] < 6 * 3_600_000:
        llm_note = cached[1] or ""
    elif llm is not None and bool(getattr(llm, "available", False)):
        try:
            llm_note = llm.complete(PORTFOLIO_SYSTEM, json.dumps({"btc_bull": btc_bull, "btc": btc, "picks": {h: pk[h][:5] for h in HORIZONS},
                                                                  "portfolio": {k: state[k] for k in ("positions", "cash_pct", "invested_pct", "index", "actions")}},
                                                                 ensure_ascii=False, default=str)).strip()
            conn.execute("INSERT OR REPLACE INTO portfolio_llm VALUES('note', ?, ?)", (t_now, llm_note)); conn.commit()
        except Exception:  # noqa: BLE001
            llm_note = cached[1] if cached else ""
    hl_spot_list = sorted(hl.get("spot", {}).items(), key=lambda kv: -kv[1]["vol24"])[:15]
    # the other two risk profiles (conservative / high risk) run on the same prices; research cards come from web/research.json
    from .spot_profiles import RiskPortfolio, PROFILES as RP
    hl_movers = [{"symbol": k, **v} for k, v in hl_spot_list]
    profiles = {"mod": {"key": "mod", "name": "Умеренный", "about": "Ядро BTC/ETH + до 5 среднесрочных идей по 8 % и до 3 коротких по 5 %; стоп −15 %, фиксация половины на +40 %, кэш ≥ 10 %. Без мемов и без монет после рывка (+40 % за 7 дней или +120 % за 30).",
                        **{k: state[k] for k in ("positions", "cash_pct", "invested_pct", "index", "history", "actions", "recent_actions", "reset")}}}
    for key in RP:
        try:
            profiles[key] = RiskPortfolio(conn, key).step(dict(px_all), cards, hl_movers, now, memes=memes, meme_status=spot_memes if key == "high" else None)
            profiles[key]["reset"] = spot_reset_info(conn, key)
        except Exception as e:  # noqa: BLE001
            profiles[key] = {"key": key, "name": RP[key]["name"], "about": RP[key]["about"], "error": str(e), "positions": [], "index": 100, "cash_pct": 100, "invested_pct": 0, "history": [], "actions": [], "recent_actions": []}
    empty = {"positions": [], "index": 100.0, "cash_pct": 100, "invested_pct": 0, "history": [], "actions": [], "recent_actions": []}
    try:      # 4th: the meme desk's paper account (written to web/memes.json earlier in the same server run)
        profiles["meme"] = meme_profile(load_meme_account(conn, now), now)
    except Exception as e:  # noqa: BLE001
        profiles["meme"] = {"key": "meme", "name": "Мем-портфель", "about": MEME_ABOUT, "error": str(e), **empty}
    try:      # 5th: «Сбалансированный · максимум потенциала» (not reset on 17.10 — it started that day under its own rules)
        profiles["bal"] = BalancedPortfolio(conn).step(rows, cards, btc_bull, memes, now, meme_status=spot_memes)
    except Exception as e:  # noqa: BLE001
        profiles["bal"] = {"key": "bal", "name": BAL["name"], "about": BAL_ABOUT, "error": str(e), **empty}
    from .spot_profiles import IDEAS, IDEAS_ABOUT, IdeasPortfolio, load_ideas
    try:      # 6th: «По рекомендациям» — buys what the spot ideas table says (web/ideas.json of the PREVIOUS run: the ideas step runs
              # after this one); prices: the same live sources as the other profiles, the source a coin was bought with is kept
        profiles["ideas"] = IdeasPortfolio(conn).step({"cg": price, "meme": meme_px, "hl": hl_px}, load_ideas(), now)
    except Exception as e:  # noqa: BLE001
        profiles["ideas"] = {"key": "ideas", "name": IDEAS["name"], "about": IDEAS_ABOUT, "experiment": True, "error": str(e), **empty}
    return {"asof": now, "available": True, "btc_bull": btc_bull, "universe": len(rows), "hyperliquid": hl.get("available", False),
            "picks": pk, "portfolio": state, "portfolios": profiles, "portfolio_order": ["cons", "mod", "high", "meme", "bal", "ideas"],
            "note": llm_note or note, "note_source": "llm" if llm_note else "rules",
            "hl_spot_top": [{"symbol": k, "vol24": v["vol24"], "price": v["price"], "chg24": v["chg24"],
                             "score": next((r["score"] for r in rows if r["symbol"] == k), None)} for k, v in hl_spot_list],
            "sources": ["CoinGecko /coins/markets", "Hyperliquid info API"]}


PORTFOLIO_SYSTEM = """Ты — портфельный консультант CRYPTO NOVΛ по спотовому рынку. Пиши по-русски, просто и конкретно, 6–10 предложений.
Используй ТОЛЬКО числа и факты из JSON: режим BTC, оценки монет, позиции, действия и их причины. Ничего не выдумывай и не добавляй
монеты, которых нет в данных. Структура: 1) что с рынком сегодня, 2) что делаем с портфелем и почему, 3) какие идеи на короткий,
средний и долгий срок, 4) чего ждём и что отменит план. Без эмодзи и без обещаний доходности."""
