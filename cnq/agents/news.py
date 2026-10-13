"""News department.

NewsCollector  — pulls RSS feeds (stdlib), dedupes, stores raw items with publish AND receive time.
NewsAnalyst    — LLM agent: per item → assets, category, direction, horizon, magnitude, confidence,
                 reasoning, what would confirm. Output is a PREDICTION and is written to the ledger.
ImpactLedger   — scores every prediction later against realised returns (Brier-style), per category,
                 so "what affects what" is measured from our own record, not assumed.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .llm import LLM

FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
    "theblock": "https://www.theblock.co/rss.xml",
    "decrypt": "https://decrypt.co/feed",
    "bitcoinmagazine": "https://bitcoinmagazine.com/.rss/full/",
}
ASSET_WORDS = {"BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "ether ", "eth "], "SOL": ["solana", "sol "], "BNB": ["bnb", "binance coin"],
               "XRP": ["xrp", "ripple"], "DOGE": ["doge"], "ADA": ["cardano", "ada "], "AVAX": ["avalanche", "avax"],
               "LINK": ["chainlink", "link "], "LTC": ["litecoin"], "MARKET": ["crypto market", "fomc", "fed ", "cpi", "sec ", "etf", "tariff", "treasury"]}
CATEGORIES = ("regulation", "etf_flows", "hack", "macro", "listing", "unlock", "tokenomics", "tech", "adoption", "onchain", "other")

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (id TEXT PRIMARY KEY, source TEXT, title TEXT, summary TEXT, link TEXT,
  published INTEGER, received INTEGER, assets TEXT);
CREATE TABLE IF NOT EXISTS news_impact (news_id TEXT, asset TEXT, category TEXT, direction TEXT, horizon TEXT,
  magnitude INTEGER, confidence REAL, reasoning TEXT, confirm TEXT, created INTEGER, price_at REAL,
  realized_ret REAL, scored INTEGER DEFAULT 0, PRIMARY KEY (news_id, asset));
"""


def _assets_hint(text: str) -> list[str]:
    t = " " + text.lower() + " "
    return [a for a, words in ASSET_WORDS.items() if any(w in t for w in words)] or ["MARKET"]


class NewsCollector:
    def __init__(self, conn: sqlite3.Connection, feeds: dict[str, str] | None = None):
        self.conn = conn; self.feeds = feeds or FEEDS
        conn.executescript(SCHEMA)

    @staticmethod
    def parse_rss(xml_text: str, source: str) -> list[dict]:
        out = []
        root = ET.fromstring(xml_text)
        now = int(time.time() * 1000)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "").strip()[:600]
            pub = item.findtext("pubDate")
            try:
                published = int(parsedate_to_datetime(pub).timestamp() * 1000) if pub else now
            except Exception:  # noqa: BLE001
                published = now
            nid = hashlib.sha1((source + title + link).encode()).hexdigest()[:16]
            out.append({"id": nid, "source": source, "title": title, "summary": desc, "link": link,
                        "published": published, "received": now, "assets_hint": _assets_hint(title + " " + desc)})
        return out

    def fetch(self, timeout: float = 20) -> list[dict]:
        new = []
        for name, url in self.feeds.items():
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq/1.0"}), timeout=timeout) as r:
                    items = self.parse_rss(r.read().decode("utf-8", "ignore"), name)
            except Exception:  # noqa: BLE001
                continue
            new += self.store(items)
        return new

    def store(self, items: list[dict]) -> list[dict]:
        new = []
        with self.conn:
            for it in items:
                cur = self.conn.execute("INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?,?)",
                                        (it["id"], it["source"], it["title"], it["summary"], it["link"], it["published"],
                                         it["received"], json.dumps(it["assets_hint"])))
                if cur.rowcount:
                    new.append(it)
        return new

    def recent(self, hours: int = 24, limit: int = 60) -> list[dict]:
        since = int(time.time() * 1000) - hours * 3_600_000
        rows = self.conn.execute("SELECT id,source,title,summary,link,published,received,assets FROM news WHERE published>=? "
                                 "ORDER BY published DESC LIMIT ?", (since, limit)).fetchall()
        return [{"id": r[0], "source": r[1], "title": r[2], "summary": r[3], "link": r[4], "published": r[5], "received": r[6],
                 "assets_hint": json.loads(r[7])} for r in rows]


