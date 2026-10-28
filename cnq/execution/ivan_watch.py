"""«Иван тест» — private read-only monitor of one Bybit account (runs on the owner's Mac inside the mirror).

The account is traded by a third-party platform that advertises a 100 % winrate. This module reads it with a READ-ONLY key:
balance, open positions, closed PnL and spot fills. It keeps the history in a local state file (~/.cryptonova_ivan.json,
outside the repo, mode 600) and computes honest stats. Winrate counts CLOSED trades only, so losers that are held and
averaged down appear in max_dd / worst_open, not in the winrate. The result is encrypted with a passphrase and published
as web/ivan.enc.json. Without the passphrase the public file shows only its timestamp.

Env: IVAN_BYBIT_KEY, IVAN_BYBIT_SECRET (read-only key), IVAN_PASS (passphrase; the panel asks for the same one).
     Optional: IVAN_STATE (state file), IVAN_EVERY (minutes between publishes, default 15), CNQ_GH_TOKEN / CNQ_GH_REPO.
     Any of the three IVAN_* missing → run() / check() return None and do nothing.

Host: https://api.bybit.com first; if the key is rejected there (10003/10004) → https://api-demo.bybit.com (Demo Trading).
The working host is remembered in the state file and re-probed only when it starts rejecting the key.

Encryption (stdlib here, WebCrypto in the browser — scripts/ivan_decrypt.js):
  km = PBKDF2-HMAC-SHA256(utf8(pass), salt16, 200 000, 64 bytes); enc_key = km[:32], mac_key = km[32:]
  keystream block i = HMAC-SHA256(enc_key, nonce16 || uint32_be(i)); ct = utf8(compact json) XOR keystream
  tag = HMAC-SHA256(mac_key, salt || nonce || ct)          (checked before decrypting)
  file = {"v":1,"alg":"pbkdf2-sha256-200k/hmac-ctr/hmac","asof":ms,"salt","nonce","ct","tag"}   (standard base64)
Stdlib only."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request

HOSTS = {"main": "https://api.bybit.com", "demo": "https://api-demo.bybit.com"}
RECV = "10000"
KEY_REJECTED = {10003, 10004, 33004}   # invalid key / bad signature / expired key → try the other host
DAY = 86_400_000
WINDOW = 7 * DAY                       # Bybit closed-pnl: endTime − startTime ≤ 7 days
BACKFILL = 60 * DAY                    # first run goes back 60 days
OVERLAP = 30 * 60_000                  # later runs re-read the last 30 min (late records), dedup by id
GONE_KEEP = 2 * 3600_000               # how long a closed position's "first seen" is kept for matching its closed-pnl rows
MAX_HIST = 600          # keep the public encrypted file small (git history grows with every publish)
MAX_CLOSED_OUT = 100
MAX_CLOSED_STATE = 5000
MAX_SPOT_STATE = 500
ITER = 200_000
ALG = "pbkdf2-sha256-200k/hmac-ctr/hmac"
BLOB_KEYS = {"v", "alg", "asof", "salt", "nonce", "ct", "tag"}
PATH = "web/ivan.enc.json"
DEFAULT_REPO = "dmytrosukhyna/cryptonova-cockpit"
EVERY_S = max(1.0, float(os.environ.get("IVAN_EVERY", "15") or 15)) * 60
NOTE = ("Винрейт считает только закрытые сделки: если убыточные позиции не закрывать, а усреднять, винрейт будет 100%, "
        "а риск — в открытых позициях и просадке счёта.")


class BybitError(RuntimeError):
    def __init__(self, code, msg, path=""):
        super().__init__(f"Bybit {path}: {code} {msg}")
        self.code, self.msg, self.path = code, msg, path


class KeyRejected(BybitError):
    pass


class BadPassword(ValueError):
    pass


# ── env / state ──────────────────────────────────────────────────────────────────────────────────────────────────────
def creds(env=None):
    env = os.environ if env is None else env
    key, secret, pw = (env.get("IVAN_BYBIT_KEY") or "").strip(), (env.get("IVAN_BYBIT_SECRET") or "").strip(), env.get("IVAN_PASS") or ""
    return (key, secret, pw) if key and secret and pw else None


def state_path(env=None) -> str:
    env = os.environ if env is None else env
    return os.path.expanduser(env.get("IVAN_STATE") or "~/.cryptonova_ivan.json")


def load_state(env=None) -> dict:
    try:
        with open(state_path(env), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_state(st: dict, env=None) -> None:
    p = state_path(env)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, separators=(",", ":"), ensure_ascii=False)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


# ── Bybit v5 (signed GET) ────────────────────────────────────────────────────────────────────────────────────────────
def sign(secret: str, ts: str, key: str, query: str) -> str:
    return hmac.new(secret.encode(), (ts + key + RECV + query).encode(), hashlib.sha256).hexdigest()


def make_fetch(key: str, secret: str, timeout: int = 15):
    """fetch(base, path, params) → the raw Bybit JSON (with retCode). HTTP errors become retCode −status (401 → 10003)."""
    def fetch(base: str, path: str, params: dict) -> dict:
        q = "&".join(f"{k}={v}" for k, v in params.items())
        ts = str(int(time.time() * 1000))
        req = urllib.request.Request(f"{base}{path}?{q}", headers={
            "X-BAPI-API-KEY": key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": RECV, "X-BAPI-SIGN": sign(secret, ts, key, q),
            "User-Agent": "cnq-ivan"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:400]
            try:
                out = json.loads(body)
                if isinstance(out, dict) and "retCode" in out:
                    return out
            except Exception:  # noqa: BLE001
                pass
            if e.code == 401:
                return {"retCode": 10003, "retMsg": "HTTP 401"}
            return {"retCode": -e.code, "retMsg": f"HTTP {e.code} {body[:120]}"}
    return fetch


def _call(fetch, base: str, path: str, params: dict) -> dict:
    out = fetch(base, path, params) or {}
    try:
        code = int(out.get("retCode"))
    except (TypeError, ValueError):
        code = -1
    if code == 0:
        return out.get("result") or {}
    if code in KEY_REJECTED:
        raise KeyRejected(code, out.get("retMsg"), path)
    raise BybitError(code, out.get("retMsg"), path)


def _f(x) -> float:
    try:
        return float(x) if x not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _sym(s: str) -> str:
    s = s or ""
    return s[:-4] if s.endswith("USDT") and len(s) > 4 else s


def parse_wallet(acc: dict) -> dict:
    eq, wal = _f(acc.get("totalEquity")), _f(acc.get("totalWalletBalance"))
    upnl, av = _f(acc.get("totalPerpUPL")), _f(acc.get("totalAvailableBalance"))
    coins = acc.get("coin") or []
    if not eq and coins:   # classic CONTRACT account: totals are per coin
        eq = sum(_f(c.get("usdValue")) or (_f(c.get("equity")) if c.get("coin") in ("USDT", "USDC") else 0.0) for c in coins)
        u = next((c for c in coins if c.get("coin") == "USDT"), {})
        wal = wal or _f(u.get("walletBalance"))
        upnl = upnl or _f(u.get("unrealisedPnl"))
        av = av or _f(u.get("availableToWithdraw")) or _f(u.get("availableBalance"))
    return {"equity": eq, "wallet": wal, "upnl": upnl, "available": av, "account": acc.get("accountType") or ""}


def read_wallet(fetch, base: str) -> dict:
    """UNIFIED first, CONTRACT if UNIFIED is refused or empty. KeyRejected propagates (host probing relies on it)."""
    err = None
    for at in ("UNIFIED", "CONTRACT"):
        try:
            res = _call(fetch, base, "/v5/account/wallet-balance", {"accountType": at})
        except KeyRejected:
            raise
        except BybitError as e:
            err = err or e
            continue
        acc = (res.get("list") or [None])[0]
        if acc:
            w = parse_wallet(acc)
            w["account"] = w["account"] or at
            return w
    if err:
        raise err
    return {"equity": 0.0, "wallet": 0.0, "upnl": 0.0, "available": 0.0, "account": ""}


def probe(fetch, prefer: str | None = None):
    """→ (host_name, wallet). The remembered host is tried first; the other one only if the key is rejected there."""
    order = [prefer] + [h for h in HOSTS if h != prefer] if prefer in HOSTS else list(HOSTS)
    last = None
    for name in order:
        try:
            return name, read_wallet(fetch, HOSTS[name])
        except KeyRejected as e:
            last = e
    raise last or KeyRejected(10003, "no host", "/v5/account/wallet-balance")


def read_positions(fetch, base: str) -> list[dict]:
    out, cursor = [], ""
    for _ in range(10):
        p = {"category": "linear", "settleCoin": "USDT", "limit": 200, **({"cursor": cursor} if cursor else {})}
        res = _call(fetch, base, "/v5/position/list", p)
        lst = res.get("list") or []
        out += [x for x in lst if _f(x.get("size")) > 0]
        cursor = res.get("nextPageCursor") or ""
        if not cursor or not lst:
            break
    return out


def windows(start: int, end: int) -> list[tuple[int, int]]:
    """[start, end] cut into contiguous ≤ 7-day windows, newest first."""
    out, e = [], end
    while e > start:
        s = max(start, e - WINDOW + 1)
        out.append((s, e))
        e = s - 1
    return out


def read_closed(fetch, base: str, start: int, end: int) -> list[dict]:
    recs = []
    for s, e in windows(start, end):
        cursor = ""
        for _ in range(50):
            p = {"category": "linear", "limit": 100, "startTime": s, "endTime": e, **({"cursor": cursor} if cursor else {})}
            res = _call(fetch, base, "/v5/position/closed-pnl", p)
            lst = res.get("list") or []
            recs += lst
            cursor = res.get("nextPageCursor") or ""
            if not cursor or not lst:
                break
    return recs


def read_spot(fetch, base: str) -> list[dict]:
    recs, cursor = [], ""
    for _ in range(3):
        p = {"category": "spot", "limit": 100, **({"cursor": cursor} if cursor else {})}
        res = _call(fetch, base, "/v5/execution/list", p)
        lst = res.get("list") or []
        recs += lst
        cursor = res.get("nextPageCursor") or ""
        if not cursor or not lst:
            break
    return recs


# ── normalisation ────────────────────────────────────────────────────────────────────────────────────────────────────
def norm_closed(r: dict) -> dict:
    """Bybit closed-pnl row → trade. Bybit's `side` there is the CLOSING order side (Sell closes a LONG). When fees are
    given, the exact check gross = closedPnl + fees ≈ ±(exitValue − entryValue) overrides that if it clearly disagrees."""
    qty = _f(r.get("closedSize")) or _f(r.get("qty"))
    entry, exit_, pnl = _f(r.get("avgEntryPrice")), _f(r.get("avgExitPrice")), _f(r.get("closedPnl"))
    side = "LONG" if r.get("side") == "Sell" else "SHORT"
    fee = None
    if r.get("openFee") not in (None, "") or r.get("closeFee") not in (None, ""):
        fee = _f(r.get("openFee")) + _f(r.get("closeFee"))
        d = (_f(r.get("cumExitValue")) - _f(r.get("cumEntryValue"))) or (exit_ - entry) * qty
        if d:
            e_long, e_short = abs(pnl + fee - d), abs(pnl + fee + d)
            if min(e_long, e_short) < max(e_long, e_short) / 3:
                side = "LONG" if e_long < e_short else "SHORT"
    close_t = int(_f(r.get("createdTime")) or _f(r.get("updatedTime")))
    return {"id": f"{r.get('orderId') or r.get('symbol')}|{close_t}|{r.get('closedSize')}", "symbol": _sym(r.get("symbol")),
            "side": side, "qty": qty, "entry": entry, "exit": exit_, "pnl": round(pnl, 4),
            "fee": round(fee, 4) if fee is not None else None, "open_time": None, "close_time": close_t}


def norm_position(p: dict, seen: dict) -> dict:
    side = "LONG" if p.get("side") == "Buy" else "SHORT"
    sym = _sym(p.get("symbol"))
    o = seen.get(f"{sym}|{side}") or {}
    return {"symbol": sym, "side": side, "size": _f(p.get("size")), "entry": _f(p.get("avgPrice")), "mark": _f(p.get("markPrice")),
            "upnl": round(_f(p.get("unrealisedPnl")), 4), "leverage": _f(p.get("leverage")), "liq": _f(p.get("liqPrice")) or None,
            "value": _f(p.get("positionValue")), "created": int(_f(p.get("createdTime"))) or None, "seen": o.get("seen")}


def norm_spot(x: dict) -> dict:
    return {"id": x.get("execId") or f"{x.get('orderId')}|{x.get('execTime')}", "symbol": _sym(x.get("symbol")),
            "side": "BUY" if x.get("side") == "Buy" else "SELL", "qty": _f(x.get("execQty")), "price": _f(x.get("execPrice")),
            "value": _f(x.get("execValue")), "fee": _f(x.get("execFee")), "time": int(_f(x.get("execTime")))}


# ── stats ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def stats(trades: list[dict]) -> dict:
    """Closed trades only. winrate = wins / n (fraction); breakeven (pnl == 0) is neither a win nor a loss."""
    pnls = [t["pnl"] for t in trades]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    by: dict[str, dict] = {}
    for t in trades:
        b = by.setdefault(t["symbol"], {"n": 0, "pnl": 0.0})
        b["n"] += 1
        b["pnl"] += t["pnl"]
    n = len(trades)
    r4 = lambda x: round(x, 4)  # noqa: E731
    fees = [t["fee"] for t in trades if t.get("fee") is not None]
    return {"n": n, "wins": len(wins), "losses": len(losses), "breakeven": n - len(wins) - len(losses),
            "winrate": round(len(wins) / n, 4) if n else None, "fees_sum": r4(sum(fees)) if fees else None, "fees_known": len(fees),
            "pnl_sum": r4(sum(pnls)), "avg_win": r4(sum(wins) / len(wins)) if wins else None,
            "avg_loss": r4(sum(losses) / len(losses)) if losses else None, "max_loss": r4(min(losses)) if losses else None,
            "best": r4(max(pnls)) if pnls else None,
            "by_symbol": {k: {"n": v["n"], "pnl": r4(v["pnl"])} for k, v in sorted(by.items(), key=lambda kv: kv[1]["pnl"])},
            "first_trade": min((t.get("open_time") or t["close_time"]) for t in trades) if trades else None}


def max_drawdown(hist) -> float:
    """Largest peak-to-trough fall of [[t, equity], …] as a fraction (0.25 = −25 %)."""
    peak, dd = 0.0, 0.0
    for _, e in hist:
        if e > peak:
            peak = e
        elif peak > 0:
            dd = max(dd, (peak - e) / peak)
    return round(dd, 4)


def _thin(hist: list) -> list:
    """≤ MAX_HIST points: when full, every other point of the oldest half is dropped (old history gets sparser, not cut)."""
    if len(hist) <= MAX_HIST:
        return hist
    h = MAX_HIST // 2
    return hist[:h][::2] + hist[h:]


# ── collect → dict ───────────────────────────────────────────────────────────────────────────────────────────────────
def collect(state: dict, now: int, fetch) -> dict:
    host, w = probe(fetch, state.get("host"))
    state["host"] = host
    base = HOSTS[host]
    state.setdefault("since", now)
    raw_pos = read_positions(fetch, base)

    # first time each open position was seen by this watcher (Bybit's createdTime may be the first-ever position on the symbol)
    seen = state.setdefault("open_seen", {})
    cur = set()
    for p in raw_pos:
        k = f"{_sym(p.get('symbol'))}|{'LONG' if p.get('side') == 'Buy' else 'SHORT'}"
        cur.add(k)
        if k not in seen or seen[k].get("gone"):
            seen[k] = {"seen": now}

    since_sync = state.get("closed_sync")
    recs = read_closed(fetch, base, (since_sync - OVERLAP) if since_sync else now - BACKFILL, now)
    have = {t["id"] for t in state.setdefault("closed", [])}
    for t in sorted((norm_closed(r) for r in recs), key=lambda t: t["close_time"]):
        if t["id"] in have:
            continue
        have.add(t["id"])
        o = seen.get(f"{t['symbol']}|{t['side']}")
        if o and state["since"] < o["seen"] <= t["close_time"]:   # opened while watched → open time known to ±1 publish
            t["open_time"] = o["seen"]
        state["closed"].append(t)
    state["closed"].sort(key=lambda t: t["close_time"])
    del state["closed"][:-MAX_CLOSED_STATE]
    state["closed_sync"] = now
    for k in list(seen):
        if k in cur:
            continue
        seen[k].setdefault("gone", now)
        if now - seen[k]["gone"] > GONE_KEEP:
            del seen[k]

    try:   # spot fills: best effort (many accounts have none; some keys lack the permission)
        have_s = {x["id"] for x in state.setdefault("spot", [])}
        for x in map(norm_spot, read_spot(fetch, base)):
            if x["id"] not in have_s:
                have_s.add(x["id"])
                state["spot"].append(x)
        state["spot"].sort(key=lambda x: x["time"])
        del state["spot"][:-MAX_SPOT_STATE]
    except Exception:  # noqa: BLE001
        pass

    positions = sorted((norm_position(p, seen) for p in raw_pos), key=lambda p: p["upnl"])
    eq = w["equity"]
    upnl = w["upnl"] or sum(p["upnl"] for p in positions)
    hist = state.setdefault("equity_hist", [])
    hist.append([now, round(eq, 2)])
    state["equity_hist"] = hist = _thin(hist)
    state["eq_peak"] = peak = max(state.get("eq_peak") or 0.0, eq)
    dd_now = (peak - eq) / peak if peak > 0 else 0.0
    state["max_dd_all"] = round(max(state.get("max_dd_all") or 0.0, dd_now, max_drawdown(hist)), 4)
    worst = min((p["upnl"] for p in positions), default=0.0)
    closed = state["closed"]
    return {"asof": now, "host": host, "account": w["account"], "equity": round(eq, 2), "wallet": round(w["wallet"], 2),
            "upnl": round(upnl, 2), "available": round(w["available"], 2), "positions": positions,
            "closed": [{k: v for k, v in t.items() if k != "id"} for t in reversed(closed[-MAX_CLOSED_OUT:])],
            "stats": stats(closed), "equity_hist": hist, "max_dd": state["max_dd_all"],
            "worst_open": round(worst / eq, 4) if worst < 0 and eq > 0 else 0.0,
            "spot": [{k: v for k, v in x.items() if k != "id"} for x in reversed(state.get("spot", [])[-100:])],
            "since": state["since"], "note": NOTE}


# ── encryption ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _keys(pw: str, salt: bytes) -> tuple[bytes, bytes]:
    km = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, ITER, dklen=64)
    return km[:32], km[32:]


def _xor_stream(enc_key: bytes, nonce: bytes, data: bytes) -> bytes:
    n = len(data)
    if not n:
        return b""
    ks = b"".join(hmac.new(enc_key, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest() for i in range((n + 31) // 32))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(n, "big")


def encrypt(obj: dict, pw: str, asof: int | None = None, salt: bytes | None = None, nonce: bytes | None = None) -> dict:
    pt = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    salt, nonce = salt or os.urandom(16), nonce or os.urandom(16)
    ek, mk = _keys(pw, salt)
    ct = _xor_stream(ek, nonce, pt)
    tag = hmac.new(mk, salt + nonce + ct, hashlib.sha256).digest()
    b64 = lambda x: base64.b64encode(x).decode()  # noqa: E731
    asof = asof if asof is not None else (obj.get("asof") if isinstance(obj, dict) else None)
    return {"v": 1, "alg": ALG, "asof": int(asof or time.time() * 1000), "salt": b64(salt), "nonce": b64(nonce), "ct": b64(ct), "tag": b64(tag)}


def decrypt(blob, pw: str):
    if isinstance(blob, (str, bytes)):
        blob = json.loads(blob)
    if not isinstance(blob, dict) or blob.get("v") != 1:
        raise ValueError("неизвестный формат файла")
    salt, nonce, ct, tag = (base64.b64decode(blob[k]) for k in ("salt", "nonce", "ct", "tag"))
    ek, mk = _keys(pw, salt)
    if not hmac.compare_digest(hmac.new(mk, salt + nonce + ct, hashlib.sha256).digest(), tag):
        raise BadPassword("Неверный пароль")
    return json.loads(_xor_stream(ek, nonce, ct).decode("utf-8"))


# ── stats only (28.10, owner's VIP spec §10): what a test needs to be judged, and nothing about the account ───────────────
STATS_PATH = "web/ivan_stats.json"
STATS_KEYS = {"v", "asof", "since", "first_trade", "closed_n", "wins", "losses", "breakeven", "win_rate", "net_closed_usd", "fees_usd",
              "fees_known", "open_n", "open_upnl_usd", "max_dd_pct", "partial_note_ru", "note_ru"}


def public_stats(out: dict) -> dict:
    """The stats-only summary that may be public: closed trades, wins / losses / breakeven, win rate with its sample size, the closed
    result after fees, the number of open positions and their open result, the account's worst drawdown in %. NO balance, equity,
    host, account type, symbols, sizes, prices or positions (those stay in the encrypted file)."""
    st = out.get("stats") or {}
    n = int(st.get("n") or 0)
    d = {"v": 1, "asof": out.get("asof"), "since": out.get("since"), "first_trade": st.get("first_trade"), "closed_n": n,
         "wins": st.get("wins"), "losses": st.get("losses"), "breakeven": st.get("breakeven"),
         "win_rate": round(100 * (st.get("wins") or 0) / n, 1) if n else None, "net_closed_usd": st.get("pnl_sum"),
         "fees_usd": st.get("fees_sum"), "fees_known": st.get("fees_known"), "open_n": len(out.get("positions") or []),
         "open_upnl_usd": out.get("upnl"), "max_dd_pct": round(100 * float(out.get("max_dd") or 0), 2),
         "partial_note_ru": "Каждое закрытие на бирже (и частичное) — отдельная строка Bybit closed-pnl, так она и считается сделкой.",
         "note_ru": NOTE}
    return {k: v for k, v in d.items() if k in STATS_KEYS}


def publish_stats(doc: dict, token: str, repo: str = DEFAULT_REPO, http=None) -> bool:
    """PUT web/ivan_stats.json — only the STATS_KEYS fields are accepted (anything else → ValueError, nothing is sent)."""
    if not token:
        return False
    if not isinstance(doc, dict) or set(doc) - STATS_KEYS:
        raise ValueError("ivan: refusing to publish fields outside the stats-only summary")
    url = f"https://api.github.com/repos/{repo}/contents/{STATS_PATH}"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-ivan"}
    content = base64.b64encode(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()).decode()
    return put_contents(url, hdr, content, "ivan: stats", http=http)


# ── publish ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _http(method: str, url: str, headers: dict, data: bytes | None = None, timeout: int = 20):
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
        return r.status, (json.loads(body) if body else None)


def publish_ivan(blob: dict, token: str, repo: str = DEFAULT_REPO, http=None, path: str = PATH, message: str = "ivan: update") -> bool:
    """PUT web/ivan.enc.json through the GitHub contents API (GET sha first), like mirror.publish does for bybit.json.
    Only an encrypted blob is accepted: any field outside v/alg/asof/salt/nonce/ct/tag → ValueError, nothing is sent."""
    if not token:
        return False
    if not isinstance(blob, dict) or not {"salt", "nonce", "ct", "tag"} <= set(blob) or set(blob) - BLOB_KEYS:
        raise ValueError("ivan: refusing to publish a payload that is not the encrypted blob")
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-ivan"}
    content = base64.b64encode(json.dumps(blob, separators=(",", ":")).encode()).decode()
    return put_contents(url, hdr, content, message, http=http)


CONFLICT = (409, 422)      # the file changed between GET and PUT: main gets bot commits every few minutes


def put_contents(url: str, hdr: dict, content_b64: str, message: str, http=None, tries: int = 3, sleep=time.sleep) -> bool:
    """GitHub contents API: GET the current sha, PUT the new content. On a version conflict (409/422 — another commit landed
    between GET and PUT) wait and retry with a fresh sha, up to `tries` times; the last conflict is raised as HTTPError."""
    http = http or _http
    for i in range(tries):
        sha = None
        try:
            st, js = http("GET", url + "?ref=main", hdr, None)
            if st == 200 and isinstance(js, dict):
                sha = js.get("sha")
        except Exception:  # noqa: BLE001
            pass
        data = {"message": message, "branch": "main", "content": content_b64, **({"sha": sha} if sha else {})}
        try:
            st, _ = http("PUT", url, {**hdr, "Content-Type": "application/json"}, json.dumps(data).encode())
        except urllib.error.HTTPError as e:
            if e.code not in CONFLICT or i == tries - 1:
                raise
            st = e.code
        if st in (200, 201):
            return True
        if st not in CONFLICT:
            return False
        if i < tries - 1:
            sleep(2 * (i + 1))
    raise urllib.error.HTTPError(url, 409, "conflict", None, None)


# ── entry points ─────────────────────────────────────────────────────────────────────────────────────────────────────
def run(state: dict | None = None, now: int | None = None, fetch=None, put=None, env=None, save=None, put_stats=None):
    """fetch → stats → encrypt → publish. Returns the plaintext dict (None when IVAN_* env is missing).
    state=None → loaded from / saved to the state file; a dict passed in is mutated and saved only via `save`.
    fetch(base, path, params) → Bybit JSON;  put(blob) → bool (default: publish_ivan with CNQ_GH_TOKEN / CNQ_GH_REPO);
    put_stats(doc) → bool (default: publish_stats — the stats-only web/ivan_stats.json, 28.10)."""
    c = creds(env)
    if not c:
        return None
    key, secret, pw = c
    env = os.environ if env is None else env
    now = int(now or time.time() * 1000)
    if state is None:
        state = load_state(env)
        save = save or (lambda s: save_state(s, env))
    out = collect(state, now, fetch or make_fetch(key, secret))
    if save:
        save(state)
    blob = encrypt(out, pw, asof=now)
    if put is None:
        tok, repo = env.get("CNQ_GH_TOKEN", ""), env.get("CNQ_GH_REPO") or DEFAULT_REPO
        put = (lambda b: publish_ivan(b, tok, repo)) if tok else None
    if put is not None and not put(blob):
        raise RuntimeError("GitHub не принял web/ivan.enc.json")
    if put_stats is None:
        tok, repo = env.get("CNQ_GH_TOKEN", ""), env.get("CNQ_GH_REPO") or DEFAULT_REPO
        put_stats = (lambda d: publish_stats(d, tok, repo)) if tok else None
    if put_stats is not None:
        try:                                    # the stats-only file never blocks the encrypted one
            put_stats(public_stats(out))
        except Exception as e:  # noqa: BLE001
            print(f"Иван тест: web/ivan_stats.json не опубликован — {str(e)[:120]}", flush=True)
    return out


def describe_error(e: Exception) -> str:
    """One short Russian line for the mirror's terminal."""
    if isinstance(e, KeyRejected):
        return "Иван тест: ключ не принят (ни основной Bybit, ни demo) — проверь, что ключ только на чтение и скопирован полностью"
    if isinstance(e, BybitError):
        known = {10002: "часы этого Mac расходятся с Bybit — включи автоматическое время в настройках",
                 10005: "у ключа нет прав на чтение счёта — пересоздай ключ «только чтение» с доступом к контрактам/позициям и кошельку",
                 10010: "ключ привязан к другому IP — пересоздай без привязки к IP или добавь IP этого Mac",
                 -403: "Bybit не пускает запросы из этой сети (HTTP 403)"}
        return f"Иван тест: {known.get(e.code) or f'Bybit ответил {e.code} {e.msg}'}"[:220]
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403, 404):
            return f"Иван тест: GitHub не принял файл (HTTP {e.code}) — проверь CNQ_GH_TOKEN"
        if e.code in CONFLICT:
            return "Иван тест: GitHub был занят другим обновлением — повторю в следующий раз, ничего делать не нужно"
        return f"Иван тест: GitHub ответил HTTP {e.code} — повторю в следующий раз"
    if isinstance(e, (urllib.error.URLError, TimeoutError, OSError)):
        return "Иван тест: нет связи с Bybit/GitHub — повторю в следующий раз"
    return f"Иван тест: ошибка — {str(e)[:150]}"


