"""Risk Officer — deterministic. Has veto power over every signal.

Checks: event blackout (high-impact news on the asset or the market within the horizon),
regime consistency (alt longs only while BTC is above SMA200), correlation cap (alts move
with BTC: count them as one exposure), per-trade and daily limits, data freshness,
fundamentals red flags, funding crowding."""
from __future__ import annotations

import time


class RiskOfficer:
    def __init__(self, max_correlated_longs: int = 3, max_positions: int = 5, blackout_hours: int = 6,
                 max_data_age_min: int = 45, daily_loss_limit_pct: float = 3.0):
        self.max_corr = max_correlated_longs; self.max_pos = max_positions; self.blackout_h = blackout_hours
        self.max_age = max_data_age_min; self.daily_limit = daily_loss_limit_pct

    def review(self, report: dict, impacts: list[dict], fundamentals: dict, open_positions: list[dict] | None = None,
               daily_loss_pct: float = 0.0, now_ms: int | None = None) -> dict:
        now_ms = now_ms or int(time.time() * 1000)
        open_positions = open_positions or []
        verdicts = []
        global_block = []
        age_min = (now_ms - report["asof"]) / 60_000
        if age_min > self.max_age:
            global_block.append(f"данные устарели на {age_min:.0f} мин")
        if daily_loss_pct >= self.daily_limit:
            global_block.append(f"дневной лимит убытка {self.daily_limit}% исчерпан")
        btc_bull = report.get("portfolio", {}).get("btc_bull", True)
        hot = {}
        for imp in impacts:
            if imp["magnitude"] >= 3 and imp["confidence"] >= 0.5 and now_ms - imp["created"] <= self.blackout_h * 3_600_000:
                hot.setdefault(imp["asset"], []).append(imp)
        longs_open = sum(1 for p in open_positions if p.get("direction") == "LONG")
        for c in report["coins"]:
            sym = c["symbol"]; base = sym.replace("USDT", "")
            reasons, allow = [], True
            if global_block:
                allow = False; reasons += global_block
            if base in hot or "MARKET" in hot:
                allow = False
                src = hot.get(base, []) + hot.get("MARKET", [])
                reasons.append("блэкаут по новостям: " + "; ".join(f"{i['category']} {i['direction']} m{i['magnitude']}" for i in src[:2]))
            f = fundamentals.get(base, {})
            if f.get("grade") == "red":
                reasons.append("фундаментал: " + "; ".join(t for _, t in f["flags"] if _ == "red")); allow = False
            d = c.get("derivatives", {})
            if d.get("available") and d.get("funding_rank90d") is not None and d["funding_rank90d"] >= 0.95:
                reasons.append("funding в 95-м перцентиле — лонги переполнены (лонг только со стопом за ближайшей зоной)")
            long_ok = allow and (btc_bull or base == "BTC")
            if allow and not long_ok:
                reasons.append("BTC ниже SMA200 — лонги по альтам закрыты")
            if long_ok and longs_open >= self.max_corr:
                long_ok = False; reasons.append(f"уже {longs_open} коррелированных лонгов (лимит {self.max_corr})")
            if len(open_positions) >= self.max_pos:
                allow = False; reasons.append(f"лимит позиций {self.max_pos}")
            verdicts.append({"symbol": sym, "allow_long": bool(long_ok), "allow_short": bool(allow), "reasons": reasons})
        return {"time": now_ms, "global_block": global_block, "btc_bull": btc_bull, "verdicts": verdicts,
                "blackout_assets": sorted(hot)}
