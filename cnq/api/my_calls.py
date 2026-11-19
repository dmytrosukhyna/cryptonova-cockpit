"""«Мои прогнозы (Дмитрий)» (owner 19.11.2026 23:08): a PAPER desk of the owner's own daily view, next to what the system says.

The owner writes a call in «Управление» (admin.html, owner-only — the same check as every /v2/admin/* call): an asset (BTC by
default, any coin), a direction (вверх / вниз / нейтрально), a horizon (до 00:00 UTC / 24 ч / 3 дня / 7 дней) and an optional
comment. The server stores it with the exact time and the price at that moment (OKX spot ticker {COIN}-USDT; BingX spot if OKX
has no answer — the source is recorded with the price). A call cannot be edited; before its end it can be deleted, and the
deletion stays visible.

Scoring at the horizon's end: the exit price = the close of the OKX 1-minute candle that contains the end time (or, if no candle,
the live price when the check runs within 5 minutes of the end — recorded as such). move % = (exit / entry − 1) × 100;
«в сторону прогноза» = +move for «вверх», −move for «вниз». Hit: «вверх» move > 0, «вниз» move < 0, «нейтрально» |move| ≤ the
horizon's band (1 % for до 00:00 / 24 ч, 2 % for 3 дня, 3 % for 7 дней). Paper result: a $1 000 position (for «вниз» a short on
paper), minus 0.1 % costs ($1) per call; «нейтрально» opens no position, its result is $0.

Side by side: «система CRYPTO NOVA AI сказала» = web/market_view.json at the moment of saving (when the market-view analyst
publishes it; until then «нет данных»), stored with the call — never recomputed later.

Owner-only, never a public file (data in <run>/data/my_calls/), never routed to any account: this module imports nothing from the
execution code and places no orders. Stdlib only."""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request

DAY = 86_400_000
HORIZONS = {"today": ("до 00:00 UTC", None, 1.0), "24h": ("24 ч", DAY, 1.0), "3d": ("3 дня", 3 * DAY, 2.0), "7d": ("7 дней", 7 * DAY, 3.0)}
DIRS = {"up": "вверх", "down": "вниз", "flat": "нейтрально"}
STAKE, COST = 1000.0, 0.001
LATE_MS = 5 * 60_000
COIN_RE = re.compile(r"^[A-Z0-9]{2,15}$")
UA = {"User-Agent": "cnq-my-calls/1.0"}


