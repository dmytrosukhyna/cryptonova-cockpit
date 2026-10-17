"""Spot meme candidates — size, tokenomics, holder concentration, liquidity lock, age, listings and backers
(docs/research/memes.md, section «Спот-мемы»). Watch list for a spot meme portfolio; no trades.

Free sources only (all reachable from GitHub Actions; each cached in agents.sqlite so one run makes few calls):
  • CoinGecko `coins/markets?category=meme-token` (top 100 by market cap; every 2 h) — price, market cap, FDV, supply, volume;
  • CoinGecko `coins/{id}` (≤ 2 coins per run, kept 7 days) — categories, links, chain addresses, genesis date;
  • backers: CoinGecko «… Portfolio» categories of the coin (free: a16z, Binance Labs, DWF Labs, Coinbase Ventures … portfolios)
    and DefiLlama `raises` matched by project NAME — but DefiLlama answered HTTP 402 (paid) on 17.10.2026, so it is tried once a
    day and usually empty. Memes rarely have backers: «нет данных» is the normal answer, never a guess;
  • holders (Solana, ≤ 3 mints per run, kept 24 h): RugCheck full report — top holders with RugCheck's labels of known accounts
    (pools, lockers, exchanges), insider flag, total holders; pools/burn/locks/labelled exchanges are excluded from the top-10 share.
    Fallback — public Solana RPC (20 largest accounts → owners → owner programs = pool vaults of Raydium, Orca, Meteora, PumpSwap,
    pump.fun); the public RPC answered HTTP 429 for WIF on 17.10.2026, so the fallback often fails. Unlabelled exchange wallets
    stay in the top-10, so for big memes the share can be OVERSTATED;
  • RugCheck summary (Solana, ≤ 3 per run, kept 24 h) — share of liquidity locked/burned and risk list;
  • DexScreener `tokens/v1/solana` — deepest pool, oldest pool date (age), link; OKX spot instruments — CEX listing.
Score: see SCORE_SPOT. Missing data is shown as «нет данных» and the component is left out of the score (coverage shown).

Status for the panel (owner 17.10.2026: «понятный статус вместо простыни текста»): every row gets `status` ∈ {"avoid", "small",
"wait"}, `status_ru` and a ≤ 12-word `why_ru`; the run returns a one-line `summary_ru`. Rules, checked in this order:
  avoid «не брать»  — a red flag (fewer than 30% of tokens circulating, insiders ≥ 25%, top-10 wallets > 60%, RugCheck «danger»),
                      OR 24 h volume < $5M (STATUS_MIN_VOL), OR not on OKX spot / Hyperliquid perp / a Solana pool ≥ $1M,
                      OR quality score < 50 (STATUS_MIN_SCORE);
  small «можно на небольшую сумму» — passed all of the above AND the price is ≥ 30% below its 30-day high (DIP_30D): in our meme
                      research (docs/research/memes.md) only buying after a flush made money; momentum/breakout entries lost;
  wait  «ждём»      — passed the checks, no dip yet.
The 30-day high is a LOWER-BOUND estimate (so the dip is never overstated): the max of the current price, the prices 24 h / 7 d /
30 d ago implied by CoinGecko's % changes, our own daily price samples from earlier runs (`hi:SYM` in meme_spot_cache) and, for memes
listed on Hyperliquid, the daily highs of the 22 days of 1h candles the meme desk already loads (`hlhi:SYM`, used only when the HL
price is within 10% of the CoinGecko price — same asset). It is a filter of risk and timing, not a forecast."""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone

CG = "https://api.coingecko.com/api/v3"
LLAMA = "https://api.llama.fi/raises"
DS = "https://api.dexscreener.com"
RPC = "https://api.mainnet-beta.solana.com"
RUG = "https://api.rugcheck.xyz/v1/tokens/{}/report/summary"
OKX_SPOT = "https://www.okx.com/api/v5/public/instruments?instType=SPOT"
H, DAY = 3_600_000, 86_400_000
MIN_MCAP, MIN_VOL = 50_000_000, 1_000_000

TIER1 = {"a16z": "a16z", "andreessen": "a16z", "paradigm": "Paradigm", "polychain": "Polychain", "pantera": "Pantera",
         "dragonfly": "Dragonfly", "multicoin": "Multicoin", "jump": "Jump", "wintermute": "Wintermute", "gsr": "GSR", "dwf": "DWF",
         "binance labs": "Binance Labs", "yzi": "YZi Labs", "coinbase ventures": "Coinbase Ventures", "animoca": "Animoca",
         "delphi": "Delphi", "sequoia": "Sequoia", "galaxy": "Galaxy", "framework": "Framework", "electric capital": "Electric Capital",
         "hashed": "Hashed", "spartan": "Spartan", "okx ventures": "OKX Ventures", "blockchain capital": "Blockchain Capital",
         "placeholder": "Placeholder", "variant": "Variant", "amber": "Amber", "flow traders": "Flow Traders", "cumberland": "Cumberland"}
