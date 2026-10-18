"""Доходность (кредитование) — the conservative sleeve of «CRYPTO NOVA Портфолио» (18.10.2026, owner: «сектор кредитования … Aave …
консервативная стратегия, процент выше, чем в банке»).

A $10 000 demo desk that SUPPLIES stablecoins to lending markets (no borrowing → nothing to liquidate, no price risk of a coin),
spread over two protocols so one bug can't take everything: USDC 40 % and USDT 30 % on Aave v3 (Ethereum), USDS 30 % in the Sky
savings rate (sUSDS). Every run it reads the current BASE supply APY (apyBase — token rewards can be switched off any day, so
they are not counted) from DefiLlama's public yields API (https://yields.llama.fi/pools) and accrues interest for the time since
the previous run at that rate. Items the API doesn't return this run are left out of the average (listed in "missing"). The rate is VARIABLE
(set by demand for loans), not fixed. Risks that remain: the protocol's smart contracts, a stablecoin losing its peg, and the
yield falling. Without a fresh rate the last known one is used (marked stale). Also publishes a small table of other yields
(staking, savings rates) for the panel — for information. Stdlib only."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import urllib.request

API = "https://yields.llama.fi/pools"
START = 10_000.0
YEAR_MS = 365 * 86_400_000
BASKET = [("aave-v3", "Ethereum", "USDC", 0.4), ("aave-v3", "Ethereum", "USDT", 0.3),
          ("sky-lending", "Ethereum", "SUSDS", 0.3)]                                     # (project, chain, symbol, share)
SHOW = [("aave-v3", "Ethereum", "USDC", "Aave · USDC (кредитование)", 1), ("aave-v3", "Ethereum", "USDT", "Aave · USDT (кредитование)", 1),
        ("aave-v3", "Ethereum", "WETH", "Aave · ETH (кредитование)", 2), ("lido", "Ethereum", "STETH", "Lido · стейкинг ETH", 2),
        ("sky-lending", "Ethereum", "SUSDS", "Sky · сберегательная ставка USDS", 1), ("compound-v3", "Ethereum", "USDC", "Compound · USDC (кредитование)", 2),
        ("aave-v3", "Base", "USDC", "Aave · USDC в сети Base", 2), ("ethena-usde", "Ethereum", "SUSDE", "Ethena · sUSDe (доход от фандинга)", 3)]


def fetch_pools(timeout: int = 40) -> list:
    req = urllib.request.Request(API, headers={"User-Agent": "cnq-yield/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("data") or []


def pick(pools: list, project: str, chain: str, symbol: str) -> dict | None:
    """The main pool of that project / chain / symbol: a plain one (no poolMeta, e.g. not an isolated or e-mode market) first,
    then the biggest by TVL."""
    xs = [p for p in pools if p.get("project") == project and p.get("chain") == chain and str(p.get("symbol", "")).upper() == symbol]
    return max(xs, key=lambda p: (not p.get("poolMeta"), p.get("tvlUsd") or 0)) if xs else None


def base_apy(p: dict) -> float | None:
    """The interest the borrowers pay (apyBase); the total apy only when the API gives no split."""
    v = p.get("apyBase")
    v = p.get("apy") if v is None else v
    return None if v is None else float(v)


def step(conn: sqlite3.Connection, now: int | None = None, fetch=fetch_pools) -> dict:
    now = now or int(time.time() * 1000)
    conn.execute("CREATE TABLE IF NOT EXISTS yield_state(k TEXT PRIMARY KEY, v TEXT)")
    st = {k: json.loads(v) for k, v in conn.execute("SELECT k, v FROM yield_state")}
    try:
        pools = fetch()
    except Exception as e:  # noqa: BLE001
        pools, err = [], f"{type(e).__name__}: {str(e)[:100]}"
    else:
        err = None
    rates = {}
    for proj, chain, sym, share in BASKET:
        p = pick(pools, proj, chain, sym)
        if p and base_apy(p) is not None:
            rates[sym] = base_apy(p)
    missing = [s for _, _, s, _ in BASKET if s not in rates]
    stale = not rates
    apy = sum(rates[s] * sh for _, _, s, sh in BASKET if s in rates) / sum(sh for _, _, s, sh in BASKET if s in rates) if rates else st.get("apy")
    if not st.get("equity"):
        st = {"equity": START, "start": now, "last": now, "apy": apy, "history": [[now, START]]}
    else:
        use = st.get("apy") if apy is None else apy
        if use is not None:
            years = max(0, now - st["last"]) / YEAR_MS
            st["equity"] *= math.exp(math.log1p(use / 100) * years)      # compounding at the current APY for the elapsed time
        st["last"], st["apy"] = now, (apy if apy is not None else st.get("apy"))
        st["history"] = (st.get("history") or [])[-500:] + [[now, round(st["equity"], 4)]]
    for k, v in st.items():
        conn.execute("INSERT OR REPLACE INTO yield_state VALUES(?, ?)", (k, json.dumps(v)))
    conn.commit()
    table = []
    for proj, chain, sym, name, risk in SHOW:
        p = pick(pools, proj, chain, sym)
        if p:
            table.append({"name_ru": name, "apy": round(float(p.get("apy") or 0), 2), "apy_base": round(base_apy(p) or 0, 2),
                          "tvl_usd": round(p.get("tvlUsd") or 0), "risk": risk, "project": proj, "symbol": sym})
    eq = st["equity"]
    return {"asof": now, "name": "Доходность · кредитование стейблкоинов", "start_equity": START, "equity": round(eq, 2),
            "pnl_usd": round(eq - START, 2), "pnl_pct": round((eq / START - 1) * 100, 3), "apy_now": round(st["apy"], 2) if st.get("apy") is not None else None,
            "rates": {k: round(v, 2) for k, v in rates.items()}, "stale": stale, "missing": missing, "error": err,
            "basket": [{"project": pr, "chain": ch, "symbol": sy, "share": sh} for pr, ch, sy, sh in BASKET], "since": st["start"],
            "history": st["history"][-300:], "table": table,
            "about_ru": ("Консервативная часть: стейблкоины в двух местах — USDC и USDT отданы в кредит на Aave (Ethereum), USDS лежит "
                         "на сберегательной ставке Sky. Мы ничего не занимаем, поэтому нечего ликвидировать, и нет риска падения цены монеты. "
                         f"Ставка плавающая, сейчас около {st['apy']:.1f}% годовых (без бонусных токенов, только то, что платят заёмщики). "
                         "Риски: ошибка в смарт-контракте, отвязка стейблкоина от доллара, падение ставки. Это демо: проценты начисляются "
                         "по текущей ставке DefiLlama." if st.get("apy") is not None else
                         "Консервативная часть: кредитование стейблкоинов на Aave и сберегательная ставка Sky. Ставка появится после первого обновления.")}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, fetch=fetch_pools) -> dict:
    doc = step(conn, now, fetch)
    with open(os.path.join(out, "yield.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc
