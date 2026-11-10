"""«Питер» — a demo / observation desk for a forex gold (XAUUSD) trader who trades in MetaTrader 5 (11.11.2026, owner-approved).
Paper and observation ONLY: nothing here places an order anywhere, nothing touches OKX or any exchange account. Owner-only: the
feed is never written to web/ (that folder is published), it lives in the API's own data folder and is served only to a signed-in
owner (GET /v2/owner/peter, cnq/api/server.py). Public texts never mention it.

  POST /v2/peter/ingest   ← tools/mt5/CryptoNovaBridge.mq5 (Peter's terminal, WebRequest). Header X-CNQ-Token: the per-trader
                            secret CNQ_PETER_TOKEN from /etc/cnq.env (compared in constant time; never logged, never echoed).
                            Body {"v": 1, "acct": sha256 of the login, "events": [...]} — deal / modify / heartbeat / history.
  GET  /v2/owner/peter         → the desk (owner session)          GET /v2/owner/peter/token → the token + the URL (owner session)

Store: <run>/data/peter/peter.sqlite (raw events, deals, positions, candles, context snapshots) and peter_live.json (the desk:
open positions, closed trades, a notional $10 000 curve scaled by his % returns, win rate, average trade, max drawdown, trades a
week, holding-time distribution). Context at every entry and exit is stored raw (bars, levels, calendar) plus plain measurements
(session, weekday, distance to the day's high / low, ATR, distance to SMA20/50/200 on H1/D1, time since the previous trade) —
no conclusions, for a later strategy study."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import threading
import time

NAME = os.environ.get("CNQ_PETER_NAME") or "Питер"
TOKEN_ENV = "CNQ_PETER_TOKEN"
URL = os.environ.get("CNQ_PETER_URL") or "https://app.crypto-capital.app/v2/peter/ingest"
START_USD = 10_000.0
MAX_BODY = 1_500_000
MAX_EVENTS = 300
RATE_PER_MIN = 120
HB_STALE_MIN = 30
WD_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
HOLD_BUCKETS = [(15, "до 15 мин"), (60, "15–60 мин"), (240, "1–4 ч"), (1440, "4–24 ч"), (10 ** 9, "больше суток")]


def _f(v, d=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def _i(v, d=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def gold_market_open(now_ms: int) -> bool:
    """XAUUSD trades Sunday 23:00 → Friday 21:00 UTC with a daily pause about 21:00–23:00 UTC (it moves an hour with US summer
    time — the pause here is the wide one)"""
    t = dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc)
    wd, h = t.weekday(), t.hour
    if wd == 5 or (wd == 4 and h >= 21) or (wd == 6 and h < 23):
        return False
    return not (21 <= h < 23)


def sessions(t_ms: int) -> list[str]:
    """the trading sessions open at that moment (UTC): Азия 23–08, Лондон 07–16, Нью-Йорк 12–21"""
    h = dt.datetime.fromtimestamp(t_ms / 1000, dt.timezone.utc).hour
    out = []
    if h >= 23 or h < 8:
        out.append("Азия")
    if 7 <= h < 16:
        out.append("Лондон")
    if 12 <= h < 21:
        out.append("Нью-Йорк")
    return out or ["межсессия"]


def _sma(c: list[float], n: int):
    return sum(c[-n:]) / n if len(c) >= n else None


def _atr(bars: list, n: int = 14):
    if len(bars) < n + 1:
        return None
    tr = [max(b[2] - b[3], abs(b[2] - p[4]), abs(b[3] - p[4])) for p, b in zip(bars[:-1], bars[1:])]
    return sum(tr[-n:]) / n


def measures(ctx: dict, price: float | None, t_ms: int, prev_t: int | None) -> dict:
    """plain measurements of the moment from the raw bars the terminal sent — numbers only, no verdicts"""
    m: dict = {"sessions": sessions(t_ms), "weekday": WD_RU[dt.datetime.fromtimestamp(t_ms / 1000, dt.timezone.utc).weekday()],
               "hour_utc": dt.datetime.fromtimestamp(t_ms / 1000, dt.timezone.utc).hour,
               "since_prev_trade_min": round((t_ms - prev_t) / 60000) if prev_t else None}
    day = ctx.get("day") or {}
    if price and _f(day.get("high")) and _f(day.get("low")):
        m["dist_day_high_pct"] = round((price / _f(day["high"]) - 1) * 100, 3)
        m["dist_day_low_pct"] = round((price / _f(day["low"]) - 1) * 100, 3)
        m["day_range_pct"] = round((_f(day["high"]) / _f(day["low"]) - 1) * 100, 3)
    for tf in ("H1", "D1"):
        bars = [b for b in ctx.get(tf) or [] if isinstance(b, list) and len(b) >= 5]
        closes = [float(b[4]) for b in bars]
        a = _atr(bars)
        if a and price:
            m[f"atr14_{tf}"] = round(a, 4)
            m[f"atr14_{tf}_pct"] = round(a / price * 100, 3)
        for n in (20, 50, 200):
            s = _sma(closes, n)
            if s and price:
                m[f"vs_sma{n}_{tf}_pct"] = round((price / s - 1) * 100, 3)
    ext = {}
    for sym, q in (ctx.get("ext") or {}).items():          # DXY / US10Y when the broker carries them
        if isinstance(q, dict) and _f(q.get("last")) is not None:
            ext[str(sym)[:16]] = {"last": _f(q.get("last")), "chg_d1_pct": _f(q.get("chg_d1_pct"))}
    if ext:
        m["ext"] = ext
    m["calendar_n"] = len([e for e in ctx.get("calendar") or [] if isinstance(e, dict)])
    return m


class Peter:
    def __init__(self, data_dir: str, env=None, web_dir: str | None = None):
        self.dir = data_dir
        os.makedirs(self.dir, exist_ok=True)
        self.env = env
        self.web = web_dir
        self.lock = threading.Lock()
        self.hits: dict[str, list] = {}
        self.db = sqlite3.connect(os.path.join(self.dir, "peter.sqlite"), check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER, kind TEXT, acct TEXT, json TEXT);
            CREATE TABLE IF NOT EXISTS deals(deal INTEGER PRIMARY KEY, pos INTEGER, t INTEGER, symbol TEXT, dtype TEXT, entry TEXT,
                volume REAL, price REAL, sl REAL, tp REAL, profit REAL, swap REAL, commission REAL, fee REAL, bal_after REAL, json TEXT);
            CREATE TABLE IF NOT EXISTS positions(pos INTEGER PRIMARY KEY, t INTEGER, json TEXT);
            CREATE TABLE IF NOT EXISTS mods(pos INTEGER, t INTEGER, sl REAL, tp REAL, PRIMARY KEY(pos, t));
            CREATE TABLE IF NOT EXISTS candles(symbol TEXT, tf TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, PRIMARY KEY(symbol, tf, t));
            CREATE TABLE IF NOT EXISTS contexts(deal INTEGER PRIMARY KEY, pos INTEGER, kind TEXT, t INTEGER, raw TEXT, measures TEXT);
            CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);""")
        self.db.commit()

    # ------------------------------------------------------------------ config
    def _tok_path(self) -> str:
        return os.path.join(self.dir, "token")

    def token(self) -> str:
        """11.11 (owner has no shell): the token made by the server itself (<run>/data/peter/token, mode 600) wins; else
        CNQ_PETER_TOKEN from /etc/cnq.env. Never logged, never echoed except to the owner endpoint."""
        try:
            with open(self._tok_path(), encoding="utf-8") as f:
                t = f.read().strip()
            if len(t) >= 24:
                return t
        except OSError:
            pass
        env = os.environ if self.env is None else self.env
        return (env.get(TOKEN_ENV) or "").strip()

    def new_token(self) -> str:
        """a fresh token, written atomically with mode 600; the old one stops working at once"""
        t = secrets.token_hex(24)
        p = self._tok_path()
        fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(t)
        os.chmod(p + ".tmp", 0o600)
        os.replace(p + ".tmp", p)
        return t

    def ensure_token(self) -> str:
        t = self.token()
        return t if len(t) >= 24 else self.new_token()

    def _meta(self, k: str, v=None):
        if v is None:
            r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return json.loads(r[0]) if r else None
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, json.dumps(v)))

    def status(self) -> dict:
        """for /v2/health and «Состояние системы»: timestamps only — nothing about the account"""
        with self.lock:
            hb, last = self._meta("last_hb"), self.db.execute("SELECT MAX(t) FROM deals").fetchone()[0]
        ok = len(self.token()) >= 24
        return {"configured": ok, "token_set": ok, "last_hb": hb, "last_deal": last}

    # ------------------------------------------------------------------ ingest
    def ingest(self, headers: dict, body: bytes, ip: str = "local", now: int | None = None) -> tuple[int, dict]:
        now = int(now or time.time() * 1000)
        tok = self.token()
        if len(tok) < 24:
            return 503, {"error": "not_configured"}
        given = str(headers.get("x-cnq-token") or "").strip()
        if not given and str(headers.get("authorization") or "").lower().startswith("bearer "):
            given = str(headers["authorization"])[7:].strip()
        if not given or not hmac.compare_digest(given.encode(), tok.encode()):
            return 401, {"error": "unauthorized"}
        q = [t for t in self.hits.get(ip, []) if now - t < 60_000]
        if len(q) >= RATE_PER_MIN:
            return 429, {"error": "rate"}
        self.hits[ip] = q + [now]
        if len(body or b"") > MAX_BODY:
            return 413, {"error": "too_large"}
        try:
            doc = json.loads((body or b"{}").decode("utf-8"))
            if not isinstance(doc, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return 400, {"error": "bad_json"}
        evs = doc.get("events") if isinstance(doc.get("events"), list) else [doc]
        if len(evs) > MAX_EVENTS:
            return 413, {"error": "too_many_events"}
        acct = str(doc.get("acct") or "")[:64]
        n = 0
        with self.lock:
            for e in evs:
                if isinstance(e, dict) and self._event(e, acct, now):
                    n += 1
            self._meta("last_ingest", now)
            self.db.commit()
            live = self._build(now)
        self._write(live)
        return 200, {"ok": True, "accepted": n, "seen": len(evs)}

    def _event(self, e: dict, acct: str, now: int) -> bool:
        kind = str(e.get("type") or "")[:16]
        if kind not in ("deal", "history", "modify", "heartbeat"):
            return False
        raw = {k: v for k, v in e.items() if k != "ctx"}          # the bars go to candles / contexts, not to the raw log twice
        self.db.execute("INSERT INTO events(t, kind, acct, json) VALUES(?,?,?,?)", (now, kind, acct, json.dumps(raw)[:20000]))
        if kind in ("deal", "history"):
            return self._deal(e, kind)
        if kind == "modify":
            pos, t = _i(e.get("pos")), _i(e.get("t")) or now
            if pos is None:
                return False
            self.db.execute("INSERT OR REPLACE INTO mods VALUES(?,?,?,?)", (pos, t, _f(e.get("sl")), _f(e.get("tp"))))
            return True
        # heartbeat: open positions, balance / equity, the last closed bars
        self._meta("last_hb", now)
        self._meta("account", {"balance": _f(e.get("balance")), "equity": _f(e.get("equity")), "currency": str(e.get("currency") or "")[:8],
                               "leverage": _i(e.get("leverage")), "server_t": _i(e.get("t")), "terminal": str(e.get("terminal") or "")[:40]})
        self.db.execute("DELETE FROM positions")
        for p in e.get("positions") or []:
            if isinstance(p, dict) and _i(p.get("pos")) is not None:
                self.db.execute("INSERT OR REPLACE INTO positions VALUES(?,?,?)", (_i(p["pos"]), now, json.dumps(p)[:4000]))
        self._candles(e.get("candles"))
        return True

    def _candles(self, cs) -> None:
        if not isinstance(cs, dict):
            return
        for sym, tfs in list(cs.items())[:12]:
            if not isinstance(tfs, dict):
                continue
            for tf, bars in tfs.items():
                if tf not in ("M5", "H1", "D1") or not isinstance(bars, list):
                    continue
                rows = [(str(sym)[:16], tf, _i(b[0]), _f(b[1]), _f(b[2]), _f(b[3]), _f(b[4])) for b in bars[-600:]
                        if isinstance(b, list) and len(b) >= 5 and _i(b[0])]
                self.db.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?)", rows)

    def _deal(self, e: dict, kind: str) -> bool:
        deal = _i(e.get("deal"))
        if deal is None:
            return False
        fresh = self.db.execute("SELECT 1 FROM deals WHERE deal=?", (deal,)).fetchone() is None
        row = (deal, _i(e.get("pos")), _i(e.get("t")), str(e.get("symbol") or "")[:16], str(e.get("dtype") or "")[:12],
               str(e.get("entry") or "")[:8], _f(e.get("volume"), 0.0), _f(e.get("price")), _f(e.get("sl")), _f(e.get("tp")),
               _f(e.get("profit"), 0.0), _f(e.get("swap"), 0.0), _f(e.get("commission"), 0.0), _f(e.get("fee"), 0.0),
               _f(e.get("bal_after")), json.dumps({k: v for k, v in e.items() if k != "ctx"})[:4000])
        self.db.execute("INSERT OR REPLACE INTO deals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        ctx = e.get("ctx") if isinstance(e.get("ctx"), dict) else None
        if ctx and row[5] in ("in", "out", "inout", "out_by") and row[2]:
            sym = row[3]
            self._candles({sym: {tf: ctx.get(tf) for tf in ("M5", "H1", "D1") if ctx.get(tf)}})
            prev = self.db.execute("SELECT MAX(t) FROM deals WHERE entry='in' AND t<?", (row[2],)).fetchone()[0]
            m = measures(ctx, row[7], row[2], prev)
            m["calendar_ours"] = self._our_calendar(row[2])
            self.db.execute("INSERT OR REPLACE INTO contexts VALUES(?,?,?,?,?,?)",
                            (deal, row[1], "entry" if row[5] == "in" else "exit", row[2], json.dumps(ctx)[:200000], json.dumps(m)))
        return fresh

    def _our_calendar(self, t_ms: int) -> list:
        """events of web/calendar.json within ±24 h of the moment (the macro calendar the system already keeps)"""
        if not self.web:
            return []
        try:
            d = json.load(open(os.path.join(self.web, "calendar.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []
        return [{"t": e.get("time"), "name": e.get("name"), "importance": e.get("importance")} for e in d.get("events") or []
                if isinstance(e, dict) and _i(e.get("time")) and abs(_i(e["time"]) - t_ms) <= 86400_000][:12]

    # ------------------------------------------------------------------ the desk
    def trades(self) -> tuple[list[dict], list[dict]]:
        rows = self.db.execute("SELECT deal, pos, t, symbol, dtype, entry, volume, price, sl, tp, profit, swap, commission, fee, bal_after "
                               "FROM deals WHERE pos IS NOT NULL AND pos != 0 ORDER BY t, deal").fetchall()
        mods = {}
        for pos, t, sl, tp in self.db.execute("SELECT pos, t, sl, tp FROM mods ORDER BY t"):
            mods.setdefault(pos, []).append({"t": t, "sl": sl, "tp": tp})
        ctx = {r[0]: json.loads(r[1]) for r in self.db.execute("SELECT deal, measures FROM contexts")}
        by: dict[int, dict] = {}
        for d, pos, t, sym, dtype, entry, vol, px, sl, tp, pr, sw, cm, fe, bal in rows:
            p = by.setdefault(pos, {"pos": pos, "symbol": sym, "side": None, "in_vol": 0.0, "in_px": 0.0, "out_vol": 0.0, "out_px": 0.0,
                                    "open_t": None, "close_t": None, "pnl": 0.0, "sl": None, "tp": None, "bal_after": None,
                                    "entry_ctx": None, "exit_ctx": None, "deals": 0})
            p["deals"] += 1
            eff = (pr or 0) + (sw or 0) + (cm or 0) + (fe or 0)
            if entry == "in":
                p["side"] = p["side"] or ("LONG" if dtype == "buy" else "SHORT")
                p["in_px"] = (p["in_px"] * p["in_vol"] + (px or 0) * vol) / (p["in_vol"] + vol) if p["in_vol"] + vol else px
                p["in_vol"] += vol
                p["open_t"] = p["open_t"] or t
                p["entry_ctx"] = p["entry_ctx"] or ctx.get(d)
            else:
                p["out_px"] = (p["out_px"] * p["out_vol"] + (px or 0) * vol) / (p["out_vol"] + vol) if p["out_vol"] + vol else px
                p["out_vol"] += vol
                p["close_t"] = t
                p["bal_after"] = bal
                p["exit_ctx"] = ctx.get(d) or p["exit_ctx"]
            p["pnl"] += eff
            p["sl"], p["tp"] = (sl if sl else p["sl"]), (tp if tp else p["tp"])
        for pos, ms in mods.items():
            if pos in by:
                by[pos]["mods"] = ms[-10:]
        closed, opened = [], []
        for p in by.values():
            if not p["side"]:
                continue
            p["in_px"], p["out_px"] = round(p["in_px"], 5) if p["in_px"] else None, round(p["out_px"], 5) if p["out_vol"] else None
            p["pnl"] = round(p["pnl"], 2)
            if p["out_vol"] >= p["in_vol"] - 1e-9 and p["close_t"]:
                base = (p["bal_after"] - p["pnl"]) if p["bal_after"] else None
                p["pct"] = round(p["pnl"] / base * 100, 4) if base and base > 0 else None
                p["hold_min"] = round((p["close_t"] - p["open_t"]) / 60000) if p["open_t"] else None
                closed.append(p)
            else:
                opened.append(p)
        closed.sort(key=lambda p: p["close_t"])
        return closed, opened

    def _build(self, now: int) -> dict:
        closed, partial = self.trades()
        acct = self._meta("account") or {}
        live = {}
        for (j,) in self.db.execute("SELECT json FROM positions"):
            try:
                x = json.loads(j)
                live[_i(x.get("pos"))] = x
            except ValueError:
                pass
        opened = []
        for p in partial:
            lp = live.get(p["pos"]) or {}
            bal = acct.get("balance")
            fl = _f(lp.get("profit"))
            opened.append({**{k: p[k] for k in ("pos", "symbol", "side", "in_px", "open_t", "sl", "tp", "entry_ctx")},
                           "volume": round(p["in_vol"] - p["out_vol"], 4), "price": _f(lp.get("price")), "sl": _f(lp.get("sl")) or p["sl"],
                           "tp": _f(lp.get("tp")) or p["tp"], "float_usd": fl, "float_pct": round(fl / bal * 100, 3) if fl is not None and bal else None})
        for pos, lp in live.items():                # positions opened before the bridge was attached (no IN deal stored)
            if pos is not None and not any(o["pos"] == pos for o in opened):
                opened.append({"pos": pos, "symbol": lp.get("symbol"), "side": "LONG" if lp.get("side") == "buy" else "SHORT",
                               "in_px": _f(lp.get("open_price")), "open_t": _i(lp.get("open_t")), "volume": _f(lp.get("volume")),
                               "price": _f(lp.get("price")), "sl": _f(lp.get("sl")), "tp": _f(lp.get("tp")), "float_usd": _f(lp.get("profit")),
                               "float_pct": None, "entry_ctx": None})
        eq, curve, peak, dd = START_USD, [], START_USD, 0.0
        if closed:
            curve.append([closed[0]["open_t"] or closed[0]["close_t"], START_USD])
        for p in closed:
            if p.get("pct") is not None:
                eq *= 1 + p["pct"] / 100
            p["notional_after"] = round(eq, 2)
            curve.append([p["close_t"], round(eq, 2)])
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak * 100)
        pcts = [p["pct"] for p in closed if p.get("pct") is not None]
        wins = [p for p in closed if p["pnl"] > 0]
        span_w = max(1.0, ((closed[-1]["close_t"] - (closed[0]["open_t"] or closed[0]["close_t"])) / (7 * 86400_000))) if closed else None
        holds = [p["hold_min"] for p in closed if p.get("hold_min") is not None]
        dist = []
        lo = 0
        for hi, lab in HOLD_BUCKETS:
            dist.append({"label": lab, "n": sum(1 for h in holds if lo <= h < hi)})
            lo = hi
        stats = {"trades": len(closed), "wins": len(wins), "winrate": round(len(wins) / len(closed), 4) if closed else None,
                 "avg_pct": round(sum(pcts) / len(pcts), 4) if pcts else None,
                 "avg_usd_notional": round((eq - START_USD) / len(closed), 2) if closed else None,
                 "best_pct": max(pcts) if pcts else None, "worst_pct": min(pcts) if pcts else None,
                 "max_dd_pct": round(dd, 2), "trades_per_week": round(len(closed) / span_w, 2) if span_w else None,
                 "hold_median_min": sorted(holds)[len(holds) // 2] if holds else None, "hold_dist": dist,
                 "net_usd_account": round(sum(p["pnl"] for p in closed), 2)}
        return {"asof": now, "name": NAME, "kind": "paper", "source": "MetaTrader 5 · мост CryptoNovaBridge", "start_usd": START_USD,
                "equity_notional": round(eq, 2), "pnl_notional": round(eq - START_USD, 2), "pnl_pct": round((eq / START_USD - 1) * 100, 3),
                "account": {"currency": acct.get("currency"), "leverage": acct.get("leverage")},      # never the balance in $
                "last_hb": self._meta("last_hb"), "stats": stats, "curve": curve[-500:],
                "open": opened, "closed": list(reversed(closed))[:200],
                "about_ru": f"«{NAME}» — наблюдение за сделками трейдера по золоту в MetaTrader 5. Только наблюдение, ордеров нет. "
                            "Кривая — условные $10 000, которые растут и падают на его % от баланса по каждой закрытой сделке."}

    def _write(self, doc: dict) -> None:
        p = os.path.join(self.dir, "peter_live.json")
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)

    def desk(self, now: int | None = None) -> tuple[int, dict]:
        """GET /v2/owner/peter"""
        with self.lock:
            d = self._build(int(now or time.time() * 1000))
        d["configured"] = len(self.token()) >= 24
        return 200, {"name": "peter", "title": NAME, "asof": d["asof"], "data": d, "configured": len(self.token()) >= 24}

    def token_view(self, rotate: bool = False) -> tuple[int, dict]:
        """GET /v2/owner/peter/token (made on the first call when missing) · POST /v2/owner/peter/token/rotate — owner only"""
        try:
            with self.lock:
                tok = self.new_token() if rotate else self.ensure_token()
        except OSError as e:
            return 500, {"error": "cannot_store", "detail": type(e).__name__}
        return 200, {"token": tok, "fingerprint": hashlib.sha256(tok.encode()).hexdigest()[:8], "url": URL,
                     "whitelist": URL.split("/v2/")[0]}
