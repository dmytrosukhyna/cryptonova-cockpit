"""Meme desk — Hyperliquid meme perpetuals + Solana DEX memecoins (docs/research/memes.md, scripts/meme_research.py).

Every server run (~30 min):
  1. Hyperliquid meme perps (`metaAndAssetCtxs` + 1h candles for the last 22 days of each liquid meme): price, 1h/24h/5d
     change, 24h volume vs the 7-day average, open interest, funding, 24 h high break, distance from the 24 h high,
     20-day mean. Ranked with a transparent score (SCORE_HL).
  2. Solana memecoins: GeckoTerminal trending + new pools → DexScreener cross-check (price, liquidity, paid boosts,
     link) → public Solana RPC (mint / freeze authority, Token-2022 extensions) → RugCheck summary (third-party risks,
     top candidates only). Safety filters and a transparent score (SCORE_SOL). WATCHLIST ONLY — no paper fills: DEX prices
     are sampled every ~30 min from aggregators, real fills on thin pools slip a lot, and rugs/honeypots cannot be
     simulated honestly. We record the price when a coin first passes the filters and show the change since then.
  3. Paper account «Мем-портфель» ($10 000, separate from all other accounts) trading the HL meme perps by RULE (below):
     ≤ 10% of equity per position, ≤ 6 positions, hard stop, half off at the take-profit, time exit; costs 0.15% round trip
     (HL taker 0.045% per side + slippage) and the live funding rate, accrued every run.
Output: web/memes.json. Stdlib only; every network call is injectable (`hl_ctx=`, `hl_candles=`, `sol=`) for tests.
Honesty: the rule is an EXPERIMENT (see RULE_STATUS / research_summary); memes can lose 100%; Solana data is third-party."""
from __future__ import annotations

import json
import math
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# Hyperliquid perp names (k = ×1000 contract). Names not listed on HL are simply ignored; delisted ones stay for research.
MEME_COINS = [
    "DOGE", "kPEPE", "kBONK", "kSHIB", "kFLOKI", "WIF", "POPCAT", "MEW", "BOME", "BRETT", "MOODENG", "PNUT", "GOAT",
    "CHILLGUY", "FARTCOIN", "SPX", "PENGU", "TRUMP", "MELANIA", "PURR", "MYRO", "NEIROETH", "kNEIRO", "NEIRO", "TURBO",
    "MOG", "kMOG", "PEOPLE", "TST", "ACT", "MEME", "DOGS", "kDOGS", "GRIFFAIN", "ZEREBRO", "AI16Z", "VINE", "JELLY",
    "YZY", "FWOG", "GIGA", "MICHI", "PONKE", "RETARDIO", "SIGMA", "LOCKIN", "MANEKI", "SLERF", "WEN", "HPOS", "TOSHI",
    "DEGEN", "MOTHER", "APU", "USELESS", "HOSICO", "LAUNCHCOIN", "kSATS", "SATS", "kLADYS", "BABYDOGE", "kBABYDOGE",
    "PEPE", "BONK", "SHIB", "FLOKI", "CAT", "kCAT", "SUNDOG", "MAGA", "BODEN", "TREMP", "SNEK", "PUPS", "CASHCAT", "NOT", "PUMP",
]
MEME_COINS = list(dict.fromkeys(MEME_COINS))

HL = "https://api.hyperliquid.xyz/info"
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
DS = "https://api.dexscreener.com"
RPC = "https://api.mainnet-beta.solana.com"
RUG = "https://api.rugcheck.xyz/v1/tokens/{}/report/summary"
UA = "Mozilla/5.0 cryptonova-memes/1.0"
H, DAY = 3_600_000, 86_400_000
TIMEOUT = 10

# ── the paper account ──────────────────────────────────────────────────────────────────────────────────────────────────
NAME = "Мем-портфель"
START_EQUITY = 10_000.0
SIZE, MAX_POS = 0.10, 6
COST = 0.0015                                # round trip; half charged on entry, half on every exit part
STOP, TP, TP_FRAC = -0.25, 0.50, 0.5
HOLD_H = 72                                  # time exit
MIN_VOL24 = 1_000_000                        # HL meme must trade ≥ $1M a day to be traded/ranked
BREAK_K = 3.0                                # breakout bar volume ≥ 3 × median 4h volume of the previous 7 days
RULE_STATUS = "эксперимент"
RULE = "pending"                             # set from the research (docs/research/memes.md)
RULES_TEXT = "Правило ещё не выбрано: идёт исследование. Сделок нет."

# ── Solana filters ─────────────────────────────────────────────────────────────────────────────────────────────────────
SOL_MIN_LIQ, SOL_MIN_VOL, SOL_MIN_AGE_H = 100_000, 500_000, 24
SOL_MAX_FDV_LIQ = 100
NOT_MEME = {"SOL", "WSOL", "USDC", "USDT", "USDS", "PYUSD", "USDG", "USD1", "FDUSD", "EURC", "JUP", "JTO", "PYTH", "RAY", "ORCA",
            "MSOL", "JITOSOL", "BSOL", "JUPSOL", "INF", "JLP", "CBBTC", "WBTC", "BTC", "ETH", "WETH", "TBTC", "ZBTC", "HNT",
            "RENDER", "W", "KMNO", "DRIFT", "MET", "SUSDE", "USDE", "XAUT", "PAXG", "TRX", "XRP", "DOGE", "PUMP"}
