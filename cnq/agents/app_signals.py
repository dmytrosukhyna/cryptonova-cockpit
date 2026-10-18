"""CRYPTO NOVΛ app signals — tracking, honest outcomes, strategy profile and improvement rules → web/app_signals.json.

Pipeline on the owner's side: Telegram «Ai Golden Crypto (Auto Bot)» → Cornix → orders on his BingX → TradeReader → app backend.
The app reads the signals from a PUBLIC JSON API (no auth headers; see the app repo network/…/utils/URLs.kt, dto/signal/*.kt):
  GET https://app.crypto-capital.app/api/signals/active      GET https://app.crypto-capital.app/api/signals/history
Response = CommonResponse {code (1 ok / 0 error), body, error_message, date}; body = list of signals in snake_case:
signal_id, name, entry_point (string: one price or a range «a - b»), stop_lost, actual_price, actual_precent, leverage_x, date (ms),
position_side, targets [{level, price, precent, is_completed}], icon_url, is_vip. The parser is tolerant (camelCase, numbers as
strings with spaces/commas, ranges, LONG/Long/buy, s/ms/ISO dates, body wrapped in a dict) and lists unknown shapes in notes_ru.

Each run:
 1. both lists are fetched and upserted into agents.sqlite `app_signals` (never deleted — the API may only return a recent window,
    our table becomes the full history). A signal that leaves /active is marked closed.
 2. Outcome engine, independent of the app's is_completed flags: 15m perpetual candles from the first FULL 15m candle after the
    signal time (no look into prices before the signal) — BingX swap (cnq.bingx.rest), fallback OKX swap, then Binance spot mirror.
    Candles live in `app_signal_candles` (shared per coin) and are fetched only while a signal is unfinished: until it is resolved,
    plus 7 days after a stop (to see whether the price came back to TP1). Finished signals keep their result in the row and their
    candles are pruned (the DB is committed to git).
      entry: one price → a candle touches it; range → a candle touches any price in it (fill at the candle open if it opened inside
      the range, else at the near edge). Not touched within 48 h, or TP1 / stop reached first → «не вошёл».
      after the fill: stop vs TP1 — which came first, the farthest TP before the stop, time to TP1, max favourable / adverse move.
      One 15m candle touching both the stop and a target → the stop counts first (cautious). No TP on the fill candle unless the
      candle opened at the entry. No exit after 14 days → closed at the market («по времени»).
    Exit policies, in % of price (net of 0.05 % fee per side + 0.02 % slippage = 0.12 % per trade) and in R (R = entry→stop):
      app        «как в приложении» — the farthest TP reached is the result (the app's showcase, not executable: nobody knows in
                 advance which TP will be the last);
      tp1        everything at TP1;   ladder  equal parts at each TP, stop stays;   ladder_be  equal parts, stop → entry after TP1;
      last       everything at the last TP or the stop.
    Leverage only multiplies the % (both reported); a leveraged loss is capped at −100 % of the margin (≈ liquidation at a move of
    100 / leverage − 0.5 %).
 3. Strategy profile from candles at signal time: direction vs 4h trend (EMA50/EMA200), RSI14 1h/4h, distance from the 24 h / 7 d
    high and low, last 4 h move, ATR %, BTC 4h trend, hour / weekday, coin, leverage, TP / stop distances and R:R → entry type
    («покупка отката в восходящем тренде», «шорт на перекупленности», …) and simple splits of winners vs losers.
 4. Proposals: a handful of explainable rules evaluated on the stored history, before / after with sample sizes; anything with fewer
    than 30 resolved signals is «рано судить». No number is invented — every figure comes from the stored rows.
Stdlib only; the API fetch and the candle fetch are injectable (tests run without network)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_BASE = "https://app.crypto-capital.app"
API_ACTIVE = API_BASE + "/api/signals/active"
API_HISTORY = API_BASE + "/api/signals/history"
# The Android app uses Ktor's Android engine (HttpURLConnection) → the system Dalvik user agent; JSON content type like ApiClient.
APP_HEADERS = {"User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 7 Build/UQ1A.240205.004)",
               "Accept": "application/json", "Content-Type": "application/json", "Accept-Charset": "UTF-8"}
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36",
              "Accept": "application/json"}
PLAIN_UA = {"User-Agent": "Mozilla/5.0 cryptonova/1.0"}

M15, H1, H4, DAY = 900_000, 3_600_000, 14_400_000, 86_400_000
IV_MS = {"15m": M15, "1h": H1, "4h": H4}
FILL_WINDOW_MS = 48 * H1
MAX_HOLD_MS = 14 * DAY
AFTER_MS = 7 * DAY
FEE_PCT, SLIP_PCT = 0.05, 0.02
COST_PCT = 2 * FEE_PCT + SLIP_PCT
MIN_SAMPLE = 30
LEV_CAP = 10
SIM_VERSION = 1
MAX_FETCH_FAIL = 6

POLICIES = [   # key, name, executable
    ("app", "Как в приложении: итог = самый дальний достигнутый TP", False),
    ("tp1", "Всё закрыть на TP1", True),
    ("ladder", "Равными частями на каждом TP, стоп на месте", True),
    ("ladder_be", "Равными частями на каждом TP, после TP1 стоп в точку входа", True),
    ("last", "Всё держать до последнего TP или стопа", True),
]
BASELINE = "ladder"
WEEKDAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

KNOWN_KEYS = {"signal_id", "name", "entry_point", "stop_lost", "actual_price", "actual_precent", "leverage_x", "date",
              "position_side", "targets", "icon_url", "is_vip"}
KNOWN_TARGET_KEYS = {"level", "price", "precent", "is_completed"}


# ------------------------------------------------------------------------------------------------------------ parsing
def _snake(k: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", str(k)).lower()


def _g(d: dict, *keys):
    """First non-empty value among keys, matching snake_case / camelCase / any case."""
    if not isinstance(d, dict):
        return None
    norm = {_snake(k): v for k, v in d.items()}
    for k in keys:
        v = norm.get(_snake(k))
        if v is not None and v != "":
            return v
    return None


def parse_num(x) -> float | None:
    """'65000', '65 000,5', '1,234.5', '0,1234', '1.234.567', '$0.52', '12%', 0.5 → float; None when not a number."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x) if math.isfinite(x) else None
    s = str(x).strip()
    for ch in (" ", " ", " ", " ", "'", "$", "%", "−"):
        s = s.replace(ch, "-" if ch == "−" else "")
    s = re.sub(r"(?i)usdt|usd", "", s).lstrip("+")
    if not s:
        return None
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except ValueError:
        pass
    neg = s.startswith("-")
    s = s.lstrip("-")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 2:
            s = "".join(parts)
        else:
            ip, fp = parts
            thousands = len(fp) == 3 and ip not in ("", "0") and ip.isdigit() and fp.isdigit()
            s = ip + fp if thousands else ip + "." + fp
    elif s.count(".") > 1:
        s = s.replace(".", "")
    m = re.match(r"^\d*\.?\d+(?:[eE][-+]?\d+)?$", s)
    if not m:
        return None
    v = float(s)
    return -v if neg else v


_RANGE_SPLIT = re.compile(r"\s*(?:[–—~/;|]|\s-\s|\bto\b|\.{2,}|,\s+)\s*|(?<=\d)-(?=\d)")


def parse_entry(x) -> tuple[float, float] | None:
    """One price → (p, p); a range «a - b», «a–b», «a / b», [a, b], {from, to} → (min, max). None when not parsable."""
    if x is None:
        return None
    if isinstance(x, (list, tuple)):
        vals = [v for v in (parse_num(i) if not isinstance(i, dict) else parse_num(_g(i, "price", "value")) for i in x) if v and v > 0]
        return (min(vals), max(vals)) if vals else None
    if isinstance(x, dict):
        vals = [v for v in (parse_num(_g(x, k)) for k in ("from", "to", "min", "max", "low", "high", "price", "value")) if v and v > 0]
        return (min(vals), max(vals)) if vals else None
    v = parse_num(x)
    if v is not None and v > 0:
        return (v, v)
    parts = [p for p in _RANGE_SPLIT.split(str(x).strip()) if p and p.strip()]
    vals = [v for v in (parse_num(p) for p in parts) if v is not None and v > 0]
    return (min(vals), max(vals)) if vals else None


def parse_side(x) -> str | None:
    s = str(x or "").strip().upper()
    if not s:
        return None
    if "SHORT" in s or s in ("SELL", "S", "ASK") or "ПРОДА" in s or "ШОРТ" in s:
        return "SHORT"
    if "LONG" in s or s in ("BUY", "L", "BID") or "ПОКУП" in s or "ЛОНГ" in s:
        return "LONG"
    return None


def parse_symbol(name) -> str | None:
    """'BTC/USDT', 'BTCUSDT', 'BTC-USDT', '#BTC/USDT', 'btc_usdt', 'BTC/USDT.P', 'BTCUSDT PERP', 'BTC' → 'BTC-USDT' (BingX perp)."""
    s = str(name or "").upper().strip()
    s = re.sub(r"[#$\s]", "", s)
    for _ in range(2):
        s = re.sub(r"(\.P|PERP|PERPETUAL|-SWAP|:USDT|\.PERP|SWAP)$", "", s)
    base = None
    for sep in ("/", "-", "_", ":"):
        if sep in s:
            base = s.split(sep, 1)[0]
            break
    if base is None:
        base = s
        for q in ("USDT", "USDC", "BUSD", "USD"):
            if s.endswith(q) and len(s) > len(q):
                base = s[: -len(q)]
                break
    base = re.sub(r"[^A-Z0-9]", "", base)
    return f"{base}-USDT" if base else None


def parse_time(x) -> int | None:
    """ms / s / µs epoch (number or digit string) or ISO-8601 → epoch ms."""
    if x is None or isinstance(x, bool):
        return None
    v = None
    if isinstance(x, (int, float)):
        v = float(x)
    else:
        s = str(x).strip()
        if re.fullmatch(r"\d+(\.\d+)?", s):
            v = float(s)
        else:
            try:
                d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                return int(d.timestamp() * 1000)
            except ValueError:
                return None
    if v is None or v <= 0:
        return None
    if v > 1e17:
        v /= 1e6        # ns
    elif v > 1e14:
        v /= 1e3        # µs
    elif v < 1e11:
        v *= 1e3        # seconds
    return int(v)


