"""«Мой портфель · OKX» — the owner's own spot portfolio, read-only and encrypted (21.10.2026, owner: «мой портфель находится на
OKX … не хочу заморачиваться с миграцией»; «хотелось бы видеть свой портфель, список активов как на биржах, объяснение по
каждому, почему мы его взяли … рекомендации, можно докупить то … учитывать недооценённые активы, это очень важно»).

Runs on the owner's Mac inside the mirror (scripts/mirror_mac.sh), like «Иван тест» (ivan_watch.py):
  1. a READ-ONLY OKX key reads the balances (trading account, funding account, Simple Earn) and the public spot tickers;
  2. the public CRYPTO NOVA model (web/altseason.json — target shares; web/quality.json — the quality screen of alts;
     web/coin_profiles.json — what each project is) gives every coin a «почему держим» line and the rebalancing plan;
  3. the result is encrypted with the owner's passphrase (the ivan_watch scheme: PBKDF2-SHA256 200 000 → HMAC-CTR + HMAC tag)
     and published as web/myspot.enc.json. The repo is public: only the encrypted blob ever leaves the Mac; the publisher
     refuses anything else. The panel decrypts it in the browser (ivanDecrypt) — no plaintext anywhere.

No trading here at all: the OKX client below has GET methods only. The plan is the model's rebalancing plan, written from real
data (live prices, the model's target shares, the quality screen); a missing score is shown as missing, never invented.
Not financial advice.

Env (~/.cryptonova.env on the Mac, never in the repo or in a chat):
  MYSPOT_OKX_KEY, MYSPOT_OKX_SECRET, MYSPOT_OKX_PASSPHRASE — an OKX API key with the «Read» permission only;
  MYSPOT_PASS — the passphrase (best: the same password as the panel login, then the panel opens the card by itself).
  Optional: MYSPOT_OKX_HOST (www.okx.com / eea.okx.com / my.okx.com — found by itself), MYSPOT_STATE, MYSPOT_EVERY (minutes, 15),
  MYSPOT_MODEL_SRC (where the public model JSON is read from), CNQ_GH_TOKEN / CNQ_GH_REPO (publishing).
  Any of the four MYSPOT_* missing → run() / check() return None and do nothing.
Stdlib only."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import ivan_watch

HOSTS = ["https://www.okx.com", "https://eea.okx.com", "https://my.okx.com"]   # global · OKX Europe (EEA) · OKX for some regions
# wrong environment / invalid key / key doesn't exist / invalid authorization / 60032: an OKX Europe key on the wrong domain
# (OKX Europe keys work only on eea.okx.com or my.okx.com) — all mean «try the next host»
KEY_REJECTED = {"50101", "50111", "50119", "50114", "60032"}
PATH = "web/myspot.enc.json"
DEFAULT_REPO = ivan_watch.DEFAULT_REPO
MODEL_SRC = os.environ.get("MYSPOT_MODEL_SRC", "https://raw.githubusercontent.com/dmytrosukhyna/cryptonova-cockpit/main/web/")
EVERY_S = max(1.0, float(os.environ.get("MYSPOT_EVERY", "15") or 15)) * 60
CASH = {"USDT", "USDC", "FDUSD", "DAI", "TUSD", "USDE", "PYUSD", "USDG", "USD", "EUR"}
DUST_USD = 1.0
BAND_PP = 1.0          # within ±1 percentage point of the target → «держать», no order
MIN_ORDER = 10.0       # smaller moves are not worth an order (OKX minimum and fees)
FUT_KEY, EARN_KEY = "Фьючерсы (сигналы)", "USDT (Earn)"
MAX_HIST = 400
VT_CALM = 1.05         # 30-day volume ≤ 1.05 × its 180-day average = «не раздут»
NOTE = ("План — это ребалансировка по модели CRYPTO NOVA «ядро + альтсезон» на сегодняшних ценах. Это не обещание дохода и не "
        "финансовый совет. Ордеров система не ставит: решаете вы.")


class OkxError(RuntimeError):
    def __init__(self, code, msg, path=""):
        super().__init__(f"OKX {path}: {code} {msg}")
        self.code, self.msg, self.path = str(code), msg, path


class KeyRejected(OkxError):
    pass


# ── env / state ──────────────────────────────────────────────────────────────────────────────────────────────────────
def creds(env=None):
    env = os.environ if env is None else env
    c = tuple((env.get(k) or "").strip() for k in ("MYSPOT_OKX_KEY", "MYSPOT_OKX_SECRET", "MYSPOT_OKX_PASSPHRASE"))
    pw = env.get("MYSPOT_PASS") or ""
    return (*c, pw) if all(c) and pw else None


def state_path(env=None) -> str:
    env = os.environ if env is None else env
    return os.path.expanduser(env.get("MYSPOT_STATE") or "~/.cryptonova_myspot.json")


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


# ── OKX v5, GET only (read) ──────────────────────────────────────────────────────────────────────────────────────────
def sign(secret: str, ts: str, method: str, path: str, body: str = "") -> str:
    return base64.b64encode(hmac.new(secret.encode(), (ts + method + path + body).encode(), hashlib.sha256).digest()).decode()


def _ts() -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def make_fetch(key: str, secret: str, passphrase: str, timeout: int = 15):
    """fetch(host, path, params, signed) → the raw OKX JSON ({"code","msg","data"}). HTTP errors keep OKX's body when it has one.
    Only GET exists: this client can not place, move or cancel anything."""
    def fetch(host: str, path: str, params: dict | None = None, signed: bool = True) -> dict:
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        hdr = {"User-Agent": "cnq-myspot/1.0", "Accept": "application/json"}
        if signed:
            ts = _ts()
            hdr.update({"OK-ACCESS-KEY": key, "OK-ACCESS-SIGN": sign(secret, ts, "GET", path + q), "OK-ACCESS-TIMESTAMP": ts,
                        "OK-ACCESS-PASSPHRASE": passphrase})
        req = urllib.request.Request(host + path + q, headers=hdr, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:  # noqa: BLE001
                return {"code": str(-e.code), "msg": f"HTTP {e.code}", "data": []}
    return fetch


def _call(fetch, host: str, path: str, params: dict | None = None, signed: bool = True) -> list:
    out = fetch(host, path, params or {}, signed)
    code = str(out.get("code"))
    if code != "0":
        cls = KeyRejected if code in KEY_REJECTED else OkxError
        raise cls(code, out.get("msg") or "", path)
    return out.get("data") or []


def probe(fetch, prefer: str | None = None) -> tuple[str, list]:
    """The host where the key works (the remembered one first) and its trading-account balance."""
    order = ([prefer] if prefer in HOSTS else []) + [h for h in HOSTS if h != prefer]
    first = None
    for h in order:
        try:
            return h, _call(fetch, h, "/api/v5/account/balance")
        except KeyRejected as e:
            first = first or e
    raise first or KeyRejected("50111", "key rejected on every OKX host")


def read_raw(fetch, host: str, bal: list | None = None) -> dict:
    """Balances from the three places a spot coin can sit + the public spot tickers. Only the trading balance is required."""
    raw = {"trading": ((bal if bal is not None else _call(fetch, host, "/api/v5/account/balance")) or [{}])[0].get("details") or []}
    for k, path in (("funding", "/api/v5/asset/balances"), ("earn", "/api/v5/finance/savings/balance")):
        try:
            raw[k] = _call(fetch, host, path)
        except OkxError:
            raw[k] = []            # an old key without that scope, or Earn not available in the region — the rest still works
    raw["tickers"] = _call(fetch, host, "/api/v5/market/tickers", {"instType": "SPOT"}, signed=False)
    try:
        cfg = _call(fetch, host, "/api/v5/account/config")
        raw["perm"] = (cfg[0].get("perm") if cfg else "") or ""
    except OkxError:
        raw["perm"] = ""
    return raw


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def prices(tickers: list) -> dict:
    """BASE → (last, open24h) in dollars from the spot tickers: *-USDT first, then *-USDC (OKX Europe has no USDT pairs), then
    *-EUR converted at the USDC-EUR / USDT-EUR rate; stablecoins are $1."""
    px, by = {}, {}
    for t in tickers or []:
        inst = str(t.get("instId") or "")
        last, o = _f(t.get("last")), _f(t.get("open24h"))
        if last and "-" in inst:
            by[inst] = (last, o)
    eur = None                 # dollars per euro = 1 / (euros per USDC or USDT)
    for k in ("USDC-EUR", "USDT-EUR"):
        if k in by:
            last, o = by[k]
            eur = (1 / last, (1 / o) if o else None)
            break
    for inst, (last, o) in by.items():
        base, quote = inst.rsplit("-", 1)
        if quote == "USDT":
            px[base] = (last, o)
    for inst, (last, o) in by.items():
        base, quote = inst.rsplit("-", 1)
        if base in px:
            continue
        if quote == "USDC":
            px[base] = (last, o)
    for inst, (last, o) in by.items():
        base, quote = inst.rsplit("-", 1)
        if base in px or quote != "EUR" or not eur or base in ("USDT", "USDC"):
            continue
        px[base] = (last * eur[0], (o * eur[1]) if o and eur[1] else None)
    for c in ("USDT", "USDC", "FDUSD", "DAI", "TUSD", "USDE", "PYUSD", "USDG", "USD"):
        px.setdefault(c, (1.0, 1.0))
    if "EUR" not in px and eur:  # OKX Europe holds EUR
        px["EUR"] = eur
    return px


def assets(raw: dict) -> list[dict]:
    """One row per coin, everything merged: qty, price, value, 24 h, avg cost and P/L when OKX gives them (trading account)."""
    px = prices(raw.get("tickers"))
    acc: dict[str, dict] = {}

    def add(ccy, qty, where, eq_usd=None, avg=None, upl=None):
        ccy = str(ccy or "").upper()
        if not ccy or not qty or qty <= 0:
            return
        a = acc.setdefault(ccy, {"ccy": ccy, "qty": 0.0, "tq": 0.0, "where": [], "eq_usd": 0.0, "avg_px": None, "upl": None})
        a["qty"] += qty
        if where == "торговый счёт":
            a["tq"] += qty
        if where not in a["where"]:
            a["where"].append(where)
        if eq_usd:
            a["eq_usd"] += eq_usd
        if avg and avg > 0:
            a["avg_px"] = avg
        if upl is not None:
            a["upl"] = (a["upl"] or 0.0) + upl

    for c in raw.get("trading") or []:
        add(c.get("ccy"), _f(c.get("eq")) or _f(c.get("cashBal")), "торговый счёт", _f(c.get("eqUsd")),
            _f(c.get("accAvgPx")) or _f(c.get("openAvgPx")), _f(c.get("spotUpl")))
    for c in raw.get("funding") or []:
        add(c.get("ccy"), _f(c.get("bal")), "счёт финансирования")
    for c in raw.get("earn") or []:
        add(c.get("ccy"), _f(c.get("amt")), "Earn")

    rows = []
    for a in acc.values():
        p = px.get(a["ccy"])
        last, o = (p if p else (None, None))
        usd = a["qty"] * last if last else (a["eq_usd"] or None)
        if usd is None or usd < DUST_USD:
            continue
        r = {"ccy": a["ccy"], "qty": a["qty"], "px": last, "usd": round(usd, 2), "where": a["where"],
             "ch24": round((last / o - 1) * 100, 2) if last and o else None, "cash": a["ccy"] in CASH}
        if a["avg_px"] and last and not r["cash"] and a["tq"] > 0:
            # OKX keeps the average cost for the trading account only, so the P/L is for the coins there (cost elsewhere is unknown)
            r["avg_px"] = a["avg_px"]
            r["pnl_usd"] = round((last - a["avg_px"]) * a["tq"], 2)
            r["pnl_pct"] = round((last / a["avg_px"] - 1) * 100, 2)
            if a["tq"] < a["qty"] * 0.999:
                r["pnl_part"] = True
        rows.append(r)
    tot = sum(r["usd"] for r in rows) or 1.0
    for r in rows:
        r["share"] = round(r["usd"] / tot * 100, 2)
    return sorted(rows, key=lambda r: -r["usd"])


# ── the model: why each coin is held, and the plan ───────────────────────────────────────────────────────────────────
def _years(days) -> str:
    if not days:
        return ""
    y = days / 365.25
    if y < 1:
        return f"{int(days)} дн."
    n = int(round(y))
    return f"{n} {'год' if n % 10 == 1 and n % 100 != 11 else 'года' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'лет'}"


def _money(v: float) -> str:
    v = float(v)
    if v >= 1e9:
        return f"${v / 1e9:.1f} млрд".replace(".", ",")
    if v >= 1e6:
        return f"${v / 1e6:.1f} млн".replace(".", ",")
    if v >= 1e3:
        return f"${v / 1e3:.0f} тыс."
    return f"${v:.0f}"


def _p(v: float) -> str:
    """39.2 → «39,2» (Russian decimal comma), 15.0 → «15»."""
    return f"{v:g}".replace(".", ",")


def quality_facts(q: dict | None, universe: int | None = None) -> dict | None:
    """The quality screen's real numbers for one alt (web/quality.json row) in plain words. None when the screen has no row."""
    if not q:
        return None
    m3, vt = _f(q.get("m3")), _f(q.get("vt"))
    parts = []
    if q.get("rank"):
        parts.append(f"{q['rank']} место из {universe or '—'} в анкете качества")
    if q.get("age"):
        parts.append(f"монете {_years(q['age'])}")
    if m3 is not None:
        parts.append("за 3 мес. наравне с BTC" if abs(m3) < 0.005 else f"за 3 мес. к BTC {'+' if m3 > 0 else '−'}{abs(m3) * 100:.0f}%")
    if vt is not None:
        parts.append("объём торгов не раздут" if vt <= VT_CALM else f"объём торгов вырос ×{vt:.1f}")
    if q.get("rev"):
        parts.append(f"у проекта есть выручка ({_money(q['rev30'])} за 30 дней)" if _f(q.get("rev30")) else "у проекта есть выручка")
    else:
        parts.append("выручки у проекта нет")
    calm = (m3 is not None and m3 <= 0) and (vt is None or vt <= VT_CALM)
    return {"rank": q.get("rank"), "score": q.get("score"), "age": q.get("age"), "m3": m3, "vt": vt, "rev": bool(q.get("rev")),
            "calm": bool(calm), "text": "; ".join(parts)}