def check(env=None, fetch=None, save: bool = True) -> str | None:
    """One read-only call for `mirror --check`. None when IVAN_* env is missing (the monitor is optional)."""
    c = creds(env)
    if not c:
        return None
    key, secret, pw = c
    fetch = fetch or make_fetch(key, secret)
    weak = (f" · совет: IVAN_PASS короткий ({len(pw)} симв.) — файл ivan.enc.json лежит в открытом репозитории, нужна фраза из 12+ символов"
            if len(pw or "") < 12 else "")                                          # 22.10 (audit): the passphrase is the only protection
    try:
        st = load_state(env)
        host, w = probe(fetch, st.get("host"))
        if save and st.get("host") != host:
            st["host"] = host
            save_state(st, env)
        msg = f"Иван тест: ключ работает ({'demo' if host == 'demo' else 'основной'}), баланс ${w['equity']:,.2f}"
        try:   # is the key really read-only? (best effort)
            info = _call(fetch, HOSTS[host], "/v5/user/query-api", {})
            if str(info.get("readOnly")) == "0":
                msg += " · внимание: у ключа есть право торговли, для монитора хватит ключа «только чтение»"
            if info.get("expiredAt"):
                msg += f" · ключ действует до {str(info['expiredAt'])[:10]}"
        except Exception:  # noqa: BLE001
            pass
        return msg + weak
    except Exception as e:  # noqa: BLE001
        return describe_error(e)