def parse_leverage(x) -> float:
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x) if x >= 1 else 1.0
    m = re.search(r"\d+(?:[.,]\d+)?", str(x or ""))
    v = float(m.group(0).replace(",", ".")) if m else 1.0
    return v if v >= 1 else 1.0


def _parse_targets(raw, problems: list) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [p for p in re.split(r"[;,|\s]+", raw) if p]
    if isinstance(raw, dict):
        raw = [dict(v, level=v.get("level", k)) if isinstance(v, dict) else {"level": k, "price": v} for k, v in raw.items()]
    out = []
    for i, t in enumerate(raw if isinstance(raw, list) else []):
        if isinstance(t, dict):
            lvl = parse_num(_g(t, "level", "lvl", "index", "number", "tp"))
            px = parse_num(_g(t, "price", "value", "target", "tp_price"))
            pct = parse_num(_g(t, "precent", "percent", "pct", "profit"))
            done = _g(t, "is_completed", "completed", "done", "hit", "reached", "is_reached")
            done = str(done).strip().lower() in ("true", "1", "yes") if not isinstance(done, bool) else done
            extra = set(_snake(k) for k in t) - KNOWN_TARGET_KEYS
            if extra:
                problems.append("target_keys:" + ",".join(sorted(extra)))
        else:
            lvl, px, pct, done = None, parse_num(t), None, False
        if px is None or px <= 0:
            problems.append("target_price")
            continue
        out.append({"level": int(lvl) if lvl is not None else i + 1, "price": px, "app_pct": pct, "done": bool(done)})
    out.sort(key=lambda r: r["level"])
    return out


def normalize(raw: dict) -> dict:
    """One API item → plain dict; `problems` lists what could not be read (never a guess)."""
    problems: list[str] = []
    if not isinstance(raw, dict):
        return {"signal_id": None, "problems": ["not_a_dict"]}
    name = _g(raw, "name", "pair", "symbol", "coin", "ticker")
    symbol = parse_symbol(name)
    entry = parse_entry(_g(raw, "entry_point", "entry", "entry_price", "entry_zone", "entries"))
    sl = parse_num(_g(raw, "stop_lost", "stop_loss", "stoploss", "sl", "stop"))
    side_raw = _g(raw, "position_side", "side", "direction", "type", "position")
    side = parse_side(side_raw)
    t = parse_time(_g(raw, "date", "created_at", "time", "timestamp", "created"))
    targets = _parse_targets(_g(raw, "targets", "take_profits", "tps", "take_profit"), problems)
    lev = parse_leverage(_g(raw, "leverage_x", "leverage", "lev"))
    sid = _g(raw, "signal_id", "id", "signalid", "uuid")
    if sid is None:
        sid = "h" + hashlib.sha1(f"{name}|{t}|{_g(raw, 'entry_point', 'entry')}".encode()).hexdigest()[:12]
        problems.append("no_id")
    for what, v in (("name", symbol), ("entry", entry), ("stop", sl), ("date", t)):
        if not v:
            problems.append(what)
    if not targets:
        problems.append("targets")
    if not side and side_raw is not None:
        problems.append(f"side:{str(side_raw)[:12]}")
    # Geometry: LONG has stop < entry < targets, SHORT the opposite. Trust the prices when the label is missing or contradicts them.
    if entry and sl and targets:
        tp1 = targets[0]["price"]
        geo = "LONG" if sl < entry[0] and tp1 > entry[1] else "SHORT" if sl > entry[1] and tp1 < entry[0] else None
        if geo is None:
            problems.append("geometry")
        elif side and side != geo:
            problems.append("side_conflict")
            side = geo
        elif not side:
            side = geo
    if not side:
        problems.append("side")
    extra = set(_snake(k) for k in raw) - KNOWN_KEYS
    vip = _g(raw, "is_vip", "vip")
    done_levels = [x["level"] for x in targets if x["done"]]
    return {"signal_id": str(sid), "name": str(name) if name is not None else None, "symbol": symbol,
            "coin": symbol.split("-")[0] if symbol else None, "side": side,
            "entry_lo": entry[0] if entry else None, "entry_hi": entry[1] if entry else None, "sl": sl, "leverage": lev,
            "t": t, "targets": targets, "is_vip": bool(vip) if isinstance(vip, bool) else str(vip).lower() in ("true", "1"),
            "app_best_tp": max(done_levels) if done_levels else 0,
            "actual_price": parse_num(_g(raw, "actual_price", "current_price")),
            "actual_pct": parse_num(_g(raw, "actual_precent", "actual_percent", "pnl")),
            "problems": problems, "extra_keys": sorted(extra)}


def extract_list(resp) -> tuple[list | None, str | None]:
    """CommonResponse {code, body, error_message} / a plain list / body wrapped in a dict → (items, error)."""
    if isinstance(resp, list):
        return resp, None
    if not isinstance(resp, dict):
        return None, f"ответ не JSON-объект ({type(resp).__name__})"
    body = None
    for k in ("body", "data", "result", "signals", "items"):
        if k in resp and resp[k] is not None:
            body = resp[k]
            break
    if isinstance(body, dict):
        for k in ("signals", "items", "list", "data", "content", "rows", "results"):
            if isinstance(body.get(k), list):
                body = body[k]
                break
    if isinstance(body, list):
        return body, None
    err = _g(resp, "error_message", "message", "msg", "error")
    return None, f"code={resp.get('code')} {str(err)[:120] if err else 'нет списка в ответе'}"


# ------------------------------------------------------------------------------------------------------------ network
def _get_api(url: str, timeout: int = 20):
    last = None
    for headers in (APP_HEADERS, BROWSER_UA):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (401, 403, 406, 429):
                raise
    raise last  # type: ignore[misc]