RUG_RU = {"mutable metadata": "метаданные можно менять", "top 10 holders high ownership": "топ-10 кошельков держат большую долю",
          "single holder ownership": "один кошелёк держит большую долю", "low amount of lp providers": "мало поставщиков ликвидности",
          "large amount of lp unlocked": "большая часть ликвидности не заблокирована", "freeze authority still enabled": "могут заморозить кошельки",
          "mint authority still enabled": "могут допечатать токены", "low liquidity": "мало ликвидности", "copycat token": "копия известного токена",
          "creator history of rugged tokens": "у создателя были «раги»", "high holder concentration": "высокая концентрация держателей"}

WARNINGS = [
    "Мем-монеты — самый рискованный рынок: цена может упасть на 50–90% за день, а токен на DEX — стать нулём (раг, ханипот).",
    "Только маленькие суммы, которые не жалко потерять полностью. Можно потерять 100%.",
    "Мем-портфель — учебный счёт на $10 000 без реальных денег. Правило — эксперимент: устойчивого преимущества история не доказала.",
    "Solana-токены — только список наблюдения с сигналами. Мы их не покупаем даже на бумаге: цены с DEX-агрегаторов раз в 30 минут "
    "не дают честно посчитать вход и выход. Проверки безопасности (авторитеты, RugCheck) снижают риск, но не убирают его.",
]

RESEARCH_SUMMARY = {"status": "в работе", "text": "Исследование ещё не завершено.", "doc": "docs/research/memes.md"}

SCORE_HL = ("балл 0–100 = 35 × место по росту за 5 дней + 20 × место по росту за 24 ч + 20 × место по объёму к среднему за 7 дней "
            "+ 15 × (цена выше 20-дневной средней) + 10 × место по открытому интересу; «место» — доля мемов, которых монета обгоняет (0…1). "
            "Мемы с объёмом < $1M в сутки не ранжируются.")
SCORE_SOL = ("балл 0–100 = импульс (0–30: рост за 6 ч, полный балл от +50%; ×0.5 если за сутки уже > +300% или за час < −15%) "
             "+ рост объёма (0–25: объём последнего часа ×24 к объёму за сутки, полный балл при ×2) "
             "+ ликвидность (0–25: логарифм, $100k = 0, $5M = 25) + возраст пула (0–20: полный балл с 7 дней) "
             "− 20 за каждый красный флаг − 5 за каждый жёлтый. «Прошёл фильтры» = ликвидность ≥ $100k, объём ≥ $500k за сутки, "
             "пулу ≥ 24 ч, нет красных флагов.")


# ── network ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _req(url: str, body=None, timeout: float = TIMEOUT, headers: dict | None = None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=timeout) as r:
        return json.loads(r.read().decode())


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception:  # noqa: BLE001
        return None


def _f(x, default=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def hl_ctx() -> dict:
    """{coin: {px, prev, vol24, oi_usd, funding_h}} for meme perps currently listed on HL; {} on failure."""
    meta, ctxs = _req(HL, {"type": "metaAndAssetCtxs"}, timeout=15)
    out = {}
    for u, c in zip(meta.get("universe", []), ctxs):
        name = u.get("name")
        if name not in MEME_COINS or u.get("isDelisted"):
            continue
        px = _f(c.get("markPx")) or _f(c.get("midPx"))
        if not px:
            continue
        out[name] = {"px": px, "prev": _f(c.get("prevDayPx")), "vol24": _f(c.get("dayNtlVlm"), 0.0),
                     "oi_usd": (_f(c.get("openInterest"), 0.0)) * px, "funding_h": _f(c.get("funding"), 0.0)}
    return out


def hl_candles(coin: str, start: int, end: int) -> list[list[float]]:
    """1h candles [t, o, h, l, c, v_usd] (closed and the forming one)."""
    r = _req(HL, {"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h", "startTime": start, "endTime": end}})
    out = []
    for x in r or []:
        c = _f(x.get("c"))
        if c:
            out.append([int(x["t"]), _f(x["o"]), _f(x["h"]), _f(x["l"]), c, (_f(x.get("v"), 0.0)) * c])
    return sorted(out)


