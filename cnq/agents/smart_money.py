"""«Куда инвестируют киты» — on-chain smart money for the VIP «Спот» section (04.11.2026, owner: «очень часто именно так и можно
найти ранний проект с большими иксами»). Research and information only: no orders, no change to any real-money rule. The signal
is written to web/smart_money_signal.json so the spot logic CAN read it later — connecting it to real money needs the owner's yes.

What it does (free, keyless sources; each one may fail → «источник недоступен», the rest still works):
  1. Smart wallets = the publicly labelled addresses of config/smart_money_wallets.json (funds, market makers, known traders — each
     with a cited public label) + the Hyperliquid top traders the whale study already tracks (public leaderboard, web/whales_top.json).
     Labels are re-checked against Blockscout's public tags / ENS (shown as confirmed or not).
  2. Blockscout API v2 (eth / base / arbitrum): ERC-20 transfers of each wallet over the last 7 days → NET inflow per token
     (in − out, so a market maker's pass-through inventory mostly cancels). A token that ≥ 2 smart wallets accumulated
     (net inflow ≥ $5k each) is a signal.
  3. DexScreener: price, liquidity, FDV / market cap, age of the oldest pool. GeckoTerminal: hourly candles → price at the first
     buy → change since. GoPlus token security: honeypot, buy/sell tax, holder concentration.
     Risk flags: low liquidity, new contract, honeypot / tax, concentrated holders, «only market makers».
  4. Hyperliquid spot (info API): spot balances of the HL whales; ≥ 2 whales that newly bought / added ≥ 10 % within 7 days.
  5. Exchange flows: summed ETH (Blockscout daily balance history), BTC (mempool.space) and SOL (public RPC) balances of a few
     known exchange wallets — a rise = coins came TO exchanges, a fall = coins left exchanges.
  6. Every signal is logged once per UTC day in data/smart_money/signals.jsonl; data/smart_money/outcomes.jsonl gets its price
     1 / 7 / 30 days after the first sighting → the panel shows whether following these signals has worked.
Runs hourly (the pipeline calls it every 30 min; in between it returns None and the old feed stays). Stdlib only, polite delays.
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics as st
import time
import urllib.request
from datetime import datetime, timezone

UA = "CryptoNovaResearch/1.0 (+https://cryptonova.vip)"
H, DAY = 3_600_000, 86_400_000
EVERY = 55 * 60_000
WINDOW = 7 * DAY
DEADLINE_S = 240                       # stop fetching after this many seconds (the pipeline step has its own timeout)
MIN_USD = 5_000                        # a wallet's 7-day net inflow of a token must be worth ≥ $5k to count
SINGLE_MIN_USD = 25_000
MIN_LIQ = 10_000                       # below: spam / unpriced — dropped (counted)
LOW_LIQ = 100_000
NEW_DAYS = 30
EARLY_FDV = 300_000_000
BIG_FDV = 5_000_000_000
CONC_TOP10 = 50.0                      # % of supply in the top-10 holders (locked / contract-LP excluded)
HL_ADD = 0.10
HL_MIN_USD = 10_000
HL_MAX_ADDRS = 20
EVM_HL_ADDRS = 10                      # HL top traders also scanned on Arbitrum (HL's bridge) and Ethereum
PAGES = 3
HORIZONS = (1, 7, 30)

BS = {"eth": "https://eth.blockscout.com", "base": "https://base.blockscout.com", "arbitrum": "https://arbitrum.blockscout.com"}
DS_CHAIN = {"eth": "ethereum", "base": "base", "arbitrum": "arbitrum"}
GT_NET = {"eth": "eth", "base": "base", "arbitrum": "arbitrum"}
GP_CHAIN = {"eth": "1", "base": "8453", "arbitrum": "42161"}
EXPLORER = {"eth": "https://etherscan.io", "base": "https://basescan.org", "arbitrum": "https://arbiscan.io"}
CHAIN_RU = {"eth": "Ethereum", "base": "Base", "arbitrum": "Arbitrum", "hl": "Hyperliquid спот"}
HL = "https://api.hyperliquid.xyz/info"
DS = "https://api.dexscreener.com/tokens/v1/"             # + {chainId}/{a,b,…}: every pool of each token (the old «latest/dex/tokens»
DS_BATCH = 10                                               # answers at most 30 pools for ALL tokens together — most were lost)
DS_MAX = 200                                                # tokens priced per run
GT = "https://api.geckoterminal.com/api/v2/networks/"
GP = "https://api.gopluslabs.io/api/v1/token_security/"
MEMPOOL = ["https://mempool.space/api/address/", "https://blockstream.info/api/address/"]
SOL_RPC = "https://api.mainnet-beta.solana.com"

# stablecoins, wrapped majors, liquid-staking and LP tokens: not «projects»
IGNORE = {"USDC", "USDT", "DAI", "USDE", "SUSDE", "USDS", "SUSDS", "FDUSD", "PYUSD", "TUSD", "FRAX", "LUSD", "GHO", "CRVUSD", "USD0",
          "USD0++", "USDB", "USDBC", "USDC.E", "USDT0", "USD₮0", "RLUSD", "USD1", "USDG", "EURC", "EURT", "XAUT", "PAXG", "WETH", "ETH", "WBTC",
          "CBBTC", "TBTC", "LBTC", "BTC.B", "STETH", "WSTETH", "RETH", "CBETH", "WEETH", "EETH", "EZETH", "RSETH", "METH", "SWETH", "OSETH",
          "WBETH", "SFRXETH", "FRXETH", "ANKRETH", "ETHX", "UNI-V2", "SLP", "CAKE-LP", "BPT", "WSOL", "SOL", "WMATIC", "WBNB", "ARB.E"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sm_hl (addr TEXT, coin TEXT, t INTEGER, total REAL, ntl REAL);
CREATE INDEX IF NOT EXISTS sm_hl_i ON sm_hl (addr, t);
CREATE TABLE IF NOT EXISTS sm_flow (asset TEXT, t INTEGER, total REAL, n INTEGER);
CREATE TABLE IF NOT EXISTS sm_kv (k TEXT PRIMARY KEY, v TEXT);
"""


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def kv_get(conn, k, default=None):
    r = conn.execute("SELECT v FROM sm_kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def kv_set(conn, k, v) -> None:
    conn.execute("INSERT OR REPLACE INTO sm_kv VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))


def _f(x, d=None):
    try:
        v = float(x)
        return v if v == v else d
    except Exception:  # noqa: BLE001
        return d


def http_json(url: str, body=None, headers: dict | None = None, timeout: float = 20):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=h,
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _sleep(s: float) -> None:
    time.sleep(s)


def iso_ms(s) -> int | None:
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return None


def day_key(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


class Src:
    """per-source status for the panel: ok / failed calls and the last error («источник недоступен» when nothing worked)"""

    def __init__(self):
        self.s: dict[str, dict] = {}

    def ok(self, k):
        self.s.setdefault(k, {"ok": 0, "fail": 0, "err": None})["ok"] += 1

    def fail(self, k, e):
        x = self.s.setdefault(k, {"ok": 0, "fail": 0, "err": None})
        x["fail"] += 1
        x["err"] = str(e)[:140]

    def doc(self, names: dict) -> dict:
        out = {}
        for k, nm in names.items():
            x = self.s.get(k)
            if not x:
                out[k] = {"name_ru": nm, "ok": None, "status_ru": "не запрашивался в этом прогоне"}
                continue
            good = x["ok"] > 0
            out[k] = {"name_ru": nm, "ok": good, "calls_ok": x["ok"], "calls_fail": x["fail"],
                      "status_ru": "работает" if good and not x["fail"] else "работает частично" if good else "источник недоступен",
                      "err": x["err"] if not good else None}
        return out


SRC_NAMES = {"blockscout": "Blockscout (Ethereum, Base, Arbitrum)", "dexscreener": "DexScreener", "geckoterminal": "GeckoTerminal",
             "goplus": "GoPlus (проверка токена)", "hyperliquid": "Hyperliquid info API (спот)", "mempool": "mempool.space (BTC)",
             "solana": "Solana RPC"}


# ------------------------------------------------------------------------------------------------------------ wallets
def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def tags_of(info: dict) -> list[str]:
    """Blockscout address JSON → its public names: public tags, metadata tags, ENS, contract name"""
    out = []
    for t in (info or {}).get("public_tags") or []:
        n = t.get("display_name") or t.get("label") if isinstance(t, dict) else t
        if n:
            out.append(str(n))
    for t in ((info or {}).get("metadata") or {}).get("tags") or []:
        if isinstance(t, dict) and t.get("name"):
            out.append(str(t["name"]))
    if (info or {}).get("ens_domain_name"):
        out.append(str(info["ens_domain_name"]))
    if (info or {}).get("name"):
        out.append(str(info["name"]))
    return list(dict.fromkeys(out))


def confirmed(label: str, tags: list[str]) -> bool:
    words = [w for w in label.lower().replace("(", " ").replace(")", " ").split() if len(w) >= 3]
    blob = " ".join(tags).lower()
    return any(w in blob for w in words)


def hl_wallets(whales: dict | None, n: int = HL_MAX_ADDRS) -> list[dict]:
    """Hyperliquid top traders the whale study tracks (src «hl»), best 60/30-day P&L first"""
    rows = []
    for p in ((whales or {}).get("top") or []) + ((whales or {}).get("others") or []):
        a = str(p.get("addr") or "")
        if p.get("src") != "hl" or not a.startswith("0x") or len(a) != 42:
            continue
        rows.append({"label": p.get("name") or p.get("short") or (a[:6] + "…" + a[-4:]), "kind": "топ-трейдер Hyperliquid",
                     "address": a.lower(), "pnl": _f(p.get("pnl_60d"), None) or _f(p.get("pnl_30d"), 0) or 0,
                     "source": f"https://app.hyperliquid.xyz/explorer/address/{a}", "source_note": "публичный лидерборд Hyperliquid"})
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: -r["pnl"]):
        if r["address"] in seen or r["pnl"] <= 0:
            continue
        seen.add(r["address"])
        out.append(r)
    return out[:n]


# ------------------------------------------------------------------------------------------------------------ Blockscout
def _tok_addr(tok: dict) -> str:
    return str(tok.get("address_hash") or tok.get("address") or "").lower()


def net_flows(items: list[dict], wallet: str, since: int, exch: set[str]) -> tuple[dict, int | None]:
    """ERC-20 transfers of one wallet → {token: {in, out, first_in_t, last_t, from_exchange, tok}}; and the oldest time seen"""
    w = wallet.lower()
    out: dict[str, dict] = {}
    oldest = None
    for it in items:
        t = iso_ms(it.get("timestamp"))
        if t is None:
            continue
        oldest = t if oldest is None else min(oldest, t)
        if t < since:
            continue
        tok = it.get("token") or {}
        if str(tok.get("type") or "ERC-20") != "ERC-20":
            continue
        ta = _tok_addr(tok)
        if not ta:
            continue
        tot = it.get("total") or {}
        dec = int(_f(tot.get("decimals"), None) or _f(tok.get("decimals"), 18) or 18)
        amt = (_f(tot.get("value"), 0) or 0) / (10 ** dec)
        fr = str((it.get("from") or {}).get("hash") or "").lower()
        to = str((it.get("to") or {}).get("hash") or "").lower()
        if fr == to:
            continue
        x = out.setdefault(ta, {"in": 0.0, "out": 0.0, "first_in_t": None, "last_t": t, "from_exchange": 0, "tok": tok, "n_in": 0})
        x["last_t"] = max(x["last_t"], t)
        if to == w:
            x["in"] += amt
            x["n_in"] += 1
            x["first_in_t"] = t if x["first_in_t"] is None else min(x["first_in_t"], t)
            if fr in exch:
                x["from_exchange"] += 1
        elif fr == w:
            x["out"] += amt
    return out, oldest


def scan_wallet(get, chain: str, addr: str, now: int, exch: set[str], src: Src, pages: int = PAGES, deadline: float = 0) -> dict:
    """one wallet on one chain → net flows over 7 days (+ how far back the pages reached)"""
    base = f"{BS[chain]}/api/v2/addresses/{addr}/token-transfers?type=ERC-20"
    items, params, reached = [], "", None
    for _ in range(pages):
        if deadline and time.time() > deadline:
            break
        try:
            j = get(base + params)
            src.ok("blockscout")
        except Exception as e:  # noqa: BLE001
            src.fail("blockscout", e)
            return {"error": str(e)[:120]}
        _sleep(0.25)
        its = (j or {}).get("items") or []
        items += its
        ts = [iso_ms(i.get("timestamp")) for i in its]
        ts = [t for t in ts if t]
        if ts:
            reached = min(ts)
        npp = (j or {}).get("next_page_params")
        if not its or not npp or (reached and reached < now - WINDOW):
            break
        params = "&" + "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in npp.items() if v is not None)
    flows, _old = net_flows(items, addr, now - WINDOW, exch)
    full = reached is not None and reached < now - WINDOW
    return {"flows": flows, "coverage_h": round(min(WINDOW, now - reached) / H, 1) if reached else 0, "full": full or len(items) < 50,
            "n_items": len(items)}


def group_tokens(scans: list[dict]) -> dict[str, dict]:
    """[{wallet, chain, flows}] → per «chain:token»: the wallets with a positive net inflow"""
    out: dict[str, dict] = {}
    for s in scans:
        for ta, x in (s.get("flows") or {}).items():
            net = x["in"] - x["out"]
            if net <= 0 or not x["n_in"]:
                continue
            tok = x["tok"]
            sym = str(tok.get("symbol") or "").strip()
            if sym.upper() in IGNORE or not sym:
                continue
            k = f"{s['chain']}:{ta}"
            g = out.setdefault(k, {"key": k, "chain": s["chain"], "address": ta, "symbol": sym[:16], "name": str(tok.get("name") or "")[:40],
                                   "bs_rate": _f(tok.get("exchange_rate")), "reputation": tok.get("reputation"), "icon": tok.get("icon_url"),
                                   "buyers": []})
            g["buyers"].append({"label": s["label"], "kind": s["kind"], "address": s["wallet"], "amount": net, "first_t": x["first_in_t"],
                                "from_exchange": x["from_exchange"] > 0, "mm": "маркетмейкер" in s["kind"]})
    return out


# ------------------------------------------------------------------------------------------------------------ market data
def ds_best(pairs: list[dict], chain: str, addr: str) -> dict | None:
    """the deepest DexScreener pool of this token on this chain + the oldest pool's age"""
    mine = [p for p in pairs or [] if p.get("chainId") == DS_CHAIN.get(chain) and str((p.get("baseToken") or {}).get("address", "")).lower() == addr]
    if not mine:
        return None
    best = max(mine, key=lambda p: _f((p.get("liquidity") or {}).get("usd"), 0) or 0)
    created = [p.get("pairCreatedAt") for p in mine if p.get("pairCreatedAt")]
    return {"price": _f(best.get("priceUsd")), "liq": _f((best.get("liquidity") or {}).get("usd"), 0) or 0, "fdv": _f(best.get("fdv")),
            "mcap": _f(best.get("marketCap")), "pool": best.get("pairAddress"), "url": best.get("url"), "dex": best.get("dexId"),
            "created": min(created) if created else None, "chg24": _f((best.get("priceChange") or {}).get("h24")),
            "vol24": _f((best.get("volume") or {}).get("h24"))}


def ds_fetch(get, chain: str, addrs: list[str], src: Src, deadline: float = 0) -> list[dict]:
    pairs = []
    for i in range(0, len(addrs), DS_BATCH):
        if deadline and time.time() > deadline:
            break
        try:
            j = get(DS + DS_CHAIN[chain] + "/" + ",".join(addrs[i:i + DS_BATCH]))
            pairs += j if isinstance(j, list) else (j or {}).get("pairs") or []
            src.ok("dexscreener")
        except Exception as e:  # noqa: BLE001
            src.fail("dexscreener", e)
        _sleep(0.25)
    return pairs


def price_at(ohlcv: list, t: int) -> float | None:
    """GeckoTerminal ohlcv_list ([ts_s, o, h, l, c, v], newest first) → close of the last candle that started ≤ t"""
    best = None
    for row in ohlcv or []:
        ts = int(row[0]) * 1000
        if ts <= t and (best is None or ts > best[0]):
            best = (ts, _f(row[4]))
    if best is None and ohlcv:
        oldest = min(ohlcv, key=lambda r: r[0])
        return _f(oldest[1])
    return best[1] if best else None


def goplus_flags(r: dict | None) -> tuple[list[str], dict]:
    """GoPlus token_security row → risk flags (ru) and the numbers behind them"""
    if not r:
        return [], {}
    flags, info = [], {}
    if str(r.get("is_honeypot")) == "1" or str(r.get("cannot_sell_all")) == "1":
        flags.append("ловушка: продать нельзя (honeypot)")
    bt, stx = _f(r.get("buy_tax"), 0) or 0, _f(r.get("sell_tax"), 0) or 0
    info["buy_tax"], info["sell_tax"] = round(bt * 100, 1), round(stx * 100, 1)
    if bt >= 0.05 or stx >= 0.05:
        flags.append(f"налог: покупка {bt * 100:.0f}%, продажа {stx * 100:.0f}%")
    top = 0.0
    for h in (r.get("holders") or [])[:10]:
        if str(h.get("is_locked")) == "1":
            continue
        top += (_f(h.get("percent"), 0) or 0) * 100
    info["top10_pct"] = round(top, 1)
    if top >= CONC_TOP10:
        flags.append(f"топ-10 кошельков держат {top:.0f}%")
    if str(r.get("is_mintable")) == "1":
        flags.append("владелец может выпускать новые токены")
    if str(r.get("is_open_source")) == "0":
        flags.append("код контракта не опубликован")
    info["holders"] = int(_f(r.get("holder_count"), 0) or 0)
    return flags, info


def risk_flags(tok: dict, now: int) -> list[str]:
    out = []
    liq = tok.get("liq") or 0
    if liq < LOW_LIQ:
        out.append(f"мало ликвидности (${liq / 1000:.0f}k)")
    if tok.get("created") and now - tok["created"] < NEW_DAYS * DAY:
        out.append(f"новый токен ({max(1, int((now - tok['created']) / DAY))} дн)")
    if tok.get("mm_only"):
        out.append("купили только маркетмейкеры — может быть их рабочий запас")
    return out + list(tok.get("gp_flags") or [])


# ------------------------------------------------------------------------------------------------------------ Hyperliquid spot
def hl_prices(meta_ctx) -> dict[str, float]:
    """spotMetaAndAssetCtxs → {token name: USDC price}"""
    try:
        meta, ctxs = meta_ctx[0], meta_ctx[1]
    except Exception:  # noqa: BLE001
        return {}
    names = {t["index"]: t["name"] for t in meta.get("tokens") or []}
    out = {"USDC": 1.0}
    for u in meta.get("universe") or []:
        tk = u.get("tokens") or []
        i = u.get("index")
        if len(tk) == 2 and tk[1] == 0 and i is not None and i < len(ctxs):
            px = _f(ctxs[i].get("markPx")) or _f(ctxs[i].get("midPx"))
            if px:
                out[names.get(tk[0], str(tk[0]))] = px
    return out


def hl_accum(conn, now: int, prices: dict, labels: dict) -> tuple[list[dict], list[dict]]:
    """(accumulated by ≥ 2 whales within 7 days, held by ≥ 2 whales now) from the sm_hl snapshots"""
    cur: dict[str, dict] = {}
    t_now = conn.execute("SELECT MAX(t) FROM sm_hl").fetchone()[0]
    if not t_now:
        return [], []
    for a, c, tot, ntl in conn.execute("SELECT addr, coin, total, ntl FROM sm_hl WHERE t=?", (t_now,)):
        cur.setdefault(c, {})[a] = (tot, ntl)
    acc, held = [], []
    for coin, by in cur.items():
        if coin in ("USDC", "USDT", "USDH", "USDE") or coin not in prices:
            continue
        px = prices[coin]
        buyers, holders = [], []
        for a, (tot, ntl) in by.items():
            usd = tot * px
            if usd < HL_MIN_USD:
                continue
            entry = ntl / tot if tot and ntl else None
            holders.append({"label": labels.get(a, a[:6] + "…" + a[-4:]), "address": a, "usd": round(usd), "entry": entry,
                            "chg_pct": round((px / entry - 1) * 100, 1) if entry else None})
            old = conn.execute("SELECT total, t FROM sm_hl WHERE addr=? AND coin=? AND t>=? AND t<=? ORDER BY t LIMIT 1",
                               (a, coin, now - WINDOW, now - 6 * H)).fetchone()
            seen_before = conn.execute("SELECT 1 FROM sm_hl WHERE addr=? AND t>=? AND t<=? LIMIT 1", (a, now - WINDOW, now - 6 * H)).fetchone()
            if not seen_before:
                continue                       # this whale was not watched 6 h+ ago — no «before» to compare with
            prev = old[0] if old else 0.0
            if prev <= 0 or tot >= prev * (1 + HL_ADD):
                if (tot - prev) * px >= HL_MIN_USD:
                    buyers.append({"label": labels.get(a, a[:6] + "…" + a[-4:]), "address": a, "usd": round((tot - prev) * px),
                                   "new": prev <= 0, "entry": entry, "chg_pct": round((px / entry - 1) * 100, 1) if entry else None})
        if len(holders) >= 2:
            held.append({"coin": coin, "price": px, "n": len(holders), "usd": sum(h["usd"] for h in holders), "holders": holders})
        if len(buyers) >= 2:
            acc.append({"coin": coin, "key": f"hl:{coin}", "price": px, "n": len(buyers), "usd": sum(b["usd"] for b in buyers), "buyers": buyers})
    acc.sort(key=lambda x: (-x["n"], -x["usd"]))
    held.sort(key=lambda x: (-x["n"], -x["usd"]))
    return acc, held[:15]


# ------------------------------------------------------------------------------------------------------------ exchange flows
def flow_change(conn, asset: str, now: int, total: float, n: int) -> dict:
    conn.execute("INSERT INTO sm_flow VALUES (?,?,?,?)", (asset, now, total, n))
    conn.execute("DELETE FROM sm_flow WHERE asset=? AND t<?", (asset, now - 40 * DAY))
    out = {"total": total, "wallets": n}
    for k, back in (("d1", DAY), ("d7", 7 * DAY)):
        r = conn.execute("SELECT total, t FROM sm_flow WHERE asset=? AND n=? AND t<=? ORDER BY t DESC LIMIT 1", (asset, n, now - back + H)).fetchone()
        out[k] = round(total - r[0], 4) if r and now - r[1] <= back + 6 * H else None
    return out


def eth_flow_from_history(hist: dict[str, list]) -> dict:
    """{wallet: [{date, value(wei)}]} → summed ETH per day, change over 1 and 7 days (forward-filled per wallet)"""
    days = sorted({it["date"] for v in hist.values() for it in v if it.get("date")})
    if not days:
        return {}
    tot = {}
    for d in days:
        s = 0.0
        for v in hist.values():
            vals = [(it["date"], _f(it.get("value"), 0) or 0) for it in v if it.get("date") and it["date"] <= d]
            if vals:
                s += max(vals)[1] / 1e18
        tot[d] = s
    last = days[-1]
    out = {"total": round(tot[last], 2), "date": last, "wallets": len(hist)}
    for k, back in (("d1", 1), ("d7", 7)):
        target = datetime.strptime(last, "%Y-%m-%d").timestamp() - back * 86400
        prev = [d for d in days if datetime.strptime(d, "%Y-%m-%d").timestamp() <= target]
        out[k] = round(tot[last] - tot[prev[-1]], 2) if prev else None
    return out


# ------------------------------------------------------------------------------------------------------------ track record
def read_jsonl(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:  # noqa: BLE001
                        pass
    except FileNotFoundError:
        pass
    return out


def append_jsonl(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def log_signals(data_dir: str, now: int, sigs: list[dict]) -> int:
    """one line per signal per UTC day (the first run of the day that sees it)"""
    path = os.path.join(data_dir, "signals.jsonl")
    day = day_key(now)
    have = {(r.get("day"), r.get("key")) for r in read_jsonl(path)}
    rows = []
    for s in sigs:
        if (day, s["key"]) in have or not s.get("price"):
            continue
        rows.append({"day": day, "t": now, "key": s["key"], "src": s.get("src"), "chain": s.get("chain"), "address": s.get("address"),
                     "symbol": s.get("symbol"), "n": s.get("n"), "wallets": [b["label"] for b in s.get("buyers") or []][:8],
                     "price": s["price"], "liq": s.get("liq"), "fdv": s.get("fdv"), "flags": len(s.get("flags") or [])})
    append_jsonl(path, rows)
    return len(rows)


def first_sightings(data_dir: str) -> dict[str, dict]:
    first = {}
    for r in read_jsonl(os.path.join(data_dir, "signals.jsonl")):
        k = r.get("key")
        if k and (k not in first or r["t"] < first[k]["t"]):
            first[k] = r
    return first


def due_outcomes(data_dir: str, now: int) -> list[tuple[dict, int]]:
    done = {(r["key"], r["h"]) for r in read_jsonl(os.path.join(data_dir, "outcomes.jsonl"))}
    out = []
    for k, r in first_sightings(data_dir).items():
        for h in HORIZONS:
            if (k, h) not in done and now >= r["t"] + h * DAY:
                out.append((r, h))
    return out


def record_outcomes(data_dir: str, now: int, prices: dict[str, float]) -> int:
    rows = []
    for r, h in due_outcomes(data_dir, now):
        px = prices.get(r["key"])
        if not px or not r.get("price"):
            continue
        late = now - (r["t"] + h * DAY) > 3 * DAY
        rows.append({"key": r["key"], "symbol": r.get("symbol"), "h": h, "t0": r["t"], "t": now, "price0": r["price"], "price": px,
                     "ret": round((px / r["price"] - 1) * 100, 2), "late": late})
    append_jsonl(os.path.join(data_dir, "outcomes.jsonl"), rows)
    return len(rows)


def track(data_dir: str) -> dict:
    """what following the signals gave: per horizon n / average / median / share in plus; the latest signals with their outcomes"""
    outs = [o for o in read_jsonl(os.path.join(data_dir, "outcomes.jsonl")) if not o.get("late")]
    stats = {}
    for h in HORIZONS:
        rs = [o["ret"] for o in outs if o["h"] == h]
        stats[str(h)] = {"n": len(rs), "avg": round(sum(rs) / len(rs), 2) if rs else None, "median": round(st.median(rs), 2) if rs else None,
                         "win": round(sum(1 for x in rs if x > 0) / len(rs), 3) if rs else None}
    by = {}
    for o in outs:
        by.setdefault(o["key"], {})[str(o["h"])] = o["ret"]
    first = first_sightings(data_dir)
    recent = sorted(first.values(), key=lambda r: -r["t"])[:25]
    return {"stats": stats, "signals_total": len(first),
            "recent": [{"key": r["key"], "symbol": r.get("symbol"), "chain": r.get("chain"), "src": r.get("src"), "t": r["t"], "n": r.get("n"),
                        "price": r.get("price"), "ret": by.get(r["key"], {})} for r in recent]}


# ------------------------------------------------------------------------------------------------------------ entry point
def run(conn: sqlite3.Connection, cfg: dict, whales: dict | None = None, prev: dict | None = None, now: int | None = None, get=http_json,
        data_dir: str = os.path.join("data", "smart_money"), force: bool = False, deadline_s: float = DEADLINE_S) -> dict | None:
    """→ (the web/smart_money.json document); None when the last run is younger than an hour. Never raises on a source error."""
    now = now or int(time.time() * 1000)
    prev = prev or {}
    if not force and prev.get("asof") and now - int(prev["asof"]) < EVERY:
        return None
    ensure_schema(conn)
    t_end = time.time() + deadline_s
    src = Src()
    notes: list[str] = []
    exch = cfg.get("exchanges") or {}
    exch_eth = {e["address"].lower() for e in exch.get("eth") or []}
    # 1. wallets (config + HL top traders) and their labels
    cfg_w = [dict(w, address=w["address"].lower()) for w in cfg.get("wallets") or []]
    hlw = hl_wallets(whales)
    wallets = cfg_w + [dict(w, chains=["arbitrum", "eth"]) for w in hlw[:EVM_HL_ADDRS]]
    tags = kv_get(conn, "tags") or {}
    for w in cfg_w:
        k = w["address"]
        if k in tags and now - tags[k].get("t", 0) < 7 * DAY:
            continue
        if time.time() > t_end:
            break
        try:
            info = get(f"{BS['eth']}/api/v2/addresses/{k}")
            src.ok("blockscout")
            tags[k] = {"t": now, "tags": tags_of(info)}
        except Exception as e:  # noqa: BLE001
            src.fail("blockscout", e)
        _sleep(0.25)
    kv_set(conn, "tags", tags)
    # 2. scans
    scans, wl_out, chain_down = [], [], {}
    for w in wallets:
        row = {"label": w["label"], "kind": w["kind"], "address": w["address"], "source": w.get("source"), "source_note": w.get("source_note"),
               "chains": [], "auto": w["kind"] == "топ-трейдер Hyperliquid"}
        tg = (tags.get(w["address"]) or {}).get("tags")
        if tg is not None:
            row["tags"] = tg[:4]
            row["confirmed"] = confirmed(w["label"], tg)
        for ch in w.get("chains") or ["eth"]:
            if ch not in BS:
                continue
            if time.time() > t_end:
                row["chains"].append({"chain": ch, "error": "не успели в этом прогоне"})
                continue
            if chain_down.get(ch, 0) >= 2:          # 04.11: base / arbitrum Blockscout answer 403 to GitHub runners — stop asking
                row["chains"].append({"chain": ch, "error": "источник недоступен"})
                continue
            s = scan_wallet(get, ch, w["address"], now, exch_eth, src, deadline=t_end)
            if s.get("error"):
                chain_down[ch] = chain_down.get(ch, 0) + 1
                row["chains"].append({"chain": ch, "error": "источник недоступен"})
                continue
            chain_down[ch] = -99
            row["chains"].append({"chain": ch, "coverage_h": s["coverage_h"], "full": s["full"], "transfers": s["n_items"]})
            scans.append({"wallet": w["address"], "label": w["label"], "kind": w["kind"], "chain": ch, "flows": s["flows"]})
        wl_out.append(row)
    groups = group_tokens(scans)
    # 3. market data for every token with a positive net inflow (prices → USD; spam / unpriced dropped)
    # DexScreener per chain: tokens ≥ 2 wallets first, then the ones with the biggest inflow by Blockscout's own USD rate, then unpriced
    def _prio(g):
        rate = g.get("bs_rate") or 0
        return (-len({b["address"] for b in g["buyers"]}), -(max(b["amount"] for b in g["buyers"]) * rate), rate > 0)
    pairs = []
    for ch in BS:
        gs = sorted([g for g in groups.values() if g["chain"] == ch], key=_prio)[:DS_MAX]
        if gs and time.time() < t_end:
            pairs += ds_fetch(get, ch, [g["address"] for g in gs], src, deadline=t_end)
    dropped = {"spam": 0, "small": 0, "big": 0}
    sigs, mm_only, singles = [], [], []
    for k, g in groups.items():
        if str(g.get("reputation") or "").lower() in ("scam", "spam"):
            dropped["spam"] += 1
            continue
        m = ds_best(pairs, g["chain"], g["address"])
        if not m or (m["liq"] or 0) < MIN_LIQ or not m["price"]:
            dropped["spam"] += 1
            continue
        g.update(m)
        for b in g["buyers"]:
            b["usd"] = round(b["amount"] * m["price"])
        g["buyers"] = sorted([b for b in g["buyers"] if b["usd"] >= MIN_USD], key=lambda b: -b["usd"])
        wallets_n = len({b["address"] for b in g["buyers"]})
        if wallets_n == 0:
            dropped["small"] += 1
            continue
        g["n"] = wallets_n
        g["non_mm_n"] = len({b["address"] for b in g["buyers"] if not b["mm"]})
        g["mm_only"] = g["non_mm_n"] == 0
        g["usd"] = sum(b["usd"] for b in g["buyers"])
        g["first_t"] = min(b["first_t"] for b in g["buyers"] if b["first_t"])
        g["src"] = "evm"
        g["chain_ru"] = CHAIN_RU[g["chain"]]
        g["explorer"] = f"{EXPLORER[g['chain']]}/token/{g['address']}"
        g["early"] = bool(g.get("fdv") and g["fdv"] < EARLY_FDV)
        g["big"] = bool(g.get("fdv") and g["fdv"] >= BIG_FDV)
        if wallets_n >= 2:
            (mm_only if g["mm_only"] else sigs).append(g)
        elif g["usd"] >= SINGLE_MIN_USD and not g["mm_only"]:
            singles.append(g)
    sigs.sort(key=lambda g: (-g["n"], not g["early"], -g["usd"]))
    mm_only.sort(key=lambda g: (-g["n"], -g["usd"]))
    singles.sort(key=lambda g: -g["usd"])
    singles = singles[:12]
    # 4. security + price at the first buy (signals first, then the others shown)
    gp_cache = kv_get(conn, "goplus") or {}
    for g in sigs[:12] + mm_only[:4] + singles[:6]:
        if time.time() > t_end:
            break
        ck = g["key"]
        if ck in gp_cache and now - gp_cache[ck]["t"] < DAY:
            r = gp_cache[ck]["r"]
        else:
            try:
                j = get(f"{GP}{GP_CHAIN[g['chain']]}?contract_addresses={g['address']}")
                r = ((j or {}).get("result") or {}).get(g["address"]) or {}
                src.ok("goplus")
                gp_cache[ck] = {"t": now, "r": {k_: r.get(k_) for k_ in ("is_honeypot", "cannot_sell_all", "buy_tax", "sell_tax", "holders",
                                                                          "is_mintable", "is_open_source", "holder_count")}}
                r = gp_cache[ck]["r"]
            except Exception as e:  # noqa: BLE001
                src.fail("goplus", e)
                r = None
            _sleep(1.0)
        g["gp_flags"], g["security"] = goplus_flags(r)
        if r is None:
            g["security"] = {"err": "проверка недоступна"}
        if g.get("pool") and g.get("first_t") and time.time() < t_end:
            try:
                j = get(f"{GT}{GT_NET[g['chain']]}/pools/{g['pool']}/ohlcv/hour?aggregate=1&limit=200",
                        headers={"Accept": "application/json;version=20230302"})
                ol = ((((j or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
                p0 = price_at(ol, g["first_t"])
                src.ok("geckoterminal")
                if p0:
                    g["price_first"] = p0
                    g["chg_since_pct"] = round((g["price"] / p0 - 1) * 100, 1)
            except Exception as e:  # noqa: BLE001
                src.fail("geckoterminal", e)
            _sleep(2.1)
    kv_set(conn, "goplus", {k_: v for k_, v in gp_cache.items() if now - v["t"] < 3 * DAY})
    for g in sigs + mm_only + singles:
        g["flags"] = risk_flags(g, now)
        g["age_days"] = round((now - g["created"]) / DAY, 1) if g.get("created") else None
        for x in ("tok",):
            g.pop(x, None)
        for b in g["buyers"]:
            b.pop("amount", None)
    # 5. Hyperliquid spot
    hl = {"accum": [], "held": [], "watched": 0}
    prices_hl = {}
    if hlw and time.time() < t_end:
        try:
            prices_hl = hl_prices(get(HL, {"type": "spotMetaAndAssetCtxs"}))
            src.ok("hyperliquid")
            labels = {w["address"]: w["label"] for w in hlw}
            for w in hlw:
                if time.time() > t_end:
                    break
                try:
                    j = get(HL, {"type": "spotClearinghouseState", "user": w["address"]})
                    src.ok("hyperliquid")
                except Exception as e:  # noqa: BLE001
                    src.fail("hyperliquid", e)
                    continue
                rows = [(w["address"], b.get("coin"), now, _f(b.get("total"), 0) or 0, _f(b.get("entryNtl"), 0) or 0)
                        for b in (j or {}).get("balances") or [] if b.get("coin")]
                conn.executemany("INSERT INTO sm_hl VALUES (?,?,?,?,?)", rows or [(w["address"], "USDC", now, 0.0, 0.0)])
                hl["watched"] += 1
                _sleep(0.2)
            conn.execute("DELETE FROM sm_hl WHERE t<?", (now - 9 * DAY,))
            conn.commit()
            hl["accum"], hl["held"] = hl_accum(conn, now, prices_hl, labels)
            hl["history_h"] = round((now - (conn.execute("SELECT MIN(t) FROM sm_hl").fetchone()[0] or now)) / H, 1)
        except Exception as e:  # noqa: BLE001
            src.fail("hyperliquid", e)
            hl["error"] = "источник недоступен"
    for a in hl["accum"]:
        a.update(src="hl", symbol=a["coin"], chain="hl", chain_ru=CHAIN_RU["hl"])
    # 6. exchange flows (BTC / ETH / SOL)
    flows = {}
    mids = {}
    try:
        mids = get(HL, {"type": "allMids"}) or {}
        src.ok("hyperliquid")
    except Exception as e:  # noqa: BLE001
        src.fail("hyperliquid", e)
    if time.time() < t_end:
        hist = {}
        for e in exch.get("eth") or []:
            try:
                j = get(f"{BS['eth']}/api/v2/addresses/{e['address'].lower()}/coin-balance-history-by-day")
                hist[e["address"]] = (j or {}).get("items") or []
                src.ok("blockscout")
            except Exception as er:  # noqa: BLE001
                src.fail("blockscout", er)
            _sleep(0.25)
        flows["ETH"] = eth_flow_from_history(hist) if hist else {"error": "источник недоступен"}
    btc_tot, btc_n, btc_err = 0.0, 0, None
    for e in exch.get("btc") or []:
        if time.time() > t_end:
            break
        for base in MEMPOOL:
            try:
                j = get(base + e["address"])
                cs = (j or {}).get("chain_stats") or {}
                btc_tot += ((_f(cs.get("funded_txo_sum"), 0) or 0) - (_f(cs.get("spent_txo_sum"), 0) or 0)) / 1e8
                btc_n += 1
                src.ok("mempool")
                break
            except Exception as er:  # noqa: BLE001
                btc_err = er
        _sleep(0.3)
    if btc_n:
        flows["BTC"] = flow_change(conn, "BTC", now, round(btc_tot, 4), btc_n)
    else:
        if btc_err:
            src.fail("mempool", btc_err)
        flows["BTC"] = {"error": "источник недоступен"}
    sol = exch.get("sol") or []
    if sol and time.time() < t_end:
        try:
            j = get(SOL_RPC, {"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts", "params": [[e["address"] for e in sol], {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}]})
            vals = (((j or {}).get("result") or {}).get("value")) or []
            got = [v for v in vals if v]
            if not got:
                raise RuntimeError((j or {}).get("error") or "пустой ответ")
            flows["SOL"] = flow_change(conn, "SOL", now, round(sum(v.get("lamports", 0) for v in got) / 1e9, 2), len(got))
            src.ok("solana")
        except Exception as e:  # noqa: BLE001
            src.fail("solana", e)
            flows["SOL"] = {"error": "источник недоступен"}
    for a, f in flows.items():
        px = _f(mids.get(a))
        f["price"] = px
        if px and not f.get("error"):
            for k in ("d1", "d7"):
                f[k + "_usd"] = round(f[k] * px) if f.get(k) is not None else None
    conn.commit()
    # 7. track record
    all_sig = sigs + hl["accum"]
    logged = log_signals(data_dir, now, all_sig)
    due = due_outcomes(data_dir, now)
    px_now = {s["key"]: s["price"] for s in all_sig if s.get("price")}
    px_now.update({f"hl:{c}": p for c, p in prices_hl.items()})
    more = []
    for ch in BS:
        need = sorted({r["address"] for r, _h in due if r.get("src") == "evm" and r.get("chain") == ch and r["key"] not in px_now and r.get("address")})
        if need and time.time() < t_end + 30:
            more += ds_fetch(get, ch, need, src)
    if more:
        for r, _h in due:
            if r.get("src") == "evm" and r["key"] not in px_now:
                m = ds_best(more, r["chain"], r["address"])
                if m and m.get("price"):
                    px_now[r["key"]] = m["price"]
    recorded = record_outcomes(data_dir, now, px_now)
    tr = track(data_dir)
    partial = time.time() > t_end
    if partial:
        notes.append("прогон не уложился во время — часть кошельков посмотрим в следующий раз")
    doc = {"asof": now, "partial": partial, "sources": src.doc(SRC_NAMES), "wallets": wl_out,
           "wallets_n": len(wl_out), "wallets_cfg": len(cfg_w), "wallets_hl": len(hlw),
           "signals": sigs[:20], "mm_only": mm_only[:10], "singles": singles, "dropped": dropped, "hl": hl, "flows": flows, "track": tr,
           "logged_today": logged, "outcomes_recorded": recorded, "tokens_seen": len(groups),
           "rule_ru": f"Сигнал — токен, который за 7 дней чисто (входящие минус исходящие) набрали ≥ 2 «умных» кошелька, каждый на ≥ ${MIN_USD // 1000}k. "
                      f"Стейблкоины, обёрнутые BTC/ETH и токены стейкинга не считаем; токены без пула ≥ ${MIN_LIQ // 1000}k ликвидности отсеиваем как спам. "
                      "Hyperliquid: ≥ 2 топ-трейдера купили или добавили ≥ 10% монеты (от $10k) за 7 дней.",
           "risk_ru": "Не финансовый совет, высокий риск. Это наблюдение за публичными кошельками: фонд может получать токены по сделке, "
                      "маркетмейкер — для работы, кит — продать через час. Ранние токены часто падают на 80–100%. Реальных сделок по этим "
                      "сигналам система не делает.",
           "notes_ru": notes[:10]}
    return doc


def signal_feed(doc: dict) -> dict:
    """the compact input the spot logic can read later (web/smart_money_signal.json) — information only until the owner connects it"""
    toks = []
    for s in (doc.get("signals") or []) + ((doc.get("hl") or {}).get("accum") or []):
        toks.append({"key": s["key"], "src": s.get("src"), "chain": s.get("chain"), "address": s.get("address"), "symbol": s.get("symbol"),
                     "wallets_n": s.get("n"), "non_mm_n": s.get("non_mm_n", s.get("n")), "usd": s.get("usd"), "first_t": s.get("first_t"),
                     "price": s.get("price"), "liq_usd": s.get("liq"), "fdv": s.get("fdv"), "early": s.get("early"),
                     "flags": s.get("flags") or [], "chg_since_pct": s.get("chg_since_pct")})
    return {"asof": doc.get("asof"), "info_only": True, "real_money": False,
            "confirmation_needed_ru": "Только информация. Подключение к реальным деньгам — только после подтверждения владельца.",
            "tokens": toks, "track": (doc.get("track") or {}).get("stats"), "risk_ru": doc.get("risk_ru")}