def _get_plain(url: str, timeout: int = 10):
    with urllib.request.urlopen(urllib.request.Request(url, headers=PLAIN_UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


class KlineSource:
    """klines(symbol 'BTC-USDT', interval '15m'|'1h'|'4h', start_ms, end_ms) → [[t, o, h, l, c], …] ascending.
    BingX swap first (cnq.bingx.rest, 1 request/s), then OKX swap, then the Binance spot mirror. `used` counts answers per source."""

    def __init__(self, get_json=None, bingx=None):
        self.get_json = get_json or _get_plain
        self._bx = bingx
        self.used: dict[str, int] = {}
        self.errors: list[str] = []

    def _bingx(self, symbol, iv, start, end):
        if self._bx is None:
            from ..bingx.rest import BingXRest
            self._bx = BingXRest()
        return [[c.open_time, c.open, c.high, c.low, c.close] for c in self._bx.klines(symbol, iv, start_ms=start, end_ms=end, limit=1440)]

    @staticmethod
    def _base_scale(symbol):
        base = symbol.split("-")[0]
        if base.startswith("1000") and len(base) > 4:   # BingX 1000PEPE = 1000 × PEPE price elsewhere
            return base[4:], 1000.0
        return base, 1.0

    def _okx(self, symbol, iv, start, end):
        base, k = self._base_scale(symbol)
        bar = {"15m": "15m", "1h": "1H", "4h": "4H"}[iv]
        ms = IV_MS[iv]
        out, t, pages = {}, start, 0
        while t <= end and pages < 15:
            hi = min(end, t + 100 * ms - 1)
            d = self.get_json(f"https://www.okx.com/api/v5/market/history-candles?instId={base}-USDT-SWAP&bar={bar}"
                              f"&before={t - 1}&after={hi + 1}&limit=100")
            if str(d.get("code")) != "0":
                raise RuntimeError(f"okx code={d.get('code')} {d.get('msg')}")
            for r in d.get("data") or []:
                ts = int(r[0])
                if start <= ts <= end:
                    out[ts] = [ts, float(r[1]) * k, float(r[2]) * k, float(r[3]) * k, float(r[4]) * k]
            t, pages = hi + 1, pages + 1
            time.sleep(0.12)
        return [out[x] for x in sorted(out)]

    def _binance(self, symbol, iv, start, end):
        base, k = self._base_scale(symbol)
        out, t = {}, start
        for _ in range(3):
            if t > end:
                break
            rows = self.get_json(f"https://data-api.binance.vision/api/v3/klines?symbol={base}USDT&interval={iv}"
                                 f"&startTime={t}&endTime={end}&limit=1000")
            if not isinstance(rows, list) or not rows:
                break
            for r in rows:
                out[int(r[0])] = [int(r[0]), float(r[1]) * k, float(r[2]) * k, float(r[3]) * k, float(r[4]) * k]
            t = int(rows[-1][0]) + IV_MS[iv]
        return [out[x] for x in sorted(out)]

    def __call__(self, symbol, iv, start, end):
        errs, empty = [], False
        for name, fn in (("BingX", self._bingx), ("OKX", self._okx), ("Binance spot", self._binance)):
            try:
                rows = fn(symbol, iv, start, end)
            except Exception as e:  # noqa: BLE001 — any source may refuse; try the next one
                errs.append(f"{name}: {str(e)[:80]}")
                continue
            if rows:
                self.used[name] = self.used.get(name, 0) + 1
                return rows
            empty = True
        if errs and not empty:
            self.errors.append(f"{symbol} {iv}: " + "; ".join(errs))
            raise RuntimeError("; ".join(errs))
        return []


class _Budget:
    def __init__(self, seconds: float, requests: int):
        self.deadline = time.monotonic() + seconds
        self.left = requests
        self.used = 0

    def take(self) -> bool:
        if self.left <= 0 or time.monotonic() > self.deadline:
            return False
        self.left -= 1
        self.used += 1
        return True


# ------------------------------------------------------------------------------------------------------------ storage
SCHEMA = """
CREATE TABLE IF NOT EXISTS app_signals (
  signal_id TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER, status TEXT, closed_seen INTEGER,
  raw TEXT, name TEXT, symbol TEXT, side TEXT, entry_lo REAL, entry_hi REAL, sl REAL, leverage REAL, t_signal INTEGER,
  targets TEXT, is_vip INTEGER, app_best_tp INTEGER, actual_price REAL, actual_pct REAL, problems TEXT, plan TEXT,
  sim TEXT, features TEXT, sim_version INTEGER, path_until INTEGER, ctx_done INTEGER DEFAULT 0,
  resolved_at INTEGER, done INTEGER DEFAULT 0, fetch_fail INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS app_signal_candles (
  symbol TEXT, interval TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, PRIMARY KEY (symbol, interval, t));
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _plan_key(s: dict) -> str:
    return json.dumps([s.get("symbol"), s.get("side"), s.get("entry_lo"), s.get("entry_hi"), s.get("sl"), s.get("t"),
                       [x["price"] for x in s.get("targets") or []]])


def upsert(conn, items_by_status: dict[str, list], now: int, active_ok: bool) -> dict:
    """items_by_status = {"active": [...raw...], "closed": [...raw...]}. Keeps every signal ever seen; returns parse stats."""
    ensure_schema(conn)
    seen_active, stats = set(), {"items": 0, "new": 0, "problems": {}, "extra_keys": set(), "examples": []}
    for status in ("active", "closed"):
        for raw in items_by_status.get(status) or []:
            s = normalize(raw)
            if not s.get("signal_id"):
                stats["problems"]["not_a_dict"] = stats["problems"].get("not_a_dict", 0) + 1
                continue
            stats["items"] += 1
            for p in s["problems"]:
                key = p.split(":")[0]
                stats["problems"][key] = stats["problems"].get(key, 0) + 1
                if key in ("entry", "stop", "targets", "date", "name", "side", "geometry") and len(stats["examples"]) < 3:
                    stats["examples"].append({k: raw.get(k) for k in list(raw)[:12]} if isinstance(raw, dict) else str(raw)[:200])
            stats["extra_keys"].update(s["extra_keys"])
            sid = s["signal_id"]
            if status == "active":
                seen_active.add(sid)
            row = conn.execute("SELECT status, plan FROM app_signals WHERE signal_id=?", (sid,)).fetchone()
            plan = _plan_key(s)
            vals = dict(raw=json.dumps(raw, ensure_ascii=False), name=s["name"], symbol=s["symbol"], side=s["side"],
                        entry_lo=s["entry_lo"], entry_hi=s["entry_hi"], sl=s["sl"], leverage=s["leverage"], t_signal=s["t"],
                        targets=json.dumps(s["targets"]), is_vip=int(s["is_vip"]), app_best_tp=s["app_best_tp"],
                        actual_price=s["actual_price"], actual_pct=s["actual_pct"], problems=json.dumps(s["problems"]), plan=plan,
                        last_seen=now, status=status)
            if row is None:
                stats["new"] += 1
                if vals["t_signal"] is None and status == "active":
                    vals["t_signal"] = now          # an active signal without a date: first seen ≈ published
                vals.update(first_seen=now, closed_seen=None)
                cols = ",".join(["signal_id"] + list(vals))
                conn.execute(f"INSERT INTO app_signals ({cols}) VALUES ({','.join('?' * (len(vals) + 1))})", [sid] + list(vals.values()))
            else:
                if vals["t_signal"] is None:
                    vals.pop("t_signal")
                if row[0] == "active" and status == "closed":
                    vals["closed_seen"] = now
                sets = ",".join(f"{k}=?" for k in vals)
                conn.execute(f"UPDATE app_signals SET {sets} WHERE signal_id=?", list(vals.values()) + [sid])
                if row[1] != plan:   # the plan changed on the server → recompute from scratch
                    conn.execute("UPDATE app_signals SET sim=NULL, done=0, path_until=NULL, ctx_done=0, resolved_at=NULL, "
                                 "features=NULL WHERE signal_id=?", (sid,))
    if active_ok:
        ids = list(seen_active)
        q = "UPDATE app_signals SET status='closed', closed_seen=COALESCE(closed_seen, ?) WHERE status='active'"
        if ids:
            q += f" AND signal_id NOT IN ({','.join('?' * len(ids))})"
        conn.execute(q, [now] + ids)
    conn.commit()
    stats["extra_keys"] = sorted(stats["extra_keys"])
    return stats


def _store_candles(conn, symbol, iv, rows) -> None:
    conn.executemany("INSERT OR REPLACE INTO app_signal_candles (symbol, interval, t, o, h, l, c) VALUES (?,?,?,?,?,?,?)",
                     [(symbol, iv, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows])


def _candles(conn, symbol, iv, start, end) -> list[list]:
    return [list(r) for r in conn.execute("SELECT t, o, h, l, c FROM app_signal_candles WHERE symbol=? AND interval=? AND t>=? AND t<=? "
                                          "ORDER BY t", (symbol, iv, start, end))]


def _have(conn, symbol, iv, start, end, share=0.9) -> bool:
    n = conn.execute("SELECT COUNT(*) FROM app_signal_candles WHERE symbol=? AND interval=? AND t>=? AND t<?",
                     (symbol, iv, start, end)).fetchone()[0]
    return n >= share * max(1, (end - start) // IV_MS[iv])


def _fetch_range(conn, klines, budget, symbol, iv, start, end) -> tuple[bool, int | None]:
    """Fetch [start, end] in 1440-candle windows. → (complete, open time of the last candle received)."""
    ms = IV_MS[iv]
    step = ms * 1440
    t, last = start, None
    while t <= end:
        if not budget.take():
            return False, last
        rows = klines(symbol, iv, t, min(t + step - 1, end))
        rows = [r for r in rows or [] if t <= int(r[0]) <= end]
        if rows:
            _store_candles(conn, symbol, iv, rows)
            last = int(rows[-1][0])
            t = last + ms
        else:
            t += step
    return True, last


# ------------------------------------------------------------------------------------------------------------ simulation
def _first_full(t: int) -> int:
    return (t // M15 + 1) * M15


def simulate(sig: dict, candles: list, now: int) -> dict:
    """Path of one signal over 15m candles [[t, o, h, l, c], …]. sig: side, entry_lo, entry_hi, sl, tps (prices by level), t, leverage."""
    s = 1 if sig["side"] == "LONG" else -1
    lo, hi, sl, tps, t0 = sig["entry_lo"], sig["entry_hi"], sig["sl"], list(sig["tps"]), sig["t"]
    lev = max(1.0, float(sig.get("leverage") or 1.0))
    n = len(tps)
    res = {"v": SIM_VERSION, "state": "pending", "n_tps": n, "best_tp": 0, "first": None, "ended": None, "resolved_at": None,
           "policies": {}, "ambiguous": 0}
    p0 = _first_full(t0)
    cs = [c for c in candles if c[0] >= p0]
    if not cs:
        return res
    res["ref_price"] = cs[0][1]
    res["last_price"], res["last_t"] = cs[-1][4], cs[-1][0]
    mid = (lo + hi) / 2
    if abs(cs[0][1] / mid - 1) > 0.5:
        res.update(state="bad_data", reason="price_mismatch")
        return res
    tp1 = tps[0]
    fill_i = fill = None
    at_open = False
    for i, c in enumerate(cs):
        if c[0] >= t0 + FILL_WINDOW_MS:
            break
        o, h, l = c[1], c[2], c[3]
        if l <= hi and h >= lo:
            if lo <= o <= hi:
                fill, at_open = o, True
            else:
                fill = hi if o > hi else lo
            fill_i = i
            break
        fav, adv = (h, l) if s > 0 else (l, h)
        if s * (fav - tp1) >= 0:
            res.update(state="not_filled", reason="tp1_before_entry", resolved_at=c[0])
            return res
        if s * (adv - sl) <= 0:
            res.update(state="not_filled", reason="sl_before_entry", resolved_at=c[0])
            return res
    if fill_i is None:
        if cs[-1][0] + M15 >= t0 + FILL_WINDOW_MS or now >= t0 + FILL_WINDOW_MS + DAY:
            res.update(state="not_filled", reason="no_touch", resolved_at=t0 + FILL_WINDOW_MS)
        return res

    F = fill
    fill_t = cs[fill_i][0]
    risk_pct = s * (F - sl) / F * 100
    if risk_pct <= 0:
        res.update(state="bad_data", reason="stop_inside_entry")
        return res

    def P(px):
        return s * (px - F) / F * 100

    liq_move = max(0.1, 100.0 / lev - 0.5) if lev > 1 else None
    res.update(state="open", fill=F, fill_t=fill_t, fill_delay_h=round((fill_t - t0) / H1, 2), risk_pct=round(risk_pct, 4),
               liq_move_pct=round(liq_move, 3) if liq_move else None, liq_risk=bool(liq_move and risk_pct >= liq_move))
    best, tp_t = 0, [None] * n
    be_t, be_best, sl_t, liq_t, end_t, ended, exit_px, end_i = None, 0, None, None, None, None, None, None
    mfe = mae = 0.0
    for j in range(fill_i, len(cs)):
        tc, o, h, l, cl = cs[j][:5]
        if tc >= fill_t + MAX_HOLD_MS:
            ended, end_t, exit_px, end_i = "timeout", tc, cs[j - 1][4], j - 1
            break
        first = j == fill_i
        fav, adv = (h, l) if s > 0 else (l, h)
        adv_pct = max(0.0, -P(adv))
        fav_pct = max(0.0, P(fav))
        sl_touch = s * (adv - sl) <= 0
        if best >= 1 and be_t is None and tp_t[0] < tc and s * (adv - F) <= 0:
            be_t, be_best = tc, best                    # stop moved to entry: entry touched on a later candle (cautious)
        if liq_move and liq_t is None and adv_pct >= liq_move and (not sl_touch or liq_move < risk_pct):
            liq_t = tc
        if sl_touch:
            if any(s * (fav - p) >= 0 for p in tps[best:]) and (not first or at_open):
                res["ambiguous"] += 1
            mae = max(mae, risk_pct)
            sl_t, ended, end_t, end_i = tc, "sl", tc, j
            break
        mae = max(mae, adv_pct)
        if not first or at_open:
            mfe = max(mfe, fav_pct)
            reached = [k for k in range(n) if s * (fav - tps[k]) >= 0]
            if reached and max(reached) + 1 > best:
                top = max(reached) + 1
                for k in range(best, top):
                    tp_t[k] = tc
                best = top
        if best == n:
            ended, end_t, end_i = "last_tp", tc, j
            break
    res.update(best_tp=best, tp_t=tp_t, sl_t=sl_t, be_t=be_t, be_best=be_best, liq_t=liq_t, liquidated=liq_t is not None,
               mfe_pct=round(mfe, 4), mae_pct=round(mae, 4), mfe_r=round(mfe / risk_pct, 3), mae_r=round(mae / risk_pct, 3),
               first="tp1" if tp_t[0] is not None else ("sl" if ended == "sl" else None),
               time_to_tp1_h=round((tp_t[0] - t0) / H1, 2) if tp_t[0] is not None else None)
    if ended:
        res.update(state="closed", ended=ended, end_t=end_t, resolved_at=end_t,
                   exit_px=exit_px if ended == "timeout" else (sl if ended == "sl" else tps[-1]))
    if ended == "sl":   # did the price come back to TP1 within 7 days after the stop? (stop too tight?)
        after = [c for c in cs[end_i + 1:] if c[0] <= sl_t + AFTER_MS]
        hit = any(s * ((c[2] if s > 0 else c[3]) - tp1) >= 0 for c in after)
        covered = cs[-1][0] + M15 >= sl_t + AFTER_MS
        res["after_tp1"] = True if hit else (False if covered else None)
    mtm = exit_px if ended == "timeout" else None
    pol = {}

    def put(key, gross, closed_at):
        net = gross - COST_PCT
        pol[key] = {"closed": True, "pct": round(gross, 4), "net": round(net, 4), "r": round(net / risk_pct, 4),
                    "lev": round(max(net * lev, -100.0), 3), "t": closed_at}

    # app — farthest TP reached is the result
    if ended:
        put("app", P(tps[best - 1]) if best else (P(sl) if ended == "sl" else P(mtm)), tp_t[best - 1] if best else end_t)
    # tp1
    if best >= 1:
        put("tp1", P(tps[0]), tp_t[0])
    elif ended:
        put("tp1", P(sl) if ended == "sl" else P(mtm), end_t)
    # last
    if ended:
        put("last", P(tps[-1]) if ended == "last_tp" else (P(sl) if ended == "sl" else P(mtm)), end_t)
    # ladder — stop stays
    if ended:
        rest_px = sl if ended == "sl" else (mtm if ended == "timeout" else None)
        g = sum(P(tps[k]) for k in range(best)) / n + ((n - best) / n * P(rest_px) if rest_px is not None else 0.0)
        put("ladder", g, end_t)
    # ladder_be — stop to entry after TP1
    if be_t is not None:
        put("ladder_be", sum(P(tps[k]) for k in range(be_best)) / n, be_t)
    elif ended == "last_tp":
        put("ladder_be", sum(P(p) for p in tps) / n, end_t)
    elif ended == "sl":
        put("ladder_be", P(sl), end_t)
    elif ended == "timeout":
        put("ladder_be", sum(P(tps[k]) for k in range(best)) / n + (n - best) / n * P(mtm), end_t)
    # liquidation: leveraged result of the part still open at liq_t = −100 % of its margin
    if liq_t is not None:
        def gone(tt):
            return tt is None or tt >= liq_t
        for key, p in pol.items():
            if key == "ladder":
                parts = [k for k in range(n) if tp_t[k] is not None and tp_t[k] < liq_t]
                lv = sum((P(tps[k]) - COST_PCT) * lev for k in parts) / n - 100.0 * (n - len(parts)) / n
                p["lev"] = round(max(lv, -100.0), 3)
            elif key == "ladder_be":
                if be_t is None or be_t > liq_t:
                    parts = [k for k in range(n) if tp_t[k] is not None and tp_t[k] < liq_t]
                    lv = sum((P(tps[k]) - COST_PCT) * lev for k in parts) / n - 100.0 * (n - len(parts)) / n
                    p["lev"] = round(max(lv, -100.0), 3)
            elif key == "tp1":
                if gone(tp_t[0]):
                    p["lev"] = -100.0
            elif key == "app":
                if gone(tp_t[best - 1] if best else None):
                    p["lev"] = -100.0
            elif key == "last" and liq_t <= (end_t or liq_t):
                p["lev"] = -100.0
    res["policies"] = pol
    res["done_at"] = (sl_t + AFTER_MS) if ended == "sl" else end_t
    return res


# ------------------------------------------------------------------------------------------------------------ features
def ema(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) <= n:
        return None
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100 - 100 / (1 + ag / al)


def atr(rows: list[list], n: int = 14) -> float | None:
    if len(rows) <= n:
        return None
    trs = [max(r[2] - r[3], abs(r[2] - p[4]), abs(r[3] - p[4])) for p, r in zip(rows, rows[1:])]
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def _r(x, nd=2):
    return None if x is None else round(x, nd)


def plan_features(sig: dict) -> dict:
    s = 1 if sig["side"] == "LONG" else -1
    mid = (sig["entry_lo"] + sig["entry_hi"]) / 2
    sl_pct = abs(mid - sig["sl"]) / mid * 100
    tp_pct = [s * (p - mid) / mid * 100 for p in sig["tps"]]
    d = datetime.fromtimestamp(sig["t"] / 1000, timezone.utc)
    return {"side": sig["side"], "coin": sig["symbol"].split("-")[0], "leverage": sig.get("leverage") or 1.0,
            "hour_utc": d.hour, "weekday": d.weekday(), "n_tps": len(sig["tps"]), "range_entry": sig["entry_hi"] > sig["entry_lo"],
            "entry_width_pct": _r((sig["entry_hi"] - sig["entry_lo"]) / mid * 100, 3),
            "sl_pct": _r(sl_pct, 3), "tp_pct": [_r(x, 3) for x in tp_pct],
            "rr": [_r(x / sl_pct, 2) if sl_pct > 0 else None for x in tp_pct]}


def _trend(rows4h):
    closes = [r[4] for r in rows4h]
    e50, e200 = ema(closes, 50), ema(closes, 200)
    if e50 is None or e200 is None:
        return None, e50, e200
    return ("up" if e50 > e200 else "down"), e50, e200


def compute_features(sig: dict, c1h: list, c4h: list, btc4h: list) -> dict:
    """Context at signal time from CLOSED bars only (bar open + length ≤ signal time)."""
    t = sig["t"]
    f = plan_features(sig)
    h1 = [r for r in c1h if r[0] + H1 <= t]
    h4 = [r for r in c4h if r[0] + H4 <= t]
    b4 = [r for r in btc4h if r[0] + H4 <= t]
    mid = (sig["entry_lo"] + sig["entry_hi"]) / 2
    if len(h1) >= 30:
        closes = [r[4] for r in h1]
        px = closes[-1]
        d24, d7 = h1[-24:], h1[-168:]
        f.update(price_at_signal=px, rsi1h=_r(rsi(closes), 1),
                 dist_hi24_pct=_r((px / max(r[2] for r in d24) - 1) * 100), dist_lo24_pct=_r((px / min(r[3] for r in d24) - 1) * 100),
                 dist_hi7d_pct=_r((px / max(r[2] for r in d7) - 1) * 100), dist_lo7d_pct=_r((px / min(r[3] for r in d7) - 1) * 100),
                 move4h_pct=_r((px / closes[-5] - 1) * 100) if len(closes) >= 5 else None,
                 move24h_pct=_r((px / closes[-25] - 1) * 100) if len(closes) >= 25 else None,
                 atr1h_pct=_r(atr(h1) / px * 100, 3) if atr(h1) else None,
                 entry_vs_market_pct=_r((mid / px - 1) * 100, 3), h7d_bars=len(d7))
    if len(h4) >= 20:
        closes4 = [r[4] for r in h4]
        tr, e50, e200 = _trend(h4)
        f.update(rsi4h=_r(rsi(closes4), 1), trend4h=tr,
                 ema50_vs_200_pct=_r((e50 / e200 - 1) * 100) if e50 and e200 else None)
    if len(b4) >= 200:
        tr, _, _ = _trend(b4)
        f.update(btc_trend4h=tr, btc_move4h_pct=_r((b4[-1][4] / b4[-2][4] - 1) * 100))
    side = sig["side"]
    if f.get("trend4h"):
        f["with_trend"] = (side == "LONG") == (f["trend4h"] == "up")
    if f.get("btc_trend4h"):
        f["with_btc"] = (side == "LONG") == (f["btc_trend4h"] == "up")
    if f.get("rsi1h") is not None:
        f["rsi_wrong"] = (side == "LONG" and f["rsi1h"] >= 70) or (side == "SHORT" and f["rsi1h"] <= 30)
    f["archetype"] = archetype(f)
    return f


def archetype(f: dict) -> str:
    side, tr, r1 = f.get("side"), f.get("trend4h"), f.get("rsi1h")
    hi24, lo24 = f.get("dist_hi24_pct"), f.get("dist_lo24_pct")
    if hi24 is None and tr is None:
        return "Покупка (нет данных о рынке)" if side == "LONG" else "Шорт (нет данных о рынке)"
    if side == "LONG":
        if tr == "up" and hi24 is not None and hi24 <= -3:
            return "Покупка отката в восходящем тренде"
        if tr == "up" and hi24 is not None and hi24 > -1:
            return "Покупка у максимума / на пробое в восходящем тренде"
        if tr == "up":
            return "Покупка в восходящем тренде"
        if (r1 is not None and r1 <= 35) or (lo24 is not None and lo24 <= 1.5):
            return "Ловля дна: покупка на перепроданности против тренда"
        if tr == "down":
            return "Покупка против нисходящего тренда"
        return "Покупка (тренд 4h не определён)"
    if tr == "down" and lo24 is not None and lo24 >= 3:
        return "Шорт отскока в нисходящем тренде"
    if tr == "down" and lo24 is not None and lo24 < 1:
        return "Шорт у минимума / на пробое вниз"
    if tr == "down":
        return "Шорт в нисходящем тренде"
    if (r1 is not None and r1 >= 65) or (hi24 is not None and hi24 >= -1.5):
        return "Шорт на перекупленности против тренда"
    if tr == "up":
        return "Шорт против восходящего тренда"
    return "Шорт (тренд 4h не определён)"


# ------------------------------------------------------------------------------------------------------------ run loop
def _row_sig(r: sqlite3.Row) -> dict:
    tg = json.loads(r["targets"] or "[]")
    return {"id": r["signal_id"], "symbol": r["symbol"], "side": r["side"], "entry_lo": r["entry_lo"], "entry_hi": r["entry_hi"],
            "sl": r["sl"], "t": r["t_signal"], "leverage": r["leverage"], "tps": [x["price"] for x in tg], "targets": tg}


def _sim_ready(sig: dict) -> bool:
    return bool(sig["symbol"] and sig["side"] and sig["entry_lo"] and sig["sl"] and sig["t"] and sig["tps"])


def process(conn, now: int, klines, budget: _Budget, notes: list) -> dict:
    """Fetch what each unfinished signal still needs (within the budget) and recompute its outcome from the cache."""
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    conn.execute("UPDATE app_signals SET done=0, sim=NULL, path_until=NULL WHERE sim_version IS NOT NULL AND sim_version<>?", (SIM_VERSION,))
    rows = cur.execute("SELECT * FROM app_signals WHERE done=0 ORDER BY (status='active') DESC, t_signal DESC").fetchall()
    st = {"unfinished": len(rows), "fetch_errors": 0, "skipped_budget": 0}
    for r in rows:
        sig = _row_sig(r)
        sid = sig["id"]
        if not _sim_ready(sig):
            conn.execute("UPDATE app_signals SET sim=?, done=1, sim_version=? WHERE signal_id=?",
                         (json.dumps({"v": SIM_VERSION, "state": "bad_data", "reason": "parse"}), SIM_VERSION, sid))
            continue
        sym, t0 = sig["symbol"], sig["t"]
        p0 = _first_full(t0)
        fail = r["fetch_fail"] or 0
        ok_any, path_complete = False, True
        path_until = r["path_until"]
        prev = json.loads(r["sim"]) if r["sim"] else {}
        stop_at = prev.get("done_at") if prev.get("state") == "closed" else None
        # 1. path candles (15m), window by window; stop as soon as the outcome (+7 days after a stop) is covered
        for _ in range(8):
            start = path_until + M15 if path_until is not None else p0
            end = now if stop_at is None else min(now, stop_at + M15)
            if start > end:
                break
            w_end = min(end, start + 1440 * M15 - 1)
            try:
                complete, last = _fetch_range(conn, klines, budget, sym, "15m", start, w_end)
            except Exception as e:  # noqa: BLE001
                fail += 1
                st["fetch_errors"] += 1
                path_complete = False
                if len(notes) < 40:
                    notes.append(f"свечи {sym}: {str(e)[:120]}")
                break
            if not complete:
                st["skipped_budget"] += 1
                path_complete = False
                break
            ok_any = True
            if last is not None:
                new_until = last if last + M15 <= now else last - M15      # the candle still forming is fetched again next run
            else:
                new_until = (w_end // M15) * M15 if w_end + M15 <= now else None   # an empty window in the past is skipped
            if new_until is None or new_until < start:
                break
            path_until = new_until
            conn.execute("UPDATE app_signals SET path_until=? WHERE signal_id=?", (path_until, sid))
            sim = simulate(sig, _candles(conn, sym, "15m", p0, now + M15), now)
            if sim["state"] == "closed":
                stop_at = sim["done_at"]
            elif sim["state"] in ("not_filled", "bad_data"):
                break
            if w_end >= end:
                break
        # 2. context at signal time (once): coin 1h + 4h, BTC 4h
        ctx_done = bool(r["ctx_done"])
        if not ctx_done:
            got_all = True
            for s2, iv, st_ in ((sym, "1h", t0 - 200 * H1), (sym, "4h", t0 - 250 * H4), ("BTC-USDT", "4h", t0 - 250 * H4)):
                if _have(conn, s2, iv, st_ + IV_MS[iv] * 5, t0 - IV_MS[iv]):
                    continue
                try:
                    complete, _ = _fetch_range(conn, klines, budget, s2, iv, st_, t0)
                    got_all = got_all and complete
                except Exception as e:  # noqa: BLE001
                    got_all = False
                    fail += 1
                    if len(notes) < 40:
                        notes.append(f"контекст {s2} {iv}: {str(e)[:100]}")
            if got_all or fail >= MAX_FETCH_FAIL:
                feat = compute_features(sig, _candles(conn, sym, "1h", t0 - 200 * H1, t0), _candles(conn, sym, "4h", t0 - 250 * H4, t0),
                                        _candles(conn, "BTC-USDT", "4h", t0 - 250 * H4, t0))
                conn.execute("UPDATE app_signals SET features=?, ctx_done=1 WHERE signal_id=?", (json.dumps(feat), sid))
                ctx_done = True
        # 3. outcome from the cache
        cs = _candles(conn, sym, "15m", p0, now + M15)
        sim = simulate(sig, cs, now)
        if not cs and (fail >= MAX_FETCH_FAIL or (path_complete and ok_any and now > t0 + FILL_WINDOW_MS + DAY)):
            sim = {"v": SIM_VERSION, "state": "no_data", "reason": "candles"}
        state = sim["state"]
        if state in ("bad_data", "no_data", "not_filled"):
            done = 1
        elif state == "closed":
            done = int(sim.get("ended") != "sl" or sim.get("after_tp1") is not None or now >= sim["done_at"] + DAY)
        else:
            done = 0
        if done and not ctx_done:
            if state in ("bad_data", "no_data") or fail >= MAX_FETCH_FAIL:
                pf = plan_features(sig)
                conn.execute("UPDATE app_signals SET features=?, ctx_done=1 WHERE signal_id=?", (json.dumps(dict(pf, archetype=archetype(pf))), sid))
            else:
                done = 0          # keep it until the context at signal time is in
        conn.execute("UPDATE app_signals SET sim=?, sim_version=?, resolved_at=?, done=?, fetch_fail=? WHERE signal_id=?",
                     (json.dumps(sim), SIM_VERSION, sim.get("resolved_at"), done, 0 if ok_any else fail, sid))
        conn.commit()
    return st


def prune(conn, now: int) -> int:
    """Finished signals keep their result; drop candles no unfinished signal needs (the DB is committed to git)."""
    need: dict[str, int] = {}
    for sym, t in conn.execute("SELECT symbol, t_signal FROM app_signals WHERE done=0 AND symbol IS NOT NULL AND t_signal IS NOT NULL"):
        need[sym] = min(need.get(sym, t), t)
    before = conn.total_changes
    for (sym,) in conn.execute("SELECT DISTINCT symbol FROM app_signal_candles WHERE interval='15m'").fetchall():
        if sym not in need:
            conn.execute("DELETE FROM app_signal_candles WHERE symbol=? AND interval='15m'", (sym,))
        else:
            conn.execute("DELETE FROM app_signal_candles WHERE symbol=? AND interval='15m' AND t<?", (sym, need[sym] - DAY))
    oldest_ctx = conn.execute("SELECT MIN(t_signal) FROM app_signals WHERE ctx_done=0 AND done=0").fetchone()[0]
    cutoff = min(oldest_ctx or now, now) - 60 * DAY
    conn.execute("DELETE FROM app_signal_candles WHERE interval IN ('1h','4h') AND t<?", (cutoff,))
    conn.commit()
    return conn.total_changes - before


# ------------------------------------------------------------------------------------------------------------ aggregation
def _load(conn) -> list[dict]:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    out = []
    for r in cur.execute("SELECT * FROM app_signals ORDER BY t_signal DESC"):
        out.append({"id": r["signal_id"], "name": r["name"], "symbol": r["symbol"], "coin": (r["symbol"] or "").split("-")[0] or None,
                    "side": r["side"], "entry_lo": r["entry_lo"], "entry_hi": r["entry_hi"], "sl": r["sl"],
                    "leverage": r["leverage"] or 1.0, "t": r["t_signal"], "targets": json.loads(r["targets"] or "[]"),
                    "status": r["status"], "first_seen": r["first_seen"], "last_seen": r["last_seen"], "closed_seen": r["closed_seen"],
                    "app_best_tp": r["app_best_tp"] or 0, "is_vip": bool(r["is_vip"]), "actual_price": r["actual_price"],
                    "actual_pct": r["actual_pct"], "problems": json.loads(r["problems"] or "[]"),
                    "sim": json.loads(r["sim"]) if r["sim"] else {}, "feat": json.loads(r["features"]) if r["features"] else {}})
    return out


def _resolved(rows):
    return [r for r in rows if r["sim"].get("state") == "closed"]


def _pol(r, key):
    p = r["sim"].get("policies", {}).get(key)
    return p if p and p.get("closed") else None


def pstats(rows, key: str, lev_cap: float | None = None) -> dict:
    xs = [p for p in (_pol(r, key) for r in rows) if p]
    if not xs:
        return {"n": 0, "winrate": None, "avg_pct": None, "avg_pct_lev": None, "avg_r": None, "sum_pct": None, "best": None, "worst": None}
    nets = [p["net"] for p in xs]
    levs = [p["lev"] for p in xs]
    return {"n": len(xs), "winrate": round(sum(1 for v in nets if v > 0) / len(xs), 3), "avg_pct": round(statistics.fmean(nets), 3),
            "avg_pct_lev": round(statistics.fmean(levs), 2), "avg_r": round(statistics.fmean(p["r"] for p in xs), 3),
            "sum_pct": round(sum(nets), 2), "sum_pct_lev": round(sum(levs), 1), "best": round(max(nets), 3), "worst": round(min(nets), 3),
            "worst_lev": round(min(levs), 1)}


def grp(rows, key: str = BASELINE) -> dict:
    """Small group summary: n resolved, TP1-before-stop share, win share and average R under the baseline policy."""
    res = _resolved(rows)
    ps = pstats(res, key)
    t1 = [r for r in res if r["sim"].get("first") in ("tp1", "sl")]
    return {"n": len(res), "tp1_rate": round(sum(1 for r in t1 if r["sim"]["first"] == "tp1") / len(t1), 3) if t1 else None,
            "winrate": ps["winrate"], "avg_r": ps["avg_r"], "avg_pct": ps["avg_pct"]}


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 3) if xs else None


def _bucket_hour(h):
    return f"{(h // 6) * 6:02d}–{(h // 6) * 6 + 6:02d} UTC"


def _small(n):
    return n < MIN_SAMPLE


def build_stats(rows) -> dict:
    res = _resolved(rows)
    st = {"policies": {k: pstats(res, k) for k, _, _ in POLICIES}}
    n = len(res)
    reach = {}
    maxn = max([r["sim"].get("n_tps", 0) for r in res] or [0])
    for k in range(1, maxn + 1):
        have = [r for r in res if r["sim"].get("n_tps", 0) >= k]
        reach[f"tp{k}"] = round(sum(1 for r in have if r["sim"].get("best_tp", 0) >= k) / len(have), 3) if have else None
    st["tp_reach"] = reach
    st["sl_share"] = round(sum(1 for r in res if r["sim"].get("first") == "sl") / n, 3) if n else None
    st["timeout_share"] = round(sum(1 for r in res if r["sim"].get("ended") == "timeout") / n, 3) if n else None
    st["median_time_to_tp1_h"] = _median([r["sim"].get("time_to_tp1_h") for r in res])
    st["median_fill_delay_h"] = _median([r["sim"].get("fill_delay_h") for r in res])
    st["median_mfe_r"] = _median([r["sim"].get("mfe_r") for r in res])
    st["median_mae_r"] = _median([r["sim"].get("mae_r") for r in res])
    st["ambiguous_candles"] = sum(r["sim"].get("ambiguous", 0) for r in res)
    st["by_side"] = {sd: grp([r for r in rows if r["side"] == sd]) for sd in ("LONG", "SHORT")}
    coins = {}
    for r in rows:
        if r["coin"]:
            coins.setdefault(r["coin"], []).append(r)
    top = sorted(coins.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:10]
    st["by_coin"] = [{"coin": c, "signals": len(v), **grp(v)} for c, v in top]
    # the app's own is_completed flags vs prices
    cmp_ = [r for r in res if r["targets"]]
    st["app_flags"] = {"n": len(cmp_), "agree": sum(1 for r in cmp_ if r["app_best_tp"] == r["sim"].get("best_tp")),
                       "app_more": sum(1 for r in cmp_ if r["app_best_tp"] > r["sim"].get("best_tp", 0)),
                       "app_less": sum(1 for r in cmp_ if r["app_best_tp"] < r["sim"].get("best_tp", 0))}
    return st


def _split(label_ru, rows, fn, labels: dict) -> dict:
    groups = {}
    for r in rows:
        k = fn(r)
        if k is None:
            continue
        groups.setdefault(k, []).append(r)
    out = [{"label": labels.get(k, str(k)), **grp(v)} for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))]
    n = sum(g["n"] for g in out)
    return {"feature_ru": label_ru, "groups": out, "n": n,
            "note_ru": f"рано судить: закрытых сигналов {n} < {MIN_SAMPLE}" if _small(n) else ""}


def build_profile(rows) -> dict:
    fr = [r for r in rows if r["feat"]]
    res = _resolved(rows)
    n = len(fr)
    prof = {"n_signals": n, "n_resolved": len(res), "sample_ok": not _small(len(res))}
    if not n:
        prof["summary_ru"] = "Пока нет сигналов с разобранными данными."
        return prof
    sides = {sd: sum(1 for r in fr if r["feat"].get("side") == sd) for sd in ("LONG", "SHORT")}
    arch = {}
    for r in fr:
        arch.setdefault(r["feat"].get("archetype") or "—", []).append(r)
    archetypes = sorted(({"label": k, "n": len(v), "share": round(len(v) / n, 3), **{f"res_{a}": b for a, b in grp(v).items()}}
                         for k, v in arch.items()), key=lambda x: -x["n"])
    F = [r["feat"] for r in fr]
    plan = {"median_sl_pct": _median([f.get("sl_pct") for f in F]),
            "median_tp1_pct": _median([(f.get("tp_pct") or [None])[0] for f in F]),
            "median_last_tp_pct": _median([(f.get("tp_pct") or [None])[-1] for f in F]),
            "median_rr_tp1": _median([(f.get("rr") or [None])[0] for f in F]),
            "median_rr_last": _median([(f.get("rr") or [None])[-1] for f in F]),
            "typical_n_tps": statistics.mode([f.get("n_tps") for f in F]) if F else None,
            "median_leverage": _median([f.get("leverage") for f in F]),
            "leverage_dist": {f"{k:g}": sum(1 for f in F if f.get("leverage") == k) for k in sorted({f.get("leverage") for f in F if f.get("leverage")})},
            "range_entry_share": round(sum(1 for f in F if f.get("range_entry")) / n, 3),
            "median_entry_vs_market_pct": _median([f.get("entry_vs_market_pct") for f in F]),
            "median_atr1h_pct": _median([f.get("atr1h_pct") for f in F])}
    ctx_known = [f for f in F if f.get("with_trend") is not None]
    btc_known = [f for f in F if f.get("with_btc") is not None]
    rsi_known = [f for f in F if f.get("rsi_wrong") is not None]
    context = {"with_trend_share": round(sum(1 for f in ctx_known if f["with_trend"]) / len(ctx_known), 3) if ctx_known else None,
               "with_trend_n": len(ctx_known),
               "with_btc_share": round(sum(1 for f in btc_known if f["with_btc"]) / len(btc_known), 3) if btc_known else None,
               "with_btc_n": len(btc_known),
               "rsi_wrong_share": round(sum(1 for f in rsi_known if f["rsi_wrong"]) / len(rsi_known), 3) if rsi_known else None,
               "median_rsi1h_long": _median([f.get("rsi1h") for f in F if f.get("side") == "LONG"]),
               "median_rsi1h_short": _median([f.get("rsi1h") for f in F if f.get("side") == "SHORT"]),
               "median_dist_hi24_long": _median([f.get("dist_hi24_pct") for f in F if f.get("side") == "LONG"]),
               "median_dist_lo24_short": _median([f.get("dist_lo24_pct") for f in F if f.get("side") == "SHORT"]),
               "median_move4h_long": _median([f.get("move4h_pct") for f in F if f.get("side") == "LONG"]),
               "median_move4h_short": _median([f.get("move4h_pct") for f in F if f.get("side") == "SHORT"])}
    hours = {}
    for f in F:
        b = _bucket_hour(f["hour_utc"])
        hours[b] = hours.get(b, 0) + 1
    coins = {}
    for f in F:
        coins[f["coin"]] = coins.get(f["coin"], 0) + 1
    sl_res = [r for r in res if r["sim"].get("ended") == "sl" and r["sim"].get("after_tp1") is not None]
    hunts = {"n": len(sl_res), "tp1_after_sl": sum(1 for r in sl_res if r["sim"]["after_tp1"])}
    rr = [r for r in fr]
    med_atr = plan["median_atr1h_pct"]
    splits = [
        _split("Лонг / шорт", rr, lambda r: r["side"], {"LONG": "лонг", "SHORT": "шорт"}),
        _split("По тренду монеты 4h (EMA50/EMA200)", rr, lambda r: r["feat"].get("with_trend"), {True: "по тренду", False: "против тренда"}),
        _split("По тренду BTC 4h", rr, lambda r: r["feat"].get("with_btc"), {True: "по тренду BTC", False: "против тренда BTC"}),
        _split("RSI 1h в «неправильную» сторону (лонг при ≥70, шорт при ≤30)", rr, lambda r: r["feat"].get("rsi_wrong"),
               {True: "RSI против сделки", False: "RSI нормальный"}),
        _split("Плечо", rr, lambda r: None if not r["feat"].get("leverage") else (r["feat"]["leverage"] > LEV_CAP),
               {False: f"до {LEV_CAP}x", True: f"больше {LEV_CAP}x"}),
        _split("Время сигнала (UTC)", rr, lambda r: _bucket_hour(r["feat"]["hour_utc"]) if r["feat"].get("hour_utc") is not None else None, {}),
        _split("Волатильность (ATR 1h к медиане)", rr,
               lambda r: None if med_atr is None or r["feat"].get("atr1h_pct") is None else r["feat"]["atr1h_pct"] > med_atr,
               {False: "спокойнее медианы", True: "волатильнее медианы"}),
        _split("R:R первой цели", rr, lambda r: None if not r["feat"].get("rr") or r["feat"]["rr"][0] is None else r["feat"]["rr"][0] >= 1,
               {False: "TP1 ближе стопа (R:R < 1)", True: "TP1 не ближе стопа (R:R ≥ 1)"}),
        _split("Тип входа", rr, lambda r: r["feat"].get("archetype"), {}),
    ]
    prof.update(sides=sides, archetypes=archetypes, plan=plan, context=context, by_hour=hours,
                by_weekday={WEEKDAYS_RU[k]: sum(1 for f in F if f.get("weekday") == k) for k in range(7)},
                coins_top=sorted(({"coin": c, "n": v} for c, v in coins.items()), key=lambda x: (-x["n"], x["coin"]))[:10],
                stop_then_tp1=hunts, splits=splits)
    # plain-language summary built only from the numbers above
    parts = []
    top = archetypes[0]
    parts.append(f"Чаще всего это «{top['label']}» — {top['n']} из {n} {_pl(n, 'сигнала', 'сигналов', 'сигналов')}; "
                 f"лонгов {sides['LONG']}, шортов {sides['SHORT']}.")
    if plan["median_sl_pct"] is not None and plan["median_tp1_pct"] is not None:
        txt = (f"Типичный план: стоп {plan['median_sl_pct']:.2f}% от входа, первая цель {plan['median_tp1_pct']:.2f}%"
               f" (R:R {plan['median_rr_tp1']}), последняя {plan['median_last_tp_pct']:.2f}% (R:R {plan['median_rr_last']}),"
               f" целей обычно {plan['typical_n_tps']}, плечо медиана {plan['median_leverage']:g}x.")
        parts.append(txt)
    if plan["median_entry_vs_market_pct"] is not None:
        e = plan["median_entry_vs_market_pct"]
        parts.append(f"Вход в среднем {'почти по рынку' if abs(e) < 0.3 else ('ниже рынка' if e < 0 else 'выше рынка')}"
                     f" ({e:+.2f}% от цены в момент сигнала).")
    if context["with_btc_share"] is not None:
        parts.append(f"По тренду BTC 4h — {context['with_btc_share'] * 100:.0f}% сигналов (из {context['with_btc_n']} с данными).")
    if hunts["n"]:
        parts.append(f"После стопа цена в течение 7 дней всё же дошла до TP1 в {hunts['tp1_after_sl']} из {hunts['n']} случаев.")
    if _small(len(res)):
        parts.append(f"Закрытых сигналов пока {len(res)} — меньше {MIN_SAMPLE}, разбиения по признакам ниже только для ориентира.")
    prof["summary_ru"] = " ".join(parts)
    return prof


def _ev(rows, key=BASELINE):
    p = pstats(rows, key)
    return {"n": p["n"], "winrate": p["winrate"], "avg_r": p["avg_r"], "avg_pct": p["avg_pct"], "sum_pct": p["sum_pct"]}


def _verdict_filter(n, before, after, skipped):
    if _small(n):
        return f"рано судить: закрытых сигналов {n} < {MIN_SAMPLE}"
    if skipped["n"] < 5:
        return f"почти нечего отсеивать ({skipped['n']} сигн.) — правило картину не меняет"
    if after["avg_r"] is None or before["avg_r"] is None:
        return "нет данных"
    d = after["avg_r"] - before["avg_r"]
    if d > 0.05 and (skipped["avg_r"] or 0) < 0:
        return (f"помогает: средний итог {before['avg_r']:+.2f}R → {after['avg_r']:+.2f}R; отсеянные {skipped['n']} сигн. "
                f"в среднем {skipped['avg_r']:+.2f}R")
    if d < -0.05:
        return f"вредит: {before['avg_r']:+.2f}R → {after['avg_r']:+.2f}R — отсеиваются в среднем прибыльные сигналы"
    return f"без заметной разницы ({before['avg_r']:+.2f}R → {after['avg_r']:+.2f}R)"


def build_proposals(rows) -> list[dict]:
    res = _resolved(rows)
    n = len(res)
    out = []
    names = {k: nm for k, nm, _ in POLICIES}
    # 1. best executable exit policy (each policy on the signals where every compared policy is closed)
    execs = [k for k, _, ex in POLICIES if ex]
    common = [r for r in res if all(_pol(r, k) for k in execs)]
    ranked = sorted(((k, _ev(common, k)) for k in execs), key=lambda kv: -(kv[1]["avg_r"] if kv[1]["avg_r"] is not None else -9))
    if ranked and ranked[0][1]["n"]:
        bk, bst = ranked[0]
        base = _ev(common, BASELINE)
        app = _ev(common, "app")
        v = (f"рано судить: закрытых сигналов {len(common)} < {MIN_SAMPLE}" if _small(len(common)) else
             (f"лучший исполнимый выход сейчас — «{names[bk]}»: {bst['avg_r']:+.2f}R на сделку против {base['avg_r']:+.2f}R"
              if bk != BASELINE else f"текущая база («{names[BASELINE]}») и так лучшая из исполнимых: {bst['avg_r']:+.2f}R"))
        if app["avg_r"] is not None and bst["avg_r"] is not None:
            v += f". Витрина приложения показывает {app['avg_r']:+.2f}R — это потолок, не реальный результат"
        out.append({"id": "exit_policy", "rule_ru": "Выбрать способ выхода по целям", "n": len(common), "before": base, "after": bst,
                    "after_policy": bk, "ranking": [{"policy": k, "name_ru": names[k], **s_} for k, s_ in ranked], "verdict_ru": v})
    else:
        out.append({"id": "exit_policy", "rule_ru": "Выбрать способ выхода по целям", "n": 0, "before": None, "after": None,
                    "verdict_ru": f"рано судить: закрытых сигналов 0 < {MIN_SAMPLE}"})

    def filt(pid, rule_ru, cond, extra_note=""):
        known = [r for r in res if cond(r) is not None]
        skip = [r for r in res if cond(r) is True]
        keep = [r for r in res if cond(r) is not True]
        before, after, skipped = _ev(res), _ev(keep), _ev(skip)
        v = _verdict_filter(n, before, after, skipped)
        if extra_note and not _small(n):
            v += ". " + extra_note
        out.append({"id": pid, "rule_ru": rule_ru, "n": n, "known": len(known), "skipped": skipped["n"], "before": before,
                    "after": after, "skipped_stats": skipped, "verdict_ru": v})

    filt("skip_vs_btc", "Не брать сигналы против тренда BTC 4h (EMA50/EMA200)",
         lambda r: None if r["feat"].get("with_btc") is None else not r["feat"]["with_btc"])
    filt("skip_vs_trend", "Не брать сигналы против тренда самой монеты 4h",
         lambda r: None if r["feat"].get("with_trend") is None else not r["feat"]["with_trend"])
    filt("skip_rsi_wrong", "Не брать лонг при RSI 1h ≥ 70 и шорт при RSI 1h ≤ 30",
         lambda r: r["feat"].get("rsi_wrong"))
    # 4. stop to entry after TP1
    both = [r for r in res if _pol(r, "ladder") and _pol(r, "ladder_be")]
    b, a = _ev(both, "ladder"), _ev(both, "ladder_be")
    if _small(len(both)):
        v = f"рано судить: закрытых сигналов {len(both)} < {MIN_SAMPLE}"
    elif a["avg_r"] is not None and b["avg_r"] is not None:
        d = a["avg_r"] - b["avg_r"]
        v = (f"{'помогает' if d > 0.03 else 'вредит' if d < -0.03 else 'без заметной разницы'}: "
             f"{b['avg_r']:+.2f}R → {a['avg_r']:+.2f}R, прибыльных сделок {b['winrate'] * 100:.0f}% → {a['winrate'] * 100:.0f}%")
    else:
        v = "нет данных"
    out.append({"id": "be_after_tp1", "rule_ru": "После TP1 переносить стоп в точку входа (выход равными частями)", "n": len(both),
                "before": b, "after": a, "verdict_ru": v})
    # 5. leverage cap
    lev_rows = [r for r in res if _pol(r, BASELINE)]
    if lev_rows:
        def lev_of(r, cap=None):
            p = _pol(r, BASELINE)
            L = r["leverage"] if cap is None else min(r["leverage"], cap)
            liq = r["sim"].get("liquidated") and (cap is None or L == r["leverage"])
            if cap is not None and L < r["leverage"]:   # recheck liquidation with the lower leverage
                lm = 100.0 / L - 0.5 if L > 1 else None
                liq = bool(lm and r["sim"].get("mae_pct", 0) >= lm)
            if liq and cap is not None and L < r["leverage"]:
                return -100.0
            return p["lev"] if cap is None else max(p["net"] * L, -100.0)
        bl = [lev_of(r) for r in lev_rows]
        al = [lev_of(r, LEV_CAP) for r in lev_rows]
        over = sum(1 for r in lev_rows if r["leverage"] > LEV_CAP)
        liq_n = sum(1 for r in lev_rows if r["sim"].get("liquidated"))
        liq_risk = sum(1 for r in lev_rows if r["sim"].get("liq_risk"))
        bst = {"n": len(bl), "avg_pct_lev": round(statistics.fmean(bl), 2), "worst_lev": round(min(bl), 1), "liquidations": liq_n,
               "stop_beyond_liquidation": liq_risk}
        ast = {"n": len(al), "avg_pct_lev": round(statistics.fmean(al), 2), "worst_lev": round(min(al), 1),
               "liquidations": sum(1 for x in al if x <= -100.0)}
        if _small(len(lev_rows)):
            v = f"рано судить: закрытых сигналов {len(lev_rows)} < {MIN_SAMPLE}"
        elif not over:
            v = f"плечо и так не выше {LEV_CAP}x — ограничение ничего не меняет"
        else:
            v = (f"плечо не меняет знак результата, только размах: средний итог на маржу {bst['avg_pct_lev']:+.1f}% → "
                 f"{ast['avg_pct_lev']:+.1f}%, худшая сделка {bst['worst_lev']:+.0f}% → {ast['worst_lev']:+.0f}%")
        if liq_risk:
            v += f". У {liq_risk} сигн. стоп дальше цены ликвидации при заявленном плече — позицию закроет ликвидация, а не стоп"
        out.append({"id": "lev_cap", "rule_ru": f"Ограничить плечо до {LEV_CAP}x", "n": len(lev_rows), "over_cap": over,
                    "before": bst, "after": ast, "verdict_ru": v})
    else:
        out.append({"id": "lev_cap", "rule_ru": f"Ограничить плечо до {LEV_CAP}x", "n": 0, "before": None, "after": None,
                    "verdict_ru": f"рано судить: закрытых сигналов 0 < {MIN_SAMPLE}"})
    # 6. time of day: the worst 6-hour UTC window (chosen on the same history → needs checking on new signals)
    buckets = {}
    for r in res:
        h = r["feat"].get("hour_utc")
        if h is not None:
            buckets.setdefault(_bucket_hour(h), []).append(r)
    cand = [(b_, _ev(v_)) for b_, v_ in buckets.items() if len(v_) >= 5]
    cand = [c for c in cand if c[1]["avg_r"] is not None]
    if cand:
        worst = min(cand, key=lambda c: c[1]["avg_r"])[0]
        filt("time_filter", f"Не брать сигналы в окне {worst}", lambda r: None if r["feat"].get("hour_utc") is None
             else _bucket_hour(r["feat"]["hour_utc"]) == worst,
             "окно выбрано по этой же истории — проверять на новых сигналах, прежде чем верить")
    else:
        out.append({"id": "time_filter", "rule_ru": "Фильтр по времени суток (UTC, окна по 6 часов)", "n": n, "before": None,
                    "after": None, "verdict_ru": f"рано судить: закрытых сигналов {n} < {MIN_SAMPLE}" if _small(n)
                    else "ни в одном окне нет 5+ закрытых сигналов"})
    return out


def _f(x, nd=6):
    return None if x is None else round(float(x), nd)


def _pl(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _state_ru(sim: dict, status: str) -> str:
    st = sim.get("state")
    if st == "pending":
        return "ждёт входа"
    if st == "not_filled":
        return {"tp1_before_entry": "не вошёл — цена ушла к TP1 без входа", "sl_before_entry": "не вошёл — стоп раньше входа"}.get(
            sim.get("reason"), "не вошёл за 48 ч")
    if st == "open":
        b = sim.get("best_tp", 0)
        return f"в позиции, достигнут TP{b}" if b else "в позиции"
    if st == "closed":
        b, n, e = sim.get("best_tp", 0), sim.get("n_tps", 0), sim.get("ended")
        if e == "last_tp":
            return f"все цели (TP{n})"
        if e == "sl":
            return f"TP{b} → стоп" if b else "стоп"
        return f"по времени (14 дн.), достигнут TP{b}" if b else "по времени (14 дн.)"
    if st in ("bad_data", "no_data"):
        return "нет данных для проверки"
    return st or "—"


def _brief(r: dict) -> dict:
    sim = r["sim"]
    return {"id": r["id"], "name": r["name"], "coin": r["coin"], "side": r["side"], "t": r["t"], "leverage": r["leverage"],
            "entry_lo": r["entry_lo"], "entry_hi": r["entry_hi"], "sl": r["sl"],
            "targets": [{"level": x["level"], "price": x["price"], "app_done": x["done"]} for x in r["targets"]],
            "state": sim.get("state"), "state_ru": _state_ru(sim, r["status"]), "best_tp": sim.get("best_tp", 0),
            "n_tps": sim.get("n_tps"), "first": sim.get("first"), "fill": _f(sim.get("fill")),
            "time_to_tp1_h": sim.get("time_to_tp1_h"), "mfe_pct": sim.get("mfe_pct"), "mae_pct": sim.get("mae_pct"),
            "app_best_tp": r["app_best_tp"], "archetype": r["feat"].get("archetype"), "with_btc": r["feat"].get("with_btc"),
            "rsi1h": r["feat"].get("rsi1h"), "status": r["status"]}


def _active_item(r: dict) -> dict:
    d = _brief(r)
    sim = r["sim"]
    s = 1 if r["side"] == "LONG" else -1
    px = sim.get("last_price")
    best = sim.get("best_tp", 0)
    d["targets"] = [dict(x, hit=i < best) for i, x in enumerate(d["targets"])]
    d.update(price=_f(px), price_t=sim.get("last_t"), age_h=None, app_actual_price=r["actual_price"], app_actual_pct=r["actual_pct"])
    if px:
        tps = [x["price"] for x in r["targets"]]
        if best < len(tps):
            nxt = tps[best]
            d["next_tp"] = {"level": r["targets"][best]["level"], "price": nxt, "to_pct": round(s * (nxt - px) / px * 100, 3)}
        d["to_sl_pct"] = round(s * (px - r["sl"]) / px * 100, 3) if r["sl"] else None
        if sim.get("fill"):
            g = s * (px - sim["fill"]) / sim["fill"] * 100
            d.update(pnl_pct=round(g, 3), pnl_lev_pct=round(max(g * r["leverage"], -100.0), 2))
        elif r["entry_lo"]:
            lo, hi = r["entry_lo"], r["entry_hi"]
            near = hi if px > hi else lo if px < lo else px
            d["to_entry_pct"] = round((near - px) / px * 100, 3)
    return d


def build_doc(conn, now: int, notes: list, api: dict, sources: dict) -> dict:
    rows = _load(conn)
    sims = [r["sim"].get("state") for r in rows]
    counts = {"total": len(rows), "active": sum(1 for r in rows if r["status"] == "active"),
              "closed": sum(1 for r in rows if r["status"] == "closed"), "resolved": sims.count("closed"),
              "not_filled": sims.count("not_filled"), "open": sims.count("open"), "pending": sims.count("pending") + sims.count(None),
              "no_data": sims.count("bad_data") + sims.count("no_data")}
    stats = build_stats(rows)
    active = []
    for r in rows:
        if r["status"] == "active":
            a = _active_item(r)
            a["age_h"] = round((now - r["t"]) / H1, 1) if r["t"] else None
            active.append(a)
    recent = []
    for r in rows:
        if r["sim"].get("state") not in ("closed", "not_filled") and r["status"] != "closed":
            continue
        b = _brief(r)
        b["policies"] = {k: ({"pct": p["net"], "lev": p["lev"], "r": p["r"]} if p else None)
                         for k, p in ((k, _pol(r, k)) for k, _, _ in POLICIES)}
        recent.append(b)
        if len(recent) >= 30:
            break
    profile = build_profile(rows)
    proposals = build_proposals(rows)
    # summary from numbers only
    P = stats["policies"]
    parts = [f"Отслежено {counts['total']} {_pl(counts['total'], 'сигнал', 'сигнала', 'сигналов')} приложения: активных сейчас "
             f"{counts['active']}, закрыто по нашей проверке ценами {counts['resolved']}, не вошли {counts['not_filled']}."]
    if counts["resolved"]:
        a, b, c = P["app"], P["tp1"], P[BASELINE]
        parts.append(f"По правилу приложения (самый дальний TP) средний итог {a['avg_pct']:+.2f}% на сделку без плеча, "
                     f"но реально исполнимо: всё на TP1 — {b['avg_pct']:+.2f}%, равными частями по целям — {c['avg_pct']:+.2f}% (после комиссий).")
        if stats["tp_reach"].get("tp1") is not None and stats["sl_share"] is not None:
            parts.append(f"TP1 достигнут в {stats['tp_reach']['tp1'] * 100:.0f}% закрытых сигналов, стоп раньше TP1 — в {stats['sl_share'] * 100:.0f}%.")
    if counts["resolved"] < MIN_SAMPLE:
        parts.append(f"Закрытых сигналов {counts['resolved']} — меньше {MIN_SAMPLE}, выводы предварительные.")
    else:
        ex = next((p for p in proposals if p["id"] == "exit_policy"), None)
        if ex and ex.get("after_policy"):
            nm = dict((k, nm) for k, nm, _ in POLICIES)[ex["after_policy"]]
            parts.append(f"Лучший исполнимый выход по истории: «{nm}» ({ex['after']['avg_r']:+.2f}R в среднем).")
    method = [
        "Итог считаем сами по свечам 15m бессрочных фьючерсов (BingX, запасные OKX и Binance), флаги «достигнут» из приложения не используем — только сравниваем.",
        "Отсчёт с первой полной 15-минутной свечи после времени сигнала. Вход: одна цена — свеча её коснулась; диапазон — коснулась любой цены диапазона. Нет касания 48 ч, или раньше входа дошли до TP1 / стопа — «не вошёл».",
        "Если одна 15-минутная свеча задела и стоп, и цель — считаем, что первым был стоп (осторожно). На свече входа цели не засчитываем, если вход был не по открытию свечи.",
        f"Комиссия 0.05% на вход и 0.05% на выход + 0.02% проскальзывание = {COST_PCT:.2f}% от цены на сделку. R — расстояние от входа до стопа. Плечо только умножает % (убыток на маржу не больше −100%).",
        "Позиция без выхода 14 дней закрывается по рынку («по времени»). После стопа ещё 7 дней смотрим, вернулась ли цена к TP1.",
        f"Всё, где закрытых сигналов меньше {MIN_SAMPLE}, помечено «рано судить».",
    ]
    bad = {}
    for r in rows:
        if r["sim"].get("state") in ("bad_data", "no_data"):
            k = f"{r['sim'].get('reason') or r['sim'].get('state')}"
            bad.setdefault(k, []).append(r["coin"] or r["name"] or r["id"])
    if bad:
        notes = notes + ["Без проверки ценой: " + "; ".join(f"{k} ×{len(v)} ({', '.join(sorted({str(x) for x in v})[:6])})"
                                                          for k, v in sorted(bad.items()))]
    return {"asof": now, "source": {"api": [API_ACTIVE, API_HISTORY], "api_ok": api, "candles": sources},
            "policies": [{"key": k, "name_ru": nm, "executable": ex} for k, nm, ex in POLICIES], "baseline": BASELINE,
            "counts": counts, "stats": stats, "active": active, "recent": recent, "profile": profile, "proposals": proposals,
            "summary_ru": " ".join(parts[:4]), "method_ru": method, "notes_ru": notes}


# ------------------------------------------------------------------------------------------------------------ entry points
def run(conn, now: int | None = None, fetch_json=None, klines=None, budget_s: float = 240, max_requests: int = 220) -> dict:
    now = int(now if now is not None else time.time() * 1000)
    ensure_schema(conn)
    notes: list[str] = []
    fetch_json = fetch_json or _get_api
    lists, api = {}, {}
    for kind, status, url in (("active", "active", API_ACTIVE), ("history", "closed", API_HISTORY)):
        try:
            items, err = extract_list(fetch_json(url))
        except Exception as e:  # noqa: BLE001
            items, err = None, f"{type(e).__name__}: {str(e)[:120]}"
        api[kind] = items is not None
        if items is None:
            notes.append(f"API {kind}: не удалось получить список ({err})")
        else:
            lists[status] = items
            api[f"{kind}_n"] = len(items)
    pstats_ = upsert(conn, lists, now, active_ok=api.get("active", False))
    if pstats_["extra_keys"]:
        notes.append("Новые поля в ответе API (пока не используются): " + ", ".join(pstats_["extra_keys"][:20]))
    probs = {k: v for k, v in pstats_["problems"].items() if k not in ("target_keys",)}
    if probs:
        notes.append("Не распознано в сигналах: " + ", ".join(f"{k} ×{v}" for k, v in sorted(probs.items())))
        for ex in pstats_["examples"][:2]:
            notes.append("Пример непонятного сигнала: " + json.dumps(ex, ensure_ascii=False)[:300])
    src = klines or KlineSource()
    budget = _Budget(budget_s, max_requests)
    st = process(conn, now, src, budget, notes)
    pruned = prune(conn, now)
    sources = dict(getattr(src, "used", {}) or {})
    sources.update(requests=budget.used, unfinished=st["unfinished"], fetch_errors=st["fetch_errors"],
                   left_for_next_run=st["skipped_budget"], pruned_rows=pruned)
    for e in (getattr(src, "errors", None) or [])[:5]:
        if len(notes) < 50:
            notes.append("Свечи: " + e)
    return build_doc(conn, now, notes, api, sources)


def _clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def write(out_dir: str = "web", conn=None, **kw) -> dict:
    if conn is None:
        os.makedirs("data", exist_ok=True)
        conn = sqlite3.connect("data/agents.sqlite")
    d = _clean(run(conn, **kw))
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(out_dir, "app_signals.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(out_dir, "app_signals.json"))
    return d