def _get(url: str, **q) -> dict:
    req = urllib.request.Request(url + ("?" + urllib.parse.urlencode(q) if q else ""), headers=UA)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _f(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def price_now(coin: str, get=_get) -> tuple[float | None, str]:
    """(price, source) — OKX spot first, BingX spot second"""
    try:
        d = get("https://www.okx.com/api/v5/market/ticker", instId=f"{coin}-USDT")
        px = _f(((d.get("data") or [{}])[0]).get("last")) if str(d.get("code")) == "0" else None
        if px:
            return px, f"OKX спот {coin}-USDT"
    except Exception:  # noqa: BLE001
        pass
    try:
        d = get("https://open-api.bingx.com/openApi/spot/v1/ticker/price", symbol=f"{coin}-USDT")
        rows = d.get("data") or []
        row = rows[0] if isinstance(rows, list) and rows else rows if isinstance(rows, dict) else {}
        trades = row.get("trades") if isinstance(row, dict) else None
        px = _f((trades[0] or {}).get("price")) if trades else _f(row.get("price")) if isinstance(row, dict) else None
        if px:
            return px, f"BingX спот {coin}-USDT"
    except Exception:  # noqa: BLE001
        pass
    return None, ""


def price_at(coin: str, t: int, get=_get) -> tuple[float | None, str]:
    """the close of the OKX 1-minute candle that contains t"""
    try:
        d = get("https://www.okx.com/api/v5/market/history-candles", instId=f"{coin}-USDT", bar="1m", after=str(t - t % 60_000 + 60_000), limit="1")
        rows = d.get("data") or [] if str(d.get("code")) == "0" else []
        if rows and int(rows[0][0]) <= t < int(rows[0][0]) + 60_000:
            px = _f(rows[0][4])
            if px:
                return px, f"OKX спот {coin}-USDT · закрытие минутной свечи"
    except Exception:  # noqa: BLE001
        pass
    return None, ""


def end_of(horizon: str, t: int) -> int:
    lab, span, _ = HORIZONS[horizon]
    return t - t % DAY + DAY if span is None else t + span


def score(c: dict, exit_px: float) -> dict:
    move = (exit_px / c["entry_px"] - 1) * 100
    band = HORIZONS[c["horizon"]][2]
    if c["dir"] == "flat":
        hit, towards, pnl = abs(move) <= band, None, 0.0
    else:
        towards = move if c["dir"] == "up" else -move
        hit = towards > 0
        pnl = STAKE * towards / 100 - STAKE * COST
    return {"exit_px": exit_px, "move_pct": round(move, 3), "towards_pct": None if towards is None else round(towards, 3),
            "hit": bool(hit), "pnl_usd": round(pnl, 2), "band_pct": band if c["dir"] == "flat" else None}


def system_view(web_dir: str | None, coin: str) -> dict | None:
    """what web/market_view.json says about the coin at this moment (lenient: the analyst's format is not fixed yet)"""
    if not web_dir:
        return None
    try:
        with open(os.path.join(web_dir, "market_view.json"), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    rows = d.get("assets") or d.get("views") or d.get("coins") or []
    if isinstance(rows, dict):
        rows = [{"asset": k, **(v if isinstance(v, dict) else {"direction": v})} for k, v in rows.items()]
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and str(r.get("asset") or r.get("sym") or r.get("coin") or "").upper() == coin:
            dir_ = str(r.get("direction") or r.get("dir") or r.get("view") or "")[:40]
            return {"direction": dir_, "text": str(r.get("text_ru") or r.get("why_ru") or r.get("text") or "")[:300],
                    "horizon": str(r.get("horizon") or d.get("horizon") or "")[:40], "asof": d.get("asof")}
    return None


class MyCalls:
    def __init__(self, data_dir: str, web_dir: str | None = None, get=_get, clock=None):
        self.dir, self.web, self.get = data_dir, web_dir, get
        self.now = clock or (lambda: int(time.time() * 1000))
        self.lock = threading.Lock()

    def _path(self) -> str:
        return os.path.join(self.dir, "calls.json")

    def _load(self) -> list:
        try:
            with open(self._path(), encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, calls: list) -> None:
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(calls, f, ensure_ascii=False)
        os.replace(tmp, self._path())

    def add(self, form: dict) -> tuple[int, dict]:
        coin = re.sub(r"[^A-Za-z0-9]", "", str(form.get("asset") or "BTC")).upper()[:15] or "BTC"
        dir_, hz = str(form.get("dir") or ""), str(form.get("horizon") or "")
        if not COIN_RE.match(coin):
            return 400, {"error": "bad_asset", "message_ru": "Монета — латинские буквы и цифры, например BTC."}
        if dir_ not in DIRS or hz not in HORIZONS:
            return 400, {"error": "bad_form", "message_ru": "Выберите направление и срок."}
        t = self.now()
        px, src = price_now(coin, self.get)
        if not px:
            return 502, {"error": "no_price", "message_ru": f"Нет цены {coin} ни на OKX, ни на BingX — прогноз не записан. Проверьте тикер."}
        c = {"id": secrets.token_hex(6), "t": t, "asset": coin, "dir": dir_, "horizon": hz, "end_t": end_of(hz, t),
             "comment": str(form.get("comment") or "").strip()[:500], "entry_px": px, "entry_src": src,
             "system": system_view(self.web, coin), "status": "open"}
        with self.lock:
            calls = self._load()
            calls.append(c)
            self._save(calls)
        return 200, {"ok": True, "message_ru": f"Записано: {coin} {DIRS[dir_]}, {HORIZONS[hz][0]}, цена {px:g} ({src}).", **self.view()}

    def delete(self, cid: str) -> tuple[int, dict]:
        with self.lock:
            calls = self._load()
            c = next((x for x in calls if x.get("id") == cid), None)
            if not c:
                return 404, {"error": "not_found"}
            if c.get("status") != "open" or self.now() >= int(c["end_t"]):
                return 409, {"error": "closed", "message_ru": "Срок прогноза уже вышел — его нельзя удалить."}
            c.update(status="deleted", deleted_t=self.now())
            self._save(calls)
        return 200, {"ok": True, "message_ru": "Прогноз удалён до срока — он остаётся в списке с пометкой.", **self.view()}

    def settle(self) -> int:
        """score every open call whose end has come; a call with no price yet stays open and is retried"""
        now, n = self.now(), 0
        with self.lock:
            calls = self._load()
            for c in calls:
                if c.get("status") != "open" or now < int(c["end_t"]):
                    continue
                px, src = price_at(c["asset"], int(c["end_t"]), self.get)
                if not px and now - int(c["end_t"]) <= LATE_MS:
                    px, src = price_now(c["asset"], self.get)
                    src = (src + " · живая цена при проверке") if px else ""
                if not px:
                    c["pending_note"] = "нет цены на момент окончания — повторю"
                    continue
                c.update(score(c, px), exit_src=src, exit_t=int(c["end_t"]), status="closed", settled_t=now)
                c.pop("pending_note", None)
                n += 1
            if n:
                self._save(calls)
        return n

    def view(self) -> dict:
        calls = self._load()
        cl = sorted([c for c in calls if c.get("status") == "closed"], key=lambda c: c["end_t"])
        def rate(xs):
            return {"n": len(xs), "hits": sum(1 for c in xs if c.get("hit")), "pct": round(100 * sum(1 for c in xs if c.get("hit")) / len(xs), 1) if xs else None,
                    "pnl_usd": round(sum(c.get("pnl_usd") or 0 for c in xs), 2)}
        cum, curve = 0.0, []
        for c in cl:
            cum += c.get("pnl_usd") or 0
            curve.append([c["end_t"], round(cum, 2)])
        return {"asof": self.now(), "owner_only": True, "paper": True, "real_account": False,
                "label_ru": "ТЕСТ · бумажные прогнозы владельца. Сделок нет, на реальный счёт ничего не попадает.",
                "rule_ru": (f"Цена входа — в момент записи (OKX спот, если нет — BingX). Итог — в конце срока: закрытие минутной свечи OKX. "
                            f"Бумажный результат — позиция ${STAKE:,.0f} (для «вниз» — шорт на бумаге), издержки {COST * 100:g}% (${STAKE * COST:g}). "
                            "«Нейтрально» — без позиции; попадание, если цена ушла не дальше ±1% (до 00:00 и 24 ч), ±2% (3 дня), ±3% (7 дней). "
                            "Прогноз нельзя изменить; до срока его можно удалить — он остаётся в списке с пометкой.").replace(",", " "),
                "open": sorted([c for c in calls if c.get("status") == "open"], key=lambda c: c["end_t"]),
                "closed": list(reversed(cl)), "deleted": [c for c in calls if c.get("status") == "deleted"],
                "stats": {"all": rate(cl),
                          "by_asset": {a: rate([c for c in cl if c["asset"] == a]) for a in sorted({c["asset"] for c in cl})},
                          "by_horizon": {h: rate([c for c in cl if c["horizon"] == h]) for h in HORIZONS if any(c["horizon"] == h for c in cl)}},
                "curve": curve, "horizons": {k: v[0] for k, v in HORIZONS.items()}, "dirs": DIRS,
                "system_ru": None if os.path.exists(os.path.join(self.web or "", "market_view.json")) else
                             "«Система CRYPTO NOVA AI сказала» появится, когда аналитик рынка начнёт публиковать web/market_view.json."}

    def start(self) -> None:
        def loop():
            while True:
                try:
                    self.settle()
                except Exception as e:  # noqa: BLE001
                    print("my_calls:", type(e).__name__, flush=True)
                time.sleep(60)
        threading.Thread(target=loop, name="my-calls", daemon=True).start()
