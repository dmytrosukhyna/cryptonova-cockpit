"""«Ручной стол (Дмитрий)» — stage 1 (owner 19.11.2026 23:12): a semi-manual PAPER desk in «Управление». Paper money only: this
module imports nothing from the execution code and never places an order on any exchange.

The owner types a coin («ADA» or «Cardano»); it is resolved to an OKX USDT perpetual ({COIN}-USDT-SWAP, OKX public instruments;
the name comes from web/coin_profiles.json) — otherwise «нет такой монеты на OKX». LONG / SHORT shows a preview first, one more
tap opens the paper position by the desk's rules (all in «Настройки», the owner can change them):
  • account: $10 000 demo; size = risk % (default 3 %) of the account's equity ÷ the stop distance; leverage = size ÷ equity,
    capped at 5× (a cap shrinks the size, so the risk is then smaller — shown);
  • stop: «swing_h1» / «swing_h4» = beyond the nearest H1 / H4 swing low (long) or high (short) on the protective side, plus a 0.1 %
    buffer; «pct» = a fixed %; never further than −16 % (stop_max_pct) and never nearer than 0.3 %;
  • take-profits: a ladder at 1R / 2R / 3R (tp_r) closing 40 / 30 / 30 % (tp_split); after TP1 the stop moves to entry; after TP2 a
    trailing stop follows the best price at the initial stop distance (trail_after_tp2);
  • fills: the OKX last price + 0.05 % slippage against us, fees 0.05 % of the notional on every fill (OKX taker).
Every minute the engine reads OKX 1-minute candles of the open coins and walks them in order: when one candle touches both the
stop and a take-profit, the stop is taken first (the conservative reading). Close (market) and «move stop» per position.
Each trade keeps its context at the open (levels near, distances, trend, ATR, session, BTC regime — cnq.agents.ivan_study.context —
and «что говорила система» from web/market_view.json) for the research of «торговля от уровней». Stats: trades, win rate, avg R,
P&L, max drawdown. Stage 2 (the chart with levels and the side panels) is not here yet."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request

from cnq.agents import ivan_study as IS

from . import my_calls

OKX = "https://www.okx.com"
START = 10_000.0
DEFAULTS = {"risk_pct": 3.0, "stop_rule": "swing_h1", "stop_pct": 3.0, "stop_max_pct": 16.0, "stop_min_pct": 0.3, "buffer_pct": 0.1,
            "tp_r": [1.0, 2.0, 3.0], "tp_split": [40, 30, 30], "be_after_tp1": True, "trail_after_tp2": True, "max_lev": 5.0}
SLIP, FEE = 0.0005, 0.0005
INST_TTL = 3600


def _get(path: str, **q):
    req = urllib.request.Request(f"{OKX}{path}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "cnq-manual-desk/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read().decode("utf-8"))
    if str(d.get("code")) != "0":
        raise RuntimeError(f"OKX {path}: {d.get('msg') or d.get('code')}")
    return d.get("data") or []


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def candles(get, inst: str, bar: str, limit: int = 300) -> list:
    """[[t, o, h, l, c]] oldest first, only closed candles (OKX confirm = 1)"""
    rows = get("/api/v5/market/candles", instId=inst, bar=bar, limit=str(limit))
    out = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows if len(r) > 8 and str(r[8]) == "1"]
    return sorted(out)


def clean_settings(s: dict, base: dict | None = None) -> dict:
    out = dict(base or DEFAULTS)
    for k, lo, hi in (("risk_pct", 0.1, 10.0), ("stop_pct", 0.3, 16.0), ("stop_max_pct", 1.0, 16.0), ("max_lev", 1.0, 5.0)):
        if k in s and _f(s[k]) is not None:
            out[k] = min(hi, max(lo, float(s[k])))
    if s.get("stop_rule") in ("swing_h1", "swing_h4", "pct"):
        out["stop_rule"] = s["stop_rule"]
    if isinstance(s.get("tp_r"), list) and 1 <= len(s["tp_r"]) <= 3 and all(_f(x) and 0.2 <= float(x) <= 20 for x in s["tp_r"]):
        out["tp_r"] = sorted(round(float(x), 2) for x in s["tp_r"])
    if isinstance(s.get("tp_split"), list) and len(s["tp_split"]) == len(out["tp_r"]) and all(_f(x) and float(x) > 0 for x in s["tp_split"]):
        tot = sum(float(x) for x in s["tp_split"])
        out["tp_split"] = [round(float(x) / tot * 100, 1) for x in s["tp_split"]]
    elif len(out["tp_split"]) != len(out["tp_r"]):
        out["tp_split"] = [round(100 / len(out["tp_r"]), 1)] * len(out["tp_r"])
    for k in ("be_after_tp1", "trail_after_tp2"):
        if k in s:
            out[k] = bool(s[k])
    return out


def plan_stop(side: str, entry: float, st: dict, h1: list, h4: list) -> tuple[float, str]:
    """(stop price, why) on the protective side, within [stop_min_pct, stop_max_pct] of the entry"""
    long_ = side == "long"
    lim_far = entry * (1 - st["stop_max_pct"] / 100) if long_ else entry * (1 + st["stop_max_pct"] / 100)
    lim_near = entry * (1 - st["stop_min_pct"] / 100) if long_ else entry * (1 + st["stop_min_pct"] / 100)
    why, stop = "", None
    if st["stop_rule"] in ("swing_h1", "swing_h4"):
        cs = h1 if st["stop_rule"] == "swing_h1" else h4
        sw = IS.swings(cs, 2, 30)
        lvls = [p for _, p in (sw["lows"] if long_ else sw["highs"])]
        cand = [p for p in lvls if (p < lim_near if long_ else p > lim_near)]
        if cand:
            lvl = max(cand) if long_ else min(cand)
            stop = lvl * (1 - st["buffer_pct"] / 100) if long_ else lvl * (1 + st["buffer_pct"] / 100)
            why = f"за ближайшим {'минимумом' if long_ else 'максимумом'} {st['stop_rule'][-2:].upper()} {lvl:g} (+{st['buffer_pct']:g}%)"
    if stop is None:
        p = st["stop_pct"] if st["stop_rule"] == "pct" else min(st["stop_pct"], st["stop_max_pct"])
        stop = entry * (1 - p / 100) if long_ else entry * (1 + p / 100)
        why = f"фиксированный {p:g}%" + ("" if st["stop_rule"] == "pct" else " (подходящего уровня нет)")
    if (long_ and stop < lim_far) or (not long_ and stop > lim_far):
        stop, why = lim_far, why + f" → ограничен −{st['stop_max_pct']:g}%"
    return stop, why


def size_for(equity: float, entry: float, stop: float, st: dict) -> dict:
    dist = abs(entry - stop) / entry
    risk_usd = equity * st["risk_pct"] / 100
    notional = risk_usd / dist
    capped = notional > equity * st["max_lev"]
    if capped:
        notional = equity * st["max_lev"]
    return {"notional": round(notional, 2), "qty": notional / entry, "lev": round(notional / equity, 2), "capped": capped,
            "risk_usd": round(notional * dist, 2), "stop_dist_pct": round(dist * 100, 3)}


class ManualDesk:
    def __init__(self, data_dir: str, web_dir: str | None = None, get=_get, clock=None):
        self.dir, self.web, self.get = data_dir, web_dir, get
        self.now = clock or (lambda: int(time.time() * 1000))
        self.lock = threading.Lock()
        self._inst = (0.0, set())

    # ------------------------------------------------------------------ state
    def _path(self) -> str:
        return os.path.join(self.dir, "desk.json")

    def _load(self) -> dict:
        try:
            with open(self._path(), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d.setdefault("start", START)
        d.setdefault("cash", START)            # realised equity: start + closed results − fees
        d.setdefault("open", [])
        d.setdefault("closed", [])
        d["settings"] = clean_settings(d.get("settings") or {}, DEFAULTS)
        return d

    def _save(self, d: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, self._path())

    # ------------------------------------------------------------------ coin
    def _instruments(self) -> set:
        t, s = self._inst
        if s and time.time() - t < INST_TTL:
            return s
        rows = self.get("/api/v5/public/instruments", instType="SWAP")
        s = {r["instId"] for r in rows if str(r.get("instId", "")).endswith("-USDT-SWAP") and r.get("state", "live") == "live"}
        self._inst = (time.time(), s)
        return s

    def _names(self) -> dict:
        try:
            with open(os.path.join(self.web or "", "coin_profiles.json"), encoding="utf-8") as f:
                coins = (json.load(f) or {}).get("coins") or {}
            return {str(v.get("name") or "").lower(): k for k, v in coins.items() if isinstance(v, dict) and v.get("name")}
        except (OSError, ValueError, AttributeError):
            return {}

    def resolve(self, q: str) -> tuple[int, dict]:
        q = str(q or "").strip()[:40]
        sym = "".join(ch for ch in q.upper() if ch.isalnum())
        try:
            inst = self._instruments()
        except Exception:  # noqa: BLE001
            return 502, {"error": "okx", "message_ru": "OKX не ответил — попробуйте ещё раз."}
        cand = [sym, self._names().get(q.lower(), "")]
        for c in cand:
            if c and f"{c}-USDT-SWAP" in inst:
                return 200, {"ok": True, "sym": c, "inst": f"{c}-USDT-SWAP"}
        return 404, {"error": "not_found", "message_ru": "нет такой монеты на OKX"}

    def _last(self, inst: str) -> float | None:
        rows = self.get("/api/v5/market/ticker", instId=inst)
        return _f((rows or [{}])[0].get("last"))

    # ------------------------------------------------------------------ open
    def preview(self, form: dict) -> tuple[int, dict]:
        code, r = self.resolve(form.get("sym") or form.get("q") or "")
        if code != 200:
            return code, r
        side = str(form.get("side") or "").lower()
        if side not in ("long", "short"):
            return 400, {"error": "bad_side"}
        d = self._load()
        st = d["settings"]
        try:
            last = self._last(r["inst"])
            h1, h4 = candles(self.get, r["inst"], "1H", 300), candles(self.get, r["inst"], "4H", 300)
        except Exception:  # noqa: BLE001
            return 502, {"error": "okx", "message_ru": "OKX не ответил — попробуйте ещё раз."}
        if not last:
            return 502, {"error": "no_price", "message_ru": "Нет цены на OKX."}
        entry = last * (1 + SLIP) if side == "long" else last * (1 - SLIP)
        stop, why = plan_stop(side, entry, st, h1, h4)
        eq = self.equity(d)
        sz = size_for(eq, entry, stop, st)
        r1 = abs(entry - stop)
        tps = [{"r": x, "px": entry + x * r1 if side == "long" else entry - x * r1, "pct": sp} for x, sp in zip(st["tp_r"], st["tp_split"])]
        return 200, {"ok": True, "sym": r["sym"], "inst": r["inst"], "side": side, "last": last, "entry": entry, "stop": stop, "stop_why": why,
                     "tps": tps, **sz, "equity": round(eq, 2), "settings": st,
                     "fees_est": round(sz["notional"] * FEE * 2, 2),
                     "note_ru": ("Размер урезан плечом " + f"{st['max_lev']:g}×: риск {sz['risk_usd']:g}$ вместо {eq * st['risk_pct'] / 100:.2f}$." if sz["capped"] else "")}

    def open(self, form: dict) -> tuple[int, dict]:
        if form.get("confirm") is not True:
            return 400, {"error": "confirm_required", "message_ru": "Сначала посмотрите параметры и подтвердите."}
        code, p = self.preview(form)
        if code != 200:
            return code, p
        now = self.now()
        try:
            h1, d1 = candles(self.get, p["inst"], "1H", 300), candles(self.get, p["inst"], "1D", 260)
            bh1, bd1 = candles(self.get, "BTC-USDT-SWAP", "1H", 300), candles(self.get, "BTC-USDT-SWAP", "1D", 260)
            ctx = IS.context(p["entry"], now, h1, d1, bh1, bd1)
        except Exception:  # noqa: BLE001 — the trade is never refused for missing context
            ctx = None
        fee = p["notional"] * FEE
        pos = {"id": secrets.token_hex(6), "sym": p["sym"], "inst": p["inst"], "side": p["side"], "t": now, "entry": p["entry"],
               "qty": p["qty"], "qty0": p["qty"], "notional": p["notional"], "lev": p["lev"], "stop0": p["stop"], "stop": p["stop"],
               "stop_why": p["stop_why"], "r_usd": p["risk_usd"], "tps": [{**x, "done": False} for x in p["tps"]], "best": p["entry"],
               "trail": False, "fees": round(fee, 4), "realised": 0.0, "fills": [{"t": now, "kind": "open", "px": p["entry"], "qty": p["qty"]}],
               "settings": p["settings"], "ctx": ctx, "system": my_calls.system_view(self.web, p["sym"]), "last_t": now, "mark": p["last"],
               "comment": str(form.get("comment") or "")[:300]}
        with self.lock:
            d = self._load()
            d["cash"] -= fee
            d["open"].append(pos)
            self._save(d)
        return 200, {"ok": True, "message_ru": f"Открыто на бумаге: {p['sym']} {p['side'].upper()} на ${p['notional']:,.0f}, стоп {p['stop']:g}.".replace(",", " "), **self.view()}

    # ------------------------------------------------------------------ manage
    def _fill(self, d: dict, pos: dict, px: float, qty: float, kind: str, t: int) -> None:
        sgn = 1 if pos["side"] == "long" else -1
        px = px * (1 - SLIP * sgn) if kind != "tp" else px          # market exits (stop / close) slip against us; TP is a limit
        pnl = sgn * (px - pos["entry"]) * qty
        fee = px * qty * FEE
        pos["realised"] = round(pos["realised"] + pnl, 4)
        pos["fees"] = round(pos["fees"] + fee, 4)
        pos["qty"] = max(0.0, pos["qty"] - qty)
        pos["fills"].append({"t": t, "kind": kind, "px": px, "qty": qty, "pnl": round(pnl, 4)})
        d["cash"] += pnl - fee

    def _finish(self, d: dict, pos: dict, t: int, why: str) -> None:
        d["open"] = [x for x in d["open"] if x["id"] != pos["id"]]
        net = pos["realised"] - pos["fees"]
        d["closed"].append({**{k: v for k, v in pos.items() if k not in ("mark",)}, "closed_t": t, "why": why, "pnl_usd": round(net, 2),
                            "r_mult": round(net / pos["r_usd"], 2) if pos["r_usd"] else None})

    def close(self, pid: str, why: str = "закрыто вручную") -> tuple[int, dict]:
        with self.lock:
            d = self._load()
            pos = next((x for x in d["open"] if x["id"] == pid), None)
            if not pos:
                return 404, {"error": "not_found"}
            try:
                px = self._last(pos["inst"])
            except Exception:  # noqa: BLE001
                px = None
            if not px:
                return 502, {"error": "no_price", "message_ru": "Нет цены на OKX — позиция не закрыта."}
            t = self.now()
            self._fill(d, pos, px, pos["qty"], "close", t)
            self._finish(d, pos, t, why)
            self._save(d)
        return 200, {"ok": True, "message_ru": "Позиция закрыта по рынку (на бумаге).", **self.view()}

    def move_stop(self, pid: str, px) -> tuple[int, dict]:
        px = _f(px)
        with self.lock:
            d = self._load()
            pos = next((x for x in d["open"] if x["id"] == pid), None)
            if not pos:
                return 404, {"error": "not_found"}
            mark = pos.get("mark") or pos["entry"]
            if not px or (pos["side"] == "long" and px >= mark) or (pos["side"] == "short" and px <= mark):
                return 400, {"error": "bad_stop", "message_ru": "Стоп должен быть с защитной стороны от текущей цены."}
            pos["stop"] = px
            pos["fills"].append({"t": self.now(), "kind": "stop_moved", "px": px, "qty": 0})
            self._save(d)
        return 200, {"ok": True, "message_ru": f"Стоп перенесён на {px:g}.", **self.view()}

    def set_settings(self, form: dict) -> tuple[int, dict]:
        with self.lock:
            d = self._load()
            d["settings"] = clean_settings(form, d["settings"])
            self._save(d)
        return 200, {"ok": True, "message_ru": "Настройки сохранены — действуют для новых позиций.", **self.view()}

    def walk(self, d: dict, pos: dict, cs: list) -> None:
        """one position through 1-minute candles [[t, o, h, l, c]] after its last check: the stop first when a candle touches both"""
        long_ = pos["side"] == "long"
        r1 = abs(pos["entry"] - pos["stop0"])
        for t, o, h, l, c in cs:
            if t < pos["last_t"] or pos["qty"] <= 1e-12:
                continue
            hit_stop = (l <= pos["stop"]) if long_ else (h >= pos["stop"])
            if hit_stop:
                self._fill(d, pos, pos["stop"], pos["qty"], "stop", t + 60_000)
                self._finish(d, pos, t + 60_000, "стоп" if not pos["trail"] else "трейлинг-стоп")
                return
            for i, tp in enumerate(pos["tps"]):
                if tp["done"] or not ((h >= tp["px"]) if long_ else (l <= tp["px"])):
                    continue
                last = all(x["done"] for j, x in enumerate(pos["tps"]) if j != i)
                q = pos["qty"] if last else min(pos["qty"], pos["qty0"] * tp["pct"] / 100)
                self._fill(d, pos, tp["px"], q, "tp", t + 60_000)
                tp["done"] = True
                if i == 0 and pos["settings"].get("be_after_tp1"):
                    pos["stop"] = max(pos["stop"], pos["entry"]) if long_ else min(pos["stop"], pos["entry"])
                if i == 1 and pos["settings"].get("trail_after_tp2"):
                    pos["trail"] = True
            if pos["qty"] <= 1e-12:
                self._finish(d, pos, t + 60_000, "все тейки")
                return
            pos["best"] = max(pos["best"], h) if long_ else min(pos["best"], l)
            if pos["trail"]:
                tr = pos["best"] - r1 if long_ else pos["best"] + r1
                pos["stop"] = max(pos["stop"], tr) if long_ else min(pos["stop"], tr)
            pos["mark"] = c
            pos["last_t"] = t + 60_000

    def tick(self) -> int:
        with self.lock:
            d = self._load()
            n = len(d["open"])
            for pos in list(d["open"]):
                try:
                    cs = candles(self.get, pos["inst"], "1m", 100)
                except Exception:  # noqa: BLE001
                    continue
                self.walk(d, pos, cs)
            if n:
                self._save(d)
        return n

    # ------------------------------------------------------------------ view
    def equity(self, d: dict) -> float:
        up = sum((1 if p["side"] == "long" else -1) * ((p.get("mark") or p["entry"]) - p["entry"]) * p["qty"] for p in d["open"])
        return d["cash"] + up

    def view(self) -> dict:
        d = self._load()
        cl = sorted(d["closed"], key=lambda c: c["closed_t"])
        eq, peak, dd, curve = d["start"], d["start"], 0.0, []
        for c in cl:
            eq += c["pnl_usd"]
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak * 100)
            curve.append([c["closed_t"], round(eq, 2)])
        wins = [c for c in cl if c["pnl_usd"] > 0]
        rs = [c["r_mult"] for c in cl if c.get("r_mult") is not None]
        for p in d["open"]:
            sgn = 1 if p["side"] == "long" else -1
            p["upnl"] = round(sgn * ((p.get("mark") or p["entry"]) - p["entry"]) * p["qty"], 2)
        return {"asof": self.now(), "owner_only": True, "paper": True, "real_account": False,
                "label_ru": "ТЕСТ · бумажный стол владельца, демо $10 000. Ордеров на бирже нет, на реальный счёт ничего не попадает.",
                "settings": d["settings"], "start": d["start"], "equity": round(self.equity(d), 2), "cash": round(d["cash"], 2),
                "open": d["open"], "closed": list(reversed(cl))[:200],
                "stats": {"trades": len(cl), "wins": len(wins), "win_pct": round(100 * len(wins) / len(cl), 1) if cl else None,
                          "avg_r": round(sum(rs) / len(rs), 2) if rs else None, "pnl_usd": round(sum(c["pnl_usd"] for c in cl), 2),
                          "max_dd_pct": round(dd, 2)}, "curve": curve,
                "stage_ru": "Этап 1: тикер, LONG / SHORT, стоп и тейки, позиции. Этап 2 (график с уровнями, «покупатели против продавцов», "
                            "объёмы, новости, календарь) — следующий."}

    def start(self) -> None:
        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001
                    print("manual_desk:", type(e).__name__, flush=True)
                time.sleep(60)
        threading.Thread(target=loop, name="manual-desk", daemon=True).start()