def plan(rows: list[dict], model: dict, quality: dict | None = None, profiles: dict | None = None) -> dict:
    """The model's rebalancing plan on today's prices. rows = assets(); model = web/altseason.json; quality = web/quality.json;
    profiles = web/coin_profiles.json. Every number here comes from those inputs — nothing is estimated."""
    quality, profiles = quality or {}, profiles or {}
    tgt = {k: float(v) for k, v in (model.get("targets_pct") or {}).items()}
    fut, earn = tgt.pop(FUT_KEY, 0.0), tgt.pop(EARN_KEY, 0.0)
    cash_t = fut + earn
    basket = set(model.get("basket") or [])
    g = model.get("gauge") or {}
    qrows = {str(r.get("symbol")).upper(): r for r in (quality.get("rows") or [])}
    qbasket = set(quality.get("basket") or [])
    uni = quality.get("universe")
    ev = quality.get("evidence") or {}
    prof = profiles.get("coins") or {}
    T = sum(r["usd"] for r in rows)
    held = {r["ccy"]: r for r in rows}
    cash_now = sum(r["usd"] for r in rows if r["cash"])

    def why(c: str, t: float) -> str:
        p = prof.get(c) or {}
        about = f" {p['name']} — {p['sector']}." if p.get("name") and p.get("sector") else ""
        if c == "BTC":
            x = ev.get("btc_regime_x")
            return (f"Ядро модели — {_p(t)}% портфеля.{about} Режим рынка сейчас «{'рост' if g.get('bull') else 'осторожно'}»."
                    + (f" На проверке 2022–2026 биткоин с учётом режима рынка дал ×{x}." if x else ""))
        if c in ("ETH", "SOL", "LINK", "NEAR") and t:
            return f"Ядро модели — {_p(t)}% портфеля.{about}" + (" Плюс доля альт-части." if c in basket else "")
        if c in basket and t:
            return f"Альт-часть модели — {_p(t)}%: одна из 10 самых торгуемых альт за полгода.{about}"
        if c in qbasket:
            return f"В модели нет, но монета в 10 лучших по анкете качества.{about}"
        return f"В модели CRYPTO NOVA этой монеты нет.{about}"

    out, buy, trim = [], [], []
    coins = sorted(set(tgt) | {c for c, r in held.items() if not r["cash"]})
    for c in coins:
        t = tgt.get(c, 0.0)
        now = held[c]["usd"] if c in held else 0.0
        now_pct = now / T * 100 if T else 0.0
        gap = t / 100 * T - now
        q = quality_facts(qrows.get(c), uni)
        r = {"ccy": c, "now_usd": round(now, 2), "now_pct": round(now_pct, 2), "target_pct": t, "gap_usd": round(gap, 2),
             "why": why(c, t), "quality": q, "core": c in ("BTC", "ETH", "SOL", "LINK", "NEAR") and t > 0,
             "basket": c in basket, "held": c in held}
        if abs(now_pct - t) < BAND_PP or abs(gap) < MIN_ORDER:
            r["act"] = "hold"
        elif gap > 0:
            r["act"] = "buy"
        elif t > 0:
            r["act"] = "trim"
        elif c in qbasket and q and q["calm"]:
            r["act"] = "keep"          # not in the model, but a top-10 quality alt that is not overheated: the owner may keep it
        else:
            r["act"] = "sell"
        if r["act"] == "buy":
            if q and q["calm"]:
                r["tag"] = "не перегрета"
            elif q and q.get("m3") is not None and q["m3"] > 0:
                r["tag"] = f"за 3 мес. обогнала BTC на {q['m3'] * 100:.0f}% — докупать частями"
            elif q:
                r["tag"] = "объём торгов растёт — докупать частями"
            elif r["core"]:
                r["tag"] = "ядро"
            else:
                r["tag"] = "оценки анкеты нет"
            buy.append(r)
        elif r["act"] in ("trim", "sell"):
            trim.append(r)
        out.append(r)
    # undervalued first: a calm alt (did not outrun BTC, volume not inflated) → the core → the rest; bigger gap first inside
    rank = lambda r: (0 if (r["quality"] and r["quality"]["calm"]) else 1 if r["core"] else 2, -r["gap_usd"])  # noqa: E731
    buy.sort(key=rank)
    trim.sort(key=lambda r: r["gap_usd"])
    cash = {"now_usd": round(cash_now, 2), "now_pct": round(cash_now / T * 100, 2) if T else 0.0, "target_pct": cash_t,
            "gap_usd": round(cash_t / 100 * T - cash_now, 2),
            "why": (f"Доллары: {_p(earn)}% модели — в USDT Earn под процент, {_p(fut)}% — резерв под фьючерсы по сигналам "
                    "(фьючерсы пока в тесте, эту часть можно держать в USDT).")}
    ideas = []
    for qr in quality.get("rows") or []:
        c = str(qr.get("symbol")).upper()
        f = quality_facts(qr, uni)
        if c in held or c in tgt or not f or not f["calm"] or not f["rev"]:
            continue
        ideas.append({"ccy": c, "quality": f})
        if len(ideas) >= 5:
            break
    br = g.get("breadth_pct")
    alt_note = (f"Сейчас рынок в режиме «{'рост' if g.get('bull') else 'осторожно'}», датчик альтсезона {'включён' if g.get('on') else 'выключен'}"
                + (f" (биткоин обгоняют {br}% альтов)." if br is not None else ".") + (" Правило модели: " + model["rules_ru"] if model.get("rules_ru") else ""))
    return {"total_usd": round(T, 2), "rows": out, "buy": buy, "trim": trim, "cash": cash, "ideas": ideas, "alt_note": alt_note,
            "regime": "рост" if g.get("bull") else "осторожно", "altseason": bool(g.get("on")),
            "model_asof": model.get("asof"), "quality_asof": quality.get("asof"), "note": NOTE}