POOL_PROGRAMS = {
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium", "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM", "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM", "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora",
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG": "Meteora DAMM", "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN": "Meteora DBC",
    "24Uqj9JCLxUeoC3hGfh5W3s9FM9uCHDS2SG3LYwBpyTi": "Meteora vault", "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump.fun"}
POOL_AUTH = {"5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1": "Raydium", "GpMZbSM2GzbTVu6U9GVm6tw4MmQFYpDu1BhuMuXq6fXh": "Raydium CPMM"}
LOCK_PROGRAMS = {"strmRqUCoQUgGUan5YhzUZa6KqdzwX5L6FpUxfmKg5m": "Streamflow", "LocpQgucEQHbqNABEYvBvwoxCPsSbG91A1QaQhQQqjn": "Jupiter Lock"}
BURN = {"1nc1nerator11111111111111111111111111111111", "11111111111111111111111111111111"}
NOT_MEME = {"usdt", "usdc", "weth", "wbtc", "steth", "dai"}

SCORE_SPOT = ("балл 0–100 = 90 × (сумма доступных частей ÷ их максимум) + бонус за инвесторов. Части: размер и ликвидность (0–25: "
              "капитализация $50M → 0, $5B → 12.5; объём $1M → 0, $100M → 12.5; логарифм), токеномика (0–25: доля в обращении = "
              "капитализация ÷ FDV; 50% и меньше → 0, 100% → 25), распределение (0–20: топ-10 кошельков без пулов, сожжённых и "
              "заблокированных; 50% и больше → 0, 10% и меньше → 20; только Solana), возраст (0–10: год и больше → 10), ликвидность "
              "заблокирована/сожжена по RugCheck (0–10, только Solana), доступность (0–10: спот OKX +5, перп Hyperliquid +3, пул на Solana "
              "от $1M +2). Если данных нет — часть не считается («покрытие» показывает долю). Бонус: фонд или маркетмейкер из списка tier-1 "
              "(a16z, Paradigm, Polychain, Pantera, Dragonfly, Multicoin, Jump, Wintermute, GSR, DWF, Binance Labs/YZi, Coinbase Ventures, "
              "Animoca, Delphi и др.) в портфеле CoinGecko или в раунде DefiLlama +10, другой известный инвестор +5, нет данных +0. "
              "Рост цены в балл не входит.")

# ── status («ждём» / «можно на небольшую сумму» / «не брать») ────────────────────────────────────────────────────────────
DIP_30D = -0.30                      # «просадка» = price ≥ 30% below the 30-day high
STATUS_MIN_SCORE = 50
STATUS_MIN_VOL = 5_000_000
STATUS_MIN_POOL = 1_000_000
STATUS_RU = {"small": "можно на небольшую сумму", "wait": "ждём", "avoid": "не брать"}
STATUS_RULES_RU = ("«Не брать» — красный флаг (в обращении меньше 30% токенов, инсайдеры или топ-10 кошельков держат слишком много, "
                   "RugCheck «опасно»), оборот меньше $5M в сутки, монеты нет ни на споте OKX, ни на Hyperliquid, ни в пуле Solana от $1M, "
                   "или балл качества ниже 50. «Можно на небольшую сумму» — проверки пройдены и цена на 30% и больше ниже максимума "
                   "за 30 дней: в нашем исследовании мемов плюс давала только покупка после обвала. «Ждём» — проверки пройдены, "
                   "просадки нет. Максимум за 30 дней — оценка снизу (цены 24 ч / 7 / 30 дней назад, наши замеры, свечи Hyperliquid). "
                   "Это не прогноз роста; только суммы, которые не жалко потерять.")