# ── HL metrics ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def to_4h(c1: list[list[float]], now: int) -> list[list[float]]:
    """Aggregate 1h candles into UTC-aligned CLOSED 4h bars [t, o, h, l, c, v]."""
    bars: dict[int, list] = {}
    for t, o, h, lo, c, v in c1:
        k = t - t % (4 * H)
        b = bars.get(k)
        if b is None:
            bars[k] = [k, o, h, lo, c, v, 1]
        else:
            b[2] = max(b[2], h); b[3] = min(b[3], lo); b[4] = c; b[5] += v; b[6] += 1
    return [b[:6] for k, b in sorted(bars.items()) if k + 4 * H <= now and b[6] >= 3]


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return None if n == 0 else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def metrics(coin: str, ctx: dict, c1: list[list[float]], now: int) -> dict:
    px = ctx["px"]
    row = {"coin": coin, "px": px, "vol24": round(ctx["vol24"]), "oi_usd": round(ctx["oi_usd"]),
           "funding_day_pct": round(ctx["funding_h"] * 24 * 100, 4),
           "chg24h": round((px / ctx["prev"] - 1) * 100, 2) if ctx.get("prev") else None}
    closed = [c for c in c1 if c[0] + H <= now]
    if closed:
        def back(hours):
            t0 = now - hours * H
            prior = [c for c in closed if c[0] <= t0]
            return prior[-1][4] if prior else None
        p1, p5 = back(1), back(120)
        row["chg1h"] = round((px / p1 - 1) * 100, 2) if p1 else None
        row["chg5d"] = round((px / p5 - 1) * 100, 2) if p5 else None
        day = [c for c in closed if c[0] >= now - DAY]
        hi24 = max((c[2] for c in day), default=None)
        row["from_high24_pct"] = round((px / hi24 - 1) * 100, 2) if hi24 else None
        last20 = [c[4] for c in closed if c[0] >= now - 20 * DAY]
        if len(last20) >= 20 * 24 * 0.9:
            row["ma20"] = sum(last20) / len(last20)
            row["above_ma20"] = px > row["ma20"]
        week = [c[5] for c in closed if c[0] >= now - 7 * DAY]
        if len(week) >= 24 * 6:
            row["vol_ratio"] = round(sum(c[5] for c in day) / (sum(week) / 7), 2) if sum(week) > 0 else None
    b4 = to_4h(c1, now)
    if len(b4) >= 43:
        last, prev6, prev42 = b4[-1], b4[-7:-1], b4[-43:-1]
        med = _median([b[5] for b in prev42])
        row["break_bar"] = last[0]
        row["break_vol_x"] = round(last[5] / med, 2) if med else None
        row["breakout"] = bool(med and last[4] > max(b[2] for b in prev6) and last[5] >= BREAK_K * med)
    return row


def _rank(rows, key):
    vals = [(r.get(key), i) for i, r in enumerate(rows) if r.get(key) is not None]
    vals.sort()
    out = {}
    for k, (_, i) in enumerate(vals):
        out[i] = k / max(1, len(vals) - 1)
    return out


def score_hl(rows: list[dict]) -> None:
    liq = [r for r in rows if r["vol24"] >= MIN_VOL24]
    r5, r24, rv, roi = _rank(liq, "chg5d"), _rank(liq, "chg24h"), _rank(liq, "vol_ratio"), _rank(liq, "oi_usd")
    for i, r in enumerate(liq):
        parts = {"рост 5д": 35 * r5.get(i, 0), "рост 24ч": 20 * r24.get(i, 0), "объём к 7д": 20 * rv.get(i, 0),
                 "выше 20-дн. средней": 15 if r.get("above_ma20") else 0, "открытый интерес": 10 * roi.get(i, 0)}
        r["score"] = round(sum(parts.values()))
        r["score_parts"] = {k: round(v, 1) for k, v in parts.items()}
    for r in rows:
        r.setdefault("score", None)


def signal_text(r: dict) -> str:
    if r["vol24"] < MIN_VOL24:
        return "мало объёма — не торгуем"
    if r.get("breakout"):
        return f"пробой 24-часового максимума на объёме ×{r.get('break_vol_x')}"
    f = r.get("from_high24_pct")
    if f is not None and f <= -20:
        return f"обвал {f:.0f}% от максимума за сутки — высокий риск"
    if r.get("above_ma20") is False:
        return "ниже 20-дневной средней — слабость"
    if (r.get("chg24h") or 0) >= 20:
        return "сильный рост за сутки — не догоняем"
    return "ждём"


# ── Solana ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _gt_pools(get, path: str) -> list[dict]:
    j = get(f"{GT}/{path}", headers={"Accept": "application/json;version=20230302"})
    toks = {x.get("id"): (x.get("attributes") or {}) for x in (j.get("included") or []) if x.get("type") == "token"}
    out = []
    for p in j.get("data") or []:
        a = p.get("attributes") or {}
        rel = p.get("relationships") or {}
        bid = ((rel.get("base_token") or {}).get("data") or {}).get("id") or ""
        if not bid.startswith("solana_"):
            continue
        mint = bid[len("solana_"):]
        name = a.get("name") or ""
        sym = (toks.get(bid, {}).get("symbol") or name.split(" / ")[0]).strip()
        pc, tx, vol = a.get("price_change_percentage") or {}, a.get("transactions") or {}, a.get("volume_usd") or {}
        created = a.get("pool_created_at")
        age_h = None
        if created:
            try:
                from datetime import datetime
                age_h = (time.time() - datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()) / 3600
            except ValueError:
                pass
        out.append({"symbol": sym, "name": toks.get(bid, {}).get("name") or sym, "mint": mint, "pool": a.get("address"),
                    "dex": ((rel.get("dex") or {}).get("data") or {}).get("id"), "price": _f(a.get("base_token_price_usd")),
                    "chg1h": _f(pc.get("h1")), "chg6h": _f(pc.get("h6")), "chg24h": _f(pc.get("h24")),
                    "vol1h": _f(vol.get("h1")), "vol6h": _f(vol.get("h6")), "vol24": _f(vol.get("h24")),
                    "liquidity": _f(a.get("reserve_in_usd")), "fdv": _f(a.get("fdv_usd")), "mcap": _f(a.get("market_cap_usd")),
                    "age_h": round(age_h, 1) if age_h is not None else None,
                    "buys24": (tx.get("h24") or {}).get("buys"), "sells24": (tx.get("h24") or {}).get("sells"),
                    "buyers24": (tx.get("h24") or {}).get("buyers"), "sellers24": (tx.get("h24") or {}).get("sellers"),
                    "source": path.split("?")[0]})
    return out


