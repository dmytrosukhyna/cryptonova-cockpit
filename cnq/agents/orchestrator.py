"""Orchestrator — runs the departments in order and assembles one decision object.

  Quant desk   (deterministic)  → report: regimes, levels, setups, derivatives, portfolio layer
  News desk    (collector+LLM)  → impacts (predictions, logged to the ledger) + calibration table
  Fundamentals (CoinGecko)      → grades + flags
  Risk officer (deterministic)  → per-asset allow/deny with reasons
  Editor       (LLM, grounded)  → Telegram post in Russian from the structured JSON only;
                                  falls back to the rule-based brief when no LLM

Nothing numeric is produced by the LLM; it only explains and formats what the desks computed."""
from __future__ import annotations

import json
import sqlite3
import time

from ..terminal.report import market_brief
from .fundamentals import snapshot as fundamentals_snapshot
from .llm import LLM, MockLLM
from .news import ImpactLedger, NewsAnalyst, NewsCollector
from .risk import RiskOfficer

EDITOR_SYSTEM = """Ты — EDITOR канала CRYPTO NOVΛ. Пишешь ежедневный пост на русском для Telegram.
Используй ТОЛЬКО числа и факты из переданного JSON. Ничего не выдумывай, не называй вероятности, которых нет в данных.
Структура: 1) режим рынка в 2 предложениях; 2) портфельный слой (фильтр BTC, сигналы тренда, лидеры/аутсайдеры);
3) по 3–5 монетам: краткосрок/среднесрок/долгосрок одной строкой каждая + уровень отмены; 4) новости с оценкой влияния
(категория, направление, что подтвердит) — только те, где magnitude ≥ 2; 5) вердикт риск-офицера одной строкой;
6) сетапы, если есть (вход/стоп/цель). Тон: спокойный, конкретный, цифры цифрами, без воды и без эмодзи-спама (максимум 1 на блок).
Длина до 2500 символов. В конце: «Не финансовый совет»."""


class Orchestrator:
    def __init__(self, db_path: str = "data/agents.sqlite", llm: LLM | None = None, with_fundamentals: bool = True,
                 with_news_fetch: bool = True):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.llm = llm or (LLM() if LLM().available else MockLLM())
        self.collector = NewsCollector(self.conn)
        self.analyst = NewsAnalyst(self.llm, self.conn)
        self.ledger = ImpactLedger(self.conn)
        self.risk = RiskOfficer()
        self.with_fund = with_fundamentals
        self.with_fetch = with_news_fetch
        self._fund_cache: dict = {}
        self._fund_time = 0

    def run(self, report: dict, news_items: list[dict] | None = None, open_positions: list[dict] | None = None,
            now_ms: int | None = None) -> dict:
        now_ms = now_ms or int(time.time() * 1000)
        prices = {c["symbol"].replace("USDT", ""): c["price"] for c in report["coins"]}
        prices["MARKET"] = prices.get("BTC")
        # --- news desk
        if news_items is not None:
            self.collector.store(news_items)
        elif self.with_fetch:
            self.collector.fetch()
        self.ledger.score_due(prices, now_ms)
        recent = self.collector.recent(hours=24)
        already = {r[0] for r in self.conn.execute("SELECT DISTINCT news_id FROM news_impact")}
        fresh = [it for it in recent if it["id"] not in already]
        impacts = self.analyst.analyze(fresh, prices) if fresh else []
        all_impacts = self._impacts_recent(hours=48)
        calibration = self.ledger.calibration()
        # --- fundamentals (cached 6h)
        if self.with_fund and now_ms - self._fund_time > 6 * 3_600_000:
            self._fund_cache = fundamentals_snapshot([c["symbol"].replace("USDT", "") for c in report["coins"]])
            self._fund_time = now_ms
        fundamentals = self._fund_cache
        # --- risk
        risk = self.risk.review(report, all_impacts, fundamentals, open_positions, now_ms=now_ms)
        # --- editor
        decision = {"time": now_ms, "asof": report["asof"], "portfolio": report.get("portfolio"),
                    "coins": [{k: c[k] for k in ("symbol", "price", "chg24", "regime", "levels", "setups", "derivatives", "brief")} for c in report["coins"]],
                    "news": {"items": recent[:25], "impacts": all_impacts, "calibration": calibration,
                             "available": bool(recent), "llm": self.llm.model, "llm_error": getattr(self.llm, "last_error", None),
                             "fresh": len(fresh), "analyzed": len(impacts)},
                    "fundamentals": fundamentals, "risk": risk}
        post = self._edit(decision)
        decision["post"] = post or market_brief(report["coins"], report["asof"], report.get("portfolio"))
        decision["post_source"] = "llm" if post else "rules"
        return decision

    def _impacts_recent(self, hours: int) -> list[dict]:
        since = int(time.time() * 1000) - hours * 3_600_000
        rows = self.conn.execute("SELECT i.news_id,i.asset,i.category,i.direction,i.horizon,i.magnitude,i.confidence,i.reasoning,i.confirm,"
                                 "i.created,n.title,n.source,n.link FROM news_impact i JOIN news n ON n.id=i.news_id WHERE i.created>=? "
                                 "ORDER BY i.magnitude DESC, i.confidence DESC", (since,)).fetchall()
        keys = ("news_id", "asset", "category", "direction", "horizon", "magnitude", "confidence", "reasoning", "confirm", "created", "title", "source", "link")
        return [dict(zip(keys, r)) for r in rows]

    def _edit(self, decision: dict) -> str:
        slim = {"asof": decision["asof"], "portfolio": decision["portfolio"],
                "coins": [{"symbol": c["symbol"], "price": c["price"], "chg24": round(c["chg24"], 4), "regime": {k: c["regime"][k] for k in ("short", "mid", "long", "r28", "atr_pct")},
                           "nearest_levels": c["levels"][:2], "setups": [s for s in c["setups"] if s["status"] in ("CALL", "READY", "WAIT_CONFIRMATION")][:2],
                           "brief": c["brief"]["lines"]} for c in decision["coins"]],
                "news_impacts": [i for i in decision["news"]["impacts"] if i["magnitude"] >= 2][:12],
                "risk": {"global_block": decision["risk"]["global_block"], "blackout": decision["risk"]["blackout_assets"],
                         "denied": [v["symbol"] for v in decision["risk"]["verdicts"] if not v["allow_long"]]}}
        try:
            txt = self.llm.complete(EDITOR_SYSTEM, json.dumps(slim, ensure_ascii=False, default=str))
        except Exception as e:  # noqa: BLE001
            decision["post_error"] = str(e)[:300]
            return ""
        return txt.strip() if txt and len(txt.strip()) > 200 else ""