def fetch_model(get=None) -> tuple[dict, dict, dict]:
    """The public model files (the same ones the panel reads). get(name) → dict; default: raw.githubusercontent."""
    def _get(name):
        req = urllib.request.Request(MODEL_SRC + name + f"?t={int(time.time() // 300)}", headers={"User-Agent": "cnq-myspot/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    get = get or _get
    out = []
    for name in ("altseason.json", "quality.json", "coin_profiles.json"):
        try:
            out.append(get(name) or {})
        except Exception:  # noqa: BLE001
            out.append({})
    return out[0], out[1], out[2]


# ── publish ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def publish_myspot(blob: dict, token: str, repo: str = DEFAULT_REPO, http=None) -> bool:
    """PUT web/myspot.enc.json (contents API, retry on a version conflict). Only the encrypted blob is accepted."""
    if not token:
        return False
    if not isinstance(blob, dict) or not {"salt", "nonce", "ct", "tag"} <= set(blob) or set(blob) - ivan_watch.BLOB_KEYS:
        raise ValueError("myspot: refusing to publish a payload that is not the encrypted blob")
    url = f"https://api.github.com/repos/{repo}/contents/{PATH}"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-myspot"}
    content = base64.b64encode(json.dumps(blob, separators=(",", ":")).encode()).decode()
    return ivan_watch.put_contents(url, hdr, content, "myspot: update", http=http)


# ── entry points ─────────────────────────────────────────────────────────────────────────────────────────────────────
def snapshot(fetch, state: dict, now: int, model=None) -> dict:
    host, bal = probe(fetch, state.get("host"))
    state["host"] = host
    raw = read_raw(fetch, host, bal)
    rows = assets(raw)
    alt, qual, prof = model if model is not None else fetch_model()
    p = plan(rows, alt, qual, prof)
    hist = [h for h in state.get("hist") or [] if isinstance(h, list) and len(h) == 2]
    day = now - now % 86_400_000
    if not hist or hist[-1][0] < day:
        hist.append([now, p["total_usd"]])
    else:
        hist[-1] = [now, p["total_usd"]]
    state["hist"] = hist[-MAX_HIST:]
    out = {"asof": now, "host": host.replace("https://", ""), "perm": raw.get("perm") or "", "assets": rows, "plan": p,
           "hist": state["hist"]}
    try:                       # «Автопилот OKX» state for the card (local file on this Mac; no keys, no network) — okx_autopilot.py
        from . import okx_autopilot
        ap = okx_autopilot.status()
        if ap:
            out["autopilot"] = ap
    except Exception:  # noqa: BLE001
        pass
    return out


def run(state: dict | None = None, now: int | None = None, fetch=None, put=None, env=None, save=None, model=None):
    """read → plan → encrypt → publish. Returns the plaintext dict (None when MYSPOT_* env is missing)."""
    c = creds(env)
    if not c:
        return None
    key, secret, passphrase, pw = c
    env = os.environ if env is None else env
    now = int(now or time.time() * 1000)
    if state is None:
        state = load_state(env)
        save = save or (lambda s: save_state(s, env))
    out = snapshot(fetch or make_fetch(key, secret, passphrase), state, now, model)
    if save:
        save(state)
    blob = ivan_watch.encrypt(out, pw, asof=now)
    if put is None:
        tok, repo = env.get("CNQ_GH_TOKEN", ""), env.get("CNQ_GH_REPO") or DEFAULT_REPO
        put = (lambda b: publish_myspot(b, tok, repo)) if tok else None
    if put is not None and not put(blob):
        raise RuntimeError("GitHub не принял web/myspot.enc.json")
    return out


def describe_error(e: Exception) -> str:
    """One short Russian line for the mirror's terminal."""
    if isinstance(e, OkxError):
        known = {"50111": "ключ не найден — проверь строку MYSPOT_OKX_KEY (скопирован полностью?)",
                 "50119": "ключ не найден на OKX — проверь строку MYSPOT_OKX_KEY",
                 "50113": "подпись не сошлась — проверь MYSPOT_OKX_SECRET",
                 "50105": "неверная фраза ключа — проверь MYSPOT_OKX_PASSPHRASE (это фраза, заданная при создании ключа)",
                 "50101": "это ключ демо-торговли — нужен ключ основного счёта",
                 "50102": "часы этого Mac расходятся с OKX — включи автоматическое время в настройках",
                 "50110": "ключ привязан к другому IP — создай ключ без привязки к IP или добавь IP этого Mac",
                 "50114": "OKX не принял ключ — пересоздай ключ «только чтение»",
                 "-403": "OKX не пускает запросы из этой сети (HTTP 403)"}
        return f"Мой портфель OKX: {known.get(e.code) or f'OKX ответил {e.code} {e.msg}'}"[:220]
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403, 404):
            return f"Мой портфель OKX: GitHub не принял файл (HTTP {e.code}) — проверь CNQ_GH_TOKEN"
        if e.code in ivan_watch.CONFLICT:
            return "Мой портфель OKX: GitHub был занят другим обновлением — повторю в следующий раз, ничего делать не нужно"
        return f"Мой портфель OKX: GitHub ответил HTTP {e.code} — повторю в следующий раз"
    if isinstance(e, (urllib.error.URLError, TimeoutError, OSError)):
        return "Мой портфель OKX: нет связи с OKX/GitHub — повторю в следующий раз"
    return f"Мой портфель OKX: ошибка — {str(e)[:150]}"


def check(env=None, fetch=None, save: bool = True) -> str | None:
    """One read-only call for `mirror --check`. None when MYSPOT_* env is missing (the card is optional)."""
    c = creds(env)
    if not c:
        return None
    key, secret, passphrase, _ = c
    fetch = fetch or make_fetch(key, secret, passphrase)
    try:
        st = load_state(env)
        host, bal = probe(fetch, st.get("host"))
        if save and st.get("host") != host:
            st["host"] = host
            save_state(st, env)
        n = sum(1 for d in ((bal or [{}])[0].get("details") or []) if (_f(d.get("eqUsd")) or 0) >= DUST_USD)
        msg = f"Мой портфель OKX: ключ работает ({host.replace('https://', '')}), монет на торговом счёте: {n}"
        try:
            cfg = _call(fetch, host, "/api/v5/account/config")
            perm = (cfg[0].get("perm") if cfg else "") or ""
            if "trade" in perm or "withdraw" in perm:
                msg += " · внимание: у ключа есть право торговли или вывода — для этой карточки нужен ключ только с правом «Чтение»"
        except Exception:  # noqa: BLE001
            pass
        return msg
    except Exception as e:  # noqa: BLE001
        return describe_error(e)