def sol_fetch(get=None, post=None, top_rug: int = 10) -> dict:
    """Solana candidates with metrics from all sources; {'rows': [...], 'sources': {name: bool}}."""
    get = get or (lambda url, headers=None: _req(url, headers=headers))
    post = post or (lambda url, body: _req(url, body))
    src = {}
    pools = []
    for path in ("trending_pools?include=base_token,dex&page=1", "trending_pools?include=base_token,dex&page=2", "new_pools?include=base_token,dex&page=1"):
        r = _safe(_gt_pools, get, path)
        src["geckoterminal"] = src.get("geckoterminal", False) or r is not None
        pools += r or []
    best: dict[str, dict] = {}
    for p in pools:
        if p["symbol"].upper() in NOT_MEME:
            continue
        q = best.get(p["mint"])
        if q is None or (p["liquidity"] or 0) > (q["liquidity"] or 0):
            if q is not None and q["source"] != p["source"]:
                p["source"] = "trending_pools" if "trending_pools" in (p["source"], q["source"]) else p["source"]
            best[p["mint"]] = p
    rows = list(best.values())
    mints = [r["mint"] for r in rows]
    # DexScreener: cross-check + link + paid boosts
    ds = {}
    ok_ds = False
    for i in range(0, len(mints), 30):
        j = _safe(get, f"{DS}/tokens/v1/solana/" + ",".join(mints[i:i + 30]))
        if isinstance(j, list):
            ok_ds = True
            for pr in j:
                m = ((pr.get("baseToken") or {}).get("address"))
                if m and ((pr.get("liquidity") or {}).get("usd") or 0) >= ((ds.get(m) or {}).get("liquidity") or {}).get("usd", -1):
                    ds[m] = pr
    src["dexscreener"] = ok_ds
    for r in rows:
        pr = ds.get(r["mint"])
        r["link"] = (pr or {}).get("url") or f"https://dexscreener.com/solana/{r['mint']}"
        if pr:
            r["ds_price"] = _f(pr.get("priceUsd"))
            r["ds_liquidity"] = _f((pr.get("liquidity") or {}).get("usd"))
            r["boosted"] = bool((pr.get("boosts") or {}).get("active"))
            info = pr.get("info") or {}
            r["socials"] = bool(info.get("websites") or info.get("socials"))
            if pr.get("pairCreatedAt") and r.get("age_h") is None:
                r["age_h"] = round((time.time() * 1000 - pr["pairCreatedAt"]) / H, 1)
    # Solana RPC: mint / freeze authority, Token-2022 extensions (one call per 100 mints)
    auth = {}
    ok_rpc = False
    for i in range(0, len(mints), 100):
        j = _safe(post, RPC, {"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts", "params": [mints[i:i + 100], {"encoding": "jsonParsed"}]})
        vals = ((j or {}).get("result") or {}).get("value") if isinstance(j, dict) else None
        if isinstance(vals, list):
            ok_rpc = True
            for m, v in zip(mints[i:i + 100], vals):
                info = ((((v or {}).get("data") or {}).get("parsed") or {}).get("info")) if isinstance((v or {}).get("data"), dict) else None
                if info is not None:
                    auth[m] = {"mint": info.get("mintAuthority"), "freeze": info.get("freezeAuthority"),
                               "ext": [e.get("extension") for e in info.get("extensions") or [] if isinstance(e, dict)],
                               "fee": next((e.get("state") for e in info.get("extensions") or [] if isinstance(e, dict) and e.get("extension") == "transferFeeConfig"), None)}
    src["solana_rpc"] = ok_rpc
    for r in rows:
        if r["mint"] in auth:
            r["auth"] = auth[r["mint"]]
    # RugCheck for the strongest pre-filter candidates only (third-party, rate-limited)
    pre = sorted([r for r in rows if (r["liquidity"] or 0) >= SOL_MIN_LIQ and (r["vol24"] or 0) >= SOL_MIN_VOL],
                 key=lambda r: -(r["vol24"] or 0))[:top_rug]
    ok_rug = False
    if pre:
        with ThreadPoolExecutor(max_workers=5) as ex:
            res = list(ex.map(lambda r: _safe(get, RUG.format(r["mint"])), pre))
        for r, j in zip(pre, res):
            if isinstance(j, dict) and ("risks" in j or "score" in j):
                ok_rug = True
                r["rug"] = {"score": j.get("score_normalised", j.get("score")),
                            "risks": [{"name": x.get("name"), "level": x.get("level")} for x in j.get("risks") or [] if isinstance(x, dict)][:8]}
    src["rugcheck"] = ok_rug
    return {"rows": rows, "sources": src}