def _f(x, default=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _cache_get(conn, k, max_age_ms, now):
    r = conn.execute("SELECT t, v FROM meme_spot_cache WHERE k=?", (k,)).fetchone()
    if r and now - r[0] <= max_age_ms:
        try:
            return json.loads(r[1])
        except ValueError:
            return None
    return None


def _cache_any(conn, k):
    r = conn.execute("SELECT t, v FROM meme_spot_cache WHERE k=?", (k,)).fetchone()
    try:
        return (r[0], json.loads(r[1])) if r else (None, None)
    except ValueError:
        return None, None


def _cache_put(conn, k, v, now):
    conn.execute("INSERT OR REPLACE INTO meme_spot_cache VALUES(?,?,?)", (k, now, json.dumps(v)))


def ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meme_spot_cache(k TEXT PRIMARY KEY, t INTEGER, v TEXT)")


def norm(x: str) -> str:
    return "".join(ch for ch in (x or "").lower() if ch.isalnum())


def tier1_of(investors: list[str]) -> list[str]:
    out = []
    for i in investors:
        low = (i or "").lower()
        for k, name in TIER1.items():
            if k in low and name not in out:
                out.append(name)
    return out


# ── Solana holders ─────────────────────────────────────────────────────────────────────────────────────────────────────

RUG_REPORT = "https://api.rugcheck.xyz/v1/tokens/{}/report"
NOT_INVESTOR = {"venture capital portfolios", "ondo intelligent portfolios"}


def holders_rugcheck(j: dict) -> dict | None:
    """Top-10 share from a RugCheck full report (pct of supply), excluding accounts RugCheck labels as pools/lockers/burn/exchanges."""
    top = j.get("topHolders") or []
    if not top:
        return None
    known = j.get("knownAccounts") or {}
    kept, excluded, insiders = [], [], 0.0
    for h in top[:20]:
        pct = _f(h.get("pct"), 0.0)
        lab = known.get(h.get("owner")) or known.get(h.get("address")) or {}
        typ, nm = (lab.get("type") or "").upper(), lab.get("name") or ""
        low = nm.lower()
        why = None
        if h.get("owner") in BURN or "BURN" in typ or "incinerator" in low:
            why = "сожжено"
        elif typ == "AMM" or "pool" in low or "amm" in low or h.get("owner") in POOL_AUTH:
            why = f"пул {nm or 'AMM'}"
        elif "LOCK" in typ or "lock" in low:
            why = f"заблокировано ({nm})"
        elif typ in ("CEX", "EXCHANGE") or any(k in low for k in ("binance", "okx", "bybit", "coinbase", "kraken", "gate", "bitget", "kucoin", "mexc", "htx")):
            why = f"биржа {nm}"
        if why:
            excluded.append({"account": h.get("owner") or h.get("address"), "pct": round(pct, 2), "why": why})
        else:
            kept.append(pct)
            if h.get("insider"):
                insiders += pct
    if not kept:
        return None
    k10 = sorted(kept, reverse=True)
    return {"top10_pct": round(sum(k10[:10]), 1), "top1_pct": round(k10[0], 1), "insiders_pct": round(insiders, 1) if insiders else 0.0,
            "excluded": excluded[:8], "checked": len(top[:20]), "holders_total": j.get("totalHolders"), "source": "RugCheck",
            "method": "20 крупнейших держателей по RugCheck; пулы, сожжённые, заблокированные и отмеченные биржи исключены"}


def holders_any(mint: str, get, post) -> dict | None:
    """RugCheck full report first (labels + insiders), public RPC as a fallback."""
    try:
        j = get(RUG_REPORT.format(mint))
        if isinstance(j, dict):
            h = holders_rugcheck(j)
            if h:
                return h
    except Exception:  # noqa: BLE001
        pass
    try:
        return holders(mint, post)
    except Exception:  # noqa: BLE001
        return None


def holders(mint: str, post) -> dict | None:
    """Top-10 share of supply held by wallets that are NOT pool vaults / burn / lock accounts (20 largest accounts checked)."""
    def rpc(method, params):
        j = post(RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(j, dict) or "result" not in j:
            raise RuntimeError(str((j or {}).get("error") if isinstance(j, dict) else j)[:120])
        return j["result"]
    big = rpc("getTokenLargestAccounts", [mint])["value"]
    sup = _f(rpc("getTokenSupply", [mint])["value"].get("uiAmountString")) or 0
    if not big or sup <= 0:
        return None
    accs = [b["address"] for b in big[:20]]
    infos = rpc("getMultipleAccounts", [accs, {"encoding": "jsonParsed"}])["value"]
    owners = []
    for x in infos:
        try:
            owners.append(x["data"]["parsed"]["info"]["owner"])
        except (TypeError, KeyError):
            owners.append(None)
    uniq = [o for o in dict.fromkeys(o for o in owners if o)]
    progs = {}
    if uniq:
        vals = rpc("getMultipleAccounts", [uniq, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}])["value"]
        progs = {o: (v or {}).get("owner") if v else None for o, v in zip(uniq, vals)}
    kept, excluded, burned = [], [], 0.0
    for b, o in zip(big[:20], owners):
        amt = _f(b.get("uiAmountString"), 0.0)
        why = None
        if o in BURN:
            why = "сожжено"; burned += amt
        elif o in POOL_AUTH:
            why = f"пул {POOL_AUTH[o]}"
        elif progs.get(o) in POOL_PROGRAMS:
            why = f"пул {POOL_PROGRAMS[progs[o]]}"
        elif progs.get(o) in LOCK_PROGRAMS:
            why = f"заблокировано ({LOCK_PROGRAMS[progs[o]]})"
        if why:
            excluded.append({"account": b["address"], "pct": round(amt / sup * 100, 2), "why": why})
        else:
            kept.append(amt)
    base = sup - burned
    top10 = sum(sorted(kept, reverse=True)[:10])
    return {"top10_pct": round(top10 / base * 100, 1) if base > 0 else None, "top1_pct": round(max(kept) / base * 100, 1) if kept and base > 0 else None,
            "excluded": excluded[:8], "checked": len(big[:20]), "source": "Solana RPC",
            "method": "20 крупнейших счетов; пулы, сожжённые и заблокированные исключены; биржевые кошельки не отличаем"}


# ── CoinGecko / DefiLlama / listings ───────────────────────────────────────────────────────────────────────────────────

def cg_markets(get) -> list[dict]:
    j = get(f"{CG}/coins/markets?vs_currency=usd&category=meme-token&order=market_cap_desc&per_page=100&page=1&price_change_percentage=7d,30d")
    if not isinstance(j, list):
        raise RuntimeError("coingecko markets")
    return [{k: x.get(k) for k in ("id", "symbol", "name", "current_price", "market_cap", "fully_diluted_valuation", "total_volume",
                                   "circulating_supply", "total_supply", "max_supply", "price_change_percentage_24h",
                                   "price_change_percentage_7d_in_currency", "price_change_percentage_30d_in_currency", "ath_change_percentage")}
            for x in j]


def cg_details(get, cid: str) -> dict:
    j = get(f"{CG}/coins/{cid}?localization=false&tickers=false&market_data=false&community_data=false&developer_data=false")
    if not isinstance(j, dict) or "id" not in j:
        raise RuntimeError("coingecko details")
    links = j.get("links") or {}
    cats = [c for c in (j.get("categories") or []) if c]
    lt = j.get("listing_timestamp")
    lts = None
    try:
        if isinstance(lt, (int, float)) and lt > 0:
            lts = int(lt * 1000 if lt < 1e11 else lt)
        elif isinstance(lt, str) and lt:
            lts = int(datetime.fromisoformat(lt.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        lts = None
    return {"categories": [c for c in cats if "portfolio" not in c.lower()][:8],
            "portfolios": [c.replace(" Portfolio", "").replace(" portfolio", "") for c in cats if "portfolio" in c.lower() and c.lower() not in NOT_INVESTOR],
            "platforms": {k: v for k, v in (j.get("platforms") or {}).items() if k and v},
            "genesis_date": j.get("genesis_date"), "listing_ts": lts, "homepage": next((u for u in links.get("homepage") or [] if u), None),
            "twitter": links.get("twitter_screen_name") or None}


def llama_matches(get, names: dict[str, str]) -> dict[str, list]:
    """{coin_id: [raise, …]} for every coin id in `names` ({coin_id: project name}); [] = DefiLlama knows no round."""
    j = get(LLAMA)
    raises = (j or {}).get("raises") if isinstance(j, dict) else None
    if not isinstance(raises, list):
        raise RuntimeError("defillama raises")
    idx: dict[str, list] = {}
    for r in raises:
        idx.setdefault(norm(r.get("name")), []).append(r)
    out = {}
    for cid, nm in names.items():
        rr = idx.get(norm(nm)) or []
        out[cid] = [{"date": r.get("date"), "round": r.get("round"), "amount_musd": r.get("amount"),
                     "lead": (r.get("leadInvestors") or [])[:6], "other": (r.get("otherInvestors") or [])[:10]} for r in rr][:5]
    return out


def okx_spot_bases(get) -> list[str]:
    j = get(OKX_SPOT)
    d = (j or {}).get("data") if isinstance(j, dict) else None
    if not d:
        raise RuntimeError("okx spot")
    return sorted({x.get("baseCcy") for x in d if x.get("quoteCcy") in ("USDT", "USDC") and x.get("baseCcy")})


# ── score ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def score(r: dict) -> None:
    parts, avail = {}, 0.0
    mc, vol = r.get("mcap") or 0, r.get("vol24") or 0
    parts["размер и ликвидность"] = 12.5 * _clip(math.log10(max(mc, 1) / 5e7) / 2) + 12.5 * _clip(math.log10(max(vol, 1) / 1e6) / 2); avail += 25
    if r.get("circ_ratio") is not None:
        parts["токеномика"] = 25 * _clip((r["circ_ratio"] - 0.5) / 0.5); avail += 25
    if (r.get("holders") or {}).get("top10_pct") is not None:
        parts["распределение"] = 20 * _clip((50 - r["holders"]["top10_pct"]) / 40); avail += 20
    if r.get("age_days") is not None:
        parts["возраст"] = 10 * _clip(r["age_days"] / 365); avail += 10
    if r.get("lp_locked_pct") is not None:
        parts["ликвидность заблокирована"] = 10 * _clip(r["lp_locked_pct"] / 100); avail += 10
    ls = r.get("listed") or {}
    parts["доступность"] = (5 if ls.get("okx_spot") else 0) + (3 if ls.get("hl_perp") else 0) + (2 if (ls.get("solana_liq") or 0) >= 1e6 else 0); avail += 10
    b = r.get("backers") or {}
    bonus = 10 if b.get("tier1") else (5 if b.get("investors") else 0)
    base = 90 * sum(parts.values()) / avail if avail else 0
    r["score_parts"] = {k: round(v, 1) for k, v in parts.items()} | {"бонус инвесторы": bonus}
    r["coverage"] = round(avail / 100, 2)
    r["score"] = int(round(min(100, base + bonus)))


def reasons_and_flags(r: dict) -> None:
    rs, fl = [], []
    def money(x):
        return f"${x / 1e9:.1f}B" if x >= 1e9 else f"${x / 1e6:.0f}M"
    rs.append(f"капитализация {money(r['mcap'])}, объём {money(r['vol24'])} за сутки")
    cr = r.get("circ_ratio")
    if cr is None:
        rs.append("доля в обращении: нет данных")
    elif cr >= 0.98:
        rs.append("в обращении ~100% токенов — разлоков нет")
    else:
        rs.append(f"в обращении {cr * 100:.0f}% — ещё {money((r.get('fdv') or 0) - r['mcap'])} может выйти на рынок")
        fl.append({"level": "red" if cr < 0.3 else "yellow", "text": f"в обращении только {cr * 100:.0f}% токенов — давление разлоков"})
    h = r.get("holders")
    if h and h.get("top10_pct") is not None:
        rs.append(f"топ-10 кошельков (без пулов, сожжённых и бирж): {h['top10_pct']:.0f}%" + (f", держателей {h['holders_total']:,}".replace(",", " ") if h.get("holders_total") else "")
                  + f" ({h.get('source')})")
        if (h.get("insiders_pct") or 0) >= 10:
            fl.append({"level": "red" if h["insiders_pct"] >= 25 else "yellow", "text": f"инсайдеры (по RugCheck) держат {h['insiders_pct']:.0f}%"})
        if h["top10_pct"] > 60:
            fl.append({"level": "red", "text": f"топ-10 кошельков держат {h['top10_pct']:.0f}%"})
        elif h["top10_pct"] > 40:
            fl.append({"level": "yellow", "text": f"топ-10 кошельков держат {h['top10_pct']:.0f}% (биржевые кошельки не отделены)"})
    else:
        rs.append("держатели: нет данных" + ("" if r.get("solana_mint") else " (не Solana — бесплатно не проверить)"))
    rs.append(f"на рынке {r['age_days'] / 365:.1f} г." if r.get("age_days") is not None and r["age_days"] >= 365 else
              (f"на рынке {r['age_days']:.0f} дн." if r.get("age_days") is not None else "возраст: нет данных"))
    if r.get("lp_locked_pct") is not None:
        rs.append(f"ликвидность заблокирована/сожжена: {r['lp_locked_pct']:.0f}% (RugCheck)")
    ls = r.get("listed") or {}
    where = [w for w, ok in (("OKX спот", ls.get("okx_spot")), ("Hyperliquid перп", ls.get("hl_perp")),
                             (f"Solana пул {money(ls.get('solana_liq') or 0)}", (ls.get("solana_liq") or 0) >= 1e5)) if ok]
    rs.append("где купить: " + (", ".join(where) if where else "нет данных"))
    b = r.get("backers") or {}
    if b.get("investors"):
        rs.append("инвесторы/маркетмейкеры: " + ", ".join(b["investors"][:5]) + f" (источник: {b.get('source')})")
    else:
        rs.append("инвесторы: нет данных" + (" (в CoinGecko нет портфелей фондов, раунды DefiLlama платные)" if b.get("checked") else " (ещё не проверено)"))
    if r.get("from_ath_pct") is not None:
        rs.append(f"от исторического максимума {r['from_ath_pct']:.0f}%")
    for x in r.get("rug_risks") or []:
        if (x.get("level") or "").lower() == "danger":
            fl.append({"level": "red", "text": "RugCheck: " + (x.get("name") or "")})
    r["reasons"], r["flags"] = rs, fl


# ── 30-day high + status ───────────────────────────────────────────────────────────────────────────────────────────────

def _day(ts: int) -> int:
    return int(ts // DAY)


def update_highs(conn, sym: str, price: float | None, now: int) -> dict:
    """Remember the highest price we saw for `sym` per UTC day (last 31 days); returns {day: high}."""
    k = f"hi:{sym}"
    d = {int(a): b for a, b in ((_cache_any(conn, k)[1] or {}).get("d") or {}).items()}
    if price:
        d[_day(now)] = max(d.get(_day(now), 0.0), float(price))
    d = {a: b for a, b in d.items() if a >= _day(now) - 31}
    _cache_put(conn, k, {"d": {str(a): b for a, b in d.items()}}, now)
    return d


def put_hl_highs(conn, highs: dict[str, dict], now: int) -> None:
    """{SYM: {"px": hl_price, "d": {day: high}}} from the meme desk's Hyperliquid 1h candles (prices already per 1 token)."""
    for sym, v in highs.items():
        _cache_put(conn, f"hlhi:{sym}", {"px": v.get("px"), "d": {str(a): b for a, b in (v.get("d") or {}).items()}}, now)


def high30(price: float | None, chgs: list, own: dict | None, hl: dict | None, now: int) -> tuple[float | None, list[str]]:
    """Lower-bound estimate of the 30-day high and the list of what it is built from."""
    if not price:
        return None, []
    pts, basis = [float(price)], []
    for c, lab in zip(chgs, ("24 ч", "7 дн", "30 дн")):
        if c is not None and -99 < c:
            pts.append(price / (1 + c / 100)); basis.append(lab)
    lo = _day(now) - 30
    own_pts = [b for a, b in (own or {}).items() if int(a) >= lo]
    if own_pts:
        pts += own_pts; basis.append(f"наши замеры {len(own_pts)} дн.")
    if hl and hl.get("px") and abs(hl["px"] / price - 1) <= 0.10:
        hp = [b for a, b in (hl.get("d") or {}).items() if int(a) >= lo]
        if hp:
            pts += hp; basis.append(f"свечи Hyperliquid {len(hp)} дн.")
    return max(pts), basis


def _words(s: str, n: int = 12) -> str:
    w = s.split()
    return s if len(w) <= n else " ".join(w[:n]).rstrip(",;:—-") + "…"


def _money(x: float) -> str:
    return f"${x / 1e9:.1f}B" if x >= 1e9 else (f"${x / 1e6:.0f}M" if x >= 1e6 else f"${x / 1e3:.0f}k")


def status_of(r: dict) -> None:
    """status / status_ru / why_ru by the rules in the module docstring (needs score, flags and from_high30_pct)."""
    reds = [f for f in r.get("flags") or [] if f.get("level") == "red"]
    ls = r.get("listed") or {}
    vol, dd = r.get("vol24") or 0, r.get("from_high30_pct")
    where = ls.get("okx_spot") or ls.get("hl_perp") or (ls.get("solana_liq") or 0) >= STATUS_MIN_POOL
    if not r.get("price"):
        st, why = "avoid", "нет цены — не оцениваем"
    elif reds:
        t = reds[0]["text"]
        if t.startswith("RugCheck: "):
            from .memes import RUG_RU
            t = "RugCheck — " + RUG_RU.get(t[10:].strip().lower(), t[10:])
        st, why = "avoid", "красный флаг: " + t
    elif vol < STATUS_MIN_VOL:
        st, why = "avoid", f"мало оборота: {_money(vol)} в сутки — цену легко сдвинуть"
    elif not where:
        st, why = "avoid", "нет на OKX и Hyperliquid, пул Solana меньше $1M"
    elif (r.get("score") or 0) < STATUS_MIN_SCORE:
        st, why = "avoid", f"низкий балл качества: {r.get('score') or 0} из 100"
    elif dd is not None and dd <= DIP_30D * 100:
        st, why = "small", f"просадка {_pct(dd)} от максимума за 30 дней, проверки пройдены"
    elif dd is not None:
        st, why = "wait", f"{_pct(dd)} от максимума за 30 дней — ждём просадку от {_pct(DIP_30D * 100)}"
    else:
        st, why = "wait", f"проверки пройдены, ждём просадку от {_pct(DIP_30D * 100)}"
    r["status"], r["status_ru"], r["why_ru"] = st, STATUS_RU[st], _words(why)


def _pct(x: float) -> str:
    v = round(x)
    return f"{v}%".replace("-", "−") if v else "0%"


def summary_of(rows: list[dict]) -> str:
    small = [r["symbol"] for r in sorted(rows, key=lambda r: -(r.get("score") or 0)) if r.get("status") == "small"]
    n_wait = sum(1 for r in rows if r.get("status") == "wait")
    n_avoid = sum(1 for r in rows if r.get("status") == "avoid")
    if small:
        return f"Сейчас можно присмотреться (на небольшую сумму): {', '.join(small[:6])}; остальные — ждём ({n_wait}) или не брать ({n_avoid})."
    return f"Сейчас брать нечего: ждём просадку — {n_wait}, не брать — {n_avoid}."


# ── run ────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def run(conn, now: int, get, post, hl_memes: set[str], deadline: float, max_details: int = 2, max_holders: int = 3) -> dict:
    """{'rows': [...], 'sources': {...}, 'note': str}. `get(url)` / `post(url, body)` return parsed JSON or raise."""
    ensure(conn)
    src = {}

    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception:  # noqa: BLE001
            return None
    mk = _cache_get(conn, "cg_markets", 2 * H, now)
    if mk is None:
        mk = safe(cg_markets, get)
        if mk:
            _cache_put(conn, "cg_markets", mk, now)
        else:
            mk = _cache_any(conn, "cg_markets")[1]
    src["coingecko"] = bool(mk)
    if not mk:
        return {"rows": [], "sources": src, "note": "CoinGecko не ответил — список спот-мемов пуст."}
    uni = [x for x in mk if (x.get("market_cap") or 0) >= MIN_MCAP and (x.get("total_volume") or 0) >= MIN_VOL and norm(x.get("symbol")) not in NOT_MEME]
    # details (≤ max_details per run, 7 days)
    det = {}
    todo = []
    for x in uni:
        d = _cache_get(conn, f"cg:{x['id']}", 7 * DAY, now)
        if d is None:
            todo.append(x["id"]); d = _cache_any(conn, f"cg:{x['id']}")[1]
        det[x["id"]] = d
    for cid in todo[:max_details]:
        if time.time() > deadline:
            break
        d = safe(cg_details, get, cid)
        if d is not None:
            _cache_put(conn, f"cg:{cid}", d, now); det[cid] = d
    # DefiLlama raises (once a day)
    rz = _cache_get(conn, "llama", DAY, now)
    if rz is None and time.time() < deadline:
        m = safe(llama_matches, get, {x["id"]: x["name"] for x in mk})
        if m is not None:
            _cache_put(conn, "llama", m, now); rz = m
    src["defillama"] = rz is not None
    rz = rz if rz is not None else (_cache_any(conn, "llama")[1] or {})
    okx = _cache_get(conn, "okx_spot", DAY, now)
    if okx is None:
        okx = safe(okx_spot_bases, get)
        if okx:
            _cache_put(conn, "okx_spot", okx, now)
        else:
            okx = _cache_any(conn, "okx_spot")[1] or []
    src["okx"] = bool(okx)
    okx = set(okx or [])
    hl = {m[1:] if m.startswith("k") and m[1:].isupper() else m for m in hl_memes}
    rows = []
    for x in uni:
        d = det.get(x["id"]) or {}
        mint = (d.get("platforms") or {}).get("solana")
        mc, fdv = x.get("market_cap") or 0, x.get("fully_diluted_valuation")
        sym = (x.get("symbol") or "").upper()
        raises = rz.get(x["id"]) if isinstance(rz, dict) else None
        inv = list(dict.fromkeys((d.get("portfolios") or []) + [i for r in raises or [] for i in (r.get("lead") or []) + (r.get("other") or [])]))
        rows.append({"id": x["id"], "symbol": sym, "name": x.get("name"), "price": x.get("current_price"), "mcap": mc, "fdv": fdv,
                     "circ_ratio": round(mc / fdv, 3) if mc and fdv else None, "vol24": x.get("total_volume") or 0,
                     "chg24h": x.get("price_change_percentage_24h"), "chg7d": x.get("price_change_percentage_7d_in_currency"),
                     "chg30d": x.get("price_change_percentage_30d_in_currency"), "from_ath_pct": x.get("ath_change_percentage"),
                     "categories": d.get("categories"), "homepage": d.get("homepage"), "twitter": d.get("twitter"),
                     "chains": sorted((d.get("platforms") or {}).keys()) or None, "solana_mint": mint, "details_known": bool(d),
                     "listed": {"okx_spot": sym in okx, "hl_perp": sym in hl, "solana_liq": None},
                     "backers": {"checked": bool(d) or raises is not None, "raises": raises or [], "investors": inv[:10], "tier1": tier1_of(inv),
                                 "source": ", ".join(x for x, ok in (("CoinGecko портфели", bool(d.get("portfolios"))), ("DefiLlama", bool(raises))) if ok) or None},
                     "age_days": None, "age_source": None, "holders": None, "lp_locked_pct": None,
                     "link": f"https://www.coingecko.com/en/coins/{x['id']}"})
        g = d.get("genesis_date")
        if d.get("listing_ts") and not g:
            rows[-1]["age_days"] = round((now - d["listing_ts"]) / DAY); rows[-1]["age_source"] = "листинг на CoinGecko"
        if g:
            try:
                rows[-1]["age_days"] = round((now - datetime.fromisoformat(g).replace(tzinfo=timezone.utc).timestamp() * 1000) / DAY)
                rows[-1]["age_source"] = "CoinGecko genesis"
            except ValueError:
                pass
    sol = [r for r in rows if r["solana_mint"]]
    # DexScreener: deepest pool, oldest pool (age), link
    for i in range(0, len(sol), 30):
        if time.time() > deadline:
            break
        j = safe(get, f"{DS}/tokens/v1/solana/" + ",".join(r["solana_mint"] for r in sol[i:i + 30]))
        if isinstance(j, list):
            src["dexscreener"] = True
            by = {}
            for pr in j:
                by.setdefault((pr.get("baseToken") or {}).get("address"), []).append(pr)
            for r in sol[i:i + 30]:
                prs = by.get(r["solana_mint"]) or []
                if prs:
                    deep = max(prs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
                    r["listed"]["solana_liq"] = (deep.get("liquidity") or {}).get("usd")
                    r["dex_link"] = deep.get("url")
                    ts = [p.get("pairCreatedAt") for p in prs if p.get("pairCreatedAt")]
                    if ts and r["age_days"] is None:
                        r["age_days"] = round((now - min(ts)) / DAY); r["age_source"] = "старейший пул DexScreener"
    # holders + RugCheck (cached per mint 24 h; ≤ max_holders new per run)
    n_new = 0
    for r in sorted(sol, key=lambda r: -r["mcap"]):
        hk, rk = f"holders:{r['solana_mint']}", f"rug:{r['solana_mint']}"
        h, rg = _cache_get(conn, hk, DAY, now), _cache_get(conn, rk, DAY, now)
        if (h is None or rg is None) and n_new < max_holders and time.time() < deadline:
            n_new += 1
            if h is None:
                h = safe(holders_any, r["solana_mint"], get, post)
                if h is not None:
                    _cache_put(conn, hk, h, now); src["holders"] = True
            if rg is None:
                j = safe(get, RUG.format(r["solana_mint"]))
                if isinstance(j, dict) and ("risks" in j or "score" in j):
                    rg = {"lp": j.get("lpLockedPct"), "risks": [{"name": x.get("name"), "level": x.get("level")} for x in j.get("risks") or [] if isinstance(x, dict)][:8]}
                    _cache_put(conn, rk, rg, now); src["rugcheck"] = True
        h = h if h is not None else _cache_any(conn, hk)[1]
        rg = rg if rg is not None else _cache_any(conn, rk)[1]
        r["holders"] = h
        if rg:
            r["lp_locked_pct"] = _f(rg.get("lp")); r["rug_risks"] = rg.get("risks")
    for r in rows:
        score(r); reasons_and_flags(r)
        own = update_highs(conn, r["symbol"], r.get("price"), now)
        hl_hi = _cache_any(conn, f"hlhi:{r['symbol']}")[1]
        hi, basis = high30(r.get("price"), [r.get("chg24h"), r.get("chg7d"), r.get("chg30d")], own, hl_hi, now)
        r["high30_px"] = hi
        r["from_high30_pct"] = round((r["price"] / hi - 1) * 100, 1) if hi and r.get("price") else None
        r["high30_basis"] = basis
        status_of(r)
    rows.sort(key=lambda r: -r["score"])
    filled = sum(1 for r in rows if r["details_known"])
    note = (f"Кандидаты в спот-портфель мемов: мемы из топ-100 CoinGecko с капитализацией от $50M и объёмом от $1M. Балл — качество и риск "
            f"(размер, токеномика, держатели, возраст, ликвидность, где купить, инвесторы), а НЕ прогноз роста: история не доказала, что эти "
            f"признаки предсказывают доходность мемов (docs/research/memes.md). Подробные данные собраны по {filled} из {len(rows)} монет — "
            f"остальные дозаполняются по 2 за запуск. Только наблюдение, без сделок.")
    top = rows[:25]
    return {"rows": top, "sources": src, "note": note, "summary_ru": summary_of(top), "status_rules_ru": STATUS_RULES_RU,
            "status_counts": {k: sum(1 for r in top if r.get("status") == k) for k in ("small", "wait", "avoid")}}