NEWS_SYSTEM = """Ты — NEWS_ANALYST отдела новостей квант-платформы CRYPTO NOVΛ.
Твоя задача — не пересказывать новости, а оценивать их ВЛИЯНИЕ на цену конкретных активов как проверяемый прогноз.
Для каждой новости верни объект:
{"id": "...", "assets": ["BTC",...] или ["MARKET"], "category": одна из %s,
 "direction": "bull"|"bear"|"neutral", "horizon": "hours"|"days"|"weeks", "magnitude": 1-5,
 "confidence": 0.0-1.0, "reasoning": "1-2 предложения: механизм влияния (кто вынужден покупать/продавать и почему)",
 "confirm": "что в цене/данных подтвердит оценку в ближайшие сутки"}
Правила: уже отыгранные новости (цена двигалась до публикации) — magnitude 1, confidence ≤ 0.3. Слухи и мнения — "other", confidence ≤ 0.2.
Не выдумывай факты, которых нет в тексте. Верни JSON-массив.""" % ", ".join(CATEGORIES)


class NewsAnalyst:
    def __init__(self, llm: LLM, conn: sqlite3.Connection):
        self.llm = llm; self.conn = conn

    def analyze(self, items: list[dict], prices: dict[str, float]) -> list[dict]:
        if not items:
            return []
        payload = [{k: it[k] for k in ("id", "source", "title", "summary", "published", "assets_hint")} for it in items[:40]]
        res = self.llm.json(NEWS_SYSTEM, json.dumps(payload, ensure_ascii=False))
        if not isinstance(res, list):
            return []
        now = int(time.time() * 1000)
        out = []
        with self.conn:
            for r in res:
                if not isinstance(r, dict) or r.get("category") not in CATEGORIES:
                    continue
                assets = r.get("assets") or ["MARKET"]
                for a in assets:
                    px = prices.get(a if a != "MARKET" else "BTC")
                    row = {"news_id": r.get("id"), "asset": a, "category": r["category"], "direction": r.get("direction", "neutral"),
                           "horizon": r.get("horizon", "days"), "magnitude": int(r.get("magnitude", 1)), "confidence": float(r.get("confidence", 0.2)),
                           "reasoning": r.get("reasoning", ""), "confirm": r.get("confirm", ""), "created": now, "price_at": px}
                    self.conn.execute("INSERT OR REPLACE INTO news_impact (news_id,asset,category,direction,horizon,magnitude,confidence,"
                                      "reasoning,confirm,created,price_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                      tuple(row[k] for k in ("news_id", "asset", "category", "direction", "horizon", "magnitude", "confidence",
                                                             "reasoning", "confirm", "created", "price_at")))
                    out.append(row)
        return out


class ImpactLedger:
    """Scores predictions once their horizon has passed. Realised move is compared with the
    predicted direction; a Brier-style score per category tells which news types actually
    move price in our record — that becomes the analyst's calibration table."""
    HORIZON_MS = {"hours": 6 * 3_600_000, "days": 2 * 86_400_000, "weeks": 10 * 86_400_000}

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn; conn.executescript(SCHEMA)

    def score_due(self, prices_now: dict[str, float], now_ms: int | None = None) -> int:
        now_ms = now_ms or int(time.time() * 1000)
        rows = self.conn.execute("SELECT news_id, asset, direction, horizon, created, price_at FROM news_impact WHERE scored=0").fetchall()
        n = 0
        with self.conn:
            for nid, asset, d, hz, created, px0 in rows:
                if px0 is None or now_ms - created < self.HORIZON_MS.get(hz, 2 * 86_400_000):
                    continue
                px = prices_now.get(asset if asset != "MARKET" else "BTC")
                if not px:
                    continue
                ret = px / px0 - 1
                self.conn.execute("UPDATE news_impact SET realized_ret=?, scored=1 WHERE news_id=? AND asset=?", (ret, nid, asset))
                n += 1
        return n

    def calibration(self) -> list[dict]:
        rows = self.conn.execute("SELECT category, direction, confidence, magnitude, realized_ret FROM news_impact WHERE scored=1").fetchall()
        by: dict[str, list] = {}
        for cat, d, conf, mag, ret in rows:
            by.setdefault(cat, []).append((d, conf, mag, ret))
        out = []
        for cat, v in by.items():
            hits = [(1 if (r > 0) == (d == "bull") else 0) for d, _, _, r in v if d != "neutral"]
            brier = sum((c - h) ** 2 for (d, c, _, _), h in zip([x for x in v if x[0] != "neutral"], hits)) / max(1, len(hits))
            out.append({"category": cat, "n": len(v), "directional_hit_rate": round(sum(hits) / len(hits), 3) if hits else None,
                        "brier": round(brier, 3) if hits else None,
                        "mean_abs_move_pct": round(100 * sum(abs(r) for *_, r in v) / len(v), 2)})
        return sorted(out, key=lambda x: -x["n"])