def sol_flags(r: dict) -> list[dict]:
    fl = []

    def add(level, text):
        fl.append({"level": level, "text": text})
    liq, vol, age, fdv = r.get("liquidity") or 0, r.get("vol24") or 0, r.get("age_h"), r.get("fdv")
    if liq < SOL_MIN_LIQ:
        add("red", f"мало ликвидности (${liq / 1e3:.0f}k < $100k)")
    if vol < SOL_MIN_VOL:
        add("yellow", f"мало объёма (${vol / 1e3:.0f}k за сутки < $500k)")
    if age is None:
        add("yellow", "возраст пула неизвестен")
    elif age < SOL_MIN_AGE_H:
        add("red", f"новый — высокий риск (пулу {age:.0f} ч)")
    if fdv and liq and fdv / liq > SOL_MAX_FDV_LIQ:
        add("yellow", f"тонкая ликвидность для такой оценки (FDV в {fdv / liq:.0f} раз больше пула)")
    c24, c1 = r.get("chg24h"), r.get("chg1h")
    if c24 is not None and c24 > 1000:
        add("yellow", f"уже улетел (+{c24:.0f}% за сутки) — погоня опасна")
    if c24 is not None and c24 < -50:
        add("yellow", f"обвал {c24:.0f}% за сутки")
    if c1 is not None and c1 < -25:
        add("yellow", f"резкое падение за час ({c1:.0f}%)")
    if liq and vol / liq > 30:
        add("yellow", f"оборот в {vol / liq:.0f} раз больше пула — возможен накрут объёма")
    b, s = r.get("buys24"), r.get("sells24")
    if isinstance(b, int) and isinstance(s, int) and b >= 50 and s == 0:
        add("red", "никто не продаёт — возможно, продать нельзя (ханипот)")
    a = r.get("auth")
    if a is None:
        add("info", "авторитеты токена не проверены (RPC не ответил)")
    else:
        if a.get("mint"):
            add("red", "могут допечатать токены (mint authority не отозван)")
        if a.get("freeze"):
            add("red", "могут заморозить кошельки (freeze authority не отозван)")
        ext = set(a.get("ext") or [])
        if "permanentDelegate" in ext:
            add("red", "Token-2022: постоянный делегат может забрать токены")
        if "transferHook" in ext:
            add("yellow", "Token-2022: при переводе вызывается чужая программа")
        fee = a.get("fee") or {}
        bps = None
        try:
            bps = int(((fee.get("newerTransferFee") or fee.get("olderTransferFee") or {}).get("transferFeeBasisPoints")))
        except (TypeError, ValueError):
            pass
        if bps:
            add("red" if bps >= 500 else "yellow", f"комиссия при переводе {bps / 100:.1f}%")
    if r.get("ds_price") and r.get("price"):
        d = abs(r["ds_price"] / r["price"] - 1)
        if d > 0.05:
            add("yellow", f"цены источников расходятся на {d * 100:.0f}%")
    if r.get("boosted"):
        add("yellow", "платная реклама на DexScreener (boost) — не сигнал качества")
    if r.get("socials") is False:
        add("yellow", "нет сайта и соцсетей")
    for x in (r.get("rug") or {}).get("risks") or []:
        lv = (x.get("level") or "").lower()
        if lv in ("danger", "warn"):
            nm = (x.get("name") or "").strip()
            add("red" if lv == "danger" else "yellow", "RugCheck: " + RUG_RU.get(nm.lower(), nm))
    return fl


def score_sol(r: dict) -> None:
    c6, c24, c1 = r.get("chg6h") or 0, r.get("chg24h") or 0, r.get("chg1h") or 0
    mom = 30 * min(1, max(0, c6 / 50))
    if c24 > 300 or c1 < -15:
        mom *= 0.5
    acc = (r["vol1h"] * 24 / r["vol24"]) if r.get("vol1h") and r.get("vol24") else None
    volg = 25 * min(1, max(0, (acc - 0.5) / 1.5)) if acc is not None else 0
    liq = r.get("liquidity") or 0
    lq = 25 * min(1, max(0, math.log10(liq / 1e5) / math.log10(50))) if liq > 0 else 0
    age = 20 * min(1, max(0, (r.get("age_h") or 0) / 168))
    red = sum(1 for f in r["flags"] if f["level"] == "red")
    yel = sum(1 for f in r["flags"] if f["level"] == "yellow")
    r["score_parts"] = {"импульс": round(mom, 1), "рост объёма": round(volg, 1), "ликвидность": round(lq, 1), "возраст": round(age, 1),
                        "штраф": -(20 * red + 5 * yel)}
    r["score"] = int(max(0, min(100, round(mom + volg + lq + age - 20 * red - 5 * yel))))
    r["passed"] = (liq >= SOL_MIN_LIQ and (r.get("vol24") or 0) >= SOL_MIN_VOL and (r.get("age_h") or 0) >= SOL_MIN_AGE_H and red == 0)
    r["red_flags"], r["yellow_flags"] = red, yel


# ── storage ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meme_pos(coin TEXT PRIMARY KEY, entry_px REAL, qty REAL, qty0 REAL, cost_in REAL, cash_out REAL,
                                            entry_time INTEGER, tp_done INTEGER, funding REAL, last_bar INTEGER, why TEXT);
        CREATE TABLE IF NOT EXISTS meme_closed(coin TEXT, entry_time INTEGER, exit_time INTEGER, entry_px REAL, exit_px REAL,
                                               pnl_usd REAL, pnl_pct REAL, why TEXT);
        CREATE TABLE IF NOT EXISTS meme_events(time INTEGER, text TEXT);
        CREATE TABLE IF NOT EXISTS meme_equity(time INTEGER PRIMARY KEY, equity REAL);
        CREATE TABLE IF NOT EXISTS meme_meta(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS meme_sol_watch(mint TEXT PRIMARY KEY, symbol TEXT, first_seen INTEGER, first_px REAL,
                                                  first_score INTEGER, last_seen INTEGER, last_px REAL, max_px REAL, min_px REAL, link TEXT);""")


def _meta(conn, k, default=None):
    r = conn.execute("SELECT v FROM meme_meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def _set(conn, k, v) -> None:
    conn.execute("INSERT OR REPLACE INTO meme_meta VALUES(?,?)", (k, str(v)))


def _px(x: float) -> str:
    if x >= 100:
        return f"${x:,.2f}"
    if x >= 1:
        return f"${x:.4f}"
    return f"${x:.6g}"


def _usd(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}${abs(x):,.2f}"


# ── the account ────────────────────────────────────────────────────────────────────────────────────────────────────────

def entry_signals(rows: list[dict], held: set[str], conn, now: int) -> list[tuple[str, float, str]]:
    """[(coin, strength, reason)] — coins the RULE wants to buy now. Strongest first."""
    if RULE != "breakout":
        return []
    out = []
    for r in rows:
        if r["coin"] in held or r["vol24"] < MIN_VOL24 or not r.get("breakout"):
            continue
        bar = r["break_bar"]
        last = int(_meta(conn, f"last_bar:{r['coin']}", 0))
        if bar <= last:                       # this 4h bar was already used (entered or exited inside it)
            continue
        out.append((r["coin"], r.get("break_vol_x") or 0, f"пробой 24-часового максимума на объёме ×{r.get('break_vol_x')} к обычному"))
    return sorted(out, key=lambda x: -x[1])


def manage(conn, ctx: dict, candles: dict, now: int, ev: list) -> float:
    """Funding, stops, take-profits and time exits for open positions. Returns cash."""
    cash = float(_meta(conn, "cash", START_EQUITY))
    last_run = int(_meta(conn, "last_run", now))
    half = COST / 2
    for coin, epx, qty, qty0, cost_in, cash_out, et, tp_done, fund, last_bar, why in conn.execute("SELECT * FROM meme_pos").fetchall():
        c = ctx.get(coin)
        px = c["px"] if c else None
        if px is None:                          # coin vanished from HL (delisted / API gap): keep, say so once per day
            continue
        # funding: live hourly rate × hours since the previous run × position value (longs pay when positive)
        hrs = max(0.0, (now - max(last_run, et)) / H)
        fee = qty * px * c["funding_h"] * hrs
        cash -= fee; fund += fee; cost_in += fee
        # walk the closed 1h bars since the last check, then the current price
        path = [(b[0], b[1], b[2], b[3], b[4]) for b in candles.get(coin, []) if b[0] > last_bar and b[0] >= et and b[0] + H <= now]
        path.append((now, px, px, px, px))
        closed = None
        for t, o, h, lo, cl in path:
            stop_px = epx * (1 + STOP)
            if lo <= stop_px:
                fill = min(o, stop_px)
                v = qty * fill * (1 - half); cash += v; cash_out += v
                closed = (fill, "стоп −25%", t); break
            if not tp_done and h >= epx * (1 + TP):
                fill = max(o, epx * (1 + TP)); q = qty0 * TP_FRAC
                v = q * fill * (1 - half); cash += v; cash_out += v; qty -= q; tp_done = 1
                ev.append((now, f"Зафиксировали половину {coin} по {_px(fill)} (+{(fill / epx - 1) * 100:.0f}%): {_usd(v - cost_in * TP_FRAC)}"))
            if t < now:
                last_bar = t
        if closed is None and now - et >= HOLD_H * H:
            v = qty * px * (1 - half); cash += v; cash_out += v
            closed = (px, f"время вышло ({HOLD_H} ч)", now)
        if closed:
            fill, reason, t = closed
            pnl = cash_out - cost_in
            pct = pnl / cost_in * 100 if cost_in else 0
            conn.execute("INSERT INTO meme_closed VALUES(?,?,?,?,?,?,?,?)", (coin, et, now, epx, fill, round(pnl, 2), round(pct, 2), reason))
            conn.execute("DELETE FROM meme_pos WHERE coin=?", (coin,))
            # do not re-enter on the same 4h bar
            _set(conn, f"last_bar:{coin}", now - now % (4 * H))
            verb = "Стоп" if reason.startswith("стоп") else "Вышли"
            ev.append((now, f"{verb}: продали {coin} по {_px(fill)} — {reason}. Итог сделки {_usd(pnl)} ({pct:+.1f}%)"))
        else:
            conn.execute("UPDATE meme_pos SET qty=?, cost_in=?, cash_out=?, tp_done=?, funding=?, last_bar=? WHERE coin=?",
                         (qty, cost_in, cash_out, tp_done, fund, last_bar, coin))
    return cash


def run(conn: sqlite3.Connection, now: int | None = None, hl_ctx_fn=hl_ctx, hl_candles_fn=hl_candles, sol_fn=sol_fetch,
        deadline_s: float = 50.0) -> dict:
    t_start = time.time()
    now = now or int(time.time() * 1000)
    _ensure(conn)
    ev: list[tuple[int, str]] = []
    warnings = list(WARNINGS)
    sources = {}
    ctx = _safe(hl_ctx_fn) or {}
    sources["hyperliquid"] = bool(ctx)
    held = {r[0] for r in conn.execute("SELECT coin FROM meme_pos")}
    want = sorted({c for c, x in ctx.items() if x["vol24"] >= MIN_VOL24} | (held & set(ctx)), key=lambda c: -ctx[c]["vol24"])[:40]
    candles: dict[str, list] = {}
    if want:
        start = now - 22 * DAY
        with ThreadPoolExecutor(max_workers=6) as ex:
            res = list(ex.map(lambda c: (c, _safe(hl_candles_fn, c, start, now)), want))
        candles = {c: v for c, v in res if v}
    rows = [metrics(c, ctx[c], candles.get(c, []), now) for c in ctx]
    score_hl(rows)
    for r in rows:
        r["signal"] = signal_text(r)
        r["held"] = r["coin"] in held
        r.pop("ma20", None)
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), -r["vol24"]))

    if ctx:
        cash = manage(conn, ctx, candles, now, ev)
        held = {r[0] for r in conn.execute("SELECT coin FROM meme_pos")}
        equity = cash + sum(q * ctx[c]["px"] for c, q in conn.execute("SELECT coin, qty FROM meme_pos") if c in ctx)
        for coin, strength, reason in entry_signals(rows, held, conn, now):
            if len(held) >= MAX_POS:
                break
            px = ctx[coin]["px"]; notional = SIZE * equity
            cost_in = notional * (1 + COST / 2)
            if cost_in > cash:
                break
            cash -= cost_in
            conn.execute("INSERT INTO meme_pos VALUES(?,?,?,?,?,?,?,?,?,?,?)", (coin, px, notional / px, notional / px, cost_in, 0.0, now, 0, 0.0, now - now % H, reason))
            bar = next((r.get("break_bar") for r in rows if r["coin"] == coin), now)
            _set(conn, f"last_bar:{coin}", bar)
            held.add(coin)
            ev.append((now, f"Купили {coin} по {_px(px)} на ${notional:,.0f} ({SIZE * 100:.0f}% счёта): {reason}. Стоп {_px(px * (1 + STOP))}, "
                            f"половину продаём на {_px(px * (1 + TP))}, остальное — через {HOLD_H} ч"))
        _set(conn, "cash", cash)
        _set(conn, "last_run", now)
    else:
        warnings.insert(0, "Hyperliquid не ответил — цены мемов не обновлены, счёт без изменений.")
        last = (conn.execute("SELECT text FROM meme_events ORDER BY time DESC LIMIT 1").fetchone() or [""])[0]
        if not last.startswith("Hyperliquid не ответил"):
            ev.append((now, "Hyperliquid не ответил — счёт без изменений"))

    # Solana watchlist (time-boxed: skipped if HL already ate the budget)
    sol_rows, sol_note = [], None
    if time.time() - t_start < deadline_s - 25:
        s = _safe(sol_fn) or {"rows": [], "sources": {}}
        sources.update({k: v for k, v in s["sources"].items()})
        for r in s["rows"]:
            r["flags"] = sol_flags(r)
            score_sol(r)
        sol_rows = sorted(s["rows"], key=lambda r: (not r["passed"], -r["score"]))
        track_sol(conn, sol_rows, now)
        if not s["rows"]:
            sol_note = "Источники по Solana не ответили — список пуст."
    else:
        sol_note = "Пропущено: не хватило времени в этом запуске."
    for k in ("geckoterminal", "dexscreener", "solana_rpc", "rugcheck"):
        sources.setdefault(k, False)

    eq = account_equity(conn, ctx)
    conn.execute("INSERT OR REPLACE INTO meme_equity VALUES(?,?)", (now, round(eq, 4)))
    conn.executemany("INSERT INTO meme_events VALUES(?,?)", ev)
    conn.execute("DELETE FROM meme_events WHERE time < ?", (now - 60 * DAY,))
    conn.commit()
    out = snapshot(conn, now, ctx, candles, rows, sol_rows, warnings, sources)
    if sol_note:
        out["solana_note"] = sol_note + " " + out["solana_note"]
    out["seconds"] = round(time.time() - t_start, 1)
    return out


def account_equity(conn, ctx) -> float:
    cash = float(_meta(conn, "cash", START_EQUITY))
    tot = cash
    for coin, epx, qty in conn.execute("SELECT coin, entry_px, qty FROM meme_pos"):
        px = ctx.get(coin, {}).get("px") or epx
        tot += qty * px
    return tot


def track_sol(conn, rows: list[dict], now: int) -> None:
    """Remember the price when a token first passed the filters (score ≥ 50) — measures the signals without paper fills."""
    for r in rows:
        if not r.get("price"):
            continue
        ex = conn.execute("SELECT first_px, max_px, min_px FROM meme_sol_watch WHERE mint=?", (r["mint"],)).fetchone()
        if ex:
            conn.execute("UPDATE meme_sol_watch SET last_seen=?, last_px=?, max_px=?, min_px=? WHERE mint=?",
                         (now, r["price"], max(ex[1], r["price"]), min(ex[2], r["price"]), r["mint"]))
            r["since_signal_pct"] = round((r["price"] / ex[0] - 1) * 100, 1)
        elif r["passed"] and r["score"] >= 50:
            conn.execute("INSERT INTO meme_sol_watch VALUES(?,?,?,?,?,?,?,?,?,?)", (r["mint"], r["symbol"], now, r["price"], r["score"], now, r["price"], r["price"], r["price"], r.get("link")))
            r["since_signal_pct"] = 0.0
    conn.execute("DELETE FROM meme_sol_watch WHERE last_seen < ?", (now - 30 * DAY,))


def snapshot(conn, now, ctx, candles, rows, sol_rows, warnings, sources) -> dict:
    cash = float(_meta(conn, "cash", START_EQUITY))
    positions = []
    for coin, epx, qty, qty0, cost_in, cash_out, et, tp_done, fund, last_bar, why in conn.execute("SELECT * FROM meme_pos ORDER BY entry_time"):
        px = ctx.get(coin, {}).get("px") or epx
        val = qty * px
        pnl = cash_out + val * (1 - COST / 2) - cost_in
        positions.append({"coin": coin, "entry_px": epx, "px": px, "qty": round(qty, 6), "value": round(val, 2), "invested": round(cost_in, 2),
                          "pnl_usd": round(pnl, 2), "pnl_pct": round(pnl / cost_in * 100, 2) if cost_in else None, "entry_time": et,
                          "hours_held": round((now - et) / H, 1), "stop_px": epx * (1 + STOP), "tp_px": epx * (1 + TP), "tp_done": bool(tp_done),
                          "exit_by": et + HOLD_H * H, "funding_paid": round(fund, 2), "why": why, "price_live": coin in ctx})
    equity = cash + sum(p["value"] for p in positions)
    closed = [{"coin": c, "entry_time": a, "exit_time": b, "entry_px": e, "exit_px": x, "pnl_usd": p, "pnl_pct": q, "why": w}
              for c, a, b, e, x, p, q, w in conn.execute("SELECT * FROM meme_closed ORDER BY exit_time DESC LIMIT 50")]
    allc = conn.execute("SELECT pnl_usd FROM meme_closed").fetchall()
    eqh = conn.execute("SELECT time, equity FROM meme_equity ORDER BY time").fetchall()
    step = max(1, -(-len(eqh) // 500))
    hist = [[t, round(e, 2)] for t, e in eqh[::step]]
    if eqh and hist[-1][0] != eqh[-1][0]:
        hist = (hist + [[eqh[-1][0], round(eqh[-1][1], 2)]])[-500:]
    events = [{"time": t, "text": x} for t, x in conn.execute("SELECT time, text FROM meme_events ORDER BY time DESC, rowid DESC LIMIT 40")]
    watch = conn.execute("SELECT symbol, first_seen, first_px, last_px, max_px, min_px, link FROM meme_sol_watch WHERE first_seen <= ? ORDER BY first_seen DESC", (now - DAY,)).fetchall()
    chg = sorted((lp / fp - 1) * 100 for _, _, fp, lp, *_ in watch if fp)
    sol_stats = {"signals_24h_plus": len(chg), "median_change_pct": round(chg[len(chg) // 2], 1) if chg else None,
                 "share_up": round(sum(1 for x in chg if x > 0) / len(chg), 2) if chg else None}
    return {
        "asof": now, "name": NAME,
        "account": {"name": NAME, "start_equity": START_EQUITY, "equity": round(equity, 2), "cash": round(cash, 2),
                    "pnl_usd": round(equity - START_EQUITY, 2), "pnl_pct": round((equity / START_EQUITY - 1) * 100, 3),
                    "positions": positions, "closed": closed,
                    "stats": {"trades": len(allc), "wins": sum(1 for (p,) in allc if p > 0), "sum_pnl": round(sum(p for (p,) in allc), 2)},
                    "events": events, "history": hist, "rules": RULES_TEXT, "rule": RULE, "rule_status": RULE_STATUS,
                    "limits": {"size_pct": SIZE * 100, "max_positions": MAX_POS, "stop_pct": STOP * 100, "take_profit_pct": TP * 100,
                               "take_profit_part": TP_FRAC, "time_exit_h": HOLD_H, "cost_round_trip_pct": COST * 100}},
        "hl_memes": rows,
        "solana": sol_rows[:30],
        "solana_note": "Только наблюдение: бумажных сделок по Solana нет. «С сигнала» — изменение цены с момента, когда токен впервые прошёл фильтры с баллом ≥ 50.",
        "solana_signal_stats": sol_stats,
        "warnings": warnings,
        "research_summary": RESEARCH_SUMMARY,
        "sources": sources,
        "score_formula": {"hl": SCORE_HL, "solana": SCORE_SOL},
    }
