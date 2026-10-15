"""Coin research department — the "анкета": one scorecard per coin (economics, team & activity, community, market,
narrative), a 0–100 rating, a risk class, theoretical take-profit points and long-term scenarios.

Data: CoinGecko /coins/markets (top-100, every run), /global, alternative.me Fear & Greed, Hyperliquid spot/perp lists,
and /coins/{id} details (community, developer, supply) fetched in rotation — a few coins per run so the free API is
never hammered; cards live in data/agents.sqlite (coin_cards) and age gracefully.

Everything here is a rule-based model: the rating is a weighted questionnaire, the targets are fixed rules
(not predictions), the long-term scenarios are compound-growth tables under stated assumptions. The panel says so.
Outputs: web/markets.json (header strip + overview), web/research.json (cards). Stdlib only.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
import urllib.request

CG = "https://api.coingecko.com/api/v3"
HL = "https://api.hyperliquid.xyz/info"
FNG = "https://api.alternative.me/fng/?limit=8"
DETAILS_PER_RUN = 10
CARD_TTL_MS = 3 * 86400_000

STABLES = {"USDT", "USDC", "DAI", "USDE", "FDUSD", "USDS", "TUSD", "PYUSD", "USD1", "BUSD", "USDD", "FRAX", "EURC", "USDG", "RLUSD"}
WRAPPED = {"WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "RETH", "WBETH", "LBTC", "BSC-USD", "SOLVBTC", "EZETH", "RSETH", "CBETH", "TBTC",
           "XAUT", "PAXG", "FIGR_HELOC", "SUSDE", "SUSDS", "BUIDL", "USDTB", "JITOSOL", "MSOL", "BNSOL"}   # + tokenized gold / yield wrappers / RWA notes
NARRATIVES = [("Meme", "мем"), ("Artificial Intelligence", "ИИ"), ("Real World Assets", "RWA"), ("Hyperliquid Ecosystem", "Hyperliquid"),
              ("Layer 1", "L1"), ("Layer 2", "L2"), ("Decentralized Finance", "DeFi"), ("Gaming", "игры"), ("Exchange-based", "биржевой токен"),
              ("Oracle", "оракул"), ("Privacy", "приватность"), ("Infrastructure", "инфраструктура"), ("Payment", "платежи"), ("Smart Contract Platform", "платформа")]


def _get(url: str, data: dict | None = None, timeout: float = 25):
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,
                                     headers={"Content-Type": "application/json", "User-Agent": "cryptonova-research/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------------- market snapshot (header strip + overview)
def markets_top(n: int = 150) -> list[dict]:
    rows = _get(f"{CG}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page={n}&page=1&price_change_percentage=24h,7d,30d,200d,1y&sparkline=false") or []
    out = []
    for r in rows:
        sym = (r.get("symbol") or "").upper()
        out.append({"id": r.get("id"), "symbol": sym, "name": r.get("name"), "rank": r.get("market_cap_rank"), "price": r.get("current_price"),
                    "mcap": r.get("market_cap"), "fdv": r.get("fully_diluted_valuation"), "volume": r.get("total_volume"),
                    "chg24": r.get("price_change_percentage_24h_in_currency"), "chg7": r.get("price_change_percentage_7d_in_currency"),
                    "chg30": r.get("price_change_percentage_30d_in_currency"), "chg200": r.get("price_change_percentage_200d_in_currency"),
                    "chg1y": r.get("price_change_percentage_1y_in_currency"), "ath": r.get("ath"), "ath_chg": r.get("ath_change_percentage"),
                    "ath_date": r.get("ath_date"), "atl_date": r.get("atl_date"), "circ": r.get("circulating_supply"), "total": r.get("total_supply"), "max": r.get("max_supply"),
                    "image": r.get("image"), "stable": sym in STABLES, "wrapped": sym in WRAPPED})
    return out


def global_stats() -> dict:
    g = (_get(f"{CG}/global") or {}).get("data") or {}
    return {"total_mcap": (g.get("total_market_cap") or {}).get("usd"), "mcap_chg24": g.get("market_cap_change_percentage_24h_usd"),
            "btc_dom": (g.get("market_cap_percentage") or {}).get("btc"), "eth_dom": (g.get("market_cap_percentage") or {}).get("eth"),
            "volume24": (g.get("total_volume") or {}).get("usd"), "coins": g.get("active_cryptocurrencies")}


def fear_greed() -> dict:
    d = (_get(FNG) or {}).get("data") or []
    hist = [{"v": int(x["value"]), "t": int(x["timestamp"]) * 1000, "label": x.get("value_classification")} for x in d if x.get("value")]
    RU = {"Extreme Fear": "крайний страх", "Fear": "страх", "Neutral": "нейтрально", "Greed": "жадность", "Extreme Greed": "крайняя жадность"}
    if not hist:
        return {"value": None}
    return {"value": hist[0]["v"], "label": RU.get(hist[0]["label"], hist[0]["label"]), "yesterday": hist[1]["v"] if len(hist) > 1 else None,
            "week": hist[6]["v"] if len(hist) > 6 else None, "history": hist[::-1]}


def hyperliquid_lists() -> dict:
    perps, spot = set(), {}
    m = _get(HL, {"type": "metaAndAssetCtxs"})
    if m and len(m) == 2:
        for u in m[0].get("universe", []):
            perps.add((u.get("name") or "").upper())
    s = _get(HL, {"type": "spotMetaAndAssetCtxs"})
    if s and len(s) == 2:
        tokens = {t["index"]: t for t in s[0].get("tokens", [])}
        for u, c in zip(s[0].get("universe", []), s[1]):
            try:
                base = tokens[u["tokens"][0]]["name"].upper()
                px = float(c.get("markPx") or 0); prev = float(c.get("prevDayPx") or 0); vol = float(c.get("dayNtlVlm") or 0)
                if px and vol:
                    spot[base] = {"price": px, "chg24": (px / prev - 1) * 100 if prev else None, "vol24": vol}
            except Exception:  # noqa: BLE001
                continue
    return {"perps": sorted(perps), "spot": spot, "available": bool(perps or spot)}


# ----------------------------------------------------------------------------- scorecard
def _log_score(x, lo, hi) -> float:
    """0–100 on a log scale between lo and hi."""
    if not x or x <= 0:
        return 0.0
    v = (math.log10(x) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    return max(0.0, min(100.0, v * 100))


def _lin(x, lo, hi) -> float:
    if x is None:
        return 50.0
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def risk_class(m: dict, det: dict | None, hl: dict) -> str:
    sym = m["symbol"]
    if sym in ("BTC", "ETH", "SOL"):
        return "cons"
    cats = (det or {}).get("categories") or []
    age = (det or {}).get("age_days")
    if any("Meme" in c for c in cats) or (m.get("mcap") or 0) < 5e8 or (age is not None and age < 365):
        return "high"
    if (m.get("rank") or 999) <= 60 and (m.get("mcap") or 0) >= 1e9:
        return "mod"
    return "high"


def build_card(m: dict, det: dict | None, hl: dict, btc_bull: bool) -> dict:
    sym = m["symbol"]
    d = det or {}
    mcap = m.get("mcap") or 0; fdv = m.get("fdv") or mcap
    circ, total, mx = m.get("circ"), m.get("total"), m.get("max")
    # economics: how much supply is still to come (dilution), FDV/MCAP, size
    dil = (1 - circ / total) if circ and total and total > 0 else 0.0
    f_eco = 0.45 * (100 - _lin(fdv / mcap if mcap else 1, 1.0, 3.0)) + 0.35 * (100 - _lin(dil, 0, 0.6)) + 0.20 * _log_score(mcap, 1e8, 1e12)
    # team & activity (developer data) + age
    dev = d.get("developer") or {}
    age = d.get("age_days")
    f_team = 0.4 * _log_score(dev.get("commits_4w") or 0, 1, 1000) + 0.3 * _log_score(dev.get("stars") or 0, 10, 100000) + 0.3 * _lin(age or 0, 0, 2000)
    if not dev and age is None:
        f_team = 40.0   # no data → neutral-low, not zero
    # community
    com = d.get("community") or {}
    f_com = 0.6 * _log_score(com.get("twitter") or 0, 1e4, 1e7) + 0.4 * _log_score(com.get("reddit") or 0, 1e3, 5e6)
    if not com:
        f_com = 40.0
    # market: momentum, liquidity, position vs ATH
    vol_ratio = (m.get("volume") or 0) / mcap if mcap else 0
    f_mkt = 0.3 * _lin(m.get("chg30"), -40, 60) + 0.25 * _lin(m.get("chg200"), -60, 150) + 0.25 * _lin(vol_ratio, 0.01, 0.3) + 0.2 * _lin(m.get("ath_chg"), -95, 0)
    # narrative: categories + Hyperliquid listing
    cats = d.get("categories") or []
    tags = [ru for en, ru in NARRATIVES if any(en.lower() in c.lower() for c in cats)]
    on_hl = sym in hl.get("spot", {}) or sym in set(hl.get("perps", []))
    if on_hl and "Hyperliquid" not in tags:
        tags.append("Hyperliquid")
    f_nar = 50 + (15 if on_hl else 0) + (10 if any(t in ("ИИ", "RWA", "Hyperliquid") for t in tags) else 0) + (5 if "L1" in tags or "платформа" in tags else 0)
    f_nar = min(100, f_nar)
    rc = risk_class(m, det, hl)
    W = {"cons": (0.30, 0.20, 0.15, 0.20, 0.15), "mod": (0.25, 0.25, 0.15, 0.20, 0.15), "high": (0.10, 0.10, 0.25, 0.35, 0.20)}[rc]
    rating = W[0] * f_eco + W[1] * f_team + W[2] * f_com + W[3] * f_mkt + W[4] * f_nar
    if m.get("stable") or m.get("wrapped"):
        rating = 0
    price = m.get("price") or 0
    ath = m.get("ath") or price
    # theoretical take-profit points — rules, not predictions
    if rc == "high":
        targets = [{"name": "вернуть тело", "mult": 2.0, "sell": "50%", "why": "при +100% продаём половину — дальше едем на прибыль"},
                   {"name": "фиксация 2", "mult": 3.0, "sell": "50% остатка", "why": "каждые +50% от предыдущей точки — половина остатка"},
                   {"name": "фиксация 3", "mult": 4.5, "sell": "50% остатка", "why": "хвост держим, пока идёт движение; стоп подтягиваем"}]
        stop = -0.30; hold = "до 30 дней или пока идёт движение"
    elif rc == "mod":
        targets = [{"name": "фиксация половины", "mult": 1.4, "sell": "50%", "why": "+40% — правило портфеля"},
                   {"name": "возврат к максимуму", "mult": max(1.45, ath / price) if price else 1.8, "sell": "25%", "why": "у исторического максимума продавцы"},
                   {"name": "удвоение", "mult": 2.0, "sell": "25%", "why": "остаток держим по тренду"}]
        stop = -0.15; hold = "1–6 месяцев"
    else:
        targets = [{"name": "ребаланс", "mult": 1.5, "sell": "до плановой доли", "why": "ядро не продаём — только возвращаем долю при перекосе"},
                   {"name": "возврат к максимуму", "mult": max(1.55, ath / price) if price else 1.8, "sell": "до плановой доли", "why": "ядро держим через циклы"}]
        stop = None; hold = "годы"
    # long-term scenarios (compound growth under stated assumptions; shown as a theoretical model)
    CAGR = {"BTC": (0.05, 0.20, 0.35), "ETH": (0.00, 0.18, 0.35), "SOL": (-0.05, 0.20, 0.45)}.get(sym, (-0.10, 0.15, 0.40) if rc == "mod" else None)
    scen = None
    if CAGR:
        scen = {"assumption": {"bear": CAGR[0], "base": CAGR[1], "bull": CAGR[2]},
                "years": {y: {k: round(price * (1 + g) ** y, 6 if price < 1 else 2) for k, g in zip(("bear", "base", "bull"), CAGR)} for y in (3, 5, 10)}}
    return {"symbol": sym, "name": m.get("name"), "id": m.get("id"), "rank": m.get("rank"), "price": price, "mcap": mcap, "fdv": fdv,
            "chg24": m.get("chg24"), "chg7": m.get("chg7"), "chg30": m.get("chg30"), "chg200": m.get("chg200"), "ath_chg": m.get("ath_chg"),
            "image": m.get("image"), "risk": rc, "rating": round(rating), "on_hl": on_hl, "tags": tags, "age_days": age,
            "factors": {"economics": round(f_eco), "team": round(f_team), "community": round(f_com), "market": round(f_mkt), "narrative": round(f_nar)},
            "detail_age_h": round((time.time() * 1000 - d["updated"]) / 3600000) if d.get("updated") else None,
            "dilution": round(dil * 100), "fdv_mcap": round(fdv / mcap, 2) if mcap else None,
            "targets": [{**t, "mult": round(t["mult"], 2), "price": round(price * t["mult"], 6 if price < 1 else 2)} for t in targets], "stop_pct": stop, "hold": hold,
            "scenarios": scen, "stable": bool(m.get("stable") or m.get("wrapped"))}


class Research:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.execute("CREATE TABLE IF NOT EXISTS coin_cards(id TEXT PRIMARY KEY, symbol TEXT, updated INTEGER, data TEXT)")
        conn.commit()

    def details(self, cid: str) -> dict | None:
        row = self.conn.execute("SELECT data FROM coin_cards WHERE id=?", (cid,)).fetchone()
        return json.loads(row[0]) if row else None

    def refresh_details(self, markets: list[dict], now: int, limit: int = DETAILS_PER_RUN) -> int:
        """Fetch /coins/{id} for the `limit` coins whose card is missing or oldest; skip stables/wrapped."""
        have = {r[0]: r[1] for r in self.conn.execute("SELECT id, updated FROM coin_cards")}
        todo = [m for m in markets if m.get("id") and not m.get("stable") and not m.get("wrapped")]
        todo.sort(key=lambda m: have.get(m["id"], 0))
        n = 0
        fails = 0
        for m in todo[:limit]:
            if now - have.get(m["id"], 0) < CARD_TTL_MS / 3:
                continue
            j = _get(f"{CG}/coins/{m['id']}?localization=false&tickers=false&market_data=false&community_data=true&developer_data=true&sparkline=false")
            if not j:
                fails += 1
                time.sleep(12.0)
                if fails >= 2:      # rate-limited: stop for this run, the next run continues the rotation
                    break
                continue
            gd = j.get("genesis_date")
            age = None
            if gd:
                try:
                    y, mo, dd = (int(x) for x in gd.split("-"))
                    age = int((now / 1000 - time.mktime((y, mo, dd, 0, 0, 0, 0, 0, 0))) / 86400)
                except Exception:  # noqa: BLE001
                    age = None
            cd = j.get("community_data") or {}; dv = j.get("developer_data") or {}
            det = {"updated": now, "categories": j.get("categories") or [], "age_days": age,
                   "community": {"twitter": cd.get("twitter_followers"), "reddit": cd.get("reddit_subscribers"), "telegram": cd.get("telegram_channel_user_count")},
                   "developer": {"stars": dv.get("stars"), "forks": dv.get("forks"), "commits_4w": dv.get("commit_count_4_weeks"), "prs": dv.get("pull_requests_merged")},
                   "sentiment_up": j.get("sentiment_votes_up_percentage"), "home": ((j.get("links") or {}).get("homepage") or [""])[0],
                   "description": ((j.get("description") or {}).get("en") or "")[:400]}
            self.conn.execute("INSERT OR REPLACE INTO coin_cards VALUES(?,?,?,?)", (m["id"], m["symbol"], now, json.dumps(det, ensure_ascii=False)))
            self.conn.commit()
            n += 1
            time.sleep(7.0)
        return n


def run(conn: sqlite3.Connection, now: int | None = None, btc_bull: bool | None = None) -> tuple[dict, dict]:
    now = now or int(time.time() * 1000)
    markets = markets_top(150)
    glob = global_stats()
    fng = fear_greed()
    hl = hyperliquid_lists()
    btc = next((m for m in markets if m["symbol"] == "BTC"), None)
    if btc_bull is None:
        btc_bull = bool(btc and (btc.get("chg200") or 0) > 0)
    R = Research(conn)
    fetched = R.refresh_details(markets, now) if markets else 0
    cards = [build_card(m, R.details(m["id"]), hl, btc_bull) for m in markets if not (m.get("stable") or m.get("wrapped"))]
    cards.sort(key=lambda c: -c["rating"])
    covered = sum(1 for c in cards if c["detail_age_h"] is not None)
    hl_spot = sorted(hl.get("spot", {}).items(), key=lambda kv: -kv[1]["vol24"])[:20]
    markets_doc = {"asof": now, "available": bool(markets), "global": glob, "fng": fng, "btc_bull": btc_bull,
                   "top": [{k: m.get(k) for k in ("symbol", "name", "id", "rank", "price", "mcap", "volume", "chg24", "chg7", "chg30", "chg200", "chg1y", "ath_chg", "ath_date", "atl_date", "image", "stable", "wrapped")} for m in markets],
                   "hl_spot_top": [{"symbol": k, **v} for k, v in hl_spot], "hl_perps": hl.get("perps", [])[:200],
                   "sources": ["CoinGecko /coins/markets, /global", "alternative.me Fear & Greed", "Hyperliquid info API"]}
    research_doc = {"asof": now, "available": bool(markets), "fetched_now": fetched, "covered": covered, "total": len(cards),
                    "cards": cards, "by_risk": {k: [c["symbol"] for c in cards if c["risk"] == k][:25] for k in ("cons", "mod", "high")},
                    "method": "Анкета: экономика (FDV/капитализация, будущая эмиссия, размер), команда и активность (коммиты, звёзды, возраст), "
                              "сообщество (Twitter, Reddit), рынок (импульс 30/200 дней, ликвидность, расстояние до максимума), нарратив (категории, Hyperliquid). "
                              "Веса зависят от класса риска. Точки фиксации — правила, сценарии на годы — модель сложного процента при заявленных допущениях, не прогноз."}
    return markets_doc, research_doc
