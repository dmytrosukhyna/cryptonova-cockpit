"""Доходность (кредитование) — the conservative sleeve of «CRYPTO NOVA Портфолио» (18.10.2026, owner: «сектор кредитования … Aave …
консервативная стратегия, процент выше, чем в банке»).

A $10 000 demo desk that SUPPLIES stablecoins to lending markets (no borrowing → nothing to liquidate, no price risk of a coin):
half USDC and half USDT on Aave v3 (Ethereum). Every run it reads the current supply APY from DefiLlama's public yields API
(https://yields.llama.fi/pools) and accrues interest for the time since the previous run at that rate. The rate is VARIABLE
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
BASKET = [("aave-v3", "Ethereum", "USDC", 0.5), ("aave-v3", "Ethereum", "USDT", 0.5)]   # (project, chain, symbol, share)
SHOW = [("aave-v3", "Ethereum", "USDC", "Aave · USDC (кредитование)", 1), ("aave-v3", "Ethereum", "USDT", "Aave · USDT (кредитование)", 1),
        ("aave-v3", "Ethereum", "WETH", "Aave · ETH (кредитование)", 2), ("lido", "Ethereum", "STETH", "Lido · стейкинг ETH", 2),
        ("sky-lending", "Ethereum", "SUSDS", "Sky · сберегательная ставка USDS", 1), ("ethena-usde", "Ethereum", "SUSDE", "Ethena · sUSDe", 3)]


def fetch_pools(timeout: int = 40) -> list:
    req = urllib.request.Request(API, headers={"User-Agent": "cnq-yield/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("data") or []


def pick(pools: list, project: str, chain: str, symbol: str) -> dict | None:
    """The biggest pool (by TVL) of that project / chain / symbol."""
    xs = [p for p in pools if p.get("project") == project and p.get("chain") == chain and str(p.get("symbol", "")).upper() == symbol]
    return max(xs, key=lambda p: p.get("tvlUsd") or 0) if xs else None


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
        if p and p.get("apy") is not None:
            rates[sym] = float(p["apy"])
    stale = len(rates) < len(BASKET)
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
            table.append({"name_ru": name, "apy": round(float(p.get("apy") or 0), 2), "tvl_usd": round(p.get("tvlUsd") or 0), "risk": risk,
                          "project": proj, "symbol": sym})
    eq = st["equity"]
    return {"asof": now, "name": "Доходность · кредитование стейблкоинов", "start_equity": START, "equity": round(eq, 2),
            "pnl_usd": round(eq - START, 2), "pnl_pct": round((eq / START - 1) * 100, 3), "apy_now": round(st["apy"], 2) if st.get("apy") is not None else None,
            "rates": {k: round(v, 2) for k, v in rates.items()}, "stale": stale, "error": err, "since": st["start"],
            "history": st["history"][-300:], "table": table,
            "about_ru": ("Консервативная часть: стейблкоины USDC и USDT отданы в кредит на Aave (Ethereum) — без займов, поэтому "
                         "нечего ликвидировать, и нет риска падения цены монеты. Ставка плавающая: её задаёт спрос на займы, сейчас "
                         f"около {st['apy']:.1f}% годовых. Риски: ошибка в смарт-контракте, отвязка стейблкоина от доллара, падение ставки. "
                         "Это демо: проценты начисляются по текущей ставке DefiLlama." if st.get("apy") is not None else
                         "Консервативная часть: кредитование стейблкоинов на Aave. Ставка появится после первого обновления.")}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, fetch=fetch_pools) -> dict:
    doc = step(conn, now, fetch)
    with open(os.path.join(out, "yield.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc
